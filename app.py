import os
import sqlite3
import secrets
import time
from datetime import datetime
from functools import wraps

from flask import Flask, request, jsonify, render_template, session, redirect, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(__file__)
DB = os.path.join(BASE_DIR, "flujo_ventas.db")
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
COMPROBANTES_DIR = os.path.join(UPLOADS_DIR, "comprobantes")
FACTURAS_DIR = os.path.join(UPLOADS_DIR, "facturas")
NOTAS_CREDITO_DIR = os.path.join(UPLOADS_DIR, "notas_credito")
os.makedirs(COMPROBANTES_DIR, exist_ok=True)
os.makedirs(FACTURAS_DIR, exist_ok=True)
os.makedirs(NOTAS_CREDITO_DIR, exist_ok=True)

ENV_PATH = os.path.join(BASE_DIR, ".env")


def _load_env():
    values = {}
    if os.path.exists(ENV_PATH):
        for line in open(ENV_PATH, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            values[k.strip()] = v.strip()
    return values


def _ensure_secret_key(values):
    if values.get("SECRET_KEY"):
        return values["SECRET_KEY"]
    key = secrets.token_hex(32)
    with open(ENV_PATH, "a", encoding="utf-8") as f:
        f.write(f"SECRET_KEY={key}\n")
    return key


_env = _load_env()
SECRET_KEY = _ensure_secret_key(_env)
ADMIN_USER = _env.get("ADMIN_USER", "admin")
ADMIN_PASS = _env.get("ADMIN_PASS", "admin123")

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB por archivo

ROLES = ["ventas", "facturacion", "deposito", "flex", "mostrador", "admin"]
ROL_LABEL = {
    "ventas": "Ventas",
    "facturacion": "Facturación",
    "deposito": "Depósito",
    "flex": "Flex",
    "mostrador": "Mostrador",
    "admin": "Administración",
}

PRIORIDADES = ["Baja", "Normal", "Alta", "Urgente"]

ESTADOS = [
    "A_FACTURAR", "FACTURADO", "EN_PREPARACION", "EN_FLEX",
    "PROGRAMADO", "ENTREGADO", "LISTO_MOSTRADOR", "RETIRADO",
]

ESTADO_LABEL = {
    "A_FACTURAR": "A facturar",
    "FACTURADO": "Facturado — definir envío/retiro",
    "EN_PREPARACION": "En preparación (Depósito)",
    "EN_FLEX": "En Flex — planificar entrega",
    "PROGRAMADO": "Entrega programada",
    "ENTREGADO": "Entregado",
    "LISTO_MOSTRADOR": "Listo para retirar (Mostrador)",
    "RETIRADO": "Retirado",
}

ESTADO_SECTOR = {
    "A_FACTURAR": "facturacion",
    "FACTURADO": "ventas",
    "EN_PREPARACION": "deposito",
    "EN_FLEX": "flex",
    "PROGRAMADO": "flex",
    "ENTREGADO": None,
    "LISTO_MOSTRADOR": "mostrador",
    "RETIRADO": None,
}

MODALIDADES_PAGO = ["Efectivo", "Transferencia", "Nave"]

ALLOWED_COMPROBANTE = {"pdf", "jpg", "jpeg", "png"}
ALLOWED_FACTURA = {"pdf"}
ALLOWED_NOTA_CREDITO = {"pdf"}

NOTA_CREDITO_LABEL = {
    "SOLICITADA": "Nota de crédito solicitada",
    "EMITIDA": "Nota de crédito emitida",
}

# Límite de intentos de inicio de sesión (en memoria; se reinicia si se reinicia el servidor)
FAILED_LOGIN_ATTEMPTS = {}
LOGIN_MAX_INTENTOS = 5
LOGIN_VENTANA_SEGUNDOS = 15 * 60


def _login_bloqueado(usuario_norm):
    ahora = time.time()
    intentos = [t for t in FAILED_LOGIN_ATTEMPTS.get(usuario_norm, []) if ahora - t < LOGIN_VENTANA_SEGUNDOS]
    FAILED_LOGIN_ATTEMPTS[usuario_norm] = intentos
    return len(intentos) >= LOGIN_MAX_INTENTOS


def _registrar_intento_fallido(usuario_norm):
    FAILED_LOGIN_ATTEMPTS.setdefault(usuario_norm, []).append(time.time())


def _limpiar_intentos(usuario_norm):
    FAILED_LOGIN_ATTEMPTS.pop(usuario_norm, None)


def _es_numero(valor):
    try:
        float(valor)
        return True
    except (TypeError, ValueError):
        return False


def get_db():
    con = sqlite3.connect(DB, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA busy_timeout = 10000")
    return con


def _asegurar_columnas(con, tabla, columnas):
    """Agrega columnas nuevas a una tabla existente sin tocar los datos ya guardados."""
    existentes = {row["name"] for row in con.execute(f"PRAGMA table_info({tabla})")}
    for nombre, definicion in columnas:
        if nombre not in existentes:
            con.execute(f"ALTER TABLE {tabla} ADD COLUMN {nombre} {definicion}")


def init_db():
    with get_db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL,
                usuario TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                rol TEXT NOT NULL,
                activo INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS pedidos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nro_cliente TEXT,
                razon_social TEXT,
                modalidad_facturacion TEXT,
                modalidad_pago TEXT,
                monto TEXT,
                comprobante_path TEXT,
                factura_pdf_path TEXT,
                cobranza_nota TEXT,
                pago_confirmado INTEGER DEFAULT 0,
                prioridad TEXT NOT NULL DEFAULT 'Normal',
                envio INTEGER,
                estado TEXT NOT NULL DEFAULT 'A_FACTURAR',
                fecha_entrega_estimada TEXT,
                observaciones TEXT,
                creado_por INTEGER,
                facturado_por INTEGER,
                prep_por INTEGER,
                flex_por INTEGER,
                entregado_por INTEGER,
                mostrador_por INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                facturado_at TIMESTAMP,
                en_preparacion_at TIMESTAMP,
                en_flex_at TIMESTAMP,
                programado_at TIMESTAMP,
                entregado_at TIMESTAMP,
                listo_mostrador_at TIMESTAMP,
                retirado_at TIMESTAMP
            )
        """)
        _asegurar_columnas(con, "pedidos", [
            ("nota_credito_estado", "TEXT"),
            ("nota_credito_motivo", "TEXT"),
            ("nota_credito_pdf_path", "TEXT"),
            ("nota_credito_solicitada_por", "INTEGER"),
            ("nota_credito_solicitada_at", "TIMESTAMP"),
            ("nota_credito_emitida_por", "INTEGER"),
            ("nota_credito_emitida_at", "TIMESTAMP"),
        ])
        con.execute("""
            CREATE TABLE IF NOT EXISTS eventos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pedido_id INTEGER NOT NULL,
                tipo TEXT NOT NULL,
                usuario_id INTEGER,
                usuario_nombre TEXT,
                detalle TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (pedido_id) REFERENCES pedidos(id)
            )
        """)
        existe_admin = con.execute("SELECT id FROM usuarios WHERE rol='admin'").fetchone()
        if not existe_admin:
            con.execute(
                "INSERT INTO usuarios (nombre, usuario, password_hash, rol) VALUES (?,?,?,?)",
                ("Administrador", ADMIN_USER, generate_password_hash(ADMIN_PASS), "admin"),
            )


init_db()


# ---------- Auth helpers ----------

def current_user():
    if "user_id" not in session:
        return None
    with get_db() as con:
        row = con.execute("SELECT * FROM usuarios WHERE id=? AND activo=1", (session["user_id"],)).fetchone()
    return dict(row) if row else None


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user():
            return jsonify({"error": "No autenticado"}), 401
        return f(*args, **kwargs)
    return decorated


def roles_required(*roles):
    def wrapper(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            u = current_user()
            if not u:
                return jsonify({"error": "No autenticado"}), 401
            if u["rol"] != "admin" and u["rol"] not in roles:
                return jsonify({"error": "No autorizado para esta acción"}), 403
            return f(u, *args, **kwargs)
        return decorated
    return wrapper


def log_evento(con, pedido_id, tipo, usuario, detalle=""):
    con.execute(
        "INSERT INTO eventos (pedido_id, tipo, usuario_id, usuario_nombre, detalle) VALUES (?,?,?,?,?)",
        (pedido_id, tipo, usuario["id"], usuario["nombre"], detalle),
    )


def sector_actual(pedido):
    return ESTADO_SECTOR.get(pedido["estado"])


def pedido_to_dict(row):
    d = dict(row)
    d["estado_label"] = ESTADO_LABEL.get(d["estado"], d["estado"])
    d["sector_actual"] = sector_actual(d)
    return d


# ---------- Páginas ----------

@app.route("/")
def index():
    if not current_user():
        return redirect("/login")
    return render_template("app.html")


@app.route("/login", methods=["GET"])
def login_page():
    if current_user():
        return redirect("/")
    return render_template("login.html")


@app.route("/api/login", methods=["POST"])
def api_login():
    body = request.get_json(silent=True) or {}
    usuario = (body.get("usuario") or "").strip()
    password = body.get("password") or ""
    usuario_norm = usuario.lower()

    if _login_bloqueado(usuario_norm):
        return jsonify({"error": "Demasiados intentos fallidos. Esperá unos minutos e intentá de nuevo."}), 429

    with get_db() as con:
        row = con.execute("SELECT * FROM usuarios WHERE usuario=? AND activo=1", (usuario,)).fetchone()
    if not row or not check_password_hash(row["password_hash"], password):
        _registrar_intento_fallido(usuario_norm)
        return jsonify({"error": "Usuario o contraseña incorrectos"}), 401

    _limpiar_intentos(usuario_norm)
    session["user_id"] = row["id"]
    session["csrf_token"] = secrets.token_hex(16)
    return jsonify({"ok": True, "rol": row["rol"], "nombre": row["nombre"], "csrf_token": session["csrf_token"]})


@app.before_request
def _csrf_protect():
    if request.method in ("POST", "PUT", "DELETE") and request.path.startswith("/api/") and request.path != "/api/login":
        token = request.headers.get("X-CSRF-Token")
        if not token or token != session.get("csrf_token"):
            return jsonify({"error": "Token de seguridad inválido o vencido. Recargá la página e intentá de nuevo."}), 403


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/me")
@login_required
def api_me():
    u = current_user()
    if not session.get("csrf_token"):
        session["csrf_token"] = secrets.token_hex(16)
    return jsonify({
        "id": u["id"], "nombre": u["nombre"], "rol": u["rol"], "rol_label": ROL_LABEL[u["rol"]],
        "csrf_token": session["csrf_token"],
    })


# ---------- Archivos ----------

@app.route("/archivo/<tipo>/<filename>")
@login_required
def servir_archivo(tipo, filename):
    if tipo == "comprobantes":
        return send_from_directory(COMPROBANTES_DIR, filename)
    if tipo == "facturas":
        return send_from_directory(FACTURAS_DIR, filename)
    if tipo == "notas_credito":
        return send_from_directory(NOTAS_CREDITO_DIR, filename)
    return jsonify({"error": "No encontrado"}), 404


def _guardar_archivo(file_storage, carpeta, prefijo, extensiones_permitidas):
    filename = secure_filename(file_storage.filename or "")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if not filename or ext not in extensiones_permitidas:
        return None, f"Archivo inválido. Extensiones permitidas: {', '.join(extensiones_permitidas)}"
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    unico = secrets.token_hex(4)
    final_name = f"{prefijo}_{ts}_{unico}_{filename}"
    file_storage.save(os.path.join(carpeta, final_name))
    return final_name, None


# ---------- Pedidos: listado y detalle ----------

@app.route("/api/pedidos", methods=["GET"])
@login_required
def listar_pedidos():
    estado = request.args.get("estado", "")
    prioridad = request.args.get("prioridad", "")
    envio = request.args.get("envio", "")
    q = request.args.get("q", "").lower()
    sort = request.args.get("sort", "recientes")

    with get_db() as con:
        rows = con.execute("SELECT * FROM pedidos ORDER BY id DESC").fetchall()
    data = [pedido_to_dict(r) for r in rows]

    if estado:
        data = [p for p in data if p["estado"] == estado]
    if prioridad:
        data = [p for p in data if p["prioridad"] == prioridad]
    if envio in ("0", "1"):
        data = [p for p in data if p["envio"] == int(envio)]
    if q:
        def match(p):
            campos = [p.get("nro_cliente"), p.get("razon_social")]
            return any(q in str(c).lower() for c in campos if c)
        data = [p for p in data if match(p)]

    if sort == "prioridad":
        orden = {"Urgente": 0, "Alta": 1, "Normal": 2, "Baja": 3}
        data.sort(key=lambda p: (orden.get(p["prioridad"], 9), -p["id"]))

    return jsonify(data)


@app.route("/api/pedidos/<int:pedido_id>", methods=["GET"])
@login_required
def detalle_pedido(pedido_id):
    with get_db() as con:
        row = con.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
        if not row:
            return jsonify({"error": "Pedido no encontrado"}), 404
        eventos = con.execute(
            "SELECT * FROM eventos WHERE pedido_id=? ORDER BY id DESC", (pedido_id,)
        ).fetchall()
    d = pedido_to_dict(row)
    d["eventos"] = [dict(e) for e in eventos]
    return jsonify(d)


# ---------- Crear pedido (Ventas / Admin) ----------

@app.route("/api/pedidos", methods=["POST"])
@roles_required("ventas")
def crear_pedido(usuario):
    f = request.form
    nro_cliente = (f.get("nro_cliente") or "").strip()
    razon_social = (f.get("razon_social") or "").strip()
    modalidad_facturacion = f.get("modalidad_facturacion")
    prioridad = f.get("prioridad")
    prioridad_confirmada = f.get("prioridad_confirmada") == "true"

    if not nro_cliente or not razon_social:
        return jsonify({"error": "Falta número de cliente o razón social"}), 400
    if modalidad_facturacion not in ("cargado", "manual"):
        return jsonify({"error": "Modalidad de facturación inválida"}), 400
    if prioridad not in PRIORIDADES:
        return jsonify({"error": "Prioridad inválida"}), 400
    if not prioridad_confirmada:
        return jsonify({"error": "Debés confirmar la prioridad seleccionada antes de enviar la solicitud"}), 400

    modalidad_pago = None
    monto = f.get("monto") or None
    comprobante_filename = None

    if modalidad_facturacion != "cargado":
        modalidad_pago = f.get("modalidad_pago")
        if modalidad_pago not in MODALIDADES_PAGO:
            return jsonify({"error": "Modalidad de pago inválida"}), 400
        archivo = request.files.get("comprobante")
        if not archivo or not archivo.filename:
            return jsonify({"error": "Falta el comprobante de pago"}), 400
        comprobante_filename, error = _guardar_archivo(archivo, COMPROBANTES_DIR, "comp", ALLOWED_COMPROBANTE)
        if error:
            return jsonify({"error": error}), 400

    observaciones = f.get("observaciones") or None

    with get_db() as con:
        cur = con.execute("""
            INSERT INTO pedidos (
                nro_cliente, razon_social, modalidad_facturacion,
                modalidad_pago, monto, comprobante_path, prioridad, estado,
                observaciones, creado_por
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            nro_cliente, razon_social, modalidad_facturacion,
            modalidad_pago, monto, comprobante_filename, prioridad, "A_FACTURAR",
            observaciones, usuario["id"],
        ))
        pedido_id = cur.lastrowid
        log_evento(con, pedido_id, "creado", usuario, f"Modalidad: {modalidad_facturacion}")
        log_evento(con, pedido_id, "prioridad_asignada", usuario, f"Prioridad inicial: {prioridad}")
        row = con.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
    return jsonify(pedido_to_dict(row)), 201


# ---------- Cambiar prioridad (creador del pedido o Admin) ----------

@app.route("/api/pedidos/<int:pedido_id>/prioridad", methods=["POST"])
@login_required
def cambiar_prioridad(pedido_id):
    usuario = current_user()
    body = request.get_json(silent=True) or {}
    nueva = body.get("prioridad")
    motivo = (body.get("motivo") or "").strip()

    if nueva not in PRIORIDADES:
        return jsonify({"error": "Prioridad inválida"}), 400
    if not motivo:
        return jsonify({"error": "Tenés que indicar el motivo del cambio de prioridad"}), 400

    with get_db() as con:
        row = con.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
        if not row:
            return jsonify({"error": "Pedido no encontrado"}), 404
        if usuario["rol"] != "admin" and usuario["id"] != row["creado_por"]:
            return jsonify({"error": "Solo el vendedor que creó la solicitud o un administrador pueden cambiar la prioridad"}), 403
        anterior = row["prioridad"]
        if anterior == nueva:
            return jsonify({"error": "La solicitud ya tiene esa prioridad"}), 400
        cur = con.execute(
            "UPDATE pedidos SET prioridad=? WHERE id=? AND prioridad=?",
            (nueva, pedido_id, anterior),
        )
        if cur.rowcount == 0:
            return jsonify({"error": "La prioridad ya fue modificada por otro usuario, volvé a intentarlo"}), 409
        log_evento(con, pedido_id, "prioridad_cambiada", usuario, f"{anterior} → {nueva}. Motivo: {motivo}")
        row = con.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
    return jsonify(pedido_to_dict(row))


# ---------- Facturación: emitir factura ----------

@app.route("/api/pedidos/<int:pedido_id>/emitir_factura", methods=["POST"])
@roles_required("facturacion")
def emitir_factura(usuario, pedido_id):
    with get_db() as con:
        row = con.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
        if not row:
            return jsonify({"error": "Pedido no encontrado"}), 404
        if row["estado"] != "A_FACTURAR":
            return jsonify({"error": "Este pedido no está pendiente de facturación"}), 409

        f = request.form
        cobranza_nota = (f.get("cobranza_nota") or "").strip()
        pago_confirmado = f.get("pago_confirmado") == "true"

        if row["modalidad_facturacion"] == "manual" and not pago_confirmado:
            return jsonify({"error": "Tenés que confirmar el pago antes de emitir la factura"}), 400
        if not cobranza_nota:
            return jsonify({"error": "Tenés que registrar la cobranza"}), 400

        archivo = request.files.get("factura_pdf")
        if not archivo or not archivo.filename:
            return jsonify({"error": "Falta adjuntar el PDF de la factura"}), 400
        factura_filename, error = _guardar_archivo(archivo, FACTURAS_DIR, f"factura{pedido_id}", ALLOWED_FACTURA)
        if error:
            return jsonify({"error": error}), 400

        cur = con.execute("""
            UPDATE pedidos SET
                factura_pdf_path=?, pago_confirmado=?, cobranza_nota=?,
                estado='FACTURADO', facturado_por=?, facturado_at=CURRENT_TIMESTAMP
            WHERE id=? AND estado='A_FACTURAR'
        """, (factura_filename, 1 if pago_confirmado else 0, cobranza_nota, usuario["id"], pedido_id))
        if cur.rowcount == 0:
            return jsonify({"error": "Este pedido ya fue facturado por otro usuario"}), 409
        log_evento(con, pedido_id, "facturado", usuario, cobranza_nota)
        row = con.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
    return jsonify(pedido_to_dict(row))


# ---------- Ventas: definir envío o retiro ----------

@app.route("/api/pedidos/<int:pedido_id>/definir_envio", methods=["POST"])
@roles_required("ventas")
def definir_envio(usuario, pedido_id):
    body = request.get_json(silent=True) or {}
    if "envio" not in body:
        return jsonify({"error": "Falta indicar si el pedido es con envío o retiro"}), 400
    envio = bool(body["envio"])

    with get_db() as con:
        row = con.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
        if not row:
            return jsonify({"error": "Pedido no encontrado"}), 404
        if row["estado"] != "FACTURADO":
            return jsonify({"error": "Este pedido no está en condiciones de definir envío/retiro"}), 409
        cur = con.execute("""
            UPDATE pedidos SET envio=?, estado='EN_PREPARACION', en_preparacion_at=CURRENT_TIMESTAMP
            WHERE id=? AND estado='FACTURADO'
        """, (1 if envio else 0, pedido_id))
        if cur.rowcount == 0:
            return jsonify({"error": "Este pedido ya fue actualizado por otro usuario"}), 409
        log_evento(con, pedido_id, "envio_definido", usuario, "Con envío" if envio else "Retiro en local")
        row = con.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
    return jsonify(pedido_to_dict(row))


# ---------- Depósito: preparar / embalar ----------

@app.route("/api/pedidos/<int:pedido_id>/preparar", methods=["POST"])
@roles_required("deposito")
def preparar_pedido(usuario, pedido_id):
    with get_db() as con:
        row = con.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
        if not row:
            return jsonify({"error": "Pedido no encontrado"}), 404
        if row["estado"] != "EN_PREPARACION":
            return jsonify({"error": "Este pedido no está en preparación"}), 409

        if row["envio"]:
            cur = con.execute("""
                UPDATE pedidos SET estado='EN_FLEX', en_flex_at=CURRENT_TIMESTAMP, prep_por=?
                WHERE id=? AND estado='EN_PREPARACION'
            """, (usuario["id"], pedido_id))
            if cur.rowcount == 0:
                return jsonify({"error": "Este pedido ya fue actualizado por otro usuario"}), 409
            log_evento(con, pedido_id, "embalado_derivado_flex", usuario, "Embalado y derivado a Flex")
        else:
            cur = con.execute("""
                UPDATE pedidos SET estado='LISTO_MOSTRADOR', listo_mostrador_at=CURRENT_TIMESTAMP, prep_por=?
                WHERE id=? AND estado='EN_PREPARACION'
            """, (usuario["id"], pedido_id))
            if cur.rowcount == 0:
                return jsonify({"error": "Este pedido ya fue actualizado por otro usuario"}), 409
            log_evento(con, pedido_id, "preparado_derivado_mostrador", usuario, "Preparado y derivado a Mostrador")

        row = con.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
    return jsonify(pedido_to_dict(row))


# ---------- Flex: programar entrega y marcar entregado ----------

@app.route("/api/pedidos/<int:pedido_id>/programar", methods=["POST"])
@roles_required("flex")
def programar_entrega(usuario, pedido_id):
    body = request.get_json(silent=True) or {}
    fecha = (body.get("fecha_entrega_estimada") or "").strip()
    if not fecha:
        return jsonify({"error": "Falta la fecha estimada de entrega"}), 400

    with get_db() as con:
        row = con.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
        if not row:
            return jsonify({"error": "Pedido no encontrado"}), 404
        if row["estado"] != "EN_FLEX":
            return jsonify({"error": "Este pedido no está pendiente de programación en Flex"}), 409
        cur = con.execute("""
            UPDATE pedidos SET fecha_entrega_estimada=?, estado='PROGRAMADO',
                programado_at=CURRENT_TIMESTAMP, flex_por=?
            WHERE id=? AND estado='EN_FLEX'
        """, (fecha, usuario["id"], pedido_id))
        if cur.rowcount == 0:
            return jsonify({"error": "Este pedido ya fue actualizado por otro usuario"}), 409
        log_evento(con, pedido_id, "programado", usuario, f"Fecha estimada de entrega: {fecha}")
        row = con.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
    return jsonify(pedido_to_dict(row))


@app.route("/api/pedidos/<int:pedido_id>/entregar", methods=["POST"])
@roles_required("flex")
def marcar_entregado(usuario, pedido_id):
    with get_db() as con:
        row = con.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
        if not row:
            return jsonify({"error": "Pedido no encontrado"}), 404
        if row["estado"] != "PROGRAMADO":
            return jsonify({"error": "Este pedido no está programado para entrega"}), 409
        cur = con.execute("""
            UPDATE pedidos SET estado='ENTREGADO', entregado_at=CURRENT_TIMESTAMP, entregado_por=?
            WHERE id=? AND estado='PROGRAMADO'
        """, (usuario["id"], pedido_id))
        if cur.rowcount == 0:
            return jsonify({"error": "Este pedido ya fue actualizado por otro usuario"}), 409
        log_evento(con, pedido_id, "entregado", usuario, "")
        row = con.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
    return jsonify(pedido_to_dict(row))


# ---------- Mostrador: marcar retirado ----------

@app.route("/api/pedidos/<int:pedido_id>/retirar", methods=["POST"])
@roles_required("mostrador")
def marcar_retirado(usuario, pedido_id):
    with get_db() as con:
        row = con.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
        if not row:
            return jsonify({"error": "Pedido no encontrado"}), 404
        if row["estado"] != "LISTO_MOSTRADOR":
            return jsonify({"error": "Este pedido no está listo para retirar"}), 409
        cur = con.execute("""
            UPDATE pedidos SET estado='RETIRADO', retirado_at=CURRENT_TIMESTAMP, mostrador_por=?
            WHERE id=? AND estado='LISTO_MOSTRADOR'
        """, (usuario["id"], pedido_id))
        if cur.rowcount == 0:
            return jsonify({"error": "Este pedido ya fue actualizado por otro usuario"}), 409
        log_evento(con, pedido_id, "retirado", usuario, "")
        row = con.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
    return jsonify(pedido_to_dict(row))


# ---------- Nota de crédito (no reemplaza ni borra la factura original) ----------

@app.route("/api/pedidos/<int:pedido_id>/nota_credito/solicitar", methods=["POST"])
@roles_required("ventas")
def solicitar_nota_credito(usuario, pedido_id):
    body = request.get_json(silent=True) or {}
    motivo = (body.get("motivo") or "").strip()
    if not motivo:
        return jsonify({"error": "Tenés que indicar el motivo de la nota de crédito"}), 400

    with get_db() as con:
        row = con.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
        if not row:
            return jsonify({"error": "Pedido no encontrado"}), 404
        if not row["factura_pdf_path"]:
            return jsonify({"error": "Este pedido todavía no tiene una factura emitida"}), 409
        if row["nota_credito_estado"] is not None:
            return jsonify({"error": "Ya existe una nota de crédito solicitada o emitida para este pedido"}), 409
        cur = con.execute("""
            UPDATE pedidos SET nota_credito_estado='SOLICITADA', nota_credito_motivo=?,
                nota_credito_solicitada_por=?, nota_credito_solicitada_at=CURRENT_TIMESTAMP
            WHERE id=? AND nota_credito_estado IS NULL
        """, (motivo, usuario["id"], pedido_id))
        if cur.rowcount == 0:
            return jsonify({"error": "Ya existe una nota de crédito solicitada o emitida para este pedido"}), 409
        log_evento(con, pedido_id, "nota_credito_solicitada", usuario, motivo)
        row = con.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
    return jsonify(pedido_to_dict(row))


@app.route("/api/pedidos/<int:pedido_id>/nota_credito/emitir", methods=["POST"])
@roles_required("facturacion")
def emitir_nota_credito(usuario, pedido_id):
    with get_db() as con:
        row = con.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
        if not row:
            return jsonify({"error": "Pedido no encontrado"}), 404
        if row["nota_credito_estado"] != "SOLICITADA":
            return jsonify({"error": "Este pedido no tiene una nota de crédito pendiente de emitir"}), 409

        archivo = request.files.get("nota_credito_pdf")
        if not archivo or not archivo.filename:
            return jsonify({"error": "Falta adjuntar el PDF de la nota de crédito"}), 400
        filename, error = _guardar_archivo(archivo, NOTAS_CREDITO_DIR, f"notacredito{pedido_id}", ALLOWED_NOTA_CREDITO)
        if error:
            return jsonify({"error": error}), 400

        cur = con.execute("""
            UPDATE pedidos SET nota_credito_estado='EMITIDA', nota_credito_pdf_path=?,
                nota_credito_emitida_por=?, nota_credito_emitida_at=CURRENT_TIMESTAMP
            WHERE id=? AND nota_credito_estado='SOLICITADA'
        """, (filename, usuario["id"], pedido_id))
        if cur.rowcount == 0:
            return jsonify({"error": "Este pedido ya no tiene una nota de crédito pendiente de emitir"}), 409
        log_evento(con, pedido_id, "nota_credito_emitida", usuario, "")
        row = con.execute("SELECT * FROM pedidos WHERE id=?", (pedido_id,)).fetchone()
    return jsonify(pedido_to_dict(row))


# ---------- Administración de usuarios ----------

@app.route("/api/usuarios", methods=["GET"])
@roles_required("admin")
def listar_usuarios(usuario):
    with get_db() as con:
        rows = con.execute("SELECT id, nombre, usuario, rol, activo, created_at FROM usuarios ORDER BY id").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/usuarios", methods=["POST"])
@roles_required("admin")
def crear_usuario(usuario_admin):
    body = request.get_json(silent=True) or {}
    nombre = (body.get("nombre") or "").strip()
    usuario_login = (body.get("usuario") or "").strip()
    password = body.get("password") or ""
    rol = body.get("rol")

    if not nombre or not usuario_login or not password:
        return jsonify({"error": "Faltan datos obligatorios"}), 400
    if rol not in ROLES:
        return jsonify({"error": "Rol inválido"}), 400
    if len(password) < 8:
        return jsonify({"error": "La contraseña debe tener al menos 8 caracteres"}), 400

    try:
        with get_db() as con:
            cur = con.execute(
                "INSERT INTO usuarios (nombre, usuario, password_hash, rol) VALUES (?,?,?,?)",
                (nombre, usuario_login, generate_password_hash(password), rol),
            )
            nuevo_id = cur.lastrowid
            row = con.execute("SELECT id, nombre, usuario, rol, activo FROM usuarios WHERE id=?", (nuevo_id,)).fetchone()
    except sqlite3.IntegrityError:
        return jsonify({"error": "Ese nombre de usuario ya existe"}), 409
    return jsonify(dict(row)), 201


@app.route("/api/usuarios/<int:user_id>", methods=["PUT"])
@roles_required("admin")
def editar_usuario(usuario_admin, user_id):
    body = request.get_json(silent=True) or {}
    with get_db() as con:
        row = con.execute("SELECT * FROM usuarios WHERE id=?", (user_id,)).fetchone()
        if not row:
            return jsonify({"error": "Usuario no encontrado"}), 404

        campos, valores = [], []
        if "nombre" in body and body["nombre"].strip():
            campos.append("nombre=?"); valores.append(body["nombre"].strip())
        if "rol" in body:
            if body["rol"] not in ROLES:
                return jsonify({"error": "Rol inválido"}), 400
            campos.append("rol=?"); valores.append(body["rol"])
        if "activo" in body:
            campos.append("activo=?"); valores.append(1 if body["activo"] else 0)
        if body.get("password"):
            if len(body["password"]) < 8:
                return jsonify({"error": "La contraseña debe tener al menos 8 caracteres"}), 400
            campos.append("password_hash=?"); valores.append(generate_password_hash(body["password"]))

        if campos:
            valores.append(user_id)
            con.execute(f"UPDATE usuarios SET {', '.join(campos)} WHERE id=?", valores)
        row = con.execute("SELECT id, nombre, usuario, rol, activo FROM usuarios WHERE id=?", (user_id,)).fetchone()
    return jsonify(dict(row))


# ---------- Métricas por vendedor ----------

def _fila_metrica_vacia(usuario_row):
    return {
        "vendedor_id": usuario_row["id"],
        "vendedor_nombre": usuario_row["nombre"],
        "vendedor_activo": bool(usuario_row["activo"]),
        "total_solicitudes": 0,
        "facturadas": 0,
        "cerradas": 0,
        "con_nota_credito": 0,
        "por_prioridad": {pr: 0 for pr in PRIORIDADES},
        "_montos": [],
    }


@app.route("/api/metricas/vendedores", methods=["GET"])
@roles_required("admin")
def metricas_vendedores(usuario_admin):
    desde = request.args.get("desde", "")
    hasta = request.args.get("hasta", "")

    with get_db() as con:
        pedidos = [dict(r) for r in con.execute("SELECT * FROM pedidos")]
        todos_usuarios = {r["id"]: dict(r) for r in con.execute("SELECT id, nombre, rol, activo FROM usuarios")}

    def en_rango(p):
        fecha = (p["created_at"] or "")[:10]
        if desde and fecha < desde:
            return False
        if hasta and fecha > hasta:
            return False
        return True

    pedidos = [p for p in pedidos if en_rango(p)]

    por_vendedor = {}
    for uid, u in todos_usuarios.items():
        if u["rol"] == "ventas":
            por_vendedor[uid] = _fila_metrica_vacia(u)

    for p in pedidos:
        vid = p["creado_por"]
        if vid not in por_vendedor:
            u = todos_usuarios.get(vid) or {"id": vid, "nombre": "Usuario eliminado", "activo": 0}
            por_vendedor[vid] = _fila_metrica_vacia(u)
        e = por_vendedor[vid]
        e["total_solicitudes"] += 1
        if p["facturado_at"]:
            e["facturadas"] += 1
        if p["estado"] in ("ENTREGADO", "RETIRADO"):
            e["cerradas"] += 1
        if p["nota_credito_estado"]:
            e["con_nota_credito"] += 1
        if p["prioridad"] in e["por_prioridad"]:
            e["por_prioridad"][p["prioridad"]] += 1
        if _es_numero(p["monto"]):
            e["_montos"].append(float(p["monto"]))

    resultado = []
    for e in por_vendedor.values():
        montos = e.pop("_montos")
        e["monto_total_registrado"] = round(sum(montos), 2) if montos else 0
        e["monto_cantidad_registros"] = len(montos)
        resultado.append(e)
    resultado.sort(key=lambda r: -r["total_solicitudes"])

    return jsonify({"desde": desde, "hasta": hasta, "vendedores": resultado})


# ---------- Metadatos para el frontend ----------

@app.route("/api/meta")
@login_required
def api_meta():
    return jsonify({
        "roles": ROLES,
        "rol_label": ROL_LABEL,
        "prioridades": PRIORIDADES,
        "estados": ESTADOS,
        "estado_label": ESTADO_LABEL,
        "modalidades_pago": MODALIDADES_PAGO,
        "nota_credito_label": NOTA_CREDITO_LABEL,
    })


# ---------- Manejo de errores ----------

@app.errorhandler(404)
def error_404(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "No encontrado"}), 404
    return e


@app.errorhandler(500)
def error_500(e):
    app.logger.exception("Error interno no controlado")
    if request.path.startswith("/api/"):
        return jsonify({"error": "Error interno del servidor. Intentá de nuevo."}), 500
    return e


if __name__ == "__main__":
    import socket
    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except Exception:
        local_ip = "tu-ip-local"
    print("\n" + "=" * 55)
    print("  FLUJO VENTAS — Espacio Electrónica")
    print("=" * 55)
    print(f"\n  Abrí en esta computadora:  http://localhost:5050")
    print(f"  Otros en la red acceden:   http://{local_ip}:5050")
    print(f"\n  Usuario admin inicial: {ADMIN_USER}")
    if ADMIN_USER == "admin" and ADMIN_PASS == "admin123":
        print("\n  ADVERTENCIA: sigue en uso la contraseña de admin por defecto (admin123).")
        print("  Cambiala en el archivo .env y volvé a iniciar antes de usar en producción.")
    print("\n  Ctrl+C para apagar el servidor")
    print("=" * 55 + "\n")
    from waitress import serve
    serve(app, host="0.0.0.0", port=5050, threads=8)

import os
import shutil
import sqlite3
from datetime import datetime

BASE_DIR = os.path.dirname(__file__)
DB = os.path.join(BASE_DIR, "flujo_ventas.db")
UPLOADS = os.path.join(BASE_DIR, "uploads")
BACKUPS_DIR = os.path.join(BASE_DIR, "backups")


def main():
    if not os.path.exists(DB):
        print(f"No se encontró la base de datos en {DB}")
        return

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(BACKUPS_DIR, ts)
    os.makedirs(dest, exist_ok=True)

    src_con = sqlite3.connect(DB)
    dst_con = sqlite3.connect(os.path.join(dest, "flujo_ventas.db"))
    with dst_con:
        src_con.backup(dst_con)
    src_con.close()
    dst_con.close()

    if os.path.exists(UPLOADS):
        shutil.copytree(UPLOADS, os.path.join(dest, "uploads"))

    print(f"Backup creado en: {dest}")
    print("Podés hacer esta copia con el sistema corriendo, sin detenerlo.")


if __name__ == "__main__":
    main()

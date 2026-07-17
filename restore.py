import os
import shutil
import sys

BASE_DIR = os.path.dirname(__file__)
DB = os.path.join(BASE_DIR, "flujo_ventas.db")
UPLOADS = os.path.join(BASE_DIR, "uploads")
BACKUPS_DIR = os.path.join(BASE_DIR, "backups")


def main():
    if len(sys.argv) < 2:
        print("Backups disponibles:")
        if os.path.isdir(BACKUPS_DIR):
            for nombre in sorted(os.listdir(BACKUPS_DIR)):
                print(" -", nombre)
        print("\nUso: python restore.py <nombre_de_carpeta_de_backup>")
        return

    carpeta = sys.argv[1]
    origen = os.path.join(BACKUPS_DIR, carpeta)
    if not os.path.isdir(origen) or not os.path.exists(os.path.join(origen, "flujo_ventas.db")):
        print(f"No existe ese backup: {origen}")
        return

    print("IMPORTANTE: el servidor (python app.py) debe estar DETENIDO antes de continuar.")
    confirm = input(f"Esto va a REEMPLAZAR los datos actuales con el backup '{carpeta}'. Escribí SI para confirmar: ")
    if confirm.strip() != "SI":
        print("Cancelado.")
        return

    shutil.copy(os.path.join(origen, "flujo_ventas.db"), DB)
    for ext in ("-wal", "-shm"):
        wal_path = DB + ext
        if os.path.exists(wal_path):
            os.remove(wal_path)

    if os.path.exists(UPLOADS):
        shutil.rmtree(UPLOADS)
    if os.path.exists(os.path.join(origen, "uploads")):
        shutil.copytree(os.path.join(origen, "uploads"), UPLOADS)

    print("Restauración completa. Iniciá el servidor de nuevo (python app.py).")


if __name__ == "__main__":
    main()

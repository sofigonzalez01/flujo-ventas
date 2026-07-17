# Sistema de Flujo de Ventas - Espacio Electrónica

Sistema web para la gestión y seguimiento del flujo de ventas de Espacio Electrónica, desarrollado con Flask y SQLite.

## Descripción

Aplicación interna para registrar y controlar operaciones de venta, incluyendo comprobantes, facturas y notas de crédito. Pensada para uso en red local por el equipo de la oficina.

## Tecnologías

- Python (Flask)
- SQLite
- HTML/CSS (Jinja2 templates)

## Instalación

1. Cloná el repositorio:

git clone https://github.com/sofigonzalez01/flujo-ventas.git

2. Instalá las dependencias:

pip install -r requirements.txt

3. Creá un archivo .env con las variables de entorno necesarias (no incluido en el repo por seguridad).
4. Ejecutá la aplicación:

python app.py

## Scripts útiles

- iniciar.bat: inicia la aplicación
- backup.bat / backup.py: genera un backup de la base de datos
- restore.bat / restore.py: restaura la base de datos desde un backup

## Notas

- La base de datos (flujo_ventas.db) y la carpeta uploads/ (comprobantes, facturas, notas de crédito) están excluidas del repositorio por contener datos internos y sensibles de la empresa.
- Ver INSTRUCCIONES.txt para más detalles de uso.

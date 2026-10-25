# Papelería Garaje POS

Aplicación web sencilla para punto de venta, inventario y registro de gastos, construida con Flask y SQLite.

## Requisitos

- Python 3.10 o superior
- Dependencias de `requirements.txt`

## Instalación y ejecución

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Abre `http://127.0.0.1:5000` en el navegador. La base `papeleria.db` se crea junto a `app.py` al iniciar la aplicación.

Para usar una clave de sesión propia, define la variable `FLASK_SECRET_KEY` antes de iniciar la aplicación. El valor de desarrollo incluido en el código no debe usarse en producción.

## Módulos

- Punto de venta con lector de código de barras y servicios rápidos
- Inventario de productos, precios y existencias
- Registro de ventas y gastos
- Resumen financiero

## Nota

La base SQLite local se excluye del repositorio para evitar publicar datos de operación. La aplicación actual es una herramienta local de desarrollo; revisa la configuración de seguridad antes de exponerla en una red o desplegarla públicamente.

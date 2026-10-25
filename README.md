# Papelería Garaje POS

Aplicación web sencilla para punto de venta, inventario y registro de gastos, construida con Flask. En local utiliza SQLite; en Vercel se conecta a PostgreSQL.

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

Para usar una clave de sesión propia, define `FLASK_SECRET_KEY` y `ADMIN_PASSWORD`. El valor de desarrollo incluido en el código no debe usarse en producción.

## Módulos

- Punto de venta con lector de código de barras y servicios rápidos
- Inventario de productos, precios y existencias
- Registro automático del costo del stock inicial al crear productos con precio de compra
- Reposición desde Finanzas, que registra el egreso y aumenta las existencias
- Registro de ventas y gastos operativos
- Resumen financiero

## Nota

La base SQLite local se excluye del repositorio para evitar publicar datos de operación. En Vercel las rutas privadas requieren la contraseña de administración configurada en `ADMIN_PASSWORD`.

## Despliegue en Vercel

1. Sube este repositorio a GitHub y conéctalo desde Vercel como un proyecto Python/Flask.
2. Crea una base PostgreSQL desde Vercel Marketplace (por ejemplo, Neon o Supabase) y configura su cadena de conexión como `DATABASE_URL`.
3. En **Settings → Environment Variables**, define `FLASK_SECRET_KEY` con un secreto aleatorio y `ADMIN_PASSWORD` con una contraseña larga y única. Añádelas a Production y Preview según corresponda.
4. Despliega el proyecto. La aplicación crea las tablas PostgreSQL al iniciar. El archivo `.python-version` fija Python 3.13 y `requirements.txt` declara Flask y Psycopg.

En local, sin `DATABASE_URL`, la app sigue usando `papeleria.db`. En Vercel, nunca se usa el archivo SQLite.

### Datos existentes

La base local no se incluye en GitHub y no se copia automáticamente a PostgreSQL. El primer despliegue empezará con tablas vacías. Si quieres conservar productos, ventas y gastos, primero haz una copia de seguridad y migra esos registros a la base PostgreSQL antes de usar producción. No publiques el archivo SQLite ni pegues las credenciales en el repositorio.

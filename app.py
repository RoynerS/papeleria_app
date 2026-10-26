from flask import Flask, Response, render_template, request, jsonify, redirect, url_for, flash, session, abort, send_from_directory
from pathlib import Path
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
import csv
import hmac
import io
import json
import os
import re
import sqlite3

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-only-change-me')
app.config['DATABASE_URL'] = (os.environ.get('DATABASE_URL') or os.environ.get('POSTGRES_URL') or '').strip()
IS_VERCEL = os.environ.get('VERCEL') == '1'
DB_NAME = Path(__file__).with_name('papeleria.db')
BOGOTA = ZoneInfo('America/Bogota')
QUICK_SERVICES = {
    'Fotocopia B/N': 1500,
    'Fotocopia Color': 5000,
    'Impresión Documento': 3000,
    'Hojas Blancas (5 un)': 2000,
    'Escaneo / Trámite': 10000,
}
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=IS_VERCEL,
)

if IS_VERCEL and not os.environ.get('FLASK_SECRET_KEY'):
    raise RuntimeError('Configura FLASK_SECRET_KEY en las variables de entorno de Vercel.')
if IS_VERCEL and not os.environ.get('ADMIN_PASSWORD'):
    raise RuntimeError('Configura ADMIN_PASSWORD en las variables de entorno de Vercel.')


def _sql(statement):
    return statement.replace('?', '%s') if app.config['DATABASE_URL'] else statement


def get_db_connection():
    if app.config['DATABASE_URL']:
        import psycopg
        from psycopg.rows import dict_row
        return PostgresConnection(psycopg.connect(app.config['DATABASE_URL'], row_factory=dict_row))
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def is_unique_violation(error):
    return isinstance(error, sqlite3.IntegrityError) or getattr(error, 'sqlstate', None) == '23505'


class PostgresConnection:
    """Thin DB-API adapter so existing parameterized queries work on both engines."""
    def __init__(self, connection):
        self.connection = connection

    def execute(self, statement, params=()):
        return self.connection.execute(_sql(statement), params)

    def cursor(self):
        return PostgresCursor(self.connection.cursor())

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()


class PostgresCursor:
    def __init__(self, cursor):
        self.cursor = cursor

    def execute(self, statement, params=()):
        self.cursor.execute(_sql(statement), params)
        return self

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()

    @property
    def rowcount(self):
        return self.cursor.rowcount

def init_db():
    conn = get_db_connection()
    if app.config['DATABASE_URL']:
        statements = [
            '''CREATE TABLE IF NOT EXISTS customers (
                national_id TEXT PRIMARY KEY, name TEXT, email TEXT, points INTEGER NOT NULL DEFAULT 0,
                consent_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'America/Bogota'))''',
            '''CREATE TABLE IF NOT EXISTS products (
                id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, barcode TEXT UNIQUE,
                cost_price DOUBLE PRECISION DEFAULT 0, price DOUBLE PRECISION NOT NULL,
                stock INTEGER NOT NULL DEFAULT 0)''',
            '''CREATE TABLE IF NOT EXISTS sales (
                id BIGSERIAL PRIMARY KEY, total DOUBLE PRECISION NOT NULL,
                customer_id TEXT REFERENCES customers(national_id), customer_name TEXT, customer_email TEXT,
                points_earned INTEGER NOT NULL DEFAULT 0,
                date TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'America/Bogota'))''',
            '''CREATE TABLE IF NOT EXISTS sale_items (
                id BIGSERIAL PRIMARY KEY, sale_id BIGINT REFERENCES sales(id),
                product_id BIGINT REFERENCES products(id), quantity INTEGER,
                price DOUBLE PRECISION, item_name TEXT)''',
            '''CREATE TABLE IF NOT EXISTS sales_audit (
                id BIGSERIAL PRIMARY KEY, sale_id BIGINT NOT NULL, action TEXT NOT NULL,
                total DOUBLE PRECISION NOT NULL, customer_id TEXT, points_earned INTEGER NOT NULL DEFAULT 0,
                customer_name TEXT, customer_email TEXT,
                items TEXT NOT NULL DEFAULT '[]',
                occurred_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'America/Bogota'))''',
            '''CREATE TABLE IF NOT EXISTS expenses (
                id BIGSERIAL PRIMARY KEY, description TEXT NOT NULL, category TEXT NOT NULL,
                amount DOUBLE PRECISION NOT NULL, quantity INTEGER DEFAULT 1,
                unit_price DOUBLE PRECISION DEFAULT 0,
                product_id BIGINT REFERENCES products(id),
                date TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'America/Bogota'))''',
        ]
        for statement in statements:
            conn.execute(statement)
        conn.execute('ALTER TABLE products ADD COLUMN IF NOT EXISTS barcode TEXT')
        conn.execute('ALTER TABLE products ADD COLUMN IF NOT EXISTS cost_price DOUBLE PRECISION DEFAULT 0')
        conn.execute('ALTER TABLE expenses ADD COLUMN IF NOT EXISTS quantity INTEGER DEFAULT 1')
        conn.execute('ALTER TABLE expenses ADD COLUMN IF NOT EXISTS unit_price DOUBLE PRECISION DEFAULT 0')
        conn.execute('ALTER TABLE expenses ADD COLUMN IF NOT EXISTS product_id BIGINT REFERENCES products(id)')
        conn.execute('ALTER TABLE sale_items ADD COLUMN IF NOT EXISTS item_name TEXT')
        conn.execute('ALTER TABLE sales ADD COLUMN IF NOT EXISTS customer_id TEXT REFERENCES customers(national_id)')
        conn.execute('ALTER TABLE customers ADD COLUMN IF NOT EXISTS email TEXT')
        conn.execute('ALTER TABLE customers ADD COLUMN IF NOT EXISTS name TEXT')
        conn.execute('ALTER TABLE customers ADD COLUMN IF NOT EXISTS consent_at TIMESTAMP')
        conn.execute('ALTER TABLE sales ADD COLUMN IF NOT EXISTS customer_email TEXT')
        conn.execute('ALTER TABLE sales ADD COLUMN IF NOT EXISTS customer_name TEXT')
        conn.execute('ALTER TABLE sales ADD COLUMN IF NOT EXISTS points_earned INTEGER NOT NULL DEFAULT 0')
        conn.execute('ALTER TABLE sales_audit ADD COLUMN IF NOT EXISTS customer_id TEXT')
        conn.execute('ALTER TABLE sales_audit ADD COLUMN IF NOT EXISTS customer_email TEXT')
        conn.execute('ALTER TABLE sales_audit ADD COLUMN IF NOT EXISTS customer_name TEXT')
        conn.execute('ALTER TABLE sales_audit ADD COLUMN IF NOT EXISTS points_earned INTEGER NOT NULL DEFAULT 0')
        conn.execute("ALTER TABLE sales_audit ADD COLUMN IF NOT EXISTS items TEXT NOT NULL DEFAULT '[]'")
    else:
        conn.execute('''
        CREATE TABLE IF NOT EXISTS customers (
            national_id TEXT PRIMARY KEY,
            name TEXT,
            email TEXT,
            points INTEGER NOT NULL DEFAULT 0,
            consent_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        conn.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            barcode TEXT UNIQUE,
            cost_price REAL DEFAULT 0,
            price REAL NOT NULL,
            stock INTEGER NOT NULL DEFAULT 0
        )
        ''')
        conn.execute('''
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            total REAL NOT NULL,
            customer_id TEXT,
            customer_name TEXT,
            customer_email TEXT,
            points_earned INTEGER NOT NULL DEFAULT 0,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        conn.execute('''
        CREATE TABLE IF NOT EXISTS sale_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_id INTEGER,
            product_id INTEGER,
            quantity INTEGER,
            price REAL,
            item_name TEXT,
            FOREIGN KEY(sale_id) REFERENCES sales(id),
            FOREIGN KEY(product_id) REFERENCES products(id)
        )
        ''')
        conn.execute('''
        CREATE TABLE IF NOT EXISTS sales_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            total REAL NOT NULL,
            customer_id TEXT,
            customer_name TEXT,
            customer_email TEXT,
            points_earned INTEGER NOT NULL DEFAULT 0,
            items TEXT NOT NULL DEFAULT '[]',
            occurred_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        conn.execute('''
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            description TEXT NOT NULL,
            category TEXT NOT NULL,
            amount REAL NOT NULL,
            quantity INTEGER DEFAULT 1,
            unit_price REAL DEFAULT 0,
            product_id INTEGER,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        for migration in [
            'ALTER TABLE products ADD COLUMN barcode TEXT',
            'ALTER TABLE products ADD COLUMN cost_price REAL DEFAULT 0',
            'ALTER TABLE expenses ADD COLUMN quantity INTEGER DEFAULT 1',
            'ALTER TABLE expenses ADD COLUMN unit_price REAL DEFAULT 0',
            'ALTER TABLE expenses ADD COLUMN product_id INTEGER',
            'ALTER TABLE sale_items ADD COLUMN item_name TEXT',
            'ALTER TABLE sales ADD COLUMN customer_id TEXT',
            'ALTER TABLE customers ADD COLUMN email TEXT',
            'ALTER TABLE customers ADD COLUMN name TEXT',
            'ALTER TABLE customers ADD COLUMN consent_at TIMESTAMP',
            'ALTER TABLE sales ADD COLUMN customer_email TEXT',
            'ALTER TABLE sales ADD COLUMN customer_name TEXT',
            'ALTER TABLE sales ADD COLUMN points_earned INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE sales_audit ADD COLUMN customer_email TEXT',
            'ALTER TABLE sales_audit ADD COLUMN customer_name TEXT',
        ]:
            try:
                conn.execute(migration)
            except sqlite3.OperationalError:
                pass
    conn.commit()
    # Preserve an auditable snapshot for sales recorded before this feature existed.
    legacy_sales = conn.execute('''
        SELECT s.id, s.total, s.date, s.customer_id, s.customer_name, s.customer_email, s.points_earned
        FROM sales s
        WHERE NOT EXISTS (SELECT 1 FROM sales_audit a WHERE a.sale_id=s.id AND a.action='VENTA REGISTRADA')
    ''').fetchall()
    for sale in legacy_sales:
        sale_items = conn.execute('''
            SELECT si.product_id, COALESCE(si.item_name, p.name, 'Artículo') AS item_name,
                   si.quantity, si.price
            FROM sale_items si LEFT JOIN products p ON p.id=si.product_id
            WHERE si.sale_id=?
        ''', (sale['id'],)).fetchall()
        details = [{'product_id': row['product_id'], 'name': row['item_name'], 'quantity': row['quantity'],
                    'unit_price': row['price'], 'line_total': float(row['price'] or 0) * int(row['quantity'] or 0)}
                   for row in sale_items]
        conn.execute('''
            INSERT INTO sales_audit (sale_id, action, total, customer_id, customer_name, customer_email, points_earned, items, occurred_at)
            VALUES (?, 'VENTA REGISTRADA', ?, ?, ?, ?, ?, ?, ?)
        ''', (sale['id'], sale['total'], sale['customer_id'], sale['customer_name'], sale['customer_email'], sale['points_earned'] or 0,
              json.dumps(details, ensure_ascii=False), sale['date']))
    conn.commit()
    conn.close()

init_db()


@app.before_request
def require_admin_and_csrf():
    if request.endpoint in {'login', 'static', 'public_css'}:
        return
    if IS_VERCEL and not session.get('authenticated'):
        if request.path.startswith('/api/') or request.path == '/checkout':
            return jsonify({'success': False, 'error': 'Sesión requerida'}), 401
        return redirect(url_for('login'))
    if IS_VERCEL and request.method == 'POST' and request.endpoint != 'login':
        expected = session.get('csrf_token')
        supplied = request.headers.get('X-CSRFToken') or request.form.get('csrf_token')
        if not expected or not supplied or not hmac.compare_digest(expected, supplied):
            abort(400, description='Token de seguridad inválido. Recarga la página e inténtalo de nuevo.')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if not IS_VERCEL:
        return redirect(url_for('pos'))
    if request.method == 'POST':
        password = request.form.get('password', '')
        expected = os.environ.get('ADMIN_PASSWORD', '')
        if expected and hmac.compare_digest(password, expected):
            session.clear()
            session['authenticated'] = True
            session['csrf_token'] = os.urandom(32).hex()
            return redirect(url_for('pos'))
        flash('Contraseña incorrecta.', 'error')
    return render_template('login.html')


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/css/<path:filename>')
def public_css(filename):
    return send_from_directory(Path(__file__).parent / 'public' / 'css', filename)


@app.context_processor
def inject_security_token():
    token = session.get('csrf_token')
    return {'csrf_token': token or ''}

def _report_range(period, anchor):
    if period == 'daily':
        start = anchor
        end = start + timedelta(days=1)
    elif period == 'weekly':
        start = anchor - timedelta(days=anchor.weekday())
        end = start + timedelta(days=7)
    elif period == 'monthly':
        start = anchor.replace(day=1)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    else:
        raise ValueError('Periodo inválido')
    return start, end

def _build_sales_report(period, anchor):
    start, end = _report_range(period, anchor)
    if app.config['DATABASE_URL']:
        start_param = datetime.combine(start, time.min)
        end_param = datetime.combine(end, time.min)
    else:
        start_param = datetime.combine(start, time.min, tzinfo=BOGOTA).astimezone(timezone.utc).replace(tzinfo=None).isoformat(' ')
        end_param = datetime.combine(end, time.min, tzinfo=BOGOTA).astimezone(timezone.utc).replace(tzinfo=None).isoformat(' ')

    conn = get_db_connection()
    sales = conn.execute('SELECT id, date, total FROM sales WHERE date>=? AND date<? ORDER BY date',
                         (start_param, end_param)).fetchall()
    expenses = conn.execute('''SELECT id, date, description, category, amount
                               FROM expenses WHERE date>=? AND date<? ORDER BY date''',
                            (start_param, end_param)).fetchall()
    conn.close()

    daily = {}
    cursor = start
    while cursor < end:
        key = cursor.isoformat()
        daily[key] = {'date': key, 'sales': 0.0, 'tickets': 0,
                      'restock': 0.0, 'other_expenses': 0.0}
        cursor += timedelta(days=1)

    def date_key(value):
        if isinstance(value, date) and not isinstance(value, datetime):
            return value.isoformat()
        if not isinstance(value, datetime):
            value = datetime.fromisoformat(str(value))
        if value.tzinfo is None:
            value = value.replace(tzinfo=BOGOTA if app.config['DATABASE_URL'] else timezone.utc)
        return value.astimezone(BOGOTA).date().isoformat()

    for sale in sales:
        key = date_key(sale['date'])
        if key in daily:
            daily[key]['sales'] += float(sale['total'] or 0)
            daily[key]['tickets'] += 1
    for expense in expenses:
        key = date_key(expense['date'])
        if key in daily:
            amount = float(expense['amount'] or 0)
            column = 'restock' if expense['category'] == 'Mercancía / Restock' else 'other_expenses'
            daily[key][column] += amount

    rows = list(daily.values())
    for row in rows:
        row['expenses'] = row['restock'] + row['other_expenses']
        row['net'] = row['sales'] - row['expenses']
    total_sales = sum(row['sales'] for row in rows)
    total_restock = sum(row['restock'] for row in rows)
    total_other_expenses = sum(row['other_expenses'] for row in rows)
    total_expenses = total_restock + total_other_expenses
    return {
        'period': period,
        'start': start.isoformat(),
        'end': (end - timedelta(days=1)).isoformat(),
        'anchor': anchor.isoformat(),
        'days': rows,
        'total_sales': total_sales,
        'total_restock': total_restock,
        'total_other_expenses': total_other_expenses,
        'total_expenses': total_expenses,
        'net': total_sales - total_expenses,
        'tickets': sum(row['tickets'] for row in rows),
    }

def _report_request_values():
    period = request.args.get('period', 'daily')
    anchor_value = request.args.get('date') or datetime.now(BOGOTA).date().isoformat()
    if period not in {'daily', 'weekly', 'monthly'}:
        raise ValueError('Selecciona día, semana o mes.')
    try:
        anchor = date.fromisoformat(anchor_value)
    except ValueError as error:
        raise ValueError('La fecha del reporte no es válida.') from error
    return period, anchor

@app.route('/reports')
def reports():
    return render_template('reports.html', today=datetime.now(BOGOTA).date().isoformat())

@app.route('/api/reports/data')
def reports_data():
    try:
        period, anchor = _report_request_values()
        return jsonify(_build_sales_report(period, anchor))
    except ValueError as error:
        return jsonify({'error': str(error)}), 400

@app.route('/reports/export.csv')
def export_report_csv():
    try:
        period, anchor = _report_request_values()
        report = _build_sales_report(period, anchor)
    except ValueError as error:
        return jsonify({'error': str(error)}), 400
    output = io.StringIO(newline='')
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Fecha', 'Ventas COP', 'Cantidad de ventas', 'Restocks COP',
                     'Otros egresos COP', 'Egresos totales COP', 'Balance COP'])
    for row in report['days']:
        writer.writerow([row['date'], round(row['sales']), row['tickets'], round(row['restock']),
                         round(row['other_expenses']), round(row['expenses']), round(row['net'])])
    writer.writerow([])
    writer.writerow(['TOTAL DEL PERIODO', round(report['total_sales']), report['tickets'],
                     round(report['total_restock']), round(report['total_other_expenses']),
                     round(report['total_expenses']), round(report['net'])])
    filename = f"reporte-{period}-{report['start']}-a-{report['end']}.csv"
    return Response('\ufeff' + output.getvalue(), mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename={filename}'})

# ─── VISTAS PRINCIPALES ────────────────────────────────────────────────────

@app.route('/')
def pos():
    conn = get_db_connection()
    products = conn.execute('SELECT * FROM products WHERE stock > 0 ORDER BY name').fetchall()
    conn.close()
    return render_template('pos.html', products=products)

@app.route('/inventory')
def inventory():
    conn = get_db_connection()
    products = conn.execute('SELECT * FROM products ORDER BY name').fetchall()
    conn.close()
    return render_template('inventory.html', products=products)

@app.route('/finance')
def finance():
    conn = get_db_connection()
    sales_total    = conn.execute('SELECT COALESCE(SUM(total),0) as total, COUNT(id) as count FROM sales').fetchone()
    today_sales_sql = "SELECT COALESCE(SUM(total),0) as total FROM sales WHERE date(date)=date('now','localtime')"
    today_expenses_sql = "SELECT COALESCE(SUM(amount),0) as total FROM expenses WHERE date(date)=date('now','localtime')"
    if app.config['DATABASE_URL']:
        today_sales_sql = "SELECT COALESCE(SUM(total),0) as total FROM sales WHERE date(date)=(NOW() AT TIME ZONE 'America/Bogota')::date"
        today_expenses_sql = "SELECT COALESCE(SUM(amount),0) as total FROM expenses WHERE date(date)=(NOW() AT TIME ZONE 'America/Bogota')::date"
    sales_today    = conn.execute(today_sales_sql).fetchone()
    expenses_total = conn.execute('SELECT COALESCE(SUM(amount),0) as total, COUNT(id) as count FROM expenses').fetchone()
    expenses_today = conn.execute(today_expenses_sql).fetchone()
    recent_sales   = conn.execute('SELECT * FROM sales ORDER BY id DESC LIMIT 50').fetchall()
    recent_expenses = conn.execute('SELECT * FROM expenses ORDER BY id DESC LIMIT 50').fetchall()
    all_products   = conn.execute('SELECT id, name, cost_price, price, stock FROM products ORDER BY name').fetchall()
    conn.close()

    total_revenue = sales_total['total'] or 0
    total_spent   = expenses_total['total'] or 0

    return render_template('finance.html',
        total_revenue=total_revenue,
        total_spent=total_spent,
        net_profit=total_revenue - total_spent,
        today_sales=sales_today['total'] or 0,
        today_expenses=expenses_today['total'] or 0,
        sales_count=sales_total['count'] or 0,
        recent_sales=recent_sales,
        recent_expenses=recent_expenses,
        all_products=all_products)

@app.route('/sales_audit')
def sales_audit():
    conn = get_db_connection()
    audit_rows = conn.execute('SELECT * FROM sales_audit ORDER BY id DESC LIMIT 500').fetchall()
    conn.close()
    audit_entries = []
    for row in audit_rows:
        entry = dict(row)
        try:
            entry['items_list'] = json.loads(entry.get('items') or '[]')
        except (TypeError, ValueError):
            entry['items_list'] = []
        audit_entries.append(entry)
    return render_template('sales_audit.html', audit_entries=audit_entries)

@app.route('/customers')
def customers():
    conn = get_db_connection()
    customer_rows = conn.execute('''
        SELECT c.national_id, c.name, c.email, c.points, c.consent_at, c.created_at,
               COUNT(s.id) AS purchase_count, MAX(s.date) AS last_purchase
        FROM customers c LEFT JOIN sales s ON s.customer_id=c.national_id
        GROUP BY c.national_id, c.name, c.email, c.points, c.consent_at, c.created_at
        ORDER BY c.created_at DESC, c.national_id
    ''').fetchall()
    conn.close()
    customer_list = [dict(row) for row in customer_rows]
    return render_template('customers.html',
                           customer_list=customer_list,
                           customer_count=len(customer_list),
                           email_count=sum(bool(row['email']) for row in customer_list),
                           total_points=sum(int(row['points'] or 0) for row in customer_list))

@app.route('/customers/<national_id>/email', methods=['POST'])
def update_customer_email(national_id):
    email = request.form.get('email', '').strip().lower()
    if not national_id.isdigit() or not 6 <= len(national_id) <= 15:
        flash('La cédula no es válida.', 'error')
        return redirect(url_for('customers'))
    if email and (len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)):
        flash('Ingresa un correo válido.', 'error')
        return redirect(url_for('customers'))
    conn = get_db_connection()
    result = conn.execute('UPDATE customers SET email=? WHERE national_id=?',
                          (email or None, national_id))
    conn.commit()
    conn.close()
    flash('Correo actualizado.' if result.rowcount else 'No se encontró ese cliente.',
          'success' if result.rowcount else 'error')
    return redirect(url_for('customers'))

# ─── CRUD PRODUCTOS ────────────────────────────────────────────────────────

@app.route('/add_product', methods=['POST'])
def add_product():
    name       = request.form['name'].strip()
    barcode    = request.form.get('barcode', '').strip() or None
    price      = float(request.form.get('price', 0) or 0)
    cost_price = float(request.form.get('cost_price', 0) or 0)
    stock      = int(request.form.get('stock', 0) or 0)

    conn = get_db_connection()
    try:
        if app.config['DATABASE_URL']:
            product_id = conn.execute(
                'INSERT INTO products (name, barcode, cost_price, price, stock) VALUES (?,?,?,?,?) RETURNING id',
                (name, barcode, cost_price, price, stock)).fetchone()['id']
        else:
            cursor = conn.execute(
                'INSERT INTO products (name, barcode, cost_price, price, stock) VALUES (?,?,?,?,?)',
                (name, barcode, cost_price, price, stock))
            product_id = cursor.lastrowid
        if stock > 0 and cost_price > 0:
            conn.execute(
                "INSERT INTO expenses (description, category, amount, quantity, unit_price, product_id) VALUES (?,?,?,?,?,?)",
                (f'Compra inicial de inventario: {name}', 'Mercancía / Restock', stock * cost_price,
                 stock, cost_price, product_id))
        conn.commit()
        if stock > 0 and cost_price > 0:
            flash('Producto creado y compra inicial registrada en Finanzas.', 'success')
        elif stock > 0:
            flash('Producto creado. No se registró gasto inicial porque falta el precio de compra.', 'warning')
    except Exception as error:
        if not is_unique_violation(error):
            raise
        conn.rollback()
        flash('Ya existe un producto con ese código de barras.', 'error')
    finally:
        conn.close()
    return redirect(url_for('inventory'))

@app.route('/edit_product/<int:product_id>', methods=['POST'])
def edit_product(product_id):
    name       = request.form['name'].strip()
    barcode    = request.form.get('barcode', '').strip() or None
    price      = float(request.form.get('price', 0) or 0)
    cost_price = float(request.form.get('cost_price', 0) or 0)
    stock      = int(request.form.get('stock', 0) or 0)

    conn = get_db_connection()
    try:
        conn.execute(
            'UPDATE products SET name=?, barcode=?, cost_price=?, price=?, stock=? WHERE id=?',
            (name, barcode, cost_price, price, stock, product_id))
        conn.commit()
    except Exception as error:
        if not is_unique_violation(error):
            raise
        conn.rollback()
        flash('Ya existe un producto con ese código de barras.', 'error')
    finally:
        conn.close()
    return redirect(url_for('inventory'))

@app.route('/delete_product/<int:product_id>', methods=['POST'])
def delete_product(product_id):
    conn = get_db_connection()
    conn.execute('UPDATE sale_items SET product_id=NULL WHERE product_id=?', (product_id,))
    conn.execute('UPDATE expenses SET product_id=NULL WHERE product_id=?', (product_id,))
    conn.execute('DELETE FROM products WHERE id=?', (product_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('inventory'))

# ─── CRUD GASTOS ───────────────────────────────────────────────────────────

@app.route('/add_expense', methods=['POST'])
def add_expense():
    description = request.form['description'].strip()
    category    = request.form.get('category', 'General').strip()
    try:
        quantity   = int(request.form.get('quantity', 1) or 1)
        unit_price = float(request.form.get('unit_price', 0) or 0)
        if category == 'Mercancía / Restock':
            product_id = int(request.form.get('product_id', 0) or 0)
            if product_id <= 0 or quantity <= 0 or unit_price <= 0:
                flash('Selecciona un producto e ingresa cantidad y costo unitario válidos.', 'error')
                return redirect(url_for('finance'))
            amount = quantity * unit_price
        else:
            product_id = None
            amount = float(request.form.get('amount', 0) or 0)
    except (TypeError, ValueError):
        flash('Revisa los valores del egreso e inténtalo de nuevo.', 'error')
        return redirect(url_for('finance'))

    conn = get_db_connection()
    try:
        if product_id:
            product = conn.execute('SELECT stock, cost_price FROM products WHERE id=?', (product_id,)).fetchone()
            if not product:
                flash('El producto seleccionado ya no existe.', 'error')
                return redirect(url_for('finance'))
            new_stock = product['stock'] + quantity
            if product['cost_price'] and new_stock > 0:
                weighted_cost = ((product['stock'] * product['cost_price']) + (quantity * unit_price)) / new_stock
            else:
                weighted_cost = unit_price
            conn.execute('UPDATE products SET stock=?, cost_price=? WHERE id=?',
                         (new_stock, weighted_cost, product_id))
        conn.execute(
            'INSERT INTO expenses (description, category, amount, quantity, unit_price, product_id) VALUES (?,?,?,?,?,?)',
            (description, category, amount, quantity, unit_price, product_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return redirect(url_for('finance'))

@app.route('/edit_expense/<int:expense_id>', methods=['POST'])
def edit_expense(expense_id):
    description = request.form['description'].strip()
    category    = request.form.get('category', 'General').strip()
    amount      = float(request.form.get('amount', 0) or 0)
    quantity    = int(request.form.get('quantity', 1) or 1)
    unit_price  = float(request.form.get('unit_price', 0) or 0)

    conn = get_db_connection()
    try:
        current = conn.execute('SELECT * FROM expenses WHERE id=?', (expense_id,)).fetchone()
        if not current:
            flash('No se encontró el egreso.', 'error')
            return redirect(url_for('finance'))
        old_restock = current['category'] == 'Mercancía / Restock' and current['product_id']
        new_restock = category == 'Mercancía / Restock'
        old_product_id = current['product_id']
        new_product_id = int(request.form.get('product_id', 0) or 0) if new_restock else None
        if new_restock:
            quantity = int(request.form.get('quantity', 1) or 1)
            unit_price = float(request.form.get('unit_price', 0) or 0)
            if new_product_id <= 0 or quantity <= 0 or unit_price <= 0:
                flash('Para un restock, selecciona producto, cantidad y costo unitario válidos.', 'error')
                return redirect(url_for('finance'))
            amount = quantity * unit_price

        same_restock = old_restock and new_restock and old_product_id == new_product_id
        if old_restock and not same_restock:
            old_product = conn.execute('SELECT stock FROM products WHERE id=?', (old_product_id,)).fetchone()
            old_quantity = current['quantity'] or 1
            if not old_product or old_product['stock'] < old_quantity:
                flash('No se puede cambiar este restock: parte de ese inventario ya se vendió.', 'error')
                return redirect(url_for('finance'))
            conn.execute('UPDATE products SET stock=stock-? WHERE id=?', (old_quantity, old_product_id))
        if new_restock:
            new_product = conn.execute('SELECT stock, cost_price FROM products WHERE id=?', (new_product_id,)).fetchone()
            if not new_product:
                flash('El producto seleccionado ya no existe.', 'error')
                return redirect(url_for('finance'))
            if same_restock:
                old_quantity = current['quantity'] or 1
                other_stock = new_product['stock'] - old_quantity
                if other_stock < 0:
                    flash('No se puede ajustar este restock: parte de ese inventario ya se vendió.', 'error')
                    return redirect(url_for('finance'))
                stock_after = other_stock + quantity
                old_unit_cost = current['unit_price'] or 0
                other_value = max(0, new_product['stock'] * (new_product['cost_price'] or 0) - old_quantity * old_unit_cost)
                weighted_cost = (other_value + quantity * unit_price) / stock_after if stock_after else unit_price
            else:
                stock_after = new_product['stock'] + quantity
                weighted_cost = ((new_product['stock'] * (new_product['cost_price'] or 0)) + quantity * unit_price) / stock_after if stock_after else unit_price
            conn.execute('UPDATE products SET stock=?, cost_price=? WHERE id=?',
                         (stock_after, weighted_cost, new_product_id))
        conn.execute(
            'UPDATE expenses SET description=?, category=?, amount=?, quantity=?, unit_price=?, product_id=? WHERE id=?',
            (description, category, amount, quantity, unit_price, new_product_id, expense_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return redirect(url_for('finance'))

@app.route('/delete_expense/<int:expense_id>', methods=['POST'])
def delete_expense(expense_id):
    conn = get_db_connection()
    try:
        expense = conn.execute('SELECT category, quantity, product_id FROM expenses WHERE id=?', (expense_id,)).fetchone()
        if expense and expense['category'] == 'Mercancía / Restock' and expense['product_id']:
            qty = expense['quantity'] or 1
            product = conn.execute('SELECT stock FROM products WHERE id=?', (expense['product_id'],)).fetchone()
            if not product or product['stock'] < qty:
                flash('No se puede eliminar este restock: parte de ese inventario ya se vendió.', 'error')
                return redirect(url_for('finance'))
            conn.execute('UPDATE products SET stock=stock-? WHERE id=?', (qty, expense['product_id']))
        conn.execute('DELETE FROM expenses WHERE id=?', (expense_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return redirect(url_for('finance'))

# ─── CRUD VENTAS ───────────────────────────────────────────────────────────

@app.route('/delete_sale/<int:sale_id>', methods=['POST'])
def delete_sale(sale_id):
    conn = get_db_connection()
    try:
        sale = conn.execute('SELECT * FROM sales WHERE id=?', (sale_id,)).fetchone()
        if not sale:
            flash('No se encontró la venta.', 'error')
            return redirect(url_for('finance'))
        # Snapshot the void before deleting the operational sale record.
        items = conn.execute('''
            SELECT si.product_id, COALESCE(si.item_name, p.name, 'Artículo') AS item_name,
                   si.quantity, si.price
            FROM sale_items si LEFT JOIN products p ON p.id=si.product_id
            WHERE si.sale_id=?
        ''', (sale_id,)).fetchall()
        details = [{'product_id': item['product_id'], 'name': item['item_name'],
                    'quantity': item['quantity'], 'unit_price': item['price'],
                    'line_total': float(item['price'] or 0) * int(item['quantity'] or 0)} for item in items]
        conn.execute('''
            INSERT INTO sales_audit (sale_id, action, total, customer_id, customer_name, customer_email, points_earned, items)
            VALUES (?, 'VENTA ANULADA', ?, ?, ?, ?, ?, ?)
        ''', (sale_id, sale['total'], sale['customer_id'], sale['customer_name'], sale['customer_email'], sale['points_earned'] or 0,
              json.dumps(details, ensure_ascii=False)))
        for item in items:
            if item['product_id'] is not None:
                conn.execute('UPDATE products SET stock = stock + ? WHERE id=?',
                             (item['quantity'], item['product_id']))
        if sale['customer_id'] and (sale['points_earned'] or 0):
            conn.execute('''UPDATE customers SET points=CASE WHEN points>? THEN points-? ELSE 0 END
                            WHERE national_id=?''',
                         (sale['points_earned'], sale['points_earned'], sale['customer_id']))
        conn.execute('DELETE FROM sale_items WHERE sale_id=?', (sale_id,))
        conn.execute('DELETE FROM sales WHERE id=?', (sale_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return redirect(url_for('finance'))

# ─── API ───────────────────────────────────────────────────────────────────

@app.route('/api/product_by_barcode/<barcode>')
def get_product_by_barcode(barcode):
    conn = get_db_connection()
    product = conn.execute('SELECT * FROM products WHERE barcode=?', (barcode.strip(),)).fetchone()
    conn.close()
    if product:
        return jsonify({'success': True, 'product': {
            'id': product['id'], 'name': product['name'],
            'barcode': product['barcode'], 'price': product['price'], 'stock': product['stock']
        }})
    return jsonify({'success': False, 'error': 'Producto no encontrado'}), 404

@app.route('/api/customer/<national_id>')
def get_customer(national_id):
    if not national_id.isdigit() or not 6 <= len(national_id) <= 15:
        return jsonify({'success': False, 'error': 'Cédula inválida'}), 400
    conn = get_db_connection()
    customer = conn.execute('SELECT name, email, points, consent_at FROM customers WHERE national_id=?', (national_id,)).fetchone()
    conn.close()
    if not customer:
        return jsonify({'success': True, 'found': False})
    return jsonify({'success': True, 'found': True, 'name': customer['name'], 'email': customer['email'],
                    'points': customer['points'], 'has_consent': bool(customer['consent_at'])})

@app.route('/checkout', methods=['POST'])
def checkout():
    data = request.get_json(silent=True) or {}
    cart = data.get('cart', [])
    if not isinstance(cart, list) or not cart:
        return jsonify({'success': False, 'error': 'Carrito vacío o inválido'}), 400

    customer_id = str(data.get('customer_id') or '').strip()
    if customer_id and (not customer_id.isdigit() or not 6 <= len(customer_id) <= 15):
        return jsonify({'success': False, 'error': 'La cédula debe tener entre 6 y 15 dígitos'}), 400
    customer_name = str(data.get('customer_name') or '').strip()
    if len(customer_name) > 160:
        return jsonify({'success': False, 'error': 'El nombre del cliente debe tener máximo 160 caracteres'}), 400
    customer_email = str(data.get('customer_email') or '').strip().lower()
    if customer_email and (not customer_id or len(customer_email) > 254 or
                           not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", customer_email)):
        return jsonify({'success': False, 'error': 'Ingresa una cédula válida y un correo con formato correcto'}), 400
    customer_consent = data.get('customer_consent') is True

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        verified_items = []
        total = 0.0
        for item in cart:
            try:
                quantity = int(item.get('quantity', 0))
            except (TypeError, ValueError, AttributeError):
                return jsonify({'success': False, 'error': 'Producto o cantidad inválidos'}), 400
            if quantity <= 0:
                return jsonify({'success': False, 'error': 'Producto o cantidad inválidos'}), 400
            if isinstance(item, dict) and item.get('isService') is True:
                service_name = item.get('name')
                service_price = QUICK_SERVICES.get(service_name)
                if service_price is None:
                    return jsonify({'success': False, 'error': 'Servicio no válido'}), 400
                total += service_price * quantity
                verified_items.append((None, quantity, service_price, service_name))
                continue
            try:
                product_id = int(item.get('id', 0))
            except (TypeError, ValueError, AttributeError):
                return jsonify({'success': False, 'error': 'Producto inválido'}), 400
            if product_id <= 0:
                return jsonify({'success': False, 'error': 'Producto inválido'}), 400
            product = conn.execute('SELECT id, name, price, stock FROM products WHERE id=?', (product_id,)).fetchone()
            if not product:
                return jsonify({'success': False, 'error': 'Un producto ya no está disponible'}), 409
            if product['stock'] < quantity:
                return jsonify({'success': False, 'error': 'No hay existencias suficientes'}), 409
            total += float(product['price']) * quantity
            verified_items.append((product_id, quantity, float(product['price']), product['name']))

        points_earned = int(total // 1000) if customer_id else 0
        customer_points = None
        if customer_id:
            customer = conn.execute('SELECT name, points, email, consent_at FROM customers WHERE national_id=?',
                                     (customer_id,)).fetchone()
            if not customer_consent:
                return jsonify({'success': False,
                                'error': 'Para asociar cédula, correo y puntos necesitamos autorización del cliente. Si no desea registrarse, quita la cédula y continúa la venta.'}), 400
            if customer:
                customer_points = int(customer['points']) + points_earned
                customer_name = customer_name or customer['name'] or ''
                customer_email = customer_email or customer['email'] or ''
                conn.execute('''UPDATE customers SET points=?, name=COALESCE(NULLIF(?, ''), name), email=COALESCE(NULLIF(?, ''), email),
                                consent_at=COALESCE(consent_at, CURRENT_TIMESTAMP) WHERE national_id=?''',
                             (customer_points, customer_name, customer_email, customer_id))
            else:
                customer_points = points_earned
                conn.execute('''INSERT INTO customers (national_id, name, email, points, consent_at)
                                VALUES (?,?, ?,?,CURRENT_TIMESTAMP)''',
                             (customer_id, customer_name or None, customer_email or None, customer_points))

        if app.config['DATABASE_URL']:
            cur.execute('INSERT INTO sales (total, customer_id, customer_name, customer_email, points_earned) VALUES (?,?,?,?,?) RETURNING id',
                        (total, customer_id or None, customer_name or None, customer_email or None, points_earned))
            sale_id = cur.fetchone()['id']
        else:
            cur.execute('INSERT INTO sales (total, customer_id, customer_name, customer_email, points_earned) VALUES (?,?,?,?,?)',
                        (total, customer_id or None, customer_name or None, customer_email or None, points_earned))
            sale_id = cur.lastrowid

        for product_id, quantity, unit_price, item_name in verified_items:
            if product_id is not None:
                cur.execute(
                    'UPDATE products SET stock = stock - ? WHERE id=? AND stock >= ?',
                    (quantity, product_id, quantity))
                if cur.rowcount != 1:
                    raise ValueError('No hay existencias suficientes')
            cur.execute(
                'INSERT INTO sale_items (sale_id, product_id, quantity, price, item_name) VALUES (?,?,?,?,?)',
                (sale_id, product_id, quantity, unit_price, item_name))
        audit_items = [{'product_id': product_id, 'name': item_name, 'quantity': quantity,
                        'unit_price': unit_price, 'line_total': unit_price * quantity}
                       for product_id, quantity, unit_price, item_name in verified_items]
        conn.execute('''
            INSERT INTO sales_audit (sale_id, action, total, customer_id, customer_name, customer_email, points_earned, items)
            VALUES (?, 'VENTA REGISTRADA', ?, ?, ?, ?, ?, ?)
        ''', (sale_id, total, customer_id or None, customer_name or None, customer_email or None, points_earned,
              json.dumps(audit_items, ensure_ascii=False)))
        conn.commit()
        return jsonify({'success': True, 'sale_id': sale_id, 'total': total,
                        'points_earned': points_earned, 'customer_points': customer_points,
                        'customer_name': customer_name or None, 'customer_email': customer_email or None})
    except Exception:
        conn.rollback()
        app.logger.exception('No se pudo completar la venta')
        return jsonify({'success': False, 'error': 'No se pudo completar la venta'}), 500
    finally:
        conn.close()

if __name__ == '__main__':
    app.run(debug=os.environ.get('FLASK_DEBUG') == '1', port=5000)

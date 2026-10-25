from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session, abort, send_from_directory
from pathlib import Path
import hmac
import os
import sqlite3

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-only-change-me')
app.config['DATABASE_URL'] = (os.environ.get('DATABASE_URL') or os.environ.get('POSTGRES_URL') or '').strip()
IS_VERCEL = os.environ.get('VERCEL') == '1'
DB_NAME = Path(__file__).with_name('papeleria.db')
QUICK_SERVICES = {
    'Fotocopia B/N': 1.50,
    'Fotocopia Color': 5.00,
    'Impresión Documento': 3.00,
    'Hojas Blancas (5 un)': 2.00,
    'Escaneo / Trámite': 10.00,
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
            '''CREATE TABLE IF NOT EXISTS products (
                id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, barcode TEXT UNIQUE,
                cost_price DOUBLE PRECISION DEFAULT 0, price DOUBLE PRECISION NOT NULL,
                stock INTEGER NOT NULL DEFAULT 0)''',
            '''CREATE TABLE IF NOT EXISTS sales (
                id BIGSERIAL PRIMARY KEY, total DOUBLE PRECISION NOT NULL,
                date TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'America/Bogota'))''',
            '''CREATE TABLE IF NOT EXISTS sale_items (
                id BIGSERIAL PRIMARY KEY, sale_id BIGINT REFERENCES sales(id),
                product_id BIGINT REFERENCES products(id), quantity INTEGER,
                price DOUBLE PRECISION, item_name TEXT)''',
            '''CREATE TABLE IF NOT EXISTS expenses (
                id BIGSERIAL PRIMARY KEY, description TEXT NOT NULL, category TEXT NOT NULL,
                amount DOUBLE PRECISION NOT NULL, quantity INTEGER DEFAULT 1,
                unit_price DOUBLE PRECISION DEFAULT 0,
                date TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'America/Bogota'))''',
        ]
        for statement in statements:
            conn.execute(statement)
        conn.execute('ALTER TABLE products ADD COLUMN IF NOT EXISTS barcode TEXT')
        conn.execute('ALTER TABLE products ADD COLUMN IF NOT EXISTS cost_price DOUBLE PRECISION DEFAULT 0')
        conn.execute('ALTER TABLE expenses ADD COLUMN IF NOT EXISTS quantity INTEGER DEFAULT 1')
        conn.execute('ALTER TABLE expenses ADD COLUMN IF NOT EXISTS unit_price DOUBLE PRECISION DEFAULT 0')
        conn.execute('ALTER TABLE sale_items ADD COLUMN IF NOT EXISTS item_name TEXT')
    else:
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
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            description TEXT NOT NULL,
            category TEXT NOT NULL,
            amount REAL NOT NULL,
            quantity INTEGER DEFAULT 1,
            unit_price REAL DEFAULT 0,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        for migration in [
            'ALTER TABLE products ADD COLUMN barcode TEXT',
            'ALTER TABLE products ADD COLUMN cost_price REAL DEFAULT 0',
            'ALTER TABLE expenses ADD COLUMN quantity INTEGER DEFAULT 1',
            'ALTER TABLE expenses ADD COLUMN unit_price REAL DEFAULT 0',
            'ALTER TABLE sale_items ADD COLUMN item_name TEXT',
        ]:
            try:
                conn.execute(migration)
            except sqlite3.OperationalError:
                pass
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
        conn.execute(
            'INSERT INTO products (name, barcode, cost_price, price, stock) VALUES (?,?,?,?,?)',
            (name, barcode, cost_price, price, stock))
        conn.commit()
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
    conn.execute('DELETE FROM products WHERE id=?', (product_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('inventory'))

# ─── CRUD GASTOS ───────────────────────────────────────────────────────────

@app.route('/add_expense', methods=['POST'])
def add_expense():
    description = request.form['description'].strip()
    category    = request.form.get('category', 'General').strip()
    amount      = float(request.form.get('amount', 0) or 0)
    quantity    = int(request.form.get('quantity', 1) or 1)
    unit_price  = float(request.form.get('unit_price', 0) or 0)

    conn = get_db_connection()
    conn.execute(
        'INSERT INTO expenses (description, category, amount, quantity, unit_price) VALUES (?,?,?,?,?)',
        (description, category, amount, quantity, unit_price))
    conn.commit()
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
    conn.execute(
        'UPDATE expenses SET description=?, category=?, amount=?, quantity=?, unit_price=? WHERE id=?',
        (description, category, amount, quantity, unit_price, expense_id))
    conn.commit()
    conn.close()
    return redirect(url_for('finance'))

@app.route('/delete_expense/<int:expense_id>', methods=['POST'])
def delete_expense(expense_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM expenses WHERE id=?', (expense_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('finance'))

# ─── CRUD VENTAS ───────────────────────────────────────────────────────────

@app.route('/delete_sale/<int:sale_id>', methods=['POST'])
def delete_sale(sale_id):
    conn = get_db_connection()
    # Revertir stock
    items = conn.execute('SELECT product_id, quantity FROM sale_items WHERE sale_id=? AND product_id IS NOT NULL', (sale_id,)).fetchall()
    for item in items:
        conn.execute('UPDATE products SET stock = stock + ? WHERE id=?', (item['quantity'], item['product_id']))
    conn.execute('DELETE FROM sale_items WHERE sale_id=?', (sale_id,))
    conn.execute('DELETE FROM sales WHERE id=?', (sale_id,))
    conn.commit()
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

@app.route('/checkout', methods=['POST'])
def checkout():
    data = request.get_json(silent=True) or {}
    cart = data.get('cart', [])
    if not isinstance(cart, list) or not cart:
        return jsonify({'success': False, 'error': 'Carrito vacío o inválido'}), 400

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

        if app.config['DATABASE_URL']:
            cur.execute('INSERT INTO sales (total) VALUES (?) RETURNING id', (total,))
            sale_id = cur.fetchone()['id']
        else:
            cur.execute('INSERT INTO sales (total) VALUES (?)', (total,))
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
        conn.commit()
        return jsonify({'success': True, 'sale_id': sale_id, 'total': total})
    except Exception:
        conn.rollback()
        app.logger.exception('No se pudo completar la venta')
        return jsonify({'success': False, 'error': 'No se pudo completar la venta'}), 500
    finally:
        conn.close()

if __name__ == '__main__':
    app.run(debug=os.environ.get('FLASK_DEBUG') == '1', port=5000)

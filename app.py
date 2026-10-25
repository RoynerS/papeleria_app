from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
import sqlite3
import os

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-only-change-me')
DB_NAME = 'papeleria.db'

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            barcode TEXT UNIQUE,
            cost_price REAL DEFAULT 0,
            price REAL NOT NULL,
            stock INTEGER NOT NULL DEFAULT 0
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            total REAL NOT NULL,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS sale_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_id INTEGER,
            product_id INTEGER,
            quantity INTEGER,
            price REAL,
            FOREIGN KEY(sale_id) REFERENCES sales(id),
            FOREIGN KEY(product_id) REFERENCES products(id)
        )
    ''')
    c.execute('''
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

    # Migraciones seguras
    for migration in [
        'ALTER TABLE products ADD COLUMN barcode TEXT',
        'ALTER TABLE products ADD COLUMN cost_price REAL DEFAULT 0',
        'ALTER TABLE expenses ADD COLUMN quantity INTEGER DEFAULT 1',
        'ALTER TABLE expenses ADD COLUMN unit_price REAL DEFAULT 0',
    ]:
        try:
            c.execute(migration)
        except sqlite3.OperationalError:
            pass

    conn.commit()
    conn.close()

init_db()

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

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
    sales_today    = conn.execute("SELECT COALESCE(SUM(total),0) as total FROM sales WHERE date(date)=date('now','localtime')").fetchone()
    expenses_total = conn.execute('SELECT COALESCE(SUM(amount),0) as total, COUNT(id) as count FROM expenses').fetchone()
    expenses_today = conn.execute("SELECT COALESCE(SUM(amount),0) as total FROM expenses WHERE date(date)=date('now','localtime')").fetchone()
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
    except sqlite3.IntegrityError:
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
    except sqlite3.IntegrityError:
        flash('Ya existe un producto con ese código de barras.', 'error')
    finally:
        conn.close()
    return redirect(url_for('inventory'))

@app.route('/delete_product/<int:product_id>', methods=['POST'])
def delete_product(product_id):
    conn = get_db_connection()
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
    data = request.json
    cart = data.get('cart', [])
    if not cart:
        return jsonify({'success': False, 'error': 'Carrito vacío'})

    total = sum(item['price'] * item['quantity'] for item in cart)
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute('INSERT INTO sales (total) VALUES (?)', (total,))
        sale_id = cur.lastrowid
        for item in cart:
            if item.get('id') and item['id'] > 0:
                cur.execute('INSERT INTO sale_items (sale_id, product_id, quantity, price) VALUES (?,?,?,?)',
                            (sale_id, item['id'], item['quantity'], item['price']))
                cur.execute('UPDATE products SET stock = stock - ? WHERE id=?', (item['quantity'], item['id']))
            else:
                cur.execute('INSERT INTO sale_items (sale_id, product_id, quantity, price) VALUES (?,NULL,?,?)',
                            (sale_id, item['quantity'], item['price']))
        conn.commit()
        return jsonify({'success': True, 'sale_id': sale_id})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)})
    finally:
        conn.close()

if __name__ == '__main__':
    app.run(debug=True, port=5000)

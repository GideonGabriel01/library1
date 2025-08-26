# database.py
import sqlite3
from datetime import datetime, timedelta
import bcrypt

DB_NAME = "library.db"

def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d

def get_connection():
    conn = sqlite3.connect(DB_NAME, detect_types=sqlite3.PARSE_DECLTYPES|sqlite3.PARSE_COLNAMES)
    conn.row_factory = dict_factory
    return conn

# ---------------- DB init ----------------
def init_db():
    conn = get_connection()
    c = conn.cursor()

    c.execute('''
    CREATE TABLE IF NOT EXISTS books (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        author TEXT,
        category TEXT,
        isbn TEXT,
        available INTEGER DEFAULT 1
    )
    ''')

    c.execute('''
    CREATE TABLE IF NOT EXISTS members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT
    )
    ''')

    c.execute('''
    CREATE TABLE IF NOT EXISTS loans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id INTEGER NOT NULL,
        member_id INTEGER NOT NULL,
        date_borrowed TEXT NOT NULL,
        date_due TEXT NOT NULL,
        date_returned TEXT,
        late_fee REAL DEFAULT 0,
        FOREIGN KEY(book_id) REFERENCES books(id),
        FOREIGN KEY(member_id) REFERENCES members(id)
    )
    ''')

    c.execute('''
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    ''')
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", ("late_fee_per_day", "0.50"))
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", ("smtp_host", ""))
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", ("smtp_port", "587"))
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", ("smtp_user", ""))
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", ("smtp_password", ""))

    # users: add a role field (admin / staff)
    c.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash BLOB NOT NULL,
        role TEXT DEFAULT 'staff'
    )
    ''')

    # audit log
    c.execute('''
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        actor TEXT,
        action TEXT,
        details TEXT,
        created_at TEXT
    )
    ''')

    conn.commit()
    conn.close()

    # ensure default admin
    create_default_admin_if_missing()

# ---------------- users (auth) ----------------
def create_user(username, password_plain, role="admin"):
    password_bytes = password_plain.encode("utf-8")
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password_bytes, salt)
    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
              (username, hashed, role))
    conn.commit()
    conn.close()
    log_audit(username, "create_user", f"created user '{username}' with role '{role}'")

def verify_user(username, password_plain):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username=?", (username,))
    row = c.fetchone()
    conn.close()
    if not row:
        return False
    hashed = row["password_hash"]
    try:
        return bcrypt.checkpw(password_plain.encode("utf-8"), hashed)
    except Exception:
        return False

def any_user_exists():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) AS cnt FROM users")
    r = c.fetchone()
    conn.close()
    return r["cnt"] > 0

def create_default_admin_if_missing():
    if not any_user_exists():
        create_user("admin", "admin", role="admin")
        log_audit("system", "create_default_admin", "created default admin user 'admin'")

def change_user_password(username, old_password_plain, new_password_plain):
    if not verify_user(username, old_password_plain):
        return {"success": False, "message": "Current password incorrect."}
    password_bytes = new_password_plain.encode("utf-8")
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password_bytes, salt)
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE users SET password_hash=? WHERE username=?", (hashed, username))
    conn.commit()
    conn.close()
    log_audit(username, "change_password", f"user '{username}' changed their password")
    return {"success": True, "message": "Password changed."}

def admin_reset_password(admin_username, target_username, new_password_plain):
    # admin resets another user's password
    password_bytes = new_password_plain.encode("utf-8")
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password_bytes, salt)
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE users SET password_hash=? WHERE username=?", (hashed, target_username))
    conn.commit()
    conn.close()
    log_audit(admin_username, "admin_reset_password", f"admin '{admin_username}' reset password for '{target_username}'")
    return {"success": True, "message": f"Password for {target_username} reset."}

def get_user_role(username):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT role FROM users WHERE username=?", (username,))
    r = c.fetchone()
    conn.close()
    return r["role"] if r else None

def list_users():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id, username, role FROM users ORDER BY username")
    rows = c.fetchall()
    conn.close()
    return rows

# ---------------- books / members / loans ----------------
def add_book(title, author, category=None, isbn=None, actor=None):
    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT INTO books (title, author, category, isbn, available) VALUES (?, ?, ?, ?, 1)",
              (title, author, category, isbn))
    conn.commit()
    conn.close()
    log_audit(actor or "unknown", "add_book", title)

def update_book(book_id, title, author, category, isbn, available=True, actor=None):
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE books SET title=?, author=?, category=?, isbn=?, available=? WHERE id=?",
              (title, author, category, isbn, 1 if available else 0, book_id))
    conn.commit()
    conn.close()
    log_audit(actor or "unknown", "update_book", f"{book_id} -> {title}")

def delete_book(book_id, actor=None):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) AS cnt FROM loans WHERE book_id=? AND date_returned IS NULL", (book_id,))
    row = c.fetchone()
    if row and row["cnt"] > 0:
        conn.close()
        return {"success": False, "message": "Cannot delete: book has active (not returned) loans."}
    # get title for audit
    c.execute("SELECT title FROM books WHERE id=?", (book_id,))
    b = c.fetchone()
    title = b["title"] if b else f"id:{book_id}"
    c.execute("DELETE FROM books WHERE id=?", (book_id,))
    conn.commit()
    conn.close()
    log_audit(actor or "unknown", "delete_book", title)
    return {"success": True, "message": "Book deleted."}

def get_book(book_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM books WHERE id=?", (book_id,))
    book = c.fetchone()
    conn.close()
    return book

def list_books(search=None):
    conn = get_connection()
    c = conn.cursor()
    if search:
        q = f"%{search}%"
        c.execute("SELECT * FROM books WHERE title LIKE ? OR author LIKE ? OR category LIKE ? OR isbn LIKE ? ORDER BY title",
                  (q, q, q, q))
    else:
        c.execute("SELECT * FROM books ORDER BY title")
    rows = c.fetchall()
    conn.close()
    return rows

def add_member(name, email=None, actor=None):
    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT INTO members (name, email) VALUES (?, ?)", (name, email))
    conn.commit()
    conn.close()
    log_audit(actor or "unknown", "add_member", name)

def list_members():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM members ORDER BY name")
    rows = c.fetchall()
    conn.close()
    return rows

def get_member(member_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM members WHERE id=?", (member_id,))
    m = c.fetchone()
    conn.close()
    return m

def search_members_by_text(text):
    conn = get_connection()
    c = conn.cursor()
    q = f"%{text}%"
    c.execute("SELECT * FROM members WHERE name LIKE ? OR email LIKE ? ORDER BY name LIMIT 200", (q, q))
    rows = c.fetchall()
    conn.close()
    return rows

# ---------------- loans ----------------
def borrow_book(book_id, member_id, days_due=14, actor=None):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT available FROM books WHERE id=?", (book_id,))
    b = c.fetchone()
    if not b:
        conn.close()
        return {"success": False, "message": "Book not found."}
    if b["available"] == 0:
        conn.close()
        return {"success": False, "message": "Book currently not available."}
    now = datetime.now()
    due = now + timedelta(days=days_due)
    c.execute("INSERT INTO loans (book_id, member_id, date_borrowed, date_due) VALUES (?, ?, ?, ?)",
              (book_id, member_id, now.isoformat(), due.isoformat()))
    c.execute("UPDATE books SET available=0 WHERE id=?", (book_id,))
    conn.commit()
    conn.close()
    log_audit(actor or "unknown", "borrow_book", f"book:{book_id} member:{member_id} due:{due.date()}")
    return {"success": True, "message": f"Borrowed successfully; due on {due.date()}"}

def return_book(loan_id, actor=None):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM loans WHERE id=?", (loan_id,))
    loan = c.fetchone()
    if not loan:
        conn.close()
        return {"success": False, "message": "Loan not found."}
    if loan["date_returned"]:
        conn.close()
        return {"success": False, "message": "Already returned."}
    late_fee_setting = get_setting("late_fee_per_day")
    try:
        late_fee_per_day = float(late_fee_setting)
    except Exception:
        late_fee_per_day = 0.50
    date_returned = datetime.now()
    date_due = datetime.fromisoformat(loan["date_due"])
    late_days = (date_returned.date() - date_due.date()).days
    late_fee = 0.0
    if late_days > 0:
        late_fee = late_days * late_fee_per_day
    c.execute("UPDATE loans SET date_returned=?, late_fee=? WHERE id=?",
              (date_returned.isoformat(), late_fee, loan_id))
    c.execute("UPDATE books SET available=1 WHERE id=?", (loan["book_id"],))
    conn.commit()
    conn.close()
    log_audit(actor or "unknown", "return_book", f"loan:{loan_id} late_fee:{late_fee}")
    return {"success": True, "message": f"Returned. Late fee: {late_fee:.2f}", "late_fee": late_fee}

def list_loans(show_all=True):
    conn = get_connection()
    c = conn.cursor()
    query = """
    SELECT loans.id as loan_id, loans.book_id, loans.member_id,
           loans.date_borrowed, loans.date_due, loans.date_returned, loans.late_fee,
           books.title as book_title, members.name as member_name
    FROM loans
    JOIN books ON loans.book_id = books.id
    JOIN members ON loans.member_id = members.id
    """
    if not show_all:
        query += " WHERE loans.date_returned IS NULL"
    query += " ORDER BY loans.date_borrowed DESC"
    c.execute(query)
    rows = c.fetchall()
    conn.close()
    return rows

def get_all_loans_for_export():
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
    SELECT loans.id as loan_id, books.title as book_title, members.name as member_name,
           loans.date_borrowed, loans.date_due, loans.date_returned, loans.late_fee
    FROM loans
    JOIN books ON loans.book_id = books.id
    JOIN members ON loans.member_id = members.id
    ORDER BY loans.date_borrowed DESC
    """)
    rows = c.fetchall()
    conn.close()
    return rows

# ---------------- settings ----------------
def get_setting(key):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = c.fetchone()
    conn.close()
    return row["value"] if row else None

def set_setting(key, value, actor=None):
    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()
    log_audit(actor or "unknown", "set_setting", f"{key}={value}")

# ---------------- audit ----------------
def log_audit(actor, action, details):
    conn = get_connection()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("INSERT INTO audit_log (actor, action, details, created_at) VALUES (?, ?, ?, ?)",
              (actor, action, details, now))
    conn.commit()
    conn.close()

def query_audit(limit=200, since=None):
    conn = get_connection()
    c = conn.cursor()
    if since:
        c.execute("SELECT * FROM audit_log WHERE created_at>=? ORDER BY created_at DESC LIMIT ?", (since.isoformat(), limit))
    else:
        c.execute("SELECT * FROM audit_log ORDER BY created_at DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    conn.close()
    return rows

# ---------------- analytics helpers ----------------
def analytics_totals():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) AS cnt FROM books")
    books = c.fetchone()["cnt"]
    c.execute("SELECT COUNT(*) AS cnt FROM members")
    members = c.fetchone()["cnt"]
    c.execute("SELECT COUNT(*) AS cnt FROM loans WHERE date_returned IS NULL")
    active_loans = c.fetchone()["cnt"]
    conn.close()
    return {"books": books, "members": members, "active_loans": active_loans}

def analytics_loans_by_month(months=6):
    # returns list of tuples (YYYY-MM, count) for the last N months
    from datetime import date
    conn = get_connection()
    c = conn.cursor()
    today = datetime.now().date()
    results = []
    for i in range(months-1, -1, -1):
        # compute year-month for i months ago
        y = today.year
        m = today.month - i
        while m <= 0:
            y -= 1
            m += 12
        start = datetime(y, m, 1)
        if m == 12:
            end = datetime(y+1, 1, 1)
        else:
            end = datetime(y, m+1, 1)
        c.execute("SELECT COUNT(*) AS cnt FROM loans WHERE date_borrowed >= ? AND date_borrowed < ?", (start.isoformat(), end.isoformat()))
        cnt = c.fetchone()["cnt"]
        results.append((f"{y}-{m:02d}", cnt))
    conn.close()
    return results

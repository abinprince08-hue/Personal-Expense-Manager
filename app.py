"""Personal Finance REST API adapted from the Budget Management System shown in the user's PDF.

Original concepts retained: income records (source, amount, date), expense records
(category, amount, date), summary, highest records, savings goal, monthly report.
The PDF contains screenshots rather than the original .py source, so this is a
careful API adaptation of the visible design, not a byte-for-byte conversion.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import os
from functools import wraps

import pymysql
from flask import Flask, jsonify, request, g
from werkzeug.security import generate_password_hash, check_password_hash
import jwt
from dotenv import load_dotenv
from flasgger import Swagger

load_dotenv()
app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False
Swagger(app, template={
    "info": {"title": "Personal Finance API", "version": "1.0.0",
             "description": "REST API adapted from the Budget Management System project."},
    "securityDefinitions": {"Bearer": {"type": "apiKey", "name": "Authorization", "in": "header",
                                         "description": "Enter: Bearer <JWT token>"}}
})

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
DB_HOST = os.getenv("MYSQL_HOST", "localhost")
DB_PORT = int(os.getenv("MYSQL_PORT", "3306"))
DB_USER = os.getenv("MYSQL_USER", "root")
DB_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
DB_NAME = os.getenv("MYSQL_DATABASE", "budgetdb")


def db_connection():
    """Open a short-lived MySQL connection; callers close it in finally blocks."""
    return pymysql.connect(host=DB_HOST, port=DB_PORT, user=DB_USER,
                           password=DB_PASSWORD, database=DB_NAME,
                           cursorclass=pymysql.cursors.DictCursor,
                           autocommit=False, charset="utf8mb4")


def response_error(message, status):
    return jsonify({"error": message}), status


def token_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return response_error("Missing Bearer token", 401)
        token = header.split(" ", 1)[1].strip()
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
            g.user_id = int(payload["user_id"])
        except (jwt.InvalidTokenError, KeyError, TypeError, ValueError):
            return response_error("Invalid or expired token", 401)
        return fn(*args, **kwargs)
    return wrapped


def parse_record(data, kind):
    """Validate JSON fields shared by income and expense records."""
    if not isinstance(data, dict):
        raise ValueError("Request body must be a JSON object")
    category_key = "source" if kind == "income" else "category"
    category = data.get(category_key)
    amount = data.get("amount")
    raw_date = data.get("date")
    if not isinstance(category, str) or not category.strip() or len(category.strip()) > 50:
        raise ValueError(f"'{category_key}' must be a non-empty string of at most 50 characters")
    try:
        amount = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("'amount' must be a valid positive number")
    if not amount.is_finite() or amount <= 0 or amount > Decimal("999999999.99"):
        raise ValueError("'amount' must be greater than zero and within the supported limit")
    try:
        parsed_date = datetime.strptime(str(raw_date), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise ValueError("'date' must use YYYY-MM-DD format")
    return category.strip(), amount, parsed_date


@app.get("/")
def home():
    """Health check.
    ---
    tags: [General]
    responses:
      200:
        description: API is running
    """
    return jsonify({"message": "Personal Finance API is running!", "status": "success"})


@app.get("/api/health/db")
def database_health():
    """Check database connectivity.
    ---
    tags: [General]
    responses:
      200: {description: Database connection successful}
      503: {description: Database unavailable}
    """
    conn = None
    try:
        conn = db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            cur.fetchone()
        return jsonify({"database": "connected"})
    except Exception:
        app.logger.exception("Database health check failed")
        return response_error("Database unavailable; check server configuration", 503)
    finally:
        if conn:
            conn.close()


@app.post("/api/auth/register")
def register():
    """Create a user account.
    ---
    tags: [Authentication]
    consumes: [application/json]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [email, password]
          properties:
            email: {type: string, example: student@example.com}
            password: {type: string, example: a-strong-password}
    responses:
      201: {description: Account created}
      400: {description: Invalid input}
      409: {description: Email already registered}
    """
    data = request.get_json(silent=True) or {}
    email = data.get("email")
    password = data.get("password")
    if not isinstance(email, str) or len(email.strip()) > 254 or "@" not in email or not isinstance(password, str) or len(password) < 8 or len(password) > 200:
        return response_error("Provide a valid email and a password of at least 8 characters", 400)
    if not SECRET_KEY or SECRET_KEY == "change-me-to-a-long-random-secret":
        return response_error("Server JWT_SECRET_KEY is not configured securely", 500)
    conn = None
    try:
        conn = db_connection()
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (email, password_hash) VALUES (%s, %s)",
                        (email.strip().lower(), generate_password_hash(password)))
            user_id = cur.lastrowid
        conn.commit()
        return jsonify({"message": "Account created successfully", "user_id": user_id}), 201
    except pymysql.err.IntegrityError:
        if conn: conn.rollback()
        return response_error("Email is already registered", 409)
    except Exception:
        if conn: conn.rollback()
        app.logger.exception("Registration failed")
        return response_error("Could not create account", 500)
    finally:
        if conn: conn.close()


@app.post("/api/auth/login")
def login():
    """Log in and receive a JWT.
    ---
    tags: [Authentication]
    consumes: [application/json]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [email, password]
          properties:
            email: {type: string, example: student@example.com}
            password: {type: string, example: a-strong-password}
    responses:
      200: {description: JWT issued}
      401: {description: Invalid credentials}
    """
    data = request.get_json(silent=True) or {}
    email, password = data.get("email"), data.get("password")
    if not isinstance(email, str) or not isinstance(password, str):
        return response_error("Email and password are required", 400)
    conn = None
    try:
        conn = db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT id, password_hash FROM users WHERE email = %s", (email.strip().lower(),))
            user = cur.fetchone()
        if not user or not check_password_hash(user["password_hash"], password):
            return response_error("Invalid email or password", 401)
        if not SECRET_KEY:
            return response_error("Server JWT_SECRET_KEY is not configured", 500)
        token = jwt.encode({"user_id": user["id"], "exp": datetime.now(timezone.utc) + timedelta(hours=12)}, SECRET_KEY, algorithm="HS256")
        return jsonify({"access_token": token, "token_type": "Bearer", "expires_in": 43200})
    except Exception:
        app.logger.exception("Login failed")
        return response_error("Could not log in", 500)
    finally:
        if conn: conn.close()


def list_records(kind):
    table = "income" if kind == "income" else "expenses"
    category_col = "source" if kind == "income" else "category"
    conn = db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT id, {category_col} AS category, amount, date FROM {table} WHERE user_id = %s ORDER BY date DESC, id DESC", (g.user_id,))
            rows = cur.fetchall()
        for row in rows:
            row["amount"] = float(row["amount"])
            row["date"] = row["date"].isoformat() if row["date"] else None
        return jsonify({"items": rows, "count": len(rows)})
    finally:
        conn.close()


def create_record(kind):
    try:
        category, amount, record_date = parse_record(request.get_json(silent=True), kind)
    except ValueError as exc:
        return response_error(str(exc), 400)
    table = "income" if kind == "income" else "expenses"
    category_col = "source" if kind == "income" else "category"
    conn = db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"INSERT INTO {table} (user_id, {category_col}, amount, date) VALUES (%s, %s, %s, %s)",
                        (g.user_id, category, str(amount), record_date))
            record_id = cur.lastrowid
        conn.commit()
        return jsonify({"message": f"{kind.title()} record created", "id": record_id,
                        category_col: category, "amount": float(amount), "date": record_date.isoformat()}), 201
    except Exception:
        conn.rollback()
        app.logger.exception("Record creation failed")
        return response_error("Could not create record", 500)
    finally:
        conn.close()


def update_record(kind, record_id):
    try:
        category, amount, record_date = parse_record(request.get_json(silent=True), kind)
    except ValueError as exc:
        return response_error(str(exc), 400)
    table = "income" if kind == "income" else "expenses"
    category_col = "source" if kind == "income" else "category"
    conn = db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE {table} SET {category_col}=%s, amount=%s, date=%s WHERE id=%s AND user_id=%s",
                        (category, str(amount), record_date, record_id, g.user_id))
            changed = cur.rowcount
        conn.commit()
        if not changed:
            return response_error(f"{kind.title()} record not found", 404)
        return jsonify({"message": f"{kind.title()} record updated"})
    except Exception:
        conn.rollback()
        app.logger.exception("Record update failed")
        return response_error("Could not update record", 500)
    finally:
        conn.close()


def delete_record(kind, record_id):
    table = "income" if kind == "income" else "expenses"
    conn = db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {table} WHERE id=%s AND user_id=%s", (record_id, g.user_id))
            deleted = cur.rowcount
        conn.commit()
        if not deleted:
            return response_error(f"{kind.title()} record not found", 404)
        return jsonify({"message": f"{kind.title()} record deleted"})
    except Exception:
        conn.rollback()
        app.logger.exception("Record deletion failed")
        return response_error("Could not delete record", 500)
    finally:
        conn.close()


@app.get("/api/income")
@token_required
def get_income():
    """List your income records. --- tags: [Income] security: - Bearer: []"""
    return list_records("income")

@app.post("/api/income")
@token_required
def post_income():
    """Create an income record. --- tags: [Income] security: - Bearer: []"""
    return create_record("income")

@app.put("/api/income/<int:record_id>")
@token_required
def put_income(record_id):
    """Update an income record. --- tags: [Income] security: - Bearer: []"""
    return update_record("income", record_id)

@app.delete("/api/income/<int:record_id>")
@token_required
def del_income(record_id):
    """Delete an income record. --- tags: [Income] security: - Bearer: []"""
    return delete_record("income", record_id)

@app.get("/api/expenses")
@token_required
def get_expenses():
    """List your expense records. --- tags: [Expenses] security: - Bearer: []"""
    return list_records("expenses")

@app.post("/api/expenses")
@token_required
def post_expense():
    """Create an expense record. --- tags: [Expenses] security: - Bearer: []"""
    return create_record("expenses")

@app.put("/api/expenses/<int:record_id>")
@token_required
def put_expense(record_id):
    """Update an expense record. --- tags: [Expenses] security: - Bearer: []"""
    return update_record("expenses", record_id)

@app.delete("/api/expenses/<int:record_id>")
@token_required
def del_expense(record_id):
    """Delete an expense record. --- tags: [Expenses] security: - Bearer: []"""
    return delete_record("expenses", record_id)


@app.get("/api/reports/summary")
@token_required
def summary():
    """Get income, expenses, balance and expense-to-income ratio. --- tags: [Reports] security: - Bearer: []"""
    conn = db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(SUM(amount), 0) AS total FROM income WHERE user_id=%s", (g.user_id,))
            income_total = Decimal(str(cur.fetchone()["total"]))
            cur.execute("SELECT COALESCE(SUM(amount), 0) AS total FROM expenses WHERE user_id=%s", (g.user_id,))
            expense_total = Decimal(str(cur.fetchone()["total"]))
        balance = income_total - expense_total
        ratio = float(expense_total / income_total * 100) if income_total else None
        return jsonify({"total_income": float(income_total), "total_expenses": float(expense_total),
                        "current_balance": float(balance), "expense_to_income_ratio_percent": ratio})
    finally:
        conn.close()


@app.get("/api/reports/monthly")
@token_required
def monthly_report():
    """Get monthly totals; supply month=1..12 and year=YYYY. --- tags: [Reports] security: - Bearer: []"""
    try:
        month, year = int(request.args.get("month", date.today().month)), int(request.args.get("year", date.today().year))
        if not 1 <= month <= 12 or not 2000 <= year <= 2100:
            raise ValueError
    except ValueError:
        return response_error("month must be 1-12 and year must be between 2000 and 2100", 400)
    conn = db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(SUM(amount),0) AS total FROM income WHERE user_id=%s AND MONTH(date)=%s AND YEAR(date)=%s", (g.user_id, month, year))
            income_total = Decimal(str(cur.fetchone()["total"]))
            cur.execute("SELECT COALESCE(SUM(amount),0) AS total FROM expenses WHERE user_id=%s AND MONTH(date)=%s AND YEAR(date)=%s", (g.user_id, month, year))
            expense_total = Decimal(str(cur.fetchone()["total"]))
        return jsonify({"month": month, "year": year, "total_income": float(income_total),
                        "total_expenses": float(expense_total), "net_balance": float(income_total-expense_total)})
    finally:
        conn.close()


@app.post("/api/reports/savings-goal")
@token_required
def savings_goal():
    """Compare current balance with a savings goal. --- tags: [Reports] security: - Bearer: []"""
    data = request.get_json(silent=True) or {}
    try:
        goal = Decimal(str(data.get("goal")))
        if not goal.is_finite() or goal <= 0:
            raise ValueError
    except (InvalidOperation, TypeError, ValueError):
        return response_error("goal must be a positive number", 400)
    conn = db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE((SELECT SUM(amount) FROM income WHERE user_id=%s),0) AS income_total, COALESCE((SELECT SUM(amount) FROM expenses WHERE user_id=%s),0) AS expense_total", (g.user_id, g.user_id))
            row = cur.fetchone()
        balance = Decimal(str(row["income_total"])) - Decimal(str(row["expense_total"]))
        return jsonify({"goal": float(goal), "current_balance": float(balance),
                        "goal_achieved": balance >= goal, "remaining": float(max(Decimal("0"), goal-balance))})
    finally:
        conn.close()


@app.errorhandler(404)
def not_found(_error):
    return response_error("Endpoint not found", 404)

@app.errorhandler(405)
def method_not_allowed(_error):
    return response_error("HTTP method not allowed for this endpoint", 405)

@app.errorhandler(400)
def bad_request(_error):
    return response_error("Bad request", 400)

if __name__ == "__main__":
    if not SECRET_KEY:
        print("WARNING: Set JWT_SECRET_KEY in .env before using authentication endpoints.")
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=os.getenv("FLASK_DEBUG", "false").lower() == "true")

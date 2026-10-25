import os
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

import jwt
import pymysql
from flasgger import Swagger
from flask import Flask, jsonify, request, g
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "")
app.config["SWAGGER"] = {
    "title": "Personal Expense Manager API",
    "uiversion": 3,
    "description": "REST API adapted from the original Budget Management System."
}
Swagger(app)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri=os.getenv("RATELIMIT_STORAGE_URI", "memory://"),
)

if not app.config["SECRET_KEY"]:
    # Local development convenience only. Set a long random secret on Render.
    app.config["SECRET_KEY"] = "development-only-change-this-secret"


def db_connection():
    required = ["MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE"]
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise RuntimeError("Missing database environment variables: " + ", ".join(missing))

    kwargs = {
        "host": os.environ["MYSQL_HOST"],
        "user": os.environ["MYSQL_USER"],
        "password": os.environ["MYSQL_PASSWORD"],
        "database": os.environ["MYSQL_DATABASE"],
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
        "autocommit": False,
        "connect_timeout": 10,
    }
    ca_path = os.getenv("MYSQL_SSL_CA")
    if ca_path:
        kwargs["ssl"] = {"ca": ca_path}
    elif os.getenv("MYSQL_SSL", "true").lower() == "true":
        # Encrypted connection. Set MYSQL_SSL_CA to Aiven's CA certificate
        # when available to enable certificate verification as well.
        kwargs["ssl"] = {"check_hostname": False}
    return pymysql.connect(**kwargs)


def init_db():
    """Create the API's tables if they do not already exist."""
    conn = db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    email VARCHAR(254) NOT NULL UNIQUE,
                    password_hash VARCHAR(255) NOT NULL,
                    role ENUM('student','admin') NOT NULL DEFAULT 'student',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS income (
                    S_No INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    source VARCHAR(150) NOT NULL,
                    amount DECIMAL(12,2) NOT NULL,
                    date DATE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_income_user_date (user_id, date),
                    CONSTRAINT fk_income_user FOREIGN KEY (user_id)
                        REFERENCES users(id) ON DELETE CASCADE
                ) ENGINE=InnoDB
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS expenses (
                    ID INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    category VARCHAR(100) NOT NULL,
                    amount DECIMAL(12,2) NOT NULL,
                    date DATE NOT NULL,
                    note VARCHAR(500) DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_expenses_user_date (user_id, date),
                    INDEX idx_expenses_user_category (user_id, category),
                    CONSTRAINT fk_expenses_user FOREIGN KEY (user_id)
                        REFERENCES users(id) ON DELETE CASCADE
                ) ENGINE=InnoDB
            """)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def json_error(message, status=400):
    return jsonify({"error": message}), status


def decimal_amount(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("amount must be a valid number")
    if not amount.is_finite() or amount <= 0:
        raise ValueError("amount must be greater than zero")
    if amount.as_tuple().exponent < -2:
        raise ValueError("amount may have at most two decimal places")
    if amount > Decimal("9999999999.99"):
        raise ValueError("amount is too large")
    return amount


def valid_date(value):
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        raise ValueError("date must use YYYY-MM-DD format")


def get_json():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("Request body must be a JSON object")
    return data


def token_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return json_error("Missing Bearer token", 401)
        token = auth.split(" ", 1)[1].strip()
        try:
            payload = jwt.decode(token, app.config["SECRET_KEY"], algorithms=["HS256"])
            g.user_id = int(payload["user_id"])
            g.user_role = payload.get("role", "student")
        except (jwt.InvalidTokenError, KeyError, TypeError, ValueError):
            return json_error("Invalid or expired token", 401)
        return fn(*args, **kwargs)
    return wrapper


def db_call(callback):
    """Run a database operation with commit/rollback and always close connection."""
    conn = db_connection()
    try:
        result = callback(conn)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def serialize(row):
    if not row:
        return row
    out = dict(row)
    for key, value in out.items():
        if isinstance(value, (date, datetime)):
            out[key] = value.isoformat()
        elif isinstance(value, Decimal):
            out[key] = float(value)
    return out


@app.get("/")
def home():
    return jsonify({
        "service": "Personal Expense Manager API",
        "status": "running",
        "docs": "/docs",
        "health": "/health"
    })


@app.get("/health")
def health():
    try:
        conn = db_connection()
        conn.close()
        return jsonify({"status": "ok", "database": "connected"})
    except Exception:
        app.logger.exception("Health check database connection failed")
        return jsonify({"status": "degraded", "database": "unavailable"}), 503


@app.post("/auth/signup")
@limiter.limit("5 per minute")
def signup():
    """
    Create an account.
    ---
    tags: [Authentication]
    consumes: [application/json]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [name, email, password]
          properties:
            name: {type: string, example: "Alex"}
            email: {type: string, example: "alex@example.com"}
            password: {type: string, example: "a-long-password"}
    responses:
      201: {description: Account created}
      400: {description: Invalid input}
      409: {description: Email already registered}
    """
    try:
        data = get_json()
        name = str(data.get("name", "")).strip()
        email = str(data.get("email", "")).strip().lower()
        password = data.get("password", "")
        if not name or len(name) > 100:
            return json_error("name is required and must be at most 100 characters")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(email) > 254:
            return json_error("A valid email is required")
        if not isinstance(password, str) or len(password) < 8 or len(password) > 128:
            return json_error("password must be 8 to 128 characters")
        password_hash = generate_password_hash(password)

        def operation(conn):
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (name, email, password_hash) VALUES (%s,%s,%s)",
                    (name, email, password_hash)
                )
                return cur.lastrowid
        user_id = db_call(operation)
        return jsonify({"message": "Account created", "user": {"id": user_id, "name": name, "email": email}}), 201
    except ValueError as exc:
        return json_error(str(exc))
    except pymysql.err.IntegrityError:
        return json_error("An account with this email already exists", 409)
    except Exception:
        app.logger.exception("Signup failed")
        return json_error("Could not create account", 500)


@app.post("/auth/login")
@limiter.limit("10 per minute")
def login():
    """
    Log in and receive a JWT.
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
            email: {type: string, example: "alex@example.com"}
            password: {type: string, example: "a-long-password"}
    responses:
      200: {description: JWT returned}
      401: {description: Invalid credentials}
    """
    try:
        data = get_json()
        email = str(data.get("email", "")).strip().lower()
        password = data.get("password", "")
        def operation(conn):
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, email, password_hash, role FROM users WHERE email=%s", (email,))
                return cur.fetchone()
        user = db_call(operation)
        if not user or not isinstance(password, str) or not check_password_hash(user["password_hash"], password):
            return json_error("Invalid email or password", 401)
        token = jwt.encode({
            "user_id": user["id"],
            "role": user["role"],
            "iat": datetime.utcnow(),
            "exp": datetime.utcnow() + timedelta(hours=12)
        }, app.config["SECRET_KEY"], algorithm="HS256")
        return jsonify({
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": 43200,
            "user": {"id": user["id"], "name": user["name"], "email": user["email"], "role": user["role"]}
        })
    except ValueError as exc:
        return json_error(str(exc))
    except Exception:
        app.logger.exception("Login failed")
        return json_error("Could not log in", 500)


@app.get("/auth/me")
@token_required
def me():
    """Return the logged-in user's profile.
    ---
    tags: [Authentication]
    security: [{Bearer: []}]
    responses:
      200: {description: Current user}
    """
    def operation(conn):
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, email, role, created_at FROM users WHERE id=%s", (g.user_id,))
            return serialize(cur.fetchone())
    try:
        user = db_call(operation)
        if not user:
            return json_error("User not found", 404)
        return jsonify(user)
    except Exception:
        app.logger.exception("Profile read failed")
        return json_error("Could not load profile", 500)


def record_routes(table):
    is_income = table == "income"
    id_col = "S_No" if is_income else "ID"
    collection = "income" if is_income else "expenses"
    label = "source" if is_income else "category"

    def list_records():
        """
        List income or expenses with pagination and search.
        ---
        tags: [Income and Expenses]
        security: [{Bearer: []}]
        parameters:
          - in: query
            name: page
            type: integer
            default: 1
          - in: query
            name: per_page
            type: integer
            default: 10
          - in: query
            name: search
            type: string
            description: Search source/category and note fields
          - in: query
            name: month
            type: integer
            description: Optional month (1-12)
          - in: query
            name: year
            type: integer
            description: Optional year
        responses:
          200: {description: Paginated records}
        """
        try:
            page = int(request.args.get("page", "1"))
            per_page = int(request.args.get("per_page", "10"))
            if page < 1 or per_page < 1 or per_page > 100:
                return json_error("page must be >= 1 and per_page must be between 1 and 100")
            search = request.args.get("search", "").strip()
            month = request.args.get("month")
            year = request.args.get("year")
            clauses = ["user_id=%s"]
            params = [g.user_id]
            if search:
                if is_income:
                    clauses.append("source LIKE %s")
                    params.append("%" + search + "%")
                else:
                    clauses.append("(category LIKE %s OR note LIKE %s)")
                    params.extend(["%" + search + "%", "%" + search + "%"])
            if month or year:
                if not (month and year):
                    return json_error("month and year must be supplied together")
                try:
                    m, y = int(month), int(year)
                    if not 1 <= m <= 12 or not 2000 <= y <= 2200:
                        raise ValueError
                except ValueError:
                    return json_error("month must be 1-12 and year must be valid")
                clauses.extend(["MONTH(date)=%s", "YEAR(date)=%s"])
                params.extend([m, y])
            where = " AND ".join(clauses)
            def operation(conn):
                with conn.cursor() as cur:
                    cur.execute(f"SELECT COUNT(*) AS total FROM {table} WHERE {where}", params)
                    total = cur.fetchone()["total"]
                    cur.execute(
                        f"SELECT * FROM {table} WHERE {where} ORDER BY date DESC, {id_col} DESC LIMIT %s OFFSET %s",
                        params + [per_page, (page - 1) * per_page]
                    )
                    rows = [serialize(r) for r in cur.fetchall()]
                    return rows, total
            rows, total = db_call(operation)
            return jsonify({"data": rows, "pagination": {
                "page": page, "per_page": per_page, "total": total,
                "pages": (total + per_page - 1) // per_page
            }})
        except ValueError:
            return json_error("page and per_page must be integers")
        except Exception:
            app.logger.exception("List records failed")
            return json_error("Could not retrieve records", 500)

    def create_record():
        """
        Add income or an expense.
        ---
        tags: [Income and Expenses]
        security: [{Bearer: []}]
        consumes: [application/json]
        parameters:
          - in: body
            name: body
            required: true
            schema:
              type: object
              required: [amount, date]
              properties:
                source: {type: string, example: "Part-time work"}
                category: {type: string, example: "Transport"}
                amount: {type: number, example: 120.50}
                date: {type: string, format: date, example: "2026-10-25"}
                note: {type: string, example: "Bus pass"}
        responses:
          201: {description: Record created}
          400: {description: Validation error}
        """
        try:
            data = get_json()
            name = str(data.get(label, "")).strip()
            if not name or len(name) > (150 if is_income else 100):
                return json_error(f"{label} is required and has an invalid length")
            amount = decimal_amount(data.get("amount"))
            record_date = valid_date(data.get("date"))
            note = str(data.get("note", "")).strip()
            if not is_income and len(note) > 500:
                return json_error("note must be at most 500 characters")
            def operation(conn):
                with conn.cursor() as cur:
                    if is_income:
                        cur.execute(
                            "INSERT INTO income (user_id, source, amount, date) VALUES (%s,%s,%s,%s)",
                            (g.user_id, name, amount, record_date)
                        )
                    else:
                        cur.execute(
                            "INSERT INTO expenses (user_id, category, amount, date, note) VALUES (%s,%s,%s,%s,%s)",
                            (g.user_id, name, amount, record_date, note)
                        )
                    return cur.lastrowid
            new_id = db_call(operation)
            return jsonify({"message": f"{collection[:-1].capitalize() if collection.endswith('s') else collection.capitalize()} record created",
                            "id": new_id}), 201
        except ValueError as exc:
            return json_error(str(exc))
        except Exception:
            app.logger.exception("Create record failed")
            return json_error("Could not create record", 500)

    def get_record(record_id):
        def operation(conn):
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {table} WHERE {id_col}=%s AND user_id=%s", (record_id, g.user_id))
                return serialize(cur.fetchone())
        try:
            row = db_call(operation)
            if not row:
                return json_error("Record not found", 404)
            return jsonify(row)
        except Exception:
            app.logger.exception("Get record failed")
            return json_error("Could not retrieve record", 500)

    def update_record(record_id):
        try:
            data = get_json()
            allowed = {label, "amount", "date"} | ({"note"} if not is_income else set())
            if not data or set(data) - allowed:
                return json_error("Provide only supported fields: " + ", ".join(sorted(allowed)))
            assignments, params = [], []
            if label in data:
                name = str(data[label]).strip()
                if not name or len(name) > (150 if is_income else 100):
                    return json_error(f"{label} is required and has an invalid length")
                assignments.append(f"{label}=%s")
                params.append(name)
            if "amount" in data:
                assignments.append("amount=%s")
                params.append(decimal_amount(data["amount"]))
            if "date" in data:
                assignments.append("date=%s")
                params.append(valid_date(data["date"]))
            if "note" in data and not is_income:
                note = str(data["note"]).strip()
                if len(note) > 500:
                    return json_error("note must be at most 500 characters")
                assignments.append("note=%s")
                params.append(note)
            if not assignments:
                return json_error("At least one field is required")
            params.extend([record_id, g.user_id])
            def operation(conn):
                with conn.cursor() as cur:
                    cur.execute(f"UPDATE {table} SET {', '.join(assignments)} WHERE {id_col}=%s AND user_id=%s", params)
                    if cur.rowcount == 0:
                        cur.execute(f"SELECT {id_col} FROM {table} WHERE {id_col}=%s AND user_id=%s", (record_id, g.user_id))
                        return cur.fetchone() is not None
                    return True
            found = db_call(operation)
            if not found:
                return json_error("Record not found", 404)
            return jsonify({"message": "Record updated"})
        except ValueError as exc:
            return json_error(str(exc))
        except Exception:
            app.logger.exception("Update record failed")
            return json_error("Could not update record", 500)

    def delete_record(record_id):
        def operation(conn):
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {table} WHERE {id_col}=%s AND user_id=%s", (record_id, g.user_id))
                return cur.rowcount
        try:
            deleted = db_call(operation)
            if not deleted:
                return json_error("Record not found", 404)
            return jsonify({"message": "Record deleted"})
        except Exception:
            app.logger.exception("Delete record failed")
            return json_error("Could not delete record", 500)

    endpoint = f"{collection}_list"
    app.add_url_rule(f"/{collection}", endpoint, token_required(list_records), methods=["GET"])
    app.add_url_rule(f"/{collection}", f"{collection}_create", token_required(create_record), methods=["POST"])
    app.add_url_rule(f"/{collection}/<int:record_id>", f"{collection}_get", token_required(get_record), methods=["GET"])
    app.add_url_rule(f"/{collection}/<int:record_id>", f"{collection}_update", token_required(update_record), methods=["PUT", "PATCH"])
    app.add_url_rule(f"/{collection}/<int:record_id>", f"{collection}_delete", token_required(delete_record), methods=["DELETE"])


record_routes("income")
record_routes("expenses")


@app.get("/summary")
@token_required
def summary():
    """Get totals, balance, and expense-to-income percentage.
    ---
    tags: [Reports]
    security: [{Bearer: []}]
    responses:
      200: {description: Budget summary}
    """
    try:
        def operation(conn):
            with conn.cursor() as cur:
                cur.execute("SELECT COALESCE(SUM(amount),0) AS total FROM income WHERE user_id=%s", (g.user_id,))
                total_income = cur.fetchone()["total"]
                cur.execute("SELECT COALESCE(SUM(amount),0) AS total FROM expenses WHERE user_id=%s", (g.user_id,))
                total_expenses = cur.fetchone()["total"]
                income = Decimal(total_income)
                expenses = Decimal(total_expenses)
                return {
                    "total_income": float(income),
                    "total_expenses": float(expenses),
                    "balance": float(income - expenses),
                    "expense_to_income_percent": float((expenses / income * 100) if income else Decimal("0"))
                }
        return jsonify(db_call(operation))
    except Exception:
        app.logger.exception("Summary failed")
        return json_error("Could not calculate summary", 500)


@app.get("/reports/monthly")
@token_required
def monthly_report():
    """Get income and expenses for a month.
    ---
    tags: [Reports]
    security: [{Bearer: []}]
    parameters:
      - in: query
        name: month
        required: true
        type: integer
        example: 10
      - in: query
        name: year
        required: true
        type: integer
        example: 2026
    responses:
      200: {description: Monthly report}
    """
    try:
        month, year = int(request.args.get("month", "")), int(request.args.get("year", ""))
        if not 1 <= month <= 12 or not 2000 <= year <= 2200:
            return json_error("month must be 1-12 and year must be valid")
        def operation(conn):
            with conn.cursor() as cur:
                cur.execute("SELECT COALESCE(SUM(amount),0) AS total FROM income WHERE user_id=%s AND MONTH(date)=%s AND YEAR(date)=%s",
                            (g.user_id, month, year))
                inc = Decimal(cur.fetchone()["total"])
                cur.execute("SELECT COALESCE(SUM(amount),0) AS total FROM expenses WHERE user_id=%s AND MONTH(date)=%s AND YEAR(date)=%s",
                            (g.user_id, month, year))
                exp = Decimal(cur.fetchone()["total"])
                cur.execute("SELECT S_No, source, amount, date FROM income WHERE user_id=%s AND MONTH(date)=%s AND YEAR(date)=%s ORDER BY date DESC",
                            (g.user_id, month, year))
                incomes = [serialize(r) for r in cur.fetchall()]
                cur.execute("SELECT ID, category, amount, date, note FROM expenses WHERE user_id=%s AND MONTH(date)=%s AND YEAR(date)=%s ORDER BY date DESC",
                            (g.user_id, month, year))
                expenses = [serialize(r) for r in cur.fetchall()]
                return {"month": month, "year": year, "total_income": float(inc),
                        "total_expenses": float(exp), "net_balance": float(inc-exp),
                        "income_records": incomes, "expense_records": expenses}
        return jsonify(db_call(operation))
    except ValueError:
        return json_error("month and year are required integers")
    except Exception:
        app.logger.exception("Monthly report failed")
        return json_error("Could not generate monthly report", 500)


@app.get("/savings-goal")
@token_required
def savings_goal():
    """Compare the current balance with a savings goal.
    ---
    tags: [Reports]
    security: [{Bearer: []}]
    parameters:
      - in: query
        name: goal
        required: true
        type: number
        example: 10000
    responses:
      200: {description: Savings goal progress}
    """
    try:
        goal = decimal_amount(request.args.get("goal"))
        def operation(conn):
            with conn.cursor() as cur:
                cur.execute("SELECT COALESCE(SUM(amount),0) AS total FROM income WHERE user_id=%s", (g.user_id,))
                income = Decimal(cur.fetchone()["total"])
                cur.execute("SELECT COALESCE(SUM(amount),0) AS total FROM expenses WHERE user_id=%s", (g.user_id,))
                expenses = Decimal(cur.fetchone()["total"])
                balance = income - expenses
                return {"goal": float(goal), "current_balance": float(balance),
                        "achieved": balance >= goal, "remaining": float(max(Decimal("0"), goal-balance))}
        return jsonify(db_call(operation))
    except ValueError as exc:
        return json_error(str(exc))
    except Exception:
        app.logger.exception("Savings goal failed")
        return json_error("Could not check savings goal", 500)


@app.get("/reports/highest")
@token_required
def highest_records():
    """Return highest income and expense records.
    ---
    tags: [Reports]
    security: [{Bearer: []}]
    responses:
      200: {description: Highest records}
    """
    try:
        def operation(conn):
            with conn.cursor() as cur:
                cur.execute("SELECT S_No, source, amount, date FROM income WHERE user_id=%s ORDER BY amount DESC LIMIT 1", (g.user_id,))
                highest_income = serialize(cur.fetchone())
                cur.execute("SELECT ID, category, amount, date, note FROM expenses WHERE user_id=%s ORDER BY amount DESC LIMIT 1", (g.user_id,))
                highest_expense = serialize(cur.fetchone())
                return {"highest_income": highest_income, "highest_expense": highest_expense}
        return jsonify(db_call(operation))
    except Exception:
        app.logger.exception("Highest records failed")
        return json_error("Could not retrieve highest records", 500)


@app.errorhandler(404)
def not_found(_):
    return json_error("Endpoint not found", 404)


@app.errorhandler(405)
def method_not_allowed(_):
    return json_error("Method not allowed for this endpoint", 405)


if __name__ == "__main__":
    # Set INIT_DB=true locally to create tables on startup.
    if os.getenv("INIT_DB", "false").lower() == "true":
        init_db()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)

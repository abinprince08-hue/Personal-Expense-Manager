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
app.config["SECRET_KEY"] = os.getenv(
    "FLASK_SECRET_KEY",
    "development-only-change-this-secret"
)

app.config["SWAGGER"] = {
    "title": "Personal Expense Manager API",
    "uiversion": 3,
    "description": "REST API based on the original Budget Management System."
}

Swagger(app)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri=os.getenv("RATELIMIT_STORAGE_URI", "memory://"),
)


def db_connection():
    required = [
        "MYSQL_HOST",
        "MYSQL_USER",
        "MYSQL_PASSWORD",
        "MYSQL_DATABASE"
    ]

    missing = [key for key in required if not os.getenv(key)]

    if missing:
        raise RuntimeError(
            "Missing database settings: " + ", ".join(missing)
        )

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
        kwargs["ssl"] = {"check_hostname": False}

    return pymysql.connect(**kwargs)


def init_db():
    conn = db_connection()

    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    email VARCHAR(254) NOT NULL UNIQUE,
                    password_hash VARCHAR(255) NOT NULL,
                    role ENUM('student','admin')
                        NOT NULL DEFAULT 'student',
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
                    CONSTRAINT fk_income_user
                        FOREIGN KEY (user_id)
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
                    INDEX idx_expenses_user_category
                        (user_id, category),
                    CONSTRAINT fk_expenses_user
                        FOREIGN KEY (user_id)
                        REFERENCES users(id) ON DELETE CASCADE
                ) ENGINE=InnoDB
            """)

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


# Set INIT_DB=true in Render only for initial table creation.
# After successful initialization, change it to false.
if os.getenv("INIT_DB", "false").lower() == "true":
    init_db()


def json_error(message, status=400):
    return jsonify({"error": message}), status


def decimal_amount(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Amount must be a valid number")

    if not amount.is_finite() or amount <= 0:
        raise ValueError("Amount must be greater than zero")

    if amount.as_tuple().exponent < -2:
        raise ValueError("Amount may have at most two decimal places")

    if amount > Decimal("9999999999.99"):
        raise ValueError("Amount is too large")

    return amount


def valid_date(value):
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        raise ValueError("Date must use YYYY-MM-DD format")


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
            payload = jwt.decode(
                token,
                app.config["SECRET_KEY"],
                algorithms=["HS256"]
            )

            g.user_id = int(payload["user_id"])
            g.user_role = payload.get("role", "student")

        except (
            jwt.InvalidTokenError,
            KeyError,
            TypeError,
            ValueError
        ):
            return json_error("Invalid or expired token", 401)

        return fn(*args, **kwargs)

    return wrapper


def db_call(callback):
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

    result = dict(row)

    for key, value in result.items():
        if isinstance(value, (date, datetime)):
            result[key] = value.isoformat()

        elif isinstance(value, Decimal):
            result[key] = float(value)

    return result


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

        return jsonify({
            "status": "ok",
            "database": "connected"
        })

    except Exception:
        app.logger.exception("Database health check failed")

        return jsonify({
            "status": "degraded",
            "database": "unavailable"
        }), 503


@app.post("/auth/signup")
@limiter.limit("5 per minute")
def signup():
    try:
        data = get_json()

        name = str(data.get("name", "")).strip()
        email = str(data.get("email", "")).strip().lower()
        password = data.get("password", "")

        if not name or len(name) > 100:
            return json_error(
                "Name is required and must be at most 100 characters"
            )

        if (
            not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)
            or len(email) > 254
        ):
            return json_error("A valid email is required")

        if (
            not isinstance(password, str)
            or len(password) < 8
            or len(password) > 128
        ):
            return json_error(
                "Password must be 8 to 128 characters"
            )

        password_hash = generate_password_hash(password)

        def operation(conn):
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users
                    (name, email, password_hash)
                    VALUES (%s, %s, %s)
                    """,
                    (name, email, password_hash)
                )

                return cur.lastrowid

        user_id = db_call(operation)

        return jsonify({
            "message": "Account created",
            "user": {
                "id": user_id,
                "name": name,
                "email": email
            }
        }), 201

    except ValueError as exc:
        return json_error(str(exc))

    except pymysql.err.IntegrityError:
        return json_error(
            "An account with this email already exists",
            409
        )

    except Exception:
        app.logger.exception("Signup failed")
        return json_error("Could not create account", 500)


@app.post("/auth/login")
@limiter.limit("10 per minute")
def login():
    try:
        data = get_json()

        email = str(data.get("email", "")).strip().lower()
        password = data.get("password", "")

        def operation(conn):
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, name, email, password_hash, role
                    FROM users
                    WHERE email = %s
                    """,
                    (email,)
                )

                return cur.fetchone()

        user = db_call(operation)

        if (
            not user
            or not isinstance(password, str)
            or not check_password_hash(
                user["password_hash"], password
            )
        ):
            return json_error("Invalid email or password", 401)

        now = datetime.utcnow()

        token = jwt.encode(
            {
                "user_id": user["id"],
                "role": user["role"],
                "iat": now,
                "exp": now + timedelta(hours=12)
            },
            app.config["SECRET_KEY"],
            algorithm="HS256"
        )

        return jsonify({
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": 43200,
            "user": {
                "id": user["id"],
                "name": user["name"],
                "email": user["email"],
                "role": user["role"]
            }
        })

    except ValueError as exc:
        return json_error(str(exc))

    except Exception:
        app.logger.exception("Login failed")
        return json_error("Could not log in", 500)


@app.get("/auth/me")
@token_required
def me():
    try:
        def operation(conn):
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, name, email, role, created_at
                    FROM users
                    WHERE id = %s
                    """,
                    (g.user_id,)
                )

                return serialize(cur.fetchone())

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
        try:
            page = int(request.args.get("page", "1"))
            per_page = int(request.args.get("per_page", "10"))

            if page < 1 or per_page < 1 or per_page > 100:
                return json_error(
                    "Page must be >= 1 and per_page must be 1 to 100"
                )

            search = request.args.get("search", "").strip()
            month = request.args.get("month")
            year = request.args.get("year")

            clauses = ["user_id = %s"]
            params = [g.user_id]

            if search:
                if is_income:
                    clauses.append("source LIKE %s")
                    params.append("%" + search + "%")
                else:
                    clauses.append(
                        "(category LIKE %s OR note LIKE %s)"
                    )
                    params.extend([
                        "%" + search + "%",
                        "%" + search + "%"
                    ])

            if month or year:
                if not (month and year):
                    return json_error(
                        "Month and year must be supplied together"
                    )

                try:
                    m = int(month)
                    y = int(year)

                    if not 1 <= m <= 12 or not 2000 <= y <= 2200:
                        raise ValueError

                except ValueError:
                    return json_error(
                        "Month must be 1-12 and year must be valid"
                    )

                clauses.extend([
                    "MONTH(date) = %s",
                    "YEAR(date) = %s"
                ])
                params.extend([m, y])

            where = " AND ".join(clauses)

            def operation(conn):
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT COUNT(*) AS total FROM {table} "
                        f"WHERE {where}",
                        params
                    )

                    total = cur.fetchone()["total"]

                    cur.execute(
                        f"""
                        SELECT * FROM {table}
                        WHERE {where}
                        ORDER BY date DESC, {id_col} DESC
                        LIMIT %s OFFSET %s
                        """,
                        params + [
                            per_page,
                            (page - 1) * per_page
                        ]
                    )

                    rows = [
                        serialize(row) for row in cur.fetchall()
                    ]

                    return rows, total

            rows, total = db_call(operation)

            return jsonify({
                "data": rows,
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": total,
                    "pages": (total + per_page - 1) // per_page
                }
            })

        except ValueError:
            return json_error("Page values must be integers")

        except Exception:
            app.logger.exception("List records failed")
            return json_error("Could not retrieve records", 500)

    def create_record():
        try:
            data = get_json()

            name = str(data.get(label, "")).strip()

            max_length = 150 if is_income else 100

            if not name or len(name) > max_length:
                return json_error(
                    f"{label} is required and has an invalid length"
                )

            amount = decimal_amount(data.get("amount"))
            record_date = valid_date(data.get("date"))
            note = str(data.get("note", "")).strip()

            if not is_income and len(note) > 500:
                return json_error(
                    "Note must be at most 500 characters"
                )

            def operation(conn):
                with conn.cursor() as cur:
                    if is_income:
                        cur.execute(
                            """
                            INSERT INTO income
                            (user_id, source, amount, date)
                            VALUES (%s, %s, %s, %s)
                            """,
                            (
                                g.user_id,
                                name,
                                amount,
                                record_date
                            )
                        )
                    else:
                        cur.execute(
                            """
                            INSERT INTO expenses
                            (user_id, category, amount, date, note)
                            VALUES (%s, %s, %s, %s, %s)
                            """,
                            (
                                g.user_id,
                                name,
                                amount,
                                record_date,
                                note
                            )
                        )

                    return cur.lastrowid

            new_id = db_call(operation)

            return jsonify({
                "message": f"{collection.capitalize()} record created",
                "id": new_id
            }), 201

        except ValueError as exc:
            return json_error(str(exc))

        except Exception:
            app.logger.exception("Create record failed")
            return json_error("Could not create record", 500)

    def get_record(record_id):
        try:
            def operation(conn):
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        SELECT * FROM {table}
                        WHERE {id_col} = %s AND user_id = %s
                        """,
                        (record_id, g.user_id)
                    )

                    return serialize(cur.fetchone())

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

            allowed = {label, "amount", "date"}

            if not is_income:
                allowed.add("note")

            if not data or set(data) - allowed:
                return json_error(
                    "Provide only supported fields: "
                    + ", ".join(sorted(allowed))
                )

            assignments = []
            params = []

            if label in data:
                name = str(data[label]).strip()
                max_length = 150 if is_income else 100

                if not name or len(name) > max_length:
                    return json_error(
                        f"{label} is required and has an invalid length"
                    )

                assignments.append(f"{label} = %s")
                params.append(name)

            if "amount" in data:
                assignments.append("amount = %s")
                params.append(decimal_amount(data["amount"]))

            if "date" in data:
                assignments.append("date = %s")
                params.append(valid_date(data["date"]))

            if "note" in data and not is_income:
                note = str(data["note"]).strip()

                if len(note) > 500:
                    return json_error(
                        "Note must be at most 500 characters"
                    )

                assignments.append("note = %s")
                params.append(note)

            if not assignments:
                return json_error("At least one field is required")

            params.extend([record_id, g.user_id])

            def operation(conn):
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        UPDATE {table}
                        SET {', '.join(assignments)}
                        WHERE {id_col} = %s AND user_id = %s
                        """,
                        params
                   

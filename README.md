# Personal Expense Manager API

Backend-only REST API based on the original Python Budget Management System. It keeps the original project's main ideas: add/view/delete income and expenses, budget summary, expense-to-income ratio, monthly reports, savings goals, and highest records. Console `input()`/`print()` interactions are replaced with HTTP requests and JSON responses so the project can be used as an API.

## Stack

- Python + Flask
- Aiven MySQL + PyMySQL
- JWT authentication
- Werkzeug password hashing
- Swagger UI via Flasgger
- Rate limiting

## Important compatibility note

The original file is a console program and cannot run unchanged as a REST API: it reads from `input()`, prints tables, and opens a Matplotlib chart. This API preserves its main business features but adapts them to HTTP/JSON. Keep your original `Abin project.py` unchanged on your device. It is not included in this deployable package because it contains a hard-coded local database password; do not upload that original file publicly. The API adapts its core features in `app.py`. The API creates its own `users`, `income`, and `expenses` tables and associates every record with a user. Do not point this at a database with incompatible tables without first backing it up and reviewing the schema.

## Run locally

1. Install Python 3.11 or 3.12.
2. Create a virtual environment and install packages:

   ```bash
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # macOS/Linux:
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env` and set the Aiven connection details. The app does not automatically load `.env`; export variables in your shell or install `python-dotenv` and load it explicitly if you want automatic local loading. For example, in PowerShell:

   ```powershell
   $env:FLASK_SECRET_KEY="a-long-random-secret"
   $env:MYSQL_HOST="your-aiven-host"
   $env:MYSQL_PORT="3306"
   $env:MYSQL_DATABASE="defaultdb"
   $env:MYSQL_USER="avnadmin"
   $env:MYSQL_PASSWORD="your-password"
   $env:MYSQL_SSL="true"
   $env:INIT_DB="true"
   python app.py
   ```

   Add `MYSQL_SSL_CA` pointing to Aiven's CA certificate when available so the server certificate can be verified. Do not commit credentials or certificates to a public repository.

4. Open `http://127.0.0.1:5000/docs` for Swagger UI. `GET /health` checks connectivity.

## Deploy to Render

1. Push this folder to a GitHub repository.
2. In Render, create a **Web Service** from that repository.
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn app:app`
5. Set these environment variables in Render:
   - `FLASK_SECRET_KEY`: use a long random secret (Render can generate it)
   - `MYSQL_HOST`: Aiven host (without `https://`)
   - `MYSQL_PORT`: usually `3306`, or the port shown by Aiven
   - `MYSQL_DATABASE`, `MYSQL_USER`, `MYSQL_PASSWORD`: values from Aiven
   - `MYSQL_SSL=true`
   - `MYSQL_SSL_CA`: optional path only if you have securely supplied the CA certificate to the service
6. Initialize the schema once, from a trusted environment, by running `python -c "from app import init_db; init_db()"` with the same environment variables, or temporarily set `INIT_DB=true` and start the service once. Prefer a one-off initialization; then remove `INIT_DB`.
7. Test `https://YOUR-SERVICE.onrender.com/health` and `https://YOUR-SERVICE.onrender.com/docs`.

**Never put database passwords or JWT secrets in GitHub.** Anyone with database credentials may be able to read or change your records.

## Authentication workflow

1. `POST /auth/signup` with JSON `{"name":"Alex","email":"alex@example.com","password":"a-long-password"}`
2. `POST /auth/login` with JSON `{"email":"alex@example.com","password":"a-long-password"}`
3. Copy `access_token` from the response.
4. In Swagger, click **Authorize** and enter `Bearer YOUR_TOKEN`, or send the HTTP header `Authorization: Bearer YOUR_TOKEN` in Postman.

## Endpoints

### Authentication
- `POST /auth/signup`
- `POST /auth/login`
- `GET /auth/me` (JWT)

### Income
- `GET /income?page=1&per_page=10&search=salary`
- `POST /income`
- `GET /income/{record_id}`
- `PUT` or `PATCH /income/{record_id}`
- `DELETE /income/{record_id}`

Income body example:
```json
{"source":"Part-time work","amount":5000,"date":"2026-10-25"}
```

### Expenses
- `GET /expenses?page=1&per_page=10&search=transport`
- `POST /expenses`
- `GET /expenses/{record_id}`
- `PUT` or `PATCH /expenses/{record_id}`
- `DELETE /expenses/{record_id}`

Expense body example:
```json
{"category":"Transport","amount":120.50,"date":"2026-10-25","note":"Bus pass"}
```

### Reports
- `GET /summary`
- `GET /reports/monthly?month=10&year=2026`
- `GET /savings-goal?goal=10000`
- `GET /reports/highest`

Every income/expense query is scoped to the authenticated user. Other users cannot read or modify the records. Amounts must be positive and have at most two decimal places. Dates use `YYYY-MM-DD`. Pagination allows up to 100 rows per page.

## HTTP status codes

- `200`: request succeeded
- `201`: resource created
- `400`: invalid input
- `401`: missing/invalid authentication
- `404`: resource or endpoint not found
- `409`: duplicate email
- `429`: rate limit reached
- `500`: unexpected server error

## Notes and future improvements

- Rate limiting currently uses in-memory storage, which is suitable for a small demo but resets on restart and is not shared between multiple instances. Use Redis for production.
- Role column is included, but admin-only routes are not implemented in this first version.
- Matplotlib's interactive pie chart is intentionally not included in the API response; a backend API should return data. A future client can chart the expense category totals.
- Tests should be added before presenting this as production-ready.

# Personal Finance REST API

A backend-only REST API adapted from the user's original **Budget Management System** project. The original PDF documents a single-file Python program connected to MySQL, with income and expense records, totals, a maximum-record report, a category chart, a savings-goal check, and monthly reports. This API keeps those core concepts while adding HTTP endpoints, per-user data isolation, password hashing, JWT authentication, validation, and Swagger documentation.

> Source note: the provided original is a PDF containing code screenshots, not the original `.py` source. This is a careful adaptation of the code/features visible in that PDF, not a byte-for-byte conversion. The schema uses the original concepts (`income.source`, `expenses.category`, `amount`, and `date`) and adds `id`/`user_id` for API records and access control.

## Tech stack

- Python + Flask
- MySQL + PyMySQL
- JWT authentication (PyJWT)
- Werkzeug password hashing
- Swagger UI (Flasgger)

## Features

- Register and log in
- Hashed passwords; short-lived JWT access token
- Create, list, update, and delete income and expense records
- User-specific data access
- Validate amount, category/source, date, and report parameters
- Summary report, monthly report, savings-goal check
- JSON error responses and Swagger UI

## Requirements

- Python 3.10+
- MySQL Server running locally
- VS Code or another editor

## Setup on Windows PowerShell

1. Create and activate a virtual environment:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

2. Install packages:

   ```powershell
   python -m pip install -r requirements.txt
   ```

3. Create the database and tables. In MySQL Workbench, open `schema.sql` and run it, or run it from a MySQL client. If your MySQL account cannot create databases, create `budgetdb` first and run the table statements after `USE budgetdb;`.

4. Copy `.env.example` to `.env` and fill in your MySQL password and a long, random `JWT_SECRET_KEY`. Do not commit `.env` to GitHub.

   ```powershell
   Copy-Item .env.example .env
   ```

5. Start the API:

   ```powershell
   python app.py
   ```

6. Visit:
   - Health check: http://127.0.0.1:5000/
   - Database check: http://127.0.0.1:5000/api/health/db
   - Swagger docs: http://127.0.0.1:5000/apidocs/

## Quick test flow

### 1. Register

`POST http://127.0.0.1:5000/api/auth/register`

```json
{"email":"student@example.com","password":"a-strong-password"}
```

### 2. Log in

`POST http://127.0.0.1:5000/api/auth/login`

```json
{"email":"student@example.com","password":"a-strong-password"}
```

Copy `access_token` from the response. In Postman, add an Authorization header:

`Authorization: Bearer YOUR_ACCESS_TOKEN`

### 3. Add an expense

`POST http://127.0.0.1:5000/api/expenses`

```json
{"amount":250,"category":"Food","date":"2026-10-25"}
```

### 4. List expenses

`GET http://127.0.0.1:5000/api/expenses` with the same Authorization header.

### 5. Other endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/` | API health |
| GET | `/api/health/db` | Check MySQL connectivity |
| POST | `/api/auth/register` | Register |
| POST | `/api/auth/login` | Get JWT |
| GET / POST | `/api/income` | List / create income |
| PUT / DELETE | `/api/income/{id}` | Update / delete income |
| GET / POST | `/api/expenses` | List / create expenses |
| PUT / DELETE | `/api/expenses/{id}` | Update / delete expenses |
| GET | `/api/reports/summary` | Overall totals and balance |
| GET | `/api/reports/monthly?month=10&year=2026` | Monthly totals |
| POST | `/api/reports/savings-goal` | Compare balance to goal; body `{"goal":5000}` |

## Error behavior

- `400`: invalid/missing input
- `401`: missing or invalid token / invalid credentials
- `404`: endpoint or owned record not found
- `405`: unsupported HTTP method
- `409`: email already registered
- `500` / `503`: server or database configuration failure

## Security notes

- Passwords are hashed; plaintext passwords are not stored.
- Expense and income queries are scoped to the authenticated user's `user_id`.
- SQL values use parameterized queries.
- `.env` is ignored by Git.
- The Flask development server is for local development only. Before public deployment, use a production WSGI server, HTTPS, a strong secret, and rate limiting. This starter does not yet implement rate limiting or automated tests.

## Relationship to the original project

The original PDF shows a single Python file using PyMySQL, PrettyTable, and Matplotlib. The REST API keeps the original financial records and core calculations but changes the interaction model: API clients send JSON instead of responding to terminal `input()` prompts. The original terminal menu and chart display are not part of this backend-only API.

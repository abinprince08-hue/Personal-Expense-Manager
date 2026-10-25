# Postman examples

Base URL: `http://127.0.0.1:5000`

## Register
- Method: `POST`
- URL: `/api/auth/register`
- Body → raw → JSON:
```json
{"email":"student@example.com","password":"a-strong-password"}
```

## Login
- Method: `POST`
- URL: `/api/auth/login`
- Body → raw → JSON:
```json
{"email":"student@example.com","password":"a-strong-password"}
```
Copy `access_token` from the response. For protected requests, add header `Authorization` with value `Bearer <token>`.

## Create expense
- Method: `POST`
- URL: `/api/expenses`
- Headers: `Authorization: Bearer <token>`, `Content-Type: application/json`
- Body:
```json
{"amount":250,"category":"Food","date":"2026-10-25"}
```

## List expenses
- Method: `GET`
- URL: `/api/expenses`
- Header: `Authorization: Bearer <token>`

## Summary
- Method: `GET`
- URL: `/api/reports/summary`
- Header: `Authorization: Bearer <token>`

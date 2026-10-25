# Quick API examples

Base URL locally: `http://127.0.0.1:5000`

## Sign up
```http
POST /auth/signup
Content-Type: application/json

{"name":"Alex","email":"alex@example.com","password":"a-long-password"}
```

## Log in
```http
POST /auth/login
Content-Type: application/json

{"email":"alex@example.com","password":"a-long-password"}
```

Copy the `access_token` from the response. Add this header to every protected request:
```http
Authorization: Bearer YOUR_ACCESS_TOKEN
```

## Add an expense
```http
POST /expenses
Authorization: Bearer YOUR_ACCESS_TOKEN
Content-Type: application/json

{"category":"Transport","amount":120.50,"date":"2026-10-25","note":"Bus pass"}
```

## View summary
```http
GET /summary
Authorization: Bearer YOUR_ACCESS_TOKEN
```

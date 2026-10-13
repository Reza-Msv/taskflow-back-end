# API Contracts & Conventions

## Source of Truth
FastAPI schemas are exported as OpenAPI 3.1 specification at:
- JSON: `/api/v1/openapi.json`
- Interactive Swagger: `/docs`

The frontend consumes this schema directly using `openapi-typescript` to ensure zero drift.

## Standard Error Format
All errors (4xx, 5xx) return:
```json
{
  "error": {
    "code": "ERROR_MACHINE_NAME",
    "message": "Human readable, safe error message",
    "details": {},
    "request_id": "req_01j9a..."
  }
}
```

## Pagination Schema
Offset-based pagination:
```json
{
  "items": [],
  "page": 1,
  "page_size": 25,
  "total": 100
}
```
Cursor-based pagination (for high-volume feeds):
```json
{
  "items": [],
  "next_cursor": "eyJpZCI6...",
  "has_more": true
}
```

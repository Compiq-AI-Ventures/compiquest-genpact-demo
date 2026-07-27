# CompiqCore Backend — API Documentation

**Version:** 1.0  
**Base URL:** `https://api.compiq.ai` (production) · `http://localhost:8000` (local)  
**Protocol:** HTTPS (TLS 1.2+) required in all non-local environments  
**Content-Type:** `application/json` for all requests and responses  
**API Style:** RESTful, JSON over HTTP  
**Auth Scheme:** Bearer JWT (HS256)

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Authentication Model](#2-authentication-model)
3. [Request & Response Conventions](#3-request--response-conventions)
4. [Rate Limiting](#4-rate-limiting)
5. [Health & Root Endpoints](#5-health--root-endpoints)
6. [Authentication Endpoints](#6-authentication-endpoints)
   - 6.1 [POST /auth/login](#61-post-authlogin)
   - 6.2 [POST /auth/refresh](#62-post-authrefresh)
   - 6.3 [POST /auth/logout](#63-post-authlogout)
   - 6.4 [GET /auth/me](#64-get-authme)
7. [Admin — Platform Users](#7-admin--platform-users)
   - 7.1 [POST /admin/users](#71-post-adminusers)
   - 7.2 [POST /admin/tenants/{tenant_id}/users](#72-post-admintenandtstenant_idusers)
8. [Admin — Tenant Management](#8-admin--tenant-management)
   - 8.1 [POST /admin/tenants](#81-post-admintenants)
   - 8.2 [GET /admin/tenants](#82-get-admintenants)
   - 8.3 [GET /admin/tenants/{tenant_id}](#83-get-admintenandtstenant_id)
   - 8.4 [PATCH /admin/tenants/{tenant_id}](#84-patch-admintenandtstenant_id)
9. [Departments](#9-departments)
   - 9.1 [GET /departments](#91-get-departments)
   - 9.2 [GET /departments/{department_id}](#92-get-departmentsdepartment_id)
   - 9.3 [POST /departments](#93-post-departments)
   - 9.4 [PATCH /departments/{department_id}](#94-patch-departmentsdepartment_id)
   - 9.5 [DELETE /departments/{department_id}](#95-delete-departmentsdepartment_id)
10. [Compensation Cycles](#10-compensation-cycles)
    - 10.1 [GET /comp-cycles/active](#101-get-comp-cyclesactive)
    - 10.2 [GET /comp-cycles/{cycle_id}](#102-get-comp-cyclescycle_id)
    - 10.3 [GET /comp-cycles/{cycle_id}/my-budget-allocation](#103-get-comp-cyclescycle_idmy-budget-allocation)
    - 10.4 [GET /comp-cycles/{cycle_id}/my-recommendations](#104-get-comp-cyclescycle_idmy-recommendations)
11. [Budget Allocations](#11-budget-allocations)
    - 11.1 [GET /budget-allocations/{allocation_id}/lines](#111-get-budget-allocationsallocation_idlines)
    - 11.2 [GET /budget-allocations/{allocation_id}/team-risk-snapshot](#112-get-budget-allocationsallocation_idteam-risk-snapshot)
    - 11.3 [PUT /comp-cycles/{cycle_id}/my-budget-allocation](#113-put-comp-cyclescycle_idmy-budget-allocation)
    - 11.4 [POST /budget-allocations/{allocation_id}/align-with-jvre](#114-post-budget-allocationsallocation_idalign-with-jvre)
    - 11.5 [PUT /budget-allocations/{allocation_id}/lines/{line_id}](#115-put-budget-allocationsallocation_idlinesline_id)
    - 11.6 [POST /budget-allocations/{allocation_id}/lines/{line_id}/refresh-view](#116-post-budget-allocationsallocation_idlinesline_idrefresh-view)
    - 11.7 [POST /budget-allocations/{allocation_id}/submit](#117-post-budget-allocationsallocation_idsubmit)
12. [Pay Recommendations](#12-pay-recommendations)
    - 12.1 [GET /pay-recommendations/pending-review](#121-get-pay-recommendationspending-review)
    - 12.2 [GET /pay-recommendations/{recommendation_id}](#122-get-pay-recommendationsrecommendation_id)
    - 12.3 [POST /comp-cycles/{cycle_id}/recommendations](#123-post-comp-cyclescycle_idrecommendations)
    - 12.4 [PUT /pay-recommendations/{recommendation_id}/components/{component}](#124-put-pay-recommendationsrecommendation_idcomponentscomponent)
    - 12.5 [POST /pay-recommendations/{recommendation_id}/align-with-jvre](#125-post-pay-recommendationsrecommendation_idalign-with-jvre)
    - 12.6 [POST /pay-recommendations/{recommendation_id}/save](#126-post-pay-recommendationsrecommendation_idsave)
    - 12.7 [POST /comp-cycles/{cycle_id}/my-recommendations/submit](#127-post-comp-cyclescycle_idmy-recommendationssubmit)
    - 12.8 [POST /pay-recommendations/{recommendation_id}/approve](#128-post-pay-recommendationsrecommendation_idapprove)
    - 12.9 [POST /pay-recommendations/{recommendation_id}/revise](#129-post-pay-recommendationsrecommendation_idrevise)
    - 12.10 [POST /pay-recommendations/{recommendation_id}/annotations](#1210-post-pay-recommendationsrecommendation_idannotations)
13. [JVRE Snapshots & Reference Data](#13-jvre-snapshots--reference-data)
    - 13.1 [GET /jvre/snapshots/{cycle_id}/{subject_user_id}](#131-get-jvresnapshotscycle_idsubject_user_id)
    - 13.2 [GET /users/{subject_user_id}/market-benchmark](#132-get-userssubject_user_idmarket-benchmark)
    - 13.3 [GET /users/{subject_user_id}/compensation-history](#133-get-userssubject_user_idcompensation-history)
14. [Error Reference](#14-error-reference)
15. [Security Architecture](#15-security-architecture)
16. [Integration Guide](#16-integration-guide)
17. [Environment & Deployment](#17-environment--deployment)

---

## 1. System Overview

CompiqCore is an enterprise SaaS compensation management platform. The backend API powers the full compensation planning lifecycle:

- **Tenant provisioning** — create and manage isolated customer organizations
- **User management** — platform-level and tenant-level user creation with role assignment
- **Department management** — organizational hierarchy within a tenant
- **Compensation cycles** — annual/periodic comp planning workflows (DRAFT → ACTIVE → CLOSED)
- **Budget allocations** — hierarchical budget distribution from C-suite down to managers
- **JVRE (Job Value & Reward Engine)** — AI-driven compensation recommendations with market benchmarking
- **Pay recommendations** — manager-driven individual pay decisions with multi-layer approval workflows
- **Audit trail** — immutable log of every sensitive action

### Architecture Tenets

| Tenet | Implementation |
|---|---|
| Tenant isolation | Row-Level Security (RLS) via Postgres GUC `app.current_tenant` |
| Defense in depth | Application layer + RLS + dependency-level role checks |
| Zero enumeration | Auth failures collapse to a single error code regardless of cause |
| Single-use refresh tokens | Refresh token rotation on every use; old token immediately revoked |
| Audit-first | Every role-escalated or sensitive operation writes to `audit_log` |
| Async I/O | FastAPI + asyncpg; all DB calls are non-blocking |

---

## 2. Authentication Model

### 2.1 Token Types

The API uses two JWT tokens. Both are HS256-signed.

| Property | Access Token | Refresh Token |
|---|---|---|
| Header | `Authorization: Bearer <token>` | Request body `refresh_token` |
| Lifetime | 30 minutes (configurable) | 14 days (configurable) |
| Claims | `sub`, `email`, `tenant_id`, `roles`, `jti`, `iat`, `exp`, `type: "access"` | `sub`, `jti`, `iat`, `exp`, `type: "refresh"` |
| Revocable | Yes — jti added to deny-list on logout | Yes — single-use; revoked on next refresh |
| Contains roles | Yes | No (re-derived from DB on refresh) |

**Access Token Payload Example:**
```json
{
  "sub": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "email": "jane@acme.com",
  "tenant_id": "f9e8d7c6-b5a4-3210-fedc-ba9876543210",
  "roles": ["MANAGER", "HR"],
  "jti": "unique-token-id",
  "iat": 1716900000,
  "exp": 1716901800,
  "type": "access"
}
```

### 2.2 Role System

Roles are scoped to either PLATFORM (users with no tenant) or TENANT (users belonging to a tenant).

**Platform-scope roles** — held by internal operators, no customer data access unless explicitly granted:

| Role Code | Description |
|---|---|
| `SUPER_ADMIN` | Unrestricted access across all platform resources |
| `PLATFORM_ADMIN` | Platform operations — tenant provisioning, platform user management |
| `SUPPORT_ADMIN` | Read-only cross-tenant access for support use cases |

**Tenant-scope roles** — held by customer users, data-access bounded to their tenant:

| Role Code | Description |
|---|---|
| `TENANT_ADMIN` | Full admin of their own tenant |
| `CXO` | C-level executive |
| `CHRO` | Chief Human Resources Officer |
| `CFO` | Chief Financial Officer |
| `HR` | Human Resources generalist |
| `HRBP` | HR Business Partner |
| `C_AND_B` | Compensation & Benefits specialist |
| `MANAGER_OF_MANAGERS` | Manager whose direct reports are themselves managers |
| `MANAGER` | People manager with direct reports |

### 2.3 Login Resolution Chain

The login endpoint resolves the authenticating user through this ordered chain:

```
1. tenant_code provided in body?
   └─ YES → resolve tenant by code → find user in that tenant
   └─ NO  → try platform user lookup (tenant_id IS NULL)
             └─ MISS → extract domain from email → resolve tenant by domain → find user
```

All failures at any step return identical `INVALID_CREDENTIALS` (no enumeration of what was wrong).

### 2.4 Tenant Context Requirement

Tenant-scoped endpoints require the authenticated user to belong to a tenant. Platform-admin users calling tenant-scoped endpoints must use appropriate override dependencies. Calling a tenant-scoped endpoint as a platform user without tenant context returns `400 TENANT_CONTEXT_REQUIRED`.

---

## 3. Request & Response Conventions

### 3.1 Request Headers

| Header | Required | Description |
|---|---|---|
| `Content-Type` | Yes (mutating requests) | Must be `application/json` |
| `Authorization` | Endpoint-dependent | `Bearer <access_token>` |
| `X-Request-ID` | Optional | Client-supplied trace ID; echoed in response headers |

### 3.2 Response Envelope

Every response — success or error — is wrapped in a standard envelope.

**Success:**
```json
{
  "status": "success",
  "message": "Human-readable description",
  "data": { ... }
}
```

**Error:**
```json
{
  "status": "fail",
  "error_code": "MACHINE_READABLE_CODE",
  "message": "Human-readable description",
  "details": { ... }
}
```

### 3.3 Pagination

All list endpoints support offset-based pagination via query parameters.

| Parameter | Type | Default | Max | Description |
|---|---|---|---|---|
| `limit` | integer | `50` | `200` | Number of items per page |
| `offset` | integer | `0` | — | Zero-based starting position |

**Paginated Response Shape:**
```json
{
  "status": "success",
  "message": "...",
  "data": {
    "items": [ ... ],
    "total": 142,
    "limit": 50,
    "offset": 0
  }
}
```

### 3.4 Response Headers

| Header | Description |
|---|---|
| `X-Request-ID` | Unique request identifier (generated or echoed) |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` (production only) |

### 3.5 Data Types

| Type | Format | Example |
|---|---|---|
| UUID | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` | `"a1b2c3d4-e5f6-7890-abcd-ef1234567890"` |
| Timestamp | ISO 8601 UTC | `"2026-05-28T14:32:00Z"` |
| Date | ISO 8601 date | `"2026-06-30"` |
| Monetary | Decimal number | `150000.00` |
| Currency | ISO 4217 3-letter code | `"USD"` |

---

## 4. Rate Limiting

Rate limiting is applied per IP address using a token-bucket algorithm.

| Endpoint | Limit | Window |
|---|---|---|
| `POST /auth/login` | 5 requests | 15 minutes |
| `POST /auth/refresh` | 5 requests | 15 minutes |
| All other endpoints | No global limit (per-endpoint limits may apply) |

**Rate Limit Exceeded Response — `429 Too Many Requests`:**
```json
{
  "status": "fail",
  "error_code": "RATE_LIMITED",
  "message": "Too many requests. Please slow down and try again shortly.",
  "details": {
    "limit": "5/15minutes"
  }
}
```

**Response Headers on Rate-Limited Response:**
```
Retry-After: 847
```

> **Integration note:** Implement exponential backoff starting at 1 second when receiving `429`. Do not retry immediately.

---

## 5. Health & Root Endpoints

### `GET /`

Root welcome endpoint. Returns API identification metadata.

**Authentication:** None  
**Rate Limit:** None

**Response — `200 OK`:**
```json
{
  "message": "Welcome to CompiqCore API",
  "version": "1.0.0"
}
```

---

### `GET /health`

Liveness probe. Returns immediately without touching any downstream dependency. Use this for basic container health checks.

**Authentication:** None  
**Rate Limit:** None

**Response — `200 OK`:**
```json
{
  "status": "healthy"
}
```

---

### `GET /health/db`

Readiness probe. Executes a lightweight `SELECT 1` against the primary database to verify connectivity and connection pool health.

**Authentication:** None  
**Rate Limit:** None

**Response — `200 OK` (database reachable):**
```json
{
  "status": "healthy",
  "database": "connected",
  "result": 1
}
```

**Response — `503 Service Unavailable` (database unreachable):**
```json
{
  "status": "unhealthy",
  "database": "unreachable",
  "error": "Connection refused"
}
```

> **DevOps note:** Configure your load balancer / Kubernetes readiness probe on `GET /health/db`. Use `GET /health` for liveness probes. Do not expose these endpoints through authenticated middleware.

---

## 6. Authentication Endpoints

Base path: `/auth`

---

### 6.1 `POST /auth/login`

Authenticates a user and issues a JWT access/refresh token pair.

**Authentication:** None (public endpoint)  
**Rate Limit:** 5 requests per 15 minutes per IP

#### Business Logic

1. Resolves the user account using the [login resolution chain](#23-login-resolution-chain).
2. Verifies the password against the stored bcrypt hash.
3. Checks that the user is active (`is_active = true`).
4. If a `tenant_code` is provided, validates the resolved tenant is `ACTIVE`.
5. Issues a new access token and a fresh single-use refresh token.
6. Records a `LOGIN` audit log entry with IP, user agent, and token `jti`.

#### Request

**Headers:**
```
Content-Type: application/json
```

**Body:**

| Field | Type | Required | Constraints | Description |
|---|---|---|---|---|
| `email` | string | Yes | Valid email format | User's email address |
| `password` | string | Yes | 8–128 characters | User's password (plaintext; TLS-encrypted in transit) |
| `tenant_code` | string | No | Lowercase alphanumeric slug | If provided, scopes login to a specific tenant. Useful when same email exists in multiple tenants. |

```json
{
  "email": "jane.smith@acme.com",
  "password": "SecureP@ssw0rd",
  "tenant_code": "acme"
}
```

#### Response — `200 OK`

```json
{
  "status": "success",
  "message": "Login successful",
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "token_type": "bearer",
    "expires_in": 1800
  }
}
```

| Field | Type | Description |
|---|---|---|
| `access_token` | string | JWT access token. Include in `Authorization: Bearer` header for all authenticated requests. |
| `refresh_token` | string | Single-use JWT refresh token. Store securely (HttpOnly cookie recommended). |
| `token_type` | string | Always `"bearer"` |
| `expires_in` | integer | Access token lifetime in seconds (default: 1800) |

#### Error Responses

| HTTP Status | `error_code` | Cause | Resolution |
|---|---|---|---|
| `401` | `INVALID_CREDENTIALS` | Wrong email, wrong password, user not found, or inactive tenant | Verify credentials; do not enumerate |
| `403` | `ACCOUNT_INACTIVE` | User account is disabled | Contact tenant admin |
| `422` | `VALIDATION_ERROR` | Malformed request body (missing fields, invalid email format) | Fix request per `details.errors` |
| `429` | `RATE_LIMITED` | Exceeded 5 login attempts in 15 minutes | Wait and retry with backoff |

**Example — `401 INVALID_CREDENTIALS`:**
```json
{
  "status": "fail",
  "error_code": "INVALID_CREDENTIALS",
  "message": "Invalid email or password.",
  "details": null
}
```

**Example — `422 VALIDATION_ERROR`:**
```json
{
  "status": "fail",
  "error_code": "VALIDATION_ERROR",
  "message": "Request validation failed.",
  "details": {
    "errors": [
      {
        "loc": ["body", "email"],
        "msg": "value is not a valid email address",
        "type": "value_error"
      }
    ]
  }
}
```

#### cURL Example

```bash
curl -X POST https://api.compiq.ai/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "jane.smith@acme.com",
    "password": "SecureP@ssw0rd",
    "tenant_code": "acme"
  }'
```

---

### 6.2 `POST /auth/refresh`

Exchanges a valid refresh token for a new access/refresh token pair. The submitted refresh token is immediately revoked (token rotation). This is a security-critical endpoint — each refresh token can be used exactly once.

**Authentication:** None (uses `refresh_token` in body)  
**Rate Limit:** 5 requests per 15 minutes per IP

#### Business Logic

1. Validates the refresh token (signature, expiry, `type: "refresh"` claim).
2. Checks the token `jti` is not in the deny-list.
3. Re-derives user roles and tenant from the database (fresh read, not from token claims).
4. Revokes the submitted refresh token (adds its `jti` to the deny-list).
5. Issues a new access token and a new single-use refresh token.

> **Security note:** Token rotation means that if a refresh token is stolen and used, the legitimate user's next refresh will fail (the stolen-token use revoked the original). Implement `401` on refresh as a session-invalidation signal.

#### Request

**Headers:**
```
Content-Type: application/json
```

**Body:**

| Field | Type | Required | Description |
|---|---|---|---|
| `refresh_token` | string | Yes | The refresh token received from `/auth/login` or the previous `/auth/refresh` call |

```json
{
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

#### Response — `200 OK`

```json
{
  "status": "success",
  "message": "Token refreshed",
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "token_type": "bearer",
    "expires_in": 1800
  }
}
```

#### Error Responses

| HTTP Status | `error_code` | Cause | Resolution |
|---|---|---|---|
| `401` | `INVALID_TOKEN` | Refresh token is expired, malformed, wrong type, or already revoked | Force re-login |
| `401` | `UNAUTHENTICATED` | Refresh token `jti` found in deny-list (already used or explicitly revoked) | Force re-login |
| `403` | `ACCOUNT_INACTIVE` | User was deactivated since token was issued | Contact administrator |
| `422` | `VALIDATION_ERROR` | Missing `refresh_token` field | Fix request body |
| `429` | `RATE_LIMITED` | Exceeded 5 refresh attempts in 15 minutes | Backoff and retry |

#### cURL Example

```bash
curl -X POST https://api.compiq.ai/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{
    "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
  }'
```

---

### 6.3 `POST /auth/logout`

Revokes the current access token by adding its `jti` to the deny-list. All subsequent requests using this token will receive `401`. The associated refresh token (if known) should be discarded by the client.

**Authentication:** `Authorization: Bearer <access_token>` (required)  
**Rate Limit:** None

#### Request

No request body. The access token in the `Authorization` header is the token being revoked.

**Headers:**
```
Authorization: Bearer <access_token>
```

#### Response — `200 OK`

```json
{
  "status": "success",
  "message": "Logged out successfully",
  "data": {
    "revoked": true
  }
}
```

#### Error Responses

| HTTP Status | `error_code` | Cause | Resolution |
|---|---|---|---|
| `401` | `UNAUTHENTICATED` | Missing, malformed, or already-expired access token | Token already invalid; treat as logged out |

> **Integration note:** Even if `/auth/logout` returns `401`, the client should clear its local token storage and redirect to login. The endpoint is idempotent from the user's perspective.

#### cURL Example

```bash
curl -X POST https://api.compiq.ai/auth/logout \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

---

### 6.4 `GET /auth/me`

Returns the complete profile of the currently authenticated user including their tenant association and all active role codes. Use this on app boot to hydrate client-side session state.

**Authentication:** `Authorization: Bearer <access_token>` (required)  
**Rate Limit:** None

#### Request

No request body.

**Headers:**
```
Authorization: Bearer <access_token>
```

#### Response — `200 OK`

```json
{
  "status": "success",
  "message": "Current user profile",
  "data": {
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "email": "jane.smith@acme.com",
    "first_name": "Jane",
    "last_name": "Smith",
    "is_active": true,
    "tenant_id": "f9e8d7c6-b5a4-3210-fedc-ba9876543210",
    "tenant": {
      "id": "f9e8d7c6-b5a4-3210-fedc-ba9876543210",
      "code": "acme",
      "name": "Acme Corp",
      "domain": "acme.com",
      "status": "ACTIVE"
    },
    "roles": ["MANAGER", "HR"],
    "created_at": "2025-01-15T09:00:00Z"
  }
}
```

| Field | Type | Description |
|---|---|---|
| `id` | UUID | User's unique identifier |
| `email` | string | Normalized (lowercase) email address |
| `first_name` | string | User's first name |
| `last_name` | string \| null | User's last name (optional) |
| `is_active` | boolean | Whether the user account is active |
| `tenant_id` | UUID \| null | The user's tenant. `null` for platform-level users |
| `tenant` | object \| null | Full tenant details. `null` for platform-level users |
| `roles` | string[] | Array of role codes active for this user |
| `created_at` | ISO 8601 | Account creation timestamp |

#### Error Responses

| HTTP Status | `error_code` | Cause | Resolution |
|---|---|---|---|
| `401` | `UNAUTHENTICATED` | Missing, expired, or revoked access token | Re-authenticate |
| `403` | `ACCOUNT_INACTIVE` | User account has been deactivated after token was issued | Re-authenticate; contact admin |

#### cURL Example

```bash
curl -X GET https://api.compiq.ai/auth/me \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

---

## 7. Admin — Platform Users

These endpoints provision platform-level and tenant-level user accounts. They require elevated privileges and are not exposed to regular tenant users.

---

### 7.1 `POST /admin/users`

Creates a new platform-level user (no tenant association). Platform users can hold only `PLATFORM`-scope roles: `SUPER_ADMIN`, `PLATFORM_ADMIN`, or `SUPPORT_ADMIN`.

**Authentication:** Required  
**Required Roles:** `SUPER_ADMIN` or `PLATFORM_ADMIN`  
**Rate Limit:** None

#### Business Logic

1. Validates all `role_codes` belong to the PLATFORM scope. Tenant-scope role codes in this request cause a `400` error.
2. Checks the email does not already exist as a platform user.
3. Hashes the provided password with bcrypt.
4. Creates the user and assigns all specified roles atomically.
5. Writes a `USER_CREATED` audit log entry.

#### Request

**Headers:**
```
Content-Type: application/json
Authorization: Bearer <platform_admin_token>
```

**Body:**

| Field | Type | Required | Constraints | Description |
|---|---|---|---|---|
| `email` | string | Yes | Valid email format | Platform user's email. Must be globally unique among platform users. |
| `password` | string | Yes | 8–128 characters | Initial password. User should change on first login. |
| `first_name` | string | Yes | 1–100 characters | User's first name |
| `last_name` | string | No | 1–100 characters | User's last name |
| `role_codes` | string[] | Yes | Non-empty array; must be PLATFORM-scope roles | Roles to assign: `SUPER_ADMIN`, `PLATFORM_ADMIN`, `SUPPORT_ADMIN` |

```json
{
  "email": "ops-admin@compiq.ai",
  "password": "TemporaryP@ss123",
  "first_name": "Operations",
  "last_name": "Admin",
  "role_codes": ["PLATFORM_ADMIN"]
}
```

#### Response — `201 Created`

```json
{
  "status": "success",
  "message": "Platform user created",
  "data": {
    "id": "b2c3d4e5-f6a7-8901-bcde-f23456789012",
    "email": "ops-admin@compiq.ai",
    "first_name": "Operations",
    "last_name": "Admin",
    "is_active": true,
    "tenant_id": null,
    "roles": ["PLATFORM_ADMIN"],
    "created_at": "2026-05-28T12:00:00Z"
  }
}
```

#### Error Responses

| HTTP Status | `error_code` | Cause | Resolution |
|---|---|---|---|
| `400` | `INVALID_ROLE_SCOPE` | One or more `role_codes` are TENANT-scope roles | Use only `SUPER_ADMIN`, `PLATFORM_ADMIN`, `SUPPORT_ADMIN` |
| `400` | `EMAIL_ALREADY_EXISTS` | Email already registered as a platform user | Use a different email |
| `401` | `UNAUTHENTICATED` | Missing or invalid token | Authenticate first |
| `403` | `FORBIDDEN` | Caller does not have `SUPER_ADMIN` or `PLATFORM_ADMIN` role | Use appropriate credentials |
| `422` | `VALIDATION_ERROR` | Malformed request body | Fix per `details.errors` |

#### cURL Example

```bash
curl -X POST https://api.compiq.ai/admin/users \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <platform_admin_token>" \
  -d '{
    "email": "ops-admin@compiq.ai",
    "password": "TemporaryP@ss123",
    "first_name": "Operations",
    "last_name": "Admin",
    "role_codes": ["PLATFORM_ADMIN"]
  }'
```

---

### 7.2 `POST /admin/tenants/{tenant_id}/users`

Creates a new user inside a specific tenant. This user will be bound to that tenant and can hold only `TENANT`-scope roles.

**Authentication:** Required  
**Required Roles:** `TENANT_ADMIN` of the target tenant, OR `PLATFORM_ADMIN` / `SUPER_ADMIN`  
**Rate Limit:** None

#### Request

**Path Parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `tenant_id` | UUID | Yes | The tenant to create the user in |

**Headers:**
```
Content-Type: application/json
Authorization: Bearer <admin_token>
```

**Body:**

| Field | Type | Required | Constraints | Description |
|---|---|---|---|---|
| `email` | string | Yes | Valid email | Email must be unique within this tenant (same email may exist in another tenant) |
| `password` | string | Yes | 8–128 characters | Initial password |
| `first_name` | string | Yes | 1–100 characters | User's first name |
| `last_name` | string | No | 1–100 characters | User's last name |
| `role_codes` | string[] | Yes | Non-empty array; must be TENANT-scope roles | Roles to assign. See [Role System](#22-role-system) for valid values. |

```json
{
  "email": "sarah.jones@acme.com",
  "password": "Welcome@2026!",
  "first_name": "Sarah",
  "last_name": "Jones",
  "role_codes": ["MANAGER", "HR"]
}
```

#### Response — `201 Created`

```json
{
  "status": "success",
  "message": "Tenant user created",
  "data": {
    "id": "c3d4e5f6-a7b8-9012-cdef-345678901234",
    "email": "sarah.jones@acme.com",
    "first_name": "Sarah",
    "last_name": "Jones",
    "is_active": true,
    "tenant_id": "f9e8d7c6-b5a4-3210-fedc-ba9876543210",
    "roles": ["MANAGER", "HR"],
    "created_at": "2026-05-28T12:00:00Z"
  }
}
```

#### Error Responses

| HTTP Status | `error_code` | Cause | Resolution |
|---|---|---|---|
| `400` | `INVALID_ROLE_SCOPE` | One or more `role_codes` are PLATFORM-scope roles | Use only TENANT-scope role codes |
| `400` | `EMAIL_ALREADY_EXISTS` | Email already registered within this tenant | Use a different email |
| `401` | `UNAUTHENTICATED` | Invalid or missing token | Authenticate first |
| `403` | `FORBIDDEN` | Caller is not admin of this tenant | Use `TENANT_ADMIN` or platform admin credentials |
| `404` | `NOT_FOUND` | `tenant_id` does not exist | Verify the tenant ID |
| `422` | `VALIDATION_ERROR` | Malformed request body | Fix per `details.errors` |

---

## 8. Admin — Tenant Management

These endpoints manage the lifecycle of tenant organizations on the platform.

---

### 8.1 `POST /admin/tenants`

Provisions a new tenant and creates its first `TENANT_ADMIN` user in a single atomic transaction. This is the primary onboarding endpoint for new customers.

**Authentication:** Required  
**Required Roles:** `SUPER_ADMIN` or `PLATFORM_ADMIN`  
**Rate Limit:** None

#### Business Logic

1. Validates tenant `code` and `domain` are globally unique.
2. Validates `domain` is a valid multi-label DNS domain.
3. Creates the tenant record with status `ACTIVE`.
4. Creates the initial admin user with the `TENANT_ADMIN` role within the new tenant.
5. All operations are atomic — if admin user creation fails, the tenant is not persisted.
6. Writes `TENANT_CREATED` and `USER_CREATED` audit entries.

#### Request

**Body:**

| Field | Type | Required | Constraints | Description |
|---|---|---|---|---|
| `name` | string | Yes | 1–255 characters | Display name of the organization |
| `code` | string | Yes | 1–64 chars; lowercase alphanumeric, `-`, `_`; must start with letter/digit; globally unique | Immutable slug identifier used in login resolution |
| `domain` | string | Yes | Valid multi-label DNS domain (e.g., `acme.com`); globally unique | Email domain for auto-resolving users to this tenant on login |
| `initial_admin.email` | string | Yes | Valid email | Email for the first TENANT_ADMIN user |
| `initial_admin.password` | string | Yes | 8–128 characters | Initial password for the tenant admin |
| `initial_admin.first_name` | string | Yes | 1–100 characters | First name |
| `initial_admin.last_name` | string | No | 1–100 characters | Last name |

```json
{
  "name": "Acme Corporation",
  "code": "acme",
  "domain": "acme.com",
  "initial_admin": {
    "email": "admin@acme.com",
    "password": "SecureAdmin@2026",
    "first_name": "John",
    "last_name": "Doe"
  }
}
```

#### Response — `201 Created`

```json
{
  "status": "success",
  "message": "Tenant created",
  "data": {
    "tenant": {
      "id": "f9e8d7c6-b5a4-3210-fedc-ba9876543210",
      "code": "acme",
      "name": "Acme Corporation",
      "domain": "acme.com",
      "status": "ACTIVE",
      "created_at": "2026-05-28T12:00:00Z",
      "updated_at": "2026-05-28T12:00:00Z"
    },
    "admin": {
      "id": "d4e5f6a7-b8c9-0123-defa-456789012345",
      "email": "admin@acme.com",
      "first_name": "John",
      "last_name": "Doe",
      "is_active": true,
      "tenant_id": "f9e8d7c6-b5a4-3210-fedc-ba9876543210",
      "roles": ["TENANT_ADMIN"],
      "created_at": "2026-05-28T12:00:00Z"
    }
  }
}
```

#### Error Responses

| HTTP Status | `error_code` | Cause | Resolution |
|---|---|---|---|
| `400` | `TENANT_CODE_TAKEN` | `code` already exists | Choose a different code |
| `400` | `DOMAIN_ALREADY_REGISTERED` | `domain` already belongs to another tenant | Use a different domain |
| `400` | `INVALID_DOMAIN` | `domain` fails DNS validation (e.g., no TLD, IP address) | Provide a valid domain like `company.com` |
| `401` | `UNAUTHENTICATED` | Invalid or missing token | Authenticate first |
| `403` | `FORBIDDEN` | Caller lacks required roles | Use `SUPER_ADMIN` or `PLATFORM_ADMIN` credentials |
| `422` | `VALIDATION_ERROR` | Malformed body | Fix per `details.errors` |

#### cURL Example

```bash
curl -X POST https://api.compiq.ai/admin/tenants \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <platform_admin_token>" \
  -d '{
    "name": "Acme Corporation",
    "code": "acme",
    "domain": "acme.com",
    "initial_admin": {
      "email": "admin@acme.com",
      "password": "SecureAdmin@2026",
      "first_name": "John",
      "last_name": "Doe"
    }
  }'
```

---

### 8.2 `GET /admin/tenants`

Returns a paginated list of all tenants on the platform, with optional status filtering.

**Authentication:** Required  
**Required Roles:** `SUPER_ADMIN`, `PLATFORM_ADMIN`, or `SUPPORT_ADMIN`  
**Rate Limit:** None

#### Request

**Query Parameters:**

| Parameter | Type | Required | Default | Allowed Values | Description |
|---|---|---|---|---|---|
| `status` | string | No | (all) | `ACTIVE`, `SUSPENDED`, `DISABLED` | Filter tenants by status |
| `limit` | integer | No | `50` | 1–200 | Items per page |
| `offset` | integer | No | `0` | ≥ 0 | Pagination offset |

#### Response — `200 OK`

```json
{
  "status": "success",
  "message": "Tenants retrieved",
  "data": {
    "items": [
      {
        "id": "f9e8d7c6-b5a4-3210-fedc-ba9876543210",
        "code": "acme",
        "name": "Acme Corporation",
        "domain": "acme.com",
        "status": "ACTIVE",
        "created_at": "2025-01-15T09:00:00Z",
        "updated_at": "2026-05-28T12:00:00Z"
      }
    ],
    "total": 47,
    "limit": 50,
    "offset": 0
  }
}
```

**TenantResponse fields:**

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Tenant's unique identifier |
| `code` | string | Immutable slug |
| `name` | string | Organization display name |
| `domain` | string | Email domain for login resolution |
| `status` | string | `ACTIVE` \| `SUSPENDED` \| `DISABLED` |
| `created_at` | ISO 8601 | Tenant provisioning timestamp |
| `updated_at` | ISO 8601 | Last update timestamp |

---

### 8.3 `GET /admin/tenants/{tenant_id}`

Retrieves full details for a single tenant.

**Authentication:** Required  
**Required Roles:** `SUPER_ADMIN`, `PLATFORM_ADMIN`, or `SUPPORT_ADMIN`

#### Path Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `tenant_id` | UUID | Yes | Target tenant's ID |

#### Response — `200 OK`

```json
{
  "status": "success",
  "message": "Tenant retrieved",
  "data": {
    "id": "f9e8d7c6-b5a4-3210-fedc-ba9876543210",
    "code": "acme",
    "name": "Acme Corporation",
    "domain": "acme.com",
    "status": "ACTIVE",
    "created_at": "2025-01-15T09:00:00Z",
    "updated_at": "2026-05-28T12:00:00Z"
  }
}
```

#### Error Responses

| HTTP Status | `error_code` | Cause | Resolution |
|---|---|---|---|
| `404` | `NOT_FOUND` | No tenant found with the given ID | Verify the UUID |
| `403` | `FORBIDDEN` | Caller lacks required roles | Use platform admin credentials |

---

### 8.4 `PATCH /admin/tenants/{tenant_id}`

Partially updates a tenant's mutable attributes. The `code` field is immutable and cannot be changed after creation.

**Authentication:** Required  
**Required Roles:** `SUPER_ADMIN` or `PLATFORM_ADMIN`

#### Path Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `tenant_id` | UUID | Yes | Target tenant's ID |

#### Request Body (all fields optional)

| Field | Type | Constraints | Description |
|---|---|---|---|
| `name` | string | 1–255 characters | New display name for the organization |
| `domain` | string | Valid DNS domain; globally unique | New email domain |
| `status` | string | `ACTIVE` \| `SUSPENDED` \| `DISABLED` | New lifecycle status |

```json
{
  "name": "Acme Corp (Rebranded)",
  "status": "SUSPENDED"
}
```

> **Warning:** Setting status to `SUSPENDED` or `DISABLED` will prevent all users of this tenant from authenticating. Active sessions retain their existing access tokens until expiry.

#### Response — `200 OK`

Returns the updated `TenantResponse` (same schema as `GET /admin/tenants/{tenant_id}`).

#### Error Responses

| HTTP Status | `error_code` | Cause | Resolution |
|---|---|---|---|
| `400` | `DOMAIN_ALREADY_REGISTERED` | New domain already assigned to another tenant | Use a unique domain |
| `404` | `NOT_FOUND` | Tenant not found | Verify the UUID |
| `403` | `FORBIDDEN` | Caller lacks required roles | Use platform admin credentials |
| `422` | `VALIDATION_ERROR` | Invalid `status` value or domain format | Fix per `details.errors` |

---

## 9. Departments

Departments model the organizational hierarchy within a tenant. They are tenant-scoped and isolated by Row-Level Security.

**Prefix:** `/departments`  
**Tenant Context:** Required (user must belong to a tenant)  
**Base Auth:** `Authorization: Bearer <tenant_user_token>`

---

### 9.1 `GET /departments`

Returns a paginated list of all departments within the authenticated user's tenant.

**Required Roles:** Any authenticated tenant member

#### Query Parameters

| Parameter | Type | Default | Max | Description |
|---|---|---|---|---|
| `limit` | integer | `50` | `200` | Items per page |
| `offset` | integer | `0` | — | Pagination offset |

#### Response — `200 OK`

```json
{
  "status": "success",
  "message": "Departments retrieved",
  "data": {
    "items": [
      {
        "id": "e5f6a7b8-c9d0-1234-efab-567890123456",
        "tenant_id": "f9e8d7c6-b5a4-3210-fedc-ba9876543210",
        "code": "ENG",
        "name": "Engineering",
        "description": "Software engineering and platform teams",
        "created_at": "2025-03-01T10:00:00Z",
        "updated_at": "2026-01-15T08:30:00Z"
      },
      {
        "id": "f6a7b8c9-d0e1-2345-fabc-678901234567",
        "tenant_id": "f9e8d7c6-b5a4-3210-fedc-ba9876543210",
        "code": "HR",
        "name": "Human Resources",
        "description": null,
        "created_at": "2025-03-01T10:00:00Z",
        "updated_at": "2025-03-01T10:00:00Z"
      }
    ],
    "total": 8,
    "limit": 50,
    "offset": 0
  }
}
```

**DepartmentResponse fields:**

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Department unique identifier |
| `tenant_id` | UUID | Owning tenant's ID |
| `code` | string | Unique uppercase code within the tenant (e.g., `ENG`, `SALES`) |
| `name` | string | Human-readable department name |
| `description` | string \| null | Optional description |
| `created_at` | ISO 8601 | Creation timestamp |
| `updated_at` | ISO 8601 | Last update timestamp |

---

### 9.2 `GET /departments/{department_id}`

Retrieves a single department by ID.

**Required Roles:** Any authenticated tenant member

#### Path Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `department_id` | UUID | Yes | Department's ID (must belong to caller's tenant) |

#### Response — `200 OK`

Returns a single `DepartmentResponse` (same schema as items in the list endpoint).

#### Error Responses

| HTTP Status | `error_code` | Cause | Resolution |
|---|---|---|---|
| `404` | `NOT_FOUND` | Department not found or belongs to a different tenant | Verify the UUID |

---

### 9.3 `POST /departments`

Creates a new department within the authenticated user's tenant.

**Required Roles:** `TENANT_ADMIN` or `HR`

#### Request Body

| Field | Type | Required | Constraints | Description |
|---|---|---|---|---|
| `code` | string | Yes | 1–64 chars; uppercase alphanumeric, `_`, `-`; must start with letter/digit; unique within tenant | Short uppercase identifier |
| `name` | string | Yes | 1–255 characters | Display name |
| `description` | string | No | 0–1000 characters | Optional description |

```json
{
  "code": "DESIGN",
  "name": "Product Design",
  "description": "UX, product design, and visual brand teams"
}
```

#### Response — `201 Created`

```json
{
  "status": "success",
  "message": "Department created",
  "data": {
    "id": "a7b8c9d0-e1f2-3456-abcd-789012345678",
    "tenant_id": "f9e8d7c6-b5a4-3210-fedc-ba9876543210",
    "code": "DESIGN",
    "name": "Product Design",
    "description": "UX, product design, and visual brand teams",
    "created_at": "2026-05-28T14:00:00Z",
    "updated_at": "2026-05-28T14:00:00Z"
  }
}
```

#### Error Responses

| HTTP Status | `error_code` | Cause | Resolution |
|---|---|---|---|
| `400` | `DEPARTMENT_CODE_TAKEN` | `code` already exists in this tenant | Choose a different code |
| `403` | `FORBIDDEN` | Caller does not have `TENANT_ADMIN` or `HR` role | Use appropriate credentials |
| `422` | `VALIDATION_ERROR` | Invalid `code` format, name too long, etc. | Fix per `details.errors` |

#### cURL Example

```bash
curl -X POST https://api.compiq.ai/departments \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <tenant_user_token>" \
  -d '{
    "code": "DESIGN",
    "name": "Product Design",
    "description": "UX, product design, and visual brand teams"
  }'
```

---

### 9.4 `PATCH /departments/{department_id}`

Partially updates a department. The `code` field is immutable after creation.

**Required Roles:** `TENANT_ADMIN` or `HR`

#### Path Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `department_id` | UUID | Yes | Department to update |

#### Request Body (all fields optional)

| Field | Type | Constraints | Description |
|---|---|---|---|
| `name` | string | 1–255 characters | New display name |
| `description` | string | 0–1000 characters | New description; pass `""` to clear |

```json
{
  "name": "Product Design & Research"
}
```

#### Response — `200 OK`

Returns the updated `DepartmentResponse`.

#### Error Responses

| HTTP Status | `error_code` | Cause | Resolution |
|---|---|---|---|
| `404` | `NOT_FOUND` | Department not found | Verify the UUID |
| `403` | `FORBIDDEN` | Insufficient roles | Use `TENANT_ADMIN` or `HR` credentials |

---

### 9.5 `DELETE /departments/{department_id}`

Permanently deletes a department. This operation is irreversible.

**Required Roles:** `TENANT_ADMIN` or `HR`

> **Warning:** Deleting a department that has associated users or compensation data may fail with a foreign key constraint error or leave orphaned references depending on cascade configuration. Verify all associations are removed before deleting.

#### Path Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `department_id` | UUID | Yes | Department to delete |

#### Response — `204 No Content`

Empty body. Department successfully deleted.

#### Error Responses

| HTTP Status | `error_code` | Cause | Resolution |
|---|---|---|---|
| `404` | `NOT_FOUND` | Department not found | Verify the UUID |
| `403` | `FORBIDDEN` | Insufficient roles | Use `TENANT_ADMIN` or `HR` credentials |
| `409` | `CONFLICT` | Department has dependent records | Remove or reassign dependents first |

---

## 10. Compensation Cycles

A compensation cycle (`comp-cycle`) represents a bounded planning period (e.g., FY2026 annual review). The cycle drives the entire JVRE workspace — budget allocations, pay recommendations, and JVRE snapshots are all scoped to a cycle.

**Prefix:** `/comp-cycles`  
**Tenant Context:** Required  
**Base Auth:** `Authorization: Bearer <tenant_user_token>`

### Cycle Lifecycle

```
DRAFT → ACTIVE → CLOSED
```

| Status | Description |
|---|---|
| `DRAFT` | Cycle under configuration; not yet visible to managers |
| `ACTIVE` | Planning is open; managers can submit recommendations |
| `CLOSED` | Planning complete; records are read-only |

---

### 10.1 `GET /comp-cycles/active`

Returns the currently active compensation cycle for the caller's tenant. Exactly one cycle can be `ACTIVE` at a time.

**Required Roles:** Any authenticated tenant member

#### Response — `200 OK`

```json
{
  "status": "success",
  "message": "Active compensation cycle",
  "data": {
    "id": "b8c9d0e1-f2a3-4567-bcde-890123456789",
    "fy_label": "FY2026",
    "status": "ACTIVE",
    "submission_deadline": "2026-07-31",
    "currency_code": "USD",
    "jvre_alignment_tolerance": 0.005,
    "cycle_started_at": "2026-05-01T00:00:00Z"
  }
}
```

**CycleResponse fields:**

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Cycle unique identifier |
| `fy_label` | string | Fiscal year label (e.g., `"FY2026"`) |
| `status` | string | `DRAFT` \| `ACTIVE` \| `CLOSED` |
| `submission_deadline` | date \| null | Last date managers can submit recommendations |
| `currency_code` | string | ISO 4217 currency for this cycle (e.g., `"USD"`) |
| `jvre_alignment_tolerance` | float | Tolerance band for JVRE alignment checks (e.g., `0.005` = ±0.5%) |
| `cycle_started_at` | ISO 8601 \| null | Timestamp when cycle moved to `ACTIVE` |

#### Error Responses

| HTTP Status | `error_code` | Cause | Resolution |
|---|---|---|---|
| `404` | `NOT_FOUND` | No active cycle exists for this tenant | Contact tenant admin to activate a cycle |

---

### 10.2 `GET /comp-cycles/{cycle_id}`

Retrieves a compensation cycle by its ID (works for any status: DRAFT, ACTIVE, or CLOSED).

**Required Roles:** Any authenticated tenant member

#### Path Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `cycle_id` | UUID | Yes | Target cycle's ID |

#### Response — `200 OK`

Same schema as `GET /comp-cycles/active`.

---

### 10.3 `GET /comp-cycles/{cycle_id}/my-budget-allocation`

Returns the caller's budget allocation for a given cycle. A budget allocation represents the total pool the caller is responsible for distributing to their direct reports.

**Required Roles:** Any authenticated tenant member (managers, HR, finance)

#### Path Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `cycle_id` | UUID | Yes | Target cycle's ID |

#### Response — `200 OK`

```json
{
  "status": "success",
  "message": "Budget allocation retrieved",
  "data": {
    "id": "c9d0e1f2-a3b4-5678-cdef-901234567890",
    "cycle_id": "b8c9d0e1-f2a3-4567-bcde-890123456789",
    "owner_user_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "status": "DRAFT",
    "total_allocated": 1250000.00,
    "strategic_reserve": 150000.00,
    "distributable_pool": 1100000.00,
    "currency_code": "USD",
    "submitted_at": null
  }
}
```

**MyBudgetAllocationResponse fields:**

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Allocation ID |
| `cycle_id` | UUID | Associated compensation cycle |
| `owner_user_id` | UUID | The user who owns this allocation (the manager/HR) |
| `status` | string | `DRAFT` \| `SUBMITTED` \| `APPROVED` |
| `total_allocated` | decimal | Total budget allocated to this manager |
| `strategic_reserve` | decimal | Amount held in reserve (not distributed to line items) |
| `distributable_pool` | decimal | `total_allocated - strategic_reserve` |
| `currency_code` | string | ISO 4217 currency |
| `submitted_at` | ISO 8601 \| null | Timestamp of last submission |

---

### 10.4 `GET /comp-cycles/{cycle_id}/my-recommendations`

Returns all pay recommendations the caller has authored for a given cycle (i.e., recommendations for their direct reports).

**Required Roles:** Any authenticated tenant member

#### Path Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `cycle_id` | UUID | Yes | Target cycle's ID |

#### Response — `200 OK`

```json
{
  "status": "success",
  "message": "My recommendations retrieved",
  "data": {
    "items": [
      {
        "recommendation_id": "d0e1f2a3-b4c5-6789-defa-012345678901",
        "subject_user_id": "e1f2a3b4-c5d6-7890-efab-123456789012",
        "subject_name": "Alice Kim",
        "subject_department": "Engineering",
        "status": "SUBMITTED",
        "recommended_base": 190000.00,
        "recommended_variable": 35000.00,
        "recommended_lti_fmv": 30000.00,
        "currency_code": "USD",
        "jvre_aligned": true,
        "last_saved_at": "2026-06-15T10:30:00Z"
      }
    ],
    "total": 6
  }
}
```

---

## 11. Budget Allocations

Budget allocations represent a manager's or HR user's slice of the total compensation budget. The allocation is broken into line items — one per direct report. The UI calls this the "Left Panel / Budget Planner."

---

### 11.1 `GET /budget-allocations/{allocation_id}/lines`

Returns all budget allocation line items for a given allocation. Each line item represents the budget designated for one member of the allocation owner's team.

**Required Roles:** Allocation owner (caller must own the allocation)

#### Path Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `allocation_id` | UUID | Yes | Budget allocation ID |

#### Response — `200 OK`

```json
{
  "status": "success",
  "message": "Allocation lines retrieved",
  "data": {
    "items": [
      {
        "id": "f2a3b4c5-d6e7-8901-fabc-234567890123",
        "allocation_id": "c9d0e1f2-a3b4-5678-cdef-901234567890",
        "recipient_user_id": "e1f2a3b4-c5d6-7890-efab-123456789012",
        "recipient_name": "Alice Kim",
        "recipient_department": "Engineering",
        "recipient_team_size": null,
        "allocated_amount": 255000.00,
        "base_pool": 190000.00,
        "variable_pool": 35000.00,
        "lti_grant_fmv_pool": 25000.00,
        "reserve_pool": 5000.00,
        "jvre_rec_amount": 250000.00,
        "currency_code": "USD",
        "notes": "Strong performer, market adjustment needed",
        "criticality": "HIGH",
        "market_position": "BELOW",
        "promotion_readiness": "READY",
        "risk_callout_text": "Flight risk if not addressed",
        "ai_suggestion_text": "Consider 8% base increase aligned with P75 market data"
      }
    ],
    "total": 6
  }
}
```

**BudgetAllocationLineResponse fields:**

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Line item ID |
| `allocation_id` | UUID | Parent allocation ID |
| `recipient_user_id` | UUID | Employee this line is for |
| `recipient_name` | string | Employee's display name |
| `recipient_department` | string \| null | Employee's department |
| `recipient_team_size` | integer \| null | Size of recipient's team (if they are also a manager) |
| `allocated_amount` | decimal | Total amount allocated to this employee |
| `base_pool` | decimal | Base salary component of the allocation |
| `variable_pool` | decimal | Variable/bonus component |
| `lti_grant_fmv_pool` | decimal | Long-term incentive grant value (fair market value) |
| `reserve_pool` | decimal | Reserve amount for this line |
| `jvre_rec_amount` | decimal | JVRE engine's recommended total amount |
| `currency_code` | string | ISO 4217 currency |
| `notes` | string \| null | Manager's free-text notes for this employee |
| `criticality` | string \| null | `HIGH` \| `MEDIUM` \| `LOW` — talent criticality assessment |
| `market_position` | string \| null | `BELOW` \| `AT` \| `ABOVE` — position relative to market |
| `promotion_readiness` | string \| null | `READY` \| `DEVELOPING` |
| `risk_callout_text` | string \| null | AI-generated risk narrative |
| `ai_suggestion_text` | string \| null | AI-generated action suggestion |

---

### 11.2 `GET /budget-allocations/{allocation_id}/team-risk-snapshot`

Returns an aggregated risk snapshot of the allocation owner's team. Used for the risk summary panel in the budget planner UI.

**Required Roles:** Allocation owner

#### Response — `200 OK`

```json
{
  "status": "success",
  "message": "Team risk snapshot",
  "data": {
    "allocation_id": "c9d0e1f2-a3b4-5678-cdef-901234567890",
    "total_headcount": 6,
    "high_criticality_count": 2,
    "medium_criticality_count": 3,
    "low_criticality_count": 1,
    "below_market_count": 3,
    "at_market_count": 2,
    "above_market_count": 1,
    "promotion_ready_count": 2,
    "total_jvre_recommendation": 1450000.00,
    "total_currently_allocated": 1350000.00,
    "alignment_gap": 100000.00,
    "currency_code": "USD"
  }
}
```

---

### 11.3 `PUT /comp-cycles/{cycle_id}/my-budget-allocation`

Updates the caller's top-level budget allocation for a cycle. Primarily used to set the strategic reserve amount.

**Required Roles:** Any authenticated tenant member (caller must own the allocation)

#### Path Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `cycle_id` | UUID | Yes | Compensation cycle ID |

#### Request Body

| Field | Type | Required | Constraints | Description |
|---|---|---|---|---|
| `strategic_reserve` | decimal | Yes | ≥ 0; ≤ `total_allocated` | Amount to hold in reserve before distribution |

```json
{
  "strategic_reserve": 150000.00
}
```

#### Response — `200 OK`

Returns the updated `MyBudgetAllocationResponse` (same schema as [10.3](#103-get-comp-cyclescycle_idmy-budget-allocation)).

#### Error Responses

| HTTP Status | `error_code` | Cause | Resolution |
|---|---|---|---|
| `400` | `RESERVE_EXCEEDS_TOTAL` | `strategic_reserve` > `total_allocated` | Reduce reserve amount |
| `400` | `CYCLE_NOT_ACTIVE` | Cycle is not in `ACTIVE` status | Confirm cycle is active |
| `404` | `NOT_FOUND` | No allocation found for caller in this cycle | Verify cycle ID |

---

### 11.4 `POST /budget-allocations/{allocation_id}/align-with-jvre`

Auto-aligns all line items in the allocation with JVRE (Job Value & Reward Engine) recommendations. This overwrites each line item's `allocated_amount` with the JVRE's `jvre_rec_amount`, subject to the tolerance band defined in the cycle (`jvre_alignment_tolerance`).

**Required Roles:** Allocation owner

#### Path Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `allocation_id` | UUID | Yes | Allocation to align |

#### Request

No body required.

#### Response — `200 OK`

Returns the updated list of allocation lines (same schema as [11.1](#111-get-budget-allocationsallocation_idlines)).

---

### 11.5 `PUT /budget-allocations/{allocation_id}/lines/{line_id}`

Updates a single budget allocation line item. Used when a manager manually adjusts an employee's allocation.

**Required Roles:** Allocation owner

#### Path Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `allocation_id` | UUID | Yes | Parent allocation ID |
| `line_id` | UUID | Yes | Line item to update |

#### Request Body

| Field | Type | Required | Constraints | Description |
|---|---|---|---|---|
| `allocated_amount` | decimal | Yes | > 0 | New total allocation for this employee |
| `notes` | string | No | 0–1000 characters | Free-text annotation for this employee |

```json
{
  "allocated_amount": 270000.00,
  "notes": "Exceptional year; market correction + merit increase warranted"
}
```

#### Response — `200 OK`

Returns the full updated list of allocation lines for the allocation (same schema as [11.1](#111-get-budget-allocationsallocation_idlines)).

#### Error Responses

| HTTP Status | `error_code` | Cause | Resolution |
|---|---|---|---|
| `400` | `LINE_NOT_IN_ALLOCATION` | `line_id` does not belong to `allocation_id` | Verify both IDs |
| `403` | `FORBIDDEN` | Caller does not own this allocation | Use allocation owner credentials |
| `404` | `NOT_FOUND` | Allocation or line not found | Verify IDs |

---

### 11.6 `POST /budget-allocations/{allocation_id}/lines/{line_id}/refresh-view`

Refreshes derived view data for a single line item (e.g., recalculates risk callout text, AI suggestions based on latest JVRE snapshot). Does not change allocated amounts.

**Required Roles:** Allocation owner

#### Response — `200 OK`

Returns the full updated list of allocation lines.

---

### 11.7 `POST /budget-allocations/{allocation_id}/submit`

Submits the completed budget allocation for review. Once submitted, line items are locked from further edits until revised.

**Required Roles:** Allocation owner

#### Request

No body required.

#### Response — `200 OK`

Returns the updated `MyBudgetAllocationResponse` with `status: "SUBMITTED"` and `submitted_at` populated.

#### Error Responses

| HTTP Status | `error_code` | Cause | Resolution |
|---|---|---|---|
| `400` | `ALLOCATION_ALREADY_SUBMITTED` | Allocation is already in `SUBMITTED` state | No action needed |
| `400` | `POOL_IMBALANCED` | Distributed line items exceed `distributable_pool` | Adjust line items to fit within budget |
| `400` | `CYCLE_NOT_ACTIVE` | Cycle is closed or in DRAFT | Confirm cycle status |

---

## 12. Pay Recommendations

Pay recommendations represent individual compensation decisions for each employee. Managers create and submit recommendations; upstream reviewers (HR, C-suite) approve or request revisions.

---

### 12.1 `GET /pay-recommendations/pending-review`

Returns all pay recommendations currently awaiting the caller's review or approval action.

**Required Roles:** Any (results scoped to caller's position in the reporting chain)

#### Response — `200 OK`

```json
{
  "status": "success",
  "message": "Pending reviews retrieved",
  "data": {
    "items": [
      {
        "recommendation_id": "d0e1f2a3-b4c5-6789-defa-012345678901",
        "subject_name": "Alice Kim",
        "subject_user_id": "e1f2a3b4-c5d6-7890-efab-123456789012",
        "actor_name": "Bob Lee",
        "status": "SUBMITTED",
        "total_recommended": 255000.00,
        "currency_code": "USD",
        "submitted_at": "2026-06-20T09:15:00Z"
      }
    ],
    "total": 4
  }
}
```

---

### 12.2 `GET /pay-recommendations/{recommendation_id}`

Returns full details of a single pay recommendation.

**Required Roles:** Any (subject must be in caller's reporting chain — either direct report, report of reports, or HR oversight)

#### Path Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `recommendation_id` | UUID | Yes | Pay recommendation ID |

#### Response — `200 OK`

```json
{
  "status": "success",
  "message": "Recommendation retrieved",
  "data": {
    "id": "d0e1f2a3-b4c5-6789-defa-012345678901",
    "cycle_id": "b8c9d0e1-f2a3-4567-bcde-890123456789",
    "subject_user_id": "e1f2a3b4-c5d6-7890-efab-123456789012",
    "subject_name": "Alice Kim",
    "actor_user_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "actor_name": "Jane Smith",
    "status": "SUBMITTED",
    "recommended_base": 190000.00,
    "recommended_variable": 35000.00,
    "recommended_lti_fmv": 25000.00,
    "recommended_lti_units": 100,
    "recommended_other_rewards": 5000.00,
    "total_recommended": 255000.00,
    "currency_code": "USD",
    "jvre_aligned": true,
    "annotations": [],
    "created_at": "2026-06-10T08:00:00Z",
    "last_saved_at": "2026-06-15T10:30:00Z",
    "submitted_at": "2026-06-20T09:15:00Z",
    "approved_at": null,
    "revised_at": null
  }
}
```

**PayRecommendationResponse fields:**

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Recommendation ID |
| `cycle_id` | UUID | Associated compensation cycle |
| `subject_user_id` | UUID | Employee being recommended for |
| `actor_user_id` | UUID | Manager/HR who created the recommendation |
| `status` | string | `DRAFT` \| `SAVED` \| `SUBMITTED` \| `APPROVED` \| `REVISION_REQUESTED` |
| `recommended_base` | decimal | Recommended annual base salary |
| `recommended_variable` | decimal | Recommended variable/bonus pay |
| `recommended_lti_fmv` | decimal | Recommended long-term incentive (fair market value) |
| `recommended_lti_units` | integer \| null | LTI grant in units (shares/options) |
| `recommended_other_rewards` | decimal | Other rewards/benefits |
| `total_recommended` | decimal | Sum of all components |
| `jvre_aligned` | boolean | Whether recommendation is within JVRE tolerance |
| `annotations` | array | List of review comments/annotations |

---

### 12.3 `POST /comp-cycles/{cycle_id}/recommendations`

Creates a new pay recommendation for a given subject in the specified cycle. This initializes the recommendation in `DRAFT` status.

**Required Roles:** Any authenticated tenant member (typically a manager)

#### Path Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `cycle_id` | UUID | Yes | Target compensation cycle |

#### Request Body

| Field | Type | Required | Description |
|---|---|---|---|
| `subject_user_id` | UUID | Yes | The employee this recommendation is for. Must be in caller's reporting chain. |

```json
{
  "subject_user_id": "e1f2a3b4-c5d6-7890-efab-123456789012"
}
```

#### Response — `201 Created`

Returns a `PayRecommendationResponse` with `status: "DRAFT"` and all component values initialized to current/existing compensation values from the latest JVRE snapshot.

#### Error Responses

| HTTP Status | `error_code` | Cause | Resolution |
|---|---|---|---|
| `400` | `RECOMMENDATION_ALREADY_EXISTS` | Recommendation for this subject in this cycle already exists | Use the existing recommendation's ID |
| `400` | `CYCLE_NOT_ACTIVE` | Cycle is not `ACTIVE` | Confirm cycle status |
| `403` | `NOT_IN_REPORTING_CHAIN` | `subject_user_id` is not in caller's reporting chain | Verify subject-manager relationship |
| `404` | `SUBJECT_NOT_FOUND` | Subject user does not exist | Verify the user ID |

---

### 12.4 `PUT /pay-recommendations/{recommendation_id}/components/{component}`

Updates a single compensation component of a pay recommendation. This is a fine-grained endpoint allowing the UI to update individual fields (e.g., just the base salary) without sending the full recommendation object.

**Required Roles:** Recommendation actor (the user who created the recommendation)

#### Path Parameters

| Parameter | Type | Required | Allowed Values | Description |
|---|---|---|---|---|
| `recommendation_id` | UUID | Yes | — | Target recommendation |
| `component` | string | Yes | `base`, `variable`, `lti_fmv`, `lti_units`, `other_rewards` | Which compensation component to update |

#### Request Body

| Field | Type | Required | Description |
|---|---|---|---|
| `value` | decimal | Yes | New value for the component. Use integer for `lti_units`. |

```json
{
  "value": 195000.00
}
```

#### Response — `200 OK`

Returns the full updated `PayRecommendationResponse` with the new `jvre_aligned` status recalculated.

#### Error Responses

| HTTP Status | `error_code` | Cause | Resolution |
|---|---|---|---|
| `400` | `INVALID_COMPONENT` | `component` path parameter is not a valid component name | Use one of the allowed values |
| `400` | `RECOMMENDATION_NOT_EDITABLE` | Recommendation is in `SUBMITTED` or `APPROVED` status | Cannot edit after submission |
| `403` | `FORBIDDEN` | Caller is not the recommendation actor | Use the actor's credentials |
| `404` | `NOT_FOUND` | Recommendation not found | Verify the ID |

---

### 12.5 `POST /pay-recommendations/{recommendation_id}/align-with-jvre`

Overwrites all component values with the JVRE engine's recommendations, within the cycle's tolerance band. Sets `jvre_aligned = true` on the recommendation.

**Required Roles:** Recommendation actor

#### Response — `200 OK`

Returns the updated `PayRecommendationResponse` with JVRE values applied and `jvre_aligned: true`.

---

### 12.6 `POST /pay-recommendations/{recommendation_id}/save`

Saves the recommendation as `SAVED` (persisted draft). Differs from submit — does not lock the record or trigger reviewer notifications. Idempotent.

**Required Roles:** Recommendation actor

#### Response — `200 OK`

Returns the updated `PayRecommendationResponse` with `status: "SAVED"` and `last_saved_at` updated.

---

### 12.7 `POST /comp-cycles/{cycle_id}/my-recommendations/submit`

Submits **all** of the caller's recommendations for a given cycle in a single operation. Moves all `DRAFT` and `SAVED` recommendations to `SUBMITTED`. Triggers reviewer notifications.

**Required Roles:** Any authenticated tenant member

#### Path Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `cycle_id` | UUID | Yes | Compensation cycle ID |

#### Response — `200 OK`

Returns the updated list of the caller's recommendations with `status: "SUBMITTED"`.

#### Error Responses

| HTTP Status | `error_code` | Cause | Resolution |
|---|---|---|---|
| `400` | `NO_RECOMMENDATIONS_TO_SUBMIT` | Caller has no draft/saved recommendations for this cycle | Create recommendations first |
| `400` | `CYCLE_PAST_DEADLINE` | Submission deadline has passed | Contact HR to request extension |
| `400` | `MISSING_REQUIRED_COMPONENTS` | One or more recommendations have zero values in required components | Fill in all components before submitting |

---

### 12.8 `POST /pay-recommendations/{recommendation_id}/approve`

Approves a submitted recommendation. Moves status to `APPROVED`. Only accessible to upstream reviewers (e.g., HR, C-suite who are above the actor in the chain).

**Required Roles:** Upstream reviewer (must be above the recommendation actor in the reporting chain, or have `HR`, `C_AND_B`, `CXO`, `CHRO`, or `CFO` role)

#### Request

No body required.

#### Response — `200 OK`

Returns the updated `PayRecommendationResponse` with `status: "APPROVED"` and `approved_at` timestamp.

#### Error Responses

| HTTP Status | `error_code` | Cause | Resolution |
|---|---|---|---|
| `400` | `RECOMMENDATION_NOT_SUBMITTED` | Recommendation is not in `SUBMITTED` status | Can only approve submitted recommendations |
| `403` | `NOT_IN_REPORTING_CHAIN` | Caller is not authorized to review this recommendation | Check reporting structure |
| `404` | `NOT_FOUND` | Recommendation not found | Verify the ID |

---

### 12.9 `POST /pay-recommendations/{recommendation_id}/revise`

Requests revision of a submitted recommendation. Moves status back to `DRAFT` and sends the annotation to the recommendation actor. Used by reviewers to request changes before approval.

**Required Roles:** Upstream reviewer (same as [12.8](#128-post-pay-recommendationsrecommendation_idapprove))

#### Request Body

| Field | Type | Required | Constraints | Description |
|---|---|---|---|---|
| `annotation_text` | string | Yes | 1–2000 characters | Explanation of why revision is needed |

```json
{
  "annotation_text": "Base increase exceeds budget allocation by 8%. Please align with the allocated amount of $190,000 or provide justification for exception."
}
```

#### Response — `200 OK`

Returns the updated `PayRecommendationResponse` with `status: "REVISION_REQUESTED"` and the annotation appended.

---

### 12.10 `POST /pay-recommendations/{recommendation_id}/annotations`

Adds a free-text annotation (comment) to a recommendation. Used by anyone in the review chain to add context or notes without changing the recommendation status.

**Required Roles:** Any user in the recommendation's reporting chain

#### Request Body

| Field | Type | Required | Constraints | Description |
|---|---|---|---|---|
| `text` | string | Yes | 1–2000 characters | Annotation text |

```json
{
  "text": "Discussed with finance — budget exception approved. Proceed with recommended amount."
}
```

#### Response — `201 Created`

```json
{
  "status": "success",
  "message": "Annotation added",
  "data": {
    "id": "a3b4c5d6-e7f8-9012-abcd-345678901234",
    "recommendation_id": "d0e1f2a3-b4c5-6789-defa-012345678901",
    "author_user_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "author_name": "Jane Smith",
    "text": "Discussed with finance — budget exception approved. Proceed with recommended amount.",
    "created_at": "2026-06-22T11:00:00Z"
  }
}
```

---

## 13. JVRE Snapshots & Reference Data

JVRE (Job Value & Reward Engine) is the AI compensation recommendation engine. These endpoints expose its snapshots, market benchmarks, and compensation history data.

---

### 13.1 `GET /jvre/snapshots/{cycle_id}/{subject_user_id}`

Returns the JVRE engine's compensation recommendation snapshot for a specific employee in a specific cycle. The snapshot contains AI-generated recommendations, market positioning, talent criticality, and risk callouts.

**Required Roles:** Any user in the subject's reporting chain, or HR/C&B

#### Path Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `cycle_id` | UUID | Yes | Target compensation cycle |
| `subject_user_id` | UUID | Yes | Employee whose snapshot to retrieve |

#### Response — `200 OK`

```json
{
  "status": "success",
  "message": "JVRE snapshot retrieved",
  "data": {
    "id": "b4c5d6e7-f8a9-0123-bcde-456789012345",
    "cycle_id": "b8c9d0e1-f2a3-4567-bcde-890123456789",
    "subject_user_id": "e1f2a3b4-c5d6-7890-efab-123456789012",
    "recommended_base": 190000.00,
    "recommended_variable": 35000.00,
    "recommended_lti_fmv": 25000.00,
    "recommended_lti_units": 100,
    "recommended_other_rewards": 5000.00,
    "currency_code": "USD",
    "criticality": "HIGH",
    "market_position": "BELOW",
    "promotion_readiness": "READY",
    "recommended_level": "L5",
    "risk_callout_text": "This employee is a high performer positioned 12% below market median. Flight risk is elevated without a meaningful adjustment.",
    "ai_suggestion_text": "Consider a base increase to $190K (P60 market) combined with an LTI refresh to address retention risk."
  }
}
```

**JvreSnapshotResponse fields:**

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Snapshot identifier |
| `cycle_id` | UUID | Compensation cycle reference |
| `subject_user_id` | UUID | Employee this snapshot is for |
| `recommended_base` | decimal \| null | JVRE-recommended annual base salary |
| `recommended_variable` | decimal \| null | JVRE-recommended variable pay |
| `recommended_lti_fmv` | decimal \| null | JVRE-recommended LTI grant (FMV) |
| `recommended_lti_units` | integer \| null | JVRE-recommended LTI in units |
| `recommended_other_rewards` | decimal \| null | Other compensation elements |
| `currency_code` | string | ISO 4217 currency |
| `criticality` | string \| null | `HIGH` \| `MEDIUM` \| `LOW` — talent criticality |
| `market_position` | string \| null | `BELOW` \| `AT` \| `ABOVE` market median |
| `promotion_readiness` | string \| null | `READY` \| `DEVELOPING` |
| `recommended_level` | string \| null | Suggested job level (e.g., `"L5"`) |
| `risk_callout_text` | string \| null | AI-generated retention risk narrative |
| `ai_suggestion_text` | string \| null | AI-generated action suggestion |

---

### 13.2 `GET /users/{subject_user_id}/market-benchmark`

Returns market compensation benchmarks for the employee's role. Used to display the market positioning context panel in the JVRE workspace.

**Required Roles:** Any user in the subject's reporting chain, or HR/C&B

#### Path Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `subject_user_id` | UUID | Yes | Employee to benchmark |

#### Response — `200 OK`

```json
{
  "status": "success",
  "message": "Market benchmark retrieved",
  "data": {
    "subject_user_id": "e1f2a3b4-c5d6-7890-efab-123456789012",
    "role_code": "SWE_SENIOR",
    "market_p25": 155000.00,
    "market_p50": 185000.00,
    "market_p75": 215000.00,
    "market_p90": 250000.00,
    "currency_code": "USD",
    "source": "Radford Global Technology Survey 2026"
  }
}
```

**MarketBenchmarkResponse fields:**

| Field | Type | Description |
|---|---|---|
| `subject_user_id` | UUID | Employee reference |
| `role_code` | string | Job role/family code used for benchmarking |
| `market_p25` | decimal | 25th percentile market compensation |
| `market_p50` | decimal | 50th percentile (median) market compensation |
| `market_p75` | decimal | 75th percentile market compensation |
| `market_p90` | decimal | 90th percentile market compensation |
| `currency_code` | string | ISO 4217 currency |
| `source` | string \| null | Survey/data source attribution |

---

### 13.3 `GET /users/{subject_user_id}/compensation-history`

Returns the historical compensation records for an employee across past cycles. Used to display the compensation trend panel.

**Required Roles:** Any user in the subject's reporting chain, or HR/C&B

#### Path Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `subject_user_id` | UUID | Yes | Employee whose history to retrieve |

#### Response — `200 OK`

```json
{
  "status": "success",
  "message": "Compensation history retrieved",
  "data": {
    "subject_user_id": "e1f2a3b4-c5d6-7890-efab-123456789012",
    "rows": [
      {
        "fiscal_year": "FY2024",
        "base_salary": 165000.00,
        "variable_pay": 25000.00,
        "lti_fmv": 20000.00,
        "total_comp": 210000.00,
        "currency_code": "USD"
      },
      {
        "fiscal_year": "FY2025",
        "base_salary": 175000.00,
        "variable_pay": 30000.00,
        "lti_fmv": 22000.00,
        "total_comp": 227000.00,
        "currency_code": "USD"
      }
    ]
  }
}
```

---

## 14. Error Reference

### 14.1 Standard Error Envelope

```json
{
  "status": "fail",
  "error_code": "MACHINE_READABLE_CODE",
  "message": "Human-readable explanation for the caller",
  "details": { }
}
```

### 14.2 HTTP Status Code Map

| HTTP Status | Semantic | Common Causes |
|---|---|---|
| `200 OK` | Success | Standard successful response |
| `201 Created` | Resource created | POST requests that create new records |
| `204 No Content` | Success, no body | DELETE operations |
| `400 Bad Request` | Client-side business logic error | Invalid state transitions, constraint violations |
| `401 Unauthorized` | Authentication failure | Missing/expired/revoked token, wrong credentials |
| `403 Forbidden` | Authorization failure | Insufficient roles, cross-tenant access attempt |
| `404 Not Found` | Resource missing | Invalid IDs, tenant-scoped records not visible |
| `409 Conflict` | State conflict | Duplicate creation, version conflict |
| `422 Unprocessable Entity` | Validation failure | Pydantic schema violations |
| `429 Too Many Requests` | Rate limit exceeded | Login/refresh brute force prevention |
| `500 Internal Server Error` | Unexpected server error | Bug, database connectivity, unhandled exception |
| `503 Service Unavailable` | Dependency unhealthy | Database unreachable |

### 14.3 Error Code Catalogue

| Error Code | HTTP Status | Cause | Resolution |
|---|---|---|---|
| `INVALID_CREDENTIALS` | `401` | Wrong email/password, inactive user, tenant mismatch | Verify credentials; no enumeration of which was wrong |
| `UNAUTHENTICATED` | `401` | Missing, malformed, expired, or revoked access token | Re-authenticate via `POST /auth/login` |
| `INVALID_TOKEN` | `401` | Refresh token invalid (wrong type, expired, already used) | Re-authenticate via `POST /auth/login` |
| `ACCOUNT_INACTIVE` | `403` | User account deactivated | Contact administrator |
| `FORBIDDEN` | `403` | Insufficient roles for this operation | Use credentials with required role |
| `NOT_IN_REPORTING_CHAIN` | `403` | Subject is not in caller's reporting chain | Verify org chart relationship |
| `ACCESS_DENIED` | `403` | Explicit access denial (logged to audit trail) | Review RBAC configuration |
| `TENANT_CONTEXT_REQUIRED` | `400` | Tenant-scoped endpoint called by platform user without tenant context | Provide appropriate tenant context |
| `TENANT_SUSPENDED` | `403` | User's tenant is `SUSPENDED` or `DISABLED` | Contact platform admin |
| `NOT_FOUND` | `404` | Resource does not exist or is not accessible to this tenant | Verify UUID and tenant membership |
| `EMAIL_ALREADY_EXISTS` | `400` | Email collision within the same scope (tenant or platform) | Use a different email |
| `TENANT_CODE_TAKEN` | `400` | Tenant `code` already registered | Choose a different code |
| `DOMAIN_ALREADY_REGISTERED` | `400` | Domain already assigned to another tenant | Use a different domain |
| `INVALID_DOMAIN` | `400` | Domain fails DNS validation | Provide a valid multi-label domain |
| `INVALID_ROLE_SCOPE` | `400` | Role codes don't match the expected scope (PLATFORM vs TENANT) | Use roles appropriate to the endpoint |
| `DEPARTMENT_CODE_TAKEN` | `400` | Department code already exists within this tenant | Choose a different code |
| `RECOMMENDATION_ALREADY_EXISTS` | `400` | Duplicate recommendation for same subject/cycle | Use the existing recommendation |
| `RECOMMENDATION_NOT_EDITABLE` | `400` | Recommendation is in a non-editable state | Cannot edit after submission |
| `RECOMMENDATION_NOT_SUBMITTED` | `400` | Approving/revising a non-submitted recommendation | Check recommendation status |
| `CYCLE_NOT_ACTIVE` | `400` | Operation requires an ACTIVE cycle | Verify cycle status |
| `CYCLE_PAST_DEADLINE` | `400` | Submission deadline has passed | Request extension from HR |
| `RESERVE_EXCEEDS_TOTAL` | `400` | Strategic reserve > total allocated | Reduce reserve amount |
| `POOL_IMBALANCED` | `400` | Total line items exceed distributable pool | Adjust allocations to fit within budget |
| `RATE_LIMITED` | `429` | Per-IP rate limit exceeded | Implement exponential backoff |
| `VALIDATION_ERROR` | `422` | Pydantic schema validation failure | Fix fields per `details.errors` array |
| `INTERNAL_ERROR` | `500` | Unhandled server-side exception | Retry with backoff; contact support if persistent |

### 14.4 Validation Error Shape

When the API returns `422 VALIDATION_ERROR`, the `details` object contains a structured breakdown of all field-level errors:

```json
{
  "status": "fail",
  "error_code": "VALIDATION_ERROR",
  "message": "Request validation failed.",
  "details": {
    "errors": [
      {
        "loc": ["body", "email"],
        "msg": "value is not a valid email address",
        "type": "value_error.email"
      },
      {
        "loc": ["body", "role_codes"],
        "msg": "field required",
        "type": "value_error.missing"
      }
    ]
  }
}
```

| Field | Description |
|---|---|
| `loc` | JSON path to the offending field (e.g., `["body", "email"]`) |
| `msg` | Human-readable error description |
| `type` | Pydantic error type code |

---

## 15. Security Architecture

### 15.1 JWT Flow

```
Client                         API Server                          Database
  │                                │                                   │
  │── POST /auth/login ──────────>│                                   │
  │   {email, password}            │── verify password hash ─────────>│
  │                                │<─ user + roles ──────────────────│
  │<── {access_token,              │── write audit(LOGIN) ───────────>│
  │     refresh_token} ───────────│                                   │
  │                                │                                   │
  │── GET /auth/me ─────────────>│                                   │
  │   Authorization: Bearer AT    │── check deny-list(jti) ─────────>│
  │                                │── fetch user ───────────────────>│
  │<── {user profile} ────────────│                                   │
  │                                │                                   │
  │── POST /auth/refresh ────────>│                                   │
  │   {refresh_token: RT}         │── validate RT signature           │
  │                                │── check deny-list(jti) ─────────>│
  │                                │── revoke RT jti ────────────────>│
  │                                │── fetch fresh roles ────────────>│
  │<── {new_access_token,          │── write new tokens ──────────────│
  │     new_refresh_token} ───────│                                   │
```

### 15.2 Middleware Chain (request processing order)

```
Incoming Request
       │
       ▼
┌─────────────────────────┐
│  1. CORSMiddleware      │  Validates Origin; rejects cross-origin requests from unlisted origins
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│  2. RequestIDMiddleware │  Generates X-Request-ID; binds to structured log context
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│  3. AccessLogMiddleware │  Logs method/path/status/latency as structured JSON
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│ 4. SecurityHeaders      │  Adds X-Frame-Options, X-Content-Type-Options, HSTS, etc.
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│ 5. SlowAPIMiddleware    │  Rate limiting by IP
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│ 6. Router               │  Route matching + FastAPI dependency injection
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│ 7. Auth Dependencies    │  get_current_user → get_tenant_context → require_*_roles
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│ 8. Route Handler        │  Business logic
└─────────────────────────┘
```

### 15.3 Tenant Isolation Layers

| Layer | Mechanism | Description |
|---|---|---|
| Architecture | Single tenant per user | Users physically belong to one tenant; no cross-tenant identity |
| Application | Dependency injection | `get_tenant_scoped_db` enforces caller's `tenant_id` on all queries |
| Database | PostgreSQL RLS | `app.current_tenant` GUC; RLS policies on all tenant tables block access to other tenants' rows even if app code has a bug |
| Override | Platform admin flag | `app.platform_override = 'true'` GUC allows bypass for legitimate platform admin operations; logged to audit trail |

### 15.4 RBAC Enforcement

Authorization is enforced at the FastAPI dependency layer before route handlers execute:

```python
# Example: HR-restricted endpoint
@router.post("/departments")
async def create_department(
    ctx: TenantContext = Depends(require_tenant_roles([RoleCode.TENANT_ADMIN, RoleCode.HR]))
):
    ...
```

- On success: dependency returns the caller's context
- On failure: dependency raises `403 FORBIDDEN` and writes `ACCESS_DENIED` to `audit_log` with required vs. actual roles

### 15.5 Audit Log

Every sensitive operation generates an immutable audit record.

| Action | Trigger |
|---|---|
| `LOGIN` | Successful login |
| `LOGOUT` | Explicit logout |
| `CURRENT_USER_VIEWED` | `GET /auth/me` |
| `ACCESS_DENIED` | Failed RBAC check |
| `USER_CREATED` | Any user creation |
| `TENANT_CREATED` | Tenant provisioning |
| `RECOMMENDATION_SUBMITTED` | Rec submission |
| `RECOMMENDATION_APPROVED` | Approval action |
| `RECOMMENDATION_REVISED` | Revision request |

---

## 16. Integration Guide

### 16.1 Authentication Flow for Frontend

**Recommended storage:**

| Token | Storage | Reason |
|---|---|---|
| Access Token | `Authorization: Bearer` header in memory | Never store in `localStorage` (XSS risk) |
| Refresh Token | `HttpOnly`, `Secure`, `SameSite=Strict` cookie | Not accessible to JavaScript; prevents CSRF + XSS theft |

**Session initialization flow:**

```
1. User submits login form
2. POST /auth/login → store access_token in memory, refresh_token in HttpOnly cookie
3. GET /auth/me → hydrate user context (roles, tenant, name)
4. On 401 from any endpoint → POST /auth/refresh → replace access_token in memory
5. On 401 from /auth/refresh → redirect to login screen
6. On app close/logout button → POST /auth/logout → clear tokens
```

### 16.2 Token Refresh Strategy

```
Request → 401 Received
          │
          ├─ Is this already a /auth/refresh request?
          │   └─ YES → Clear tokens → Redirect to login
          │
          └─ NO → POST /auth/refresh
                  │
                  ├─ Success → Retry original request with new access_token
                  │
                  └─ 401/403 → Clear tokens → Redirect to login
```

**Important:** Serialize concurrent refresh attempts. If three requests simultaneously fail with `401`, only one should call `/auth/refresh`; the other two should await the refresh result and then retry.

### 16.3 Idempotency Behavior

| Endpoint | Idempotent | Notes |
|---|---|---|
| `GET` (all) | Yes | Safe to retry |
| `POST /auth/login` | No | Each call issues new tokens |
| `POST /auth/refresh` | No | Old token revoked; never retry on `401` |
| `POST /auth/logout` | Effectively yes | Revoking an already-revoked token silently succeeds |
| `POST /departments` | No | Duplicate `code` returns `400` |
| `PUT` (all) | Yes | Full replacement semantics |
| `PATCH` (all) | Yes | Partial update; same result if called twice with same body |
| `DELETE` (all) | Yes | Second delete returns `404` (resource already gone) |
| `POST /pay-recommendations/{id}/save` | Yes | Explicitly designed as idempotent |
| `POST /pay-recommendations/{id}/approve` | Yes (after first call) | Approving an already-approved rec is a no-op |

### 16.4 Retry Strategy

```
Network error / 5xx → Retry with exponential backoff:
  Attempt 1: immediate
  Attempt 2: 1 second
  Attempt 3: 2 seconds
  Attempt 4: 4 seconds
  Max retries: 4
  Add ±25% jitter to prevent thundering herd

401 Unauthorized → Refresh token first (see 16.2), then retry once
429 Too Many Requests → Wait for Retry-After seconds, then retry
400 / 403 / 404 / 422 → Do NOT retry; these are client errors
```

### 16.5 Tenant-Scoped Endpoint Checklist

Before calling any tenant-scoped endpoint, verify:

- [ ] User is authenticated (valid access token)
- [ ] User has a `tenant_id` in their profile (from `GET /auth/me`)
- [ ] User's tenant status is `ACTIVE` (not `SUSPENDED` or `DISABLED`)
- [ ] User has the required role for the operation

### 16.6 Common Mistakes

| Mistake | Symptom | Fix |
|---|---|---|
| Sending `tenant_code` as UUID instead of slug | `422` or `401 INVALID_CREDENTIALS` | `tenant_code` is a short string like `"acme"`, not a UUID |
| Using refresh token as access token | `401 UNAUTHENTICATED` | Send access token in `Authorization: Bearer` header |
| Retrying `/auth/refresh` on failure | Token rotation confusion, locked out | On refresh `401`, force re-login; never retry refresh |
| Not serializing concurrent refreshes | Multiple `401`s after only one token was issued | Use a single in-flight refresh mutex in your HTTP client |
| Creating duplicate recommendations | `400 RECOMMENDATION_ALREADY_EXISTS` | Check existing recommendations before creating |
| Calling DELETE then expecting 204 on retry | `404 NOT_FOUND` | DELETE is idempotent in behavior but returns `404` after first call |
| Sending `Content-Type: multipart/form-data` | `422 VALIDATION_ERROR` | Use `Content-Type: application/json` for all requests |

### 16.7 Postman Collection Tips

1. Set a collection-level variable `base_url` = `https://api.compiq.ai`
2. Create a collection-level pre-request script that auto-refreshes the access token when `{{access_token}}` is expired
3. Use collection-level auth set to `Bearer Token` with value `{{access_token}}`
4. After running `POST /auth/login`, use a test script to set `pm.collectionVariables.set("access_token", pm.response.json().data.access_token)`

**Example login test script:**
```javascript
const response = pm.response.json();
if (response.status === "success") {
    pm.collectionVariables.set("access_token", response.data.access_token);
    pm.collectionVariables.set("refresh_token", response.data.refresh_token);
}
```

---

## 17. Environment & Deployment

### 17.1 Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ENVIRONMENT` | Yes | `development` | `development` \| `staging` \| `production`. Controls `/docs` visibility, HSTS, boot-time validations. |
| `DATABASE_URL` | Yes | — | `postgresql+asyncpg://user:pass@host:5432/dbname` |
| `JWT_SECRET_KEY` | Yes | — | Minimum 32-character random string. Must not be the dev sentinel in production. |
| `JWT_ALGORITHM` | No | `HS256` | JWT signing algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | `30` | Access token TTL in minutes |
| `REFRESH_TOKEN_EXPIRE_DAYS` | No | `14` | Refresh token TTL in days |
| `REDIS_URL` | Production: Yes | — | `redis://host:6379/0`. Required for multi-instance deny-list sharing. |
| `CORS_ALLOW_ORIGINS` | Yes (prod) | `*` | Comma-separated allowed origins. Never use `*` with credentials in production. |
| `CORS_ALLOW_CREDENTIALS` | No | `true` | Allow cookies/auth headers in cross-origin requests |
| `LOG_LEVEL` | No | `INFO` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` \| `CRITICAL` |
| `DB_POOL_SIZE` | No | `5` | SQLAlchemy connection pool size |
| `DB_MAX_OVERFLOW` | No | `10` | Max connections above pool size |
| `RATE_LIMIT_STORAGE_URI` | No | `memory://` | `memory://` or `redis://...`. Use Redis for multi-instance deployments. |
| `RATE_LIMIT_LOGIN` | No | `5/15minutes` | Login rate limit (slowapi format) |
| `INIT_SUPER_ADMIN_EMAIL` | Bootstrap only | — | Email for initial super admin (used by `bootstrap-super-admin` CLI command) |
| `INIT_SUPER_ADMIN_PASSWORD` | Bootstrap only | — | Password for initial super admin |

### 17.2 OpenAPI / Swagger

| Environment | Swagger UI | ReDoc | OpenAPI JSON |
|---|---|---|---|
| `development` | Available at `/docs` | Available at `/redoc` | Available at `/openapi.json` |
| `staging` | Available at `/docs` | Available at `/redoc` | Available at `/openapi.json` |
| `production` | **Disabled** | **Disabled** | **Disabled** |

### 17.3 Production Checklist

- [ ] `ENVIRONMENT=production`
- [ ] Strong `JWT_SECRET_KEY` (≥ 32 chars, random, not the dev sentinel)
- [ ] `REDIS_URL` configured for multi-instance deny-list
- [ ] `CORS_ALLOW_ORIGINS` set to explicit allowed origins (no wildcard)
- [ ] Alembic migrations applied: `alembic upgrade head`
- [ ] Super admin bootstrapped: `compiqcorebe bootstrap-super-admin`
- [ ] `LOG_LEVEL=INFO` or `WARNING`
- [ ] `DB_POOL_SIZE` and `DB_MAX_OVERFLOW` tuned for expected load
- [ ] Rate limit storage pointing to Redis (`RATE_LIMIT_STORAGE_URI`)
- [ ] Health probes configured: liveness on `/health`, readiness on `/health/db`
- [ ] TLS termination at load balancer level
- [ ] `/docs`, `/redoc`, `/openapi.json` confirmed unavailable externally

---

*Documentation generated for CompiqCore Backend v1.0 — 2026-05-28*

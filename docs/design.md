# URL Shortener Service — Design Document

---

## Table of Contents

1. [Functional Requirements](#1-functional-requirements)
2. [Non-Functional Requirements](#2-non-functional-requirements)
3. [Core Entities](#3-core-entities)
4. [API Contracts](#4-api-contracts)
5. [High Level Design](#5-high-level-design)

---

## 1. Functional Requirements

### 1.1 URL Management

| ID    | Requirement                                                                 |
|-------|-----------------------------------------------------------------------------|
| FR-01 | User can submit a long URL and receive a unique short URL in return         |
| FR-02 | User can optionally specify a custom alias instead of an auto-generated code|
| FR-03 | User can set an optional expiry date/time for a short URL                   |
| FR-04 | User can list all short URLs they have created                              |
| FR-05 | User can update the destination URL of an existing short link               |
| FR-06 | User can delete a short URL they own                                        |

### 1.2 Redirection

| ID    | Requirement                                                                 |
|-------|-----------------------------------------------------------------------------|
| FR-07 | Visiting a short URL redirects the user to the original long URL            |
| FR-08 | Expired short URLs return an appropriate error response                     |
| FR-09 | Deleted or non-existent short URLs return a 404 response                   |

### 1.3 Analytics

| ID    | Requirement                                                                 |
|-------|-----------------------------------------------------------------------------|
| FR-10 | Every click on a short URL is recorded as an analytics event                |
| FR-11 | Each click event captures: timestamp, referrer, device type, country        |
| FR-12 | User can retrieve aggregated analytics for any of their short URLs          |
| FR-13 | Analytics includes: total clicks, clicks by date, clicks by country, clicks by device |

### 1.4 Authentication & Authorization

| ID    | Requirement                                                                 |
|-------|-----------------------------------------------------------------------------|
| FR-14 | User can register and receive an API key                                    |
| FR-15 | All write operations (create, update, delete) require a valid API key       |
| FR-16 | User can only manage short URLs they own                                    |
| FR-17 | User can rotate their API key                                               |

### 1.5 Safety & Validation

| ID    | Requirement                                                                 |
|-------|-----------------------------------------------------------------------------|
| FR-18 | System validates that submitted URLs are well-formed before shortening      |
| FR-19 | System enforces rate limits per API key to prevent abuse                    |
| FR-20 | Health check endpoint is publicly available for infrastructure monitoring   |

---

## 2. Non-Functional Requirements

### 2.1 Performance

| ID     | Requirement                                                                |
|--------|----------------------------------------------------------------------------|
| NFR-01 | Redirect latency < 100ms at p99 under normal load                         |
| NFR-02 | URL shortening latency < 300ms at p99                                      |
| NFR-03 | Analytics query response < 500ms for up to 1M click events per URL        |

### 2.2 Availability & Reliability

| ID     | Requirement                                                                |
|--------|----------------------------------------------------------------------------|
| NFR-04 | Redirect service targets 99.9% uptime                                     |
| NFR-05 | No short URL data loss — persistent storage with durability guarantees    |
| NFR-06 | Service degrades gracefully — if analytics write fails, redirect succeeds |

### 2.3 Scalability

| ID     | Requirement                                                                |
|--------|----------------------------------------------------------------------------|
| NFR-07 | System supports 10M+ stored short URLs                                    |
| NFR-08 | Redirect path is horizontally scalable (stateless handlers)               |
| NFR-09 | Analytics ingestion is decoupled from redirect path (async writes)        |

### 2.4 Security

| ID     | Requirement                                                                |
|--------|----------------------------------------------------------------------------|
| NFR-10 | API keys are stored as hashed values (bcrypt/SHA-256), never plaintext    |
| NFR-11 | All inputs are validated to prevent injection attacks                     |
| NFR-12 | IP addresses in analytics are hashed for PII compliance                   |
| NFR-13 | HTTPS enforced on all endpoints in production                             |

### 2.5 Observability

| ID     | Requirement                                                                |
|--------|----------------------------------------------------------------------------|
| NFR-14 | All API requests produce structured logs (JSON) with request ID           |
| NFR-15 | Key metrics exposed: request rate, error rate, redirect latency, cache hit rate |
| NFR-16 | Health and readiness endpoints available for infrastructure monitoring     |

### 2.6 Maintainability

| ID     | Requirement                                                                |
|--------|----------------------------------------------------------------------------|
| NFR-17 | Codebase is modular with clear separation between API, business logic, and storage layers |
| NFR-18 | All public endpoints are documented via OpenAPI spec                      |
| NFR-19 | Unit test coverage ≥ 80% on business logic layer                         |
| NFR-20 | Integration tests cover all critical user journeys                        |

---

## 3. Core Entities

### 3.1 User

Represents a registered user of the service.

| Field        | Type      | Constraints              | Description                        |
|--------------|-----------|--------------------------|------------------------------------|
| id           | UUID      | PK                       | Unique identifier                  |
| email        | String    | Unique, Not Null         | User's email address               |
| created_at   | Timestamp | Not Null, Default: now() | Account creation time              |
| is_active    | Boolean   | Not Null, Default: true  | Soft-delete / deactivation flag    |

---

### 3.2 APIKey

Represents an authentication key issued to a user.

| Field        | Type      | Constraints              | Description                              |
|--------------|-----------|--------------------------|------------------------------------------|
| id           | UUID      | PK                       | Unique identifier                        |
| key_hash     | String    | Not Null                 | SHA-256 hash of the raw key (never store plaintext) |
| key_prefix   | String    | Not Null                 | First 8 chars of raw key (for display)   |
| user_id      | UUID      | FK → User, Not Null      | Owner of the key                         |
| created_at   | Timestamp | Not Null, Default: now() | Key creation time                        |
| last_used_at | Timestamp | Nullable                 | Last successful authentication time     |
| is_active    | Boolean   | Not Null, Default: true  | Allows key rotation without data loss    |

---

### 3.3 ShortURL

The core entity — maps a short code to an original URL.

| Field        | Type      | Constraints                    | Description                              |
|--------------|-----------|--------------------------------|------------------------------------------|
| id           | UUID      | PK                             | Unique identifier                        |
| short_code   | String    | Unique, Not Null, Indexed      | Auto-generated or custom alias (6-32 chars) |
| original_url | Text      | Not Null                       | The destination long URL                 |
| owner_id     | UUID      | FK → User, Not Null            | User who created the link                |
| created_at   | Timestamp | Not Null, Default: now()       | Creation time                            |
| expires_at   | Timestamp | Nullable                       | Optional expiry; null means never expires|
| is_active    | Boolean   | Not Null, Default: true        | Soft-delete flag                         |
| click_count  | Integer   | Not Null, Default: 0           | Denormalized counter for fast reads      |

---

### 3.4 ClickEvent

Records each individual visit to a short URL.

| Field        | Type      | Constraints              | Description                              |
|--------------|-----------|--------------------------|------------------------------------------|
| id           | UUID      | PK                       | Unique identifier                        |
| short_url_id | UUID      | FK → ShortURL, Not Null  | Which short URL was clicked              |
| clicked_at   | Timestamp | Not Null, Default: now() | When the click occurred                  |
| ip_hash      | String    | Nullable                 | SHA-256 of visitor IP (PII-safe)         |
| user_agent   | Text      | Nullable                 | Raw user agent string                    |
| device_type  | String    | Nullable                 | Derived: desktop / mobile / tablet / bot |
| referrer     | Text      | Nullable                 | HTTP Referer header value                |
| country_code | String    | Nullable                 | 2-letter ISO country code (from IP geo) |

---

### Entity Relationship Diagram

```
User ──────────── has many ──────────── APIKey
  │
  └──────────── owns many ──────────── ShortURL
                                           │
                                       has many
                                           │
                                       ClickEvent
```

---

## 4. API Contracts

Base path: `/api/v1`
Authentication: `X-API-Key: <key>` header on all protected endpoints

---

### 4.1 Auth

#### Register — Create a new user and receive an API key

```
POST /api/v1/auth/register

Request:
{
  "email": "user@example.com"
}

Response 201:
{
  "user_id": "uuid",
  "api_key": "sk_abc123...",       ← shown once, never again
  "key_prefix": "sk_abc123"        ← for future reference
}

Errors:
  409 — email already registered
  422 — invalid email format
```

#### Rotate API Key

```
POST /api/v1/auth/rotate-key
Headers: X-API-Key: <current_key>

Response 200:
{
  "api_key": "sk_xyz789...",       ← new key
  "key_prefix": "sk_xyz789"
}

Errors:
  401 — invalid or missing API key
```

---

### 4.2 URL Management

#### Shorten a URL

```
POST /api/v1/urls
Headers: X-API-Key: <key>

Request:
{
  "original_url": "https://very-long-url.com/some/deep/path?query=value",
  "custom_alias": "my-campaign",   ← optional, 3-32 chars, alphanumeric + hyphens
  "expires_at": "2026-12-31T23:59:59Z"  ← optional ISO 8601
}

Response 201:
{
  "id": "uuid",
  "short_code": "my-campaign",
  "short_url": "https://sho.rt/my-campaign",
  "original_url": "https://very-long-url.com/some/deep/path?query=value",
  "expires_at": "2026-12-31T23:59:59Z",
  "created_at": "2026-08-03T10:00:00Z"
}

Errors:
  400 — invalid URL format
  401 — invalid or missing API key
  409 — custom alias already taken
  422 — validation error (alias format, expiry in past)
  429 — rate limit exceeded
```

#### List My URLs

```
GET /api/v1/urls?page=1&limit=20&active_only=true
Headers: X-API-Key: <key>

Response 200:
{
  "items": [
    {
      "id": "uuid",
      "short_code": "abc123",
      "short_url": "https://sho.rt/abc123",
      "original_url": "https://...",
      "click_count": 142,
      "expires_at": null,
      "created_at": "2026-08-01T10:00:00Z",
      "is_active": true
    }
  ],
  "total": 45,
  "page": 1,
  "limit": 20
}
```

#### Get a Single URL

```
GET /api/v1/urls/{id}
Headers: X-API-Key: <key>

Response 200: { ...same shape as list item... }

Errors:
  401 — unauthorized
  403 — not owner
  404 — not found
```

#### Update a URL

```
PUT /api/v1/urls/{id}
Headers: X-API-Key: <key>

Request (all fields optional):
{
  "original_url": "https://new-destination.com",
  "expires_at": "2027-01-01T00:00:00Z"
}

Response 200: { ...updated URL object... }

Errors:
  401 — unauthorized
  403 — not owner
  404 — not found
  422 — validation error
```

#### Delete a URL

```
DELETE /api/v1/urls/{id}
Headers: X-API-Key: <key>

Response 204: (no body)

Errors:
  401 — unauthorized
  403 — not owner
  404 — not found
```

---

### 4.3 Redirection

#### Redirect to Original URL

```
GET /{short_code}
(No auth required — public endpoint)

Response 302: Location: <original_url>

Errors:
  404 — short code not found
  410 — short URL has expired
```

---

### 4.4 Analytics

#### Get Analytics for a URL

```
GET /api/v1/urls/{id}/analytics?from=2026-01-01&to=2026-08-03
Headers: X-API-Key: <key>

Response 200:
{
  "short_url_id": "uuid",
  "short_code": "abc123",
  "total_clicks": 1042,
  "unique_clicks": 873,
  "clicks_by_date": [
    { "date": "2026-08-01", "count": 312 },
    { "date": "2026-08-02", "count": 298 }
  ],
  "clicks_by_country": [
    { "country_code": "US", "count": 540 },
    { "country_code": "IN", "count": 210 }
  ],
  "clicks_by_device": [
    { "device_type": "mobile",  "count": 620 },
    { "device_type": "desktop", "count": 380 },
    { "device_type": "tablet",  "count": 42  }
  ],
  "top_referrers": [
    { "referrer": "twitter.com", "count": 390 },
    { "referrer": "direct",      "count": 210 }
  ]
}

Errors:
  401 — unauthorized
  403 — not owner
  404 — not found
```

---

### 4.5 Health

#### Health Check

```
GET /health
(No auth required)

Response 200:
{
  "status": "healthy",
  "timestamp": "2026-08-03T10:00:00Z",
  "checks": {
    "database": "ok",
    "cache":    "ok"
  }
}
```

---

## 5. High Level Design

### 5.1 Component Overview

```
                         ┌──────────────────────────────┐
                         │          Clients             │
                         │  Browser / Mobile / API App  │
                         └──────────────┬───────────────┘
                                        │ HTTPS
                         ┌──────────────▼───────────────┐
                         │        API Gateway           │
                         │    (Rate Limit / TLS)        │
                         └──────────────┬───────────────┘
                                        │
               ┌────────────────────────┼────────────────────────┐
               │                        │                        │
   ┌───────────▼──────────┐ ┌───────────▼──────────┐ ┌──────────▼──────────┐
   │   Redirect Service   │ │    URL API Service   │ │  Analytics Service  │
   │                      │ │                      │ │                     │
   │  GET /{short_code}   │ │  POST /api/v1/urls   │ │  GET analytics      │
   │  • Cache lookup      │ │  GET  /api/v1/urls   │ │  Aggregates events  │
   │  • DB fallback       │ │  PUT  /api/v1/urls   │ │                     │
   │  • Async event emit  │ │  DELETE              │ │                     │
   │  • 302 redirect      │ │  Auth / validation   │ │                     │
   └──────────┬───────────┘ └──────────┬───────────┘ └──────────┬──────────┘
              │                        │                        │
   ┌──────────▼────────────────────────▼──────┐      ┌─────────▼──────────┐
   │              Redis Cache                 │      │   Event Queue      │
   │  • short_code → original_url (hot links) │      │  (Click Events)    │
   │  • Rate limit counters per API key       │      │  async ingestion   │
   └──────────────────────┬───────────────────┘      └─────────┬──────────┘
                          │                                    │
   ┌──────────────────────▼────────────────────────────────────▼──────────┐
   │                        PostgreSQL                                     │
   │                                                                       │
   │   users          api_keys         short_urls          click_events    │
   │   ──────         ────────         ──────────          ────────────    │
   │   id             id               id                  id              │
   │   email          key_hash         short_code          short_url_id    │
   │   created_at     user_id          original_url        clicked_at      │
   │   is_active      is_active        owner_id            ip_hash         │
   │                  last_used_at     expires_at          device_type     │
   │                                   click_count         country_code    │
   │                                   is_active           referrer        │
   └───────────────────────────────────────────────────────────────────────┘
```

---

### 5.2 Redirect Flow (Critical Path — must be < 100ms)

```
Browser                 Redirect Service          Redis         PostgreSQL
   │                          │                     │               │
   │── GET /abc123 ──────────►│                     │               │
   │                          │── GET short:abc123 ►│               │
   │                          │                     │               │
   │                  Cache HIT (80% of traffic)    │               │
   │                          │◄── original_url ────│               │
   │                          │                     │               │
   │                  Cache MISS                    │               │
   │                          │── SELECT * WHERE ───┼──────────────►│
   │                          │   short_code=abc123 │               │
   │                          │◄────────────────────┼── URL row ────│
   │                          │── SET short:abc123 ►│               │
   │                          │   (TTL: 1 hour)     │               │
   │                          │                     │               │
   │                          │── emit click_event ─────► Queue     │
   │                          │   (async, non-blocking)             │
   │◄── 302 Location ─────────│                                     │
```

---

### 5.3 URL Shortening Flow

```
API Client              URL API Service            Redis          PostgreSQL
   │                          │                     │               │
   │── POST /api/v1/urls ────►│                     │               │
   │   X-API-Key: sk_abc      │                     │               │
   │                          │── validate API key ─┼──────────────►│
   │                          │◄────────────────────┼── user row ───│
   │                          │                     │               │
   │                          │── check rate limit ►│               │
   │                          │◄── counter ok ──────│               │
   │                          │                     │               │
   │                          │── validate URL (RFC 3986)           │
   │                          │── check alias availability ─────────►│
   │                          │── generate short_code (if no alias) │
   │                          │                     │               │
   │                          │── INSERT short_url ─┼──────────────►│
   │                          │◄────────────────────┼── saved row ──│
   │                          │                     │               │
   │◄── 201 { short_url } ────│                                     │
```

---

### 5.4 Key Design Decisions

| Decision | Rationale |
|---|---|
| **Redirect service is separate from write API** | Redirect is read-heavy and latency-critical; isolating it prevents write traffic from impacting p99 redirect times |
| **Redis cache in front of PostgreSQL on redirect path** | Avoids DB hit for hot links; cache hit rate target ~80% reduces DB load significantly |
| **Click events written async via queue** | Decouples analytics from redirect path; a slow analytics write never blocks a redirect |
| **Denormalized click_count on ShortURL** | Fast reads for "total clicks" without aggregating ClickEvent table; updated asynchronously |
| **API key auth over OAuth/JWT** | Simpler for developer integrations; no session management overhead; keys are per-user and rotatable |
| **Soft deletes (is_active flag)** | Preserves analytics history for deleted URLs; avoids FK violations on ClickEvent table |
| **SHA-256 hash of IP address** | Enables deduplication for unique click counting while protecting PII |

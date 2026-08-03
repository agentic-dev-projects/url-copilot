# url-copilot

An AI-powered URL shortener service with an agentic SDLC orchestration system.

**url-copilot** is two things in one:

1. **A production-quality URL shortener** — shorten links, track clicks, view analytics
2. **An AI orchestration system** — takes any feature request or enhancement as input and automatically plans, implements, tests, and documents the change against the codebase

---

## What It Does

### URL Shortener Service
- Shorten any long URL to a compact short link
- Optional custom aliases and expiry dates
- Click analytics — by date, country, device, and referrer
- API key authentication with rate limiting

### AI Orchestration System (The Copilot)
- Accepts natural language requirements (feature requests, bug fixes, enhancements)
- Interprets intent, identifies ambiguity, and decomposes into a task dependency graph
- Executes tasks using Claude AI — generates code, tests, and documentation
- Human approval gates for high-impact changes (schema migrations, deployments)
- Full audit trail of every decision and action taken

---

## Project Structure

```
url-copilot/
├── docs/                    # Design documentation
│   └── design.md            # FR, NFR, entities, APIs, HLD
├── service/                 # URL shortener application (FastAPI)
│   ├── api/                 # Route handlers
│   ├── models/              # SQLAlchemy models
│   ├── schemas/             # Pydantic request/response schemas
│   ├── core/                # Business logic
│   └── tests/               # Unit and integration tests
├── orchestrator/            # AI SDLC orchestration system
│   ├── agents/              # Claude-powered task agents
│   ├── task_graph/          # Dependency graph engine
│   ├── gates/               # Human approval checkpoints
│   ├── audit/               # Audit logging and traceability
│   └── scenarios/           # Greenfield, brownfield, ambiguous demos
└── README.md
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| API Framework | FastAPI (Python) |
| Database | PostgreSQL |
| Cache / Rate Limiting | Redis |
| AI Orchestration | Claude API (Anthropic) |
| Testing | pytest |
| Containerization | Docker + Docker Compose |

---

## Getting Started

### Prerequisites

- Python 3.11+
- Docker Desktop (for PostgreSQL and Redis)
- Git

### 1. Clone the repository

```bash
git clone git@github.com:agentic-dev-projects/url-copilot.git
cd url-copilot
```

### 2. Add Docker CLI to your PATH (macOS)

```bash
export PATH="$PATH:/Applications/Docker.app/Contents/Resources/bin"
```

To make this permanent, add the line above to your `~/.bash_profile` or `~/.zshrc`.

### 3. Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 4. Install dependencies

```bash
pip install -r requirements.txt
```

### 5. Configure environment variables

```bash
cp .env.example .env
```

The defaults in `.env` work out of the box with Docker Compose — no edits needed for local development.

### 6. Start PostgreSQL and Redis

```bash
docker compose up db cache -d
```

### 7. Run database migrations

```bash
alembic upgrade head
```

### 8. Start the application

```bash
uvicorn service.main:app --reload
```

The API is now running at `http://localhost:8000`.

---

## Verifying the Setup

### Health check

```bash
curl http://localhost:8000/health
# {"status": "healthy"}
```

### Register and get an API key

```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com"}'
# {"user_id": "...", "api_key": "sk_...", "key_prefix": "sk_..."}
```

### Shorten a URL

```bash
curl -X POST http://localhost:8000/api/v1/urls \
  -H "Content-Type: application/json" \
  -H "x-api-key: YOUR_API_KEY" \
  -d '{"original_url": "https://example.com/some/long/path"}'
# {"short_code": "abc123", "short_url": "http://localhost:8000/abc123", ...}
```

### Visit a short URL

Open `http://localhost:8000/abc123` in your browser — it redirects to the original URL.

### View analytics

```bash
curl http://localhost:8000/api/v1/urls/URL_ID/analytics \
  -H "x-api-key: YOUR_API_KEY"
```

### Interactive API docs

Open `http://localhost:8000/docs` in your browser for the full Swagger UI.

---

## Running Tests

Tests use SQLite and stub out Redis — no external services needed.

```bash
pytest service/tests/ -v
```

---

## Documentation

- [Design Document](docs/design.md) — Functional requirements, data model, API contracts, high level design

---

## Assignment Context

Built as part of the *Agentic Software Engineering System* interview assignment.
Demonstrates end-to-end SDLC automation with controlled autonomy:
agents execute under defined boundaries, humans own oversight and final quality.

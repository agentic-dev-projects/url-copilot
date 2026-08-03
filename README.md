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

## Documentation

- [Design Document](docs/design.md) — Functional requirements, data model, API contracts, high level design

---

## Assignment Context

Built as part of the *Agentic Software Engineering System* interview assignment.
Demonstrates end-to-end SDLC automation with controlled autonomy:
agents execute under defined boundaries, humans own oversight and final quality.

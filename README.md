# AI News Intelligence & Synthesis Platform

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109%2B-009688.svg)](https://fastapi.tiangolo.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-18.4%20%7C%2016%2B-336791.svg)](https://www.postgresql.org/)
[![pgvector](https://img.shields.io/badge/pgvector-0.8.6-blue.svg)](https://github.com/pgvector/pgvector)
[![Google Gemini](https://img.shields.io/badge/Google%20Gemini-gemini--embedding--001-orange.svg)](https://ai.google.dev/)
[![Resend](https://img.shields.io/badge/Resend-Email%20API-black.svg)](https://resend.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Release: V1.0.0](https://img.shields.io/badge/Release-V1.0.0%20Frozen-success.svg)](#)

A production-grade, autonomous intelligence platform that ingests, normalizes, deduplicates, clusters, synthesizes, ranks, curates, and delivers high-signal artificial intelligence developments across research papers, corporate announcements, developer discourse, and technical media.

---

## 🌟 What the Platform Does

The **AI News Intelligence & Synthesis Platform** continuously monitors the global AI landscape, transforming noisy raw data into concise, structured, and actionable intelligence. Rather than relying on simple RSS feeds or keyword alerts, the platform operates as an end-to-end editorial intelligence engine:

1. **Autonomous Multi-Source Ingestion**: Monitors official AI research labs (OpenAI, Anthropic, Google DeepMind), ArXiv preprint repositories, developer discussions (Hacker News), YouTube technical channels, and premier tech journalism.
2. **Deterministic & Semantic Deduplication**: Eliminates duplicate reporting across publications using a combination of URL canonicalization, SHA-256 fingerprinting, and dense vector similarity clustering (`vector(1536)`).
3. **Structured AI Synthesis**: Distills complex technical papers and product announcements into executive takeaways, factual key points, and impact assessments using Google Gemini.
4. **Transparent Multi-Factor Ranking**: Computes explainable priority scores based on source authority, freshness velocity, social engagement, and technical depth.
5. **Editorial Daily Curation**: Strictly constrains daily digests to the top 5–10 most significant stories with cross-domain diversity guarantees.
6. **Automated Newsletter Dispatch**: Compiles responsive HTML and plaintext email digests delivered via Resend, backed by delivery idempotency and cryptographic webhook verification.
7. **Production Command Center & Portfolio**: Integrates seamlessly with a product-first UI featuring subscriber preferences and delivery customization.

---

## 🎯 Why It Exists

The pace of AI development has created unprecedented information overload:
- Hundreds of research papers appear daily on ArXiv.
- Dozens of corporate announcements and open-source models are released every week.
- Traditional aggregators suffer from duplicate articles, low-signal reposts, clickbait headlines, and lack of synthesis.

This platform bridges that gap by applying **production software engineering, vector search, and structured LLM intelligence** to deliver a single, daily, high-signal briefing.

---

## 🏗️ System Architecture

```text
[ UPSTREAM SOURCES ]
  ├── RSS / Atom Feeds (Tech publications, Lab blogs)
  ├── Official AI Lab Portals (OpenAI, Anthropic, DeepMind)
  ├── ArXiv Preprints (cs.AI, cs.LG, cs.CL, cs.CV, stat.ML)
  ├── Developer Discourse (Hacker News Firebase API)
  └── YouTube Video Channels (Google Data API v3)
             │
             ▼
[ INGESTION & NORMALIZATION ]
  ├── URL Canonicalization (tracking strip, version strip)
  ├── Content Fingerprinting (SHA-256)
  └── Unified Content Model (ARTICLE, RESEARCH_PAPER, VIDEO, COMMUNITY_POST)
             │
             ▼
[ STORAGE & DETERMINISTIC DEDUPLICATION ]
  └── PostgreSQL 18.4 + pgvector 0.8.6 (`content_items`)
             │
             ▼
[ ON-DEMAND AI PIPELINE ]
  ├── LLM Analysis (Gemini: Summary, Key Points, Relevance)
  └── Vector Generation (gemini-embedding-001: 1536-dimensional native vector)
             │
             ▼
[ SEMANTIC DEDUPLICATION & STORY CLUSTERING ]
  ├── pgvector Cosine Distance (<=>) Cross-Matching
  ├── Temporal Windowing (72h sliding window)
  └── Story Cluster Assignment (`stories`, `story_content_items`)
             │
             ▼
[ MULTI-FACTOR RANKING & CURATION ]
  ├── Explainable Scoring (Authority + Freshness + Velocity + Diversity)
  └── Editorial Curation Engine (Top 5–10 stories constraint)
             │
             ▼
[ DISPATCH & NEWSLETTER SUBSCRIPTION ]
  ├── Daily Automation Runner (`DailyDispatchService`)
  ├── Jinja2 Digest Compilation (HTML + Plaintext)
  ├── Email Dispatch via Resend API
  └── Asynchronous Delivery Webhook Processing (Signature Verified)
             │
             ▼
[ PRESENTATION & PORTFOLIO INTEGRATION ]
  ├── FastAPI REST Endpoints (`/api/v1/...`)
  └── Portfolio Command Center (Modern Web UI + Customization Modal)
```

Detailed technical architecture documentation is available in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## 🔄 Complete Workflow

```text
SOURCE
  ↓
INGESTION (Fault-isolated adapters fetch raw data)
  ↓
NORMALIZATION (Canonical URLs, metadata JSON, ContentType enum)
  ↓
DETERMINISTIC DEDUPLICATION (SHA-256 fingerprint matching)
  ↓
AI ANALYSIS (Gemini chat model extracts summary & key points)
  ↓
ON-DEMAND EMBEDDING (gemini-embedding-001 generates 1536-dim vector)
  ↓
SEMANTIC DEDUPLICATION (pgvector cosine similarity <=> thresholding)
  ↓
STORY CLUSTERING (Aggregates multi-source evidence into unified Story)
  ↓
STORY SYNTHESIS (Headline, key takeaways, why it matters)
  ↓
RANKING (Authority, freshness, velocity, depth scoring)
  ↓
EDITORIAL CURATION (Top 5–10 stories selected with diversity caps)
  ↓
DIGEST GENERATION (Jinja2 renders responsive HTML & plaintext)
  ↓
SCHEDULED DISPATCH (Daily automation verifies date-based idempotency)
  ↓
EMAIL DELIVERY (Resend REST API dispatches transactional emails)
  ↓
DELIVERY WEBHOOK (Svix signature verification syncs delivery status)
  ↓
SUBSCRIBER STATUS (Opened, clicked, delivered, bounced tracking)
```

---

## 💻 Technology Stack

| Component | Technology | Purpose |
| :--- | :--- | :--- |
| **Backend Framework** | FastAPI (Python 3.11+) | Async REST API, OpenAPI docs, Pydantic v2 schemas |
| **Database Engine** | PostgreSQL 18.4 / 16+ | Relational persistence, JSONB metadata, ACID transactions |
| **Vector Extension** | pgvector 0.8.6 | Native `vector(1536)` storage & cosine distance (`<=>`) |
| **ORM & Migrations** | SQLAlchemy 2.0 (Async) + Alembic | Type-safe async repository pattern, schema versioning |
| **AI Synthesis** | Google Gemini (1.5/2.5 Flash) | Structured entity extraction, summaries, relevance scoring |
| **Embeddings** | Google Gemini `gemini-embedding-001` | 1536-dimensional native dense vector generation |
| **Email Delivery** | Resend API | Transactional email dispatch with deliverability tracking |
| **Webhook Security** | Svix / HMAC SHA-256 | Cryptographic webhook signature verification |
| **Templating** | Jinja2 | Responsive HTML & plaintext newsletter compilation |
| **Frontend UI** | Vanilla JS / Modern CSS / Vercel | Decoupled portfolio integration, zero heavy runtime |
| **Testing Suite** | Pytest + Pytest-Asyncio | 183 automated tests, 100% green regression coverage |

---

## 🧠 Core Engineering Subsystems

### 1. Ingestion Engine
- **RSS/Atom Adapter**: XML parsing with feed-level timeout and content-length limits.
- **Official Labs Adapter**: Scrapes and parses research cards from OpenAI, Anthropic, and Google DeepMind without requiring headless browsers.
- **ArXiv Adapter**: Queries the official ArXiv Atom API with automated rate limiting and canonical versionless URL resolution.
- **Hacker News Adapter**: Consumes the official Firebase REST API with `asyncio.Semaphore(15)` concurrency control.
- **YouTube Adapter**: Quota-conscious Google Data API v3 consumption (2 quota units per channel run).

### 2. Multi-Stage Deduplication
- **Stage 1 (URL)**: Removes UTM tags, tracking query parameters, and protocol variances.
- **Stage 2 (SHA-256)**: Content hashing guarantees repeat ingestion runs result in 0 duplicate database records.
- **Stage 3 (Vector Similarity)**: On-demand cosine similarity clustering prevents reporting the same event from multiple media outlets.

### 3. On-Demand Embedding Policy
To maintain a cost-effective, sustainable architecture:
- Historical content items are **not** bulk-embedded.
- Vectors are generated **strictly on demand** when candidate items are selected for clustering, ranking, or semantic search.
- Native `vector(1536)` storage ensures high-speed index operations directly inside PostgreSQL.

### 4. Ranking & Curation Engine
Every story is scored using a multi-factor transparent formula:
$$\text{Score} = w_a \cdot \text{Authority} + w_f \cdot \text{Freshness} + w_v \cdot \text{Velocity} + w_c \cdot \text{Completeness}$$
The curation subsystem enforces a strict constraint of **5 to 10 stories per daily digest**, ensuring subscribers receive focused, high-signal briefings.

### 5. Newsletter & Webhook Subsystem
- **Subscription API**: Idempotent email subscription with user preference storage (frequency, age group, technical topics).
- **Digest Engine**: Generates responsive, accessible HTML and plaintext emails with secure, tokenized one-click unsubscribe links.
- **Delivery Idempotency**: Prevents duplicate email dispatch for any given schedule or calendar date.
- **Webhook Ingestion**: Authenticates Resend webhooks via cryptographic signatures to synchronize real-time event status (`delivered`, `opened`, `clicked`, `bounced`).

---

## 🚀 Quick Start & Development Setup

### Prerequisites
- Python 3.11+
- PostgreSQL 16+ with `pgvector` extension (PostgreSQL 18.4 recommended)
- Google Gemini API Key
- Resend API Key (optional for dry-run testing)

### 1. Clone & Setup Virtual Environment
```bash
git clone <repository-url>
cd "AI NEWS AGGREGATOR"

# Create and activate Python virtual environment
python -m venv backend/.venv
# Windows:
backend\.venv\Scripts\activate
# Linux/macOS:
source backend/.venv/bin/activate

# Install dependencies
pip install -r backend/requirements.txt
```

### 2. Environment Configuration
Copy `.env.example` to `.env` and fill in your configuration:
```bash
cp .env.example .env
```
Key variables:
```dotenv
DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5433/ai_news_intel
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5433/ai_news_intel_test
GEMINI_API_KEY=your_gemini_api_key
GEMINI_CHAT_MODEL=gemini-1.5-flash
GEMINI_EMBEDDING_MODEL=gemini-embedding-001
GEMINI_EMBEDDING_DIMENSIONS=1536
RESEND_API_KEY=re_your_resend_api_key
RESEND_WEBHOOK_SECRET=whsec_your_webhook_secret
CORS_ORIGINS=["http://localhost:5500","http://127.0.0.1:5500","https://abdullah-shaikh-portfolio.vercel.app","https://abdullah-ai-systems-portfolio.vercel.app"]
```

### 3. Database Migrations
Run Alembic migrations to bring the schema to the latest head:
```bash
cd backend
alembic upgrade head
```

### 4. Run Development Server
```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
Verify operational probes:
```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
```

---

## 🧪 Testing & Validation

The test suite covers unit tests, repository queries, ingestion adapters, ranking algorithms, digest compilation, webhook validation, and end-to-end service orchestration:

```bash
# Run complete test suite (183 tests, 100% green)
pytest -q
```

To run the comprehensive Phase 19 production readiness verification script:
```bash
python backend/scripts/verify_phase19_final.py
```
This script validates:
- Database baseline invariants (1,348 ContentItems, 4 Stories, 4 authentic Gemini vectors)
- Alembic migration head (`f8a9b0c1d2e3`)
- Native pgvector 1536-dim cosine similarity
- FastAPI operational routes (`/health`, `/api/v1/overview`, `/api/v1/dispatch/status`)
- Daily dispatch automation in dry-run mode
- Resend webhook processing with signature verification

---

## 🚢 Deployment Strategy

The platform is designed for practical, zero-cost to low-cost production hosting:

| Component | Target Provider | Free / Low-Cost Tier Details |
| :--- | :--- | :--- |
| **Backend API** | Render / Railway / Fly.io | Stateless web service running Uvicorn ASGI |
| **Database + Vector** | Neon / Supabase | Managed PostgreSQL with native `pgvector` pre-installed |
| **Frontend / Portfolio** | Vercel | Global CDN, static hosting with HTTPS |
| **Daily Automation** | GitHub Actions / Cloudflare Cron | Scheduled cURL calling `/api/v1/dispatch/daily` with Bearer auth |

For detailed step-by-step deployment instructions, migration runbooks, and environment checklists, see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) and [docs/PRODUCTION.md](docs/PRODUCTION.md).

---

## 🛡️ Security & Reliability

- **Secret Isolation**: Zero API keys or database credentials are committed to version control or exposed in frontend code.
- **Strict CORS**: Cross-origin requests are limited to trusted production frontend domains.
- **SQL Injection Safety**: All queries use SQLAlchemy 2.0 async parameterized statements.
- **Webhook Replay Protection**: Svix cryptographic signature validation prevents forged or replayed webhook events.
- **Graceful Error Isolation**: External API outages (Gemini, Resend, upstream RSS) trigger sanitized fallbacks without leaking stack traces.

---

## 🗺️ Engineering Phase Progress (V1.0.0 Release)

All core engineering phases are fully implemented, tested, and verified:

- [x] **Phase 1: Core Backend & Data Access Layer**
- [x] **Phase 2: Source Registry System**
- [x] **Phase 3: RSS / Atom Ingestion Pipeline**
- [x] **Phase 4: Official AI Organizations Ingestion**
- [x] **Phase 5: ArXiv Research Ingestion**
- [x] **Phase 6: Hacker News & Community Signals Ingestion**
- [x] **Phase 7: YouTube Video Ingestion**
- [x] **Phase 8: Unified Content Model & Normalization**
- [x] **Phase 9: AI Processing & LLM Abstraction**
- [x] **Phase 10: Semantic Deduplication & Vector Matching**
- [x] **Phase 11: Story Clustering & Synthesis**
- [x] **Phase 12: Explainable Multi-Factor Ranking Engine**
- [x] **Phase 13: Editorial Curation Subsystem (5–10 Stories Constraint)**
- [x] **Phase 14: Comprehensive FastAPI REST API**
- [x] **Phase 15: Command-Center Frontend Discovery**
- [x] **Phase 16: On-Demand AI Pipeline Orchestration**
- [x] **Phase 17: User Subscriptions & Preference Modal**
- [x] **Phase 18: Automated Dispatch & Resend Webhook Ingestion**
- [x] **Phase 19: Production Deployment, E2E Validation & V1 Release Freeze**

See [docs/PHASE_1_18_FINAL_AUDIT.md](docs/PHASE_1_18_FINAL_AUDIT.md) and [docs/PHASE_19_FINAL_RELEASE.md](docs/PHASE_19_FINAL_RELEASE.md) for detailed audit findings.

---

## 🔭 Future Roadmap (Post-V1)

The following capabilities are reserved for future phases:
- **Multi-Channel Dispatch**: Native delivery via WhatsApp Business API and Telegram Bot API (currently displayed as "Coming Soon" in subscriber modal).
- **Deep Research Mode**: Automated multi-paper literature synthesis across ArXiv citation graphs.
- **Personalized Embeddings**: Subscriber-specific vector profiles for customized ranking weights.
- **Distributed Queues**: Redis/Celery integration if concurrent subscriber dispatches scale beyond single-node async limits.

---

## 📜 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

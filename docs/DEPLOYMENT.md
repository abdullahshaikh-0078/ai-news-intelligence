# PRODUCTION DEPLOYMENT GUIDE — $0 / LOW-COST TIER
**AI News Intelligence & Synthesis Platform**

---

## 1. Overview & Architectural Deployment Strategy

The AI News Intelligence Platform is architected as a stateless, lightweight ASGI backend accompanied by a consumer-facing portfolio presentation frontend and a native PostgreSQL + pgvector vector store.

```
       +------------------------------------+
       |  Portfolio Frontend (Vercel)       |
       |  Static HTML5/ES6, CSS3, Vanilla JS|
       +-----------------+------------------+
                         | HTTPS / REST API
                         v
       +-----------------+------------------+
       |  FastAPI Backend (Render/Fly.io)   |
       |  Stateless Uvicorn Engine          |
       +-----------------+------------------+
                         | Native pgvector (port 5432/5433)
                         v
       +-----------------+------------------+
       |  Managed PostgreSQL + pgvector     |
       |  (Neon / Supabase / Render PG)     |
       +------------------------------------+
```

### Free-Tier / Low-Cost Allocation:
- **Backend API:** Render Web Service (Free Tier) or Fly.io (Hobby)
- **Database & Vector Store:** Neon Serverless Postgres or Supabase (Free Tier with native pgvector enabled)
- **Frontend / Portfolio:** Vercel (Free Hobby Tier)
- **Transactional Email:** Resend (Free Tier: 100 emails/day, 3,000/month)
- **AI Synthesis & Embeddings:** Google AI Studio Gemini API (`gemini-3.6-flash` and `gemini-embedding-001`)

---

## 2. Managed Database Setup (PostgreSQL 16–18 with pgvector)

### Recommended Providers:
1. **Neon Serverless Postgres** (https://neon.tech):
   - Native support for `pgvector` out of the box.
   - Generous free storage (0.5 GiB) and compute.
2. **Supabase Postgres** (https://supabase.com):
   - Supports native `vector` extension up to 2000 dimensions (fully supports 1536-dim Gemini embeddings).

### Initializing pgvector & Schema Migration:
1. Connect via `psql` or database console:
   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   ```
2. Run database migrations:
   ```bash
   alembic upgrade head
   ```
   *Active Head:* `f8a9b0c1d2e3` (Phase 18).

---

## 3. Backend Deployment (FastAPI on Render / Railway / Fly.io)

### Dockerfile (Production Image):
The backend contains a production-ready Dockerfile:
```dockerfile
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Environment Variables Checklist:
Set the following environment variables in your hosting provider's dashboard:

| Variable | Description | Example / Required Format |
| :--- | :--- | :--- |
| `ENVIRONMENT` | Environment identifier | `production` |
| `DEBUG` | Enable/disable debug mode | `false` |
| `DATABASE_URL` | Async PostgreSQL connection | `postgresql+asyncpg://user:pass@host:port/dbname` |
| `TEST_DATABASE_URL` | Staging/Test DB URL | `postgresql+asyncpg://user:pass@host:port/testdb` |
| `GEMINI_API_KEY` | Google Gemini API Key | `AIzaSy...` (from Google AI Studio) |
| `GEMINI_CHAT_MODEL` | Analysis LLM model | `gemini-3.6-flash` |
| `GEMINI_EMBEDDING_MODEL` | Vector embedding model | `gemini-embedding-001` |
| `GEMINI_EMBEDDING_DIMENSIONS` | Embedding dimensions | `1536` |
| `RESEND_API_KEY` | Resend API Key | `re_...` (from Resend dashboard) |
| `RESEND_WEBHOOK_SECRET` | Resend Svix webhook key | `whsec_...` |
| `EMAIL_FROM` | Verified sender email | `AI News <newsletter@yourdomain.com>` |
| `NEWSLETTER_BASE_URL` | Public site domain | `https://abdullahshaikh.dev` |
| `CORS_ORIGINS` | Allowed portfolio origins | `["https://abdullah-shaikh-portfolio.vercel.app","https://abdullah-ai-systems-portfolio.vercel.app"]` |
| `DISPATCH_SECRET_TOKEN` | Optional auth token for cron | `your_secure_random_dispatch_token` |

### Health Checks & Probes:
- **Liveness Probe:** `GET /health` (Returns HTTP 200 `{"status": "healthy"}`)
- **Readiness Probe:** `GET /ready` (Verifies database connectivity)

---

## 4. Automated Daily Dispatch Scheduling (Target: 9:00 AM IST / 03:30 UTC)

To trigger the daily AI News dispatch automatically without needing 24/7 background workers (which sleep on Render Free Web Services):

### Target Delivery Schedule:
- **Local Time:** 9:00 AM Asia/Kolkata (IST = UTC+5:30)
- **UTC Schedule:** 03:30 UTC daily
- **Cron Expression:** `30 3 * * *` (Minute 30, Hour 3, Every Day)
- **Idempotency Guarantee:** Date-based digest locking and subscriber delivery records guarantee that re-running or triggering multiple times on the same date will safely skip already-sent subscribers and never send duplicate emails.

### Option A: Render Cron Job (Native Render Infrastructure)
In the Render Dashboard:
1. Click **New +** -> **Cron Job**.
2. Connect your `ai-news-intelligence` repository (Docker environment).
3. Set **Schedule:** `30 3 * * *`
4. Set **Command:**
   ```bash
   python -m app.services.dispatch_service
   ```
   *(Or trigger the HTTP endpoint if preferred:)*
   ```bash
   curl -fsS -X POST "https://ai-news-intelligence-vvh5.onrender.com/api/v1/dispatch/daily" \
     -H "Authorization: Bearer $DISPATCH_SECRET_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"run_ingestion": true, "dry_run": false}'
   ```
5. Attach the same environment group / variables as the web service (`DATABASE_URL`, `GEMINI_API_KEY`, `RESEND_API_KEY`, `DISPATCH_SECRET_TOKEN`).

### Option B: Free External Webhook Cron (cron-job.org / GitHub Actions)
If using an external HTTP scheduler to avoid burning Render compute minutes:
- **URL:** `https://ai-news-intelligence-vvh5.onrender.com/api/v1/dispatch/daily`
- **Method:** `POST`
- **Schedule:** `30 3 * * *` (03:30 UTC daily)
- **Headers:**
  - `Authorization: Bearer <YOUR_DISPATCH_SECRET_TOKEN>`
  - `Content-Type: application/json`
- **Request Body:**
  ```json
  {"run_ingestion": true, "dry_run": false}
  ```

---

## 5. Resend Delivery Webhook Configuration

1. In Resend Dashboard, navigate to **Webhooks** -> **Add Webhook**.
2. **Endpoint URL:** `https://your-api-domain.com/api/v1/newsletter/webhooks/resend`
3. **Events to subscribe:**
   - `email.delivered`
   - `email.bounced`
   - `email.complained`
   - `email.opened`
   - `email.clicked`
4. Copy the **Signing Secret** (`whsec_...`) and set it as `RESEND_WEBHOOK_SECRET` in your backend environment.

---

## 6. Portfolio Frontend Deployment (Vercel)

1. Connect the `abdullah-ai-systems-portfolio` repository to Vercel.
2. In the project root, ensure `scripts/core/api-client.js` resolves the API URL:
   ```javascript
   export const API_BASE_URL = window.__AI_NEWS_API_URL__ || "https://your-backend-api.onrender.com/api/v1";
   ```
3. Deploy directly via Vercel Git integration. Zero build configuration required for vanilla HTML/CSS/JS.

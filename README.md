# Shelf Space Spy

**See the shelf before your competitors do.**

Shelf Space Spy is a market-intelligence app for small brands. You pick a **market** (a product category in a country), add a few search phrases and the competitors you care about, and the app watches what Google Shopping shows shoppers and what Google Trends says they are searching for. It remembers every scan, detects what changed, scores where demand is outrunning the shelf, suggests what to investigate next, and writes a short weekly brief.

It works for **any category**. No product logic is hard-coded: skincare, earbuds, running shoes or espresso machines all run through the same engine.

> **The one rule it is built around:** data is collected and calculated by deterministic code, every result is shown with its evidence, and the AI writer that summarizes it is never allowed to make up a number.

---

## Contents

1. [What problem it solves](#1-what-problem-it-solves)
2. [What makes it different](#2-what-makes-it-different)
3. [Features](#3-features)
4. [How it works](#4-how-it-works)
5. [How SerpAPI is used](#5-how-serpapi-is-used)
6. [How the numbers are made](#6-how-the-numbers-are-made)
7. [Getting started](#7-getting-started)
8. [How to use it](#8-how-to-use-it)
9. [Configuration](#9-configuration)
10. [API reference](#10-api-reference)
11. [Project structure](#11-project-structure)
12. [Testing, quality and CI](#12-testing-quality-and-ci)
13. [Operations](#13-operations)
14. [Limits, honestly](#14-limits-honestly)
15. [License](#15-license)

---

## 1. What problem it solves

A small brand rarely has time to watch a crowded online marketplace, and a single look at Google Shopping is a snapshot that says little about direction. Typical questions are hard to answer:

- *Who actually holds the visible shelf for the phrases my customers search?*
- *Did a competitor just cut a price, launch a product, or disappear from the results?*
- *Is search demand for this phrase rising while the shelf stays thin, or is it already crowded?*
- *Where is there room, and what should I look at this week?*

Shelf Space Spy turns those into a repeatable routine: **scan, remember, compare, explain.** It gives you a history of the shelf instead of a single picture, tells you how much to trust each finding, and points to what to investigate next.

**Who it helps:** founders and small marketing teams who sell physical products online, want to research a new category before entering it, or want to keep an eye on competitors without paying for a heavyweight analytics suite.

---

## 2. What makes it different

| | |
|---|---|
| **Category-agnostic** | Create a market for any category and country. Brand detection, pack and size parsing, retailer cleaning and scoring all work from the data, not from a built-in product list. |
| **A memory, not a snapshot** | Every scan is stored. Changes are found by comparing scans, and **Replay** lets you step back to any past scan. |
| **Confidence is separate from everything** | Every change and score carries a confidence. Severity (how big) and confidence (how much to trust) are never merged, and a volatile shelf lowers confidence instead of hiding the change. |
| **The AI never does maths** | The weekly brief is written only from computed JSON. Its output is checked for numbers, dates, spelled-out figures and words like "doubled" that the data does not support, regenerated once if it fails, and replaced by a plain template if it fails again. |
| **Honest by design** | It says "observed visibility", never "market share". Inferred brands, thin history, unstable shelves and low search volume are marked wherever they affect a result. |
| **Every recommendation shows its work** | Recommended actions come from deterministic rules. Each lists its evidence, a suggested action, a confidence and its limitations. |
| **Built to protect your API spend** | Disk cache, a recency guard, a hard daily cap, bounded retries and one-scan-at-a-time per market keep SerpAPI usage predictable. |
| **Reads like a product** | A cinematic home page, a dashboard with bar charts, light and dark themes, mobile layouts and keyboard-accessible drawers and tooltips. |

---

## 3. Features

### Home page (the first thing you see after login)
One scrolling page: an animated hero (with live counts of your markets, probes and competitors), an About section explaining the Observe → Remember → Detect → Understand → Advise → Act loop, a "What we offer" grid, **your markets** (click one to open its dashboard), an **Add a market** section with the guided setup, and a footer. Motion respects `prefers-reduced-motion`.

### Market dashboard pages

| Page | What you get |
|---|---|
| **Overview** | KPI cards, a demand-versus-supply chart, ranked **market signals**, observed shelf share (bars), score by probe (bars), recent changes by type (bars), price bands by probe (stacked bars), recent changes, a brief summary and a competitor table. |
| **Changes** | A day-grouped timeline of price, rank, new-product, new-competitor and removal events, filterable by type, severity, confidence and date. Each row opens an **evidence drawer** that explains the change and why its confidence is what it is. |
| **Scanner** | Every listing from the latest scan: search, sort by column, filter by probe or brand. The **product drawer** charts a product's shelf position and price across scans. Inferred brands are flagged. |
| **Demand** | Google Trends per probe: the last 4 complete weeks against the 8 before (both shaded), the unfinished week drawn dashed and excluded, a prior-versus-recent bar chart, momentum, and low-volume and short-history warnings. |
| **Shelf** | Observed visibility shares, a **brand × probe heatmap**, gainers and decliners against the previous scan, supply structure (distinct products, brands, retailers, top-3 concentration), price bands and a methodology note. |
| **Opportunities** | A demand-versus-supply chart, score bars, a ranked list and an evidence drawer that keeps the **score** and the **confidence** apart and shows the four components behind the score. Also hosts **Recommended actions**. |
| **Weekly brief** | A headline, the brief's own opportunities and watch-outs up front, the full text, and its provenance (AI-written or template, and why). Earlier briefs are kept. |
| **Alerts** | An inbox with unread states and filters, and editable rules. |
| **Replay** | A slider through stored scans showing that scan's shelf, changes, prices and scores. It is read-only. |

### Recommended actions
Deterministic rules over data you already have. They never call SerpAPI or a language model.

- **Investigate** a probe scored Golden or Attractive with enough evidence.
- **Gather evidence** when the signal is promising but history is thin, the shelf is unstable, search volume is very low or Trends is missing.
- **Deprioritize** a probe that looks crowded.
- **Test a price** inside the middle band (a hypothesis, with a warning about discounts and the lack of margin data).
- **Review a competitor's price move**, **look at a new entrant**, **check why a product left the shelf**, using only trusted real events.

### Alerts
Five default rules per market: competitor price cuts and new discounts, new competitors, new competitor products, your brand's price and rank moves, and products leaving the shelf. A rule fires only for events at or above its minimum confidence and severity, only after a live scan, and each alert is stored once. Rules can be switched off or tuned.

### Operations
Background scans with live progress in the top bar, a recency guard, a daily scan limit, scan timeouts, restart recovery, structured JSON logs with secrets redacted, and a database backup script.

---

## 4. How it works

```
                 ┌────────────┐   ┌──────────────┐   ┌───────────────┐
  Google  ─────► │  SerpAPI   │──►│ scan service │──►│ normalize     │
 Shopping +      │ (cached,   │   │ (job, guard, │   │ brand, retail │
  Trends         │  retried)  │   │  budget)     │   │ pack, identity│
                 └────────────┘   └──────────────┘   └──────┬────────┘
                                                            ▼
        ┌───────────────────────────────────────────────────────────┐
        │ SQLite: scans · products · observations · trends · events │
        │         opportunities · reports · alerts · scan jobs      │
        └───────┬───────────────────────────────────────────────────┘
                ▼
   intelligence/ (pure functions: events, demand, shelf, supply, pricing,
                  opportunity score, recommendations)
                ▼
   services/ + API (FastAPI)  ──►  React app (landing + dashboard)
                ▼
   ai/brief.py: narrates computed JSON only, then checks the result
```

**A scan, step by step**

1. You click **Run live scan**. Guards are checked before anything starts: recency (409) and the daily limit (429). If a scan is already running for the market, you get that one back.
2. The job fetches one Google Shopping search per probe and one Google Trends request.
3. Each listing is normalized: brand (confirmed → pattern → guess, guesses flagged *inferred*), retailer, pack and size, and a stable product identity.
4. The scan, products, observations and Trends series are stored.
5. Events and opportunity scores are computed; alert rules run on the new events.
6. The job finishes and every page refetches. Progress is visible in the top bar and survives page changes.

**Stack:** Python 3.12 + FastAPI + SQLite (raw SQL), PyJWT, RapidFuzz, httpx; React + TypeScript + Vite, TanStack Query, React Router, Recharts. Tests: pytest, Vitest, Playwright. Linting: ruff, mypy, ESLint, Prettier.

---

## 5. How SerpAPI is used

SerpAPI returns Google's results as JSON. It is the **only** external data source, and everything goes through one module, `backend/app/services/serpapi.py`.

| Engine | When | What is taken |
|---|---|---|
| `google_shopping` | Once per probe on every scan | Per listing: title, retailer (`source`), price, old price, position, rating, reviews, `product_id`, link, thumbnail |
| `google_trends` | Once per scan (up to 5 keywords) | Weekly interest series for the last 12 months, with partial weeks flagged |
| `google_autocomplete` + a shopping search | Only on **Suggest probes and brands** | Keyword and brand suggestions for a new market |

**Cost.** One credit per request. A scan costs *probes + 1*, so a 3-probe market costs 4. Suggestions cost 2. Failed requests cost nothing.

**Cost control**
- **Disk cache** (`backend/data/serpapi_cache/`), keyed by request parameters. The API key is never part of a key or a file. A repeat inside the window costs 0.
- **Dry run:** `POST /api/markets/{id}/scan?dry_run=true` returns the estimated cost and spends nothing.
- **Recency guard:** a live scan is refused if the last one is younger than `SCAN_MIN_INTERVAL_MINUTES` (default 60). `force` overrides this.
- **Daily limit:** `DAILY_CREDIT_BUDGET` (default 50 per UTC day, `0` disables) is a hard cap across all markets. `force` cannot bypass it.
- **Retries:** rate limits and server errors are retried with bounded exponential backoff. Quota and bad-key errors are never retried.
- **One scan per market at a time**, so two clicks never spend twice.

Only the extracted fields are stored, not the full raw payload.

---

## 6. How the numbers are made

All of this is pure code in `backend/app/intelligence/`, with no database or web imports.

**Shelf share (observed visibility).** Each listing counts `1 / (1 + 0.1 × (position − 1))`, so higher positions count more. A brand's share is its weight over the total sampled. It is *not* market share: it is a sampled view of one Google results page.

**Change events.** Found by comparing scans: new product, new competitor, removed product, price drop, price rise, discount started, rank gain, rank loss.
- *Presence events* need two earlier scans. A *removal* needs a product seen twice that is then missing from three scans in a row; an empty scan counts as a failed fetch, and a mass disappearance is reported with low confidence as a shelf shift, not exits.
- *Price events* are for the same offer only (a Google `product_id` at one retailer); without both, no event is produced.
- *Rank events* need a move of at least 3 positions with one end in the top 20.

**Confidence and stability.** A probe's *stability* is how much its set of products carries over between consecutive scans. A volatile probe lowers an event's confidence (never its severity). Low-confidence events are stored and hidden by default.

**Demand.** The average interest of the last 4 complete weeks against the 8 before, plus momentum. The current unfinished week is never used. Very low volume is flagged, and Trends batches are never mixed.

**Opportunity score (0–100)** per probe, weighted: demand 40, shelf room 25, weak incumbents 20, price headroom 15. Labels: Golden ≥ 75, Attractive 55–74, Monitor 35–54, Crowded < 35. Confidence (data volume, scan count, shelf stability) is reported separately and never changes the score. Weights are judgment calls until you have enough real history to calibrate them.

**Product identity.** A listing matches an existing product by Google `product_id`, then exact title, then brand + sizes + core words, then a guarded fuzzy match (numbers must agree). `2 x 30ml`, `30ml x 2` and `30 ml pack of 2` are the same pack; `1 L` equals `1000 ml`; different sizes stay separate products.

**Brands.** `source` is the shop, not the maker. A brand comes from your confirmed list, then "by X" and "X |" title patterns, then a first-word guess (flagged *inferred*). Generic words like "Refurbished" are ignored, one spelling is kept per brand, and a seller store (`Retailer - Seller`) is used only when the title starts with that name.

**Weekly brief.** Claude receives only the computed JSON (events with confidence and caveats, scores, probe stability) and is told not to calculate or hide uncertainty. The reply is checked for unsupported numbers, dates, spelled-out figures and arithmetic words. One regeneration, then a deterministic template. With no `ANTHROPIC_API_KEY` the template is always used. Each brief records why.

---

## 7. Getting started

### Prerequisites
- Python 3.12 or newer and [uv](https://docs.astral.sh/uv/)
- Node.js 20 or newer and npm
- A [SerpAPI](https://serpapi.com) API key (needed for live scans and suggestions)

### Install

```bash
git clone https://github.com/Sneha123-zudo/Shelf_Space_Spy.git
cd Shelf_Space_Spy

# backend
cd backend
uv sync
cp .env.example .env        # then edit .env: add SERPAPI_API_KEY
cd ..

# frontend
cd frontend
npm install
cd ..
```

### Run

**macOS / Linux**
```bash
scripts/dev.sh              # API on :8000 and UI on :5173 (Ctrl+C stops both)
scripts/dev.sh --api-only
scripts/dev.sh --ui-only
```

**Windows (PowerShell)**
```powershell
.\scripts\dev.ps1           # each server in its own window
```

**Manually**
```bash
cd backend  && .venv/bin/python -m uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev          # http://localhost:5173
```

Open **http://localhost:5173**. API docs are at **http://localhost:8000/docs**, health at `/api/health`.

### Sign in
In development the app seeds an account:

```text
Email:    demo@shelfspacespy.com
Password: Demo@123
```

It exists only when `ENVIRONMENT` is `development` or `test`. Outside those, the app refuses to start with the example `JWT_SECRET` (or one under 32 characters).

---

## 8. How to use it

1. **Log in.** You land on the home page.
2. **Add a market.** Scroll to *Add a market* and press **Start a new market**. Type a category and country, press **Suggest probes and brands** (about 2 SerpAPI credits), then keep the search phrases (1–5) and competitors you want. A market is a category in a country, up to 5 probes, optionally your own brand, and competitors.
3. **Open it** from *Your markets*, then press **Run live scan** in the top bar. The dialog shows what will be fetched. Progress appears in the top bar and a banner, and you can leave the page while it runs.
4. **Read the first scan.** With one scan you get the shelf, prices, demand and opportunity scores. Changes, stability and removals need more scans.
5. **Scan again** after hours or days. Each scan adds to the history; changes and confidence improve with it.
6. **Work through the pages:** Overview for the story, Changes for what moved, Opportunities and Recommended actions for where to look, Weekly brief for a summary.
7. **Turn on what matters:** tune Alerts rules and use Replay to see how the shelf looked at any earlier scan.

**Tips**
- Use phrases a shopper would actually type. Very low-volume phrases are flagged and scored cautiously.
- A single scan is a snapshot. Treat recommendations as hypotheses until several scans agree.
- Check **Inferred brand** tags in the Scanner before relying on a brand-level number.

**Try the UI without spending anything:** `VITE_USE_MOCK=1 npm run dev` serves an in-memory fake API.

**Add flagged sample history to a market** (so Changes, Alerts and Replay have something to show): `POST /api/markets/{id}/demo-history`. It is built from your latest real scan, so it shows the features work, not real-world behaviour.

---

## 9. Configuration

Everything is read from `backend/.env` (see `backend/.env.example`). Never commit `.env`.

| Variable | Purpose | Default |
|---|---|---|
| `SERPAPI_API_KEY` | Required for live scans and suggestions | – |
| `ANTHROPIC_API_KEY` | Optional. Enables AI narration of the weekly brief | – (template is used) |
| `ANTHROPIC_MODEL` | Model for the brief | `claude-sonnet-5-5` |
| `JWT_SECRET` | Signs login tokens. Must be strong outside development | example value |
| `ENVIRONMENT` | `development` / `test` allow the example secret and seeded account | `development` |
| `FRONTEND_ORIGINS` | Allowed CORS origins | `http://localhost:5173` |
| `SCAN_MIN_INTERVAL_MINUTES` | Recency guard window | `60` |
| `DAILY_CREDIT_BUDGET` | Hard daily cap on live SerpAPI credits (`0` = off) | `50` |
| `SCAN_JOB_TIMEOUT_SECONDS` | Cancel a scan that runs longer | `600` |
| `SUGGEST_CACHE_TTL_HOURS` | How long suggestions are reused | `168` |
| `TRENDS_DATE_RANGE` | Google Trends window (`today 5-y` for year-over-year) | `today 12-m` |
| `SERPAPI_CACHE_DIR` | Cache location | `backend/data/serpapi_cache` |
| `SERPAPI_MAX_ATTEMPTS`, `SERPAPI_BACKOFF_BASE_SECONDS`, `SERPAPI_BACKOFF_CAP_SECONDS` | Retry policy for rate limits and 5xx | `3`, `1.0`, `20.0` |
| `LOG_LEVEL`, `LOG_FORMAT` | `text` or `json` logs (secrets are redacted) | `INFO`, `text` |
| `DEMO_*` | Seeded development account and example market(s) | see `.env.example` |
| `VITE_API_PROXY` (frontend) | Where `/api` is proxied in dev/preview | `http://localhost:8000` |

---

## 10. API reference

Everything is under `/api`, with JWT bearer auth (`POST /api/auth/login`). Interactive docs: `/docs`.

**Markets and scanning**

| Method and path | What it does |
|---|---|
| `POST /auth/login`, `GET /auth/me` | Sign in / current user |
| `GET /markets`, `GET/PUT/DELETE /markets/{id}` | List, read, update, delete your markets |
| `POST /markets` | Create `{category, country, keywords[1-5], competitors[], brand_name?, name?}` |
| `POST /markets/suggest` | Suggested probes and brands for `{category, country}` |
| `POST /markets/{id}/scan` | Run a scan now (`force`, `dry_run`, `refresh`) |
| `POST /markets/{id}/scan-jobs` | Start a background scan: `202` with a job, or `200` with the running one |
| `GET /markets/{id}/scan-jobs/latest`, `/{job_id}` | Poll a job (`status`, `progress_done/total`, `error_code`) |
| `GET /credits` | Today's live credit usage against the daily limit |
| `POST /markets/{id}/demo-history` | Add flagged sample history |

**Read-only sections** (`GET /markets/{id}/…`; a market with no data answers `200` with `has_data: false`; most accept `?scan_id=` to view an older scan)

| Path | Returns |
|---|---|
| `dashboard` | KPIs, events, shelf share, competitors, opportunities, latest brief |
| `scans`, `replay` | Scan list; stored shopping scans for the slider |
| `events` | Paged events: `type` (repeatable), `include_low_confidence`, `limit`, `offset` |
| `observations`, `products/{id}` | Paged listings (`keyword`, `brand`, `q`, `sort`); one product across scans |
| `demand`, `shelf`, `supply`, `pricing` | Per-probe Trends signal and series; visibility with stability; supply structure; price bands |
| `opportunities`, `recommendations` | Scores with components, confidence and caveats; recommended actions with evidence |
| `reports`, `POST reports/weekly` | Brief history; generate a brief |
| `alerts`, `POST alerts/read`, `alert-rules`, `PATCH alert-rules/{id}`, `GET /alerts/summary` | Inbox, mark read, rules, unread counts |

---

## 11. Project structure

```
backend/
  app/
    api/            FastAPI routers (auth, markets, sections, operations, health)
    core/           config, security (JWT, scrypt), redacting JSON logging
    intelligence/   pure analytics: events, demand, shelf share, supply, pricing,
                    opportunity score, recommendations
    services/       scan pipeline, SerpAPI client, normalization, jobs, alerts,
                    section read models
    ai/brief.py     weekly brief: narration + claim checking + template fallback
    db.py, repo.py  SQLite schema and all SQL
  tests/            pytest (no network, no real keys)
frontend/
  src/
    pages/          landing page and the dashboard pages
    components/     shell, charts, drawers, badges, landing sections
    api/            typed client, hooks (TanStack Query), mock API
    lib/            formatting and pure helpers
  e2e/              Playwright smoke tests
scripts/            dev.sh / dev.ps1, backup_db.sh / backup_db.ps1
.github/workflows/  CI
```

---

## 12. Testing, quality and CI

```bash
# backend
cd backend
uv run ruff check app tests
uv run mypy app
uv run pytest                      # ~190 tests, no network, no SerpAPI credits

# frontend
cd frontend
npm run check                      # typecheck, eslint, prettier, vitest, build
npm run e2e                        # Playwright (desktop + mobile) on the mock API
```

- Tests use a temporary database and cache and a fake SerpAPI layer. Any real network call fails the test.
- GitHub Actions (`.github/workflows/ci.yml`) runs the backend checks and `npm run check` on every push and pull request.
- A guard test fails if category-specific words appear in the application code.

---

## 13. Operations

- **Backup:** `scripts/backup_db.sh` (or `backup_db.ps1`) writes a consistent copy of `backend/data/shelf_space_spy.db` to `backups/` (git-ignored). Restore by copying a file back.
- **Logs:** `LOG_FORMAT=json` for log collectors. API keys, tokens and passwords are redacted; request logs hold only method, path, status, duration and an id.
- **Safety:** one scan per market at a time, a job timeout, and any job left running by a restart is marked failed at startup. Foreign keys are enforced in SQLite.
- **Data:** SQLite lives in `backend/data/`. Delete a market from the API and its scans, events and alerts go with it.

---

## 14. Limits, honestly

- **Not market share or sales.** Google Shopping and Trends show what is visible and searched. They do not show sales, margins or real customer demand. Treat every finding as a hypothesis to investigate.
- **A sampled shelf.** Google reorders results between requests. That is why presence and rank events need history and carry a confidence.
- **Real-world accuracy still needs real history.** The tests prove the logic on synthetic scan sequences. Thresholds (rank moves, stability cut-offs, removal run length) and the score weights are judgment calls until you have several real scans taken hours or days apart.
- **Brand detection is best-effort.** Inferred brands are flagged and a few guesses will be wrong.
- **Not built yet:** a scheduler (scans are manual), email or webhook alerts, per-market threshold overrides, related Trends queries (the scanner stores only the interest series), counting market suggestions toward the daily limit.

---

## 15. License

MIT. See [LICENSE](LICENSE).

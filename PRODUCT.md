# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Primary: MoSPI's CPI compilation team and the DGCA Tariff Monitoring Unit — the
statisticians who currently produce the airfare component of India's CPI by hand.
They evaluate VIMAAN as a candidate replacement methodology and tool, not as pitch
theatre, so credibility on estimator choice, provenance, and honesty about what's
real versus simulated matters more than surface polish.

Secondary, same sitting: SIH26056 finals judges, who score the console live and
on-site. The bar it's designed to clear is "would a domain expert trust this,"
not "does this look impressive for 90 seconds."

## Product Purpose

VIMAAN is a real-time airfare price index for India's CPI. The current method
(DGCA Tariff Monitoring Unit) samples 78 routes once a month by hand, ~60 days
before publication — one price per route, when fares vary by date, advance-purchase
window, cabin, carrier, stops, and baggage. VIMAAN samples 517 routes, 3x/day,
across 8 advance-purchase windows and a 60-day horizon (1,440 fares/route/month),
publishing same-day in SDMX-JSON. Success is a MoSPI/DGCA reviewer concluding the
methodology is sound enough to warrant a real pilot, and SIH judges scoring it as
methodologically credible rather than merely visually impressive.

## Positioning

The differentiator is the estimator, not the scraping volume. A naive daily-chained
index invents index drift from sample churn alone (a flight sells out, a route
isn't scheduled) — demonstrated in the prototype's chain-drift simulation: 15%
matched-sample churn on prices that return exactly to base produces −61.26 on a
naive chained index vs +0.00 on GEKS-Jevons. VIMAAN runs the estimators national
statistics offices already use on web-collected prices (Jevons elementary
aggregates, GEKS-Jevons multilateral on a 25-month rolling window cross-checked
against Time-Product-Dummy, hedonic quality adjustment, month-of-departure
attribution, seasonal adjustment with Indian moving-holiday regressors, real
DGCA passenger weights) rather than a naive chain a competing scraper could copy
without also copying the methodology discipline. On seasonal adjustment, state
the engine and not the aspiration: X-13ARIMA-SEATS runs only where the Census
Bureau binary is installed, and the deployed API does not have it, so the
published adjusted series is produced by a RegARIMA holiday regression plus STL.
Every response carries the engine that actually ran.

## Operating Context

- Live, on-site SIH26056 finals demo round; idea-submission deck already
  submitted (26RBU142_SIH26056_BharatBytes.pptx/pdf).
- Deployed at vimaan-console.vercel.app (Vercel Standard Deployment Protection
  enabled — only the production alias is publicly reachable; deployment-specific
  URLs show a login page). One Vercel project serves both surfaces: the static
  console from `prototype/web`, and the FastAPI backend as a Python function at
  `api/index.py`, routed at `/v1/*`. Same origin, so the console needs no CORS.
- Storage is Neon Postgres (Vercel Marketplace, Singapore region), reached via
  `DATABASE_URL`. SQLite remains supported for local work — `vimaan/pgcompat.py`
  presents Postgres behind the same interface, so one dialect lives in the
  source and the differences live in one file.
- Collection runs three times a day via Vercel Cron hitting
  `/api/cron/collect`, authenticated with `CRON_SECRET`.
- The prototype is the canonical demo surface: `prototype/vimaan-console.html`
  (source of truth, hand-edited, ~880KB self-contained). `prototype/web/index.html`
  is a generated build (via `build_web.py`) — never edit it directly.
- Four views: The Index (headline figure, provenance, 32-month series, seasonal
  adjustment, route contributions, live SDMX payload), Why One Price Isn't Enough
  (route map + 80-cell price surface per route), Why The Maths Matters
  (chain-drift demo with a 1–25 month window slider), Can You Trust It
  (collection coverage published honestly, per-lane health). A "Guide me"
  seven-step walkthrough drives the whole console.
- Collection basis, in order of durability: Lane A, airline tariff pages
  (statutory disclosure under Rule 135(2), Aircraft Rules 1937 — cannot be
  blocked); Lane B, licensed distribution APIs (Travelpayouts Data API, after
  Amadeus Self-Service was decommissioned 17 Jul 2026); Lane C, public travel
  portals (robots.txt observed, rate-budgeted, blockable — the index survives on
  A and B alone). Explicitly out of scope: CAPTCHA solving, login/paywall
  circumvention, fingerprint spoofing, any personal data.
- Backend (`backend/`, FastAPI + medallion store, 99 tests) implements the index
  engine — Jevons, GEKS-Jevons, TPD cross-check, hedonic quality adjustment,
  targeted-mean imputation, seasonal adjustment, acquisition-basis parallel
  series, revision tracking — and serves it over `/v1/*` and SDMX-JSON. The
  console reads that API.

## Capabilities and Constraints

- **The console reads live data from the VIMAAN API and requires a network
  connection.** This replaces the previous offline-first constraint, which was
  dropped deliberately on 2026-09-10: the console now shows the published
  index, its provenance and its SDMX payload from the API rather than from
  figures compiled into the page. When the API is unreachable the console says
  so in a banner and marks the compiled-in numbers as sample data — it never
  silently presents them as live, because that is the one failure the product's
  own honesty principle cannot survive.
- The console is still a single HTML file with no build step for its own
  markup; `prototype/build_web.py` produces the hosted copy from it.
- Real and sourced, must not be contradicted: CPI 2024 group weights incl.
  Transport at 9.43% (MoSPI, PIB PRID 2227012); the 78-route monthly baseline
  (PIB/MoCA, PRID 1810467); DGCA monthly domestic city-pair traffic (500 routes,
  146.3M passengers, 2024) via Vonter/india-aviation-traffic (ODbL); India's
  boundary (DataMeet OSM extract); the index estimators themselves (ONS
  Methodology Working Paper 12, ILO/IMF CPI Manual 2020).
- Mixed provenance, and the distinction must survive every change: the bulk of
  the panel is still `scripts/simulate_panel.py` output, tagged `simulated` in
  `silver_fare`. Real Lane B fares from the Travelpayouts Data API are tagged
  `travelpayouts` and are genuinely collected, with each row linked to the
  hashed raw payload it was parsed from. `/v1/collection/stats` reports the
  design figures and the measured ones side by side; the console shows the
  measured one. The chain-drift demonstration and the coverage map remain
  illustrations built from the sampling design.
- A published figure is flagged `provisional` automatically when weighted
  coverage falls below 70% *or* the imputed share exceeds 50%, and the reason is
  published with it in `provisional_reason`. These are different failures:
  coverage asks whether the heavy routes were seen at all, imputation asks how
  much of what was published was actually measured. Real backend behaviour, not
  decoration.
- Terminology to use precisely, not loosely: Jevons / GEKS-Jevons / Time-Product-
  Dummy / mean splicing / Lowe aggregation / hedonic quality adjustment /
  chain drift / matched-sample churn / SDMX-JSON.

## Brand Commitments

- Name: **VIMAAN** — "Validated Index of Market Airfares for Augmenting
  National-statistics." Team Bharat Bytes, SIH26056, Theme: Travel & Tourism,
  Category: Software.
- Voice: precise and unhedged about what's real vs. simulated ("Honest note on
  the data" is a stated value, not boilerplate) — the "Can You Trust It" view
  exists specifically to publish shortfalls rather than hide them. Do not soften
  this into generic confident marketing copy.
- Globe rendering adapted from WorldPolicy-Env (`globe.jsx`, Apache-2.0), ported
  to vanilla JS, recoloured, re-pointed at India's boundary — attribution must be
  preserved if this component is touched.

## Evidence on Hand

- `26RBU142_SIH26056_BharatBytes.pptx/pdf` — submitted idea-round deck.
- `backend/README.md` — authoritative real-vs-simulated table; consult before
  any copy change that states what VIMAAN "does" or "measures."
- `SOLUTION_PLAN.md` — full technical plan and proposed production stack
  (Python 3.11, Playwright, PostgreSQL 16 + TimescaleDB, Polars, statsmodels,
  X-13ARIMA-SEATS, FastAPI, React, Docker Compose) — not the prototype's stack,
  don't conflate the two.
- No testimonials, customer logos, or third-party endorsements exist; none
  should be fabricated or implied.

## Product Principles

1. Domain credibility over demo spectacle — every visual claim must survive a
   MoSPI/DGCA statistician reading it closely, not just play well on a stage.
2. Real vs. simulated stays legible everywhere — never let a chart, label, or
   transition blur the line the README and backend README both draw explicitly.
3. The methodology is the product — Jevons/GEKS-Jevons/hedonic adjustment are
   the differentiator; visual design should make that legible, not bury it
   under generic dashboard chrome.
4. Live over compiled-in — the console reads the published figure and its
   provenance from the API. A number on screen that cannot be traced to the
   store is either labelled as sample data or not shown.
5. Coverage and provenance are first-class, not footnotes — the 70%-coverage
   `provisional` flag and per-lane health are real product behavior the design
   must surface, not hide.

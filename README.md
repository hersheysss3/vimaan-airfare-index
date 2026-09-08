# VIMAAN

**Validated Index of Market Airfares for Augmenting National-statistics**

A real-time airfare price index for India, built for **Smart India Hackathon 2026**,
problem statement **SIH26056** (Ministry of Statistics and Programme Implementation).

**Live prototype → https://vimaan-console.vercel.app**

---

## The problem

India's Consumer Price Index still takes its airfare component by hand. The DGCA's
Tariff Monitoring Unit checks **78 routes, once a month**, by opening airline
websites, and the figure reaches the CPI roughly two months later.

That would be a reasonable method if a flight had one price. It doesn't. The fare
you see is a function of the route, the departure date, how far ahead you book, the
cabin, the carrier, the number of stops and the baggage allowance. Sampling one cell
of that surface once a month measures noise, not inflation — and airfare sits inside
the Transport group, weighted **9.43%** of CPI 2024.

## What VIMAAN does

| | Present method | VIMAAN |
|---|---|---|
| Routes measured | 78 | 517 |
| Price checks per route per month | 1 | 90 |
| Fares per route per month | 1 | 1,440 |
| Publication lag | ~60 days | same day |

Three times a day it collects fares across 517 routes, over eight advance-purchase
windows and a 60-day forward horizon, then reduces them to one index number with its
provenance attached, published in SDMX-JSON — the format MoSPI, the RBI and the IMF
already exchange statistics in.

## Why the method matters more than the scraping

Collecting more prices is the easy half. **How you average them decides the answer.**

Chain a daily index over prices that bounce between fare buckets and it ratchets
upward and never comes back down, even when prices do. That is *chain drift*. On a
simulation with **constant true prices and zero real inflation**, a naive daily-chained
index invents **+12.9 index points** of price rises that never happened.

VIMAAN uses the estimators national statistics offices already run on web-collected
prices:

- **Jevons** geometric mean for elementary aggregates (the CPI Manual's prescription —
  arithmetic means are upward-biased on volatile prices)
- **GEKS-Jevons** multilateral on a 25-month rolling window, cross-checked against
  **Time-Product-Dummy**, extended by mean splicing so published history is never revised
- **Hedonic quality adjustment** so swapping a nonstop for a one-stop reads as a
  quality change, not deflation
- **Month-of-departure attribution** (ONS practice), with an acquisition-basis series
  published in parallel
- **X-13ARIMA-SEATS** with Indian moving-holiday regressors, because Diwali moves
- **DGCA base-period passenger weights** for upper-level aggregation

The `Why The Maths Matters` view in the prototype lets you drag the comparison window
from 1 to 25 months and watch the invented inflation collapse from +13.6 to +0.1.

## Collection basis

Two of the three intake lanes do not scrape at all.

| Lane | Basis | Can it be blocked? |
|---|---|---|
| A — airline tariff pages | **Rule 135(2), Aircraft Rules 1937** obliges every airline to publish its established tariff | No, it is a statutory disclosure |
| B — licensed distribution APIs | Contractual. Amadeus Self-Service was decommissioned 17 Jul 2026; the working free route is now the Travelpayouts Data API | No |
| C — public travel portals | Publicly displayed prices, `robots.txt` observed, rate-budgeted | Yes, and the index survives on A and B |

Explicitly **out of scope by design**: no CAPTCHA solving, no login or paywall
circumvention, no fingerprint spoofing, and no personal data at any stage.

---

## Repository layout

```
├── prototype/
│   ├── vimaan-console.html      the console, fully self-contained (858 KB)
│   ├── web/index.html           the same build for hosting (131 KB, CDN libs)
│   ├── build_web.py             generates web/ from the offline build
│   ├── test.js                  puppeteer regression suite
│   └── shot.js                  screenshot harness
├── build/
│   ├── plates.py                generates every deck chart with matplotlib
│   ├── make_final.py            fills the official SIH template
│   ├── _prims.py                shared python-pptx primitives
│   ├── n_*.png                  the generated chart plates
│   └── prep/prep.html           source for the team run sheet
├── SIH2026-VIMAAN-SIH26056-BharatBytes.pptx    the submission deck
├── SIH2026-VIMAAN-SIH26056-BharatBytes.pdf
├── VIMAAN_Presentation_Prep.pdf                13-page team run sheet
└── SOLUTION_PLAN.md                            full technical plan
```

## The prototype

Four views, one HTML file, no build step.

- **The Index** — the headline figure with its provenance, a 32-month series with
  seasonal adjustment, which routes drove the month's move, and the live SDMX payload
- **Why One Price Isn't Enough** — a route map, and the full 80-cell price surface for
  any route you pick. One button dims it to the single cell a monthly manual check
  would have caught
- **Why The Maths Matters** — the chain-drift demonstration, with the window slider
- **Can You Trust It** — collection coverage with the shortfalls published rather than
  hidden, and per-lane health

Press **Guide me** for a seven-step walkthrough that drives the whole console.

### Running it

```bash
# no install, no server
open prototype/vimaan-console.html
```

The offline build inlines three.js and framer-motion, so it works with the network
unplugged — deliberate, because a demo should not depend on venue wifi.

```bash
npm install                       # puppeteer + framer-motion
node prototype/test.js ./out dark # regression suite
python prototype/build_web.py     # rebuild the hosted copy
```

### Rebuilding the deck

```bash
cd build
python plates.py        # regenerate every chart from data
python make_final.py    # fill the SIH template
```

Every visual in the deck is either a matplotlib chart driven by data or a native
PowerPoint shape. There is no AI-generated illustration in it.

---

## Deployment

Hosted on Vercel from `prototype/web`, connected to this repository so pushes to
`main` deploy automatically.

```bash
cd prototype && python build_web.py
cd web && npx vercel deploy --prod
```

> **Note on URLs.** This account has Vercel's Standard Deployment Protection enabled,
> so only the production alias `vimaan-console.vercel.app` is publicly reachable.
> Deployment-specific URLs will show a Vercel login page.

## Tech

**Prototype** — one HTML file, vanilla JS, no framework. SVG charts written by hand,
a Canvas-2D orthographic globe, a WebGL price surface via three.js, animation via
framer-motion's vanilla DOM API. Degrades cleanly with no GPU and with no network.

**Deck pipeline** — Python, matplotlib, python-pptx, Pillow.

**Backend** ([backend/](backend/)) — Python, FastAPI, SQLite medallion store, and an
index engine with 69 tests asserting its properties. Route weights come from real
published DGCA traffic; see [backend/README.md](backend/README.md) for exactly what is
real and what is simulated.

**Proposed production stack** (see [SOLUTION_PLAN.md](SOLUTION_PLAN.md)) — Python 3.11,
Playwright, PostgreSQL 16 + TimescaleDB, Polars, statsmodels, X-13ARIMA-SEATS, FastAPI,
React, Docker Compose.

---

## Honest note on the data

**The prototype runs on a seeded simulation, not live collection.** It is deterministic
across reloads. What is real and sourced:

- CPI 2024 group weights, including Transport at 9.43% — MoSPI, PIB PRID 2227012
- The 78-route monthly baseline — PIB / MoCA, PRID 1810467
- India's boundary — DataMeet OSM extract, carrying India's official extent
- The index estimators — ONS Methodology Working Paper 12, ILO/IMF CPI Manual 2020

What is modelled from the sampling design rather than collected: the fare grid, the
chain-drift simulation, and the coverage map. Every figure in the deck carries a
`Source:` line stating which it is.

## Attribution

- The globe's layered rendering approach is adapted from
  [WorldPolicy-Env](https://github.com/Krishpotanwar/WorldPolicy-Env) (`globe.jsx`),
  **Apache-2.0** — ported to vanilla JS, recoloured, and re-pointed at India's own
  boundary. No assets or data were taken from that project.
- India boundary simplified from [DataMeet maps](https://github.com/datameet/maps)
- World silhouette from [Natural Earth](https://github.com/nvkelso/natural-earth-vector)
  110m, with India excluded so it is drawn from the DataMeet boundary instead
- [three.js](https://threejs.org) (MIT), [framer-motion](https://github.com/motiondivision/motion) (MIT)

## References

Full list in [SOLUTION_PLAN.md](SOLUTION_PLAN.md) and on slide 6 of the deck. The
load-bearing ones:

1. MoSPI — FAQs on the CPI 2024 Series, Annexure V
2. PIB — first CPI release on base 2024 = 100, 12 Feb 2026 (PRID 2227012)
3. Aircraft Rules, 1937 — Rule 135(2)
4. Collection of Statistics Act, 2008
5. PIB / MoCA — DGCA Tariff Monitoring Unit (PRID 1810467)
6. DGCA — Monthly Domestic Traffic Statistics
7. ONS — Aggregate index of air fares methodology (FOI-2023-1164)
8. ONS — Methodology Working Paper 12; and on multilateral index methods
9. ILO / IMF / OECD / Eurostat — Consumer Price Index Manual (2020)
10. Statistics Canada — Air Transportation Index in the CPI (2020)

---

**Team Bharat Bytes** · Problem SIH26056 · Theme: Travel & Tourism · Category: Software

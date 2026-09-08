# SIH26056 — VIMAAN
### Validated Index of Market Airfares for Augmenting National-statistics
**Real-time Airfare Price Index for India via automated collection from airline & OTA portals, for augmentation of the CPI**

Organisation: **MoSPI** · Theme: Travel & Tourism · Category: Software

---

## 0. The 30-second frame

> "Skyscanner tells you what a ticket costs. **VIMAAN tells the Reserve Bank whether to raise interest rates.**"

We are not building a fare-search product. We are building **statistical infrastructure**: a reproducible, audit-grade, daily airfare price index that plugs into the CPI 2024 series as a machine-readable feed.

---

## 1. Ground truth — what actually exists today (research findings)

| Fact | Source | Why it matters to us |
|---|---|---|
| CPI new series **base 2024=100** released **12 Feb 2026**; items up 299→358; services 40→50; COICOP 2018 classification; 12 online markets added | MoSPI CPI-2024 FAQ (Annexure V), PIB | Our output must speak CPI-2024 language, not CPI-2012 |
| Airfare, telephone, OTT prices are collected **"through online platforms"** — still human-driven | MoSPI CPI-2024 FAQ | The *manual online collection* is exactly what we automate |
| **Transport weight = 9.43%** in CPI 2024 | MoSPI | Sizes our contribution: air transport is a sub-item inside this |
| DGCA **Tariff Monitoring Unit** checks **78 routes, monthly, by manually opening airline websites** | PIB / MoCA | Our baseline to beat: 78 → 500+ routes, monthly → 3× daily |
| **Rule 135(2), Aircraft Rules 1937**: every air transport undertaking must **publish its established tariff on its website** and maintain tariff records for DGCA | Aircraft Rules 1937 | **Legal foundation** — published tariffs are mandated public data, not scraped secrets |
| ONS collects fares at **fixed advance-purchase windows** (long-haul 6/3/1 month, short-haul 3/1, domestic 1 month), on a secret "index day", and records the change in the **month of departure, not purchase** | ONS FOI-2023-1164 | Tells us the *correct* sampling design — most teams will miss this entirely |
| NSOs use **Jevons elementary + GEKS-Jevons / Time-Product-Dummy multilateral + rolling-window splicing** for web-scraped prices to avoid chain drift | ONS Working Paper 12; Eurostat | Our methodological moat |

---

## 2. Problem decomposition — why this is hard (and why most teams will get it wrong)

Naive framing: *"scrape prices, average them, divide by base, ×100."* That fails for five reasons:

1. **Airfare is not a price — it is a surface.**
   The observed fare is a function `P(route, departure_date, days_before_departure, cabin, carrier, stops, refundability, baggage)`. Sampling one cell per route per day measures noise, not inflation.

2. **Dynamic pricing / chain drift.**
   Fares move 15–20× per day and bounce between fare buckets. Chaining a daily index over such data produces **chain drift** — the index can rise several percent per year even with flat prices. This is a known, published failure mode.

3. **Product-mix change masquerades as price change.**
   If IndiGo swaps a nonstop for a 1-stop on a route, average fare falls — but that is a *quality* change, not deflation.

4. **Listed price ≠ transacted price.**
   OTAs display the cheapest available bucket. Actual realised yield differs. An index built purely on displayed minima is biased.

5. **Acquisition vs use timing.**
   A ticket bought in August for a December flight — which month's inflation does it belong to? International practice says **month of departure**. Almost no hackathon team will handle this.

**VIMAAN is designed around these five problems.** That is the entire pitch.

---

## 3. Solution architecture

### 3.1 Four-layer system

```
LAYER 0 — COMPLIANT ACQUISITION (3 independent lanes)
  Lane A  Statutory/Published tariff pages ....... Rule 135(2) mandated airline tariff pages
  Lane B  Licensed distribution APIs .............. Amadeus Self-Service, Duffel, airline NDC sandboxes
  Lane C  Consumer-facing portals (robots-aware) .. MakeMyTrip, Cleartrip, Ixigo, Google Flights
  Reference feeds .......................... DGCA traffic & yield, IOCL ATF price, holiday calendar,
                                             OpenFlights airport master, MoSPI CPI-2024 weights
        |
        v
LAYER 1 — MEDALLION DATA PLANE
  BRONZE  immutable raw snapshot (HTML/JSON + SHA-256 + collector version + timestamp)  -> object store
  SILVER  normalised FareObservation rows (IATA-coded, tax-inclusive, currency-parsed)   -> PostgreSQL
  GOLD    the PRICE SURFACE cube: route x depart_date x lead_time x cabin x carrier      -> TimescaleDB
        |
        v
LAYER 2 — INDEX ENGINE (the differentiator)
  2a Hedonic quality adjustment  (log-fare ~ stops + duration + depart-slot + refundable + bag + carrier)
  2b Elementary aggregate        Jevons geometric mean per (route x lead-time-window x cabin)
  2c Multilateral index          GEKS-Jevons + Time-Product-Dummy, 25-month rolling window, mean splice
  2d Upper-level aggregation     Lowe/Young index, weights = DGCA passenger traffic x CPI-2024 structure
  2e Seasonal adjustment         X-13ARIMA-SEATS with Indian festival regressors (Diwali/Holi/Eid moving)
  2f Bias correction             listed->transacted calibration against DGCA revenue/pax yield
  2g Imputation                  two-way fixed-effects model (route FE + date FE + lead FE)
        |
        v
LAYER 3 — DELIVERY
  FastAPI REST + OpenAPI/Swagger  |  SDMX-JSON export (NSO exchange standard)
  React dashboard (6 views)       |  Auto-generated monthly PDF bulletin
  Grafana pipeline observability  |  Full revision & audit trail
```

### 3.2 Why three acquisition lanes (this is the compliance story)

| Lane | Legal basis | Blocking risk | Coverage |
|---|---|---|---|
| A — Published airline tariff pages | **Rule 135(2) mandates publication**; Collection of Statistics Act, 2008 empowers MoSPI to collect | Very low | Carrier-level, authoritative |
| B — Licensed APIs (Amadeus/Duffel/NDC) | Contractual, free developer tiers | Zero | Structured, multi-carrier, no HTML parsing |
| C — Consumer portals | Publicly displayed prices; `robots.txt` respected; rate-budgeted | Medium | Reflects what a consumer actually pays |

**Explicitly out of scope, by design:** CAPTCHA-solving services, login/paywall circumvention, residential-proxy evasion, ToS-breaking fingerprint spoofing.

We state this on the slide. Pitching CAPTCHA bypass to a government ministry is a self-inflicted wound; **declining to do it is a scoring point**, because it proves we understand we are building a public-sector system. If one lane degrades, the other two carry the index — that is the resilience argument, not evasion.

### 3.3 The Price Surface (core data model)

Each observation:

```json
{
  "obs_id": "OBS-2026-08-29-DEL-BOM-6E2345-L14",
  "collected_at": "2026-08-29T02:14:07+05:30",
  "lane": "B_api",          "source": "amadeus",
  "origin": "DEL", "destination": "BOM",
  "depart_date": "2026-09-12", "lead_days": 14, "lead_bucket": "L14",
  "carrier": "6E", "flight_no": "6E-2345",
  "cabin": "economy", "fare_family": "saver",
  "price_total_inr": 4850, "base_fare": 3990, "taxes_fees": 860,
  "stops": 0, "duration_min": 135, "depart_slot": "early_morning",
  "refundable": false, "checked_bag_kg": 15, "seats_remaining_shown": 4,
  "raw_sha256": "...", "collector_version": "1.4.2"
}
```

Sampling design (adapted from ONS to a domestic-heavy market):

- **Lead-time buckets:** L1, L3, L7, L14, L21, L30, L45, L60 days before departure.
- **Departure calendar:** every departure date in a rolling 60-day horizon — we can afford full coverage because we are automated, so no secret "index day" is needed.
- **Cabin:** economy and premium/business separately.
- **Cadence:** 3 collections/day (02:00, 11:00, 19:00 IST) to capture intra-day revenue-management moves; daily route price = **median of intra-day observations**.
- **Attribution:** each fare is attributed to its **departure month** (use basis), with an acquisition-basis series published in parallel so MoSPI can choose.

Volume: 500 routes × 60 departure dates × 8 lead buckets is over-sampled; we collect on a **PPS (probability-proportional-to-size) rotating panel** driven by DGCA traffic — about 120k–200k observations/day, feasible on a single machine.

### 3.4 Index mathematics (this is what wins)

**Step 1 — Hedonic quality adjustment.** For route *i*, period *t*:

```
ln P_j = a + b1*stops_j + b2*ln(duration_j) + b3*slot_j + b4*refundable_j
         + b5*bag_j + sum(g_c * carrier_c) + d*lead_bucket_j + e_j
```

Quality-adjusted price = fare re-priced to a fixed reference specification. Removes product-mix drift.

**Step 2 — Elementary aggregate: Jevons (geometric mean).** Within each stratum *s* = (route × lead-bucket × cabin):

```
P_Jevons(s, 0->t) = PRODUCT_j ( p_j^t / p_j^0 ) ^ (1/n)
```

Geometric, not arithmetic — international CPI manual requirement; arithmetic means are upward-biased for volatile prices.

**Step 3 — Multilateral, drift-free: GEKS-Jevons.** Over a 25-month rolling window *T*:

```
P_GEKS(0,t) = PRODUCT_{l=0..T} [ P_J(0,l) * P_J(l,t) ] ^ (1/(T+1))
```

plus a **Time-Product-Dummy** cross-check (regression `ln p_jt = mu_t + lambda_j + e`), and **mean splicing** to extend the window without revising history. This is the published NSO answer to chain drift in high-frequency web data.

**Step 4 — Upper-level aggregation: Lowe/Young with DGCA weights.**

```
AirfareIndex(t) = [ SUM_i ( w_i * I_i(t) ) / SUM_i w_i ] * 100
w_i = pax_i(base) / SUM pax(base)
```

Also published as the classic **Laspeyres** form for direct comparability with the existing MoSPI method — we show both and explain the gap.

**Step 5 — Seasonal adjustment.** X-13ARIMA-SEATS with **moving-holiday regressors** for Diwali, Holi, Eid, Christmas and school-vacation windows — Indian festival dates shift across the Gregorian calendar, so a plain seasonal filter mis-attributes them.

**Step 6 — Listed→transacted bias correction.** Estimate `k_i = realised_yield_i / listed_median_i` from DGCA route revenue and passenger data; apply as a slow-moving level correction, published with confidence bands.

**Step 7 — Quality gate.** Every published number carries coverage %, source count, imputation share, revision flag and a reproducibility hash. Anything below threshold is published as *provisional*.

### 3.5 Sub-indices produced

| Sub-index | Cut | Consumer |
|---|---|---|
| National Airfare Index | All routes, weighted | **CPI 2024 — Transport** |
| Route index | Per O-D pair | DGCA TMU, route regulation |
| Carrier index | Per airline | CCI, competition analysis |
| Regional index | Metro–Metro, Metro–Tier2, NE connectivity, UDAN routes | Regional inflation, MoCA |
| Cabin index | Economy vs Business | Distributional analysis |
| Lead-time index | L1/L7/L30/L60 | Measures the *advance-purchase penalty* — a statistic India does not currently publish |
| Volatility index | Dispersion of fares per route | Early warning for fare surges |

---

## 4. Beyond-the-brief features (each maps to a real stakeholder)

| Feature | What it does | Who wants it |
|---|---|---|
| **Fare-Surge Sentinel** | Detects abnormal simultaneous fare-band convergence across carriers on a route | DGCA TMU, Parliament questions on fare gouging |
| **ATF pass-through model** | Regresses index on IOCL ATF price with distributed lags — quantifies how much of a fare rise is fuel | MoCA, RBI |
| **Nowcast** | Forecasts next month's index from bookings curve + fuel + calendar; publishes a fan chart | RBI policy timing |
| **Event overlay** | Flags fare spikes during floods, festivals, exam seasons | NDMA, MoCA |
| **SDMX-JSON endpoint** | Emits the index in the exact format MoSPI/IMF/RBI exchange statistics in | MoSPI integration — the "this is deployable" proof |
| **Reproducibility bundle** | One command re-derives any published number from hashed raw snapshots | Statistical audit / RTI defensibility |

---

## 5. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Collectors | **Playwright** (async, robots-aware) + **httpx** for API lanes | Handles JS-heavy portals; async gives throughput without evasion tricks |
| Scheduling / queue | **Redis + RQ workers**, **APScheduler** for cadence; Airflow named as the production path | Fits 36h; retry, backoff, dead-letter |
| Storage | **PostgreSQL 16 + TimescaleDB** (hypertables, continuous aggregates, compression); **Parquet + DuckDB** for analytical replay; object store for Bronze | Time-series queries in ms; Parquet makes reproducibility cheap |
| Processing | **Python 3.11**, **Polars** (hot path) + **Pandas** (analysis), **NumPy/SciPy** | Polars is 5–10× faster on the surface cube |
| Statistics | **statsmodels** (X-13ARIMA-SEATS, OLS hedonics), custom `vimaan-index` module (Jevons, Lowe, GEKS-J, TPD, splicing) | The index library *is* the IP |
| API | **FastAPI + Pydantic v2 + Uvicorn**, OpenAPI/Swagger, **SDMX-JSON** serializer, Redis response cache | Live Swagger demo lands hard with government judges |
| Dashboard | **React 18 + Vite + TypeScript + Tailwind**, **Recharts** + **visx**, **MapLibre GL / deck.gl ArcLayer** for route flows | deck.gl arcs over India = the visual wow moment |
| Observability | **Prometheus + Grafana**, structured JSON logs, per-lane success SLOs | "Our system self-monitors" = operational maturity |
| Delivery | **Docker Compose** (one command), GitHub Actions CI, pytest + Great Expectations data tests | Judges can run it; data tests prove rigour |

---

## 6. Repository layout

```
vimaan/
├── docker-compose.yml            # one command brings up everything
├── Makefile                      # make demo / make seed / make index
├── collectors/
│   ├── lane_a_tariff/            # Rule 135(2) published tariff pages
│   ├── lane_b_api/               # amadeus.py, duffel.py, ndc_sandbox.py
│   ├── lane_c_portal/            # makemytrip.py, cleartrip.py, ixigo.py, gflights.py
│   ├── core/  base.py  robots.py  ratebudget.py  snapshot.py   # SHA-256 Bronze writer
│   └── schedule.py               # PPS rotating panel, 3x daily cadence
├── pipeline/
│   ├── normalize.py  iata.py  currency.py  tax_rules.py
│   ├── validate.py               # z-score + IQR outliers, Great Expectations suite
│   └── surface.py                # builds the GOLD price-surface cube
├── indexengine/
│   ├── hedonic.py                # quality adjustment regression
│   ├── elementary.py             # Jevons / Dutot / Carli
│   ├── multilateral.py           # GEKS-J, TPD, rolling window, mean splice
│   ├── aggregate.py              # Lowe / Young / Laspeyres, DGCA weights
│   ├── seasonal.py               # X-13ARIMA-SEATS + Indian festival regressors
│   ├── bias.py                   # listed -> transacted calibration
│   └── impute.py                 # two-way fixed effects
├── api/
│   └── main.py  routers/  sdmx.py  cache.py
├── dashboard/                    # React + Vite + deck.gl
├── analytics/                    # nowcast.py, atf_passthrough.py, sentinel.py
├── data/                         # routes_top500.csv, dgca_pax.csv, airports.geojson,
│                                 # atf_prices.csv, festival_calendar.csv, base_2024.csv
└── docs/  METHODOLOGY.md  API.md  COMPLIANCE.md  REVISIONS.md
```

---

## 7. API surface

```
GET /v1/index/national?period=2026-08&basis=use&adjusted=true
GET /v1/index/national/series?from=2024-01&to=2026-08
GET /v1/index/route/{origin}-{destination}?period=2026-08
GET /v1/index/carrier/{code}?period=2026-08
GET /v1/index/region/{region}?period=2026-08
GET /v1/index/leadtime?bucket=L7&period=2026-08     # advance-purchase penalty
GET /v1/index/volatility?route=DEL-BOM&period=2026-08
GET /v1/surface?route=DEL-BOM&depart=2026-09-12     # full lead-time price curve
GET /v1/quality?period=2026-08                      # coverage, imputation share, revision flags
GET /v1/methodology/{index_id}                      # formula + parameters used for THIS number
GET /v1/sdmx/CPI_AIRFARE?period=2026-08             # SDMX-JSON for MoSPI ingestion
GET /v1/alerts/surge?since=2026-08-01               # Fare-Surge Sentinel
GET /v1/health
```

Every index response embeds `{value, basis, method, window, coverage_pct, imputed_pct, revision, reproducibility_hash}`. **Nothing is published without provenance.**

---

## 8. Dashboard — 6 views

1. **National Overview** — big index number, MoM/YoY deltas, 24-month trend with raw vs seasonally-adjusted overlay, confidence band, data-freshness badge.
2. **Route Explorer** — MapLibre India map, deck.gl arcs (thickness = passenger weight, colour = YoY change). Click an arc → route price surface heatmap (departure date × lead time).
3. **Carrier Comparison** — small multiples of carrier indices, fare-band distribution ridgeline, market-share-weighted contribution waterfall.
4. **Seasonality & Events** — raw vs X-13 adjusted, festival markers, ATF pass-through overlay with lag slider.
5. **Data Quality Console** — route × date coverage heatmap, per-lane success rate, imputation share, live collection log, SLO gauges.
6. **Bulletin** — one-click "Airfare Price Index — <Month> <Year>" PDF, MoSPI-styled, charts plus methodology annex.

The **contribution waterfall** is the sleeper visual: it shows *which routes drove the index move*. That is the chart an economist actually wants, and no other team will build it.

---

## 9. Team plan (6 members)

| # | Role | Owns | Prep before hackathon |
|---|---|---|---|
| M1 | Acquisition Lead | Lane C portal collectors, robots/rate budget, snapshot integrity | Selector maps for 4 portals; 21-day backup dataset |
| M2 | Data Engineer | Lane A + B, normalisation, surface cube, TimescaleDB | Amadeus/Duffel dev keys; IATA + tax rules tables |
| M3 | Index Statistician | Hedonic, Jevons, GEKS-J/TPD, splicing, seasonal | Implement and unit-test the index library **before** the event |
| M4 | Backend | FastAPI, SDMX, caching, quality metadata, auth | DB schema + OpenAPI contract frozen at hour 0 |
| M5 | Frontend | 6 dashboard views, deck.gl map, charts | Figma of all 6 screens; component library scaffolded |
| M6 | DevOps + Pitch | Docker Compose, Grafana, demo script, deck, backup video | Compose running on all 6 laptops; deck v1 done |

### 36-hour plan

| Hours | M1+M2 (data) | M3+M4 (index+API) | M5 (UI) | M6 (ops/pitch) |
|---|---|---|---|---|
| 0–2 | Freeze route panel + lead buckets | Freeze schema + API contract + formula set | Wireframes locked | Repo, Compose skeleton |
| 2–6 | Lane B API collectors live (fastest win first) | DB + Timescale + FastAPI health | Shell, routing, theme, mock data | PG + Redis + Grafana up |
| 6–14 | Lane A + C collectors; 100 routes flowing | Hedonic + Jevons + Lowe end-to-end | Overview + Route Explorer on real API | End-to-end integration |
| 14–22 | Scale to 300+ routes; validation suite green | GEKS-J + TPD + X-13 + sub-indices; SDMX | Carrier, Seasonality, Quality views | Bulletin generator; deck v2 |
| 22–30 | Reliability, retries, coverage push | Nowcast, ATF pass-through, Surge Sentinel | Polish, animation, dark mode, responsive | **Backup video recorded** |
| 30–36 | Full team: 3× demo rehearsal, Q&A drill, failure-mode dry run, deck final | | | |

**Insurance policy:** pre-collect 21 days of real data before the event and ship it as a seeded Docker volume. `make demo` must work with the WiFi unplugged.

---

## 10. Feasibility, risks, mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Portal blocks the collector | High | Medium | 3 independent lanes; Lane A/B are statutory/contractual and cannot be blocked. Index survives on any one lane |
| Legal challenge over collection | Low | High | Rule 135(2) mandated publication + Collection of Statistics Act 2008 + robots.txt compliance + zero PII (DPDP-clean) + no circumvention. Documented in `COMPLIANCE.md` |
| No base-period (2024) fares to divide by | High | High | Backcast base from DGCA published yields, archived fare data and Wayback snapshots; publish base as *estimated* with a band, converging as live data accumulates |
| Chain drift inflates the index | Certain if naive | High | GEKS-J / TPD multilateral + 25-month rolling window + mean splice |
| Listed ≠ transacted bias | Certain | Medium | DGCA yield calibration factor, published with uncertainty |
| Site layout changes break parsers | Medium | Medium | Schema-drift detector + per-lane SLO alarms; parsers are config-driven, not hardcoded |
| Demo-day network failure | Medium | Fatal | Seeded 21-day dataset + recorded backup video + fully offline Compose |
| Judges ask "is this just Skyscanner?" | Certain | — | The one-liner, then the SDMX and methodology endpoints |

---

## 11. Impact

**Economic.** Air transport sits inside the 9.43% Transport weight of CPI-2024. A biased airfare component propagates into headline CPI → RBI repo decisions → EMIs, deposit rates and bond yields for 140 crore people. Moving airfare from a manual, thin, lagged sample to a daily, 500-route, quality-adjusted index measurably improves the accuracy of the number the entire economy is priced off.

**Administrative.** DGCA's TMU covers 78 routes checked by hand each month. VIMAAN gives it 500+ routes, three times a day, with automatic surge alerts — a step change in regulatory capability at near-zero marginal cost per route.

**Consumer and transparency.** Public route and lead-time indices tell citizens when to book and expose the true advance-purchase penalty. No such index exists for India today.

**Institutional.** SDMX output means MoSPI, RBI and IMF can ingest it without human hands. Reproducibility hashes make every published number defensible under RTI and statistical audit.

**Scalable.** Swap collectors and weights and the same engine yields a rail-fare index, a hotel-tariff index or an intercity-bus index. The architecture is a **general high-frequency services-price index platform**; airfare is the first instance.

---

## 12. Judge Q&A drill

| Question | Answer |
|---|---|
| "How is this different from MakeMyTrip?" | "They sell tickets. We produce an official statistic. Our output is an index number with a methodology endpoint and an SDMX feed — it goes into the CPI, not into a shopping cart." |
| "Isn't scraping illegal?" | "Two of our three lanes don't scrape at all — Rule 135(2) of the Aircraft Rules *requires* airlines to publish tariffs, and we use licensed distribution APIs. The third reads publicly displayed prices, honours robots.txt, rate-limits, and collects zero personal data. We deliberately do **not** solve CAPTCHAs or spoof fingerprints." |
| "What if a site blocks you?" | "The index is designed to survive on one lane. Lane A and B are statutory and contractual. We also hold a 21-day buffer, so even total collection failure doesn't move the monthly number." |
| "Why not just ask the airlines?" | "Rule 135 already makes them publish it — we read what they are legally required to disclose. Voluntary sharing would be aggregated, delayed and self-reported. We capture what the consumer actually faces." |
| "Dynamic pricing makes this meaningless." | "Dynamic pricing is precisely why a single monthly manual check is meaningless. We sample eight lead-time buckets, three times daily, take medians, and apply GEKS-Jevons multilateral methods that are drift-free under exactly this volatility." |
| "Why geometric mean?" | "International CPI manual practice for elementary aggregates. Arithmetic means are upward-biased when prices are volatile — that bias would be imported straight into headline CPI." |
| "How accurate?" | "We cross-validate across three independent lanes and report coverage and imputation share with every number. Divergence beyond a threshold is flagged, not silently averaged. Every published value is reproducible from hashed raw snapshots." |
| "Can MoSPI actually use it?" | "Live Swagger, then `/v1/sdmx/CPI_AIRFARE`. That is the exchange format their systems already speak." |

---

## 13. What separates us from the field

| VIMAAN | What a typical team will submit |
|---|---|
| Price **surface** (route × date × lead-time × cabin) | One price per route per day |
| Jevons elementary + GEKS-J/TPD multilateral + splicing | Plain arithmetic Laspeyres |
| Hedonic quality adjustment | No control for stops/duration/cabin |
| Departure-month attribution (ONS practice) | Purchase-date attribution |
| Compliance as a designed layer; CAPTCHA-solving explicitly refused | "We use 2Captcha and rotating proxies" |
| SDMX-JSON + methodology endpoint + reproducibility hash | Dashboard only |
| Festival-aware X-13ARIMA-SEATS | Raw trend, or none |
| Listed→transacted bias correction via DGCA yields | Assumes listed = paid |
| Fare-Surge Sentinel + ATF pass-through + nowcast | Charts only |
| One-command Docker + seeded offline demo | Live demo that dies with the WiFi |

---

## 14. References

1. MoSPI — *FAQs on CPI 2024 Series* (Annexure V), Feb 2026 — mospi.gov.in
2. PIB — *First press release of CPI on base 2024=100*, 12 Feb 2026
3. MoSPI — *Expert Group Report on Comprehensive Updation of CPI*
4. ONS — *Aggregate index of air fares methodology* (FOI-2023-1164)
5. ONS — *Methodology Working Paper 12: comparison of index number methodology used on UK web-scraped price data*
6. ONS — *Introducing multilateral index methods into consumer price statistics*
7. ILO/IMF/OECD/UNECE/Eurostat/World Bank — *Consumer Price Index Manual: Concepts and Methods* (2020)
8. Aircraft Rules, 1937 — Rule 135 (tariff establishment and publication)
9. Collection of Statistics Act, 2008
10. PIB / MoCA — *DGCA Tariff Monitoring Unit monitors airfares on 78 routes*
11. DGCA — Monthly Domestic Traffic Statistics (route-wise passengers and load factors)
12. Statistics Canada — *Enhancements to the Air Transportation Index in the CPI* (2020)
13. Statistics Netherlands / Eurostat — web scraping in official price statistics
14. IOCL — Aviation Turbine Fuel price circulars

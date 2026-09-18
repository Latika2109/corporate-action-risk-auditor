# Corporate Action Risk Auditor

**Team:** Ctrl+Alt+Elite — CSI SIESGST Enigma 5.0 Hackathon

A human-in-the-loop exception management platform that ingests raw NSE/BSE
corporate action feeds, cross-validates them by ISIN, flags date/ratio
conflicts, and simulates portfolio impact in a shadow ledger before any
risk officer signs off on a real adjustment.

This repo is a working MVP scaffold — every phase below is implemented and
tested, not just planned. Use the 5-day breakdown to divide ownership across
the team and to know what to extend vs. what's already load-bearing.

## Project layout

```
backend/
  app/
    models/
      schemas.py     Pydantic canonical schemas (Phase 1)
      db.py           SQLAlchemy models + engine (SQLite/Postgres)
    core/
      ingestion.py    CSV + text-dump parsing, quarantine gateway (Phase 1)
      validation.py   Cross-exchange ISIN join + date/ratio rules (Phase 2)
      simulation.py   Shadow ledger simulator + sign-off (Phase 3)
    routers/
      ingest.py       POST /api/ingest/run
      simulate.py     POST /api/simulate/event/{id}, /pending, /signoff
      exceptions.py   GET /api/events, /anomalies, /quarantine, /holdings
    seed.py           Seeds mock BrokerHoldings
    main.py           FastAPI app entrypoint
  requirements.txt
  Procfile            Render/Railway start command
frontend/
  dashboard.py        Streamlit exception dashboard (Phase 4)
  requirements.txt
  Procfile
data/
  sample_nse/, sample_bse/     Structured CSV fixtures with planted anomalies
  mock_dumps/                  Unstructured circular text fixtures
tests/
  test_pipeline.py    Pytest smoke tests for ingestion/validation/simulation
```

## Quickstart (local dev)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt -r frontend/requirements.txt

# Terminal 1: backend
cd backend
python -m app.seed          # seeds mock broker holdings
uvicorn app.main:app --reload --port 8000

# Terminal 2: frontend
cd frontend
streamlit run dashboard.py
```

Then in the Streamlit UI's "Upload & Run" tab, upload
`data/sample_nse/nse_corporate_actions.csv` and
`data/sample_bse/bse_corporate_actions.csv` — these fixtures are seeded with
a planted date mismatch, a planted ratio mismatch, and one quarantined row
per exchange, so you'll see every dashboard section populated immediately.

Run tests: `pytest tests/ -v`

---

## 5-Day Implementation Plan

### Phase 1 — Data Ingestion & Normalization (Day 1–2)

**Owner focus:** Pandas parsing robustness, canonical schema, quarantine gateway.

Built in [`backend/app/core/ingestion.py`](backend/app/core/ingestion.py) and
[`backend/app/models/schemas.py`](backend/app/models/schemas.py):

- **Canonical schema** (`StandardizedEvent`): `isin`, `exchange`, `action_type`,
  `ratio_multiplier`, `ex_date`, `record_date`, `status`. Pydantic validators
  enforce ISIN length (12 chars) and positive ratios at the model boundary —
  bad data cannot silently enter the system as a "valid" event.
- **Header aliasing** (`COLUMN_ALIASES`): NSE and BSE use different column
  names for the same concept (`isin` vs `isin_code`, `ex_date` vs `exdate`).
  A reverse-lookup map normalizes any known variant before parsing.
- **Ratio parsing** (`_parse_ratio`): handles `"1:10"`, `"1-for-10"`, and
  plain numeric strings. **Important convention:** SPLIT ratios are
  `new/old` (a 1:10 split → multiplier 10.0), but BONUS ratios add to the
  existing holding (a 2:1 bonus → multiplier 1 + new/old). This is
  action-type-aware — get the action type wrong and the simulated balance
  will be wrong, so this is the first thing to sanity-check if numbers look
  off in Phase 3.
- **Date parsing** (`_parse_date`): tries a list of known formats, falls back
  to `pandas.to_datetime`. Returns `None` (never raises) so callers can
  quarantine cleanly.
- **Quarantine Gateway** (`ingest_csv_bytes`, `ingest_text_dump`): every row
  is parsed inside a try/except. Missing ISIN, unparseable date, unparseable
  ratio, or a file with no required columns at all → routed to
  `QuarantinedRecord` with a human-readable `reason` string, never a crash.
  This is the core reliability guarantee of the whole system — verify it
  with `tests/test_pipeline.py::test_ingestion_quarantines_missing_isin_and_bad_date`.
- **Unstructured text dumps** (`ingest_text_dump`): splits circulars on blank
  lines into blocks, regex-extracts ISIN / ratio / date tokens per block.
  Malformed blocks are quarantined individually so one broken circular
  doesn't block the rest of the batch.

**Day 1:** schemas.py, db.py, ingestion.py CSV path, quarantine gateway, unit tests.
**Day 2:** text-dump parser, header-alias coverage for real NSE/BSE export
quirks your team finds, expand fixture files in `data/`.

### Phase 2 — Cross-Exchange Validation Engine (Day 3)

**Owner focus:** the rule matrix — this is the highest-value logic for judges.

Built in [`backend/app/core/validation.py`](backend/app/core/validation.py):

- **ISIN Matching**: NSE and BSE events converted to DataFrames, joined with
  `pd.merge(..., how="outer", indicator=True)` on `isin`. The indicator lets
  us classify three cases: matched on both exchanges, NSE-only, BSE-only.
- **Rule 1 — Date Conflict**: `nse.record_date != bse.record_date` →
  `AnomalyRecord(conflict_type=DATE_MISMATCH)`. This is the state-holiday-drift
  scenario from the spec (e.g. NSE settles on the 16th, BSE on the 17th).
- **Rule 2 — Ratio Conflict**: `abs(nse.ratio - bse.ratio) > 1e-4` (float
  tolerance, not `!=`) → `AnomalyRecord(conflict_type=RATIO_MISMATCH)`.
- Matched pairs with **no** conflicts get `status=VERIFIED` on both sides —
  only `VERIFIED` events are eligible for Phase 3 simulation
  (`simulate.py` enforces this with a 409 if you try to simulate an anomaly).
- Events present on only one exchange currently pass through unflagged
  (nothing to cross-check yet) — worth a product decision on Day 3: should
  single-source events require a manual "second source" confirmation before
  they're simulation-eligible? Easy extension point in `cross_validate`.

Verify with sample data:
`curl -X POST localhost:8000/api/ingest/run -F nse_file=@data/sample_nse/nse_corporate_actions.csv -F bse_file=@data/sample_bse/bse_corporate_actions.csv`
— the fixtures are planted to produce exactly one DATE_MISMATCH and one
RATIO_MISMATCH so you can confirm the matrix end-to-end in one call.

### Phase 3 — Shadow Ledger Simulation (Day 4)

**Owner focus:** the "why this matters to a broker" logic — pre-market-open
variance detection.

Built in [`backend/app/core/simulation.py`](backend/app/core/simulation.py):

- `BrokerHolding` mock table (`backend/app/seed.py`) — client_id/isin/shares,
  seeded via `python -m app.seed`. Purely a sandbox table; nothing here
  touches a real Demat account.
- `simulate_holdings_for_event(event, holdings)`: filters holdings by
  `event.isin`, multiplies `shares * event.ratio_multiplier`, and if a
  `depository_expected_shares` map is supplied, computes `variance` against
  it — this is the "does our math match what the depository is about to
  credit" check that should run before market open.
- `apply_sign_off`: pure state transition, `PENDING_AUTHORIZATION` →
  `APPROVED`/`REJECTED`. Deliberately does **not** write back to
  `broker_holdings` — a real ledger-write job is out of scope for the MVP
  and should consume `APPROVED` `ShadowSimulationEntry` rows downstream.
- API guard: `POST /api/simulate/event/{id}` returns 409 if the event isn't
  `VERIFIED` yet — you cannot simulate an anomaly, which is the whole point
  of the human-in-the-loop model.

**Day 4 stretch goal:** wire `depository_expected_shares` to a second mock
input (a "depository credit file") so the variance column in the dashboard
has real non-zero values to demo, not just zeros.

### Phase 4 — Streamlit Exception Dashboard (Day 5 morning)

Built in [`frontend/dashboard.py`](frontend/dashboard.py), three tabs matching
the spec exactly:

1. **Upload & Run** — dual file uploaders (NSE/BSE), POSTs both to
   `/api/ingest/run` as multipart, shows a summary + raw JSON of the run.
2. **Exception Queue** — three `st.dataframe` tables: Quarantine Queue
   (`GET /api/quarantine`), Discrepancy Matrix filtered to
   `resolution_status == REQUIRES_HUMAN_REVIEW` (`GET /api/anomalies`), and
   all ingested events for context (`GET /api/events`).
3. **Simulation Viewer & Sign-Off** — dropdown of `VERIFIED` events to
   trigger simulation, then a card per pending `ShadowSimulationEntry` with
   pre/post/variance metrics and **Approve / Reject** buttons hitting
   `POST /api/simulate/signoff`.

No custom CSS, no component libraries — `st.tabs`, `st.dataframe`,
`st.metric`, `st.container(border=True)` do all the work. This is
intentional per the "logic-heavy MVP" brief; don't spend Day 5 on styling.

Set `API_BASE_URL` env var if the backend isn't on `localhost:8000`
(see `frontend/.env.example`).

### Phase 5 — Deployment (Day 5 afternoon)

Deployed via a Render **Blueprint** ([`render.yaml`](render.yaml)) that
provisions all three pieces — backend, frontend, and a free Postgres
instance — from one file, wired together automatically.

#### Render (one-click Blueprint)

1. Push this repo to GitHub (already done).
2. In the Render dashboard: **New → Blueprint**, connect this repo. Render
   reads `render.yaml` from the repo root and provisions:
   - `risk-auditor-db` — a free Postgres instance
   - `risk-auditor-backend` — the FastAPI service, with `DATABASE_URL`
     auto-injected from the Postgres instance
   - `risk-auditor-frontend` — the Streamlit dashboard, with `API_BASE_URL`
     auto-injected as the backend's private-network `host:port`
3. Click **Apply**. Both services build and deploy; no manual env var entry
   needed — the Blueprint's `fromDatabase` / `fromService` references handle
   the wiring.
4. Once live, open the frontend service's public URL to use the dashboard.

The two services talk to each other over Render's private network (free,
faster than a public hop), which is why `API_BASE_URL` resolves to a bare
`host:port` rather than a public URL — `frontend/dashboard.py` auto-prepends
`http://` if the env var has no scheme, so no manual tweaking is needed.

To redeploy after code changes, just push to `main` — Render Blueprints
auto-deploy on push by default.

#### Manual setup (alternative to Blueprint)

If you'd rather configure services by hand instead of via `render.yaml`:
- **Backend:** New → Web Service → root directory `backend`, build
  `pip install -r requirements.txt`, start
  `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Attach a Postgres
  instance and set `DATABASE_URL` to its connection string.
- **Frontend:** New → Web Service → root directory `frontend`, build
  `pip install -r requirements.txt`, start
  `streamlit run dashboard.py --server.port $PORT --server.address 0.0.0.0`.
  Set `API_BASE_URL` to the backend's public URL (include `https://`).

#### Why not Vercel

Vercel is built for static sites and serverless functions. Streamlit needs a
long-lived process holding a WebSocket connection per user, which Vercel's
serverless model doesn't support, and FastAPI here relies on a persistent
DB connection pool rather than a stateless request/response function — both
services are a better fit for Render's always-on web services.

---

## Known extension points (not yet built)

- Single-exchange-only events currently auto-pass as unflagged; consider
  requiring a second-source confirmation rule.
- No auth on the FastAPI endpoints — fine for a hackathon demo, not for
  anything beyond it.
- `apply_sign_off` doesn't write to `broker_holdings` — by design, but a
  real system needs a downstream consumer of `APPROVED` simulation rows.
- Bonus/split ratio conventions vary by real-world notice wording; the
  `action_type`-aware parsing in `_parse_ratio` is a reasonable default but
  worth validating against real NSE/BSE circular samples if you get access
  to them before the demo.

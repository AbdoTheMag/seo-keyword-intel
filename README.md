# Keyword Intelligence — SERP scraper + cluster (FastAPI + React)

Pragmatic, production-minded toolchain: defensive Google SERP scraper (Selenium headful, UA rotation, viewport jitter, human pacing, proxy support), TF-IDF → SVD → k-means clusterer with exemplars and top-terms, FastAPI backend, modern React + Vite frontend.

---

## to use the program;

1. Clone repo
2. Create and activate Python venv
3. Install backend deps
4. (Optional, recommended) install Node and chrome-for-testing
5. Configure `.env`
6. Run the backend (`uvicorn`) and frontend (`npm run dev`) in command prompt.

---

## Install (backend)

```bash
python -m venv .venv
# mac / linux
source .venv/bin/activate
# windows (powershell)
# .venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

---

## chrome-for-testing (recommended)

Use a stable official chrome binary to reduce false positives.

```bash
npm install -g @puppeteer/browsers
browsers install chrome@stable
```

---

## `.env`

Copy `examples/.env.example` → `.env` (or export env vars).

variables:

```
# API fallbacks (optional)
SERP_API_KEY=         # SerpAPI key (preferred fallback)
GOOGLE_CSE_KEY=       # Google Custom Search API key (fallback)
GOOGLE_CSE_CX=        # Custom Search Engine ID

# Chrome / driver (optional)
CHROME_BINARY_PATH=   # path to chrome-for-testing binary
CHROMEDRIVER_PATH=    # explicit chromedriver path (if needed)

# Proxy (optional, recommended for scale)
PROXY=http://user:pass@host:port

# Overrides
UA_POOL_FILE=         # optional file (one UA per line)
DEBUG_DIR=debug
LOG_DIR=logs
```

---

## Run backend

Development:

```bash
# from repo root
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

CLI mode (single-run clustering):

```bash
python -m backend.main --keywords "dog food,best dog food" --per_keyword 5
```

Notes:

`--headless` allowed but increases block risk.
Use the `--proxy` command to pass a proxy string on CLI
`--api-first` will give you SERP APIs if keys are present.

---

## Endpoints

`GET /ping`
`POST /api/scrape` - the body looks like: `{ "keyword": "..." }` - which returns the unified SERP records for one keyword.
`POST /api/cluster` - gives the full clustering payload (meta, top_terms, exemplars, results).
* `GET /download/csv` and `GET /download/json` - download latest processed exports.

---

## JSON output

Successful `/api/cluster` gives you the json with the clustering metadata, cluster contents & hits and full results array.

```

`debug_path` points to saved HTML in `debug/` when the scraper detects a block.

---

## Frontend

```bash
cd frontend
npm install
npm run dev
# open http://localhost:5173
```

Build static assets:

```bash
npm run build
```

Frontend features:

* modern responsive layout, cluster cards, exemplars, export CSV.
* charts-ready output and clearly visible silhouette/cluster stats.


---

## Proxy, headless, and blocking guidance

* **Proxy**: use residential or mobile proxy provider when scraping at scale. Pass via `PROXY` env or `--proxy` CLI. Rotate IPs periodically.
* **Headless**: its better to run headful Chrome. Headless increases detection risk.
* **Chrome-for-Testing**: reduces environment mismatches; install via `@puppeteer/browsers`.
* **Rate control**: the scraper uses human-like waits and retries; reduce concurrency and add jitter to avoid flags.

---

## Debugging blocks

1. look through `debug/<keyword>_<ts>.html`. Look for: `recaptcha`, `unusual traffic`, `cloudflare`, or blank DOM.
2. If challenge present try:
run a manual browser from the same proxy IP and confirm SERP,
change to a fresh residential IP,
enable API fallback (SERP_API_KEY or Google CSE).
3. If DOM is present but selectors failed, paste 200 lines of the debug HTML for diagnosis.

---

## notes

* For stable, high-volume pipelines use licensed SERP APIs (SerpAPI or Google CSE).
* Keep UA pool moderate and rotate per session.
* Persist `data/raw/` and `data/processed/` to durable storage if running on VM.
* Add a robust proxy-rotator and queue to scale safely.
* Add monitoring on `logs/scraper.log` and alert on spikes of `blocked` results.

---

## Testing & CI

* Unit-test `backend/clusterer.py`: top-terms, exemplar indices, silhouette fallback
* CI: run `pytest` and linting
* optionally you can add a lightweight integration test that runs cluster on a small canned JSON (no live scraping)

---

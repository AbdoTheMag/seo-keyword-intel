
# Serp-scraper
```
Google SERP scraper with block-detection and optional API fallback.
the scraper's features include:
- Headful Selenium using Chrome-for-Testing binary when available
- UA rotation, viewport randomization
- Optional residential proxy integration via env / CLI
- Human-like scrolling / waits
- Challenge detection (CAPTCHA / interstitial)
- Exponential backoff retries via tenacity
- Fallback to SerpAPI or Google CSE when blocked (if API key present)
- CLI entrypoint and JSON output
```

## Install in a python enviroment

```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
````

install chrome-for-testing:

```bash
npm install -g @puppeteer/browsers
browsers install chrome@stable
```

## Env

Copy `.env.example` → `.env` and fill keys:

```
SERP_API_KEY=
GOOGLE_CSE_KEY=
GOOGLE_CSE_CX=
CHROME_BINARY_PATH=
CHROMEDRIVER_PATH=
PROXY=
UA_POOL_FILE=
DEBUG_DIR=debug
LOG_DIR=logs
```

## CLI usage

Basic scrape:
```
python backend/scraper.py \
  --keywords-file keywords.txt \
  --out out.json
```

Limit items:
```
python backend/scraper.py \
  --keywords-file keywords.txt \
  --out out.json \
  --max-items 10
```

Use proxy:
```
python backend/scraper.py \
  --keywords-file keywords.txt \
  --out out.json \
  --proxy "http://user:pass@host:port"
```

API-first mode:
```
python backend/scraper.py --keywords-file keywords.txt --out out.json --api-first
```

Headless (higher block risk):
```
python backend/scraper.py --keywords-file keywords.txt --out out.json --headless
```

## Output

`out.json` is a list of records:
```
{
  "keyword": "...",
  "title": "...",
  "snippet": "...",
  "url": "...",
  "position": 1,
  "source": "selenium|serpapi|google_cse|blocked",
  "blocked": false,
  "debug_path": null
}
```

If blocked, `debug_path` points to saved HTML in `debug/`.

## Debug

Open `debug/*.html` to confirm recaptcha, unusual-traffic, cloudflare, or empty DOM.
Retry with residential proxy.

# requirements.txt
```
selenium>=4.22.0
webdriver-manager>=4.0.1
requests>=2.31.0
beautifulsoup4>=4.12.2
tenacity>=8.2.2
python-dotenv>=1.0.1
fake-useragent>=1.5.1
lxml>=5.2.1
urllib3>=2.2.1
```



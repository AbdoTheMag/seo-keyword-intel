"""
backend/scraper.py

Robust SERP scraper with legal, defensive measures and API fallback.
DO NOT USE this to bypass or defeat CAPTCHAs. If a challenge is detected,
the scraper writes debug HTML and optionally falls back to a legitimate SERP API.

Features:
- Headful Selenium using Chrome-for-Testing binary when available
- UA rotation, viewport randomization
- Optional residential proxy integration via env / CLI
- Human-like scrolling / waits
- Challenge detection (CAPTCHA / interstitial)
- Exponential backoff retries via tenacity
- Fallback to SerpAPI or Google CSE when blocked (if API key present)
- CLI entrypoint and JSON output
"""

import os
import sys
import time
import json
import random
import logging
import argparse
from typing import List, Dict, Optional, Tuple
from urllib.parse import quote_plus
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential_jitter, retry_if_result

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# optional: webdriver-manager fallback
try:
    from webdriver_manager.chrome import ChromeDriverManager
    _WDM_AVAILABLE = True
except Exception:
    _WDM_AVAILABLE = False

# logging
LOG_DIR = os.getenv("LOG_DIR", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(LOG_DIR, "scraper.log"))
    ]
)
logger = logging.getLogger("scraper")

# defaults
DEFAULT_TIMEOUT = int(os.getenv("SCRAPER_TIMEOUT", "15"))
DEFAULT_MAX_ITEMS = int(os.getenv("SCRAPER_MAX_ITEMS", "10"))
DEBUG_DIR = os.getenv("DEBUG_DIR", "debug")
os.makedirs(DEBUG_DIR, exist_ok=True)

# minimal UA pool; allow override from env VAR "UA_POOL_FILE" (one UA per line)
DEFAULT_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# viewport options
VIEWPORTS = [(1366,768),(1440,900),(1536,864),(1280,800),(360,800)]

# env-based keys for fallback APIs
SERP_API_KEY = os.getenv("SERP_API_KEY")            # SerpAPI key
GOOGLE_CSE_KEY = os.getenv("GOOGLE_CSE_KEY")        # Google Custom Search API key
GOOGLE_CSE_CX  = os.getenv("GOOGLE_CSE_CX")         # Google CSE CX (search engine id)

# helper: load UA pool from file if specified
def load_user_agents() -> List[str]:
    path = os.getenv("UA_POOL_FILE")
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf8") as fh:
            lines = [l.strip() for l in fh if l.strip()]
            if lines:
                return lines
    return DEFAULT_UA_POOL

UA_POOL = load_user_agents()

# -------------------------
# Detection utilities
# -------------------------
def detect_blocking(html: str) -> Tuple[bool, Optional[str]]:
    """
    Return (is_blocked, reason).
    Look for canonical signs of challenge pages and CAPTCHAs.
    """
    if not html:
        return True, "empty_html"
    lower = html.lower()
    # heuristics
    if "unusual traffic" in lower or "our systems have detected unusual traffic" in lower:
        return True, "unusual_traffic_message"
    if "recaptcha" in lower or "g-recaptcha" in lower:
        return True, "recaptcha"
    if "please enable javascript" in lower and "cloudflare" in lower:
        return True, "cloudflare_js_challenge"
    if "sorry" in lower and "our systems have detected unusual traffic" in lower:
        return True, "sorry_unusual"
    # generic “robot” or interstitial patterns
    if "are you a robot" in lower or "press and hold" in lower:
        return True, "robot_challenge"
    # no result markers
    soup = BeautifulSoup(html, "html.parser")
    # if no h3 or typical result elements present, mark suspect
    if not soup.find("h3") and not soup.select(".VwiC3b") and not soup.select("div.g"):
        return True, "no_result_nodes"
    return False, None

# -------------------------
# Chrome binary detection
# -------------------------
def find_chrome_for_testing_binary() -> Optional[str]:
    """
    If @puppeteer/browsers chrome-for-testing is installed, try to auto-locate its binary.
    Typical locations vary by OS and installation mechanism; check common env vars.
    """
    # user override
    path = os.getenv("CHROME_BINARY_PATH")
    if path and os.path.exists(path):
        return path

    # Puppeteer browsers default locations (common)
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, ".local", "share", "puppeteer", "chrome-linux64", "chrome"),
        os.path.join(home, ".local", "share", "puppeteer", "chrome-linux64", "chrome-wrapper"),
        os.path.join(home, ".puppeteer", "chrome", "chrome"),
        os.path.join(home, ".puppeteer", "chrome", "chrome.exe"),
        os.path.join(home, "AppData", "Local", "Puppeteer", "chrome-win", "chrome.exe"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None

# -------------------------
# Selenium driver factory
# -------------------------
def make_chrome_driver(headless: bool = False, proxy: Optional[str] = None,
                       user_agent: Optional[str] = None, chromedriver_path: Optional[str] = None) -> webdriver.Chrome:
    opts = Options()
    # always prefer headful for SERP
    if headless:
        # user explicitly requested headless; keep but warn
        logger.warning("Headless mode requested; headless mode increases block risk.")
        opts.add_argument("--headless=new")

    # security and detect-reducing args
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-background-networking")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument("--disable-blink-features=AutomationControlled")

    # UA
    ua = user_agent or random.choice(UA_POOL)
    opts.add_argument(f"--user-agent={ua}")

    # proxy
    if proxy:
        logger.info("Using proxy: %s", proxy)
        opts.add_argument(f"--proxy-server={proxy}")

    # window size randomized
    w, h = random.choice(VIEWPORTS)
    opts.add_argument(f"--window-size={w},{h}")

    # try to set binary
    chrome_bin = find_chrome_for_testing_binary()
    if chrome_bin:
        try:
            opts.binary_location = chrome_bin
            logger.info("Using chrome-for-testing binary: %s", chrome_bin)
        except Exception:
            pass

    # service (webdriver-manager fallback)
    service = None
    if chromedriver_path:
        service = Service(chromedriver_path)
    else:
        if _WDM_AVAILABLE:
            # webdriver-manager will download a matching chromedriver
            path = ChromeDriverManager().install()
            service = Service(path)
        else:
            # rely on PATH chromedriver
            service = Service()

    driver = webdriver.Chrome(service=service, options=opts)
    # attempt to mask webdriver property
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        })
    except Exception:
        pass
    return driver

# -------------------------
# human-like interactions
# -------------------------
def human_like_wait_short():
    time.sleep(random.uniform(0.6, 1.6))

def human_like_wait_medium():
    time.sleep(random.uniform(2.2, 4.1))

def human_scrolling(driver, passes_min=2, passes_max=4):
    try:
        height = driver.execute_script("return document.body.scrollHeight")
    except Exception:
        height = 1200
    passes = random.randint(passes_min, passes_max)
    for _ in range(passes):
        frac = random.uniform(0.25, 0.9)
        dest = int(height * frac)
        driver.execute_script(f"window.scrollTo(0, {dest});")
        human_like_wait_short()
        # small jitter scroll
        driver.execute_script("window.scrollBy(0, Math.floor((Math.random()-0.5)*120));")
        human_like_wait_short()

# -------------------------
# Extraction helpers
# -------------------------
def extract_serp_from_html(html: str, max_items: int = 10) -> List[Dict]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    # primary selectors: h3 within result containers
    # iterate over result blocks
    results = soup.select("div.g, div[data-hveid], div[data-ved]")
    pos = 1
    for g in results:
        if len(out) >= max_items:
            break
        h3 = g.find("h3")
        if not h3:
            continue
        title = h3.get_text(" ", strip=True)
        # find link
        a = g.find("a", href=True)
        url = a["href"] if a else ""
        # snippet heuristics
        snippet_el = g.select_one(".VwiC3b, .IsZvec, .aCOpRe, .s3v9rd")
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        out.append({"title": title, "snippet": snippet, "url": url, "position": pos})
        pos += 1
    # fallback: h3 tags globally
    if not out:
        for h in soup.find_all("h3"):
            if len(out) >= max_items:
                break
            a = h.find_parent("a", href=True)
            url = a["href"] if a else ""
            title = h.get_text(" ", strip=True)
            out.append({"title": title, "snippet": "", "url": url, "position": len(out)+1})
    return out

# -------------------------
# SerpAPI / Google CSE fallback
# -------------------------
def serpapi_search(key: str, query: str, max_items: int = 10) -> List[Dict]:
    # SerpAPI JSON format
    url = "https://serpapi.com/search"
    params = {"q": query, "engine": "google", "api_key": key, "num": max_items, "hl": "en", "gl": "us"}
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    out = []
    serp = data.get("organic_results") or data.get("organic_results", [])
    pos = 1
    for item in serp[:max_items]:
        title = item.get("title") or item.get("position") or ""
        snippet = item.get("snippet") or item.get("description") or ""
        link = item.get("link") or item.get("url") or ""
        out.append({"title": title, "snippet": snippet, "url": link, "position": pos})
        pos += 1
    return out

def google_cse_search(key: str, cx: str, query: str, max_items: int = 10) -> List[Dict]:
    url = "https://www.googleapis.com/customsearch/v1"
    params = {"key": key, "cx": cx, "q": query, "num": max_items}
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    out = []
    items = data.get("items", [])[:max_items]
    pos = 1
    for itm in items:
        title = itm.get("title","")
        snippet = itm.get("snippet","")
        link = itm.get("link","")
        out.append({"title": title, "snippet": snippet, "url": link, "position": pos})
        pos += 1
    return out

# -------------------------
# high-level fetch function
# -------------------------
def save_debug_html(keyword: str, html: str) -> str:
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    safe_kw = "".join([c if c.isalnum() else "_" for c in keyword])[:80]
    fname = f"{safe_kw}_{ts}.html"
    path = os.path.join(DEBUG_DIR, fname)
    with open(path, "w", encoding="utf8") as fh:
        fh.write(html)
    logger.info("Saved debug HTML to %s", path)
    return path

def _should_retry(result):
    # used by tenacity; retry if result is None or indicates blocked
    if result is None:
        return True
    # result is a tuple (success_flag, payload)
    success, payload = result
    return not success

@retry(stop=stop_after_attempt(4), wait=wait_exponential_jitter(1, 4), retry=retry_if_result(_should_retry))
def fetch_serp_headful(keyword: str, max_items: int = 10,
                       headless: bool = False, proxy: Optional[str] = None,
                       user_agent: Optional[str] = None, timeout: int = DEFAULT_TIMEOUT,
                       chromedriver_path: Optional[str] = None) -> Optional[Tuple[bool, List[Dict]]]:
    """
    Returns (success_bool, results_list) or (False, {"blocked_reason":..., "debug_path":...})
    Tenacity will retry when this returns a Falsey success flag.
    """
    logger.info("Fetching SERP headful for %s (proxy=%s)", keyword, bool(proxy))
    driver = None
    try:
        driver = make_chrome_driver(headless=headless, proxy=proxy, user_agent=user_agent, chromedriver_path=chromedriver_path)
        # random viewport via driver.set_window_size if available
        try:
            w,h = random.choice(VIEWPORTS)
            driver.set_window_size(w,h)
        except Exception:
            pass

        # navigate to neutral Google front to set cookies
        try:
            driver.get("https://www.google.com/ncr")
            human_like_wait_short()
        except Exception:
            pass

        # accept consent if present (localized)
        try:
            WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button[aria-label*='Agree'], button[aria-label*='agree'], button[jsname='higCR']"))
            ).click()
            human_like_wait_short()
        except Exception:
            # ignore if not present
            pass

        q = quote_plus(keyword)
        url = f"https://www.google.com/search?q={q}&num={max_items}&hl=en&gl=us"
        driver.get(url)
        # initial wait
        human_like_wait_medium()

        html = driver.page_source
        blocked, reason = detect_blocking(html)
        if blocked:
            debug_path = save_debug_html(keyword, html)
            logger.warning("Blocked while scraping %s: %s", keyword, reason)
            return False, {"blocked_reason": reason, "debug_path": debug_path}

        # explicit wait for result nodes up to timeout
        try:
            WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "h3"))
            )
        except Exception:
            # continue; we'll parse whatever we have
            pass

        # do human-like scrolling
        human_scrolling(driver, passes_min=2, passes_max=4)
        human_like_wait_short()

        html = driver.page_source
        blocked, reason = detect_blocking(html)
        if blocked:
            debug_path = save_debug_html(keyword, html)
            logger.warning("Blocked on second check for %s: %s", keyword, reason)
            return False, {"blocked_reason": reason, "debug_path": debug_path}

        results = extract_serp_from_html(html, max_items=max_items)
        if not results:
            debug_path = save_debug_html(keyword, html)
            logger.warning("No extractable results for %s. saved debug html", keyword)
            return False, {"blocked_reason": "no_results", "debug_path": debug_path}

        # success
        return True, results

    except Exception as e:
        logger.exception("Exception during headful fetch for %s: %s", keyword, e)
        # driver page source save if available
        html = ""
        try:
            if driver:
                html = driver.page_source
        except Exception:
            html = ""
        debug_path = save_debug_html(keyword, html) if html else None
        return False, {"blocked_reason": "exception", "error": str(e), "debug_path": debug_path}

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

# -------------------------
# public high-level function
# -------------------------
def fetch_serp(keyword: str, max_items: int = 10,
               proxy: Optional[str] = None, headless: bool = False,
               user_agent: Optional[str] = None, api_first: bool = False,
               chromedriver_path: Optional[str] = None) -> List[Dict]:
    """
    Primary entry for fetching SERP results for a single keyword.
    Returns unified list of dicts with keys: keyword,title,snippet,url,position,source,blocked,debug_path
    """
    # API-first option: if configured prefer API when keys present
    if api_first and SERP_API_KEY:
        try:
            api_rows = serpapi_search(SERP_API_KEY, keyword, max_items)
            unified = []
            for r in api_rows:
                unified.append({"keyword": keyword, "title": r["title"], "snippet": r["snippet"], "url": r["url"],
                                "position": r["position"], "source": "serpapi", "blocked": False, "debug_path": None})
            return unified
        except Exception as e:
            logger.warning("SerpAPI fallback failed: %s", e)

    # try headful Selenium mode with retries handled by tenacity wrapper
    success_flag, payload = fetch_serp_headful(keyword, max_items=max_items, headless=headless,
                                               proxy=proxy, user_agent=user_agent, chromedriver_path=chromedriver_path)
    if success_flag:
        unified = []
        for r in payload:
            unified.append({"keyword": keyword, "title": r["title"], "snippet": r["snippet"], "url": r["url"],
                            "position": r["position"], "source": "selenium", "blocked": False, "debug_path": None})
        return unified

    # if blocked, payload contains debug info and reason
    reason = payload.get("blocked_reason") if isinstance(payload, dict) else None
    debug_path = payload.get("debug_path") if isinstance(payload, dict) else None
    logger.info("Primary scraping blocked for %s: %s; debug=%s", keyword, reason, debug_path)

    # fallback: SerpAPI
    if SERP_API_KEY:
        try:
            rows = serpapi_search(SERP_API_KEY, keyword, max_items)
            unified = [{"keyword": keyword, "title": r["title"], "snippet": r["snippet"], "url": r["url"],
                        "position": r["position"], "source": "serpapi", "blocked": False, "debug_path": None} for r in rows]
            logger.info("Returned %d results from SerpAPI for %s", len(unified), keyword)
            return unified
        except Exception as e:
            logger.warning("SerpAPI error: %s", e)

    # fallback: Google Custom Search
    if GOOGLE_CSE_KEY and GOOGLE_CSE_CX:
        try:
            rows = google_cse_search(GOOGLE_CSE_KEY, GOOGLE_CSE_CX, keyword, max_items)
            unified = [{"keyword": keyword, "title": r["title"], "snippet": r["snippet"], "url": r["url"],
                        "position": r["position"], "source": "google_cse", "blocked": False, "debug_path": None} for r in rows]
            logger.info("Returned %d results from Google CSE for %s", len(unified), keyword)
            return unified
        except Exception as e:
            logger.warning("Google CSE error: %s", e)

    # if no fallback succeeded, return payload indicating block
    return [{"keyword": keyword, "title": "", "snippet": "", "url": "",
             "position": 0, "source": "blocked", "blocked": True, "debug_path": debug_path, "blocked_reason": reason}]

# -------------------------
# CLI runner
# -------------------------
def scrape_keywords_from_file(keywords_file: str, out_path: str,
                              max_items: int = DEFAULT_MAX_ITEMS, proxy: Optional[str] = None,
                              headless: bool = False, api_first: bool = False,
                              chromedriver_path: Optional[str] = None):
    with open(keywords_file, "r", encoding="utf8") as fh:
        keywords = [l.strip() for l in fh if l.strip()]
    all_rows = []
    for kw in keywords:
        logger.info("Scraping keyword: %s", kw)
        rows = fetch_serp(kw, max_items=max_items, proxy=proxy, headless=headless, api_first=api_first, chromedriver_path=chromedriver_path)
        all_rows.extend(rows)
        # polite pause between keywords
        time.sleep(random.uniform(1.5, 3.5))
    # ensure output dir
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf8") as fh:
        json.dump(all_rows, fh, indent=2, ensure_ascii=False)
    logger.info("Wrote %d records to %s", len(all_rows), out_path)


def main_cli():
    p = argparse.ArgumentParser()
    p.add_argument("--keywords-file", required=True, help="Plain text file with one keyword per line")
    p.add_argument("--out", required=True, help="Output JSON path")
    p.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS)
    p.add_argument("--proxy", default=os.getenv("PROXY"))
    p.add_argument("--headless", action="store_true", help="Run headless (increases block risk)")
    p.add_argument("--api-first", action="store_true", help="Try API fallback first if API key present")
    p.add_argument("--chromedriver-path", default=os.getenv("CHROMEDRIVER_PATH"))
    args = p.parse_args()
    scrape_keywords_from_file(args.keywords_file, args.out, max_items=args.max_items, proxy=args.proxy,
                              headless=args.headless, api_first=args.api_first, chromedriver_path=args.chromedriver_path)


if __name__ == "__main__":
    main_cli()

# backend/scraper.py
"""
Defensive SERP fetcher. Detects CAPTCHAs / challenge pages and falls back to SerpAPI or Google CSE.
Does NOT attempt to bypass or solve challenges.
"""

import os
import sys
import time
import json
import random
import logging
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential_jitter, retry_if_result

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

try:
    from webdriver_manager.chrome import ChromeDriverManager
    _WDM_AVAILABLE = True
except Exception:
    _WDM_AVAILABLE = False

LOG_DIR = os.getenv("LOG_DIR", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(level=logging.INFO,
                    handlers=[logging.StreamHandler(sys.stdout),
                                logging.FileHandler(os.path.join(LOG_DIR, "scraper.log"))])
logger = logging.getLogger("scraper")

DEBUG_DIR = os.getenv("DEBUG_DIR", "debug")
os.makedirs(DEBUG_DIR, exist_ok=True)

SERP_API_KEY = os.getenv("SERP_API_KEY")
GOOGLE_CSE_KEY = os.getenv("GOOGLE_CSE_KEY")
GOOGLE_CSE_CX = os.getenv("GOOGLE_CSE_CX")

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

VIEWPORTS = [(1366,768),(1440,900),(1536,864),(1280,800),(360,800)]

def detect_blocking(html: str) -> Tuple[bool, Optional[str]]:
    if not html:
        return True, "empty_html"
    l = html.lower()
    if "unusual traffic" in l or "our systems have detected unusual traffic" in l:
        return True, "unusual_traffic"
    if "recaptcha" in l or "g-recaptcha" in l:
        return True, "recaptcha"
    if "please enable javascript" in l and "cloudflare" in l:
        return True, "cloudflare_js_challenge"
    if "are you a robot" in l or "press and hold" in l:
        return True, "robot_challenge"
    soup = BeautifulSoup(html, "html.parser")
    if not soup.find("h3") and not soup.select(".VwiC3b") and not soup.select("div.g"):
        return True, "no_result_nodes"
    return False, None

def save_debug_html(keyword: str, html: str) -> str:
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    safe = "".join([c if c.isalnum() else "_" for c in keyword])[:60]
    fn = f"{safe}_{ts}.html"
    path = os.path.join(DEBUG_DIR, fn)
    with open(path, "w", encoding="utf8") as fh:
        fh.write(html)
    return path

def find_chrome_for_testing_binary() -> Optional[str]:
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, ".local", "share", "puppeteer", "chrome-linux64", "chrome"),
        os.path.join(home, ".puppeteer", "chrome", "chrome"),
        os.path.join(home, "AppData", "Local", "Puppeteer", "chrome-win", "chrome.exe"),
    ]
    path = os.getenv("CHROME_BINARY_PATH")
    if path and os.path.exists(path):
        return path
    for c in candidates:
        if os.path.exists(c):
            return c
    return None

def make_chrome_driver(headless: bool = False, proxy: Optional[str] = None, user_agent: Optional[str] = None, chromedriver_path: Optional[str] = None):
    opts = Options()
    if headless:
        logger.warning("Headless requested: this increases block risk.")
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-extensions")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument("--disable-blink-features=AutomationControlled")
    ua = user_agent or random.choice(UA_POOL)
    opts.add_argument(f"--user-agent={ua}")
    if proxy:
        opts.add_argument(f"--proxy-server={proxy}")
    w,h = random.choice(VIEWPORTS)
    opts.add_argument(f"--window-size={w},{h}")
    chrome_bin = find_chrome_for_testing_binary()
    if chrome_bin:
        try:
            opts.binary_location = chrome_bin
        except Exception:
            pass
    service = None
    if chromedriver_path:
        service = Service(chromedriver_path)
    else:
        if _WDM_AVAILABLE:
            path = ChromeDriverManager().install()
            service = Service(path)
        else:
            service = Service()
    driver = webdriver.Chrome(service=service, options=opts)
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        })
    except Exception:
        pass
    return driver

def extract_serp_from_html(html: str, max_items: int = 10):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    results = soup.select("div.g, div[data-hveid], div[data-ved]")
    pos = 1
    for g in results:
        if len(out) >= max_items:
            break
        h3 = g.find("h3")
        if not h3:
            continue
        title = h3.get_text(" ", strip=True)
        a = g.find("a", href=True)
        url = a["href"] if a else ""
        snippet_el = g.select_one(".VwiC3b, .IsZvec, .aCOpRe, .s3v9rd")
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        out.append({"title": title, "snippet": snippet, "url": url, "position": pos})
        pos += 1
    if not out:
        for h in soup.find_all("h3"):
            if len(out) >= max_items:
                break
            a = h.find_parent("a", href=True)
            url = a["href"] if a else ""
            title = h.get_text(" ", strip=True)
            out.append({"title": title, "snippet": "", "url": url, "position": len(out)+1})
    return out

def serpapi_search(key: str, query: str, max_items: int = 10):
    url = "https://serpapi.com/search"
    params = {"q": query, "engine": "google", "api_key": key, "num": max_items, "hl": "en", "gl": "us"}
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    out = []
    serp = data.get("organic_results") or []
    pos = 1
    for item in serp[:max_items]:
        title = item.get("title") or ""
        snippet = item.get("snippet") or item.get("description") or ""
        link = item.get("link") or item.get("url") or ""
        out.append({"title": title, "snippet": snippet, "url": link, "position": pos})
        pos += 1
    return out

def google_cse_search(key: str, cx: str, query: str, max_items: int = 10):
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

def _should_retry(result):
    if result is None:
        return True
    success, payload = result
    return not success

@retry(stop=stop_after_attempt(3), wait=wait_exponential_jitter(1,3), retry=retry_if_result(_should_retry))
def fetch_serp_headful(keyword: str, max_items: int = 10,
                       headless: bool = False, proxy: Optional[str] = None,
                       user_agent: Optional[str] = None, timeout: int = 15,
                       chromedriver_path: Optional[str] = None):
    logger.info("fetch headful: %s", keyword)
    driver = None
    try:
        driver = make_chrome_driver(headless=headless, proxy=proxy, user_agent=user_agent, chromedriver_path=chromedriver_path)
        try:
            w,h = random.choice(VIEWPORTS)
            driver.set_window_size(w,h)
        except Exception:
            pass
        try:
            driver.get("https://www.google.com/ncr")
            time.sleep(random.uniform(0.8, 1.5))
        except Exception:
            pass
        try:
            q = quote_plus(keyword)
            url = f"https://www.google.com/search?q={q}&num={max_items}&hl=en&gl=us"
            driver.get(url)
            time.sleep(random.uniform(2.5, 4.1))
        except Exception as e:
            logger.warning("driver.get failed: %s", e)
        html = driver.page_source
        blocked, reason = detect_blocking(html)
        if blocked:
            debug = save_debug_html(keyword, html)
            return False, {"blocked_reason": reason, "debug_path": debug}
        try:
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "h3")))
        except Exception:
            pass
        # scrolling
        try:
            height = driver.execute_script("return document.body.scrollHeight")
        except Exception:
            height = 1200
        for _ in range(random.randint(2,4)):
            dest = int(height * random.uniform(0.25, 0.9))
            driver.execute_script(f"window.scrollTo(0, {dest});")
            time.sleep(random.uniform(0.6, 1.2))
        time.sleep(0.8)
        html = driver.page_source
        blocked, reason = detect_blocking(html)
        if blocked:
            debug = save_debug_html(keyword, html)
            return False, {"blocked_reason": reason, "debug_path": debug}
        rows = extract_serp_from_html(html, max_items=max_items)
        if not rows:
            debug = save_debug_html(keyword, html)
            return False, {"blocked_reason": "no_results", "debug_path": debug}
        return True, rows
    except Exception as e:
        logger.exception("fetch headful exception: %s", e)
        try:
            html = driver.page_source if driver else ""
        except Exception:
            html = ""
        debug = save_debug_html(keyword, html) if html else None
        return False, {"blocked_reason": "exception", "error": str(e), "debug_path": debug}
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

def fetch_serp(keyword: str, max_items: int = 10,
               proxy: Optional[str] = None, headless: bool = False,
               user_agent: Optional[str] = None, api_first: bool = False,
               chromedriver_path: Optional[str] = None):
    # API-first option
    if api_first and SERP_API_KEY:
        try:
            rows = serpapi_search(SERP_API_KEY, keyword, max_items)
            return [{"keyword": keyword, **r, "source": "serpapi", "blocked": False}]
        except Exception:
            pass

    success, payload = fetch_serp_headful(keyword, max_items=max_items, headless=headless,
                                         proxy=proxy, user_agent=user_agent, chromedriver_path=chromedriver_path)
    if success:
        return [{"keyword": keyword, **r, "source": "selenium", "blocked": False} for r in payload]

    reason = payload.get("blocked_reason") if isinstance(payload, dict) else None
    debug = payload.get("debug_path") if isinstance(payload, dict) else None

    if SERP_API_KEY:
        try:
            rows = serpapi_search(SERP_API_KEY, keyword, max_items)
            return [{"keyword": keyword, **r, "source": "serpapi", "blocked": False} for r in rows]
        except Exception:
            pass

    if GOOGLE_CSE_KEY and GOOGLE_CSE_CX:
        try:
            rows = google_cse_search(GOOGLE_CSE_KEY, GOOGLE_CSE_CX, keyword, max_items)
            return [{"keyword": keyword, **r, "source": "google_cse", "blocked": False} for r in rows]
        except Exception:
            pass

    return [{"keyword": keyword, "title": "", "snippet": "", "url": "", "position": 0, "source": "blocked", "blocked": True, "debug_path": debug, "blocked_reason": reason}]

# backend/scraper.py
import time
import random
import json
import logging
from typing import List, Dict
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/605.1.15",
]

DEFAULT_SLEEP_RANGE = (1.0, 2.0)
MAX_RETRIES = 5

def _random_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.google.com/",
    }

def _backoff_sleep(retry_count: int):
    base = 1.0
    jitter = random.uniform(0.3, 1.0)
    sleep_time = base * (2 ** retry_count) + jitter
    logger.debug("backoff sleeping %.2fs", sleep_time)
    time.sleep(sleep_time)

def _parse_serp(html: str, max_items: int) -> List[Dict]:
    """
    Robust SERP parser with fallback selectors. Returns list of dicts with keys:
    'title', 'snippet', 'url'
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []
    seen = set()

    # Primary candidate containers: div[jscontroller], div.g, div[data-header-feature]
    containers = []
    containers.extend(soup.select("div[jscontroller]"))
    containers.extend(soup.select("div.g"))
    containers.extend(soup.select("div[data-header-feature]"))
    # final fallback: find all h3s and use parents
    if not containers:
        for h3 in soup.find_all("h3"):
            p = h3.find_parent("div")
            if p is not None:
                containers.append(p)

    for c in containers:
        # Title: h3 preferred, then div[role="heading"], then any strong text
        title_el = c.select_one("h3") or c.select_one('div[role="heading"]') or c.select_one("strong")
        if not title_el:
            continue
        title = title_el.get_text(" ", strip=True)
        if not title:
            continue

        # URL: prefer anchor closest to title, fallback to any anchor with http(s)
        url = ""
        a = None
        # search anchors in container
        for a_try in c.select("a[href]"):
            href = a_try.get("href", "")
            if href.startswith("/url?q=") or href.startswith("http"):
                a = a_try
                break
        if a:
            url = a.get("href", "")
            # clean typical /url?q= redirect
            if url.startswith("/url?q="):
                try:
                    url = url.split("/url?q=", 1)[1].split("&", 1)[0]
                except Exception:
                    pass

        # Snippet: .VwiC3b is common, also .IsZvec, .aCOpRe, .s3v9rd
        snippet_el = c.select_one(".VwiC3b") or c.select_one(".IsZvec") or c.select_one(".aCOpRe") or c.select_one(".s3v9rd")
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""

        key = (title, url, snippet)
        if key in seen:
            continue
        seen.add(key)

        # Normalize URL to empty string if not http(s)
        if not url.startswith("http"):
            url = url if url.startswith("http") else ""

        results.append({"title": title, "snippet": snippet, "url": url})
        if len(results) >= max_items:
            break

    return results

def scrape_google(keyword: str, num: int = 10, pause_range=DEFAULT_SLEEP_RANGE) -> List[Dict]:
    """
    Scrape Google SERP for a single keyword and return list of {keyword, title, snippet, url, position}
    """
    results = []
    query = quote_plus(keyword)
    url = f"https://www.google.com/search?q={query}&num={num}&hl=en"
    retries = 0

    while True:
        try:
            headers = _random_headers()
            logger.info("Fetching %s (headers: %s)", url, headers["User-Agent"])
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 429:
                if retries >= MAX_RETRIES:
                    logger.warning("Max retries reached for %s (429).", keyword)
                    break
                _backoff_sleep(retries)
                retries += 1
                continue
            resp.raise_for_status()
            html = resp.text

            parsed = _parse_serp(html, max_items=num)
            # DEBUG: if parsed is empty, save full HTML so user can inspect block page
            if not parsed:
                debug_path = f"debug_{keyword.replace(' ', '_')}.html"
                try:
                    with open(debug_path, "w", encoding="utf-8") as fh:
                        fh.write(html)
                    logger.warning("Parsed 0 results for %s. Saved raw HTML to %s", keyword, debug_path)
                except Exception:
                    logger.exception("Failed to write debug HTML for %s", keyword)


            position = 1
            for item in parsed:
                results.append({
                    "keyword": keyword,
                    "title": item.get("title") or "",
                    "snippet": item.get("snippet") or "",
                    "url": item.get("url") or "",
                    "position": position,
                })
                position += 1

            # polite random pause
            time.sleep(random.uniform(*pause_range))
            break
        except requests.RequestException as e:
            logger.warning("Request error: %s", e)
            if retries >= MAX_RETRIES:
                logger.error("Max retries, aborting for keyword: %s", keyword)
                break
            _backoff_sleep(retries)
            retries += 1
            continue

    return results

def scrape_keywords(keywords: List[str], per_keyword: int = 10, save_raw_path: str = None) -> List[Dict]:
    all_results = []
    for kw in keywords:
        try:
            res = scrape_google(kw, num=per_keyword)
            all_results.extend(res)
            # small pause between keywords
            time.sleep(random.uniform(1.0, 2.0))
        except Exception as ex:
            logger.exception("Failed scraping keyword %s: %s", kw, ex)

    if save_raw_path:
        # ensure parent exists
        try:
            with open(save_raw_path, "w", encoding="utf-8") as fh:
                json.dump(all_results, fh, ensure_ascii=False, indent=2)
        except Exception:
            logger.exception("Failed writing raw JSON to %s", save_raw_path)
    return all_results

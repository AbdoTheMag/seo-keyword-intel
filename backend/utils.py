# backend/utils.py
import os
import re
import json
from typing import List, Dict
import pandas as pd
from urllib.parse import urlparse

CLEAN_RE = re.compile(r"\s+")
URL_CLEAN_RE = re.compile(r"^/url\?q=(?P<u>https?://[^&]+)")

def safe_mkdir(path: str):
    if not path:
        return
    os.makedirs(path, exist_ok=True)

def normalize_text(s: str) -> str:
    if not s:
        return ""
    s = s.strip()
    s = CLEAN_RE.sub(" ", s)
    return s

def extract_domain(url: str) -> str:
    if not url:
        return ""
    try:
        m = URL_CLEAN_RE.match(url)
        if m:
            url = m.group("u")
        p = urlparse(url)
        host = p.netloc or ""
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""

def excerpt_for_ui(text: str, length: int = 160) -> str:
    if not text:
        return ""
    text = normalize_text(text)
    if len(text) <= length:
        return text
    # try to cut at sentence boundary
    cut = text[:length].rfind(". ")
    if cut > 0:
        return text[:cut+1]
    return text[:length].rstrip() + "…"

def results_to_dataframe(results: List[Dict]) -> pd.DataFrame:
    normalized = []
    for r in results:
        title = normalize_text(r.get("title", "") or "")
        snippet = normalize_text(r.get("snippet", "") or "")
        url = r.get("url") or ""
        m = URL_CLEAN_RE.match(url) if isinstance(url, str) else None
        if m:
            url = m.group("u")
        domain = extract_domain(url)
        normalized.append({
            "keyword": r.get("keyword", "") or "",
            "title": title,
            "snippet": snippet,
            "excerpt": excerpt_for_ui(snippet or title, length=160),
            "url": url,
            "domain": domain,
            "position": r.get("position", None),
        })
    df = pd.DataFrame(normalized)
    expected_cols = ["keyword", "title", "snippet", "excerpt", "url", "domain", "position"]
    for col in expected_cols:
        if col not in df.columns:
            df[col] = pd.Series(dtype="object")
    df = df[expected_cols]
    return df

def save_raw_json(results: List[Dict], path: str):
    if path:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(results, fh, ensure_ascii=False, indent=2)

def save_clustered_df(df, path: str):
    if path:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        df.to_csv(path, index=False)

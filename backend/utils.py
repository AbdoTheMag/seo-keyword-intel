# backend/utils.py
import os
import re
import json
from typing import List, Dict
import pandas as pd

CLEAN_RE = re.compile(r"\s+")
URL_CLEAN_RE = re.compile(r"^/url\?q=(?P<u>https?://[^&]+)")

def safe_mkdir(path):
    os.makedirs(path, exist_ok=True)

def normalize_text(s: str) -> str:
    if not s:
        return ""
    s = s.strip()
    s = CLEAN_RE.sub(" ", s)
    return s

def results_to_dataframe(results: List[Dict]) -> pd.DataFrame:
    normalized = []
    for r in results:
        title = normalize_text(r.get("title", ""))
        snippet = normalize_text(r.get("snippet", ""))
        url = r.get("url") or ""
        # handle redirect /url?q= style
        if isinstance(url, str):
            m = URL_CLEAN_RE.match(url)
            if m:
                url = m.group("u")
        normalized.append({
            "keyword": r.get("keyword", "") or "",
            "title": title,
            "snippet": snippet,
            "url": url,
            "position": r.get("position", None),
        })
    df = pd.DataFrame(normalized)
    # Ensure canonical columns exist even if empty to avoid KeyError on df["title"]
    expected_cols = ["keyword", "title", "snippet", "url", "position"]
    for col in expected_cols:
        if col not in df.columns:
            df[col] = pd.Series(dtype="object")
    # Reorder columns
    df = df[expected_cols]
    return df

def save_raw_json(results: List[Dict], path: str):
    safe_mkdir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)

def save_clustered_df(df, path: str):
    safe_mkdir(os.path.dirname(path))
    df.to_csv(path, index=False)

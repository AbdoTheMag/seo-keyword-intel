# backend/main.py
import os
import argparse
import json
import time
import random
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.utils import (
    results_to_dataframe,
    save_raw_json,
    save_clustered_df,
    safe_mkdir
)
from backend.clusterer import KeywordClusterer
from backend.scraper import fetch_serp


# -------------------------------
# FASTAPI SETUP
# -------------------------------
app = FastAPI(title="Keyword Intelligence API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# -------------------------------
# PATH SETUP
# -------------------------------
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(ROOT, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
PROCESSED_DIR = os.path.join(DATA_DIR, "processed")

RAW_PATH = os.path.join(RAW_DIR, "keyword.json")
CSV_PATH = os.path.join(PROCESSED_DIR, "clustered.csv")
JSON_OUTPUT_PATH = os.path.join(PROCESSED_DIR, "clustered.json")

safe_mkdir(RAW_DIR)
safe_mkdir(PROCESSED_DIR)


# -------------------------------
# REQUEST MODELS
# -------------------------------
class ScrapeReq(BaseModel):
    keyword: str
    country: Optional[str] = None


class ClusterReq(BaseModel):
    keywords: List[str]
    per_keyword: int = 10
    k: Optional[int] = None
    proxy: Optional[str] = None
    headless: bool = False
    api_first: bool = False
    user_agent: Optional[str] = None


# -------------------------------
# SCRAPER WRAPPER
# -------------------------------
def scrape_keywords_wrapper(
    keywords: List[str],
    per_keyword: int = 10,
    save_raw_path: Optional[str] = None,
    proxy: Optional[str] = None,
    headless: bool = False,
    api_first: bool = False,
    user_agent: Optional[str] = None,
    sleep_range: tuple = (1.5, 3.5),
    chromedriver_path: Optional[str] = None
) -> List[dict]:

    all_results = []

    for kw in keywords:
        rows = fetch_serp(
            keyword=kw,
            max_items=per_keyword,
            proxy=proxy,
            headless=headless,
            user_agent=user_agent,
            api_first=api_first,
            chromedriver_path=chromedriver_path
        )

        for r in rows:
            r.setdefault("keyword", kw)
            all_results.append({
                "keyword": r.get("keyword", kw),
                "title": r.get("title", ""),
                "snippet": r.get("snippet", ""),
                "url": r.get("url", ""),
                "position": int(r.get("position", 0)),
                "source": r.get("source", "unknown"),
                "blocked": bool(r.get("blocked", False)),
                "debug_path": r.get("debug_path"),
                "blocked_reason": r.get("blocked_reason"),
            })

        time.sleep(random.uniform(*sleep_range))

    if save_raw_path:
        save_raw_json(all_results, save_raw_path)

    return all_results


# -------------------------------
# ROUTES
# -------------------------------
@app.get("/ping")
async def ping():
    return {"status": "ok"}


@app.post("/api/scrape")
async def scrape(req: ScrapeReq):
    out = fetch_serp(
        req.keyword,
        max_items=10,
        country=req.country,
    )
    return {"ok": True, "results": out}


@app.post("/api/cluster")
async def cluster_api(req: ClusterReq):
    keywords = req.keywords

    raw_results = scrape_keywords_wrapper(
        keywords,
        per_keyword=req.per_keyword,
        proxy=req.proxy,
        headless=req.headless,
        api_first=req.api_first,
        user_agent=req.user_agent,
        save_raw_path=RAW_PATH
    )

    df = results_to_dataframe(raw_results)
    if df.empty:
        raise HTTPException(500, "No SERP results returned.")

    df["text"] = (df["title"].fillna("") + " " + df["snippet"].fillna("")).str.strip()
    texts = df["text"].tolist()
    if not any(texts):
        raise HTTPException(500, "Scraped rows contained no text.")

    clusterer = KeywordClusterer()
    clusterer.fit_transform(texts)

    k = req.k or clusterer.suggest_k()
    clustering = clusterer.cluster(texts, k=k)
    df["cluster"] = clustering["labels"]

    # build exemplars: map exemplar indexes from clustering -> full row details
    exemplars_raw = clustering.get("exemplars", {}) or {}
    exemplars = {}
    for cid, items in exemplars_raw.items():
        ex_list = []
        for it in items:
            try:
                idx = int(it.get("index", -1))
            except Exception:
                idx = -1
            if 0 <= idx < len(df):
                row = df.iloc[idx]
                ex_list.append({
                    "text": (row.get("title", "") or "") + " — " + (row.get("excerpt", "") or ""),
                    "url": row.get("url", "") or "",
                    "domain": row.get("domain", "") or "",
                    "position": int(row.get("position")) if row.get("position") not in (None, "", float("nan")) else None,
                    "distance": float(it.get("distance", 0.0) or 0.0)
                })
        exemplars[int(cid)] = ex_list

    out = {
        "meta": {
            "keywords": keywords,
            "per_keyword": int(req.per_keyword),
            "k": int(clustering.get("k", clustering.get("k", 0))),
            "cluster_labels": clustering.get("cluster_labels", {}),
            "silhouette": clustering.get("silhouette", None)
        },
        "top_terms": clustering.get("top_terms_per_cluster", {}),
        "exemplars": exemplars,
        "results": df.to_dict(orient="records"),
    }

    # persist enriched outputs
    save_clustered_df(df, CSV_PATH)
    with open(JSON_OUTPUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    return out



# -------------------------------
# CLI MODE
# -------------------------------
def run_cli_mode(keywords: List[str], per_keyword: int = 10, k: int = None,
                 proxy: Optional[str] = None, headless: bool = False,
                 api_first: bool = False, chromedriver_path: Optional[str] = None):

    raw_results = scrape_keywords_wrapper(
        keywords=keywords,
        per_keyword=per_keyword,
        save_raw_path=RAW_PATH,
        proxy=proxy,
        headless=headless,
        api_first=api_first,
        chromedriver_path=chromedriver_path,
    )

    df = results_to_dataframe(raw_results)
    if df.empty:
        raise RuntimeError("No SERP results returned.")

    df["text"] = (df["title"].fillna("") + " " + df["snippet"].fillna("")).str.strip()
    texts = df["text"].tolist()

    clusterer = KeywordClusterer()
    clusterer.fit_transform(texts)
    k = k or clusterer.suggest_k()
    clustering = clusterer.cluster(texts, k=k)

    df["cluster"] = clustering["labels"]
    save_clustered_df(df, CSV_PATH)

    out = {
        "meta": {"keywords": keywords, "per_keyword": per_keyword, "k": k},
        "top_terms": clustering["top_terms_per_cluster"],
        "results": df.to_dict(orient="records")
    }

    with open(JSON_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"Wrote raw: {RAW_PATH}")
    print(f"Wrote csv: {CSV_PATH}")
    print(f"Wrote json: {JSON_OUTPUT_PATH}")


# -------------------------------
# BOOTSTRAP
# -------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--keywords", type=str, default=None)
    parser.add_argument("--per_keyword", type=int, default=10)
    parser.add_argument("--k", type=int, default=None)
    parser.add_argument("--proxy", type=str, default=None)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--api-first", action="store_true")
    parser.add_argument("--chromedriver-path", default=None)
    parser.add_argument("--serve", action="store_true")

    args = parser.parse_args()

    if args.keywords and not args.serve:
        kws = [k.strip() for k in args.keywords.split(",") if k.strip()]
        run_cli_mode(
            kws,
            per_keyword=args.per_keyword,
            k=args.k,
            proxy=args.proxy,
            headless=args.headless,
            api_first=args.api_first,
            chromedriver_path=args.chromedriver_path
        )
    else:
        import uvicorn
        uvicorn.run(app, host=args.host, port=args.port)

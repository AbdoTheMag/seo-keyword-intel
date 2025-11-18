# backend/main.py
import os
import argparse
import json
import time
import random
from typing import List, Optional

from flask import Flask, request, jsonify, send_file, abort

# package-style imports (works with `python -m backend.main`)
from backend.utils import results_to_dataframe, save_raw_json, save_clustered_df, safe_mkdir
from backend.clusterer import KeywordClusterer
from backend.scraper import fetch_serp  # robust scraper: headful, proxy, API-fallback

# paths
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(ROOT, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
PROCESSED_DIR = os.path.join(DATA_DIR, "processed")
RAW_PATH = os.path.join(RAW_DIR, "keyword.json")
CSV_PATH = os.path.join(PROCESSED_DIR, "clustered.csv")
JSON_OUTPUT_PATH = os.path.join(PROCESSED_DIR, "clustered.json")

safe_mkdir(RAW_DIR)
safe_mkdir(PROCESSED_DIR)

app = Flask(__name__)

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok"})

@app.route("/cluster", methods=["POST"])
def cluster_endpoint():
    """
    JSON: { "keywords": [...], "per_keyword": 10, "k": optional int,
            "proxy": optional, "headless": optional bool, "api_first": optional bool }
    """
    payload = request.get_json(force=True)
    if not payload:
        return jsonify({"error": "missing json payload"}), 400

    keywords = payload.get("keywords") or payload.get("kw") or []
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]
    if not isinstance(keywords, list) or not keywords:
        return jsonify({"error": "keywords must be a non-empty list or comma-separated string"}), 400

    per_keyword = int(payload.get("per_keyword", 10))
    requested_k = payload.get("k", None)
    proxy = payload.get("proxy", None)
    headless = bool(payload.get("headless", False))
    api_first = bool(payload.get("api_first", False))
    user_agent = payload.get("user_agent", None)

    raw_results = scrape_keywords_wrapper(keywords,
                                         per_keyword=per_keyword,
                                         proxy=proxy,
                                         headless=headless,
                                         api_first=api_first,
                                         user_agent=user_agent,
                                         save_raw_path=RAW_PATH)

    save_raw_json(raw_results, RAW_PATH)

    # ETL
    df = results_to_dataframe(raw_results)
    if df.empty or ("title" not in df.columns and "snippet" not in df.columns):
        return jsonify({
            "error": "No SERP results parsed. Google may have blocked the request or the DOM selectors failed.",
            "advice": "Try lower per_keyword, add longer delays, run fewer queries, or inspect data/raw/keyword.json"
        }), 500

    df["text"] = (df["title"].fillna("") + " " + df["snippet"].fillna("")).str.strip()
    texts = df["text"].tolist()
    if not any(texts):
        return jsonify({"error": "Scraped rows contained no text for clustering."}), 500

    # Clustering
    clusterer = KeywordClusterer()
    clusterer.fit_transform(texts)
    k = int(requested_k) if requested_k else clusterer.suggest_k()
    clustering = clusterer.cluster(texts, k=k)
    df["cluster"] = clustering["labels"]

    top_terms = clustering["top_terms_per_cluster"]
    cluster_labels = clustering["cluster_labels"]

    out = {
        "meta": {
            "keywords": keywords,
            "per_keyword": per_keyword,
            "k": k,
            "cluster_labels": cluster_labels,
        },
        "top_terms": top_terms,
        "results": df.to_dict(orient="records"),
    }

    # Save exports
    save_clustered_df(df, CSV_PATH)
    with open(JSON_OUTPUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    return jsonify(out)


@app.route("/download/csv", methods=["GET"])
def download_csv():
    if not os.path.exists(CSV_PATH):
        abort(404)
    return send_file(CSV_PATH, as_attachment=True, download_name="clustered.csv")


@app.route("/download/json", methods=["GET"])
def download_json():
    if not os.path.exists(JSON_OUTPUT_PATH):
        abort(404)
    return send_file(JSON_OUTPUT_PATH, as_attachment=True, download_name="clustered.json")


# ---- Integration wrapper that uses fetch_serp ----
def scrape_keywords_wrapper(keywords: List[str],
                            per_keyword: int = 10,
                            save_raw_path: Optional[str] = None,
                            proxy: Optional[str] = None,
                            headless: bool = False,
                            api_first: bool = False,
                            user_agent: Optional[str] = None,
                            sleep_range: tuple = (1.5, 3.5),
                            chromedriver_path: Optional[str] = None) -> List[dict]:
    """
    High-level wrapper maintaining previous API shape.
    Calls fetch_serp for each keyword and normalizes output.
    """
    all_results = []
    for kw in keywords:
        # call the robust fetcher
        rows = fetch_serp(keyword=kw,
                          max_items=per_keyword,
                          proxy=proxy,
                          headless=headless,
                          user_agent=user_agent,
                          api_first=api_first,
                          chromedriver_path=chromedriver_path)

        # fetch_serp returns unified list of dicts already;
        # ensure fields exist and attach keyword if missing
        for r in rows:
            r.setdefault("keyword", kw)
            r.setdefault("position", r.get("position", 0))
            # source and blocked flags should exist from fetch_serp
            all_results.append({
                "keyword": r.get("keyword", kw),
                "title": r.get("title", "") or "",
                "snippet": r.get("snippet", "") or "",
                "url": r.get("url", "") or "",
                "position": int(r.get("position", 0) or 0),
                "source": r.get("source", "unknown"),
                "blocked": bool(r.get("blocked", False)),
                "debug_path": r.get("debug_path", None),
                "blocked_reason": r.get("blocked_reason", None),
            })

        # polite jitter between top-level keywords
        time.sleep(random.uniform(sleep_range[0], sleep_range[1]))

    # optionally persist raw results in-situ
    if save_raw_path:
        try:
            save_raw_json(all_results, save_raw_path)
        except Exception:
            pass

    return all_results


# ---- CLI mode runner ----
def run_cli_mode(keywords: List[str], per_keyword: int = 10, k: int = None,
                 proxy: Optional[str] = None, headless: bool = False,
                 api_first: bool = False, chromedriver_path: Optional[str] = None):
    raw_results = scrape_keywords_wrapper(keywords,
                                         per_keyword=per_keyword,
                                         save_raw_path=RAW_PATH,
                                         proxy=proxy,
                                         headless=headless,
                                         api_first=api_first,
                                         chromedriver_path=chromedriver_path)
    if not raw_results:
        raise RuntimeError(
            "No SERP results returned. Google likely blocked the request or DOM selectors failed. "
            "Inspect data/raw/keyword.json for raw HTML or results."
        )

    df = results_to_dataframe(raw_results)
    if df.empty:
        raise RuntimeError("No SERP results returned after normalization.")

    df["text"] = (df["title"].fillna("") + " " + df["snippet"].fillna("")).str.strip()
    texts = df["text"].tolist()
    if not any(texts):
        raise RuntimeError("Scraped rows contained no text for clustering.")

    clusterer = KeywordClusterer()
    clusterer.fit_transform(texts)
    if k is None:
        k = clusterer.suggest_k()
    clustering = clusterer.cluster(texts, k=k)
    df["cluster"] = clustering["labels"]

    save_clustered_df(df, CSV_PATH)
    out = {
        "meta": {
            "keywords": keywords,
            "per_keyword": per_keyword,
            "k": k,
        },
        "top_terms": clustering["top_terms_per_cluster"],
        "results": df.to_dict(orient="records"),
    }
    with open(JSON_OUTPUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    print(f"Wrote raw: {RAW_PATH}")
    print(f"Wrote csv: {CSV_PATH}")
    print(f"Wrote json: {JSON_OUTPUT_PATH}")


# ---- CLI / server bootstrap ----
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SEO Keyword Intelligence Tool - backend runner")
    parser.add_argument("--host", default="0.0.0.0", help="Host")
    parser.add_argument("--port", type=int, default=8000, help="Port")
    parser.add_argument("--keywords", type=str, default=None,
                        help="Comma separated keywords to run in CLI mode")
    parser.add_argument("--per_keyword", type=int, default=10)
    parser.add_argument("--k", type=int, default=None)
    parser.add_argument("--serve", action="store_true", help="Start HTTP server")
    parser.add_argument("--proxy", type=str, default=None, help="Optional proxy for scraper (http://user:pass@host:port)")
    parser.add_argument("--headless", action="store_true", help="Run headless (increases block risk)")
    parser.add_argument("--api-first", action="store_true", help="Try API fallback first if keys present")
    parser.add_argument("--chromedriver-path", default=None, help="Explicit chromedriver binary path")
    args = parser.parse_args()

    if args.keywords and not args.serve:
        kws = [k.strip() for k in args.keywords.split(",") if k.strip()]
        run_cli_mode(kws, per_keyword=args.per_keyword, k=args.k,
                     proxy=args.proxy, headless=args.headless,
                     api_first=args.api_first, chromedriver_path=args.chromedriver_path)
    else:
        # start server
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
        print(f"Starting server on {args.host}:{args.port}")
        app.run(host=args.host, port=args.port, debug=False)

# backend/main.py
import os
import argparse
import json
from typing import List
from flask import Flask, request, jsonify, send_file, abort
from werkzeug.utils import secure_filename

from scraper import scrape_keywords
from utils import results_to_dataframe, save_raw_json, save_clustered_df, safe_mkdir
from clusterer import KeywordClusterer

DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
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
    Expects JSON: { "keywords": ["a", "b", ...], "per_keyword": 10, "k": optional int }
    Returns cluster JSON with assignments and metadata.
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

    # Scrape
    raw_results = scrape_keywords(keywords, per_keyword, save_raw_path=RAW_PATH)
    save_raw_json(raw_results, RAW_PATH)

    # ETL
    df = results_to_dataframe(raw_results)
    # Defensive guard: if no rows, abort with clear message (avoid KeyError)
    if df.empty or ("title" not in df.columns and "snippet" not in df.columns):
        return jsonify({
            "error": "No SERP results parsed. Google may have blocked the request or the DOM selectors failed.",
            "advice": "Try lower per_keyword, add longer delays, run fewer queries, or inspect data/raw/keyword.json"
        }), 500

    # Build text field for clustering
    df["text"] = (df["title"].fillna("") + " " + df["snippet"].fillna("")).str.strip()
    texts = df["text"].tolist()
    if not any(texts):
        return jsonify({"error": "Scraped rows contained no text for clustering."}), 500

    # Clustering
    clusterer = KeywordClusterer()
    clusterer.fit_transform(texts)
    k = int(requested_k) if requested_k else clusterer.suggest_k()
    clustering = clusterer.cluster(texts, k=k)
    labels = clustering["labels"]
    df["cluster"] = labels

    # Top terms & labels
    top_terms = clustering["top_terms_per_cluster"]
    cluster_labels = clustering["cluster_labels"]

    # Build output structure
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

def run_cli_mode(keywords: List[str], per_keyword: int = 10, k: int = None):
    # Reuse the endpoint logic by calling functions directly
    raw_results = scrape_keywords(keywords, per_keyword, save_raw_path=RAW_PATH)
    save_raw_json(raw_results, RAW_PATH)

    df = results_to_dataframe(raw_results)
    if df.empty:
        raise RuntimeError(
            "No SERP results returned. Google likely blocked the request or DOM selectors failed. "
            "Inspect data/raw/keyword.json for raw HTML or results."
        )

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

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SEO Keyword Intelligence Tool - backend runner")
    parser.add_argument("--host", default="0.0.0.0", help="Host")
    parser.add_argument("--port", type=int, default=8000, help="Port")
    parser.add_argument("--keywords", type=str, default=None,
                        help="Comma separated keywords to run in CLI mode")
    parser.add_argument("--per_keyword", type=int, default=10)
    parser.add_argument("--k", type=int, default=None)
    parser.add_argument("--serve", action="store_true", help="Start HTTP server")
    args = parser.parse_args()

    if args.keywords and not args.serve:
        kws = [k.strip() for k in args.keywords.split(",") if k.strip()]
        run_cli_mode(kws, per_keyword=args.per_keyword, k=args.k)
    else:
        # start server
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
        print(f"Starting server on {args.host}:{args.port}")
        app.run(host=args.host, port=args.port, debug=False)

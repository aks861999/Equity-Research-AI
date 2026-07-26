import time
import requests

from ..ingestion.time_utils import to_iso

GNEWS_API_URL = "https://gnews.io/api/v4/search"

def fetch_metric_gnews(company_search_name: str, search_template: str, max_results: int, api_key: str):
    """Fetch articles for a single metric using the gnews.io API."""
    query = search_template.format(COMPANY=company_search_name)
    params = {
        "q": query,
        "lang": "en",
        "country": "us",
        "max": max_results,
        "apikey": api_key
    }
    
    try:
        resp = requests.get(GNEWS_API_URL, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json().get("articles", [])
        elif resp.status_code == 429:
            print("  [GNews] Rate limit exceeded (429). Sleeping for 60 seconds...")
            time.sleep(60)
            return fetch_metric_gnews(company_search_name, search_template, max_results, api_key)
        else:
            print(f"  [GNews] API Error: {resp.status_code} - {resp.text}")
            return []
    except Exception as e:
        print(f"  [GNews] Request failed: {e}")
        return []

def collect_all_metric_articles(company_search_name: str, metrics: list, max_per_metric: int = 1, api_key: str = None):
    """Runs every metric's search string for one company via gnews.io."""
    collected = {}
    print("\n--- GNews API Fetching ---")
    for metric in metrics:
        articles = fetch_metric_gnews(company_search_name, metric["search_template"], max_per_metric, api_key)
        print(f"[{metric['metric_id']}] Fetched: {len(articles)} articles")
        
        for a in articles:
            url = a.get("url")
            if not url:
                continue
                
            # Dedup by the real URL immediately
            if url not in collected:
                collected[url] = {
                    "title": a.get("title"),
                    "url": url,
                    "published_at": a.get("publishedAt"),
                    "matched_metrics": [],
                }
            existing_ids = {m["metric_id"] for m in collected[url]["matched_metrics"]}
            if metric["metric_id"] not in existing_ids:
                collected[url]["matched_metrics"].append({
                    "metric_id": metric["metric_id"],
                    "query_window": "gnews_api"
                })
        
        # GNews free tier allows 10 requests per minute. Wait 6 seconds between metrics.
        time.sleep(6) 
        
    print("--------------------------\n")
    return collected

# We keep this function name so main.py doesn't break, but it just passes data through now.
def decode_unique_links(collected: dict, db=None, ticker=None, decode_delay_range=(0, 0)):
    """GNews returns real URLs directly, so no decoding is needed! 
    We just format the data to match what main.py expects."""
    decoded = []
    for url, meta in collected.items():
        decoded.append({
            "title": meta["title"],
            "url": meta["url"],
            "google_link": url, # Store the real URL here for DB consistency
            "published_at": to_iso(meta["published_at"]),
            "matched_metrics": meta["matched_metrics"],
        })
    return decoded
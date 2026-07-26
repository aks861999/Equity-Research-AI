import logging
from ..config import load_companies, get_company
from ..ingestion.queries import get_unscored_articles
from ..ingestion.upsert import apply_sentiment_result
from .sentiment import FinBERTAnalyzer

logger = logging.getLogger("run_sentiment")

def run_pending_sentiments(db, batch_limit: int = 100000):
    # Initialize the model ONCE before the loop
    analyzer = FinBERTAnalyzer()
    
    # Load companies to get primary_search_name for relevance checking
    companies = {c["ticker"]: c for c in load_companies()}
    
    pending = get_unscored_articles(db, limit=batch_limit)
    logger.info("Found %d articles pending sentiment analysis.", len(pending))
    
    done = 0
    for doc in pending:
        body_text = doc.get("body_text")
        title = doc.get("title", "")
        ticker = doc.get("ticker")
        
        # Get the company's search name (e.g., "MercadoLibre") for relevance scoring
        company_name = companies.get(ticker, {}).get("primary_search_name", ticker)
        
        if not body_text:
            continue
            
        # Run FinBERT
        sentiment_payload = analyzer.score_article(title, body_text, company_name)
        
        # Save back to CouchDB
        apply_sentiment_result(db, doc["_id"], sentiment_payload)
        done += 1
        
        logger.info("[sentiment] %s -> %s (Score: %.4f, Rel: %.1f)", 
                    ticker, sentiment_payload["label"], sentiment_payload["score"], sentiment_payload["relevance"])
        
    return {"processed": len(pending), "succeeded": done}
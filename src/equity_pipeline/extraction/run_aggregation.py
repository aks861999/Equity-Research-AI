import logging
import math
import statistics
from datetime import datetime, timezone

logger = logging.getLogger("run_aggregation")

LAMBDA = 0.05  # Time-decay factor (30-day old article carries ~22% weight of today's)

def run_metric_aggregation(db):
    # 1. Ensure the metric_scores relational table exists
    with db.conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS metric_scores (
                id TEXT PRIMARY KEY,
                ticker TEXT,
                metric_id TEXT,
                weighted_sentiment FLOAT,
                dispersion FLOAT,
                volume INT,
                calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        db.conn.commit()

    # 2. Fetch all articles that have a sentiment score using raw SQL 
    # (bypassing the find() method because it doesn't support IS NOT NULL easily)
    with db.conn.cursor() as cur:
        cur.execute("""
            SELECT doc FROM articles 
            WHERE doc->>'type' = 'raw_article' 
              AND doc->'sentiment' IS NOT NULL
            LIMIT 100000
        """)
        articles = [row[0] for row in cur.fetchall()]

    logger.info("Found %d scored articles to aggregate.", len(articles))

    # 3. Group article scores by ticker and metric_id
    buckets = {}
    for doc in articles:
        ticker = doc.get("ticker")
        sentiment = doc.get("sentiment")
        if not sentiment:
            continue
            
        score = sentiment.get("score", 0.0)
        relevance = sentiment.get("relevance", 1.0)
        
        # Calculate days_ago for time-decay
        pub_date_str = doc.get("published_at")
        days_ago = 0.0
        if pub_date_str:
            try:
                pub_date = datetime.fromisoformat(pub_date_str.replace("Z", "+00:00"))
                days_ago = (datetime.now(timezone.utc) - pub_date).total_seconds() / 86400.0
                if days_ago < 0: days_ago = 0.0
            except Exception:
                pass

        decay = math.exp(-LAMBDA * days_ago)
        weight = relevance * decay

        # An article can belong to multiple metrics. Add it to each metric's bucket.
        matched_metrics = doc.get("matched_metrics", [])
        for m in matched_metrics:
            metric_id = m.get("metric_id")
            if not metric_id:
                continue
                
            key = (ticker, metric_id)
            if key not in buckets:
                buckets[key] = []
            buckets[key].append({"score": score, "weight": weight})

    # 4. Calculate aggregated values and upsert into metric_scores table
    inserted = 0
    with db.conn.cursor() as cur:
        for (ticker, metric_id), scores_list in buckets.items():
            if not scores_list:
                continue

            total_weight = sum(s["weight"] for s in scores_list)
            if total_weight == 0:
                total_weight = 1.0  # Avoid division by zero

            # Step 4 (c): Weighted Sentiment
            weighted_sentiment = sum(s["score"] * s["weight"] for s in scores_list) / total_weight
            
            # Step 5: Volume
            volume = len(scores_list)
            
            # Step 5: Dispersion (Standard Deviation of raw scores)
            raw_scores = [s["score"] for s in scores_list]
            dispersion = statistics.stdev(raw_scores) if len(raw_scores) > 1 else 0.0

            doc_id = f"{ticker}:{metric_id}"
            
            cur.execute("""
                INSERT INTO metric_scores (id, ticker, metric_id, weighted_sentiment, dispersion, volume, calculated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE 
                SET weighted_sentiment = EXCLUDED.weighted_sentiment,
                    dispersion = EXCLUDED.dispersion,
                    volume = EXCLUDED.volume,
                    calculated_at = EXCLUDED.calculated_at;
            """, (
                doc_id, ticker, metric_id, 
                round(weighted_sentiment, 4), 
                round(dispersion, 4), 
                volume, 
                datetime.now(timezone.utc)
            ))
            inserted += 1
            
        db.conn.commit()

    logger.info("Aggregated and stored %d final metric scores.", inserted)
    return {"processed_articles": len(articles), "metrics_scored": inserted}
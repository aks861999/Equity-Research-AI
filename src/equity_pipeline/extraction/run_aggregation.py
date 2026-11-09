import logging
import math
import statistics
from datetime import datetime, timezone

from ..config import load_metrics
from .metric_relevance import (WEIGHT_ABSENT, WEIGHT_IN_BODY, WEIGHT_TITLE_OR_LEDE, metric_relevance,
                               phrases_by_metric)

logger = logging.getLogger("run_aggregation")

LAMBDA = 0.05  # Time-decay factor (30-day old article carries ~22% weight of today's)

# An article with no known publish date is weighted as if this many days old. It used to count
# as published today (maximum weight), which made undated articles the most influential ones.
# 0 treats them as fresh. A weight shared by all articles of a metric cancels in the weighted
# average, so this only matters where dated and undated articles are mixed.
UNKNOWN_DATE_AGE_DAYS = 30.0


def article_age_days(published_at: str | None, now: datetime | None = None) -> tuple[float, bool]:
    """(age in days, date_is_known). A missing or unparsable date gives (UNKNOWN_DATE_AGE_DAYS, False)."""
    if not published_at:
        return UNKNOWN_DATE_AGE_DAYS, False
    try:
        published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return UNKNOWN_DATE_AGE_DAYS, False
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return max((now - published).total_seconds() / 86400.0, 0.0), True


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
              AND jsonb_typeof(doc->'sentiment') = 'object'
            LIMIT 100000
        """)
        articles = [row[0] for row in cur.fetchall()]

    logger.info("Found %d scored articles to aggregate.", len(articles))

    # 3. Group article scores by ticker and metric_id
    buckets = {}
    date_counts = {"known": 0, "unknown": 0}
    phrases = phrases_by_metric(load_metrics())
    relevance_counts = {WEIGHT_TITLE_OR_LEDE: 0, WEIGHT_IN_BODY: 0, WEIGHT_ABSENT: 0}
    for doc in articles:
        ticker = doc.get("ticker")
        sentiment = doc.get("sentiment")
        if not sentiment:
            continue
            
        score = sentiment.get("score", 0.0)
        relevance = sentiment.get("relevance", 1.0)
        
        # Calculate days_ago for time-decay (unknown dates get UNKNOWN_DATE_AGE_DAYS)
        days_ago, date_known = article_age_days(doc.get("published_at"))
        date_counts["known" if date_known else "unknown"] += 1

        decay = math.exp(-LAMBDA * days_ago)
        weight = relevance * decay

        # An article can belong to multiple metrics. Add it to each metric's bucket, weighted by
        # how much its full text is about that metric (extraction/metric_relevance.py):
        # 1.0 named in title/opening, 0.6 later in the body, 0.2 not named at all.
        matched_metrics = doc.get("matched_metrics", [])
        for m in matched_metrics:
            metric_id = m.get("metric_id")
            if not metric_id:
                continue

            metric_w = metric_relevance(doc.get("title"), doc.get("body_text"), phrases.get(metric_id))
            if metric_w in relevance_counts:
                relevance_counts[metric_w] += 1

            key = (ticker, metric_id)
            if key not in buckets:
                buckets[key] = []
            buckets[key].append({"score": score, "weight": weight * metric_w})

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
    if date_counts["unknown"]:
        logger.info("%d of %d scored articles have no publish date and were weighted as %.0f days old.",
                    date_counts["unknown"], len(articles), UNKNOWN_DATE_AGE_DAYS)
    logger.info("Article-metric pairs by body relevance: %d named in title/opening, %d later in the body, "
                "%d not named (weighted 0.2).", relevance_counts[WEIGHT_TITLE_OR_LEDE],
                relevance_counts[WEIGHT_IN_BODY], relevance_counts[WEIGHT_ABSENT])
    return {"processed_articles": len(articles), "metrics_scored": inserted,
            "articles_without_date": date_counts["unknown"],
            "metric_pairs_title_or_opening": relevance_counts[WEIGHT_TITLE_OR_LEDE],
            "metric_pairs_in_body": relevance_counts[WEIGHT_IN_BODY],
            "metric_pairs_not_named": relevance_counts[WEIGHT_ABSENT]}
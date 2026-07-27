import logging
from openai import OpenAI
import instructor
from pydantic import BaseModel, Field
from typing import List

from ..config import llm_settings

logger = logging.getLogger("run_summarization")

# 1. Define the Pydantic Schema for Structured Output
class NewsSummary(BaseModel):
    key_positives: List[str] = Field(..., description="Top 3 positive drivers from the recent news and filings.")
    key_negatives: List[str] = Field(..., description="Top 3 negative drivers or risks from the recent news and filings.")
    overall_summary: str = Field(..., description="A concise 2-sentence summary of the current news narrative.")

def extract_key_themes(db, ticker: str):
    settings = llm_settings()
    
    # 2. Patch the OpenAI client with Instructor
    client = instructor.from_openai(
        OpenAI(base_url=settings["base_url"], api_key=settings["api_key"])
    )
    
    # 3. Fetch the top 5 most recent items (News AND SEC Filings) that have body_text
    with db.conn.cursor() as cur:
        cur.execute("""
            SELECT doc->>'title', doc->>'body_text'
            FROM articles 
            WHERE doc->>'ticker' = %s 
              AND doc->>'body_text' IS NOT NULL
            ORDER BY doc->>'published_at' DESC
            LIMIT 5;
        """, (ticker,))
        articles = cur.fetchall()

    if not articles:
        logger.warning("No articles found for %s", ticker)
        return

    # 4. Format the prompt context (Full article text, no truncation)
    article_context = ""
    for i, (title, body) in enumerate(articles, 1):
        article_context += f"Document {i}:\nTitle: {title}\nText: {body}\n\n---\n\n"

    prompt = f"""
    You are an expert equity research analyst. Based on the following recent news articles and SEC filings for {ticker}, 
    identify the key positives and negatives driving the current narrative.
    
    {article_context}
    """

    logger.info("Sending %d full documents to LLM for %s...", len(articles), ticker)
    
    # 5. Call the LLM using Instructor and the Pydantic Model
    try:
        summary_payload = client.chat.completions.create(
            model=settings["model"],
            messages=[
                {"role": "system", "content": "You are an expert equity research analyst. Extract the key positives and negatives driving the current news narrative."},
                {"role": "user", "content": prompt}
            ],
            response_model=NewsSummary,
            max_retries=2
        )
        
        # 6. Print the results directly to the console
        print("\n" + "="*60)
        print(f"📊 SUMMARY FOR {ticker}")
        print("="*60)
        print(f"\n💡 OVERALL SUMMARY:\n{summary_payload.overall_summary}\n")
        
        print("✅ KEY POSITIVES:")
        for p in summary_payload.key_positives:
            print(f"  ➕ {p}")
            
        print("\n❌ KEY NEGATIVES:")
        for n in summary_payload.key_negatives:
            print(f"  ➖ {n}")
        print("="*60 + "\n")
        
    except Exception as e:
        logger.error("LLM extraction failed for %s: %s", ticker, e)

def run_all_summaries(db):
    from ..config import load_companies
    for company in load_companies():
        extract_key_themes(db, company["ticker"])
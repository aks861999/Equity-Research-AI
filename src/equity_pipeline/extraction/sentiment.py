import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification
from ..ingestion.time_utils import now_iso

class FinBERTAnalyzer:
    def __init__(self, model_name="ProsusAI/finbert"):
        print("Loading FinBERT model...")
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_name)
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model.to(self.device)
        self.model.eval()
        if self.device == "cuda":
            self.model.half()  # FP16 on GPU for speed
        self.labels = ["positive", "negative", "neutral"]

    def _chunk_text_to_ids(self, text, max_length=512, stride=50):
        tokens = self.tokenizer.encode(text, add_special_tokens=False)
        if not tokens:
            return []
        chunks, step = [], max_length - stride
        for i in range(0, len(tokens), step):
            chunk_ids = tokens[i : i + max_length - 2]
            if not chunk_ids:
                break
            chunk_ids = [self.tokenizer.cls_token_id] + chunk_ids + [self.tokenizer.sep_token_id]
            chunks.append(chunk_ids)
            if i + step >= len(tokens):
                break
        return chunks

    def _predict_sentiment_from_ids(self, chunk_ids_list, batch_size=32):
        if not chunk_ids_list:
            return []
        results = []
        for i in range(0, len(chunk_ids_list), batch_size):
            batch = chunk_ids_list[i : i + batch_size]
            max_len = max(len(ids) for ids in batch)
            input_ids = torch.full((len(batch), max_len), self.tokenizer.pad_token_id, dtype=torch.long)
            attention_mask = torch.zeros((len(batch), max_len), dtype=torch.long)
            for j, ids in enumerate(batch):
                input_ids[j, :len(ids)] = torch.tensor(ids, dtype=torch.long)
                attention_mask[j, :len(ids)] = 1
            
            input_ids = input_ids.to(self.device)
            attention_mask = attention_mask.to(self.device)
            
            with torch.inference_mode():
                logits = self.model(input_ids=input_ids, attention_mask=attention_mask).logits
                probs = torch.softmax(logits.float(), dim=-1)
            
            idx = torch.argmax(probs, dim=-1).cpu().tolist()
            pos_neg = (probs[:, 0] - probs[:, 1]).cpu().tolist()
            probs_list = probs.cpu().tolist()
            
            for k in range(len(batch)):
                results.append({"label": self.labels[idx[k]], "score": pos_neg[k], "probs": probs_list[k]})
        return results

    def calculate_relevance(self, title, body_text, company_name):
        """Step 1 of your framework: Relevance filter proxy.
        Checks if company name is in title or first 1000 chars (approx first 2 paragraphs)."""
        if not company_name:
            return 1.0 # Default to full weight if no name provided
        
        company_lower = company_name.lower()
        title_lower = (title or "").lower()
        lede_lower = (body_text or "")[:1000].lower()
        
        if company_lower in title_lower:
            return 1.0
        elif company_lower in lede_lower:
            return 0.8 # Mentioned early, high relevance
        else:
            return 0.2 # Passively mentioned further down, down-weight heavily

    def score_article(self, title, body_text, company_name):
        chunks = self._chunk_text_to_ids(body_text, self.tokenizer)
        if not chunks:
            return {
                "label": "neutral",
                "score": 0.0,
                "probs": [0.0, 0.0, 0.0],
                "n_chunks": 0,
                "relevance": 0.0,
                "scored_at": now_iso(),
                "model": "ProsusAI/finbert"
            }
        
        chunk_results = self._predict_sentiment_from_ids(chunks)
        
        # Weighted average based on chunk length
        weights = torch.tensor([len(ids) for ids in chunks], dtype=torch.float32)
        weights = weights / weights.sum()
        probs_tensor = torch.tensor([r["probs"] for r in chunk_results], dtype=torch.float32)
        agg_probs = (probs_tensor * weights.unsqueeze(1)).sum(dim=0).tolist()
        
        idx = agg_probs.index(max(agg_probs))
        relevance = self.calculate_relevance(title, body_text, company_name)
        
        return {
            "label": self.labels[idx],
            "score": agg_probs[0] - agg_probs[1],  # P(pos) - P(neg)
            "probs": agg_probs,
            "n_chunks": len(chunks),
            "relevance": relevance,
            "scored_at": now_iso(),
            "model": "ProsusAI/finbert"
        }
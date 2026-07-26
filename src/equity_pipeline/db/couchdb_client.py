"""
Thin CouchDB HTTP client.

The one non-obvious piece here is `upsert()`: instead of separate create/update
paths, every write goes through get-current-doc -> apply update_fn -> PUT,
retrying on 409 (conflict) by re-reading the latest _rev. This is what makes
re-running the ingestion pipeline safe — the same article seen again just
merges into the existing doc instead of duplicating it or failing.
"""
import requests
from requests.auth import HTTPBasicAuth


class CouchDBClient:
    def __init__(self, base_url: str, db_name: str, user: str = None, password: str = None):
        self.base_url = base_url.rstrip("/")
        self.db_name = db_name
        self.auth = HTTPBasicAuth(user, password) if user else None
        self.db_url = f"{self.base_url}/{self.db_name}"

    def ensure_db(self):
        """Create the database if it doesn't exist yet. Safe to call every run."""
        resp = requests.put(self.db_url, auth=self.auth)
        if resp.status_code not in (201, 412):  # 412 = already exists
            resp.raise_for_status()

    def get(self, doc_id: str):
        resp = requests.get(f"{self.db_url}/{doc_id}", auth=self.auth)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    def upsert(self, doc_id: str, update_fn, max_retries: int = 5):
        """
        update_fn(existing_doc_or_None) -> full new doc dict (no _id/_rev needed;
        this method sets them). Called again on every 409 retry with the freshly
        re-fetched existing doc, so update_fn must be safe to call more than once.
        """
        last_status, last_text = None, None
        for _ in range(max_retries):
            existing = self.get(doc_id)
            new_doc = update_fn(existing)
            new_doc["_id"] = doc_id
            if existing:
                new_doc["_rev"] = existing["_rev"]

            resp = requests.put(f"{self.db_url}/{doc_id}", auth=self.auth, json=new_doc)
            if resp.status_code in (200, 201, 202):
                return resp.json()
            if resp.status_code == 409:
                last_status, last_text = resp.status_code, resp.text
                continue
            resp.raise_for_status()

        raise RuntimeError(
            f"Upsert conflict retries exhausted for {doc_id}: {last_status} {last_text}"
        )

    def create_index(self, fields: list, name: str):
        resp = requests.post(
            f"{self.db_url}/_index",
            auth=self.auth,
            json={"index": {"fields": fields}, "name": name, "type": "json"},
        )
        resp.raise_for_status()
        return resp.json()

    def find(self, selector: dict, limit: int = 200, fields: list = None):
        body = {"selector": selector, "limit": limit}
        if fields:
            body["fields"] = fields
        resp = requests.post(f"{self.db_url}/_find", auth=self.auth, json=body)
        resp.raise_for_status()
        return resp.json().get("docs", [])

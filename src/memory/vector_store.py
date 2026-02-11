"""
LanceDB-backed vector store for Archi's long-term semantic memory.
Uses sentence-transformers for embeddings; persistent storage under data/vectors.
"""

import hashlib
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import lancedb
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

TABLE_NAME = "archi_memory"


def _data_dir() -> str:
    base = os.environ.get("ARCHI_ROOT", os.getcwd())
    return os.path.join(base, "data", "vectors")


class VectorStore:
    """LanceDB vector store: add memories with embeddings, search by similarity."""

    def __init__(self, data_dir: Optional[str] = None) -> None:
        if data_dir is None:
            data_dir = _data_dir()
        os.makedirs(data_dir, exist_ok=True)
        logger.info("Initializing LanceDB at %s", data_dir)
        self.db = lancedb.connect(data_dir)
        logger.info("Loading embedding model...")
        self.embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
        self._table = self._init_table()
        logger.info("Vector store initialized")

    def _init_table(self) -> Any:
        try:
            return self.db.open_table(TABLE_NAME)
        except Exception:
            logger.info("Creating new table: %s", TABLE_NAME)
            init_text = "Initialization placeholder."
            init_vec = self.embedding_model.encode([init_text])[0]
            initial = [
                {
                    "id": "_init_",
                    "text": init_text,
                    "vector": init_vec,
                    "timestamp": datetime.now().isoformat(),
                    "type": "system",
                    "metadata_json": "{}",
                }
            ]
            return self.db.create_table(TABLE_NAME, data=initial, mode="overwrite")

    def _generate_id(self, text: str) -> str:
        content = f"{text}_{datetime.now().isoformat()}"
        return hashlib.md5(content.encode()).hexdigest()

    def add_memory(
        self,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Add a memory with auto-generated embedding. Returns memory id."""
        memory_id = self._generate_id(text)
        embedding = self.embedding_model.encode([text])[0]
        meta_copy = dict(metadata or {})
        mem_type = meta_copy.pop("type", "general")
        row: Dict[str, Any] = {
            "id": memory_id,
            "text": text,
            "vector": embedding,
            "timestamp": datetime.now().isoformat(),
            "type": mem_type,
            "metadata_json": json.dumps(meta_copy),
        }
        try:
            self._table.add([row])
            logger.debug("Added memory: %s", memory_id[:8])
            return memory_id
        except Exception as e:
            logger.error("Failed to add memory: %s", e)
            raise

    def search(
        self,
        query: str,
        n_results: int = 5,
        filter_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Search for similar memories. Returns list of {text, distance, id, metadata}."""
        try:
            query_embedding = self.embedding_model.encode([query])[0]
            q = self._table.search(query_embedding).limit(n_results)
            if filter_metadata and "type" in filter_metadata:
                t = filter_metadata["type"]
                q = q.where(f"type = '{t}'")
            results = q.to_list()
            memories: List[Dict[str, Any]] = []
            for r in results:
                if r.get("id") == "_init_":
                    continue
                dist = r.get("_distance", r.get("_lance_distance", 0.0))
                meta: Dict[str, Any] = {"type": r.get("type", "general")}
                try:
                    meta.update(json.loads(r.get("metadata_json") or "{}"))
                except (json.JSONDecodeError, TypeError):
                    pass
                memories.append({
                    "text": r["text"],
                    "distance": float(dist),
                    "id": r["id"],
                    "metadata": meta,
                })
            logger.debug("Found %d relevant memories", len(memories))
            return memories
        except Exception as e:
            logger.error("Search failed: %s", e)
            return []

    def get_memory_count(self) -> int:
        """Total number of memories (excluding init placeholder)."""
        try:
            n = self._table.count_rows()
            return max(0, n - 1)
        except Exception:
            return 0

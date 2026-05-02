"""Embedding client for semantic memory retrieval.

Uses OpenAI-compatible embedding endpoints. If no key/client is available, it
returns None so memory retrieval can fall back to local text matching.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv


@dataclass
class EmbeddingConfig:
    provider: str
    model: str
    base_url: str
    api_key: Optional[str]
    dim: int


def load_embedding_config(providers_yaml: Path) -> EmbeddingConfig:
    load_dotenv()
    raw = yaml.safe_load(Path(providers_yaml).read_text(encoding="utf-8"))
    cfg = raw["embedding"]
    return EmbeddingConfig(
        provider=cfg["provider"],
        model=cfg["model"],
        base_url=cfg["base_url"],
        api_key=os.environ.get(cfg["api_key_env"]),
        dim=int(cfg.get("dim", 0)),
    )


class EmbeddingClient:
    def __init__(self, config: EmbeddingConfig):
        self.config = config
        self._client = None
        self.last_error: Optional[str] = None
        if config.api_key:
            try:
                from openai import OpenAI
                self._client = OpenAI(
                    api_key=config.api_key,
                    base_url=config.base_url,
                )
            except ImportError:
                self.last_error = "openai package is not installed"
                pass

    @property
    def is_live(self) -> bool:
        return self._client is not None

    def embed(self, text: str) -> Optional[list[float]]:
        text = (text or "").strip()
        if not text or not self._client:
            return None
        try:
            resp = self._client.embeddings.create(
                model=self.config.model,
                input=text,
            )
            self.last_error = None
            return list(resp.data[0].embedding)
        except Exception as e:
            self.last_error = str(e)
            return None

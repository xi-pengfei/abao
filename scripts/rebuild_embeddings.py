"""Build semantic indexes for existing memories.

Requires the embedding API key configured in .env, for example:
    DASHSCOPE_API_KEY=...

Run from the project root:
    python -m scripts.rebuild_embeddings
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.embedding_client import EmbeddingClient, load_embedding_config
from memory.growth_diary import GrowthDiary
from memory.layers import MemoryStore
from memory.memory_core import MemoryCore


def main() -> None:
    client = EmbeddingClient(load_embedding_config(ROOT / "config" / "providers.yaml"))
    if not client.is_live:
        print("Embedding client 不可用：请在 .env 配置 DASHSCOPE_API_KEY。")
        sys.exit(1)

    probe = client.embed("测试")
    if probe is None:
        print("Embedding API 调用失败，没有写入索引。")
        if client.last_error:
            print(f"原因：{client.last_error}")
        sys.exit(1)

    store = MemoryStore(ROOT / "data" / "memory.db")
    memory = MemoryCore(
        store,
        GrowthDiary(ROOT / "data" / "diary.jsonl"),
        embed=client.embed,
        embedding_model=client.config.model,
    )
    try:
        pending = len(store.unembedded_memories(limit=10000))
        count = memory.index_unembedded(limit=10000)
        print(f"完成：待索引 {pending} 条，新增/补齐 {count} 条 memory embedding。")
        if pending and count == 0:
            print("没有成功写入。请检查 embedding key、模型名、网络，或 API 余额/权限。")
    finally:
        memory.close()


if __name__ == "__main__":
    main()

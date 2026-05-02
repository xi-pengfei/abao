"""Rebuild structured facts from existing conversation records.

Run from the project root:
    python -m scripts.rebuild_facts
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory.facts import extract_facts
from memory.layers import MemoryStore


def main() -> None:
    store = MemoryStore(ROOT / "data" / "memory.db")
    records = store.recent_conversations(limit=10000)
    inserted = 0
    try:
        store.clear_facts()
        for record in reversed(records):
            if not record.content.startswith("[user] "):
                continue
            text = record.content[len("[user] "):]
            facts = extract_facts(text, subject="user", source_memory_id=record.id)
            for fact in facts:
                store.upsert_fact(fact)
                inserted += 1
        print(f"完成：扫描 {len(records)} 条对话，写入/更新 {inserted} 条结构化事实。")
    finally:
        store.close()


if __name__ == "__main__":
    main()

"""CLI 对话循环。

用法：
  python -m adapters.cli

退出输入 :q 或 Ctrl-D。
输入 :state 查看当前性格状态。
输入 :diary 查看最近的成长日记。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 强制 stdio 走 UTF-8，规避 Mac/Linux 上 LANG 没设好导致的中文乱码。
# 注：这只解决"输出/读入字符串编码"问题，无法阻止 Terminal.app 自身的输入法崩溃。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")

# 让 import 正确工作（从项目根运行时）
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.abao import Abao, AbaoPaths


def _print_state(abao: Abao) -> None:
    print("\n--- 性格快照 ---")
    print(abao.personality.describe_for_prompt())
    print()


def _print_diary(abao: Abao, n: int = 5) -> None:
    entries = abao.diary.recent(n)
    if not entries:
        print("\n--- 还没有日记 ---\n")
        return
    print(f"\n--- 最近 {len(entries)} 条成长日记 ---")
    for e in entries:
        print(f"  [第{e.day}天] {e.trigger_type}")
        print(f"    {e.reflection}")
        print()


def main():
    paths = AbaoPaths(
        config_dir=ROOT / "config",
        data_dir=ROOT / "data",
    )
    abao = Abao(paths)

    print("阿宝 已就位。输入 :q 退出，:state 看性格，:diary 看日记。")
    print(f"LLM live: {abao.primary_llm.is_live}")
    print()

    try:
        while True:
            try:
                line = input("你 > ").strip()
            except EOFError:
                print()
                break
            if not line:
                continue
            if line in (":q", ":quit", "exit"):
                break
            if line == ":state":
                _print_state(abao)
                continue
            if line == ":diary":
                _print_diary(abao)
                continue

            reply = abao.converse(line, speaker="user")
            print(f"\n阿宝 > {reply}\n")
    finally:
        abao.shutdown()
        print("（阿宝安静下来。下次再见。）")


if __name__ == "__main__":
    main()

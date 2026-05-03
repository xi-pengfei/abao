"""LLM 客户端：OpenAI 兼容接口。

支持 DeepSeek / Qwen / OpenAI / 任何 OpenAI 兼容的服务，
通过 providers.yaml 配置切换。

设计原则：
  - 不在这一层做 prompt 工程
  - 失败优雅降级（返回 None，让上层决定怎么办）
  - 支持 stub 模式（无 key 时也能运行，便于开发和测试）
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


@dataclass
class LLMConfig:
    provider: str
    model: str
    base_url: str
    api_key: Optional[str]
    temperature: float = 0.7
    max_tokens: int = 800
    request_timeout: float = 45.0


def _load_env(env_path: Optional[Path] = None) -> None:
    """加载 .env 到进程环境。"""
    if env_path and env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv()


def load_config(providers_yaml: Path, role: str = "primary") -> LLMConfig:
    """从 providers.yaml 读 LLM 配置。role: primary | diary"""
    _load_env()
    raw = yaml.safe_load(Path(providers_yaml).read_text(encoding="utf-8"))
    cfg = raw["llm"][role]
    api_key = os.environ.get(cfg["api_key_env"])
    return LLMConfig(
        provider=cfg["provider"],
        model=cfg["model"],
        base_url=cfg["base_url"],
        api_key=api_key,
        temperature=cfg.get("temperature", 0.7),
        max_tokens=cfg.get("max_tokens", 800),
        request_timeout=cfg.get("request_timeout", 45.0),
    )


class LLMClient:
    """OpenAI 兼容客户端。"""

    def __init__(self, config: LLMConfig):
        self.config = config
        self._client = None
        if config.api_key:
            try:
                from openai import OpenAI
                self._client = OpenAI(
                    api_key=config.api_key,
                    base_url=config.base_url,
                    timeout=config.request_timeout,
                )
            except ImportError:
                pass

    @property
    def is_live(self) -> bool:
        return self._client is not None

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Optional[str]:
        """发起一次对话补全。

        失败返回 None。上层负责降级（比如成长日记缺反思时填占位）。
        """
        if not self._client:
            return None
        try:
            resp = self._client.chat.completions.create(
                model=self.config.model,
                messages=messages,
                temperature=temperature if temperature is not None else self.config.temperature,
                max_tokens=max_tokens if max_tokens is not None else self.config.max_tokens,
            )
            return resp.choices[0].message.content
        except Exception as exc:
            logger.warning("LLM chat failed: %s", exc)
            return None

    def complete(self, prompt: str, **kwargs) -> Optional[str]:
        """便捷接口：单 user prompt 补全。"""
        return self.chat([{"role": "user", "content": prompt}], **kwargs)

    def stream_chat(
        self,
        messages: list[dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Iterator[str]:
        """Stream chat completion deltas.

        If the live client is unavailable, yields nothing; callers decide the
        fallback behavior.
        """
        if not self._client:
            return
        try:
            stream = self._client.chat.completions.create(
                model=self.config.model,
                messages=messages,
                temperature=temperature if temperature is not None else self.config.temperature,
                max_tokens=max_tokens if max_tokens is not None else self.config.max_tokens,
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except Exception as exc:
            logger.warning("LLM stream failed: %s", exc)
            return

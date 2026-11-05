"""Provider routing shared by harness adapters.

Campaign models are OpenRouter ids by default, including ``openai/...`` aliases.
Direct OpenAI is opt-in with ``EVMBENCH_MODEL_PROVIDER=openai`` so a namespaced
model is never silently sent to the wrong provider.
"""

from __future__ import annotations

import os

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENAI_BASE_URL = "https://api.openai.com/v1"

DIRECT_OPENAI = "openai"
OPENROUTER = "openrouter"


def model_provider() -> str:
    value = (os.getenv("EVMBENCH_MODEL_PROVIDER") or OPENROUTER).strip().lower()
    if value not in (DIRECT_OPENAI, OPENROUTER):
        raise ValueError(
            f"EVMBENCH_MODEL_PROVIDER must be 'openai' or 'openrouter', got {value!r}"
        )
    return value


def is_direct_openai() -> bool:
    return model_provider() == DIRECT_OPENAI


__all__ = [
    "DIRECT_OPENAI",
    "OPENAI_BASE_URL",
    "OPENROUTER",
    "OPENROUTER_BASE_URL",
    "is_direct_openai",
    "model_provider",
]

"""Provider abstraction enforcing JSON Schema at the model boundary."""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class LLMCall:
    provider: str
    model: str
    duration_ms: int
    cost_usd: float | None
    request_id: str | None


class StructuredProvider(Protocol):
    def generate(
        self,
        *,
        system: str,
        prompt: str,
        output_type: type[T],
    ) -> tuple[T, LLMCall]: ...


class ClaudeCLIProvider:
    """Authenticated local Claude CLI with tools disabled and schema required."""

    def __init__(
        self,
        *,
        model: str = "sonnet",
        max_budget_usd: float = 0.25,
        timeout_sec: float = 180,
    ):
        self.model = model
        self.max_budget_usd = max_budget_usd
        self.timeout_sec = timeout_sec

    def generate(
        self,
        *,
        system: str,
        prompt: str,
        output_type: type[T],
    ) -> tuple[T, LLMCall]:
        schema = output_type.model_json_schema()
        command = [
            "claude", "--print", "--output-format", "json",
            "--no-session-persistence", "--safe-mode", "--tools", "",
            "--model", self.model,
            "--max-budget-usd", str(self.max_budget_usd),
            "--system-prompt", system,
            "--json-schema", json.dumps(schema, ensure_ascii=False),
            prompt,
        ]
        started = time.monotonic()
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=self.timeout_sec,
        )
        duration_ms = round((time.monotonic() - started) * 1000)
        if result.returncode:
            raise RuntimeError(
                f"Claude CLI 调用失败 ({result.returncode}): {result.stderr[-2000:]}"
            )
        wrapper = json.loads(result.stdout)
        payload = wrapper.get("structured_output")
        if payload is None:
            raw = wrapper.get("result", wrapper)
            payload = json.loads(raw) if isinstance(raw, str) else raw
        parsed = output_type.model_validate(payload)
        return parsed, LLMCall(
            provider="claude-cli",
            model=self.model,
            duration_ms=duration_ms,
            cost_usd=wrapper.get("total_cost_usd"),
            request_id=wrapper.get("session_id"),
        )


__all__ = ["ClaudeCLIProvider", "LLMCall", "StructuredProvider"]

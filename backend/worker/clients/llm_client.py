"""Eden AI LLM HTTP client (OpenAI-compatible chat completions) with structured JSON output.

Reads API key and endpoint settings from environment variables / ``Settings`` and supports
an offline mock mode for tests and CI (no real API key required).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

DEFAULT_EDENAI_BASE_URL = "https://api.edenai.run/v3"
DEFAULT_LLM_MODEL = "deepinfra/Qwen/Qwen3-VL-30B-A3B-Instruct"  # model z otwartymi wagami


class StructuredOutputError(ValueError):
    """Raised when the LLM fails to return JSON conforming to the required schema."""


@dataclass(frozen=True)
class LLMConfig:
    """Configuration for connecting to the Eden AI API or offline mock mode."""

    model: str = DEFAULT_LLM_MODEL
    api_key: str = field(default="", repr=False)
    base_url: str = DEFAULT_EDENAI_BASE_URL
    temperature: float = 0.0
    timeout_seconds: float = 30.0
    max_retries: int = 2
    offline: bool = True


def _check_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def validate_json_schema(data: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Validate JSON data against a lightweight JSON Schema subset.

    Returns a list of human-readable validation errors (empty if valid).
    """
    errors: list[str] = []

    expected_type = schema.get("type")
    if expected_type is not None:
        allowed_types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(_check_type(data, t) for t in allowed_types):
            errors.append(f"{path}: expected type {expected_type}, got {type(data).__name__}")
            return errors

    if "enum" in schema and data not in schema["enum"]:
        errors.append(f"{path}: value {data!r} not in allowed enum {schema['enum']}")

    if isinstance(data, str):
        min_len = schema.get("minLength")
        max_len = schema.get("maxLength")
        if min_len is not None and len(data) < min_len:
            errors.append(f"{path}: string shorter than minLength={min_len}")
        if max_len is not None and len(data) > max_len:
            errors.append(f"{path}: string longer than maxLength={max_len}")

    if isinstance(data, (int, float)) and not isinstance(data, bool):
        minimum = schema.get("minimum")
        if minimum is not None and data < minimum:
            errors.append(f"{path}: value {data} < minimum={minimum}")

    if isinstance(data, dict):
        for req_key in schema.get("required", []):
            if req_key not in data:
                errors.append(f"{path}: missing required key '{req_key}'")
        properties = schema.get("properties", {})
        for key, prop_schema in properties.items():
            if key in data:
                errors.extend(validate_json_schema(data[key], prop_schema, f"{path}.{key}"))

    if isinstance(data, list):
        min_items = schema.get("minItems")
        if min_items is not None and len(data) < min_items:
            errors.append(f"{path}: array length {len(data)} < minItems={min_items}")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict) and item_schema:
            for idx, item in enumerate(data):
                errors.extend(validate_json_schema(item, item_schema, f"{path}[{idx}]"))

    return errors


def _extract_json_text(raw: str) -> str:
    """Strip optional markdown code fences around a JSON payload."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _build_endpoint_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


class LLMClient:
    """HTTP client for Eden AI chat completions with strict JSON schema output."""

    def __init__(
        self,
        config: LLMConfig | None = None,
        *,
        transport: Callable[[str, str, int], str] | None = None,
    ) -> None:
        self.config = config or LLMConfig(
            model=os.getenv("LLM_MODEL", DEFAULT_LLM_MODEL),
            api_key=os.getenv("EDENAI_API_KEY", ""),
            base_url=os.getenv("EDENAI_BASE_URL", DEFAULT_EDENAI_BASE_URL),
            timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
            offline=os.getenv("LLM_DEMO_MODE", "true").lower() != "false",
        )
        self._transport = transport

    def _call_http(self, prompt: str, system_prompt: str) -> str:
        if not self.config.api_key.strip():
            raise StructuredOutputError("Missing EDENAI_API_KEY for Eden AI API call.")

        url = _build_endpoint_url(self.config.base_url)
        parsed_url = urllib_parse.urlparse(url)
        if parsed_url.scheme not in {"http", "https"}:
            raise StructuredOutputError(
                f"Unsupported Eden AI URL scheme: {parsed_url.scheme!r} (only http/https allowed)"
            )

        payload: dict[str, Any] = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
        }

        body = json.dumps(payload).encode("utf-8")
        req = urllib_request.Request(  # noqa: S310
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib_request.urlopen(  # noqa: S310
                req, timeout=self.config.timeout_seconds
            ) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib_error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise StructuredOutputError(f"Eden AI HTTP call failed: {exc}") from exc

        choices = data.get("choices") or []
        if not choices:
            raise StructuredOutputError("Empty choices in Eden AI response")
        return str(choices[0].get("message", {}).get("content", ""))

    def generate_structured(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        system_prompt: str = "Zwracaj wyłącznie poprawny JSON zgodny ze schematem.",
        mock_response_fn: Callable[[str], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Request JSON from Eden AI, validate against schema, retry or reject."""
        attempts = self.config.max_retries + 1
        last_errors: list[str] = []
        current_prompt = prompt

        for attempt in range(attempts):
            if self._transport is not None:
                raw_text = self._transport(current_prompt, system_prompt, attempt)
            elif self.config.offline:
                if mock_response_fn is None:
                    raise StructuredOutputError(
                        "Offline mode requires mock_response_fn or custom transport."
                    )
                raw_text = json.dumps(mock_response_fn(self.config.model), ensure_ascii=False)
            else:
                raw_text = self._call_http(current_prompt, system_prompt)

            try:
                parsed = json.loads(_extract_json_text(raw_text))
            except json.JSONDecodeError as exc:
                last_errors = [f"Invalid JSON: {exc}"]
            else:
                errors = validate_json_schema(parsed, schema)
                if not errors and isinstance(parsed, dict):
                    return parsed
                last_errors = errors or ["Top-level JSON must be an object"]

            error_summary = "; ".join(last_errors)
            current_prompt = (
                f"{prompt}\n\nPoprzednia odpowiedź była niezgodna ze schematem: "
                f"{error_summary}. Popraw i zwróć wyłącznie poprawny JSON."
            )

        raise StructuredOutputError(
            f"LLM ({self.config.model}) failed schema validation after "
            f"{attempts} attempts: {'; '.join(last_errors)}"
        )

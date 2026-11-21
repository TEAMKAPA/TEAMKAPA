"""Local LLM HTTP client (Ollama / vLLM / OpenAI-compatible) with structured output.

Supports PLLuM and Bielik model configurations and an offline mock mode for tests/CI.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import os
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

MODEL_PRESETS: dict[str, dict[str, str]] = {
    "pllum": {
        "name": "PLLuM",
        "model_id": "pllum:12b-instruct",
        "provider": "ollama",
    },
    "bielik": {
        "name": "Bielik",
        "model_id": "bielik:11b-v2.3-instruct",
        "provider": "ollama",
    },
}


class StructuredOutputError(ValueError):
    """Raised when the LLM fails to return JSON conforming to the required schema."""


@dataclass(frozen=True)
class LLMConfig:
    """Configuration for connecting to a local LLM endpoint or offline mock."""

    model: str = "pllum"
    base_url: str = "http://localhost:11434"
    api_format: str = "ollama"  # "ollama" or "openai"
    temperature: float = 0.0
    timeout_seconds: float = 30.0
    max_retries: int = 2
    offline: bool = True

    @property
    def canonical_model(self) -> str:
        key = self.model.strip().lower()
        if key in MODEL_PRESETS:
            return MODEL_PRESETS[key]["model_id"]
        return self.model

    @property
    def model_family(self) -> str:
        lower = self.model.strip().lower()
        if "bielik" in lower:
            return "bielik"
        if "pllum" in lower:
            return "pllum"
        return lower


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


def validate_json_schema(
    data: Any, schema: dict[str, Any], path: str = "$"
) -> list[str]:
    """Validate JSON data against a lightweight JSON Schema subset.

    Returns a list of human-readable validation errors (empty if valid).
    """
    errors: list[str] = []

    expected_type = schema.get("type")
    if expected_type is not None:
        allowed_types = (
            expected_type if isinstance(expected_type, list) else [expected_type]
        )
        if not any(_check_type(data, t) for t in allowed_types):
            errors.append(
                f"{path}: expected type {expected_type}, got {type(data).__name__}"
            )
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
                errors.extend(
                    validate_json_schema(data[key], prop_schema, f"{path}.{key}")
                )

    if isinstance(data, list):
        min_items = schema.get("minItems")
        if min_items is not None and len(data) < min_items:
            errors.append(f"{path}: array length {len(data)} < minItems={min_items}")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict) and item_schema:
            for idx, item in enumerate(data):
                errors.extend(
                    validate_json_schema(item, item_schema, f"{path}[{idx}]")
                )

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


class LLMClient:
    """HTTP client for local PLLuM / Bielik models with strict JSON schema output."""

    def __init__(
        self,
        config: LLMConfig | None = None,
        *,
        transport: Callable[[str, str, int], str] | None = None,
    ) -> None:
        self.config = config or LLMConfig(
            model=os.getenv("LOCAL_LLM_MODEL", "pllum"),
            base_url=os.getenv("LOCAL_LLM_URL", "http://localhost:11434"),
            api_format=os.getenv("LOCAL_LLM_API_FORMAT", "ollama"),
            offline=os.getenv("LOCAL_LLM_OFFLINE", "true").lower() != "false",
        )
        self._transport = transport

    def _call_http(self, prompt: str, system_prompt: str) -> str:
        base = self.config.base_url.rstrip("/")
        if self.config.api_format == "openai":
            url = f"{base}/v1/chat/completions"
            payload: dict[str, Any] = {
                "model": self.config.canonical_model,
                "temperature": self.config.temperature,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
            }
        else:
            url = f"{base}/api/chat"
            payload = {
                "model": self.config.canonical_model,
                "stream": False,
                "format": "json",
                "options": {"temperature": self.config.temperature},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
            }

        body = json.dumps(payload).encode("utf-8")
        req = urllib_request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib_request.urlopen(
                req, timeout=self.config.timeout_seconds
            ) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib_error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise StructuredOutputError(f"Local LLM HTTP call failed: {exc}") from exc

        if self.config.api_format == "openai":
            choices = data.get("choices") or []
            if not choices:
                raise StructuredOutputError("Empty choices in OpenAI response")
            return str(choices[0].get("message", {}).get("content", ""))

        return str(data.get("message", {}).get("content", ""))

    def generate_structured(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        system_prompt: str = "Zwracaj wyłącznie poprawny JSON zgodny ze schematem.",
        mock_response_fn: Callable[[str], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Request JSON from the LLM, validate against schema, retry or reject."""
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
                raw_text = json.dumps(
                    mock_response_fn(self.config.model_family), ensure_ascii=False
                )
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
            f"LLM ({self.config.canonical_model}) failed schema validation after "
            f"{attempts} attempts: {'; '.join(last_errors)}"
        )

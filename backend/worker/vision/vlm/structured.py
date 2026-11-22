"""Odpowiedź strukturalna VLM: schemat w żądaniu, walidacja Pydantic i jedna ponowna próba."""

import json
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from backend.worker.vision.vlm.client import (
    VlmClient,
    VlmImage,
    VlmInvalidResponse,
    VlmRequest,
    VlmUsage,
)
from backend.worker.vision.vlm.prompts import Prompt
from backend.worker.vision.vlm.schemas import json_schema

RETRY_NOTE = (
    "\n\nYour previous answer was not valid JSON matching the schema. "
    "Answer again with a single JSON object that matches the schema exactly."
)


@dataclass(frozen=True)
class Answer[M: BaseModel]:
    value: M
    attempts: int
    usage: VlmUsage
    cached: bool


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines)
    return stripped


def ask[M: BaseModel](
    client: VlmClient,
    task: str,
    prompt: Prompt,
    values: dict[str, str],
    image: VlmImage,
    model: type[M],
) -> Answer[M]:
    """Odpowiedź zgodna z ``model``; po dwóch nieudanych próbach ``VlmInvalidResponse``.

    ``VlmUnavailable`` (błąd API, limit czasu) przechodzi do wywołującego bez ponowienia tutaj
    (ponowienia transportowe robi klient).
    """
    schema = json_schema(model)
    text = prompt.render({**values, "SCHEMA": json.dumps(schema, ensure_ascii=False)})
    prompt_tokens = completion_tokens = 0
    cached = True
    for attempt in (1, 2):
        request = VlmRequest(
            task=task,
            prompt=text if attempt == 1 else text + RETRY_NOTE,
            prompt_version=prompt.version,
            image=image,
            schema_name=task,
            schema=schema,
        )
        response = client.complete(request)
        prompt_tokens += response.usage.prompt_tokens
        completion_tokens += response.usage.completion_tokens
        cached = cached and response.cached
        try:
            value = model.model_validate_json(_strip_fences(response.content))
        except ValidationError:
            continue
        return Answer(value, attempt, VlmUsage(prompt_tokens, completion_tokens), cached)
    raise VlmInvalidResponse(f"{task}: odpowiedź niezgodna ze schematem po ponowieniu")

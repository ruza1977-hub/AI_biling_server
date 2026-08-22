from __future__ import annotations

from dataclasses import dataclass
import httpx

from .config import settings


class OpenAIGatewayError(Exception):
    def __init__(self, status_code: int, code: str, detail: str):
        self.status_code = status_code
        self.code = code
        self.detail = detail
        super().__init__(detail)


@dataclass(slots=True)
class OpenAIResult:
    response_id: str | None
    output_text: str
    input_tokens: int
    output_tokens: int


def _extract_output_text(data: dict) -> str:
    parts: list[str] = []
    for item in data.get("output", []) or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content", []) or []:
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                parts.append(content["text"])
    return "".join(parts)


def create_response(*, model: str, input_text: str, instructions: str | None, max_output_tokens: int) -> OpenAIResult:
    if not settings.openai_api_key:
        raise OpenAIGatewayError(503, "OPENAI_KEY_NOT_CONFIGURED", "OPENAI_API_KEY is not configured on the server")

    payload: dict = {
        "model": model,
        "input": input_text,
        "max_output_tokens": max_output_tokens,
        "store": False,
    }
    if instructions:
        payload["instructions"] = instructions

    try:
        with httpx.Client(timeout=settings.openai_timeout_seconds) as client:
            response = client.post(
                f"{settings.openai_base_url.rstrip('/')}/responses",
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except httpx.TimeoutException as exc:
        raise OpenAIGatewayError(504, "OPENAI_TIMEOUT", "OpenAI request timed out") from exc
    except httpx.HTTPError as exc:
        raise OpenAIGatewayError(502, "OPENAI_NETWORK_ERROR", "Unable to reach OpenAI") from exc

    if response.status_code >= 400:
        code = "OPENAI_ERROR"
        detail = response.text[:1200]
        try:
            body = response.json()
            err = body.get("error") or {}
            code = str(err.get("code") or err.get("type") or code)
            detail = str(err.get("message") or detail)
        except ValueError:
            pass
        # Do not expose credentials/headers; only OpenAI's sanitized error message.
        raise OpenAIGatewayError(response.status_code, code, detail)

    data = response.json()
    usage = data.get("usage") or {}
    return OpenAIResult(
        response_id=data.get("id"),
        output_text=_extract_output_text(data),
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
    )

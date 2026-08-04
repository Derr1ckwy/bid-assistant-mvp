from __future__ import annotations

import json
import re
from typing import Any

import requests

from bid_assistant.config import Settings


class LLMError(RuntimeError):
    pass


def extract_json_object(value: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", value.strip(), flags=re.IGNORECASE)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise LLMError("模型没有返回 JSON 对象。")
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"模型 JSON 无法解析：{exc}") from exc


class OpenAICompatibleClient:
    def __init__(self, settings: Settings):
        self.base_url = settings.llm_base_url
        self.api_key = settings.llm_api_key
        self.model = settings.llm_model
        self.timeout = settings.llm_timeout_seconds

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def is_available(self) -> bool:
        try:
            response = requests.get(f"{self.base_url}/models", headers=self.headers, timeout=3)
            return response.ok
        except requests.RequestException:
            return False

    def chat(self, messages: list[dict[str, str]], *, json_mode: bool = False) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=self.headers,
                json=payload,
                timeout=self.timeout,
            )
            if response.status_code >= 400 and json_mode:
                payload.pop("response_format", None)
                response = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers=self.headers,
                    json=payload,
                    timeout=self.timeout,
                )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except (requests.RequestException, KeyError, ValueError) as exc:
            raise LLMError(f"模型调用失败：{exc}") from exc

    def chat_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        return extract_json_object(self.chat(messages, json_mode=True))

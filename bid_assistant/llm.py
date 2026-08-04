from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import requests

from bid_assistant.config import Settings


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMHealth:
    available: bool
    status: str
    message: str
    models: tuple[str, ...] = ()


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
        self.chunk_chars = settings.llm_chunk_chars
        self.max_chunks = settings.llm_max_chunks

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _response_error(response: requests.Response) -> str:
        try:
            payload = response.json()
            error = payload.get("error", payload)
            if isinstance(error, dict):
                detail = error.get("message") or error.get("detail")
                if detail:
                    return str(detail)[:300]
        except (ValueError, AttributeError):
            pass
        return response.text.strip()[:300] or "未返回错误详情"

    def check_health(self) -> LLMHealth:
        try:
            response = requests.get(f"{self.base_url}/models", headers=self.headers, timeout=5)
        except requests.ConnectionError:
            return LLMHealth(
                False,
                "unreachable",
                f"无法连接模型服务 {self.base_url}。本地 Ollama 用户请先启动服务；云端接口请检查地址和网络。",
            )
        except requests.Timeout:
            return LLMHealth(False, "timeout", f"连接模型服务超时：{self.base_url}。")
        except requests.RequestException as exc:
            return LLMHealth(False, "request_error", f"模型连接检查失败：{type(exc).__name__}。")

        if response.status_code in (401, 403):
            return LLMHealth(False, "unauthorized", "模型接口拒绝访问，请检查 API Key 和账号权限。")
        if response.status_code >= 400:
            return LLMHealth(
                False,
                "http_error",
                f"模型接口返回 HTTP {response.status_code}：{self._response_error(response)}",
            )
        try:
            payload = response.json()
            models = tuple(
                str(item["id"])
                for item in payload.get("data", [])
                if isinstance(item, dict) and item.get("id")
            )
        except (ValueError, AttributeError, TypeError):
            return LLMHealth(False, "invalid_response", "模型接口可访问，但 /models 返回的不是有效 JSON。")
        if models and self.model not in models:
            preview = "、".join(models[:6])
            return LLMHealth(
                False,
                "model_missing",
                f"模型接口可访问，但未找到 {self.model}。当前可用模型：{preview}。",
                models,
            )
        return LLMHealth(True, "ok", f"模型接口可用：{self.model}", models)

    def is_available(self) -> bool:
        return self.check_health().available

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
            if response.status_code in (400, 422) and json_mode:
                payload.pop("response_format", None)
                response = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers=self.headers,
                    json=payload,
                    timeout=self.timeout,
                )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise LLMError("模型返回了空内容。")
            return content
        except requests.ConnectionError as exc:
            raise LLMError(
                f"无法连接模型服务 {self.base_url}。请先在左侧“模型配置”中检测连接。"
            ) from exc
        except requests.Timeout as exc:
            raise LLMError(f"模型请求超过 {self.timeout} 秒未完成，请缩小分段或提高超时时间。") from exc
        except requests.HTTPError as exc:
            response = exc.response
            if response is None:
                raise LLMError("模型接口返回 HTTP 错误。") from exc
            raise LLMError(
                f"模型接口返回 HTTP {response.status_code}：{self._response_error(response)}"
            ) from exc
        except (requests.RequestException, KeyError, ValueError, TypeError) as exc:
            raise LLMError(f"模型响应格式异常：{type(exc).__name__}。") from exc

    def chat_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        return extract_json_object(self.chat(messages, json_mode=True))

from __future__ import annotations

import base64
from pathlib import Path
from urllib.parse import urlparse

import requests


class ImageGenerationError(RuntimeError):
    """Raised when a cloud image request cannot be completed safely."""


def _response_error(response: requests.Response) -> str:
    try:
        payload = response.json()
        error = payload.get("error", payload)
        if isinstance(error, dict):
            message = error.get("message") or error.get("detail")
            if message:
                return str(message)[:300]
    except (ValueError, AttributeError, TypeError):
        pass
    return response.text.strip()[:300] or "云端图片接口返回了未知错误"


def _decode_image_item(item: dict, *, timeout: int) -> bytes:
    encoded = item.get("b64_json")
    if isinstance(encoded, str) and encoded:
        try:
            return base64.b64decode(encoded, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise ImageGenerationError("云端返回的图片 Base64 无法解析") from exc

    image_url = item.get("url")
    if not isinstance(image_url, str) or not image_url.strip():
        raise ImageGenerationError("云端响应中没有图片内容")
    parsed = urlparse(image_url)
    if parsed.scheme not in {"http", "https"}:
        raise ImageGenerationError("云端返回了不支持的图片地址")
    try:
        response = requests.get(image_url, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ImageGenerationError(f"下载云端图片失败：{type(exc).__name__}") from exc
    return response.content


def generate_image(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    output_path: str | Path,
    size: str = "1536x1024",
    timeout: int = 180,
) -> Path:
    """Generate one image through an OpenAI-compatible Images API.

    The API key is accepted only in memory and is never written to the output
    file or logs. Both ``b64_json`` and temporary URL responses are supported.
    """

    if not base_url.strip() or not api_key.strip() or not model.strip():
        raise ImageGenerationError("请提供云端接口地址、API Key 和图片模型名称")
    if not prompt.strip():
        raise ImageGenerationError("图片提示词不能为空")
    if timeout < 1:
        raise ImageGenerationError("图片请求超时时间必须大于 0")

    endpoint = f"{base_url.rstrip('/')}/images/generations"
    payload = {
        "model": model.strip(),
        "prompt": prompt.strip(),
        "n": 1,
        "size": size,
    }
    try:
        response = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {api_key.strip()}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
    except requests.ConnectionError as exc:
        raise ImageGenerationError(f"无法连接云端图片接口：{base_url}") from exc
    except requests.Timeout as exc:
        raise ImageGenerationError(f"图片请求超过 {timeout} 秒仍未完成") from exc
    except requests.HTTPError as exc:
        raise ImageGenerationError(
            f"云端图片接口返回 HTTP {response.status_code}：{_response_error(response)}"
        ) from exc
    except (requests.RequestException, ValueError, TypeError) as exc:
        raise ImageGenerationError(f"云端图片接口响应异常：{type(exc).__name__}") from exc

    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        raise ImageGenerationError("云端图片接口返回了空结果")
    image_bytes = _decode_image_item(items[0], timeout=timeout)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(image_bytes)
    return target

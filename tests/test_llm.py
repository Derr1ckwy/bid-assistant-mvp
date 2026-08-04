import pytest
import requests
from unittest.mock import Mock, patch

from bid_assistant.config import Settings
from bid_assistant.llm import LLMError, OpenAICompatibleClient, extract_json_object


def test_extract_json_object_from_fenced_response() -> None:
    value = """```json
{"project_name": "测试项目", "items": [1, 2]}
```"""

    result = extract_json_object(value)

    assert result["project_name"] == "测试项目"
    assert result["items"] == [1, 2]


def test_extract_json_object_rejects_non_json() -> None:
    with pytest.raises(LLMError):
        extract_json_object("模型没有给出结构化结果")


def _client(tmp_path, model: str = "qwen3:4b") -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        Settings(
            data_dir=tmp_path,
            llm_base_url="http://localhost:11434/v1",
            llm_api_key="ollama",
            llm_model=model,
        )
    )


def test_health_reports_unreachable_service(tmp_path) -> None:
    client = _client(tmp_path)
    with patch("bid_assistant.llm.requests.get", side_effect=requests.ConnectionError("refused")):
        health = client.check_health()

    assert health.available is False
    assert health.status == "unreachable"
    assert "无法连接模型服务" in health.message
    assert "ConnectionError" not in health.message


def test_health_reports_missing_model(tmp_path) -> None:
    client = _client(tmp_path)
    response = Mock(status_code=200, text="")
    response.json.return_value = {"data": [{"id": "qwen3:1.7b"}]}
    with patch("bid_assistant.llm.requests.get", return_value=response):
        health = client.check_health()

    assert health.available is False
    assert health.status == "model_missing"
    assert "qwen3:4b" in health.message
    assert health.models == ("qwen3:1.7b",)


def test_health_accepts_configured_model(tmp_path) -> None:
    client = _client(tmp_path)
    response = Mock(status_code=200, text="")
    response.json.return_value = {"data": [{"id": "qwen3:4b"}]}
    with patch("bid_assistant.llm.requests.get", return_value=response):
        health = client.check_health()

    assert health.available is True
    assert health.status == "ok"


def test_chat_connection_error_is_actionable(tmp_path) -> None:
    client = _client(tmp_path)
    with patch("bid_assistant.llm.requests.post", side_effect=requests.ConnectionError("refused")):
        with pytest.raises(LLMError, match="模型配置"):
            client.chat([{"role": "user", "content": "测试"}])

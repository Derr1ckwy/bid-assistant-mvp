import pytest

from bid_assistant.llm import LLMError, extract_json_object


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

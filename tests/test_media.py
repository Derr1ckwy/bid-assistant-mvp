from pathlib import Path
from unittest.mock import Mock, patch

import requests

from bid_assistant.media import ImageGenerationError, generate_image


def test_generate_image_decodes_base64_response(tmp_path: Path) -> None:
    response = Mock(status_code=200, content=b"", text="")
    response.json.return_value = {"data": [{"b64_json": "aGVsbG8="}]}
    output = tmp_path / "visual.png"
    with patch("bid_assistant.media.requests.post", return_value=response) as request:
        result = generate_image(
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
            model="gpt-image-1",
            prompt="教育培训课堂插画",
            output_path=output,
        )

    assert result == output
    assert output.read_bytes() == b"hello"
    request.assert_called_once()
    assert request.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-test"


def test_generate_image_rejects_missing_credentials(tmp_path: Path) -> None:
    with patch("bid_assistant.media.requests.post") as request:
        try:
            generate_image(
                base_url="https://api.openai.com/v1",
                api_key="",
                model="gpt-image-1",
                prompt="课堂插画",
                output_path=tmp_path / "visual.png",
            )
        except ImageGenerationError as exc:
            assert "API Key" in str(exc)
        else:
            raise AssertionError("missing credentials should fail before network request")
        request.assert_not_called()


def test_generate_image_reports_connection_error(tmp_path: Path) -> None:
    with patch(
        "bid_assistant.media.requests.post",
        side_effect=requests.ConnectionError("offline"),
    ):
        try:
            generate_image(
                base_url="https://api.openai.com/v1",
                api_key="sk-test",
                model="gpt-image-1",
                prompt="课堂插画",
                output_path=tmp_path / "visual.png",
            )
        except ImageGenerationError as exc:
            assert "无法连接" in str(exc)
        else:
            raise AssertionError("connection errors should be converted")

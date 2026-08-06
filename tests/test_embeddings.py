from unittest.mock import Mock, patch

from bid_assistant.embeddings import OpenAICompatibleEmbeddingClient


def _response_for(texts: list[str]) -> Mock:
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "data": [
            {"index": index, "embedding": [float(len(text)), float(index + 1)]}
            for index, text in enumerate(texts)
        ]
    }
    return response


def test_embedding_client_batches_requests_and_preserves_order() -> None:
    payloads: list[list[str]] = []

    def fake_post(*args, **kwargs):
        texts = list(kwargs["json"]["input"])
        payloads.append(texts)
        return _response_for(texts)

    client = OpenAICompatibleEmbeddingClient(
        "http://127.0.0.1:11435/v1",
        "embedding",
        "qwen3-embedding:0.6b",
        batch_size=2,
    )

    with patch("bid_assistant.embeddings.requests.post", side_effect=fake_post):
        vectors = client.embed(["a", "bb", "ccc"])

    assert payloads == [["a", "bb"], ["ccc"]]
    assert [vector[0] for vector in vectors] == [1.0, 2.0, 3.0]


def test_embedding_query_adds_optional_instruction() -> None:
    payloads: list[list[str]] = []

    def fake_post(*args, **kwargs):
        texts = list(kwargs["json"]["input"])
        payloads.append(texts)
        return _response_for(texts)

    client = OpenAICompatibleEmbeddingClient(
        "http://127.0.0.1:11435/v1",
        "embedding",
        "qwen3-embedding:0.6b",
        query_instruction="Retrieve tender evidence.",
    )

    with patch("bid_assistant.embeddings.requests.post", side_effect=fake_post):
        client.embed_query("屋面渗漏如何验收？")

    assert payloads == [["Instruct: Retrieve tender evidence.\nQuery: 屋面渗漏如何验收？"]]

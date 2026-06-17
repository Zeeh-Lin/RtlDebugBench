"""Tests for the reusable LLM client."""

import pytest

from s2_input_construction.llm_client import LLMClient, strip_markdown_fences


def test_strip_markdown_fences_json():
    text = '```json\n{"selected_doc_paths": []}\n```'
    assert strip_markdown_fences(text) == '{"selected_doc_paths": []}'


def test_strip_markdown_fences_plain():
    text = "plain text"
    assert strip_markdown_fences(text) == "plain text"


def test_call_returns_stripped_content(monkeypatch, mocker):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    mock_openai_cls = mocker.patch("openai.OpenAI")
    mock_client = mock_openai_cls.return_value
    mock_response = mocker.MagicMock()
    mock_response.choices[0].message.content = "  hello world  "
    mock_client.chat.completions.create.return_value = mock_response

    client = LLMClient(base_delay=0.01)
    result = client.call("prompt")
    assert result == "hello world"
    mock_client.chat.completions.create.assert_called_once()


def test_call_json_parses_json_response(monkeypatch, mocker):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    mock_openai_cls = mocker.patch("openai.OpenAI")
    mock_client = mock_openai_cls.return_value
    mock_response = mocker.MagicMock()
    mock_response.choices[0].message.content = '{"selected_doc_paths": ["doc/a.md"]}'
    mock_client.chat.completions.create.return_value = mock_response

    client = LLMClient(base_delay=0.01)
    result = client.call_json("prompt")
    assert result == {"selected_doc_paths": ["doc/a.md"]}


def test_call_retries_and_raises(monkeypatch, mocker):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    mock_openai_cls = mocker.patch("openai.OpenAI")
    mock_client = mock_openai_cls.return_value
    mock_client.chat.completions.create.side_effect = RuntimeError("API down")

    client = LLMClient(max_retries=2, base_delay=0.01)
    with pytest.raises(RuntimeError, match="LLM call failed after 2 retries"):
        client.call("prompt")
    assert mock_client.chat.completions.create.call_count == 2


def test_call_json_retries_on_parse_error(monkeypatch, mocker):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    mock_openai_cls = mocker.patch("openai.OpenAI")
    mock_client = mock_openai_cls.return_value
    mock_response = mocker.MagicMock()
    # First call returns invalid JSON, second returns valid JSON.
    mock_response.choices[0].message.content = "not json"
    mock_client.chat.completions.create.return_value = mock_response

    client = LLMClient(max_retries=2, base_delay=0.01)
    with pytest.raises(RuntimeError, match="LLM JSON call failed after 2 retries"):
        client.call_json("prompt")
    assert mock_client.chat.completions.create.call_count == 2


def test_client_missing_api_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    client = LLMClient()
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        _ = client.client

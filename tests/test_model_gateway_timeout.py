"""模型短时超时应重试，连续超时应给出可操作的提示。"""
import httpx
import pytest

from trippilot.retrieval import module1_model_gateway as gateway


class FakeResponse:
    status_code = 200

    def json(self):
        return {"choices": [{"message": {"content": '{"intent":"qa"}'}}]}


class FakeClient:
    attempts = 0
    always_timeout = False

    def __init__(self, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def post(self, *_args, **_kwargs):
        self.__class__.attempts += 1
        if self.always_timeout or self.attempts == 1:
            raise httpx.ReadTimeout("provider stalled")
        return FakeResponse()


def test_structured_model_retries_single_timeout(monkeypatch):
    FakeClient.attempts = 0
    FakeClient.always_timeout = False
    monkeypatch.setattr(gateway.httpx, "Client", FakeClient)
    monkeypatch.setattr(gateway.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(gateway, "get_settings", lambda: type("Settings", (), {
        "llm_api_key": "test", "llm_model": "test", "llm_base_url": "https://example.test/v1"
    })())
    result = gateway.ModelGateway().complete_json([{"role": "user", "content": "test"}])
    assert result == {"intent": "qa"}
    assert FakeClient.attempts == 2


def test_structured_model_explains_repeated_timeout(monkeypatch):
    FakeClient.attempts = 0
    FakeClient.always_timeout = True
    monkeypatch.setattr(gateway.httpx, "Client", FakeClient)
    monkeypatch.setattr(gateway.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(gateway, "get_settings", lambda: type("Settings", (), {
        "llm_api_key": "test", "llm_model": "test", "llm_base_url": "https://example.test/v1"
    })())
    with pytest.raises(gateway.ModelUnavailable, match="模型服务响应超时"):
        gateway.ModelGateway().complete_json([{"role": "user", "content": "test"}])
    assert FakeClient.attempts == 2

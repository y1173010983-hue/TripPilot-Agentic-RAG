"""测试使用隔离 SQLite 与临时原文件目录，不读取真实模型密钥，不产生外部费用。"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRIP_ENVIRONMENT", "test")
    monkeypatch.setenv("TRIP_DATABASE_URL", "sqlite:///./data/test.db")
    monkeypatch.setenv("TRIP_STORAGE_ROOT", str(tmp_path / "files"))
    monkeypatch.setenv("TRIP_VECTOR_BACKEND", "bm25")
    monkeypatch.setenv("TRIP_LLM_API_KEY", "")
    from trippilot.config import get_settings
    from trippilot.database import get_engine, Base
    get_settings.cache_clear()
    get_engine.cache_clear()
    from trippilot.app import create_app
    Base.metadata.create_all(get_engine())
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        yield c
    get_engine().dispose()
    get_engine.cache_clear()
    get_settings.cache_clear()


@pytest.fixture
def accounts(client):
    from trippilot.database import session_factory
    from trippilot.models import User
    from trippilot.auth import passwords
    with session_factory() as db:
        db.add(User(username="admin", password_hash=passwords.hash("Test-password-123"), role="admin"))
        db.commit()
    headers = {}
    for name in ["admin", "traveler", "another"]:
        if name != "admin":
            assert client.post("/api/v1/auth/register", json={"username": name, "password": "Test-password-123"}).status_code == 201
        response = client.post("/api/v1/auth/login", json={"username": name, "password": "Test-password-123"})
        assert response.status_code == 200, response.text
        headers[name] = {"Authorization": "Bearer " + response.json()["access_token"]}
    return headers

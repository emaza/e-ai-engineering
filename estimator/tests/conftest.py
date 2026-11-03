import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app


def make_settings(**overrides) -> Settings:
    """Settings de test: ignoran el .env local para no depender de keys reales."""
    values = {"OPENAI_API_KEY": "sk-test", "ANTHROPIC_API_KEY": "sk-ant-test"} | overrides
    return Settings(_env_file=None, **values)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def client(settings: Settings):
    app.dependency_overrides[get_settings] = lambda: settings
    yield TestClient(app)
    app.dependency_overrides.clear()

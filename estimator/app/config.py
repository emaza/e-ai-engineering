from functools import lru_cache
from typing import Annotated, Literal

from fastapi import Depends
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Provider = Literal["openai", "anthropic"]

# Modelo económico por defecto de cada proveedor (se usa si LLM_MODEL está vacío)
DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-haiku-4-5",
}


class Settings(BaseSettings):
    """Configuración de la aplicación, leída de variables de entorno y del archivo .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    OPENAI_API_KEY: str | None = None
    ANTHROPIC_API_KEY: str | None = None
    LLM_PROVIDER: Provider = "openai"
    LLM_MODEL: str | None = None
    APP_ENV: Literal["development", "staging", "production"] = "development"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "DEBUG"
    CACHE_ENABLED: bool = True
    CACHE_TTL_SECONDS: int = Field(default=86_400, gt=0)
    CACHE_MAX_ENTRIES: int = Field(default=256, gt=0)

    @model_validator(mode="after")
    def validate_api_key_for_provider(self) -> "Settings":
        """Exige la API key del proveedor seleccionado."""
        if self.LLM_PROVIDER == "openai" and not self.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY es obligatoria cuando LLM_PROVIDER='openai'")
        if self.LLM_PROVIDER == "anthropic" and not self.ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY es obligatoria cuando LLM_PROVIDER='anthropic'")
        return self

    @property
    def model(self) -> str:
        """Modelo efectivo: LLM_MODEL si está definido, si no el económico del proveedor."""
        return self.LLM_MODEL or DEFAULT_MODELS[self.LLM_PROVIDER]

    @property
    def api_key(self) -> str:
        """API key del proveedor seleccionado (garantizada por el validador)."""
        key = self.OPENAI_API_KEY if self.LLM_PROVIDER == "openai" else self.ANTHROPIC_API_KEY
        assert key is not None
        return key


@lru_cache
def get_settings() -> Settings:
    """Devuelve la configuración cacheada (singleton).

    Al estar cacheada, cualquier cambio en .env requiere reiniciar uvicorn.
    """
    return Settings()


# Inyección de dependencias en FastAPI (sustituible en tests con dependency_overrides)
SettingsDep = Annotated[Settings, Depends(get_settings)]

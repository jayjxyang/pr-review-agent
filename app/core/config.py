from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    github_webhook_secret: str = ""
    github_app_token: str = ""
    redis_url: str = "redis://localhost:6379/0"
    ai_gateway_url: str = "http://localhost:8080"
    llm_model: str = "deepseek-chat"
    diff_token_limit: int = 4000


@lru_cache
def get_settings() -> Settings:
    return Settings()

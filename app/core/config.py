from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    github_webhook_secret: str = ""
    github_app_token: str = ""
    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "postgresql://localhost:5432/pr_review"

    # Gateway settings
    ai_gateway_url: str = "http://localhost:8080/v1"
    ai_gateway_key: str = ""

    # Scenario aliases (routed by gateway)
    scan_scenario: str = "code-review-scan"
    reason_scenario: str = "code-review-reason"

    # Agent loop constraints
    max_rounds: int = 15
    max_input_tokens: int = 60000
    compress_at_round: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
LOCAL_ENVIRONMENTS = {"development", "dev", "local", "test"}
EXAMPLE_JWT_SECRET_MARKER = "change-this"


class Settings(BaseSettings):
    """Runtime configuration loaded from backend/.env when it exists."""

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    app_name: str
    environment: str
    frontend_origins: str
    serpapi_api_key: str = ""
    jwt_secret: str
    jwt_algorithm: str
    access_token_expire_minutes: int
    demo_user_email: str
    demo_user_password: str
    demo_user_name: str
    demo_market_name: str
    demo_market_country: str
    demo_market_category: str
    demo_brand_name: str
    demo_market_keywords: str
    demo_competitors: str

    # Optional second example market from a different category. Empty name or keywords = not seeded.
    demo_market_2_name: str = ""
    demo_market_2_country: str = "US"
    demo_market_2_category: str = ""
    demo_market_2_brand: str = ""
    demo_market_2_keywords: str = ""
    demo_market_2_competitors: str = ""

    # Optional LLM narration. Without a key the weekly brief uses the deterministic template.
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5-5"

    # SerpAPI credit protection.
    scan_min_interval_minutes: int = 60
    # Hard daily ceiling on live SerpAPI credits across all markets (UTC day). 0 disables the cap.
    # `force` cannot bypass it. Cached replays cost nothing and are always allowed.
    daily_credit_budget: int = 50
    # A background scan that runs longer than this is cancelled and marked failed.
    scan_job_timeout_seconds: int = 600
    suggest_cache_ttl_hours: int = 168
    serpapi_cache_dir: str = ""
    trends_date_range: str = "today 12-m"
    # Retries for SerpAPI rate limits and transient failures: bounded exponential backoff.
    serpapi_max_attempts: int = 3
    serpapi_backoff_base_seconds: float = 1.0
    serpapi_backoff_cap_seconds: float = 20.0

    # Logging: "text" for people, "json" for log collectors.
    log_level: str = "INFO"
    log_format: str = "text"

    @property
    def is_local(self) -> bool:
        """Development and test runs may use the example secret and the seeded demo account."""
        return self.environment.lower() in LOCAL_ENVIRONMENTS

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.frontend_origins.split(",") if origin.strip()]

    @property
    def market_keywords(self) -> list[str]:
        return [keyword.strip() for keyword in self.demo_market_keywords.split(",") if keyword.strip()]

    @property
    def market_2_keywords(self) -> list[str]:
        return [keyword.strip() for keyword in self.demo_market_2_keywords.split(",") if keyword.strip()]

    @property
    def market_2_competitors(self) -> list[str]:
        return [brand.strip() for brand in self.demo_market_2_competitors.split(",") if brand.strip()]

    @property
    def competitors(self) -> list[str]:
        return [brand.strip() for brand in self.demo_competitors.split(",") if brand.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment / backend/.env


def check_startup_safety(settings: Settings) -> None:
    """Refuse to start a non-local deployment with a placeholder or weak JWT secret."""
    if settings.is_local:
        return
    secret = settings.jwt_secret
    if EXAMPLE_JWT_SECRET_MARKER in secret.lower() or len(secret) < 32:
        raise RuntimeError(
            "JWT_SECRET is the example value or shorter than 32 characters. Set a strong random secret "
            "in backend/.env before running with ENVIRONMENT=" + settings.environment
        )

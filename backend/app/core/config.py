from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Values come from environment variables / .env (never committed)."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="MDA_", extra="ignore")

    database_url: str = "postgresql+psycopg://mda@localhost:5432/mda"
    # Separate database for pytest; it is wiped and re-migrated on each test run.
    test_database_url: str = "postgresql+psycopg://mda@localhost:5432/mda_test"
    timezone: str = "Asia/Jerusalem"

    # Sessions: approved 30-day inactivity expiry.
    session_idle_days: int = 30
    # Avoid a DB write on every request: refresh last_activity_at at most this often.
    session_touch_minutes: int = 5
    cookie_secure: bool = False  # MUST be true behind HTTPS in any shared deployment
    session_cookie_name: str = "mda_session"
    csrf_cookie_name: str = "mda_csrf"

    # Login rate limit (per process, per email+IP).
    login_max_failures: int = 8
    login_window_minutes: int = 15
    # Simultaneous argon2 hash/verify operations (64 MiB each). Bounds API memory (D-52).
    password_hash_concurrency: int = 2

    # Reminders. (The "registration closing soon" reminder was removed by decision 2026-10-12.)
    draft_idle_minutes: int = 10

    # Notification channel. "none" = no phone provider connected; messages are marked
    # "simulated" and only shown in-app. Never reported as delivered.
    push_channel: str = "none"
    worker_poll_seconds: int = 30

    # Web Push (VAPID). The private key lives outside Git and outside temporary folders; only the public key
    # is exposed to the browser. Empty = Web Push not configured (the test action then says so).
    vapid_public_key: str = ""
    vapid_private_key_path: str = ""
    vapid_subject: str = "mailto:admin@example.com"  # contact for the push services; must be a real-looking domain (not *.test)
    push_test_interval_seconds: int = 30

    # Email (password reset). "brevo" sends through Brevo's transactional API (needs BREVO_API_KEY and a verified
    # sender). "capture" writes each message to a local folder for development; it must never be used live.
    # "off" sends nothing; requests are still answered neutrally and the admin is told email is not configured.
    email_mode: str = "off"
    brevo_api_key: str = Field(default="", validation_alias="BREVO_API_KEY")  # exact name, no MDA_ prefix
    email_from_address: str = ""
    email_from_name: str = "נוער מדא"
    email_capture_dir: str = ""
    public_base_url: str = ""  # the trusted address of the app, e.g. http://localhost:5173 or https://host
    reset_token_minutes: int = 60

    # Google sign-in (OpenID Connect). Enabled only when both values are set.
    google_client_id: str = ""
    google_client_secret: str = ""
    oauth_state_minutes: int = 10

    @field_validator("database_url", "test_database_url")
    @classmethod
    def _psycopg_driver(cls, url: str) -> str:
        """Managed databases (e.g. DigitalOcean's ${db.DATABASE_URL}) hand out postgres:// or
        postgresql:// URLs; SQLAlchemy needs the psycopg 3 driver named explicitly."""
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix):]
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()

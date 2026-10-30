from __future__ import annotations

import math
import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
UPLOADS = ROOT / "uploads"
STATIC = ROOT / "static"
DEFAULT_DATABASE_URL = f"sqlite:///{(ROOT / 'damage_mapper.db').as_posix()}"
load_dotenv(ROOT / ".env", override=False)


class Settings:
    def __init__(self) -> None:
        self.environment = os.getenv(
            "DM_ENVIRONMENT",
            "production" if os.getenv("RENDER", "").lower() == "true" else "development",
        ).strip().lower()
        if self.environment not in {"development", "test", "production"}:
            raise ValueError("DM_ENVIRONMENT must be development, test, or production")
        self.database_url = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL).strip()
        if not self.database_url.strip():
            raise ValueError("DATABASE_URL must not be blank")
        if self.database_url.startswith("postgresql://"):
            self.database_url = self.database_url.replace(
                "postgresql://", "postgresql+psycopg://", 1
            )
        self.storage_backend = os.getenv("DM_STORAGE_BACKEND", "local").strip().lower()
        if self.storage_backend not in {"local", "supabase"}:
            raise ValueError("DM_STORAGE_BACKEND must be 'local' or 'supabase'")
        self.supabase_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        self.supabase_service_role_key = os.getenv(
            "SUPABASE_SERVICE_ROLE_KEY", ""
        ).strip()
        self.supabase_storage_bucket = os.getenv(
            "SUPABASE_STORAGE_BUCKET", "incident-evidence"
        ).strip()
        if self.storage_backend == "supabase":
            parsed_supabase_url = urlsplit(self.supabase_url)
            local_supabase = parsed_supabase_url.hostname in {
                "localhost",
                "127.0.0.1",
                "::1",
            }
            if (
                parsed_supabase_url.scheme not in {"http", "https"}
                or not parsed_supabase_url.netloc
                or not parsed_supabase_url.hostname
                or parsed_supabase_url.username
                or parsed_supabase_url.password
                or parsed_supabase_url.path not in {"", "/"}
                or parsed_supabase_url.query
                or parsed_supabase_url.fragment
                or (parsed_supabase_url.scheme != "https" and not local_supabase)
            ):
                raise ValueError(
                    "SUPABASE_URL must be an HTTPS URL (HTTP is allowed for localhost)"
                )
            if not self.supabase_service_role_key:
                raise ValueError(
                    "SUPABASE_SERVICE_ROLE_KEY is required for Supabase Storage"
                )
            if (
                not self.supabase_storage_bucket
                or "/" in self.supabase_storage_bucket
                or "\\" in self.supabase_storage_bucket
            ):
                raise ValueError("SUPABASE_STORAGE_BUCKET must not be blank")
        self.cors_origins = self._csv_setting("DM_CORS_ORIGINS")
        for origin in self.cors_origins:
            parsed_origin = urlsplit(origin)
            if (
                parsed_origin.scheme not in {"http", "https"}
                or not parsed_origin.netloc
                or parsed_origin.path not in {"", "/"}
                or parsed_origin.query
                or parsed_origin.fragment
                or parsed_origin.username
                or parsed_origin.password
            ):
                raise ValueError(
                    "DM_CORS_ORIGINS must contain comma-separated origins without paths"
                )
        self.trusted_hosts = self._csv_setting("DM_TRUSTED_HOSTS")
        if "*" in self.trusted_hosts:
            raise ValueError("DM_TRUSTED_HOSTS must not contain a wildcard")
        self.max_upload_bytes = int(os.getenv("DM_MAX_UPLOAD_BYTES", 10 * 1024 * 1024))
        self.max_image_pixels = int(os.getenv("DM_MAX_IMAGE_PIXELS", 40_000_000))
        if self.max_upload_bytes <= 0 or self.max_image_pixels <= 0:
            raise ValueError("Upload byte and pixel limits must be positive")
        self.disable_model = os.getenv("DM_DISABLE_MODEL", "0") == "1"
        self.demo_enabled = os.getenv("DM_ENABLE_DEMO", "1") == "1"
        self.clip_model = os.getenv("DM_CLIP_MODEL", "openai/clip-vit-base-patch32")
        self.yolo_model = os.getenv("DM_YOLO_MODEL", "").strip()
        self.ai_review_threshold = float(os.getenv("DM_AI_REVIEW_THRESHOLD", "0.55"))
        if not math.isfinite(self.ai_review_threshold) or not (
            0 <= self.ai_review_threshold <= 1
        ):
            raise ValueError("DM_AI_REVIEW_THRESHOLD must be between 0 and 1")
        self.auth_bootstrap_secret = os.getenv("DM_ADMIN_BOOTSTRAP_SECRET", "")
        self.auth_token_ttl_minutes = int(os.getenv("DM_AUTH_TOKEN_TTL_MINUTES", "480"))
        self.auth_login_limit = int(os.getenv("DM_AUTH_LOGIN_LIMIT", "10"))
        self.auth_register_limit = int(os.getenv("DM_AUTH_REGISTER_LIMIT", "5"))
        self.auth_upload_limit = int(os.getenv("DM_AUTH_UPLOAD_LIMIT", "20"))
        if self.auth_token_ttl_minutes <= 0 or min(
            self.auth_login_limit, self.auth_register_limit, self.auth_upload_limit
        ) <= 0:
            raise ValueError("Authentication token lifetime and rate limits must be positive")
        if self.auth_bootstrap_secret and len(self.auth_bootstrap_secret) < 32:
            raise ValueError(
                "DM_ADMIN_BOOTSTRAP_SECRET must contain at least 32 characters"
            )
        self.high_risk_priority_threshold = float(
            os.getenv("DM_HIGH_RISK_PRIORITY_THRESHOLD", "75")
        )
        self.duplicate_radius_m = float(os.getenv("DM_DUPLICATE_RADIUS_M", "150"))
        self.duplicate_window_hours = float(os.getenv("DM_DUPLICATE_WINDOW_HOURS", "48"))
        self.duplicate_hash_distance = int(os.getenv("DM_DUPLICATE_HASH_DISTANCE", "10"))
        if not math.isfinite(self.high_risk_priority_threshold) or not (
            0 <= self.high_risk_priority_threshold <= 100
        ):
            raise ValueError("DM_HIGH_RISK_PRIORITY_THRESHOLD must be between 0 and 100")
        if (
            not math.isfinite(self.duplicate_radius_m)
            or not math.isfinite(self.duplicate_window_hours)
            or self.duplicate_radius_m <= 0
            or self.duplicate_window_hours <= 0
        ):
            raise ValueError("Duplicate search radius and time window must be positive")
        if not 0 <= self.duplicate_hash_distance <= 64:
            raise ValueError("DM_DUPLICATE_HASH_DISTANCE must be between 0 and 64")
        if self.environment == "production":
            if not self.database_url.startswith("postgresql+psycopg://"):
                raise ValueError("Production requires a PostgreSQL DATABASE_URL")
            if self.storage_backend != "supabase":
                raise ValueError("Production requires DM_STORAGE_BACKEND=supabase")
            if self.demo_enabled:
                raise ValueError("Production requires DM_ENABLE_DEMO=0")
            if not self.trusted_hosts:
                raise ValueError("Production requires DM_TRUSTED_HOSTS")
            if any(not origin.startswith("https://") for origin in self.cors_origins):
                raise ValueError("Production CORS origins must use HTTPS")

    @staticmethod
    def _csv_setting(name: str) -> list[str]:
        return [
            value.strip().rstrip("/")
            for value in os.getenv(name, "").split(",")
            if value.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()

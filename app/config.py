from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _to_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("BACKEND_HOST", "0.0.0.0")
    port: int = int(os.getenv("BACKEND_PORT", "8000"))
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./iot_backend.db")
    mqtt_enabled: bool = _to_bool(os.getenv("MQTT_ENABLED"), False)
    mqtt_host: str = os.getenv("MQTT_HOST", "127.0.0.1")
    mqtt_port: int = int(os.getenv("MQTT_PORT", "1883"))
    mqtt_username: str = os.getenv("MQTT_USERNAME", "")
    mqtt_password: str = os.getenv("MQTT_PASSWORD", "")
    mqtt_topic_prefix: str = os.getenv("MQTT_TOPIC_PREFIX", "dorm").strip("/") or "dorm"
    admin_username: str = os.getenv("ADMIN_USERNAME", "admin")
    admin_email: str = os.getenv("ADMIN_EMAIL", "admin@dorm.local")
    admin_password: str = os.getenv("ADMIN_PASSWORD", "admin123")
    cmd_timeout_seconds: int = int(os.getenv("CMD_TIMEOUT_SECONDS", "30"))
    online_timeout_seconds: int = int(os.getenv("ONLINE_TIMEOUT_SECONDS", "60"))


settings = Settings()

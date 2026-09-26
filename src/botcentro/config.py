"""Configuración desde variables de entorno. Los secretos nunca se registran (SRS-N02)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field


class ConfigError(ValueError):
    pass


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"falta la variable de entorno {name}")
    return value


def _key(env: Mapping[str, str], name: str) -> bytes | None:
    value = env.get(name, "").strip()
    if not value:
        return None
    if len(value.encode()) < 32:
        raise ConfigError(f"{name} debe tener al menos 32 bytes")
    return value.encode()


@dataclass(frozen=True)
class Settings:
    insforge_base_url: str
    service_email: str
    service_password: str = field(repr=False)
    telegram_bot_id: int | None = None
    telegram_webhook_secret: str | None = field(default=None, repr=False)
    pseudonym_key: bytes | None = field(default=None, repr=False)
    callback_key: bytes | None = field(default=None, repr=False)
    telegram_message_limit: int = 4096
    telegram_rate_limit_per_minute: int = 10
    object_store_dir: str = "var/objects"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        bot_id = env.get("BOTCENTRO_TELEGRAM_BOT_ID", "").strip()
        return cls(
            insforge_base_url=_required(env, "BOTCENTRO_INSFORGE_URL"),
            service_email=_required(env, "BOTCENTRO_SERVICE_EMAIL"),
            service_password=_required(env, "BOTCENTRO_SERVICE_PASSWORD"),
            telegram_bot_id=int(bot_id) if bot_id else None,
            telegram_webhook_secret=env.get("BOTCENTRO_TELEGRAM_WEBHOOK_SECRET") or None,
            pseudonym_key=_key(env, "BOTCENTRO_PSEUDONYM_KEY"),
            callback_key=_key(env, "BOTCENTRO_CALLBACK_KEY"),
            telegram_message_limit=int(env.get("BOTCENTRO_TELEGRAM_MESSAGE_LIMIT", "4096")),
            telegram_rate_limit_per_minute=int(env.get("BOTCENTRO_TELEGRAM_RATE_LIMIT", "10")),
            object_store_dir=env.get("BOTCENTRO_OBJECT_STORE_DIR", "var/objects"),
        )

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_id and self.telegram_webhook_secret and self.pseudonym_key)

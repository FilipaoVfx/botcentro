"""Estado breve de la interfaz en Redis (centrorequirement.md §19, §21.5, §22; DEC-18).

* Clave de aislamiento: entorno + bot + chat + usuario. Nunca contiene texto de preguntas.
* La sesión caduca como máximo 24 h después de capturarse (sin TTL deslizante) y se guarda con
  compare-and-swap sobre `revision`: dos actualizaciones concurrentes no mezclan estado (UI-F28).
* Los botones llevan un token opaco `v1:<12 caracteres>` (15 bytes, límite de Telegram 64); lo
  que significa el botón vive aquí, con dueño, chat y vencimiento (UI-F26).
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from redis.asyncio import Redis

from botcentro.telegram_ui.contracts import CallbackRecord, ResultSet, SessionContext

CALLBACK_PREFIX = "v1:"
_CAS = """
local current = redis.call('GET', KEYS[1])
if current then
  if tonumber(cjson.decode(current)['revision']) ~= tonumber(ARGV[2]) then return 0 end
elseif tonumber(ARGV[2]) ~= 0 then
  return 0
end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[3])
return 1
"""


class UiState:
    def __init__(self, redis: Redis, *, bot_id: int, namespace: str = "prod", callback_ttl: int = 1800,
                 result_set_ttl: int = 1800, auth_ttl: int = 300, dedupe_ttl: int = 600) -> None:
        self.redis = redis
        self.prefix = f"bc:{namespace}:{bot_id}"
        self.callback_ttl = callback_ttl
        self.result_set_ttl = result_set_ttl
        self.auth_ttl = auth_ttl
        self.dedupe_ttl = dedupe_ttl
        self._cas = redis.register_script(_CAS)

    def _key(self, kind: str, chat_id: int, user_id: int) -> str:
        return f"{self.prefix}:{chat_id}:{user_id}:{kind}"

    # -- sesión --------------------------------------------------------------------------------

    async def load(self, chat_id: int, user_id: int) -> SessionContext:
        raw = await self.redis.get(self._key("session", chat_id, user_id))
        if raw:
            ctx = SessionContext.model_validate_json(raw)
            if ctx.expires_at > datetime.now(timezone.utc):
                return ctx
        return SessionContext()

    async def save(self, chat_id: int, user_id: int, ctx: SessionContext) -> bool:
        """Guarda si nadie cambió la sesión desde que se leyó; incrementa `revision`."""
        ttl = int((ctx.expires_at - datetime.now(timezone.utc)).total_seconds())
        if ttl <= 0:
            return False
        expected = ctx.revision
        stored = ctx.model_copy(update={"revision": expected + 1})
        ok = await self._cas(keys=[self._key("session", chat_id, user_id)],
                             args=[stored.model_dump_json(), expected, ttl])
        if ok:
            ctx.revision = expected + 1
        return bool(ok)

    async def clear(self, chat_id: int, user_id: int) -> None:
        await self.redis.delete(self._key("session", chat_id, user_id))

    # -- listas mostradas (ordinales y paginación) ---------------------------------------------

    async def save_result_set(self, chat_id: int, user_id: int, rs: ResultSet) -> None:
        await self.redis.set(self._key(f"rs:{rs.result_set_id}", chat_id, user_id), rs.model_dump_json(),
                             ex=self.result_set_ttl)

    async def get_result_set(self, chat_id: int, user_id: int, result_set_id: str) -> ResultSet | None:
        raw = await self.redis.get(self._key(f"rs:{result_set_id}", chat_id, user_id))
        return ResultSet.model_validate_json(raw) if raw else None

    # -- botones -------------------------------------------------------------------------------

    async def issue_callback(self, record: CallbackRecord) -> str:
        token = secrets.token_urlsafe(9)  # 72 bits, 12 caracteres
        await self.redis.set(f"{self.prefix}:cb:{token}", record.model_dump_json(), ex=self.callback_ttl)
        data = CALLBACK_PREFIX + token
        assert len(data.encode()) <= 64
        return data

    async def resolve_callback(self, data: str | None) -> CallbackRecord | None:
        if not data or not data.startswith(CALLBACK_PREFIX) or len(data.encode()) > 64:
            return None
        raw = await self.redis.get(f"{self.prefix}:cb:{data[len(CALLBACK_PREFIX):]}")
        return CallbackRecord.model_validate_json(raw) if raw else None

    # -- deduplicación y autorización ----------------------------------------------------------

    async def first_time(self, event_id: str) -> bool:
        """Verdadero la primera vez que se ve un evento (callback o update) en la ventana."""
        return bool(await self.redis.set(f"{self.prefix}:seen:{event_id}", "1", nx=True, ex=self.dedupe_ttl))

    async def cached_authorization(self, user_hash: str) -> bool | None:
        raw = await self.redis.get(f"{self.prefix}:auth:{user_hash}")
        return None if raw is None else raw in (b"1", "1")

    async def cache_authorization(self, user_hash: str, authorized: bool) -> None:
        await self.redis.set(f"{self.prefix}:auth:{user_hash}", "1" if authorized else "0", ex=self.auth_ttl)

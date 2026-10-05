import asyncio
import hashlib
import json
import os
import random
import threading
import time
from collections import defaultdict

import httpx
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.models.entities import now
from app.models.intelligence import ApiCache, Setting


class TokenBucket:
    def __init__(self, rate=3, capacity=3):
        self.rate, self.capacity, self.tokens = rate, capacity, float(capacity)
        self.updated = time.monotonic()
        self.lock = threading.Lock()

    async def acquire(self, redis_url="", provider=""):
        if redis_url:
            from redis.asyncio import Redis

            client = Redis.from_url(redis_url, socket_connect_timeout=0.5, socket_timeout=0.5)
            script = """
local clock = redis.call('TIME')
local current = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
local rate, capacity = tonumber(ARGV[1]), tonumber(ARGV[2])
local state = redis.call('HMGET', KEYS[1], 'tokens', 'updated')
local tokens = math.min(capacity, tonumber(state[1] or capacity) + math.max(0, current - tonumber(state[2] or current)) * rate)
local delay = 0
if tokens >= 1 then tokens = tokens - 1 else delay = (1 - tokens) / rate end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'updated', current)
redis.call('EXPIRE', KEYS[1], math.ceil(capacity / rate) + 60)
return tostring(delay)
"""
            try:
                while True:
                    delay = float(
                        await client.eval(script, 1, "rate:" + provider, self.rate, self.capacity)
                    )
                    if delay == 0:
                        return
                    await asyncio.sleep(delay)
            except Exception:
                pass
            finally:
                await client.aclose()
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                delay = (1 - self.tokens) / self.rate
            await asyncio.sleep(delay)


buckets = defaultdict(TokenBucket)


def cache_key(provider, path, params, method="GET", body=None):
    safe = {k: v for k, v in (params or {}).items() if k.lower() not in {"apikey", "api_key"}}
    payload = [provider, path, safe, method, body]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def replay_enabled(db):
    row = db.get(Setting, "replay_mode")
    return row.value["enabled"] if row else get_settings().replay_mode


class HttpCache:
    def __init__(self, db: Session, provider: str, base_url: str, headers=None, transport=None):
        self.db, self.provider, self.base_url = db, provider, base_url
        self.headers, self.transport = headers or {}, transport
        self.settings = get_settings()

    def read(self, key):
        row = self.db.get(ApiCache, key)
        if row:
            return row.response_json["payload"]
        path = self.settings.cache_dir / f"{key}.json"
        if path.is_file():
            try:
                return json.loads(path.read_text())["payload"]
            except (OSError, ValueError, KeyError):
                return None
        if self.settings.redis_url and not replay_enabled(self.db):
            try:
                from redis import Redis

                value = Redis.from_url(
                    self.settings.redis_url, socket_connect_timeout=0.2, socket_timeout=0.2
                ).get("chain:" + key)
                if value:
                    return json.loads(value)
            except Exception:
                pass
        return None

    def write(self, key, payload):
        row = self.db.get(ApiCache, key)
        if row:
            row.response_json = {"payload": payload}
            row.fetched_at = now()
        else:
            self.db.add(
                ApiCache(key=key, provider=self.provider, response_json={"payload": payload})
            )
        self.db.commit()
        directory = self.settings.cache_dir
        directory.mkdir(parents=True, exist_ok=True)
        tmp = directory / f"{key}.{os.getpid()}.{random.randrange(10**9)}.tmp"
        tmp.write_text(json.dumps({"payload": payload}, sort_keys=True))
        tmp.replace(directory / f"{key}.json")
        if self.settings.redis_url:
            try:
                from redis import Redis

                Redis.from_url(
                    self.settings.redis_url, socket_connect_timeout=0.2, socket_timeout=0.2
                ).setex("chain:" + key, 86400, json.dumps(payload))
            except Exception:
                pass

    async def request(self, path, params=None, method="GET", body=None):
        key = cache_key(self.provider, path, params, method, body)
        cached = self.read(key)
        if replay_enabled(self.db):
            if cached is None:
                raise AppError(
                    "REPLAY_CACHE_MISS",
                    "No recorded response for this request",
                    404,
                    {"provider": self.provider, "key": key},
                )
            return cached, "cache"
        if cached is not None:
            row = self.db.get(ApiCache, key)
            if row and (time.time() - row.fetched_at.timestamp()) < self.settings.cache_ttl_seconds:
                return cached, "cache"
        failure = None
        for attempt in range(3):
            await buckets[self.provider].acquire(self.settings.redis_url, self.provider)
            try:
                async with httpx.AsyncClient(
                    base_url=self.base_url,
                    timeout=15,
                    headers=self.headers,
                    transport=self.transport,
                ) as client:
                    response = await client.request(method, path, params=params, json=body)
                if response.status_code == 404:
                    raise AppError("CHAIN_RECORD_NOT_FOUND", "Provider has no such record", 404)
                response.raise_for_status()
                payload = response.json()
                if (
                    isinstance(payload, dict)
                    and payload.get("status") == "0"
                    and payload.get("message") not in {"No transactions found", "No records found"}
                ):
                    raise AppError(
                        "PROVIDER_ERROR",
                        "Provider rejected the request",
                        502,
                        {"provider": self.provider},
                    )
                self.write(key, payload)
                return payload, "live"
            except (httpx.HTTPError, ValueError, AppError) as exc:
                failure = exc
                if isinstance(exc, AppError) and exc.status == 404:
                    raise
                if attempt < 2:
                    await asyncio.sleep(min(4, 0.25 * 2**attempt) * random.uniform(0.5, 1))
        if cached is not None:
            return cached, "cache"
        if isinstance(failure, AppError):
            raise failure
        raise AppError(
            "CHAIN_PROVIDER_UNAVAILABLE",
            "Provider unavailable and no cached response exists",
            502,
            {"provider": self.provider},
        ) from failure

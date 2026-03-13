"""
Upstash Redis REST API 客户端

通过 REST API 与 Upstash Redis 交互，支持 GET / SET / DEL，
以及 TTL 和 SET NX 语义。

环境变量:
    UPSTASH_URL   — Upstash Redis REST 端点（如 https://xxx.upstash.io）
    UPSTASH_TOKEN — Upstash REST API Bearer Token
"""

import json
import logging
import os
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


class RedisClientError(Exception):
    """Redis 操作异常。"""


class UpstashRedisClient:
    """基于 REST API 的 Upstash Redis 异步客户端。"""

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
    ) -> None:
        self.url = (url or os.environ.get("UPSTASH_URL", "")).rstrip("/")
        self.token = token or os.environ.get("UPSTASH_TOKEN", "")
        self._session: aiohttp.ClientSession | None = None

        if not self.url or not self.token:
            logger.warning(
                "UPSTASH_URL 或 UPSTASH_TOKEN 未设置，Redis 功能将被禁用。"
            )
            self._enabled = False
        else:
            self._enabled = True

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def base_url(self) -> str:
        return self.url

    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建共享 HTTP Session。"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=10),
            )
        return self._session

    async def close(self) -> None:
        """关闭共享 Session。"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _request(self, *args: str) -> Any:
        """发送 REST 命令到 Upstash。"""
        if not self._enabled:
            return None

        body = list(args)

        try:
            session = await self._get_session()
            async with session.post(
                self.url,
                data=json.dumps(body),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error("Redis REST 请求失败 [%s]: %s", resp.status, text)
                    return None
                data = await resp.json()
                return data.get("result")
        except Exception as exc:
            logger.error("Redis REST 请求异常: %s", exc)
            # Session 可能已损坏，重置
            self._session = None
            return None

    # ── 基础操作 ──────────────────────────────────────────

    async def get(self, key: str) -> str | None:
        """GET key"""
        return await self._request("GET", key)

    async def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        """
        SET key value [EX seconds] [NX]

        Parameters
        ----------
        key : str
        value : str
        ex : int | None  过期时间（秒）
        nx : bool  仅在 key 不存在时设置

        Returns
        -------
        bool  是否设置成功
        """
        cmd: list[str] = ["SET", key, value]
        if ex is not None:
            cmd.extend(["EX", str(ex)])
        if nx:
            cmd.append("NX")
        result = await self._request(*cmd)
        return result is not None and result != ""

    async def delete(self, key: str) -> bool:
        """DEL key"""
        result = await self._request("DEL", key)
        return result is not None and int(result) > 0

    async def lpush(self, key: str, value: str) -> int | None:
        """LPUSH key value"""
        return await self._request("LPUSH", key, value)

    async def lrange(self, key: str, start: int, stop: int) -> list[str]:
        """LRANGE key start stop"""
        result = await self._request("LRANGE", key, str(start), str(stop))
        return result if isinstance(result, list) else []

    async def expire(self, key: str, seconds: int) -> bool:
        """EXPIRE key seconds"""
        result = await self._request("EXPIRE", key, str(seconds))
        return result is not None and int(result) == 1


# ── 全局单例 ──────────────────────────────────────────────

_client: UpstashRedisClient | None = None


def get_redis_client() -> UpstashRedisClient:
    """获取全局 Redis 客户端单例。"""
    global _client
    if _client is None:
        _client = UpstashRedisClient()
    return _client

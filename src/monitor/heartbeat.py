"""
心跳监控模块

- write_heartbeat(): 写 heartbeat:{name} (TTL 2h)
- check_heartbeat(): 检查心跳是否存在
- alert_admin(): 向管理员发送告警
"""

import logging
import os

from storage.redis_client import get_redis_client

logger = logging.getLogger(__name__)

HEARTBEAT_TTL_SECONDS = 2 * 3600  # 2 小时


async def write_heartbeat(name: str) -> None:
    """
    写入心跳记录。

    Key: heartbeat:{name}  Value: "alive"  TTL: 2h

    Parameters
    ----------
    name : str  心跳名称（如 "fetch", "push"）
    """
    redis = get_redis_client()
    if not redis.enabled:
        logger.debug("Redis 未启用，跳过心跳写入: %s", name)
        return
    key = f"heartbeat:{name}"
    await redis.set(key, "alive", ex=HEARTBEAT_TTL_SECONDS)
    logger.info("💓 心跳已写入: %s (TTL=%dh)", name, HEARTBEAT_TTL_SECONDS // 3600)


async def check_heartbeat(name: str) -> bool:
    """
    检查心跳是否存在。

    Parameters
    ----------
    name : str  心跳名称

    Returns
    -------
    bool  True 表示心跳存在（服务健康）
    """
    redis = get_redis_client()
    if not redis.enabled:
        logger.debug("Redis 未启用，心跳检查跳过: %s", name)
        return True  # 未启用时默认健康
    key = f"heartbeat:{name}"
    result = await redis.get(key)
    alive = result is not None
    if alive:
        logger.debug("💓 心跳正常: %s", name)
    else:
        logger.warning("💔 心跳缺失: %s", name)
    return alive


async def alert_admin(message: str) -> bool:
    """
    向管理员发送告警消息。

    通过 Telegram Bot API 推送（复用 pusher.telegram 模块）。

    Parameters
    ----------
    message : str  告警内容

    Returns
    -------
    bool  是否发送成功
    """
    # 延迟导入避免循环依赖
    from pusher.telegram import send_admin_alert
    from pusher.formatter import format_alert

    formatted = format_alert(message)
    return await send_admin_alert(formatted)

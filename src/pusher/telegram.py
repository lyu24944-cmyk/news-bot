"""
Telegram Bot 推送模块

- send_telegram(): 调 Telegram Bot API 发送消息
- mark_pushed(): 推送成功后写 Redis 防重复
- BOT_TOKEN 从环境变量 TELEGRAM_BOT_TOKEN 读取
"""

import logging
import os

import aiohttp

from storage.redis_client import get_redis_client

logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_API_BASE = "https://api.telegram.org"
PUSH_TTL_SECONDS = 48 * 3600  # 48 小时


async def send_telegram(
    chat_id: str,
    message: str,
    parse_mode: str = "Markdown",
) -> bool:
    """
    通过 Telegram Bot API 发送消息。

    Parameters
    ----------
    chat_id : str  Telegram Chat ID
    message : str  消息内容
    parse_mode : str  解析模式 (Markdown / HTML)

    Returns
    -------
    bool  是否发送成功
    """
    token = BOT_TOKEN
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN 未设置，跳过推送。消息内容:")
        logger.info("→ [chat_id=%s]\n%s", chat_id, message[:500])
        return False

    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("ok"):
                        logger.info("✅ Telegram 推送成功: chat_id=%s", chat_id)
                        return True
                    else:
                        logger.error("Telegram API error: %s", data.get("description"))
                        return False
                else:
                    body = await resp.text()
                    logger.error("Telegram HTTP %d: %s", resp.status, body[:200])
                    return False

    except Exception as exc:
        logger.error("Telegram 推送异常: %s", exc)
        return False


async def mark_pushed(chat_id: str, news_id: str) -> None:
    """
    推送成功后写 Redis 标记，防止重复推送。

    Key: push:{chatId}:{newsId}  TTL: 48h
    """
    redis = get_redis_client()
    if not redis.enabled:
        return
    key = f"push:{chat_id}:{news_id}"
    await redis.set(key, "1", ex=PUSH_TTL_SECONDS)
    logger.debug("已标记推送: %s", key)


async def send_admin_alert(message: str, admin_chat_id: str = "") -> bool:
    """向管理员发送告警消息。"""
    admin_id = admin_chat_id or os.environ.get("ADMIN_CHAT_ID", "")
    if not admin_id:
        logger.warning("ADMIN_CHAT_ID 未设置，告警消息仅日志输出:")
        logger.warning("🚨 %s", message)
        return False
    return await send_telegram(admin_id, message)

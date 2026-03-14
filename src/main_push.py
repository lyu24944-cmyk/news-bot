"""
News-Bot 推送入口 - 分布式锁 + 心跳检查 + 新闻推送

流程:
1. 获取分布式锁 lock:push:{hourKey} (NX EX 3600)
2. 检查 heartbeat:fetch 是否存在
3. 读取 news:{today}:* → 遍历用户 → 过滤 → 格式化 → 推送
4. 写入 heartbeat:push

用法:
    python main_push.py
"""

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# 确保 src 包可被导入
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from storage.redis_client import get_redis_client
from pusher.dispatcher import get_all_users, filter_news_for_user
from pusher.formatter import format_digest
from pusher.telegram import send_telegram, mark_pushed
from pusher.pushplus import send_pushplus
from monitor.heartbeat import write_heartbeat, check_heartbeat, alert_admin

# ── 日志配置 ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


async def acquire_lock(hour_key: str) -> bool:
    """
    尝试获取分布式锁，防止并发重复推送。

    Key: lock:push:{hourKey}  NX  EX 3600

    Returns
    -------
    bool  是否成功获取锁
    """
    redis = get_redis_client()
    if not redis.enabled:
        logger.info("Redis 未启用，跳过分布式锁。")
        return True  # 未启用时直接放行

    lock_key = f"lock:push:{hour_key}"
    acquired = await redis.set(lock_key, "locked", ex=3600, nx=True)

    if acquired:
        logger.info("🔒 获取分布式锁成功: %s", lock_key)
    else:
        logger.warning("🔒 分布式锁已被占用: %s — 退出防止重复推送。", lock_key)

    return acquired


async def load_today_news() -> list[dict]:
    """从 Redis 读取今日所有新闻 (news:{date}:*)。"""
    redis = get_redis_client()
    if not redis.enabled:
        logger.warning("Redis 未启用，无法读取新闻数据。")
        return []

    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")

    # 使用 KEYS 命令扫描 news:{date}:* 模式的键
    news_keys = await redis._request("KEYS", f"news:{date_str}:*")
    if not news_keys:
        logger.info("今日 (%s) 无新闻数据。", date_str)
        return []

    news_list = []
    for key in news_keys:
        raw = await redis.get(key)
        if raw:
            try:
                news_list.append(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                continue

    logger.info("从 Redis 加载了 %d 条今日新闻。", len(news_list))
    return news_list


async def push_to_user(
    user_prefs: dict,
    news_items: list[dict],
) -> int:
    """
    向单个用户推送新闻（Telegram + PushPlus 双渠道）。

    Returns
    -------
    int  成功推送的新闻数
    """
    chat_id = str(user_prefs.get("chat_id", ""))
    user_lang = user_prefs.get("language", user_prefs.get("lang", "zh"))

    if not chat_id:
        logger.warning("用户缺少 chat_id，跳过。")
        return 0

    if not news_items:
        logger.debug("用户 %s 无匹配新闻，跳过推送。", chat_id)
        return 0

    # 格式化消息
    message = format_digest(news_items, user_lang)

    # ── 渠道 1: Telegram ──
    tg_success = False
    if len(message) <= 4096:
        tg_success = await send_telegram(chat_id, message)
    else:
        tg_success = True
        batch_size = 4
        for start in range(0, len(news_items), batch_size):
            batch = news_items[start : start + batch_size]
            batch_msg = format_digest(batch, user_lang)
            if len(batch_msg) > 4096:
                batch_msg = batch_msg[:4090] + "\n..."
            if not await send_telegram(chat_id, batch_msg):
                tg_success = False

    # ── 渠道 2: PushPlus 微信推送 ──
    pp_success = await send_pushplus(
        content=message,
        title=f"📰 今日新闻摘要 ({len(news_items)}条)",
    )

    success = tg_success or pp_success  # 任一渠道成功即可

    if success:
        for news in news_items:
            news_id = news.get("fingerprint", "")
            if news_id:
                await mark_pushed(chat_id, news_id)
        channels = []
        if tg_success: channels.append("Telegram")
        if pp_success: channels.append("PushPlus")
        logger.info("📬 已向用户 %s 推送 %d 条新闻 [%s]。", chat_id, len(news_items), "+".join(channels))
        return len(news_items)
    else:
        logger.error("📭 向用户 %s 所有渠道推送失败。", chat_id)
        return 0


async def main() -> None:
    """推送主流程。"""
    logger.info("News-Bot 推送引擎启动…")

    now = datetime.now(timezone.utc)
    hour_key = now.strftime("%Y%m%d_%H")
    current_hour = now.hour

    # ── 1. 获取分布式锁 ──
    if not await acquire_lock(hour_key):
        return

    # ── 2. 检查抓取心跳 ──
    fetch_alive = await check_heartbeat("fetch")
    if not fetch_alive:
        logger.warning("⚠️  heartbeat:fetch 不存在，抓取服务可能异常！")
        await alert_admin(
            "⚠️ 抓取服务心跳缺失！\n"
            "heartbeat:fetch 不存在，请检查 main_fetch.py 是否正常运行。"
        )

    # ── 3. 读取今日新闻 ──
    all_news = await load_today_news()
    if not all_news:
        logger.info("今日无新闻可推送。")
        await write_heartbeat("push")
        return

    # ── 4. 遍历用户 → 过滤 → 推送 ──
    users = await get_all_users()
    if not users:
        logger.info("无注册用户，跳过推送。")
        await write_heartbeat("push")
        return

    total_pushed = 0
    total_users = 0

    for user_prefs in users:
        try:
            # 过滤匹配的新闻
            matched = await filter_news_for_user(all_news, user_prefs, current_hour)

            if matched:
                pushed = await push_to_user(user_prefs, matched)
                total_pushed += pushed
                if pushed > 0:
                    total_users += 1

        except Exception as exc:
            chat_id = user_prefs.get("chat_id", "?")
            logger.error("❌ 用户 %s 推送异常: %s", chat_id, exc)

    # ── 5. 写入推送统计到 Redis（供监控面板读取）──
    redis = get_redis_client()
    if redis.enabled:
        date_str = now.strftime("%Y%m%d")
        push_stats = {
            "total_users": total_users,
            "total_news": total_pushed,
            "channels": {"Telegram": total_users, "PushPlus": 0},
        }
        await redis.set(
            f"stats:push:{date_str}",
            json.dumps(push_stats, ensure_ascii=False),
            ex=72 * 3600,
        )
        logger.info("📊 推送统计已写入 Redis: stats:push:%s", date_str)

    # ── 6. 写入推送心跳 ──
    await write_heartbeat("push")

    logger.info(
        "📊 推送完成: 向 %d 个用户推送了 %d 条新闻。",
        total_users, total_pushed,
    )


if __name__ == "__main__":
    asyncio.run(main())


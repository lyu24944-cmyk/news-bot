"""
一次性脚本：手动注册用户到 Redis，使推送引擎能推送给你。
"""
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from storage.redis_client import get_redis_client


async def register_user():
    redis = get_redis_client()
    if not redis.enabled:
        print("❌ Redis 未启用！请设置 UPSTASH_URL 和 UPSTASH_TOKEN 环境变量。")
        return

    # ← 替换为你的 Telegram Chat ID（就是你在 ADMIN_CHAT_ID 里填的那个数字）
    CHAT_ID = input("请输入你的 Telegram Chat ID: ").strip()

    prefs = {
        "chat_id": CHAT_ID,
        "lang": "zh",
        "categories": ["tech", "world", "finance", "science", "society", "sports", "health"],
        "min_importance": 1,
        "push_times_utc": list(range(24)),  # 每小时都可推
        "timezone_offset": 8,
    }

    # 写入用户偏好
    await redis.set(f"user:{CHAT_ID}:prefs", json.dumps(prefs, ensure_ascii=False))
    print(f"✅ 用户偏好已写入: user:{CHAT_ID}:prefs")

    # 更新用户索引
    raw = await redis.get("users:index")
    index = json.loads(raw) if raw else []
    if CHAT_ID not in index:
        index.append(CHAT_ID)
    await redis.set("users:index", json.dumps(index))
    print(f"✅ 用户索引已更新: {index}")
    print(f"\n🎉 注册完成！下次推送引擎运行时会向你推送新闻。")


if __name__ == "__main__":
    asyncio.run(register_user())

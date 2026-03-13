"""立刻推送最新 AI 新闻给指定用户"""
import asyncio, json, sys, aiohttp
sys.path.insert(0, "src")
from storage.redis_client import UpstashRedisClient
from pusher.formatter import format_digest

CHAT_ID = "5711162361"
BOT = "8721055778:AAEIIWXaHGcjTQUynCmCqPCmwAolo6Gy9oU"

async def main():
    r = UpstashRedisClient()
    keys = await r._request("KEYS", "news:*")
    news = []
    for k in keys:
        raw = await r._request("GET", k)
        if raw:
            news.append(json.loads(raw))

    valid = [n for n in news if n.get("ai", {}).get("valid")]
    valid.sort(key=lambda x: x.get("ai", {}).get("importance", 0), reverse=True)
    top = valid[:8]
    
    print(f"有效新闻: {len(valid)} 条, 推送 Top {len(top)}")
    for i, n in enumerate(top):
        ai = n.get("ai", {})
        cat = ai.get("category", "?")
        imp = ai.get("importance", 0)
        headline = ai.get("headline", n.get("title", ""))[:40]
        print(f"  {i+1}. [{cat}] imp={imp} {headline}")

    langs = [n.get("source_lang", "zh") for n in top]
    msg = format_digest(top, "zh", langs)

    async with aiohttp.ClientSession() as s:
        async with s.post(
            f"https://api.telegram.org/bot{BOT}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"},
        ) as resp:
            r2 = await resp.json()
            if r2.get("ok"):
                print("✅ 推送成功！去 Telegram 查看吧！")
            else:
                print(f"❌ 推送失败: {r2}")

asyncio.run(main())

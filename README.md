# 📰 News-Bot

AI 驱动的智能新闻聚合与推送系统。自动采集 RSS 新闻 → AI 分类摘要 → Telegram 定时推送。

[![Fetch Engine](https://github.com/<OWNER>/<REPO>/actions/workflows/fetch.yml/badge.svg)](https://github.com/<OWNER>/<REPO>/actions/workflows/fetch.yml)
[![Push Engine](https://github.com/<OWNER>/<REPO>/actions/workflows/push.yml/badge.svg)](https://github.com/<OWNER>/<REPO>/actions/workflows/push.yml)

---

## ✨ 功能特性

| 模块 | 功能 |
|------|------|
| 🕸️ RSS 采集 | 异步抓取 6+ 中英文源，主/备 URL 自动切换 |
| 🧹 内容清洗 | HTML 剥离 + 双重实体解码 + trafilatura 全文提取 |
| 🤖 AI 引擎 | DeepSeek → OpenAI 降级链，自动分类/打标签/评重要性/生成摘要 |
| 🔁 智能去重 | SHA256 指纹 + difflib 模糊标题匹配 + 延迟 ACK |
| 📬 推送引擎 | 分布式锁防竞争，按用户偏好过滤 Top 8 推送 |
| 🤖 Telegram Bot | InlineKeyboard 订阅管理，Cloudflare Worker 部署 |
| 💓 心跳监控 | 服务健康检查 + 管理员告警 |

---

## 🏗️ 系统架构

```
┌──────────────┐     ┌──────────────┐     ┌──────────────────┐
│  RSS Sources │────>│  Fetch Engine│────>│  Upstash Redis   │
│  (6+ feeds)  │     │  (Cron 3x/d) │     │  (news storage)  │
└──────────────┘     └──────┬───────┘     └────────┬─────────┘
                            │                      │
                     ┌──────▼───────┐     ┌────────▼─────────┐
                     │  AI Engine   │     │  Push Engine      │
                     │  (DeepSeek/  │     │  (Cron hourly)    │
                     │   OpenAI)    │     └────────┬─────────┘
                     └──────────────┘              │
                                          ┌────────▼─────────┐
┌──────────────────┐                      │  Telegram Bot    │
│  CF Worker       │<────────────────────>│  (send digest)   │
│  (subscription)  │                      └──────────────────┘
└──────────────────┘
```

---

## 🚀 一键部署（3 步完成）

### Step 1: Fork 仓库

点击右上角 **Fork** 按钮，将仓库 Fork 到自己的账号。

### Step 2: 配置 GitHub Secrets

进入 Fork 后的仓库 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

按以下表格逐一添加：

| Secret 名称 | 必须 | 说明 | 获取方式 |
|-------------|------|------|----------|
| `UPSTASH_URL` | ✅ | Upstash Redis REST URL | [Upstash Console](https://console.upstash.com/) → 创建 Redis → REST API → URL |
| `UPSTASH_TOKEN` | ✅ | Upstash Redis REST Token | 同上 → REST API → Token |
| `DEEPSEEK_KEY` | ⚡ | DeepSeek API Key（AI 首选） | [DeepSeek Platform](https://platform.deepseek.com/) → API Keys |
| `OPENAI_KEY` | ⚡ | OpenAI API Key（AI 备选） | [OpenAI Platform](https://platform.openai.com/api-keys) |
| `TELEGRAM_BOT_TOKEN` | ✅ | Telegram Bot Token | Telegram 搜索 [@BotFather](https://t.me/BotFather) → `/newbot` |
| `ADMIN_CHAT_ID` | 📌 | 管理员 Telegram Chat ID | Telegram 搜索 [@userinfobot](https://t.me/userinfobot) → 获取你的 ID |

> ⚡ `DEEPSEEK_KEY` 和 `OPENAI_KEY` 至少配置一个，否则 AI 功能降级为纯文本兜底。
>
> 📌 `ADMIN_CHAT_ID` 可选，配置后可接收系统告警通知。

### Step 3: 启用 Actions

进入 **Actions** 标签页，点击 **"I understand my workflows, go ahead and enable them"**。

🎉 **完成！** 系统将自动按计划运行：
- 每天 **08:00 / 14:00 / 20:00**（北京时间）采集新闻
- 每小时推送匹配的新闻给已订阅用户

---

## ⏰ 定时任务说明

| Workflow | Cron (UTC) | 北京时间 | 用途 |
|----------|-----------|---------|------|
| `fetch.yml` | `0 0,6,12 * * *` | 08:00 / 14:00 / 20:00 | 采集 + AI 处理 |
| `push.yml` | `5 * * * *` | 每小时第 05 分 | 推送新闻给用户 |
| `keep_alive.yml` | `0 0 1 * *` | 每月 1 日 | 防止仓库被禁用 |

---

## 🤖 Telegram Bot 部署（可选）

Telegram Bot 用于用户自助管理订阅，部署在 Cloudflare Workers。

```bash
cd worker
npm install
wrangler secret put TELEGRAM_BOT_TOKEN
wrangler secret put TELEGRAM_SECRET
wrangler secret put UPSTASH_URL
wrangler secret put UPSTASH_TOKEN
npm run deploy
```

设置 Webhook：
```bash
curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://news-bot-telegram.<sub>.workers.dev","secret_token":"<SECRET>"}'
```

详见 [`worker/SETUP.md`](worker/SETUP.md)。

---

## ✅ 验证系统是否正常

### 1. 手动触发 Fetch
进入 **Actions** → **📰 News Fetch Engine** → **Run workflow** → 查看日志输出。

### 2. 手动触发 Push
进入 **Actions** → **📬 News Push Engine** → **Run workflow** → 查看日志。

### 3. 检查 Telegram Bot
打开 Telegram，向你的 Bot 发送 `/start`，应收到欢迎消息。

### 4. 检查 Redis 数据
登录 [Upstash Console](https://console.upstash.com/) → Data Browser，查看：
- `news:*` — 采集的新闻
- `user:*:prefs` — 用户订阅配置
- `heartbeat:*` — 服务心跳

---

## 📁 项目结构

```
news-bot/
├── .github/workflows/
│   ├── fetch.yml            # 采集引擎定时任务
│   ├── push.yml             # 推送引擎定时任务
│   └── keep_alive.yml       # 仓库保活
├── config/
│   └── feeds.yaml           # RSS 源配置（6 个中英文源）
├── src/
│   ├── fetcher/
│   │   ├── rss_fetcher.py       # 异步 RSS 抓取（主/备 URL）
│   │   ├── content_cleaner.py   # HTML 清洗
│   │   └── content_extractor.py # trafilatura 全文 + 智能截断
│   ├── processor/
│   │   ├── ai_engine.py         # AI 分类摘要（DeepSeek/OpenAI）
│   │   ├── dedup.py             # 指纹去重 + 模糊匹配
│   │   └── validator.py         # AI 输出校验
│   ├── pusher/
│   │   ├── dispatcher.py        # 用户过滤 + Top 8
│   │   ├── formatter.py         # Telegram 消息格式化
│   │   └── telegram.py          # Telegram Bot API
│   ├── storage/
│   │   └── redis_client.py      # Upstash Redis REST 客户端
│   ├── monitor/
│   │   └── heartbeat.py         # 心跳监控 + 告警
│   ├── main_fetch.py            # 采集入口
│   └── main_push.py             # 推送入口
├── worker/
│   ├── src/index.js             # Cloudflare Worker (Telegram Bot)
│   ├── wrangler.toml            # Worker 部署配置
│   └── SETUP.md                 # Worker 部署指南
├── requirements.txt
└── README.md
```

---

## ❓ 常见问题

### Q: Actions 没有按时运行？
GitHub Actions Cron 可能有 5-15 分钟的延迟，这是正常现象。如果超过 1 小时未运行，检查 Actions 是否被禁用（仓库 60 天无活动会自动禁用，`keep_alive.yml` 已解决此问题）。

### Q: AI 摘要显示"AI 服务暂时不可用"？
未配置 `DEEPSEEK_KEY` 或 `OPENAI_KEY`，或 API 余额不足。请检查 Secrets 配置和 API 账户余额。

### Q: Telegram Bot 不响应命令？
1. 检查 Webhook 是否正确设置：`curl https://api.telegram.org/bot<TOKEN>/getWebhookInfo`
2. 查看 Worker 日志：`cd worker && npm run tail`
3. 确认 `TELEGRAM_BOT_TOKEN` 和 `TELEGRAM_SECRET` 正确

### Q: 新闻重复推送？
确认 `UPSTASH_URL` 和 `UPSTASH_TOKEN` 已正确配置。Redis 存储推送记录（TTL 48h），未配置时去重功能降级。

### Q: 如何添加新的 RSS 源？
编辑 `config/feeds.yaml`，按格式添加新源即可：
```yaml
- name: "源名称"
  url: "https://example.com/feed"
  lang: "en"           # zh 或 en
  category_hint: "tech"
  update_freq: "high"  # high 或 low
  fuzzy_window_hours: 6
```

### Q: 如何修改推送时间？
编辑 `.github/workflows/fetch.yml` 中的 cron 表达式。注意使用 UTC 时间（北京时间 - 8 小时）。

---

## 📄 License

MIT

# News-Bot Telegram Worker — 部署指南

## 1. 前置准备

### 1.1 创建 Telegram Bot
1. 与 [@BotFather](https://t.me/BotFather) 对话，发送 `/newbot`
2. 记录获得的 **Bot Token**（格式: `123456:ABC-DEF...`）

### 1.2 Upstash Redis
1. 注册 [Upstash](https://upstash.com/)
2. 创建 Redis 数据库
3. 在控制台获取 **REST URL** 和 **REST Token**

### 1.3 安装 Wrangler
```bash
npm install -g wrangler
wrangler login
```

---

## 2. 配置 Secrets

```bash
cd worker

# 设置 Telegram Bot Token
wrangler secret put TELEGRAM_BOT_TOKEN
# 输入: 你的 Bot Token

# 设置 Webhook 验证密钥（自定义字符串，如 my-super-secret-2024）
wrangler secret put TELEGRAM_SECRET
# 输入: 你的自定义密钥

# 设置 Upstash Redis
wrangler secret put UPSTASH_URL
# 输入: https://xxx.upstash.io

wrangler secret put UPSTASH_TOKEN
# 输入: 你的 Upstash REST Token
```

---

## 3. 部署 Worker

```bash
cd worker
npm install
npm run deploy
```

部署成功后会输出 Worker URL，如:
```
https://news-bot-telegram.<your-subdomain>.workers.dev
```

---

## 4. 设置 Telegram Webhook

将以下 `curl` 命令中的变量替换为你的实际值：

```bash
curl -X POST "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://news-bot-telegram.<your-subdomain>.workers.dev",
    "secret_token": "<YOUR_TELEGRAM_SECRET>",
    "allowed_updates": ["message", "callback_query"],
    "max_connections": 40
  }'
```

### Windows PowerShell 版本：

```powershell
$body = @{
    url = "https://news-bot-telegram.<your-subdomain>.workers.dev"
    secret_token = "<YOUR_TELEGRAM_SECRET>"
    allowed_updates = @("message", "callback_query")
    max_connections = 40
} | ConvertTo-Json

Invoke-RestMethod `
    -Uri "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook" `
    -Method Post `
    -ContentType "application/json" `
    -Body $body
```

### 验证 Webhook 状态：

```bash
curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getWebhookInfo"
```

---

## 5. 验证

1. 打开 Telegram，搜索你的 Bot
2. 发送 `/start`，应收到欢迎消息
3. 发送 `/subscribe`，应显示分类选择按钮
4. 发送 `/settings`，应显示当前配置

---

## 6. 本地开发

```bash
cd worker
npm run dev
```

可配合 [ngrok](https://ngrok.com/) 暴露本地端口用于测试：
```bash
ngrok http 8787
```

然后将 ngrok URL 设置为 Webhook 地址。

---

## 7. 查看日志

```bash
npm run tail
```

实时查看 Worker 运行日志和错误信息。

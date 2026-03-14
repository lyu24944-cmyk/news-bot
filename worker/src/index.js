/**
 * News-Bot Telegram Webhook — Cloudflare Worker
 *
 * 处理用户订阅管理：命令解析 + InlineKeyboard 回调 + Upstash Redis 存储
 */

// ── 常量 ──────────────────────────────────────────────────

const CATEGORIES = [
  { id: "tech",    label: "🖥️ 科技" },
  { id: "world",   label: "🌍 国际" },
  { id: "finance", label: "💰 财经" },
  { id: "science", label: "🔬 科学" },
  { id: "society", label: "🏙️ 社会" },
  { id: "sports",  label: "⚽ 体育" },
  { id: "health",  label: "❤️ 健康" },
];

const DEFAULT_PREFS = {
  categories: ["tech", "world", "science"],
  min_importance: 3,
  language: "zh",
  push_times_utc: ["00:00", "06:00", "10:00"],
  timezone_offset: 8,
};

// ── Upstash Redis REST ────────────────────────────────────

async function redisRequest(env, ...args) {
  const url = env.UPSTASH_URL;
  const token = env.UPSTASH_TOKEN;
  if (!url || !token) return null;

  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(args),
    });
    if (!resp.ok) return null;
    const data = await resp.json();
    return data.result;
  } catch {
    return null;
  }
}

async function getUserPrefs(env, chatId) {
  const raw = await redisRequest(env, "GET", `user:${chatId}:prefs`);
  if (!raw) return { ...DEFAULT_PREFS };
  try {
    return JSON.parse(raw);
  } catch {
    return { ...DEFAULT_PREFS };
  }
}

async function saveUserPrefs(env, chatId, prefs) {
  await redisRequest(
    env,
    "SET",
    `user:${chatId}:prefs`,
    JSON.stringify(prefs)
  );

  // 更新用户索引（确保推送引擎能找到此用户）
  const indexRaw = await redisRequest(env, "GET", "users:index");
  let index = [];
  try {
    index = indexRaw ? JSON.parse(indexRaw) : [];
  } catch {
    index = [];
  }
  const id = String(chatId);
  if (!index.includes(id)) {
    index.push(id);
    await redisRequest(env, "SET", "users:index", JSON.stringify(index));
  }
}

// ── Telegram API ──────────────────────────────────────────

async function tgApi(env, method, body) {
  const url = `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`;
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return resp.json();
}

function sendMessage(env, chatId, text, extra = {}) {
  return tgApi(env, "sendMessage", {
    chat_id: chatId,
    text,
    parse_mode: "Markdown",
    ...extra,
  });
}

function answerCallback(env, callbackId, text = "") {
  return tgApi(env, "answerCallbackQuery", {
    callback_query_id: callbackId,
    text,
  });
}

function editMessage(env, chatId, messageId, text, replyMarkup = undefined) {
  return tgApi(env, "editMessageText", {
    chat_id: chatId,
    message_id: messageId,
    text,
    parse_mode: "Markdown",
    reply_markup: replyMarkup,
  });
}

// ── 键盘构建 ──────────────────────────────────────────────

function buildCategoryKeyboard(userCategories) {
  const rows = [];
  for (let i = 0; i < CATEGORIES.length; i += 2) {
    const row = [];
    for (let j = i; j < Math.min(i + 2, CATEGORIES.length); j++) {
      const cat = CATEGORIES[j];
      const isActive = userCategories.includes(cat.id);
      row.push({
        text: `${isActive ? "✅" : "⬜"} ${cat.label}`,
        callback_data: `toggle:${cat.id}`,
      });
    }
    rows.push(row);
  }
  rows.push([{ text: "💾 确认保存", callback_data: "confirm" }]);
  return { inline_keyboard: rows };
}

function buildSettingsKeyboard(prefs) {
  return {
    inline_keyboard: [
      // 最低重要性
      [
        { text: "⭐ 重要性 ≥1", callback_data: "set_importance:1" },
        { text: "⭐⭐ ≥2", callback_data: "set_importance:2" },
        { text: "⭐⭐⭐ ≥3", callback_data: "set_importance:3" },
      ],
      [
        { text: "⭐⭐⭐⭐ ≥4", callback_data: "set_importance:4" },
        { text: "⭐⭐⭐⭐⭐ ≥5", callback_data: "set_importance:5" },
      ],
      // 语言
      [
        { text: `${prefs.language === "zh" ? "✅" : "⬜"} 🇨🇳 中文`, callback_data: "set_lang:zh" },
        { text: `${prefs.language === "en" ? "✅" : "⬜"} 🇬🇧 English`, callback_data: "set_lang:en" },
      ],
      // 确认
      [{ text: "💾 确认保存", callback_data: "confirm" }],
    ],
  };
}

// ── 命令处理 ──────────────────────────────────────────────

async function handleStart(env, chatId) {
  const text =
    `🤖 *欢迎使用 News-Bot！*\n\n` +
    `我会根据你的偏好，定时推送精选新闻摘要。\n\n` +
    `📌 *快速开始：*\n` +
    `1️⃣ /now — 立即获取最新新闻\n` +
    `2️⃣ /subscribe — 选择你感兴趣的新闻分类\n` +
    `3️⃣ /settings — 设置推送语言和重要性阈值\n` +
    `4️⃣ 坐等新闻推送！\n\n` +
    `输入 /help 查看所有命令。`;
  return sendMessage(env, chatId, text);
}

async function handleSubscribe(env, chatId) {
  const prefs = await getUserPrefs(env, chatId);
  const text =
    `📰 *订阅管理*\n\n` +
    `点击切换你感兴趣的新闻分类：\n` +
    `（✅ = 已订阅，⬜ = 未订阅）`;
  const keyboard = buildCategoryKeyboard(prefs.categories || []);
  return sendMessage(env, chatId, text, { reply_markup: keyboard });
}

async function handleSettings(env, chatId) {
  const prefs = await getUserPrefs(env, chatId);
  const cats = (prefs.categories || []).join(", ") || "无";
  const text =
    `⚙️ *当前设置*\n\n` +
    `📂 订阅分类: ${cats}\n` +
    `⭐ 最低重要性: ${prefs.min_importance || 3}\n` +
    `🌐 推送语言: ${prefs.language === "zh" ? "🇨🇳 中文" : "🇬🇧 English"}\n` +
    `⏰ 推送时间 (UTC): ${(prefs.push_times_utc || []).join(", ")}\n\n` +
    `点击下方按钮修改设置：`;
  const keyboard = buildSettingsKeyboard(prefs);
  return sendMessage(env, chatId, text, { reply_markup: keyboard });
}

async function handleHelp(env, chatId) {
  const text =
    `📖 *命令列表*\n\n` +
    `/start — 欢迎语与使用说明\n` +
    `/now — 📰 立即获取最新新闻\n` +
    `/subscribe — 管理新闻分类订阅\n` +
    `/settings — 查看与修改推送设置\n` +
    `/help — 显示此帮助信息\n\n` +
    `💡 *Tips:*\n` +
    `• 发送 /now 随时查看最新新闻，/now 10 可查看更多\n` +
    `• 你可以随时修改订阅分类和推送偏好\n` +
    `• 新闻按重要性评分排序，只推送你关心的内容\n` +
    `• 支持中英文新闻源`;
  return sendMessage(env, chatId, text);
}

// ── Callback 处理 ─────────────────────────────────────────

async function handleCallback(env, callback) {
  const chatId = callback.message?.chat?.id;
  const messageId = callback.message?.message_id;
  const callbackId = callback.id;
  const data = callback.data || "";

  if (!chatId) {
    return answerCallback(env, callbackId, "❌ 无法识别用户");
  }

  const prefs = await getUserPrefs(env, chatId);

  // ── toggle:{category}
  if (data.startsWith("toggle:")) {
    const category = data.split(":")[1];
    const cats = prefs.categories || [];
    const idx = cats.indexOf(category);
    if (idx >= 0) {
      cats.splice(idx, 1);
    } else {
      cats.push(category);
    }
    prefs.categories = cats;
    await saveUserPrefs(env, chatId, prefs);

    const keyboard = buildCategoryKeyboard(cats);
    const catNames = cats.length > 0 ? cats.join(", ") : "无";
    await editMessage(
      env,
      chatId,
      messageId,
      `📰 *订阅管理*\n\n当前订阅: ${catNames}\n\n点击切换分类：`,
      keyboard
    );
    const label = CATEGORIES.find((c) => c.id === category)?.label || category;
    return answerCallback(env, callbackId, `${idx >= 0 ? "取消" : "已订阅"} ${label}`);
  }

  // ── set_importance:{n}
  if (data.startsWith("set_importance:")) {
    const level = parseInt(data.split(":")[1], 10);
    prefs.min_importance = Math.min(Math.max(level, 1), 5);
    await saveUserPrefs(env, chatId, prefs);

    const keyboard = buildSettingsKeyboard(prefs);
    await editMessage(
      env,
      chatId,
      messageId,
      `⚙️ *设置已更新*\n\n⭐ 最低重要性: ${prefs.min_importance}\n🌐 语言: ${prefs.language === "zh" ? "🇨🇳 中文" : "🇬🇧 English"}\n\n继续调整或点击确认：`,
      keyboard
    );
    return answerCallback(env, callbackId, `重要性已设为 ≥${prefs.min_importance}`);
  }

  // ── set_lang:{lang}
  if (data.startsWith("set_lang:")) {
    const lang = data.split(":")[1];
    prefs.language = lang === "en" ? "en" : "zh";
    await saveUserPrefs(env, chatId, prefs);

    const keyboard = buildSettingsKeyboard(prefs);
    await editMessage(
      env,
      chatId,
      messageId,
      `⚙️ *设置已更新*\n\n⭐ 最低重要性: ${prefs.min_importance}\n🌐 语言: ${prefs.language === "zh" ? "🇨🇳 中文" : "🇬🇧 English"}\n\n继续调整或点击确认：`,
      keyboard
    );
    return answerCallback(env, callbackId, `语言已设为 ${lang === "en" ? "English" : "中文"}`);
  }

  // ── confirm
  if (data === "confirm") {
    await saveUserPrefs(env, chatId, prefs);
    const cats = (prefs.categories || []).join(", ") || "无";
    await editMessage(
      env,
      chatId,
      messageId,
      `✅ *设置已保存！*\n\n` +
        `📂 订阅分类: ${cats}\n` +
        `⭐ 最低重要性: ${prefs.min_importance}\n` +
        `🌐 推送语言: ${prefs.language === "zh" ? "🇨🇳 中文" : "🇬🇧 English"}\n` +
        `⏰ 推送时间: ${(prefs.push_times_utc || []).join(", ")}\n\n` +
        `新闻将根据以上偏好推送给你 📬`
    );
    return answerCallback(env, callbackId, "✅ 设置已保存");
  }

  // ── fb:{type}:{batchId}  —  反馈按钮（👍有用 / 👎无用）
  if (data.startsWith("fb:")) {
    const parts = data.split(":");
    if (parts.length === 3) {
      const feedbackType = parts[1]; // "useful" or "useless"
      const batchId = parts[2];     // "YYYYMMDDHH"
      const dateStr = batchId.slice(0, 8);
      const userId = callback.from?.id || "unknown";

      // 1. 存储个人反馈 (TTL 30天)
      await redisRequest(
        env, "SET",
        `feedback:${batchId}:${userId}`,
        feedbackType,
        "EX", String(30 * 86400)
      );

      // 2. 更新当日聚合统计
      const statsKey = `stats:feedback:${dateStr}`;
      const existing = await redisRequest(env, "GET", statsKey);
      let stats = { useful: 0, useless: 0 };
      try { stats = existing ? JSON.parse(existing) : stats; } catch {}

      if (feedbackType === "useful") {
        stats.useful += 1;
      } else {
        stats.useless += 1;
      }

      await redisRequest(
        env, "SET", statsKey,
        JSON.stringify(stats),
        "EX", String(30 * 86400)
      );

      const emoji = feedbackType === "useful" ? "👍" : "👎";
      return answerCallback(env, callbackId, `${emoji} 感谢反馈！你的意见帮助我们改进推送质量。`);
    }
  }

  return answerCallback(env, callbackId, "❓ 未知操作");
}

// ── /now 即时新闻 ─────────────────────────────────────

function escapeMarkdown(text) {
  return text.replace(/[*_`\[]/g, (ch) => `\\${ch}`);
}

async function handleNow(env, chatId, count = 5) {
  // 1. 获取今日日期 (UTC)
  const now = new Date();
  const dateStr = now.toISOString().slice(0, 10).replace(/-/g, "");

  // 2. 从 Redis 获取所有新闻键
  const keys = await redisRequest(env, "KEYS", `news:${dateStr}:*`);
  if (!keys || keys.length === 0) {
    return sendMessage(env, chatId, "📭 今日暂无新闻数据。\n\u8bf7稍后再试，或等待下一次自动拉取。");
  }

  // 3. 读取每条新闻
  const articles = [];
  for (const key of keys) {
    const raw = await redisRequest(env, "GET", key);
    if (!raw) continue;
    try {
      const article = JSON.parse(raw);
      const ai = article.ai || {};
      if (ai.valid) {
        articles.push(article);
      }
    } catch {
      continue;
    }
  }

  if (articles.length === 0) {
    return sendMessage(env, chatId, "📭 今日新闻均未通过 AI 校验，暂无可显示内容。");
  }

  // 4. 按 importance 降序排列，取 Top N
  articles.sort((a, b) => (b.ai?.importance || 0) - (a.ai?.importance || 0));
  const topN = articles.slice(0, Math.min(count, 20));

  // 5. 格式化消息
  const timeStr = now.toISOString().slice(11, 16) + " UTC";
  const lines = [
    `📰 *实时新闻* | ${now.toISOString().slice(0, 10)}`,
    `⏰ ${timeStr} | 共 ${articles.length} 条，显示 Top ${topN.length}`,
    "",
    "━".repeat(25),
  ];

  for (let i = 0; i < topN.length; i++) {
    const art = topN[i];
    const ai = art.ai || {};
    const headline = escapeMarkdown(ai.headline || art.title || "(无标题)");
    const importance = ai.importance || 1;
    const category = ai.category || "—";
    const summary = ai.summary || "";
    const link = art.link || "";
    const stars = "⭐".repeat(Math.min(Math.max(importance, 1), 5));

    lines.push("");
    lines.push(`*${i + 1}. ${headline}*`);
    lines.push(`   ${stars} ${importance}/5 | 📂 ${category}`);

    if (summary) {
      const short = summary.length > 150 ? summary.slice(0, 147) + "…" : summary;
      lines.push(`   📝 ${escapeMarkdown(short)}`);
    }

    if (link) {
      lines.push(`   🔗 [阅读原文](${link})`);
    }

    lines.push("");
    lines.push("─".repeat(25));
  }

  lines.push("");
  lines.push("_发送 /now 10 查看更多 | /subscribe 管理订阅_");

  let message = lines.join("\n");
  // Telegram 4096 字符限制
  if (message.length > 4096) {
    message = message.slice(0, 4090) + "\n...";
  }

  return sendMessage(env, chatId, message);
}

// ── Worker 入口 ───────────────────────────────────────────

export default {
  async fetch(request, env) {
    // 只接受 POST
    if (request.method !== "POST") {
      return new Response("Method Not Allowed", { status: 405 });
    }

    try {
      const update = await request.json();

      // ── 处理命令消息 ──
      if (update.message?.text) {
        const chatId = update.message.chat.id;
        const text = update.message.text.trim();
        const command = text.split("@")[0].split(" ")[0]; // 去掉 @botname 和参数

        switch (command) {
          case "/start":
            await handleStart(env, chatId);
            break;
          case "/subscribe":
            await handleSubscribe(env, chatId);
            break;
          case "/settings":
            await handleSettings(env, chatId);
            break;
          case "/help":
            await handleHelp(env, chatId);
            break;
          case "/now": {
            // 支持 /now 或 /now 10
            const parts = text.split(/\s+/);
            const count = parts.length > 1 ? parseInt(parts[1], 10) || 5 : 5;
            await handleNow(env, chatId, count);
            break;
          }
          default:
            // 未知命令，提示使用 /help
            if (text.startsWith("/")) {
              await sendMessage(
                env,
                chatId,
                "❓ 未知命令，输入 /help 查看可用命令。"
              );
            }
        }
      }

      // ── 处理 InlineKeyboard 回调 ──
      if (update.callback_query) {
        await handleCallback(env, update.callback_query);
      }

      return new Response("OK", { status: 200 });
    } catch (err) {
      console.error("Worker error:", err);
      return new Response("Internal Server Error", { status: 500 });
    }
  },
};

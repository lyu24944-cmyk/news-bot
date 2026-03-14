/**
 * News-Bot Telegram Webhook — Cloudflare Worker
 * 重写版：简化逻辑、HTML 格式、全面错误隔离
 */

// ── 常量 ──
const CATEGORIES = [
  { id: "tech", label: "🖥️ 科技" },
  { id: "world", label: "🌍 国际" },
  { id: "finance", label: "💰 财经" },
  { id: "science", label: "🔬 科学" },
  { id: "society", label: "🏙️ 社会" },
  { id: "sports", label: "⚽ 体育" },
  { id: "health", label: "❤️ 健康" },
];

const DEFAULT_PREFS = {
  categories: ["tech", "world", "science"],
  min_importance: 3,
  language: "zh",
  push_times_utc: ["00:00", "04:00", "10:00"],
  timezone_offset: 8,
};

// ── Redis REST ──
async function redis(env, ...args) {
  try {
    const resp = await fetch(env.UPSTASH_URL, {
      method: "POST",
      headers: {
        Authorization: "Bearer " + env.UPSTASH_TOKEN,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(args),
    });
    if (!resp.ok) return null;
    const data = await resp.json();
    return data.result;
  } catch (e) {
    console.error("Redis error:", e.message);
    return null;
  }
}

// 批量执行多个 Redis 命令（Upstash pipeline API）
async function redisPipeline(env, commands) {
  try {
    var resp = await fetch(env.UPSTASH_URL + "/pipeline", {
      method: "POST",
      headers: {
        Authorization: "Bearer " + env.UPSTASH_TOKEN,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(commands),
    });
    if (!resp.ok) return null;
    var data = await resp.json();
    return data; // [{result: ...}, {result: ...}, ...]
  } catch (e) {
    console.error("Redis pipeline error:", e.message);
    return null;
  }
}

async function getPrefs(env, chatId) {
  try {
    const raw = await redis(env, "GET", "user:" + chatId + ":prefs");
    return raw ? JSON.parse(raw) : { ...DEFAULT_PREFS };
  } catch {
    return { ...DEFAULT_PREFS };
  }
}

async function savePrefs(env, chatId, prefs) {
  await redis(env, "SET", "user:" + chatId + ":prefs", JSON.stringify(prefs));
  // 更新用户索引
  try {
    const raw = await redis(env, "GET", "users:index");
    const idx = raw ? JSON.parse(raw) : [];
    const id = String(chatId);
    if (!idx.includes(id)) {
      idx.push(id);
      await redis(env, "SET", "users:index", JSON.stringify(idx));
    }
  } catch {}
}

// ── Telegram API ──
async function tg(env, method, body) {
  try {
    const resp = await fetch(
      "https://api.telegram.org/bot" + env.TELEGRAM_BOT_TOKEN + "/" + method,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }
    );
    return await resp.json();
  } catch (e) {
    console.error("TG API error:", e.message);
    return { ok: false };
  }
}

function send(env, chatId, text, extra) {
  return tg(env, "sendMessage", {
    chat_id: chatId,
    text: text,
    parse_mode: "HTML",
    ...(extra || {}),
  });
}

function answer(env, cbId, text) {
  return tg(env, "answerCallbackQuery", {
    callback_query_id: cbId,
    text: text || "",
  });
}

function editMsg(env, chatId, msgId, text, markup) {
  const body = {
    chat_id: chatId,
    message_id: msgId,
    text: text,
    parse_mode: "HTML",
  };
  if (markup) body.reply_markup = markup;
  return tg(env, "editMessageText", body);
}

// ── 时间工具 ──
function utcToLocal(utcTime, offset) {
  try {
    var s = String(utcTime || "00:00");
    var parts = s.split(":");
    var h = parseInt(parts[0], 10) || 0;
    var m = parseInt(parts[1], 10) || 0;
    var lh = ((h + offset) % 24 + 24) % 24;
    return String(lh).padStart(2, "0") + ":" + String(m).padStart(2, "0");
  } catch (e) {
    return "00:00";
  }
}

function localTimes(prefs) {
  try {
    var off = prefs.timezone_offset || 8;
    var times = prefs.push_times_utc || ["00:00", "04:00", "10:00"];
    return times.map(function (t) { return utcToLocal(t, off); }).join(", ");
  } catch (e) {
    return "08:00, 12:00, 18:00";
  }
}

// ── 键盘 ──
function catKeyboard(userCats) {
  const rows = [];
  for (let i = 0; i < CATEGORIES.length; i += 2) {
    const row = [];
    for (let j = i; j < Math.min(i + 2, CATEGORIES.length); j++) {
      const c = CATEGORIES[j];
      const on = userCats.includes(c.id);
      row.push({ text: (on ? "✅ " : "⬜ ") + c.label, callback_data: "t:" + c.id });
    }
    rows.push(row);
  }
  rows.push([{ text: "💾 确认保存", callback_data: "ok" }]);
  return { inline_keyboard: rows };
}

function settingsKeyboard(prefs) {
  return {
    inline_keyboard: [
      [
        { text: "⭐ ≥1", callback_data: "imp:1" },
        { text: "⭐⭐ ≥2", callback_data: "imp:2" },
        { text: "⭐⭐⭐ ≥3", callback_data: "imp:3" },
      ],
      [
        { text: "⭐⭐⭐⭐ ≥4", callback_data: "imp:4" },
        { text: "⭐⭐⭐⭐⭐ ≥5", callback_data: "imp:5" },
      ],
      [
        { text: (prefs.language === "zh" ? "✅" : "⬜") + " 🇨🇳 中文", callback_data: "lang:zh" },
        { text: (prefs.language === "en" ? "✅" : "⬜") + " 🇬🇧 English", callback_data: "lang:en" },
      ],
      [{ text: "💾 确认保存", callback_data: "ok" }],
    ],
  };
}

// ── HTML 转义 ──
function esc(s) {
  if (!s) return "";
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// ── 命令处理 ──

function cmdStart(env, chatId) {
  return send(env, chatId,
    "<b>🤖 欢迎使用 News-Bot！</b>\n\n" +
    "我会根据你的偏好，定时推送精选新闻摘要。\n\n" +
    "📌 <b>快速开始：</b>\n" +
    "1️⃣ /now — 立即获取最新新闻\n" +
    "2️⃣ /subscribe — 选择感兴趣的新闻分类\n" +
    "3️⃣ /settings — 设置推送偏好\n" +
    "4️⃣ /help — 查看所有命令"
  );
}

async function cmdSubscribe(env, chatId) {
  const prefs = await getPrefs(env, chatId);
  return send(env, chatId,
    "<b>📰 订阅管理</b>\n\n点击切换感兴趣的分类：\n（✅ = 已订阅，⬜ = 未订阅）",
    { reply_markup: catKeyboard(prefs.categories || []) }
  );
}

async function cmdSettings(env, chatId) {
  const prefs = await getPrefs(env, chatId);
  const cats = (prefs.categories || []).join(", ") || "无";
  const langText = prefs.language === "zh" ? "🇨🇳 中文" : "🇬🇧 English";
  return send(env, chatId,
    "<b>⚙️ 当前设置</b>\n\n" +
    "📂 订阅分类: " + cats + "\n" +
    "⭐ 最低重要性: " + (prefs.min_importance || 3) + "\n" +
    "🌐 推送语言: " + langText + "\n" +
    "⏰ 推送时间: " + localTimes(prefs) + " (北京时间)\n\n" +
    "点击下方按钮修改：",
    { reply_markup: settingsKeyboard(prefs) }
  );
}

function cmdHelp(env, chatId) {
  return send(env, chatId,
    "<b>📖 命令列表</b>\n\n" +
    "/start — 欢迎语\n" +
    "/now — 📰 立即获取最新新闻\n" +
    "/subscribe — 管理新闻分类订阅\n" +
    "/settings — 查看与修改推送设置\n" +
    "/help — 显示此帮助信息\n\n" +
    "💡 发送 /now 10 可查看更多新闻"
  );
}

async function cmdNow(env, chatId, count) {
  count = count || 5;

  // 检查 Redis 连接
  if (!env.UPSTASH_URL || !env.UPSTASH_TOKEN) {
    return send(env, chatId, "⚠️ 系统配置异常，请联系管理员。");
  }

  // 今日日期 (UTC)
  var now = new Date();
  var d = now.toISOString().slice(0, 10).replace(/-/g, "");

  // 获取所有新闻键
  var keys = await redis(env, "KEYS", "news:" + d + ":*");
  if (!keys || keys.length === 0) {
    return send(env, chatId, "📭 今日暂无新闻数据，请稍后再试。");
  }

  // 使用 pipeline 批量读取所有新闻（1次HTTP请求替代70+次）
  var getCmds = keys.map(function (k) { return ["GET", k]; });
  var results = await redisPipeline(env, getCmds);
  if (!results) {
    return send(env, chatId, "⚠️ 读取新闻失败，请稍后再试。");
  }

  var articles = [];
  for (var i = 0; i < results.length; i++) {
    try {
      var raw = results[i] && results[i].result;
      if (!raw) continue;
      var art = JSON.parse(raw);
      if (art.ai && art.ai.valid) {
        articles.push(art);
      }
    } catch (e) {}
  }

  if (articles.length === 0) {
    return send(env, chatId, "📭 今日新闻均未通过 AI 校验，暂无可显示内容。");
  }

  // 按重要性排序
  articles.sort(function (a, b) {
    return (b.ai.importance || 0) - (a.ai.importance || 0);
  });
  const top = articles.slice(0, Math.min(count, 20));

  // 格式化
  const lines = [];
  lines.push("<b>📰 实时新闻</b> | " + now.toISOString().slice(0, 10));
  lines.push("共 " + articles.length + " 条，显示 Top " + top.length);
  lines.push("━━━━━━━━━━━━━━━━━━━━");

  for (let i = 0; i < top.length; i++) {
    const art = top[i];
    const ai = art.ai || {};
    const headline = esc(ai.headline || art.title || "(无标题)");
    const imp = ai.importance || 1;
    const cat = ai.category || "—";
    const stars = "⭐".repeat(Math.min(Math.max(imp, 1), 5));

    lines.push("");
    lines.push("<b>" + (i + 1) + ". " + headline + "</b>");
    lines.push("   " + stars + " " + imp + "/5 | 📂 " + cat);

    if (ai.summary) {
      const short = ai.summary.length > 150 ? ai.summary.slice(0, 147) + "…" : ai.summary;
      lines.push("   📝 " + esc(short));
    }
    if (art.link) {
      lines.push('   🔗 <a href="' + art.link + '">阅读原文</a>');
    }
    lines.push("───────────────────");
  }

  lines.push("");
  lines.push("<i>发送 /now 10 查看更多 | /subscribe 管理订阅</i>");

  let msg = lines.join("\n");
  if (msg.length > 4090) msg = msg.slice(0, 4090) + "\n...";

  return send(env, chatId, msg);
}

// ── Callback 处理 ──
async function handleCB(env, cb) {
  const chatId = cb.message && cb.message.chat && cb.message.chat.id;
  const msgId = cb.message && cb.message.message_id;
  const cbId = cb.id;
  const data = cb.data || "";

  if (!chatId) return answer(env, cbId, "❌ 无法识别用户");

  const prefs = await getPrefs(env, chatId);

  // 切换分类
  if (data.startsWith("t:")) {
    const cat = data.slice(2);
    const cats = prefs.categories || [];
    const idx = cats.indexOf(cat);
    if (idx >= 0) { cats.splice(idx, 1); } else { cats.push(cat); }
    prefs.categories = cats;
    await savePrefs(env, chatId, prefs);
    await editMsg(env, chatId, msgId,
      "<b>📰 订阅管理</b>\n\n当前: " + (cats.join(", ") || "无") + "\n\n点击切换分类：",
      catKeyboard(cats));
    const label = CATEGORIES.find(function (c) { return c.id === cat; });
    return answer(env, cbId, (idx >= 0 ? "取消 " : "已订阅 ") + (label ? label.label : cat));
  }

  // 设置重要性
  if (data.startsWith("imp:")) {
    const lv = parseInt(data.slice(4), 10);
    prefs.min_importance = Math.min(Math.max(lv, 1), 5);
    await savePrefs(env, chatId, prefs);
    await editMsg(env, chatId, msgId,
      "<b>⚙️ 设置已更新</b>\n\n⭐ 最低重要性: " + prefs.min_importance +
      "\n🌐 语言: " + (prefs.language === "zh" ? "🇨🇳 中文" : "🇬🇧 English") +
      "\n\n继续调整或点击确认：",
      settingsKeyboard(prefs));
    return answer(env, cbId, "重要性已设为 ≥" + prefs.min_importance);
  }

  // 设置语言
  if (data.startsWith("lang:")) {
    prefs.language = data.slice(5) === "en" ? "en" : "zh";
    await savePrefs(env, chatId, prefs);
    await editMsg(env, chatId, msgId,
      "<b>⚙️ 设置已更新</b>\n\n⭐ 最低重要性: " + prefs.min_importance +
      "\n🌐 语言: " + (prefs.language === "zh" ? "🇨🇳 中文" : "🇬🇧 English") +
      "\n\n继续调整或点击确认：",
      settingsKeyboard(prefs));
    return answer(env, cbId, "语言已设为 " + (prefs.language === "en" ? "English" : "中文"));
  }

  // 确认保存
  if (data === "ok") {
    await savePrefs(env, chatId, prefs);
    const cats = (prefs.categories || []).join(", ") || "无";
    await editMsg(env, chatId, msgId,
      "<b>✅ 设置已保存！</b>\n\n" +
      "📂 订阅分类: " + cats + "\n" +
      "⭐ 最低重要性: " + prefs.min_importance + "\n" +
      "🌐 推送语言: " + (prefs.language === "zh" ? "🇨🇳 中文" : "🇬🇧 English") + "\n" +
      "⏰ 推送时间: " + localTimes(prefs) + " (北京时间)\n\n" +
      "新闻将根据以上偏好推送给你 📬");
    return answer(env, cbId, "✅ 设置已保存");
  }

  // 反馈按钮
  if (data.startsWith("fb:")) {
    const parts = data.split(":");
    if (parts.length === 3) {
      const fbType = parts[1];
      const batchId = parts[2];
      const dateStr = batchId.slice(0, 8);
      const userId = (cb.from && cb.from.id) || "unknown";
      await redis(env, "SET", "feedback:" + batchId + ":" + userId, fbType, "EX", "2592000");
      try {
        const raw = await redis(env, "GET", "stats:feedback:" + dateStr);
        const st = raw ? JSON.parse(raw) : { useful: 0, useless: 0 };
        st[fbType] = (st[fbType] || 0) + 1;
        await redis(env, "SET", "stats:feedback:" + dateStr, JSON.stringify(st), "EX", "2592000");
      } catch {}
      return answer(env, cbId, (fbType === "useful" ? "👍" : "👎") + " 感谢反馈！");
    }
  }

  return answer(env, cbId, "❓ 未知操作");
}

// ── Worker 入口 ──
export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("Method Not Allowed", { status: 405 });
    }

    try {
      const update = await request.json();

      // 处理命令
      if (update.message && update.message.text) {
        const chatId = update.message.chat.id;
        const text = update.message.text.trim();
        const cmd = text.split("@")[0].split(" ")[0];

        if (cmd === "/start") await cmdStart(env, chatId);
        else if (cmd === "/subscribe") await cmdSubscribe(env, chatId);
        else if (cmd === "/settings") await cmdSettings(env, chatId);
        else if (cmd === "/help") await cmdHelp(env, chatId);
        else if (cmd === "/now") {
          const p = text.split(/\s+/);
          const n = p.length > 1 ? parseInt(p[1], 10) || 5 : 5;
          await cmdNow(env, chatId, n);
        }
        else if (text.startsWith("/")) {
          await send(env, chatId, "❓ 未知命令，输入 /help 查看可用命令。");
        }
      }

      // 处理回调
      if (update.callback_query) {
        await handleCB(env, update.callback_query);
      }

      return new Response("OK", { status: 200 });
    } catch (err) {
      console.error("Worker error:", err.message, err.stack);
      // 即使出错也返回 200，避免 Telegram 反复重试导致更多错误
      return new Response("OK", { status: 200 });
    }
  },
};

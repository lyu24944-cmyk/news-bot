/**
 * Vercel Serverless Function — Telegram Webhook 反馈处理
 *
 * 接收 Telegram callback_query（用户点击 👍/👎 按钮后触发）。
 * 将反馈数据存入 Upstash Redis，并回复用户 "✅ 感谢反馈!"
 *
 * 环境变量:
 *   UPSTASH_URL         — Redis REST 端点
 *   UPSTASH_TOKEN       — Redis REST Token
 *   TELEGRAM_BOT_TOKEN  — Telegram Bot API Token
 *
 * callback_data 格式: fb:{type}:{batch_id}
 *   type: "useful" | "useless"
 *   batch_id: "YYYYMMDDHH" (10字符)
 *
 * Redis 写入:
 *   feedback:{batch_id}:{user_id} → "useful" | "useless"  TTL=30天
 *   stats:feedback:{YYYYMMDD}     → JSON { useful: N, useless: N }
 */

export default async function handler(req, res) {
  // 只接受 POST
  if (req.method !== 'POST') {
    return res.status(200).json({ ok: true, method: req.method });
  }

  const UPSTASH_URL = process.env.UPSTASH_URL;
  const UPSTASH_TOKEN = process.env.UPSTASH_TOKEN;
  const BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;

  if (!UPSTASH_URL || !UPSTASH_TOKEN || !BOT_TOKEN) {
    console.error('Missing env vars');
    return res.status(200).json({ ok: false, error: 'config' });
  }

  const update = req.body;

  // 只处理 callback_query（内联按钮点击）
  if (!update || !update.callback_query) {
    return res.status(200).json({ ok: true, type: 'ignored' });
  }

  const callbackQuery = update.callback_query;
  const callbackData = callbackQuery.data || '';      // "fb:useful:2026031404"
  const userId = callbackQuery.from?.id || 'unknown';
  const callbackId = callbackQuery.id;

  // 解析 callback_data
  const parts = callbackData.split(':');
  if (parts.length !== 3 || parts[0] !== 'fb') {
    return res.status(200).json({ ok: true, type: 'invalid_data' });
  }

  const feedbackType = parts[1]; // "useful" or "useless"
  const batchId = parts[2];     // "2026031404"
  const dateStr = batchId.slice(0, 8); // "20260314"

  // Redis helper
  const headers = {
    'Authorization': `Bearer ${UPSTASH_TOKEN}`,
    'Content-Type': 'application/json',
  };

  async function redis(...args) {
    const resp = await fetch(UPSTASH_URL, {
      method: 'POST',
      headers,
      body: JSON.stringify(args),
    });
    const data = await resp.json();
    return data.result;
  }

  try {
    // 1. 存储个人反馈 (TTL 30天)
    await redis(
      'SET',
      `feedback:${batchId}:${userId}`,
      feedbackType,
      'EX',
      String(30 * 86400)
    );

    // 2. 更新当日聚合统计
    const statsKey = `stats:feedback:${dateStr}`;
    const existing = await redis('GET', statsKey);
    let stats = existing ? JSON.parse(existing) : { useful: 0, useless: 0 };

    if (feedbackType === 'useful') {
      stats.useful += 1;
    } else {
      stats.useless += 1;
    }

    await redis(
      'SET',
      statsKey,
      JSON.stringify(stats),
      'EX',
      String(30 * 86400)
    );

    // 3. 回复用户（弹出提示）
    const emoji = feedbackType === 'useful' ? '👍' : '👎';
    await fetch(
      `https://api.telegram.org/bot${BOT_TOKEN}/answerCallbackQuery`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          callback_query_id: callbackId,
          text: `${emoji} 感谢反馈！你的意见帮助我们改进推送质量。`,
          show_alert: false,
        }),
      }
    );

    console.log(`Feedback: user=${userId} type=${feedbackType} batch=${batchId}`);
    return res.status(200).json({ ok: true, feedback: feedbackType });

  } catch (err) {
    console.error('Feedback error:', err.message);
    return res.status(200).json({ ok: false, error: err.message });
  }
}

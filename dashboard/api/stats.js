/**
 * Vercel Serverless Function — News-Bot 统计 API
 * 
 * 从 Upstash Redis 实时读取统计数据，返回 JSON。
 * 
 * 环境变量 (在 Vercel 中配置):
 *   UPSTASH_URL   — Upstash Redis REST 端点
 *   UPSTASH_TOKEN — Upstash REST API Token
 */

export default async function handler(req, res) {
  // CORS headers
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET');

  const UPSTASH_URL = process.env.UPSTASH_URL;
  const UPSTASH_TOKEN = process.env.UPSTASH_TOKEN;

  if (!UPSTASH_URL || !UPSTASH_TOKEN) {
    return res.status(500).json({ error: 'UPSTASH_URL or UPSTASH_TOKEN not set' });
  }

  const headers = {
    'Authorization': `Bearer ${UPSTASH_TOKEN}`,
    'Content-Type': 'application/json',
  };

  // Helper: execute a Redis command
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
    // Get today's date (UTC)
    const now = new Date();
    const today = now.toISOString().slice(0, 10).replace(/-/g, '');

    // Read all stats keys
    const [
      fetchStats,
      pushStats,
      fetchHistory,
      heartbeatFetch,
      heartbeatPush,
      feedbackStats,
    ] = await Promise.all([
      redis('GET', `stats:fetch:${today}`),
      redis('GET', `stats:push:${today}`),
      redis('GET', 'stats:fetch:history'),
      redis('GET', 'heartbeat:fetch'),
      redis('GET', 'heartbeat:push'),
      redis('GET', `stats:feedback:${today}`),
    ]);

    // Parse stats with defaults
    const fetch_data = fetchStats ? JSON.parse(fetchStats) : {
      total: 0, new: 0, deduped: 0, ai_success: 0, ai_fallback: 0, by_source: {}, by_provider: {}
    };
    const push_data = pushStats ? JSON.parse(pushStats) : {
      total_users: 0, total_news: 0, channels: {}
    };
    const history = fetchHistory ? JSON.parse(fetchHistory) : [];
    const feedback_data = feedbackStats ? JSON.parse(feedbackStats) : {
      useful: 0, useless: 0
    };

    return res.status(200).json({
      updated_at: now.toISOString(),
      date: `${today.slice(0, 4)}-${today.slice(4, 6)}-${today.slice(6, 8)}`,
      fetch: fetch_data,
      push: push_data,
      feedback: feedback_data,
      history: history.slice(-7), // Last 7 days
      health: {
        fetch_alive: heartbeatFetch !== null,
        push_alive: heartbeatPush !== null,
      },
    });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}

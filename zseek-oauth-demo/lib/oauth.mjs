import { execFile, spawn } from 'node:child_process';
import { createHash, randomBytes, timingSafeEqual } from 'node:crypto';

export const userInterfaces = [
  ['contents', '我的创作', '/api/v1/user/contents'],
  ['followees', '我的关注', '/api/v1/user/followees'],
  ['favlists', '收藏夹', '/api/v1/user/favlists'],
  ['favlist_contents', '收藏内容', '/api/v1/user/favlist_contents'],
  ['collections', '近期收藏', '/api/v1/user/collections'],
].map(([id, name, endpoint]) => ({ id, name, endpoint }));

function keychain(service, account) {
  return new Promise((resolve) => {
    execFile('/usr/bin/security', ['find-generic-password', '-s', service, '-a', account, '-w'], (error, stdout) => {
      resolve(!error ? stdout.toString().trim() : null);
    });
  });
}

function runCurl(lines) {
  return new Promise((resolve, reject) => {
    const child = spawn('curl', ['--config', '-'], { stdio: ['pipe', 'pipe', 'pipe'] });  // Windows 兼容：原模板硬编码 /usr/bin/curl
    const stdout = [];
    const stderr = [];
    child.stdout.on('data', (chunk) => stdout.push(chunk));
    child.stderr.on('data', (chunk) => stderr.push(chunk));
    child.on('close', (code) => {
      if (code !== 0) return reject(new Error(Buffer.concat(stderr).toString('utf8').trim() || '网络请求失败'));
      try { resolve(JSON.parse(Buffer.concat(stdout).toString('utf8'))); }
      catch { reject(new Error('知乎开放平台返回了无法解析的响应')); }
    });
    child.stdin.end(`${lines.join('\n')}\n`);
  });
}

function safe(value) {
  if (!value || /[\r\n"\\]/.test(value)) throw new Error('凭证格式无效');
  return value;
}

function payloadError(payload, fallback) {
  const data = payload?.data ?? payload?.Data;
  const message = typeof data === 'string' ? data : data?.message || payload?.message || payload?.Message || fallback;
  const error = new Error(String(message).slice(0, 200));
  error.code = payload?.code ?? payload?.Code ?? 'OAUTH_FAILED';
  return error;
}

function cookieId(request) {
  const value = (request.headers.cookie || '').split(';').map((item) => item.trim()).find((item) => item.startsWith('zhihu_hackathon_session='));
  return value ? decodeURIComponent(value.slice(value.indexOf('=') + 1)) : null;
}

function equal(left, right) {
  const a = Buffer.from(String(left || ''));
  const b = Buffer.from(String(right || ''));
  return a.length === b.length && timingSafeEqual(a, b);
}

function firstItem(payload) {
  return Array.isArray(payload?.Data?.Items) ? payload.Data.Items[0] || null : null;
}

function userRequestConfig(accessSecret, oauthToken, url) {
  return [
    'silent', 'show-error', 'max-time = 30', 'request = "GET"', `url = "${url}"`,
    `header = "Authorization: Bearer ${safe(accessSecret)}"`,
    `header = "X-OAuth-Token: ${safe(oauthToken)}"`,
    `header = "X-Request-Timestamp: ${Math.floor(Date.now() / 1000)}"`,
    'header = "Content-Type: application/json"',
  ];
}

export function createOAuth(config) {
  const sessions = new Map();
  const oauthConfig = config.oauth;

  function session(request, response) {
    let id = cookieId(request);
    let current = id ? sessions.get(id) : null;
    if (!current) {
      id = randomBytes(24).toString('base64url');
      current = { id, state: null, token: null, expiresAt: null, profile: null, stateVerified: null, error: null };
      sessions.set(id, current);
      response.setHeader('Set-Cookie', `zhihu_hackathon_session=${id}; HttpOnly; SameSite=Lax; Path=/; Max-Age=28800`);
    }
    return current;
  }

  async function credentials() {
    const [appKey, accessSecret] = await Promise.all([
      process.env.ZHIHU_OAUTH_APP_KEY || keychain(oauthConfig.credentialService, oauthConfig.credentialAccount),
      process.env.ZHIHU_ACCESS_SECRET || keychain('zhihu-cli', 'access-secret'),
    ]);
    return { appKey, accessSecret };
  }

  async function status(request, response) {
    const current = session(request, response);
    const creds = await credentials();
    if (current.expiresAt && current.expiresAt <= Date.now()) {
      current.token = null;
      current.profile = null;
      current.error = { code: 'TOKEN_EXPIRED', message: '授权已过期，请重新连接。' };
    }
    return {
      configured: Boolean(creds.appKey && creds.accessSecret && oauthConfig.redirectUri),
      callbackConfigured: Boolean(oauthConfig.redirectUri),
      authorized: Boolean(current.token),
      appId: oauthConfig.appId,
      redirectUri: oauthConfig.redirectUri,
      profile: current.profile,
      stateVerified: current.stateVerified,
      expiresAt: current.expiresAt ? new Date(current.expiresAt).toISOString() : null,
      error: current.error,
      interfaces: userInterfaces,
    };
  }

  async function start(request, response) {
    const current = session(request, response);
    if (!oauthConfig.redirectUri) {
      throw Object.assign(new Error('本地地址无法完成知乎登录。请先部署应用并配置公网回调地址。'), { code: 'DEPLOYMENT_REQUIRED' });
    }
    const { appKey } = await credentials();
    if (!appKey) throw Object.assign(new Error('OAuth app_key 尚未配置'), { code: 'APP_KEY_REQUIRED' });
    current.state = randomBytes(24).toString('base64url');
    current.error = null;
    const url = new URL('https://openapi.zhihu.com/authorize');
    url.searchParams.set('redirect_uri', oauthConfig.redirectUri);
    url.searchParams.set('app_id', oauthConfig.appId);
    url.searchParams.set('response_type', 'code');
    url.searchParams.set('state', current.state);
    return url.toString();
  }

  async function callback(request, response, url) {
    const current = session(request, response);
    const code = url.searchParams.get('authorization_code') || url.searchParams.get('code');
    const returnedState = url.searchParams.get('state');
    if (!code) throw Object.assign(new Error('回调缺少 authorization_code'), { code: 'CODE_MISSING' });
    if (returnedState && !equal(returnedState, current.state)) {
      throw Object.assign(new Error('state 校验失败'), { code: 'STATE_MISMATCH' });
    }
    const { appKey, accessSecret } = await credentials();
    if (!appKey || !accessSecret) throw new Error('后端凭证配置不完整');
    const form = new URLSearchParams({
      app_id: oauthConfig.appId,
      app_key: safe(appKey),
      grant_type: 'authorization_code',
      redirect_uri: oauthConfig.redirectUri,
      code: safe(code),
    }).toString();
    const payload = await runCurl([
      'silent', 'show-error', 'max-time = 20', 'request = "POST"',
      'url = "https://openapi.zhihu.com/access_token"',
      'header = "Content-Type: application/x-www-form-urlencoded"', `data = "${form}"`,
    ]);
    const token = payload?.access_token || payload?.data?.access_token || payload?.Data?.access_token;
    if (!token) throw payloadError(payload, '未获得 OAuth access token');
    const expiresIn = Number(payload?.expires_in ?? payload?.data?.expires_in ?? payload?.Data?.expires_in);
    current.token = token;
    current.expiresAt = Number.isFinite(expiresIn) ? Date.now() + expiresIn * 1000 : null;
    current.stateVerified = Boolean(returnedState);
    current.state = null;
    current.error = null;

    try {
      const profilePayload = await runCurl(userRequestConfig(accessSecret, token, 'https://openapi.zhihu.com/user'));
      const source = profilePayload?.data || profilePayload?.Data || profilePayload?.user || null;
      if (source && typeof source === 'object') {
        current.profile = {
          name: source.name || source.Fullname || source.fullname || null,
          avatarUrl: source.avatar_url || source.AvatarUrl || null,
          headline: source.headline || source.Headline || null,
          url: source.url || source.Url || null,
        };
      }
    } catch { current.profile = null; }
  }

  async function runAll(request, response) {
    const current = session(request, response);
    if (!current.token) throw Object.assign(new Error('请先完成知乎账号授权'), { code: 'LOGIN_REQUIRED' });
    const { accessSecret } = await credentials();
    if (!accessSecret) throw new Error('开放平台 Access Secret 未配置');
    const context = {};
    const results = [];
    for (const definition of userInterfaces) {
      let query = { Limit: '1' };
      if (definition.id === 'contents') query = { ...query, ContentType: 'all', Offset: '0', SortField: 'ts', SortOrder: 'desc' };
      if (definition.id === 'followees') query.Offset = '0';
      if (definition.id === 'favlist_contents') {
        if (!context.favlistToken) {
          results.push({ ...definition, status: 'empty', item: null, message: '账号没有可用于测试的收藏夹。' });
          continue;
        }
        query = { ...query, FavlistUrlToken: String(context.favlistToken), Offset: '0' };
      }
      try {
        const payload = await runCurl(userRequestConfig(
          accessSecret,
          current.token,
          `https://developer.zhihu.com${definition.endpoint}?${new URLSearchParams(query)}`,
        ));
        if (payload?.Code !== 0) throw payloadError(payload, '用户数据接口失败');
        const item = firstItem(payload);
        if (definition.id === 'favlists' && item?.UrlToken) context.favlistToken = item.UrlToken;
        results.push({ ...definition, status: item ? 'success' : 'empty', item, message: item ? null : '接口成功但没有数据。' });
      } catch (error) {
        results.push({ ...definition, status: 'error', item: null, message: error.message });
      }
    }
    return results;
  }

  function logout(request, response) {
    const current = session(request, response);
    current.token = null; current.expiresAt = null; current.profile = null; current.state = null; current.stateVerified = null; current.error = null;
  }

  function record(request, response, error) {
    session(request, response).error = { code: String(error.code || 'OAUTH_FAILED'), message: String(error.message).slice(0, 200) };
  }

  // ---- 个人遗珠报告：登录用户的回答 × 知寻 U 值 ----
  // 协议：拉取「我的创作」(answer)，按问题分组，查知寻后端缓存。
  // 仅分析用户勾选的问题（省 token）：analyze=[qid...] 触发，之后轮询到完成。
  const ZSEEK_BACKEND = (process.env.ZSEEK_BACKEND || 'http://127.0.0.1:8010').replace(/\/$/, '');
  const PERSONAL_MAX_QUESTIONS = 10;
  const CONTENTS_CACHE_TTL = 10 * 60 * 1000;
  const contentsCache = new Map(); // sessionId -> { at, items }
  const triggered = new Map();     // sessionId -> Set(qid) 本会话已触发的分析

  const topicKey = (title) => createHash('sha1').update(String(title), 'utf8').digest('hex').slice(0, 12);

  async function personal(request, response, analyze = []) {
    const current = session(request, response);
    if (!current.token) throw Object.assign(new Error('请先完成知乎账号授权'), { code: 'LOGIN_REQUIRED' });
    const { accessSecret } = await credentials();
    if (!accessSecret) throw new Error('开放平台 Access Secret 未配置');

    let cached = contentsCache.get(current.id);
    if (!cached || Date.now() - cached.at > CONTENTS_CACHE_TTL) {
      const items = [];
      let lastCode = null;
      for (const offset of [0, 20, 40]) {
        const payload = await runCurl(userRequestConfig(
          accessSecret, current.token,
          `https://developer.zhihu.com/api/v1/user/contents?ContentType=answer&Limit=20&Offset=${offset}&SortField=ts&SortOrder=desc`,
        ));
        lastCode = payload?.Code ?? payload?.code ?? null;
        const page = Array.isArray(payload?.Data?.Items) ? payload.Data.Items : [];
        // 放宽过滤：只要求 ContentType=answer 且有 Url；问题 id 解析失败的在下一步计为 skipped
        items.push(...page.filter((it) => it.ContentType === 'answer' && it.Url));
        if (page.length < 20) break;
      }
      console.error(`[personal] contents: Code=${lastCode} 原始=${items.length} 条，URL 样例=${items.slice(0, 3).map((it) => String(it.Url).slice(0, 60)).join(' | ') || '无'}，标题样例=${items.slice(0, 3).map((it) => String(it.Title || '(空)').slice(0, 40)).join(' | ') || '无'}`);
      if (items.length) {
        cached = { at: Date.now(), items: items.slice(0, 30) };
        contentsCache.set(current.id, cached);  // 空结果不缓存，便于重试
      } else {
        cached = { at: 0, items: [] };
      }
    }

    const parsed = cached.items.map((it) => {
      const u = String(it.Url);
      let qid = null;
      let aid = null;
      let m = u.match(/question\/(\d+)[/?#]answer[/?#]?(\d+)/) || u.match(/question\/(\d+).*answer[/?#](\d+)/);
      if (m) {
        qid = m[1];
        aid = m[2];
      } else {
        // 短链 https://www.zhihu.com/answer/{aid}：无问题 id，用 aid 占位当分组键（title 才是后端 key 的来源）
        const ma = u.match(/answer\/(\d+)/);
        const mq = u.match(/[?&]question=(\d+)/) || u.match(/question[=/](\d+)/);
        if (ma) { aid = ma[1]; qid = mq ? mq[1] : `a${aid}`; }
      }
      return qid && aid
        ? { qid, aid, url: it.Url, title: it.Title || '', likes: it.LikeCount ?? 0, createdAt: it.CreatedAt ?? 0 }
        : null;
    });
    const answers = parsed.filter(Boolean);
    if (cached.items.length && !answers.length) {
      console.error('[personal] 警告：contents 返回了回答但 URL 均无法解析出 question/answer id');
    }

    const byQ = new Map();
    for (const a of answers) {
      if (!byQ.has(a.qid)) {
        if (byQ.size >= PERSONAL_MAX_QUESTIONS) continue;
        byQ.set(a.qid, { qid: a.qid, title: a.title, url: a.url, mine: [] });
      }
      byQ.get(a.qid).mine.push(a);
    }

    const want = new Set(analyze.map(String));
    const mineTriggered = triggered.get(current.id) || new Set();
    const questions = [];
    for (const q of byQ.values()) {
      const key = topicKey(q.title);
      let res;
      try { res = await fetch(`${ZSEEK_BACKEND}/api/result/${key}`); } catch { res = { ok: false, status: 0 }; }
      if (res.ok) {
        const report = await res.json();
        const pool = (report.answers || []).filter((x) => x.underestimate_index != null);
        const n = pool.length;
        const rankOf = (arr, hit) => arr.indexOf(hit);
        const byVotes = [...pool].sort((x, y) => (y.votes ?? 0) - (x.votes ?? 0));
        const byV = [...pool].sort((x, y) => (y.info_score ?? 0) - (x.info_score ?? 0));
        const pct = (arr, hit) => (n > 1 ? (n - 1 - rankOf(arr, hit)) / (n - 1) : 1);
        const mine = q.mine.map((a) => {
          const hit = pool.find((x) => (x.url || '').includes(`/answer/${a.aid}`) || String(x.content_id || '').includes(a.aid));
          if (!hit) return { ...a, matched: false };
          const expoPct = pct(byVotes, hit);
          const valuePct = pct(byV, hit);
          return {
            ...a, matched: true, U: hit.underestimate_index, V: hit.info_score,
            votes: hit.votes ?? 0, badges: hit.badges || [], reason: hit.excavation_reason || '',
            expo_pct: expoPct, value_pct: valuePct, gap: valuePct - expoPct,
          };
        });
        questions.push({ qid: q.qid, title: q.title, url: q.url, key, status: 'done', sample_size: report.sample_size ?? n, mine });
      } else if (res.status === 404) {
        // 未缓存：只有用户勾选才触发分析（后端按 key 幂等去重，重复触发安全）
        if (want.has(q.qid) && !mineTriggered.has(q.qid)) {
          try {
            // 仅当 qid 是真实问题 id 时才传 question_url（短链回答无问题 id，枚举通道会 10001）
            const numericQid = /^\d+$/.test(q.qid);
            const ar = await fetch(`${ZSEEK_BACKEND}/api/analyze`, {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ title: q.title, question_url: numericQid ? `https://www.zhihu.com/question/${q.qid}` : undefined }),
            });
            let arStatus = 'queued';
            try { arStatus = (await ar.json()).status || 'queued'; } catch { /* 保持 queued */ }
            mineTriggered.add(q.qid);
            triggered.set(current.id, mineTriggered);
            questions.push({ qid: q.qid, title: q.title, url: q.url, key, status: arStatus === 'running' ? 'running' : 'queued', mine: q.mine.map((a) => ({ ...a, matched: false })) });
            continue;
          } catch { /* 后端暂不可用时保持 pending */ }
        }
        let status = mineTriggered.has(q.qid) ? 'running' : 'pending';
        let position = null;
        let stage = null;
        if (status === 'running') {
          // 已触发但缓存未出：查后端任务状态；error/none（含后端重启丢失）释放触发锁，允许用户重试
          try {
            const sj = await fetch(`${ZSEEK_BACKEND}/api/jobs/${key}/status`);
            const s = await sj.json();
            if (s.status === 'error') {
              status = 'error';
              mineTriggered.delete(q.qid);
              triggered.set(current.id, mineTriggered);
            } else if (s.status === 'queued') {
              status = 'queued';
              position = s.position ?? null;
            } else if (s.status === 'running') {
              stage = {
                done: s.stage_done ?? 0, total: s.stage_total ?? 0, current: s.stage_current || null,
                article: s.current_article || null,
                ap: s.article_total ? { done: s.article_done ?? 0, total: s.article_total } : null,
              };
            } else if (s.status !== 'running') {
              status = 'pending';
              mineTriggered.delete(q.qid);
              triggered.set(current.id, mineTriggered);
            }
          } catch { /* 状态查询失败保持 running */ }
        }
        questions.push({ qid: q.qid, title: q.title, url: q.url, key, status, position, stage, mine: q.mine.map((a) => ({ ...a, matched: false })) });
      } else {
        questions.push({ qid: q.qid, title: q.title, url: q.url, key, status: 'error', mine: q.mine.map((a) => ({ ...a, matched: false })) });
      }
    }

    const done = questions.filter((x) => x.status === 'done');
    const matched = done.flatMap((x) => x.mine.filter((m) => m.matched));
    return {
      summary: {
        answers_total: answers.length,
        raw_contents: cached.items.length,
        questions: questions.length,
        done: done.length,
        pending: questions.filter((x) => x.status === 'pending').length,
        queued: questions.filter((x) => x.status === 'queued').length,
        running: questions.filter((x) => x.status === 'running').length,
        matched: matched.length,
        pearls: matched.filter((m) => (m.badges || []).some((b) => String(b).includes('遗珠'))).length,
        buried: matched.filter((m) => m.gap > 0.3).length,
        avg_gap: matched.length ? Math.round((matched.reduce((s, m) => s + m.gap, 0) / matched.length) * 100) / 100 : null,
      },
      questions,
    };
  }

  return { status, start, callback, runAll, logout, record, personal };
}

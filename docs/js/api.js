/**
 * api.js — 后端 REST 契约访问层（同源）。
 *
 * 覆盖《01-总体设计.md》§5.1 中前端需要的端点：
 *   GET /api/occurrences、/api/calendar/counts、/api/venues、/api/stats
 *   GET /api/ics（只用于生成订阅链接，页面不请求它）
 *   POST /api/submissions（纠错 / 投稿）
 *
 * demo 模式（?demo=1）或用户主动降级时，改用 sample-data.js，不发任何网络请求。
 * 所有失败都归一化为 ApiError，交给界面渲染明确的错误态（不允许白屏）。
 */
import {
  getSampleVenues,
  getSampleStats,
  getSampleCounts,
  querySampleOccurrences,
} from './sample-data.js';
import { filterOccurrences, pickCounts } from './static-data.js';

const TIMEOUT_MS = 12000;

/**
 * 静态模式：没有后端，数据来自构建时导出的 JSON（data/*.json）。
 *
 * 触发条件（任一）：
 *   * index.html 里注入了 `window.__STATIC__ = true`（build-static 生成）
 *   * 直接以 file:// 打开
 *   * 页面在 github.io 上
 *
 * 为什么需要它：GitHub Pages 只能托管静态文件。静态站可完整浏览
 * （筛选/月历/详情/来源跳转），但没有采集、纠错投稿与 .ics 订阅。
 */
const STATIC = Boolean(
  (typeof window !== 'undefined' && window.__STATIC__)
  || (typeof location !== 'undefined'
      && (location.protocol === 'file:' || /\.github\.io$/.test(location.hostname))),
);

export function isStatic() {
  return STATIC;
}

/** 静态数据缓存（每个文件只取一次） */
const staticCache = new Map();

async function getStaticJSON(name) {
  if (staticCache.has(name)) return staticCache.get(name);
  const p = fetch(`data/${name}`, { cache: 'force-cache' }).then((res) => {
    if (!res.ok) throw new ApiError(`静态数据缺失：data/${name}（HTTP ${res.status}）`, {
      status: res.status, url: `data/${name}`,
    });
    return res.json();
  }).catch((err) => {
    staticCache.delete(name); // 失败不缓存，允许重试
    if (err instanceof ApiError) throw err;
    throw new ApiError(`无法读取静态数据 data/${name}`, { url: `data/${name}` });
  });
  staticCache.set(name, p);
  return p;
}

export class ApiError extends Error {
  constructor(message, { status = 0, url = '' } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.url = url;
  }
}

/** 过滤掉空值后拼 query（后端把空串视为「不筛选」）。
 *
 * 数组值用**重复参数**（`flag=a&flag=b`）而不是逗号拼接，
 * 这样 FastAPI 侧 `flag: list[str]` 能直接拿到列表。
 */
export function buildQuery(params = {}) {
  const sp = new URLSearchParams();
  Object.keys(params).forEach((key) => {
    const v = params[key];
    if (v === undefined || v === null || v === '') return;
    if (Array.isArray(v)) {
      v.filter((x) => x !== undefined && x !== null && x !== '')
        .forEach((x) => sp.append(key, String(x)));
      return;
    }
    sp.set(key, String(v));
  });
  const s = sp.toString();
  return s ? `?${s}` : '';
}

async function getJSON(path, params) {
  const url = `${path}${buildQuery(params)}`;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  let res;
  try {
    res = await fetch(url, {
      signal: ctrl.signal,
      headers: { Accept: 'application/json' },
      credentials: 'same-origin',
      cache: 'no-store',
    });
  } catch (err) {
    const aborted = err && err.name === 'AbortError';
    throw new ApiError(aborted ? `请求超时（${TIMEOUT_MS / 1000} 秒）` : '无法连接到后端 API', { url });
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) throw new ApiError(`后端返回 HTTP ${res.status}`, { status: res.status, url });
  try {
    return await res.json();
  } catch (err) {
    throw new ApiError('后端返回的内容不是合法 JSON', { status: res.status, url });
  }
}

/** 由筛选状态生成 /api/occurrences 的查询参数。 */
export function occurrenceParams(filters = {}, extra = {}) {
  // 独立标记用重复参数（flag=a&flag=b），语义是「与」——两者都命中才返回
  const flags = Array.isArray(filters.flags) ? filters.flags : [];
  return {
    from: filters.from,
    to: filters.to,
    city: filters.city,
    kind: filters.kind,
    is_idol: filters.is_idol,
    flag: flags.length ? flags.map((f) => (f === 'girl_band' ? '女子乐队' : 'acg')) : undefined,
    venue_id: filters.venue_id,
    artist_id: filters.artist_id,
    price_max: filters.price_max,
    status: filters.status,
    q: filters.q,
    ...extra,
  };
}

/**
 * 主查询。返回 { items, total, generated_at, demo }。
 * 后端约定 items 为 occurrence 数组（字段见设计稿 §5.2）。
 */
export async function loadOccurrences(filters = {}, { demo = false } = {}) {
  if (demo) {
    const data = querySampleOccurrences(filters);
    // 轻微延时，让骨架屏在 demo 下也能被看到（真实请求天然有延时）
    await sleep(120);
    return { ...data, demo: true };
  }
  if (STATIC) {
    const bundle = await getStaticJSON('occurrences.json');
    const all = Array.isArray(bundle.items) ? bundle.items : [];
    const matched = filterOccurrences(all, occurrenceParams(filters));
    const page = Number(filters.page || 1);
    const size = Number(filters.page_size || 200);
    const start = (page - 1) * size;
    return {
      items: matched.slice(start, start + size),
      total: matched.length,
      generated_at: bundle.generated_at || '',
      demo: false,
      static: true,
    };
  }
  const params = occurrenceParams(filters, { page: filters.page || 1, page_size: filters.page_size || 200 });
  const data = await getJSON('/api/occurrences', params);
  return {
    items: Array.isArray(data.items) ? data.items : [],
    total: typeof data.total === 'number' ? data.total : (data.items || []).length,
    generated_at: data.generated_at || '',
    demo: false,
  };
}

/** 月历轻量计数：{ days: { '2026-10-08': { band, idol, followed } } }。 */
export async function loadCounts(month, city, { demo = false } = {}) {
  if (demo) {
    await sleep(80);
    return { ...getSampleCounts(month, city), demo: true };
  }
  if (STATIC) {
    let file = null;
    try {
      file = await getStaticJSON(`calendar/${month}.json`);
    } catch (err) {
      // 该月没有数据文件是正常情况（不在导出范围内），返回空而不是报错
      return { days: {}, demo: false, static: true };
    }
    return { days: pickCounts(file, city), demo: false, static: true };
  }
  const data = await getJSON('/api/calendar/counts', { month, city });
  return { days: (data && data.days) || {}, demo: false };
}

/** 场地下拉。 */
export async function loadVenues({ demo = false } = {}) {
  if (demo) return { items: getSampleVenues(), demo: true };
  if (STATIC) {
    const data = await getStaticJSON('venues.json');
    return { items: Array.isArray(data.items) ? data.items : [], demo: false, static: true };
  }
  const data = await getJSON('/api/venues', {});
  return { items: Array.isArray(data.items) ? data.items : [], demo: false };
}

/** 源健康度 / 总量统计（页脚展示，不阻塞主流程）。 */
export async function loadStats({ demo = false } = {}) {
  if (demo) return { ...getSampleStats(), demo: true };
  if (STATIC) {
    const data = await getStaticJSON('stats.json');
    return { ...data, demo: false, static: true };
  }
  const data = await getJSON('/api/stats', {});
  return { ...data, demo: false };
}

/** 静态站的元信息（生成时间、可用月份等），供界面提示「数据快照时间」。 */
export async function loadStaticMeta() {
  if (!STATIC) return null;
  try {
    return await getStaticJSON('meta.json');
  } catch (err) {
    return null;
  }
}

/* ------------------------------------------------------------------ */
/* 功能开关                                                            */
/* ------------------------------------------------------------------ */

/** 读取后端功能开关（拿不到时抛错，调用方回退到 features.js 的静态值）。 */
export async function loadFeatures() {
  const data = await getJSON('/api/features', {});
  return (data && data.features) || null;
}

/* ------------------------------------------------------------------ */
/* 采集任务（页面上的「更新数据」按钮）                                  */
/* ------------------------------------------------------------------ */

/** 启动一轮采集；返回 job 快照。已有任务在跑时会返回那一个（不报错）。 */
export async function startUpdate({ useBrowser = false } = {}) {
  const res = await fetch(
    `/api/admin/update${buildQuery({ use_browser: useBrowser ? 'true' : '' })}`,
    {
      method: 'POST',
      headers: { Accept: 'application/json' },
      credentials: 'same-origin',
      cache: 'no-store',
    },
  );
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const j = await res.json();
      if (j && j.detail) detail = j.detail;
    } catch (err) { /* 忽略：后端没返回 JSON */ }
    throw new ApiError(`无法启动更新：${detail}`, { status: res.status });
  }
  const data = await res.json();
  return data.job;
}

/** 查询任务状态（轮询用，超时给短一点避免卡住 UI）。 */
export async function loadUpdateStatus(jobId = '') {
  const path = jobId ? `/api/admin/update/${jobId}` : '/api/admin/update';
  const data = await getJSON(path, {});
  return jobId ? data.job : data;
}

/**
 * .ics 订阅链接（一条 URL 就是一个日历，设计稿 §5.3）。
 * 注意：**不带 from/to** —— 订阅关心的是「未来所有该城市/该类型的场次」，
 * 若把当前周/月的范围写进订阅链接，日历客户端只会拿到一小段。
 * 只拼接链接供「复制订阅链接」使用，前端不请求它。
 */
export function buildIcsUrl(filters = {}) {
  return `/api/ics${buildQuery({
    city: filters.city,
    is_idol: filters.is_idol,
    venue_id: filters.venue_id,
    artist_id: filters.artist_id,
  })}`;
}

/** 纠错 / 投稿：POST /api/submissions → review_task。 */
export async function submitCorrection(payload) {
  let res;
  try {
    res = await fetch('/api/submissions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify(payload),
    });
  } catch (err) {
    throw new ApiError('无法连接到后端 API', { url: '/api/submissions' });
  }
  if (!res.ok) throw new ApiError(`HTTP ${res.status}`, { status: res.status, url: '/api/submissions' });
  try {
    return await res.json();
  } catch (err) {
    return {};
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

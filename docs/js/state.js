/**
 * state.js — 筛选状态 ↔ URL query 双向同步。
 *
 * 设计要求（§4.7）：筛选器状态全部写进 URL，可分享、可收藏、刷新不丢，
 * 并且支持浏览器前进/后退（app.js 监听 popstate 重新读状态）。
 *
 * URL 参数（前 5 个是契约要求的，其余为视图记忆）：
 *   from / to / city / is_idol / venue_id / q      ← 设计稿 §4.4 明确要求
 *   kind / status / price_max / view / month / d / demo
 * `range` 不单独入 URL：from/to 已经完整描述了时间范围，预设由 detectPreset 反推。
 */
import { presetRange, detectPreset, todayISO, monthKey } from './util.js';

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const MONTH_RE = /^\d{4}-\d{2}$/;
const PRESETS = ['week', 'weekend', 'month'];

function normDate(v) {
  return DATE_RE.test(String(v || '')) ? String(v) : '';
}

function pickEnum(v, allowed) {
  const s = String(v || '');
  return allowed.includes(s) ? s : '';
}

/** 读取 URL → 状态对象；缺失 from/to 时按「本周」补齐。 */
export function readState(search = window.location.search) {
  const p = new URLSearchParams(search);
  const ref = todayISO();

  let from = normDate(p.get('from'));
  let to = normDate(p.get('to'));
  let preset = pickEnum(p.get('range'), PRESETS) || 'week';
  if (!from || !to) {
    const r = presetRange(preset, ref);
    from = r.from;
    to = r.to;
  } else {
    if (to < from) {
      const t = from;
      from = to;
      to = t;
    }
    preset = detectPreset(from, to, ref);
  }

  const isIdolRaw = p.get('is_idol');
  const isIdol = isIdolRaw === 'true' || isIdolRaw === 'false' ? isIdolRaw : '';
  const venueId = p.get('venue_id');
  const priceMax = p.get('price_max');

  // 独立分类标记（可多选，**「与」关系**）：女子乐队 / acg
  // 逗号分隔存在 URL 里，保持链接简短可分享
  const FLAG_KEYS = ['girl_band', 'acg'];
  const flags = String(p.get('flags') || '')
    .split(',')
    .map((s) => s.trim().toLowerCase())
    .filter((s) => FLAG_KEYS.includes(s));

  const month = MONTH_RE.test(String(p.get('month') || '')) ? String(p.get('month')) : monthKey(from);
  const d = normDate(p.get('d'));
  // 默认城市 = 广州（设计稿 §4.3 首页蓝图为「广州」）；`?city=` 显式传空串 = 全省
  const cityParam = p.get('city');

  return {
    from,
    to,
    preset,
    city: cityParam === null ? '广州' : cityParam,
    kind: String(p.get('kind') || ''),
    is_idol: isIdol,
    flags,
    venue_id: venueId ? String(venueId) : '',
    venue_type: String(p.get('venue_type') || ''),
    status: String(p.get('status') || ''),
    price_max: priceMax ? String(priceMax) : '',
    q: String(p.get('q') || ''),
    // 艺人/团体名搜索。与 `q` 分开：`q` 只搜**标题**，
    // `artist` 搜**演出阵容**（能捞到标题里没有的团，实测有效）。
    artist: String(p.get('artist') || ''),
    view: p.get('view') === 'calendar' ? 'calendar' : 'list',
    month,
    d,
    demo: p.get('demo') === '1',
  };
}

/** 状态 → query 字符串（固定顺序，便于阅读与分享）。 */
export function toQuery(state) {
  const p = new URLSearchParams();
  p.set('from', state.from);
  p.set('to', state.to);
  if (state.city) p.set('city', state.city);
  if (state.kind) p.set('kind', state.kind);
  if (state.is_idol) p.set('is_idol', state.is_idol);
  if (state.flags && state.flags.length) p.set('flags', state.flags.join(','));
  if (state.venue_id) p.set('venue_id', state.venue_id);
  if (state.venue_type) p.set('venue_type', state.venue_type);
  if (state.status) p.set('status', state.status);
  if (state.price_max) p.set('price_max', state.price_max);
  if (state.q) p.set('q', state.q);
  if (state.artist) p.set('artist', state.artist);
  if (state.view && state.view !== 'list') p.set('view', state.view);
  if (state.month) p.set('month', state.month);
  if (state.d) p.set('d', state.d);
  if (state.demo) p.set('demo', '1');
  return p.toString();
}

/**
 * 写入 URL。
 * mode='push'（默认）用于离散筛选动作（进历史，支持后退）；
 * mode='replace' 用于输入框连续输入与初始化归一化（避免污染历史）。
 */
export function writeState(state, { mode = 'push' } = {}) {
  const qs = toQuery(state);
  const url = `${window.location.pathname}${qs ? `?${qs}` : ''}`;
  if (mode === 'replace') window.history.replaceState({ gd: true }, '', url);
  else window.history.pushState({ gd: true }, '', url);
}

/** 纯函数版：根据状态生成"分享用"完整链接。 */
export function shareUrl(state) {
  const qs = toQuery(state);
  return `${window.location.origin}${window.location.pathname}${qs ? `?${qs}` : ''}`;
}

/** 切换预设时间范围（本周 / 本周末 / 本月）。 */
export function withPreset(state, preset, ref = todayISO()) {
  const r = presetRange(preset, ref);
  const next = { ...state, from: r.from, to: r.to, preset };
  // 月历跟随范围起始日所在月份，避免「切了范围但月历还停在上个月」
  if (preset === 'month') next.month = monthKey(r.from);
  if (next.d && (next.d < r.from || next.d > r.to)) next.d = '';
  return next;
}

/** 选择某一天（月历格子 / 当日面板）：同时校正 month 与 from/to 的一致性。 */
export function withDay(state, iso) {
  const next = { ...state, d: iso, month: monthKey(iso) };
  if (iso < next.from || iso > next.to) {
    // 点到范围外的一天：列表视图把范围收窄为该日，保证左侧能看到内容
    if (state.view === 'list') {
      next.from = iso;
      next.to = iso;
      next.preset = 'custom';
    }
  }
  return next;
}

/** 换月（月历翻页）。 */
export function withMonth(state, month) {
  const next = { ...state, month };
  if (next.d && monthKey(next.d) !== month) next.d = '';
  return next;
}

/**
 * 直接设置日期范围（日期选择控件用）。
 *
 * 与 `withPreset` 的区别：预设是「算出来的范围」，这里是**用户手选**的，
 * 所以 `preset` 归零为 `custom`（否则 UI 会高亮一个与实际范围不符的预设）。
 * 任意一端为空时，用另一端补全 —— 避免出现 `from > to` 的空区间。
 */
export function withRange(state, from, to) {
  let f = normDate(from) || state.from;
  let t = normDate(to) || state.to;
  if (f && t && t < f) {
    // 用户把结束日拉到起始日之前：以**刚改的那端**为准（不静默交换，
    // 否则用户会看到自己没输入过的值）
    if (normDate(to)) f = t;
    else t = f;
  }
  const next = { ...state, from: f, to: t, preset: 'custom' };
  // 选中日若移出范围就清掉，避免列表与月历不一致
  if (next.d && (next.d < f || next.d > t)) next.d = '';
  return next;
}

/** 便捷：当前月的前/后一月（用 UTC 归一化，跨年也正确）。 */
export function shiftMonth(month, delta) {
  const [y, m] = String(month).split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1 + delta, 1));
  return `${dt.getUTCFullYear()}-${String(dt.getUTCMonth() + 1).padStart(2, '0')}`;
}

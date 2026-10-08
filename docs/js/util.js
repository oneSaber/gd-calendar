/**
 * util.js — 通用工具：日期/时间解析、格式化、文本与 DOM 辅助。
 *
 * 设计要点：**一切时间按字符串字面量处理**。
 * 后端返回的是 Asia/Shanghai 的 ISO 串（如 2026-10-08T19:30:00+08:00），
 * 若交给 `new Date()` 再取本地年月日，浏览器时区不同就会出现日期漂移
 * （例如 UTC 环境下 "今天" 会错一天）。这里直接正则取字面量，保证跨时区一致。
 */

/** 一周中文名，索引 0 = 周日（与 Date#getUTCDay 对齐）。 */
export const WEEK_CN = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

const ISO_RE = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/;

export function pad2(n) {
  return String(n).padStart(2, '0');
}

/** 解析 ISO 串，返回 { date:'YYYY-MM-DD', time:'HH:MM', iso }；解析失败返回 null。 */
export function parseISO(value) {
  if (!value) return null;
  const m = ISO_RE.exec(String(value));
  if (!m) return null;
  return {
    date: `${m[1]}-${m[2]}-${m[3]}`,
    time: m[4] ? `${m[4]}:${m[5]}` : '',
    iso: String(value),
  };
}

/** 只取日期部分 'YYYY-MM-DD'。 */
export function isoDate(value) {
  const p = parseISO(value);
  return p ? p.date : '';
}

/** 只取时间部分 'HH:MM'，无时间返回 ''。 */
export function isoTime(value) {
  const p = parseISO(value);
  return p ? p.time : '';
}

/** 今天的 'YYYY-MM-DD'（本地时区，只用于「今天/本周」这类惯性参照）。 */
export function todayISO(now = new Date()) {
  return `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}`;
}

/** 日期加减：用 UTC 运算避免夏令时/时区边界问题。 */
export function addDays(iso, n) {
  const [y, m, d] = String(iso).split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  dt.setUTCDate(dt.getUTCDate() + n);
  return `${dt.getUTCFullYear()}-${pad2(dt.getUTCMonth() + 1)}-${pad2(dt.getUTCDate())}`;
}

/** 0 = 周日 … 6 = 周六。 */
export function weekdayIndex(iso) {
  const [y, m, d] = String(iso).split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d)).getUTCDay();
}

export function weekdayCN(iso) {
  return WEEK_CN[weekdayIndex(iso)] || '';
}

/** 周六 / 周日（月历里做加粗标记）。 */
export function isWeekend(iso) {
  const i = weekdayIndex(iso);
  return i === 0 || i === 6;
}

/** 'YYYY-MM' */
export function monthKey(iso) {
  return String(iso).slice(0, 7);
}

/** '2026年10月' */
export function monthLabel(monthKeyValue) {
  const [y, m] = String(monthKeyValue).split('-');
  return `${y}年${Number(m)}月`;
}

/** '10月8日' */
export function dayLabel(iso) {
  const [, m, d] = String(iso).split('-');
  return `${Number(m)}月${Number(d)}日`;
}

/** '2026年10月8日' */
export function fullDayLabel(iso) {
  const [y, m, d] = String(iso).split('-');
  return `${y}年${Number(m)}月${Number(d)}日`;
}

/** 相对今天：今天 / 明天 / 昨天 / ''。 */
export function relativeDayNote(iso, today = todayISO()) {
  const diff = daysBetween(today, iso);
  if (diff === 0) return '今天';
  if (diff === 1) return '明天';
  if (diff === -1) return '昨天';
  if (diff === 2) return '后天';
  return '';
}

/** b - a 的天数差（按日期字面量）。 */
export function daysBetween(a, b) {
  const toNum = (iso) => {
    const [y, m, d] = String(iso).split('-').map(Number);
    return Date.UTC(y, m - 1, d);
  };
  return Math.round((toNum(b) - toNum(a)) / 86400000);
}

/** 该月最后一天 'YYYY-MM-DD'。 */
export function monthEnd(monthKeyValue) {
  const [y, m] = String(monthKeyValue).split('-').map(Number);
  const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
  return `${monthKeyValue}-${pad2(last)}`;
}

/** 所在周的周一。 */
export function startOfWeek(iso) {
  const delta = (weekdayIndex(iso) + 6) % 7;
  return addDays(iso, -delta);
}

/**
 * 月历网格：固定 42 格（6 行 × 7 列，周一起始），保证不同月份高度一致。
 * 每格 { iso, day, inMonth, weekend }。
 */
export function monthDays(monthKeyValue) {
  const start = startOfWeek(`${monthKeyValue}-01`);
  const cells = [];
  for (let i = 0; i < 42; i += 1) {
    const iso = addDays(start, i);
    cells.push({
      iso,
      day: Number(iso.slice(8, 10)),
      inMonth: iso.slice(0, 7) === monthKeyValue,
      weekend: isWeekend(iso),
    });
  }
  return cells;
}

/** 时间范围预设：本周（周一–周日）/ 本周末（周六–周日）/ 本月。 */
export function presetRange(preset, ref = todayISO()) {
  if (preset === 'weekend') {
    const mon = startOfWeek(ref);
    return { from: addDays(mon, 5), to: addDays(mon, 6) };
  }
  if (preset === 'month') {
    const mk = monthKey(ref);
    return { from: `${mk}-01`, to: monthEnd(mk) };
  }
  const mon = startOfWeek(ref);
  return { from: mon, to: addDays(mon, 6) };
}

/** 由 from/to 反推预设名，便于高亮「本周 / 本周末 / 本月」；不匹配返回 'custom'。 */
export function detectPreset(from, to, ref = todayISO()) {
  for (const p of ['week', 'weekend', 'month']) {
    const r = presetRange(p, ref);
    if (r.from === from && r.to === to) return p;
  }
  return 'custom';
}

/** '10月5日 – 10月11日'（跨月时补月份）。 */
export function rangeLabel(from, to) {
  if (!from || !to) return '';
  if (from === to) return dayLabel(from);
  const sameMonth = monthKey(from) === monthKey(to);
  return sameMonth ? `${dayLabel(from)} – ${dayLabel(to)}` : `${dayLabel(from)} – ${dayLabel(to)}`;
}

/** 金额：整数不带小数，非整数保留两位（后端价格支持 0.01）。 */
export function formatMoney(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '';
  return Number.isInteger(n) ? String(n) : n.toFixed(2);
}

/**
 * 价格展示（设计稿 §4.3）：
 * is_free → 无料 / 免费；min == max → ¥120；否则 ¥120–150。
 */
export function formatPrice(price) {
  if (!price) return '价格待定';
  if (price.is_free) return '无料 / 免费';
  const hasMin = typeof price.min === 'number' && Number.isFinite(price.min);
  const hasMax = typeof price.max === 'number' && Number.isFinite(price.max);
  if (!hasMin && !hasMax) return '价格待定';
  if (hasMin && hasMax) {
    if (price.min === price.max) return `¥${formatMoney(price.min)}`;
    if (price.min === 0) return `免费 – ¥${formatMoney(price.max)}`;
    return `¥${formatMoney(price.min)}–${formatMoney(price.max)}`;
  }
  if (hasMin) return `¥${formatMoney(price.min)} 起`;
  return `¥${formatMoney(price.max)} 以内`;
}

/** '2026-10-08 19:20'（用于「最后更新」）。 */
export function formatDateTime(value) {
  const p = parseISO(value);
  if (!p) return '';
  return `${p.date} ${p.time || '00:00'}`;
}

/** HTML 转义：所有来自 API 的文本都必须经过它再进 innerHTML。 */
export function escapeHtml(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** 标题里的装饰性 ★/空白 折叠，用于占位海报取首字。 */
export function firstGlyph(title) {
  const cleaned = String(title || '').replace(/^[\s★☆·・\-–—|]+/, '');
  return cleaned.slice(0, 1) || '演';
}

/**
 * 折叠标题开头的装饰性 ★☆（设计稿 §4.9：装饰性 emoji 折叠为角标）。
 * 地偶条目我们会额外渲染彩色 ★ 角标，若标题本身以 ★ 开头会出现「★★」。
 * 只在地偶条目上调用，保留乐队标题原样。
 */
export function stripLeadingMarks(title) {
  return String(title || '').replace(/^[\s★☆✦✧]+/, '');
}

/** 场地地址拼装：地址已含行政区时不重复（后端 address 字段口径不统一）。 */
export function combineAddress(venue) {
  if (!venue) return '';
  const district = String(venue.district || '').trim();
  const address = String(venue.address || '').trim();
  if (!district) return address;
  if (!address) return district;
  return address.startsWith(district) ? address : `${district} · ${address}`;
}

export function debounce(fn, wait = 300) {
  let timer = null;
  return function debounced(...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), wait);
  };
}

/** 复制到剪贴板：优先 Clipboard API，file:// 等非安全上下文退回 execCommand。 */
export async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (err) {
    /* 继续走兜底 */
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.top = '-1000px';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    ta.remove();
    return ok;
  } catch (err) {
    return false;
  }
}

/** 下载文本文件（.ics 用）。 */
export function downloadText(filename, text, mime = 'text/calendar;charset=utf-8') {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

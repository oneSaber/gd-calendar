/**
 * ics.js — 前端本地生成单场次的 .ics（iCalendar）文件。
 *
 * 取舍说明：设计稿 §5.3 的 `/api/ics` 契约是「一条筛选 URL = 一个日历」
 * （city / is_idol / artist_id 维度），并没有「单场次下载」端点。
 * 「加进我的日历」按钮面向单个 occurrence，因此这里在本地按同一套字段规范
 * 生成 VEVENT（UID 用 `occurrence.id@gdlive`，与后端保持一致，改期时客户端会更新而非重复添加）。
 * 好处：离线可用、demo 模式下也可用、不依赖后端额外实现。
 */
import { parseISO, isoDate, formatMoney, formatPrice, combineAddress } from './util.js';
import { sourceLabel } from './labels.js';

/** 转义 iCalendar 文本值（RFC 5545 3.3.11）。 */
function esc(text) {
  return String(text === null || text === undefined ? '' : text)
    .replace(/\\/g, '\\\\')
    .replace(/;/g, '\\;')
    .replace(/,/g, '\\,')
    .replace(/\r?\n/g, '\\n');
}

/** 折行：每行不超过 74 字节（按字符近似处理，中文折行不影响解析）。 */
function fold(line) {
  if (line.length <= 73) return line;
  const parts = [];
  let rest = line;
  parts.push(rest.slice(0, 73));
  rest = rest.slice(73);
  while (rest.length > 0) {
    parts.push(` ${rest.slice(0, 72)}`);
    rest = rest.slice(72);
  }
  return parts.join('\r\n');
}

/** '2026-10-08T19:30' → '20261008T193000' */
function compactLocal(value) {
  const p = parseISO(value);
  if (!p || !p.time) return '';
  return `${p.date.replace(/-/g, '')}T${p.time.replace(':', '')}00`;
}

/** 当前 UTC 时间 → '20261008T120000Z' */
function stampUTC(date = new Date()) {
  const p = (n) => String(n).padStart(2, '0');
  return `${date.getUTCFullYear()}${p(date.getUTCMonth() + 1)}${p(date.getUTCDate())}T${p(date.getUTCHours())}${p(date.getUTCMinutes())}${p(date.getUTCSeconds())}Z`;
}

/** 无结束时间时按「开演 + 2 小时」估算。 */
function plus2h(value) {
  const p = parseISO(value);
  if (!p || !p.time) return '';
  const [y, m, d] = p.date.split('-').map(Number);
  const [hh, mm] = p.time.split(':').map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d, hh, mm));
  dt.setUTCHours(dt.getUTCHours() + 2);
  const pp = (n) => String(n).padStart(2, '0');
  return `${dt.getUTCFullYear()}${pp(dt.getUTCMonth() + 1)}${pp(dt.getUTCDate())}T${pp(dt.getUTCHours())}${pp(dt.getUTCMinutes())}00`;
}

/** 生成单场次的 .ics 文本。 */
export function buildIcs(item) {
  const ev = item.event || {};
  const title = ev.title || '（无标题）';
  const venueName = item.venue && item.venue.name ? item.venue.name : '场地待定';
  const address = combineAddress(item.venue);
  const startValue = item.start_at || item.open_at;
  const dtStart = compactLocal(startValue);
  const dtEnd = item.end_at && parseISO(item.end_at).time
    ? compactLocal(item.end_at)
    : plus2h(startValue);

  const descLines = [];
  if (item.open_at && item.start_at) descLines.push(`开场 ${parseISO(item.open_at).time} / 开演 ${parseISO(item.start_at).time}`);
  descLines.push(`票价：${formatPrice(item.price)}`);
  const tickets = Array.isArray(item.tickets) ? item.tickets : [];
  if (tickets.length) {
    descLines.push(`票种：${tickets.map((t) => `${t.name}${t.price === null || t.price === undefined ? '' : ` ¥${formatMoney(t.price)}`}${t.channel ? `(${t.channel})` : ''}`).join(' / ')}`);
  }
  const lineup = (Array.isArray(item.lineup) ? item.lineup : []).slice().sort((a, b) => Number(a.order || 0) - Number(b.order || 0));
  if (lineup.length) descLines.push(`阵容：${lineup.map((a) => a.name).join(' / ')}`);
  if (item.age_limit) descLines.push(`限制：${item.age_limit}`);
  const extra = item.extra || {};
  Object.keys(extra).forEach((k) => descLines.push(`${k}：${extra[k]}`));
  if (item.tokuten_note) descLines.push(`特典：${item.tokuten_note}`);
  const sources = Array.isArray(item.sources) ? item.sources : [];
  if (sources.length) descLines.push(`来源：${sources.map((s) => `${sourceLabel(s.code)}${s.url ? ` ${s.url}` : ''}`).join(' / ')}`);
  descLines.push('由「广东地下演出日历」生成，仅聚合公开的演出事实信息。');

  const statusMap = { cancelled: 'CANCELLED', postponed: 'TENTATIVE', unknown: 'TENTATIVE' };

  const lines = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//GD Live Calendar//GD Live Calendar//CN',
    'CALSCALE:GREGORIAN',
    'METHOD:PUBLISH',
    'BEGIN:VEVENT',
    `UID:${item.id}@gdlive`,
    `DTSTAMP:${stampUTC()}`,
  ];
  // 时间精度 tbd（连日期都没有）时不下发 DTSTART/DTEND，避免生成非法日历项
  if (dtStart) lines.push(`DTSTART;TZID=Asia/Shanghai:${dtStart}`);
  else if (isoDate(startValue)) lines.push(`DTSTART;VALUE=DATE:${isoDate(startValue).replace(/-/g, '')}`);
  if (dtEnd) lines.push(`DTEND;TZID=Asia/Shanghai:${dtEnd}`);
  lines.push(
    `SUMMARY:${esc(`${title} @ ${venueName}`)}`,
    `LOCATION:${esc([venueName, address].filter(Boolean).join(' '))}`,
    `DESCRIPTION:${esc(descLines.join('\n'))}`,
    `STATUS:${statusMap[item.status] || 'CONFIRMED'}`,
  );
  const firstUrl = (sources.find((s) => s && s.url) || {}).url;
  if (firstUrl) lines.push(`URL:${esc(firstUrl)}`);
  lines.push('END:VEVENT', 'END:VCALENDAR');

  return `${lines.map(fold).join('\r\n')}\r\n`;
}

/** 下载文件名：gdlive-9002-星屑公演.ics */
export function icsFilename(item) {
  const title = ((item.event && item.event.title) || '演出').replace(/[\\/:*?"<>|\s]+/g, '').slice(0, 24);
  return `gdlive-${item.id}-${title}.ics`;
}

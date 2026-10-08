/**
 * render.js — 视图渲染（纯字符串拼装 + innerHTML，事件由 app.js 委托）。
 *
 * 包含：时间轴列表、月历网格（大/小两种）、当日列表、详情面板、
 *      骨架屏、空状态、错误状态。
 * 所有来自 API 的文本都必须经 escapeHtml；时间一律用 <time datetime>（无障碍要求）。
 */
import {
  escapeHtml,
  parseISO,
  isoDate,
  weekdayCN,
  isWeekend,
  dayLabel,
  fullDayLabel,
  monthLabel,
  monthDays,
  todayISO,
  formatPrice,
  formatMoney,
  formatDateTime,
  relativeDayNote,
  firstGlyph,
  stripLeadingMarks,
  combineAddress,
} from './util.js';
import {
  statusInfo,
  kindLabel,
  roleLabel,
  sourceLabel,
  categoryOf,
  eventFlags,
  isLowConfidence,
  needsDashed,
} from './labels.js';

const ICON_WARN = '<svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true"><path d="M12 4.5 21 20H3z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M12 10v4.2M12 17.2v.1" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>';
const ICON_EMPTY = '<svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true"><rect x="3.5" y="5.5" width="17" height="15" rx="2.5" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M3.5 10h17M8 3.5v4M16 3.5v4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><path d="M9 15.5h6" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
const ICON_PLUG = '<svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true"><path d="M9 3v6M15 3v6M6 9h12v3a6 6 0 0 1-6 6 6 6 0 0 1-6-6z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M12 18v3" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';

/** 时间轴上显示的时刻：优先「开演」，其次「开场」，非 exact 精度显示待定。 */
export function timeBits(item) {
  const precision = item.date_precision || 'exact';
  const start = parseISO(item.start_at);
  const open = parseISO(item.open_at);
  if (precision === 'exact') {
    if (start && start.time) return { time: start.time, label: '开演', datetime: item.start_at || '' };
    if (open && open.time) return { time: open.time, label: '开场', datetime: item.open_at || '' };
  }
  return { time: '待定', label: '时间待定', datetime: item.start_at || item.open_at || '' };
}

/** 分组：按日期归类，保持原顺序（后端已按时间升序）。 */
export function groupByDate(items) {
  const map = new Map();
  items.forEach((it) => {
    const date = isoDate(it.start_at || it.open_at);
    if (!date) return;
    if (!map.has(date)) map.set(date, []);
    map.get(date).push(it);
  });
  return Array.from(map.entries()).map(([date, list]) => ({ date, items: list }));
}

/** 分类标签（地偶额外带 ★，颜色不作唯一标识）。 */
function categoryTag(item) {
  const idol = categoryOf(item) === 'idol';
  const text = idol ? '★ 地偶' : '乐队';
  return `<span class="tag ${idol ? 'idol' : 'band'}">${text}</span>`;
}

function statusTag(item) {
  const st = statusInfo(item.status);
  return `<span class="tag ${st.cls}">${escapeHtml(st.label)}</span>`;
}

/**
 * 来源标签（可点击跳原站）：[秀动] [豆瓣]
 *
 * 跳转统一走 /api/go?url=... —— 由后端做协议白名单校验后 302 到原站，
 * 这样前端不必直接暴露原站域名，也便于统计外链点击。
 */
function sourcesHtml(item) {
  const list = Array.isArray(item.sources) ? item.sources : [];
  if (!list.length) return '<span class="dim">来源未知</span>';
  const seen = new Set();
  const tags = [];
  list.forEach((s) => {
    const label = sourceLabel(s && s.code);
    if (seen.has(label)) return;
    seen.add(label);
    if (s && s.url) {
      const go = `/api/go?url=${encodeURIComponent(s.url)}`;
      tags.push(
        `<a class="src-link" href="${escapeHtml(go)}" target="_blank" ` +
        `rel="noopener nofollow" title="在新标签页打开原站：${escapeHtml(s.url)}">` +
        `${escapeHtml(label)}<span class="ext" aria-hidden="true">↗</span></a>`
      );
    } else {
      tags.push(`<b>${escapeHtml(label)}</b>`);
    }
  });
  return `来源 ${tags.join(' ')}`;
}

/**
 * 独立标记的小标签：女子乐队 / ACG。
 *
 * 与地偶的 ★ 区分开——地偶是**主分类**（决定色条颜色），
 * 这两个是**附加标记**，所以用小号胶囊而不是星号，可以同时出现多个。
 */
function flagsHtml(item, { xs = true } = {}) {
  const flags = eventFlags(item && item.event);
  if (!flags.length) return '';
  return flags
    .map((f) => {
      const cls = f === '女子乐队' ? 'girl' : 'acg';
      return `<span class="tag flag ${cls}${xs ? ' xs' : ''}">${escapeHtml(f)}</span>`;
    })
    .join('');
}

/**
 * 详情页主海报。
 *
 * 海报地址由后端代理（/api/archive/poster?url=...）—— 豆瓣图有防盗链，
 * 前端直接引用原站 URL 会 418 裂图。加载失败时降级为「首字 + 渐变」占位，
 * 不让用户看到浏览器的裂图图标。
 */
function posterHtml(ev, title, idol) {
  const ph =
    `<div class="poster ph ${idol ? 'idol' : 'band'}" aria-hidden="true">` +
    `<span>${escapeHtml(firstGlyph(title))}</span><small>暂无海报</small></div>`;

  const src = (ev && ev.poster_url) || (ev && ev.poster_thumb);
  if (!src) return ph;

  return (
    `<img class="poster" src="${escapeHtml(src)}" ` +
    `alt="${escapeHtml(title)} 演出海报" loading="lazy" decoding="async" ` +
    `data-poster-fallback="1">`
  );
}

/** 卡片公共部分：标题、meta、来源行。 */
function cardInner(item) {
  const rawTitle = (item.event && item.event.title) || '（无标题）';
  const idol = categoryOf(item) === 'idol';
  // 地偶条目已自带彩色 ★ 角标，标题开头的装饰性 ★ 折叠掉，避免「★★」
  const title = idol ? stripLeadingMarks(rawTitle) : rawTitle;
  const st = statusInfo(item.status);
  const series = item.event && item.event.series_key;
  const venue = item.venue;
  const low = isLowConfidence(item);
  const extra = item.extra || {};
  const hasTokuten = Boolean(item.tokuten_note) || Object.keys(extra).some((k) => /特典|チェキ|物贩/.test(k));

  const tags = [categoryTag(item), statusTag(item)];
  if (series && series !== title) tags.push(`<span class="tag ghost">系列 ${escapeHtml(series)}</span>`);
  if (hasTokuten) tags.push('<span class="tag soft">含特典会</span>');
  // 状态补充说明（改期去向等）
  if (item.status_note) tags.push(`<span class="tag warn soft">${escapeHtml(item.status_note)}</span>`);

  const meta = [];
  meta.push(venue && venue.name ? escapeHtml(venue.name) : '<span class="dim">场地待定</span>');
  if (venue && venue.district) meta.push(escapeHtml(venue.district));
  meta.push(`<span class="price">${escapeHtml(formatPrice(item.price))}</span>`);
  if (item.age_limit) meta.push(escapeHtml(item.age_limit));

  const srcExtra = [];
  if (low) {
    srcExtra.push(`<span class="conf">自动解析（置信度 ${escapeHtml(String(item.confidence))}），可能不准</span>`);
    srcExtra.push(`<button type="button" class="linkish" data-correct="${escapeHtml(item.id)}">帮忙确认</button>`);
  } else if (item.confidence !== undefined && item.confidence !== null && item.confidence < 0.95) {
    srcExtra.push(`<span class="dim">置信度 ${escapeHtml(String(item.confidence))}</span>`);
  }

  return `
    <div class="ttl">${idol ? '<span class="star" role="img" aria-label="地偶场">★</span>' : ''}${escapeHtml(title)} ${tags.join(' ')} ${flagsHtml(item)}</div>
    <div class="meta">${meta.join('<span class="dot">·</span>')}</div>
    <div class="src">${sourcesHtml(item)}${srcExtra.length ? ` <span class="dot">·</span> ${srcExtra.join(' ')}` : ''}</div>`;
}

/** 时间轴单条卡片（左侧 3px 色条：乐队=电蓝 / 地偶=粉紫）。 */
export function eventCardHtml(item, opts = {}) {
  const idol = categoryOf(item) === 'idol';
  const bits = timeBits(item);
  const dashed = needsDashed(item);
  const selected = String(item.id) === String(opts.selectedId || '');
  const cls = [
    'card',
    idol ? 'idol' : 'band',
    dashed ? 'dashed' : '',
    selected ? 'sel' : '',
    item.status === 'finished' ? 'past' : '',
    item.status === 'cancelled' ? 'cancelled' : '',
  ].filter(Boolean).join(' ');
  const title = idol ? stripLeadingMarks((item.event && item.event.title) || '') : ((item.event && item.event.title) || '');
  const venueName = item.venue && item.venue.name ? item.venue.name : '场地待定';
  const aria = `${title}，${venueName}，${formatPrice(item.price)}，${statusInfo(item.status).label}`;
  return `
      <div class="ev">
        <div class="t">
          <time datetime="${escapeHtml(bits.datetime)}">${escapeHtml(bits.time)}</time>
          <em>${escapeHtml(bits.label)}</em>
        </div>
        <article class="${cls}" role="button" tabindex="0" data-occ-id="${escapeHtml(item.id)}"
                 aria-label="${escapeHtml(aria)}" ${selected ? 'aria-current="true"' : ''}>
          ${cardInner(item)}
        </article>
      </div>`;
}

/**
 * 时间轴列表：按日期分组 + 左侧固定时间轴。
 * opts: { selectedId, today, meta:{total,generated_at,demo} }
 */
export function renderTimeline(container, items, opts = {}) {
  const today = opts.today || todayISO();
  const groups = groupByDate(items);
  const idolCount = items.filter((it) => categoryOf(it) === 'idol').length;
  const meta = opts.meta || {};
  const head = `
    <div class="list-head">
      <b>${escapeHtml(opts.rangeLabel || '')}</b>
      <span>共 <b>${escapeHtml(String(meta.total !== undefined ? meta.total : items.length))}</b> 场</span>
      <span class="idol-num">其中地偶 ${escapeHtml(String(idolCount))} 场</span>
      ${meta.demo ? '<span class="tag demo">示例数据</span>' : ''}
      ${meta.generated_at ? `<span class="dim">· 数据更新 ${escapeHtml(formatDateTime(meta.generated_at))}</span>` : ''}
    </div>`;

  const body = groups.map((g) => {
    const note = relativeDayNote(g.date, today);
    const sub = [weekdayCN(g.date), isWeekend(g.date) ? '周末' : '', note].filter(Boolean).join(' · ');
    return `
    <section class="daygroup">
      <h3 class="daylabel">
        <b>${escapeHtml(fullDayLabel(g.date))}</b>
        <span>${escapeHtml(sub)}</span>
        <span class="cnt">${g.items.length} 场</span>
      </h3>
      ${g.items.map((it) => eventCardHtml(it, opts)).join('')}
    </section>`;
  }).join('');

  container.innerHTML = head + body;
}

/** 时间轴形状的骨架屏（不用转圈）。 */
export function renderTimelineSkeleton(container, blocks = 3) {
  let html = '<div class="list-head skeleton-line"></div>';
  for (let i = 0; i < blocks; i += 1) {
    html += `
    <section class="daygroup">
      <div class="daylabel"><span class="sk sk-daylabel"></span></div>
      ${[0, 1].map(() => `
      <div class="ev">
        <div class="t"><span class="sk sk-time"></span></div>
        <div class="card sk-card">
          <span class="sk sk-title"></span>
          <span class="sk sk-meta"></span>
          <span class="sk sk-src"></span>
        </div>
      </div>`).join('')}
    </section>`;
  }
  container.innerHTML = html;
}

export function renderMonthSkeleton(container) {
  const cells = Array.from({ length: 42 }).map(() => '<span class="cell sk-cell"></span>').join('');
  container.innerHTML = `
    <section class="cal cal-big">
      <div class="calhead"><span class="sk sk-daylabel"></span></div>
      <div class="calgrid">${cells}</div>
    </section>`;
}

/**
 * 月历网格：格子内**只显示数量点阵**（蓝=乐队，粉=地偶，最多 4 点 + +N）。
 * opts: { month, days:{date:{band,idol,followed}}, selected, today, compact, demo }
 */
export function renderMonth(container, opts = {}) {
  const month = opts.month;
  const counts = opts.days || {};
  const selected = opts.selected || '';
  const today = opts.today || todayISO();
  const compact = Boolean(opts.compact);
  const cells = monthDays(month);

  const dow = ['一', '二', '三', '四', '五', '六', '日']
    .map((d) => `<span class="dow" role="columnheader">${d}</span>`).join('');

  let monthTotal = 0;
  let idolTotal = 0;
  let girlTotal = 0;
  let acgTotal = 0;
  const body = cells.map((c) => {
    const c0 = counts[c.iso] || {};
    const band = Number(c0.band || 0);
    const idol = Number(c0.idol || 0);
    const followed = Number(c0.followed || 0);
    // 独立标记：与 band/idol 正交，某天可能有 3 场但其中 2 场是 ACG
    const girl = Number(c0.girl_band || 0);
    const acg = Number(c0.acg || 0);
    const total = band + idol;
    if (c.inMonth) {
      monthTotal += total;
      idolTotal += idol;
      girlTotal += girl;
      acgTotal += acg;
    }
    // 点阵：最多 4 个点，其余折叠为 +N
    const dots = [];
    for (let i = 0; i < Math.min(band, 4); i += 1) dots.push('<i class="pip"></i>');
    for (let i = 0; i < Math.min(idol, 4 - dots.length); i += 1) dots.push('<i class="pip i"></i>');
    const rest = total - dots.length;
    const pips = dots.length || rest > 0
      ? `<span class="pips">${dots.join('')}${rest > 0 ? `<span class="more">+${rest}</span>` : ''}</span>`
      : '';
    // 独立标记用「小字角标」表示，不做第 5、6 个点（格子太小会挤爆）
    const marks = [
      girl > 0 ? `<span class="mk girl" title="含女子乐队场次">女</span>` : '',
      acg > 0 ? `<span class="mk acg" title="含 ACG 场次">A</span>` : '',
    ].filter(Boolean).join('');
    const cls = [
      'cell',
      c.inMonth ? '' : 'out',
      c.weekend ? 'wk' : '',
      c.iso === today ? 'today' : '',
      c.iso === selected ? 'sel' : '',
    ].filter(Boolean).join(' ');
    const aria = [
      dayLabel(c.iso),
      weekdayCN(c.iso),
      c.iso === today ? '今天' : '',
      total
        ? `共 ${total} 场（乐队 ${band}，地偶 ${idol}${girl ? `，女子乐队 ${girl}` : ''}${acg ? `，ACG ${acg}` : ''}）`
        : '没有收录的演出',
    ].filter(Boolean).join('，');
    return `<button type="button" class="${cls}" data-day="${c.iso}" role="gridcell"
              aria-label="${escapeHtml(aria)}" aria-pressed="${c.iso === selected ? 'true' : 'false'}"
              ${c.iso === today ? 'aria-current="date"' : ''}>
        <span class="n">${c.day}</span>
        ${followed > 0 ? '<span class="star" title="有关注艺人的场次" aria-label="有关注艺人">★</span>' : ''}
        ${marks}
        ${pips}
      </button>`;
  }).join('');

  const nav = `
    <div class="nav">
      <button type="button" data-month-nav="-1" aria-label="上个月">‹</button>
      <button type="button" data-month-nav="1" aria-label="下个月">›</button>
      ${!compact ? '<button type="button" data-month-today="1" class="nav-today">今天</button>' : ''}
    </div>`;

  const sub = compact
    ? ''
    : `<span class="cal-sub">本月共 ${monthTotal} 场 · 地偶 ${idolTotal} 场` +
      `${girlTotal ? ` · 女子乐队 ${girlTotal} 场` : ''}` +
      `${acgTotal ? ` · ACG ${acgTotal} 场` : ''}${opts.demo ? ' · 示例数据' : ''}</span>`;

  container.innerHTML = `
    <section class="cal ${compact ? 'cal-mini' : 'cal-big'}">
      <div class="calhead">
        <b>${escapeHtml(monthLabel(month))}</b>${sub}
        ${nav}
      </div>
      <div class="calgrid" role="grid" aria-label="${escapeHtml(monthLabel(month))} 月历">${dow}${body}</div>
      <div class="legend">
        <span><i style="background:var(--band)"></i>乐队场</span>
        <span><i style="background:var(--idol)"></i>地偶场</span>
        <span>★ 有关注艺人的场</span>
        <span><b class="mk girl">女</b> 含女子乐队</span>
        <span><b class="mk acg">A</b> 含 ACG</span>
        <span class="legend-tip">格子内不写标题，点格子看当日列表</span>
      </div>
    </section>`;
}

/** 右侧「当日列表」面板。 */
export function renderDayPanel(container, opts = {}) {
  const date = opts.date;
  const items = opts.items || [];
  const header = `
    <div class="panel-head">
      <b>${escapeHtml(date ? dayLabel(date) : '当日')}</b>
      <span class="dim">${escapeHtml(date ? weekdayCN(date) : '')}${items.length ? ` · ${items.length} 场` : ''}</span>
      <button type="button" class="icon-btn" data-close-day aria-label="关闭当日列表">✕</button>
    </div>`;

  if (opts.error) {
    container.innerHTML = `${header}<div class="panel-hint">当日数据加载失败：${escapeHtml(opts.error)}</div>`;
    return;
  }
  if (opts.loading) {
    container.innerHTML = `${header}
      <div class="daylist">${[0, 1].map(() => '<div class="sk sk-dayitem"></div>').join('')}</div>`;
    return;
  }
  if (!items.length) {
    container.innerHTML = `${header}
      <div class="panel-hint">这一天没有收录的演出。<button type="button" class="linkish" data-city-all="1">换个城市看看</button></div>`;
    return;
  }

  const list = items.map((it) => {
    const idol = categoryOf(it) === 'idol';
    const bits = timeBits(it);
    const st = statusInfo(it.status);
    const selected = String(it.id) === String(opts.selectedId || '');
    const venueName = it.venue && it.venue.name ? it.venue.name : '场地待定';
    const rawTitle = (it.event && it.event.title) || '';
    const titleText = idol ? stripLeadingMarks(rawTitle) : rawTitle;
    return `
      <button type="button" class="dayitem ${idol ? 'idol' : 'band'} ${selected ? 'sel' : ''}" data-occ-id="${escapeHtml(it.id)}">
        <span class="di-time">${escapeHtml(bits.time)}</span>
        <span class="di-body">
          <span class="di-ttl">${idol ? '<span class="star">★</span>' : ''}${escapeHtml(titleText)}</span>
          <span class="di-meta">${escapeHtml(venueName)} · ${escapeHtml(formatPrice(it.price))} · <span class="tag ${st.cls} xs">${escapeHtml(st.label)}</span></span>
        </span>
      </button>`;
  }).join('');

  container.innerHTML = `${header}<div class="daylist">${list}</div>`;
}

/** 详情面板（桌面端 sticky）。 */
export function renderDetail(container, opts = {}) {
  const item = opts.item;
  if (!item) {
    container.innerHTML = `
      <div class="panel-empty">
        <p>从左侧点一条演出，这里显示详情。</p>
        <p class="dim">包含阵容、票种、特典规则、来源与「加进我的日历」。</p>
      </div>`;
    return;
  }
  const ev = item.event || {};
  const idol = categoryOf(item) === 'idol';
  const rawTitle = ev.title || '（无标题）';
  const title = idol ? stripLeadingMarks(rawTitle) : rawTitle;
  const st = statusInfo(item.status);
  const low = isLowConfidence(item);
  const bits = timeBits(item);
  const date = isoDate(item.start_at || item.open_at);
  const open = parseISO(item.open_at);
  const start = parseISO(item.start_at);
  const timeLines = [];
  if (open && open.time) timeLines.push(`${open.time} 开场`);
  if (start && start.time) timeLines.push(`${start.time} 开演`);
  if (!timeLines.length) timeLines.push('时间待定');
  const venue = item.venue;
  const tickets = Array.isArray(item.tickets) ? item.tickets : [];
  const lineup = (Array.isArray(item.lineup) ? item.lineup : [])
    .slice()
    .sort((a, b) => (Number(a.order || 0) - Number(b.order || 0)));
  const extra = item.extra || {};
  const extraKeys = Object.keys(extra);
  const sources = Array.isArray(item.sources) ? item.sources : [];
  const lastFetched = sources
    .map((s) => (s && s.fetched_at) || '')
    .filter(Boolean)
    .sort()
    .pop();
  const buyUrl = (tickets.find((t) => t && t.url) || {}).url;

  const poster = posterHtml(ev, title, idol);

  const priceText = formatPrice(item.price);
  const ticketBrief = tickets.length
    ? tickets.map((t) => `${escapeHtml(t.name || '票')} ${t.price === null || t.price === undefined ? '' : `¥${escapeHtml(formatMoney(t.price))}`}`.trim()).join(' / ')
    : '—';

  const lineupHtml = lineup.length
    ? `<ol class="lineup">${lineup.map((a) => `
        <li><span class="lu-name">${escapeHtml(a.name || '待确认')}</span><span class="role">${escapeHtml(roleLabel(a.role))}</span></li>`).join('')}</ol>`
    : '<p class="dim small">阵容待确认。</p>';

  const ticketsHtml = tickets.length
    ? `<ul class="tickets">${tickets.map((t) => {
        const ts = statusInfo(t.status);
        return `<li>
          <span class="tk-name">${escapeHtml(t.name || '票')}</span>
          <span class="tk-price">${t.price === null || t.price === undefined ? '—' : `¥${escapeHtml(formatMoney(t.price))}`}</span>
          <span class="tk-ch">${escapeHtml(t.channel || '')}</span>
          <span class="tag ${ts.cls} xs">${escapeHtml(ts.label)}</span>
          ${t.url ? `<a class="tk-link" href="${escapeHtml(t.url)}" target="_blank" rel="noopener nofollow">购票 →</a>` : '<span class="tk-link dim">无线上票</span>'}
        </li>`;
      }).join('')}</ul>`
    : '<p class="dim small">暂无票种信息。</p>';

  const extraHtml = (extraKeys.length || item.tokuten_note)
    ? `<div class="extra">
        <b>特典 / 物贩 / 规则</b>
        ${extraKeys.length ? `<div class="extra-kv">${extraKeys.map((k) => `<span><em>${escapeHtml(k)}</em>${escapeHtml(extra[k])}</span>`).join('')}</div>` : ''}
        ${item.tokuten_note ? `<p>${escapeHtml(item.tokuten_note)}</p>` : ''}
      </div>`
    : '';

  const sourceLinks = sources.length
    ? sources.map((s) => {
        const label = escapeHtml(sourceLabel(s && s.code));
        if (!(s && s.url)) return `<span>${label}</span>`;
        const go = `/api/go?url=${encodeURIComponent(s.url)}`;
        // 显示原站域名，让用户清楚要跳去哪里
        let host = '';
        try { host = new URL(s.url).hostname.replace(/^www\./, ''); } catch (e) { host = ''; }
        return (
          `<a class="src-link" href="${escapeHtml(go)}" target="_blank" ` +
          `rel="noopener nofollow" title="${escapeHtml(s.url)}">${label}` +
          `<span class="ext" aria-hidden="true">↗</span></a>` +
          (host ? `<span class="dim src-host">${escapeHtml(host)}</span>` : '')
        );
      }).join(' · ')
    : '来源未知';

  // 有来源链接时，给一个显眼的「查看原页」按钮（取主来源）
  const primary = sources.find((s) => s && s.is_primary && s.url) || sources.find((s) => s && s.url);
  const originBtn = primary
    ? `<a class="btn g" href="/api/go?url=${encodeURIComponent(primary.url)}" ` +
      `target="_blank" rel="noopener nofollow">查看原页 ↗</a>`
    : '';

  container.innerHTML = `
    ${poster}
    ${low ? `<div class="warnbox">⚠️ 本条由文案自动解析，置信度 ${escapeHtml(String(item.confidence))}，场地与票价请以主办方为准。<button type="button" class="linkish" data-correct="${escapeHtml(item.id)}">帮忙确认</button></div>` : ''}
    <h3 class="d-title">${idol ? '<span class="star" role="img" aria-label="地偶场">★</span>' : ''}${escapeHtml(title)}</h3>
    <div class="d-sub">
      <span class="tag ${idol ? 'idol' : 'band'}">${idol ? '★ 地偶' : '乐队'}</span>
      ${flagsHtml(item, { xs: false })}
      <span class="tag ghost">${escapeHtml(kindLabel(ev.kind, idol))}</span>
      ${ev.series_key ? `<span class="tag ghost">系列：${escapeHtml(ev.series_key)}</span>` : ''}
      <span class="tag ${st.cls}">${escapeHtml(st.label)}</span>
      ${opts.demo ? '<span class="tag demo">示例数据</span>' : ''}
    </div>
    <dl class="kv">
      <dt>时间</dt>
      <dd><time datetime="${escapeHtml(bits.datetime)}">${escapeHtml(date ? fullDayLabel(date) : '日期待定')}（${escapeHtml(date ? weekdayCN(date) : '待定')}）</time><br>${timeLines.map(escapeHtml).join(' / ')}${item.date_precision && item.date_precision !== 'exact' ? ` <span class="tag warn xs">日期精度：${escapeHtml(item.date_precision)}</span>` : ''}</dd>
      <dt>场地</dt>
      <dd>${venue && venue.name ? escapeHtml(venue.name) : '<span class="dim">场地待定</span>'}
        ${venue && (venue.district || venue.address) ? `<br><span class="dim">${escapeHtml(combineAddress(venue))}</span>` : ''}
        ${venue && venue.lng && venue.lat ? `<br><span class="dim mono">${escapeHtml(String(venue.lng))}, ${escapeHtml(String(venue.lat))}</span>` : ''}
      </dd>
      <dt>票价</dt>
      <dd>${escapeHtml(priceText)}<br><span class="dim">${ticketBrief}</span></dd>
      ${extraKeys.length || item.tokuten_note ? '<dt>特典</dt><dd>' + escapeHtml(extraKeys.map((k) => `${k} ${extra[k]}`).join(' · ') || '见下方规则') + '</dd>' : ''}
      <dt>限制</dt>
      <dd>${escapeHtml(item.age_limit || '未标注')}${item.id_required ? ' · 需实名' : ''}</dd>
      <dt>状态</dt>
      <dd><span class="tag ${st.cls}">${escapeHtml(st.label)}</span>${item.status_note ? ` <span class="dim">${escapeHtml(item.status_note)}</span>` : ''}</dd>
    </dl>

    <div class="d-sec-t">阵容（登场顺序）</div>
    ${lineupHtml}

    <div class="d-sec-t">票务</div>
    ${ticketsHtml}

    ${extraHtml}

    <div class="btns">
      ${buyUrl ? `<a class="btn p" href="${escapeHtml(buyUrl)}" target="_blank" rel="noopener nofollow">去购票 →</a>` : ''}
      ${originBtn}
      <button type="button" class="btn ${buyUrl ? '' : 'p'}" data-ics="${escapeHtml(item.id)}">加进我的日历 (.ics)</button>
      <button type="button" class="btn g" data-correct="${escapeHtml(item.id)}">纠错 / 补充</button>
    </div>

    <div class="note">
      信息来源：${sourceLinks}<br>
      最后更新：${escapeHtml(lastFetched ? formatDateTime(lastFetched) : '未知')} ｜ 置信度 ${escapeHtml(String(item.confidence !== undefined ? item.confidence : '—'))} ｜ ${item.verified ? '已人工/规则核验' : '未经核验'}<br>
      本站仅聚合公开的演出事实信息，票务与版权归主办方所有。
    </div>`;
}

/** 空状态：明确告知 + 「换个城市看看」入口（设计稿 §4.7）。 */
export function renderEmpty(container, opts = {}) {
  const title = opts.title || '这段时间没有收录的演出';
  const hint = opts.hint || '试试换个城市、放宽时间范围，或者清空筛选条件。';
  const cityButtons = (opts.cities || [])
    .map((c) => `<button type="button" class="chip" data-switch-city="${escapeHtml(c)}">${escapeHtml(c || '全省')}</button>`)
    .join('');
  container.innerHTML = `
    <div class="state state-empty">
      <div class="state-ico">${ICON_EMPTY}</div>
      <h3>${escapeHtml(title)}</h3>
      <p>${escapeHtml(hint)}</p>
      ${cityButtons ? `<div class="state-sub">换个城市看看</div><div class="state-actions">${cityButtons}</div>` : ''}
      ${opts.extraHtml || ''}
    </div>`;
}

/** 错误状态：API 不可用时必须给出明确提示（不允许白屏）。 */
export function renderError(container, opts = {}) {
  container.innerHTML = `
    <div class="state state-error" role="alert">
      <div class="state-ico">${opts.icon === 'plug' ? ICON_PLUG : ICON_WARN}</div>
      <h3>${escapeHtml(opts.title || '无法加载演出数据')}</h3>
      <p>${escapeHtml(opts.message || '后端 API 请求失败。')}</p>
      ${opts.url ? `<p class="dim small">请求地址：<code>${escapeHtml(opts.url)}</code></p>` : ''}
      <div class="state-actions">
        <button type="button" class="btn" data-retry="1">重新加载</button>
        <button type="button" class="btn p" data-demo="1">查看内置示例数据</button>
      </div>
      <p class="dim small">提示：本前端为静态页面，需要通过后端服务（FastAPI 挂载 <code>/</code>）或本地
        <code>python -m http.server</code> 打开；直接双击 HTML 会因浏览器同源策略无法访问 <code>/api</code>，
        且 <code>file://</code> 会拦截 ES 模块。</p>
    </div>`;
}

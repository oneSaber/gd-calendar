/**
 * app.js — 入口模块：读取 URL 状态 → 取数 → 渲染，并装配全部交互。
 *
 * 数据流（单向）：
 *   URL query --readState--> 状态 S --api/filters--> 数据 --render--> DOM
 *   任何交互只做两件事之一：改 S 并写回 URL（进历史，支持前进/后退），然后 refresh()。
 *
 * 视图分工：
 *   列表视图：主区 = 时间轴；侧栏 = 迷你月历 + 详情面板
 *   月历视图：主区 = 大月历；侧栏 = 当日列表 + 详情面板
 */
import {
  escapeHtml,
  debounce,
  copyText,
  downloadText,
  todayISO,
  monthKey,
  monthLabel,
  rangeLabel,
  formatDateTime,
} from './util.js';
import { CITIES, FLAG_OPTIONS, KINDS, STATUS_OPTIONS, VENUE_TYPES, sourceLabel } from './labels.js';
import * as api from './api.js';
import * as store from './state.js';
import * as view from './render.js';
import * as updateCtl from './update.js';
import { buildIcs, icsFilename } from './ics.js';
import { createArtistPicker } from './artists.js';

// 供 index.html 的启动守卫判断模块是否加载成功（file:// 下 ES 模块会被拦截）
window.__GD_READY__ = true;

const dom = {};
/** 艺人搜索组件实例（在 wireEvents 里创建；syncControls 会用到） */
let artistPicker = null;
let S = null;                     // 当前筛选/视图状态
const itemIndex = new Map();      // occurrence.id -> item（详情面板就地取数，避免重复请求）
const countsCache = new Map();    // `${month}|${city}` -> counts
const dayCache = new Map();       // `${date}|${filters}` -> 当日列表
let allVenues = [];
let lastMeta = { total: 0, generated_at: '', demo: false };
let selectedId = null;
let statsLoaded = false;

/* ------------------------------------------------------------------ 启动 */

/**
 * 功能开关（js/features.js 定义默认值，后端 /api/features 可覆盖）。
 *
 * 设计取舍：静态文件是**兜底**，保证后端挂掉时开关依然生效；
 * 后端读到了就以后端为准，这样改 .env 就能统一控制，
 * 不用去动前端文件。两者都拿不到时按「关闭」处理（保守）。
 */
const FEATURES = { ics: false, update: false, submit: true, ...(window.__FEATURES__ || {}) };

async function loadFeatures() {
  try {
    const remote = await api.loadFeatures();
    if (remote && typeof remote === 'object') {
      // 后端只返回它明确管理的开关，未提及的保持静态文件里的值
      Object.keys(remote).forEach((k) => {
        if (k in FEATURES) FEATURES[k] = Boolean(remote[k]);
      });
    }
  } catch (err) {
    // 后端不可用：用 features.js 的值，不打扰用户
  }
}

/** 静态站点：显示快照横幅 + 关掉后端相关入口。 */
async function initStaticMode() {
  if (!api.isStatic()) return;
  const bar = document.getElementById('static-banner');
  if (bar) bar.hidden = false;
  const meta = await api.loadStaticMeta();
  const gen = document.getElementById('static-generated');
  if (gen && meta && meta.generated_at) {
    gen.textContent = ` 数据快照时间：${String(meta.generated_at).replace('T', ' ').slice(0, 16)}`;
  }
}

/** 按开关隐藏元素（用 hidden 而不是删 DOM，便于排查时手动打开） */
function applyFeatureFlags() {
  const root = document.documentElement;
  // 全局 CSS 开关：详情面板里的 .ics 按钮是**渲染出来的**，
  // 不适合逐个 DOM 操作，用根元素 class 统一控制更不容易漏。
  root.classList.toggle('no-ics', !FEATURES.ics);
  root.classList.toggle('no-update', !FEATURES.update);
  root.classList.toggle('no-submit', !FEATURES.submit);

  // ⚠️ 按钮必须**双向**设置 hidden：本函数会被调用两次（先用静态值，再用后端
  //    /api/features 的值覆盖）。若只在关闭时置 true，第一次调用留下的 true
  //    永远不会被撤销 —— 于是 .env 里打开开关，顶栏按钮依然是隐藏的。
  const setHidden = (id, hidden) => {
    const el = document.getElementById(id);
    if (el) el.hidden = hidden;
  };

  ['ics-btn', 'ics-btn-2'].forEach((id) => setHidden(id, !FEATURES.ics));
  setHidden('feedback-btn', !FEATURES.submit);
  setHidden('update-btn', !FEATURES.update);
  // 文档入口只在静态站里有意义：`docs.html` / `SPEC.html` / `DATASOURCES.html`
  // 是 build-static 生成或复制的，本地 `serve` 时**不存在**（点进去会 404）。
  setHidden('docs-link', !api.isStatic());
  // 进度条只在关闭时隐藏：打开时它的显隐由 update.js 按任务状态控制，
  // 这里置 false 会让空进度条在页面加载后就冒出来。
  if (!FEATURES.update) setHidden('update-bar', true);
}

init();

function init() {
  cacheDom();
  // 模块已成功执行：撤掉 index.html 的启动失败提示（守卫可能已因加载慢而提前显示）
  const bootBox = document.getElementById('boot-error');
  if (bootBox) bootBox.hidden = true;
  S = store.readState();
  buildControls();
  bindEvents();
  syncControls();
  store.writeState(S, { mode: 'replace' }); // 归一化 URL（补全 from/to）
  measureHeader();
  refresh();
  loadVenues();
  loadStats();

  // 功能开关：先按静态值立即生效，再尝试用后端配置覆盖
  applyFeatureFlags();
  loadFeatures().then(() => {
    applyFeatureFlags();
    // 「更新数据」按钮：采集完成后自动刷新列表与月历
    if (FEATURES.update) {
      updateCtl.bindUpdateButton({
        onDone: (snap) => {
          if (snap && snap.state === 'done') {
            countsCache.clear(); // 计数可能变了，必须丢掉缓存
            loadVenues();
            refresh();
            loadStats();
          }
        },
      });
    }
  });
  initStaticMode();
}

function cacheDom() {
  const id = (x) => document.getElementById(x);
  dom.header = document.querySelector('.topbar');
  dom.cityChips = id('city-chips');
  dom.typeChips = id('type-chips');
  dom.flagChips = id('flag-chips');
  dom.kindSel = id('kind-sel');
  dom.venueTypeSel = id('venue-type-sel');
  dom.venueSel = id('venue-sel');
  dom.statusSel = id('status-sel');
  dom.priceChip = id('price-chip');
  dom.qInput = id('q');
  // 日期选择（目标要求「支持日期选择」）
  dom.dateFrom = id('date-from');
  dom.dateTo = id('date-to');
  dom.dateClear = id('date-clear');
  // 艺人搜索（目标要求「支持艺人搜索」）
  dom.artistInput = id('artist-q');
  dom.artistList = id('artist-list');
  dom.rangeSeg = id('range-seg');
  dom.viewSeg = id('view-seg');
  dom.main = id('main');
  dom.miniCal = id('mini-cal');
  dom.dayPanel = id('day-panel');
  dom.detail = id('detail-panel');
  dom.footSummary = id('foot-summary');
  dom.footStats = id('foot-stats');
  dom.demoBanner = id('demo-banner');
  dom.modal = id('modal');
  dom.modalBody = id('modal-body');
  dom.modalTitle = id('modal-title');
  dom.toast = id('toast');
}

function buildControls() {
  // 城市：全省 + 8 城（设计稿 §0 覆盖范围）
  dom.cityChips.innerHTML = ['', ...CITIES]
    .map((c) => `<button type="button" class="chip" data-city="${escapeHtml(c)}">${escapeHtml(c || '全省')}</button>`)
    .join('');
  // 类型：全部 / 乐队 / 地偶（地偶带 ★，颜色不作唯一标识）
  dom.typeChips.innerHTML = [['', '全部类型'], ['false', '乐队'], ['true', '★ 地偶']]
    .map(([v, label], i) => `<button type="button" class="chip ${i === 2 ? 'idol' : ''}" data-is-idol="${v}">${escapeHtml(label)}</button>`)
    .join('');
  // 独立标记：与「类型」正交，可多选（多选是「与」关系，即同时命中）
  if (dom.flagChips) {
    dom.flagChips.innerHTML = FLAG_OPTIONS.map(
      (f) => `<button type="button" class="chip flag ${f.cls}" data-flag="${f.key}" ` +
        `aria-pressed="false" title="${escapeHtml(f.title)}">${escapeHtml(f.label)}</button>`
    ).join('');
  }
  dom.kindSel.innerHTML = KINDS.map((k) => `<option value="${escapeHtml(k.value)}">${escapeHtml(k.label)}</option>`).join('');
  dom.statusSel.innerHTML = STATUS_OPTIONS.map((s) => `<option value="${escapeHtml(s.value)}">${escapeHtml(s.label)}</option>`).join('');
  // 场地类型：含「★ 免费场地」—— 商场中庭 / 公园 / 高校这类
  // **不上售票平台**的场地，只能靠小红书、微博发现（本项目重点之一）
  if (dom.venueTypeSel) {
    dom.venueTypeSel.innerHTML = VENUE_TYPES
      .map((v) => `<option value="${escapeHtml(v.value)}">${escapeHtml(v.label)}</option>`)
      .join('');
  }
}

/* --------------------------------------------------------------- 事件绑定 */

function bindEvents() {
  // 全局委托：所有交互（筛选、卡片、月历、按钮）统一在此分发
  document.addEventListener('click', onGlobalClick);
  document.addEventListener('keydown', onGlobalKeydown);

  // 海报加载失败降级：用捕获阶段监听 img 的 error 事件（error 不冒泡，必须捕获）
  // 场景：豆瓣图有防盗链、图床偶发 5xx、或某场次海报被删除 → 不能让用户看到裂图。
  document.addEventListener('error', onPosterError, true);

  dom.kindSel.addEventListener('change', () => update({ kind: dom.kindSel.value }));
  dom.statusSel.addEventListener('change', () => update({ status: dom.statusSel.value }));
  dom.venueSel.addEventListener('change', () => update({ venue_id: dom.venueSel.value }));
  if (dom.venueTypeSel) {
    dom.venueTypeSel.addEventListener('change', () => update({ venue_type: dom.venueTypeSel.value }));
  }
  dom.qInput.addEventListener('input', debounce(onSearchInput, 350));

  // ---- 日期选择（目标要求「支持日期选择」）----
  // `change` 而不是 `input`：日期控件在用户逐步点年月时也会触发 input，
  // 每次都重查会让月历闪烁。change 只在确定日期后触发一次。
  const onDate = (which) => () => {
    const from = which === 'from' ? dom.dateFrom.value : S.from;
    const to = which === 'to' ? dom.dateTo.value : S.to;
    if (from && to && to < from) {
      // 不静默交换（用户会看到自己没输入的值），只是不提交这个非法组合
      toast('结束日期不能早于开始日期');
      return;
    }
    S = store.withRange(S, from, to);
    store.writeState(S);
    syncControls();
    refresh();
  };
  if (dom.dateFrom) dom.dateFrom.addEventListener('change', onDate('from'));
  if (dom.dateTo) dom.dateTo.addEventListener('change', onDate('to'));
  if (dom.dateClear) {
    dom.dateClear.addEventListener('click', () => {
      // 清空 = 回到「本周」预设，而不是留一个空区间
      S = store.withPreset({ ...S, from: '', to: '' }, 'week');
      store.writeState(S);
      syncControls();
      refresh();
    });
  }

  // ---- 艺人搜索（目标要求「支持艺人搜索」）----
  if (dom.artistInput) {
    artistPicker = createArtistPicker({
      input: dom.artistInput,
      list: dom.artistList,
      isStatic: api.isStatic,
      onChange: (name) => {
        // 输入过程中用 replace 不污染历史；这里也保持 replace（连续输入）
        update({ artist: name }, { mode: 'replace' });
      },
    });
  }

  window.addEventListener('popstate', () => {
    // 浏览器前进/后退：重新读 URL 并整体重绘
    S = store.readState();
    syncControls();
    refresh();
  });
  window.addEventListener('resize', measureHeader);
}

/**
 * 海报加载失败 → 就地替换成「首字 + 渐变」占位。
 *
 * 只处理我们标记过的海报图（data-poster-fallback），避免影响其它图片。
 * 用一个 sentinel 属性防止占位图本身再次触发 error 导致无限循环。
 */
function onPosterError(e) {
  const img = e.target;
  if (!(img instanceof HTMLImageElement)) return;
  if (!img.hasAttribute('data-poster-fallback')) return;
  if (img.getAttribute('data-fallback-done') === '1') return;
  img.setAttribute('data-fallback-done', '1');

  const title = (img.getAttribute('alt') || '').replace(/\s*演出海报\s*$/, '') || '?';
  const glyph = Array.from(title.trim())[0] || '?';
  const isIdol = img.closest('.panel')?.querySelector('.tag.idol') != null;

  const ph = document.createElement('div');
  ph.className = `poster ph ${isIdol ? 'idol' : 'band'}`;
  ph.setAttribute('aria-hidden', 'true');
  ph.innerHTML = `<span>${glyph}</span><small>海报暂不可用</small>`;
  img.replaceWith(ph);
}

function onGlobalClick(e) {
  const pick = (sel) => (e.target instanceof Element ? e.target.closest(sel) : null);
  let el;

  if (pick('[data-modal-close]') || (e.target instanceof Element && e.target.classList.contains('modal-back'))) {
    closeModal();
    return;
  }
  if ((el = pick('[data-switch-city]'))) { update({ city: el.dataset.switchCity }); return; }
  if (pick('[data-city-all]')) { update({ city: '' }); return; }
  if ((el = pick('[data-correct]'))) { openCorrect(el.dataset.correct); return; }
  if ((el = pick('[data-ics]'))) { downloadIcs(el.dataset.ics); return; }
  if (pick('[data-close-day]')) { S = { ...S, d: '' }; store.writeState(S); syncControls(); refresh(); return; }
  if (pick('[data-retry]')) { refresh(); return; }
  if (pick('[data-demo]')) { enableDemo(); return; }
  if ((el = pick('[data-day]'))) { pickDay(el.dataset.day); return; }
  if ((el = pick('[data-month-nav]'))) { shiftMonth(Number(el.dataset.monthNav)); return; }
  if (pick('[data-month-today]')) { goToday(); return; }
  if ((el = pick('[data-city]'))) { update({ city: el.dataset.city }); return; }
  if ((el = pick('[data-is-idol]'))) { update({ is_idol: el.dataset.isIdol }); return; }
  if ((el = pick('[data-flag]'))) {
    // 切换单个标记，保留其它已选标记
    const key = el.dataset.flag;
    const cur = S.flags || [];
    const next = cur.includes(key) ? cur.filter((x) => x !== key) : [...cur, key];
    update({ flags: next });
    return;
  }
  if ((el = pick('[data-range]'))) { setRange(el.dataset.range); return; }
  if ((el = pick('[data-view]'))) { setView(el.dataset.view); return; }
  if (pick('#price-chip')) { update({ price_max: S.price_max ? '' : '150' }); return; }
  if (pick('#ics-btn') || pick('#ics-btn-2')) { copyIcsLink(); return; }
  if (pick('#copy-link-btn')) { copyShareLink(); return; }
  if (pick('#about-btn')) { openAbout(); return; }
  if (pick('#feedback-btn')) { openCorrect(selectedId || ''); return; }
  if ((el = pick('[data-occ-id]'))) { selectOccurrence(el.dataset.occId); return; }
}

function onGlobalKeydown(e) {
  if (e.key === 'Escape') {
    closeModal();
    return;
  }
  // 卡片是 role=button 的 article，需要手动支持 Enter / Space
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const el = document.activeElement;
  if (el && el.dataset && el.dataset.occId) {
    e.preventDefault();
    selectOccurrence(el.dataset.occId);
  }
}

function onSearchInput() {
  const q = dom.qInput.value.trim();
  if (q === S.q) return;
  S = { ...S, q };
  store.writeState(S, { mode: 'replace' }); // 输入不污染历史
  refresh();
}

/* ------------------------------------------------------------- 状态变更 */

/** 通用更新：写 URL（pushState）后重绘。 */
function update(patch, { mode = 'push' } = {}) {
  const next = { ...S, ...patch };
  // 城市变化时，若已选场地不属于该城，则清空场地筛选
  if (patch.city !== undefined && S.venue_id) {
    const v = allVenues.find((x) => String(x.id) === String(S.venue_id));
    if (v && patch.city && v.city !== patch.city) next.venue_id = '';
  }
  S = next;
  renderVenueOptions();
  store.writeState(S, { mode });
  syncControls();
  refresh();
}

function setRange(preset) {
  S = store.withPreset(S, preset);
  S = { ...S, month: monthKey(S.from) };
  store.writeState(S);
  syncControls();
  refresh();
}

function setView(v) {
  if (v === S.view) return;
  S = { ...S, view: v };
  if (v === 'calendar' && (!S.d || monthKey(S.d) !== S.month)) {
    const today = todayISO();
    S.d = monthKey(today) === S.month ? today : `${S.month}-01`;
  }
  store.writeState(S);
  syncControls();
  refresh();
}

async function pickDay(iso) {
  if (S.view === 'list') {
    // 列表视图：点某天 = 把时间范围收窄到这一天（左侧直接看到当天排期）
    S = { ...S, from: iso, to: iso, preset: 'custom', d: iso, month: monthKey(iso) };
  } else {
    S = store.withDay(S, iso);
  }
  store.writeState(S);
  syncControls();
  await refresh();
}

function shiftMonth(delta) {
  S = { ...S, month: store.shiftMonth(S.month, delta) };
  if (S.d && monthKey(S.d) !== S.month) S.d = '';
  store.writeState(S);
  syncControls();
  refresh();
}

function goToday() {
  const today = todayISO();
  S = { ...S, month: monthKey(today), d: today };
  store.writeState(S);
  syncControls();
  refresh();
}

function enableDemo() {
  S = { ...S, demo: true };
  store.writeState(S);
  syncControls();
  refresh();
  loadVenues();
  loadStats();
}

/** 控件回显（chip 高亮、下拉值、输入框、范围说明、示例横幅）。 */
function syncControls() {
  dom.cityChips.querySelectorAll('[data-city]').forEach((b) => b.classList.toggle('on', b.dataset.city === S.city));
  dom.typeChips.querySelectorAll('[data-is-idol]').forEach((b) => b.classList.toggle('on', b.dataset.isIdol === S.is_idol));
  if (dom.flagChips) {
    dom.flagChips.querySelectorAll('[data-flag]').forEach((b) => {
      const on = (S.flags || []).includes(b.dataset.flag);
      b.classList.toggle('on', on);
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }
  dom.rangeSeg.querySelectorAll('[data-range]').forEach((b) => {
    const on = b.dataset.range === S.preset;
    b.classList.toggle('on', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  dom.viewSeg.querySelectorAll('[data-view]').forEach((b) => {
    const on = b.dataset.view === S.view;
    b.classList.toggle('on', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  if (dom.venueTypeSel) dom.venueTypeSel.value = S.venue_type || '';
  dom.kindSel.value = S.kind;
  dom.statusSel.value = S.status;
  dom.priceChip.classList.toggle('on', Boolean(S.price_max));
  dom.priceChip.setAttribute('aria-pressed', S.price_max ? 'true' : 'false');
  dom.priceChip.textContent = S.price_max ? `¥${S.price_max} 以下` : '价格不限';
  if (document.activeElement !== dom.qInput) dom.qInput.value = S.q;
  // 日期选择控件回填（不在编辑时才写，避免打断输入）
  if (dom.dateFrom && document.activeElement !== dom.dateFrom) dom.dateFrom.value = S.from || '';
  if (dom.dateTo && document.activeElement !== dom.dateTo) dom.dateTo.value = S.to || '';
  // 艺人搜索框回填
  if (artistPicker) artistPicker.setValue(S.artist || '');
  dom.demoBanner.hidden = !S.demo;
}

/* ----------------------------------------------------------------- 取数 */

function baseFilters() {
  return {
    city: S.city,
    kind: S.kind,
    is_idol: S.is_idol,
    flags: S.flags || [],
    venue_id: S.venue_id,
    venue_type: S.venue_type,
    status: S.status,
    price_max: S.price_max,
    q: S.q,
    // 艺人搜索：搜阵容（后端 artist_q / 静态站本地过滤同名参数）
    artist: S.artist,
  };
}

function listFilters() {
  return { ...baseFilters(), from: S.from, to: S.to };
}

function filterKey() {
  return [S.city, S.kind, S.is_idol, (S.flags || []).join('+'), S.venue_id, S.venue_type, S.status, S.price_max, S.q, S.artist, S.demo ? 'demo' : ''].join('|');
}

async function getCounts(month, city) {
  const key = `${month}|${city || ''}|${S.demo ? 'demo' : ''}`;
  if (!countsCache.has(key)) countsCache.set(key, api.loadCounts(month, city, { demo: S.demo }));
  return countsCache.get(key);
}

function indexItems(items) {
  items.forEach((it) => itemIndex.set(String(it.id), it));
}

function currentItem() {
  return selectedId ? itemIndex.get(String(selectedId)) || null : null;
}

/** 选中项失效时自动回落到第一条，保证详情面板不是空的。 */
function resolveSelection(items) {
  if (selectedId && items.some((it) => String(it.id) === String(selectedId))) return String(selectedId);
  return items.length ? String(items[0].id) : null;
}

/* --------------------------------------------------------------- 渲染 */

async function refresh() {
  measureHeader();
  if (S.view === 'calendar') await renderCalendarView();
  else await renderListView();
  updateFootNote();
}

/* ---------- 列表视图 ---------- */
async function renderListView() {
  dom.miniCal.hidden = false;
  dom.dayPanel.hidden = true;
  dom.dayPanel.innerHTML = '';
  dom.main.setAttribute('aria-busy', 'true');
  view.renderTimelineSkeleton(dom.main);
  renderMiniCalendar(); // 独立并发加载，失败不影响主列表

  let data = null;
  let error = null;
  try {
    data = await api.loadOccurrences(listFilters(), { demo: S.demo });
  } catch (err) {
    error = err;
  }
  dom.main.setAttribute('aria-busy', 'false');

  if (error) {
    view.renderError(dom.main, {
      title: '无法加载演出数据',
      message: error.message,
      url: error.url || `/api/occurrences${api.buildQuery(listFilters())}`,
    });
    view.renderDetail(dom.detail, { item: null });
    lastMeta = { total: 0, generated_at: '', demo: S.demo };
    return;
  }

  indexItems(data.items);
  lastMeta = data;
  selectedId = resolveSelection(data.items);

  if (!data.items.length) {
    // 单日范围（点过日历某天）用「这一天」，否则「这段时间」——
    // 否则会出现「这段时间没有收录的演出：10月17日 · 珠海」这种自相矛盾的提示
    const singleDay = S.from === S.to;
    view.renderEmpty(dom.main, {
      title: singleDay ? '这一天没有收录的演出' : '这段时间没有收录的演出',
      hint: `${rangeLabel(S.from, S.to)}${S.city ? ` · ${S.city}` : ' · 全省'} 没有匹配的场次；` +
        (singleDay
          ? '可以换个城市，或点上面的时间范围看更宽的区间。'
          : '可以换个城市、放宽时间范围，或清空筛选条件。'),
      cities: cityChoices(S.city),
      extraHtml: '<div class="state-actions"><button type="button" class="btn" data-retry="1">重新加载</button>' +
        (S.demo ? '' : '<button type="button" class="btn p" data-demo="1">查看内置示例数据</button>') + '</div>',
    });
  } else {
    view.renderTimeline(dom.main, data.items, {
      selectedId,
      today: todayISO(),
      rangeLabel: rangeLabel(S.from, S.to),
      meta: data,
    });
  }
  view.renderDetail(dom.detail, { item: currentItem(), demo: S.demo });
}

async function renderMiniCalendar() {
  try {
    const data = await getCounts(S.month, S.city);
    if (S.view !== 'list') return; // 已切到月历视图：避免把结果写进隐藏的迷你月历
    view.renderMonth(dom.miniCal, {
      month: S.month,
      days: data.days,
      selected: S.d,
      today: todayISO(),
      compact: true,
      demo: data.demo,
    });
  } catch (err) {
    dom.miniCal.innerHTML = `<div class="panel-hint">月历计数加载失败：${escapeHtml(err.message)}</div>`;
  }
}

/* ---------- 月历视图 ---------- */
async function renderCalendarView() {
  dom.miniCal.hidden = true;
  dom.miniCal.innerHTML = ''; // 清掉迷你月历的残留 DOM（隐藏状态下不会被看到）
  dom.dayPanel.hidden = false;
  dom.main.setAttribute('aria-busy', 'true');
  view.renderMonthSkeleton(dom.main);

  // 选中日兜底：URL 的 d → 今天（若在本月）→ 本月 1 日
  const today = todayISO();
  if (!S.d || monthKey(S.d) !== S.month) {
    S.d = monthKey(today) === S.month ? today : `${S.month}-01`;
    store.writeState(S, { mode: 'replace' });
  }

  let counts = null;
  let error = null;
  try {
    counts = await getCounts(S.month, S.city);
  } catch (err) {
    error = err;
  }
  dom.main.setAttribute('aria-busy', 'false');

  if (error) {
    view.renderError(dom.main, {
      title: '无法加载月历计数',
      message: error.message,
      url: error.url || `/api/calendar/counts${api.buildQuery({ month: S.month, city: S.city })}`,
    });
    lastMeta = { total: 0, generated_at: lastMeta.generated_at, demo: S.demo };
  } else {
    view.renderMonth(dom.main, {
      month: S.month,
      days: counts.days,
      selected: S.d,
      today,
      demo: counts.demo,
    });
    // 页脚口径：月视图下统计当前月的场次（计数接口只按 month+city 过滤）
    const days = counts.days || {};
    const total = Object.keys(days).reduce(
      (acc, k) => acc + (Number(days[k].band) || 0) + (Number(days[k].idol) || 0),
      0,
    );
    lastMeta = { total, generated_at: lastMeta.generated_at, demo: counts.demo };
  }

  await renderDayPanel();
}

async function renderDayPanel() {
  const date = S.d;
  if (!date) {
    dom.dayPanel.hidden = true;
    return;
  }
  dom.dayPanel.hidden = false;
  view.renderDayPanel(dom.dayPanel, { date, loading: true, selectedId });

  const key = `${date}|${filterKey()}`;
  try {
    let data = dayCache.get(key);
    if (!data) {
      data = await api.loadOccurrences({ ...baseFilters(), from: date, to: date }, { demo: S.demo });
      dayCache.set(key, data);
    }
    if (S.view !== 'calendar' || S.d !== date) return; // 期间用户已切走：丢弃这次结果
    indexItems(data.items);
    selectedId = resolveSelection(data.items);
    view.renderDayPanel(dom.dayPanel, { date, items: data.items, selectedId });
    view.renderDetail(dom.detail, { item: currentItem(), demo: S.demo });
  } catch (err) {
    view.renderDayPanel(dom.dayPanel, { date, error: err.message });
    view.renderDetail(dom.detail, { item: null });
  }
}

function updateFootNote() {
  const parts = [];
  // 列表视图报「时间范围」，月历视图报「月份」，避免口径混淆
  parts.push(S.view === 'calendar'
    ? `<b>${escapeHtml(monthLabel(S.month))}</b>`
    : `<b>${escapeHtml(rangeLabel(S.from, S.to))}</b>`);
  parts.push(S.city ? escapeHtml(S.city) : '全省');
  parts.push(`共 <b>${escapeHtml(String(lastMeta.total || 0))}</b> 场`);
  if (lastMeta.generated_at) parts.push(`数据最后更新 ${escapeHtml(formatDateTime(lastMeta.generated_at))}`);
  dom.footSummary.innerHTML = parts.join(' · ');
}

/* --------------------------------------------------------- 侧栏 / 详情 */

function selectOccurrence(id) {
  selectedId = String(id);
  document.querySelectorAll('[data-occ-id]').forEach((el) => {
    const on = el.dataset.occId === selectedId;
    el.classList.toggle('sel', on);
    if (el.getAttribute('role') === 'button') el.setAttribute('aria-current', on ? 'true' : 'false');
  });
  view.renderDetail(dom.detail, { item: currentItem(), demo: S.demo });
  // 窄屏下详情面板在下方，滚动过去更顺手
  if (window.matchMedia('(max-width: 900px)').matches) {
    dom.detail.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

/* ------------------------------------------------------------- .ics / 分享 */

async function copyIcsLink() {
  const url = new URL(api.buildIcsUrl(baseFilters()), window.location.href).href;
  const ok = await copyText(url);
  toast(ok
    ? `订阅链接已复制：${url}${S.demo ? '（示例模式，需后端运行后才会返回日历）' : ''}`
    : `复制失败，请手动复制：${url}`, 4200);
}

async function copyShareLink() {
  const ok = await copyText(store.shareUrl(S));
  toast(ok ? '本页链接已复制，可直接分享' : '复制失败，请从地址栏复制');
}

function downloadIcs(id) {
  const item = itemIndex.get(String(id));
  if (!item) {
    toast('找不到该场次数据，请重新加载后再试');
    return;
  }
  const filename = icsFilename(item);
  downloadText(filename, buildIcs(item));
  toast(`已生成日历文件：${filename}（可直接导入手机日历）`, 4000);
}

/* ----------------------------------------------------------------- 弹窗 */

function openModal(title, bodyHtml) {
  dom.modalTitle.textContent = title;
  dom.modalBody.innerHTML = bodyHtml;
  dom.modal.hidden = false;
  document.body.classList.add('modal-open');
  const first = dom.modalBody.querySelector('textarea, input, select, button');
  if (first) first.focus();
}

function closeModal() {
  if (dom.modal.hidden) return;
  dom.modal.hidden = true;
  dom.modalBody.innerHTML = '';
  document.body.classList.remove('modal-open');
}

function openCorrect(id) {
  const item = itemIndex.get(String(id));
  const title = item && item.event ? item.event.title : '';
  openModal('纠错 / 补充信息', `
    <p class="dim">${title
      ? `条目：<b>${escapeHtml(title)}</b>（occurrence id ${escapeHtml(String(id))}）`
      : '未指定具体场次 —— 请在说明里写清「哪一天 / 哪个场地 / 哪支乐队」'}</p>
    <label class="fld"><span>问题类型</span>
      <select id="cor-kind">
        <option value="wrong_time">时间不对</option>
        <option value="wrong_venue">场地不对</option>
        <option value="wrong_price">票价 / 票种不对</option>
        <option value="wrong_lineup">阵容或登场顺序不对</option>
        <option value="status_change">已取消 / 已改期</option>
        <option value="other">其它补充</option>
      </select>
    </label>
    <label class="fld"><span>补充说明</span>
      <textarea id="cor-msg" rows="4" placeholder="例如：开演时间应为 20:00，来源：主办方微博"></textarea>
    </label>
    <label class="fld"><span>联系方式（可选）</span>
      <input id="cor-contact" placeholder="邮箱或微信，便于回访确认">
    </label>
    <div class="modal-actions">
      <button type="button" class="btn p" data-submit="1">提交</button>
      <button type="button" class="btn" data-modal-close="1">取消</button>
    </div>
    <p class="dim small">提交后进入人工审核队列（review_task），24 小时内处理；提交即表示你确认信息来自公开渠道。
      ${S.demo ? '当前为示例数据模式，提交不会真正入库。' : ''}</p>
  `);
  const submit = dom.modalBody.querySelector('[data-submit]');
  if (submit) submit.addEventListener('click', () => submitCorrect(id));
}

async function submitCorrect(id) {
  const kind = (dom.modalBody.querySelector('#cor-kind') || {}).value || 'other';
  const message = ((dom.modalBody.querySelector('#cor-msg') || {}).value || '').trim();
  const contact = ((dom.modalBody.querySelector('#cor-contact') || {}).value || '').trim();
  if (!message) {
    toast('请先填写补充说明');
    return;
  }
  const btn = dom.modalBody.querySelector('[data-submit]');
  if (btn) {
    btn.disabled = true;
    btn.textContent = '提交中…';
  }
  try {
    const payload = { kind, message, contact, page_url: window.location.href };
    if (id) payload.occurrence_id = Number(id) || id;
    await api.submitCorrection(payload);
    closeModal();
    toast('感谢！投稿将在 24h 内审核');
  } catch (err) {
    if (btn) {
      btn.disabled = false;
      btn.textContent = '提交';
    }
    const copied = await copyText(`【纠错】occurrence ${id || '（未指定）'}\n类型：${kind}\n说明：${message}\n联系方式：${contact || '（未填）'}`);
    toast(`提交失败：${err.message}${copied ? '；内容已复制到剪贴板，可粘贴给维护者' : ''}`, 5000);
  }
}

function openAbout() {
  openModal('数据来源与免责声明', `
    <p>本站只聚合<b>公开渠道的演出事实信息</b>（谁 / 何时 / 何地 / 多少钱），来源包括秀动、豆瓣同城、B 站会员购、
      微博与场地自营动态等；每条记录都保留来源链接与抓取时间，可溯源。</p>
    <p>不转载正文文案、不搬运海报原图（图片引用原站地址）；不做代购、不加价转售、不提供抢票服务、不转售数据。</p>
    <p>自动解析的条目会标注置信度；<b>置信度低于 0.8 或场地/时间待确认的场次用虚线边框与「待确认」徽标提示</b>，
      改期场次保留原日期痕迹，避免乐迷扑空。信息以主办方与场地方公告为准。</p>
    <p>若主办方、场地方或任何权利人要求移除内容，请通过「纠错 / 补充」入口联系我们，我们会立即处理。</p>
    <div class="modal-actions"><button type="button" class="btn" data-modal-close="1">知道了</button></div>
  `);
}

/* ------------------------------------------------------------- 场地下拉 / 统计 */

async function loadVenues() {
  try {
    const data = await api.loadVenues({ demo: S.demo });
    allVenues = Array.isArray(data.items) ? data.items : [];
  } catch (err) {
    allVenues = []; // 下拉不可用不影响主流程
  }
  renderVenueOptions();
}

function renderVenueOptions() {
  const list = allVenues.filter((v) => !S.city || v.city === S.city || String(v.id) === String(S.venue_id));
  const opts = ['<option value="">全部场地</option>'].concat(
    list.map((v) => `<option value="${escapeHtml(v.id)}">${escapeHtml(v.name)}${S.city ? '' : `（${escapeHtml(v.city || '')}）`}</option>`),
  );
  dom.venueSel.innerHTML = opts.join('');
  dom.venueSel.value = S.venue_id;
}

async function loadStats() {
  if (statsLoaded && !S.demo) return;
  try {
    const st = await api.loadStats({ demo: S.demo });
    statsLoaded = true;
    const src = (st.sources || [])
      .filter((s) => s && s.code)
      .slice(0, 6)
      .map((s) => escapeHtml(sourceLabel(s.code)))
      .join(' · ');
    dom.footStats.innerHTML = [
      `已收录 <b>${escapeHtml(String(st.occurrences || 0))}</b> 场`,
      `${escapeHtml(String(st.events || 0))} 个活动`,
      `${escapeHtml(String(st.artists || 0))} 位艺人/团体`,
      `${escapeHtml(String(st.venues || 0))} 个场地`,
      src ? `<span class="dim">来源：${src}</span>` : '',
    ].filter(Boolean).join(' · ');
  } catch (err) {
    dom.footStats.textContent = '统计数据暂不可用';
  }
}

/* ----------------------------------------------------------------- 小工具 */

function cityChoices(current) {
  return ['', ...CITIES].filter((c) => c !== current);
}

let toastTimer = null;
function toast(message, ms = 3000) {
  dom.toast.textContent = message;
  dom.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    dom.toast.hidden = true;
  }, ms);
}

/** 顶部吸顶高度 → CSS 变量，右侧 sticky 面板据此定位。 */
function measureHeader() {
  if (!dom.header) return;
  const h = dom.header.offsetHeight || 0;
  document.documentElement.style.setProperty('--hdr', `${h + 12}px`);
}

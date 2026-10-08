/**
 * sample-data.js — 内置合成示例数据（**非真实排期**，仅用于演示与降级）。
 *
 * 何时使用：
 *   1) URL 带 `?demo=1`；
 *   2) 后端 `/api/occurrences` 请求失败时，用户点「查看内置示例数据」。
 * 字段结构与《01-总体设计.md》§5.2 的 occurrence 契约完全一致，并额外覆盖
 * 售罄 / 已改期 / 已取消 / 待确认（低置信度）/ 免费 / 场地待定 等边界情形。
 *
 * 日期全部相对「今天」生成，因此任何一天打开 demo 都能看到当前周/周末/本月的排期。
 */
import { addDays, isoDate, todayISO } from './util.js';
import { isIdolItem } from './labels.js';

const T = todayISO();
/** 相对今天 n 天的日期。 */
const D = (n) => addDays(T, n);
/** 相对今天 n 天的 ISO 时间串（带 +08:00，与后端一致）。 */
const at = (n, hhmm) => `${D(n)}T${hhmm}:00+08:00`;

/** 原始示例条目（未排序）。 */
const RAW_ITEMS = [
  {
    id: 9001,
    event: { id: 801, title: '痛仰乐队「不败」巡演 广州站', series_key: '不败巡演', kind: 'tour_stop', is_idol: false, poster_thumb: null },
    city: '广州',
    venue: { id: 1, name: 'MAO Livehouse 广州（永庆坊店）', district: '荔湾区', address: '荔湾区恩宁路永庆坊', lng: 113.2421, lat: 23.1157 },
    open_at: at(0, '19:00'),
    start_at: at(0, '20:00'),
    end_at: at(0, '22:30'),
    date_precision: 'exact',
    status: 'on_sale',
    status_note: null,
    price: { min: 180, max: 280, currency: 'CNY', is_free: false },
    age_limit: '全年龄',
    lineup: [
      { artist_id: 11, name: '痛仰乐队', role: 'headliner', order: 1 },
      { artist_id: 12, name: '潮汐计划', role: 'guest', order: 2 },
    ],
    tickets: [
      { name: '预售票', price: 180, channel: '秀动', status: 'on_sale', url: 'https://example.com/tickets/9001-pre' },
      { name: '现场票', price: 280, channel: '现场', status: 'unknown', url: null },
    ],
    extra: {},
    tokuten_note: null,
    confidence: 0.96,
    verified: true,
    sources: [
      { code: 'showstart', url: 'https://example.com/showstart/9001', fetched_at: at(-1, '10:12') },
      { code: 'douban', url: 'https://example.com/douban/9001', fetched_at: at(0, '10:40') },
    ],
  },
  {
    id: 9002,
    event: { id: 802, title: '★ 星屑公演 Vol.12', series_key: '星屑公演', kind: 'idol_regular', is_idol: true, poster_thumb: null },
    city: '广州',
    venue: { id: 2, name: 'SD Livehouse（北场馆）', district: '海珠区', address: '海珠区南洲路 154 号侨建·HICITY 2F', lng: 113.3021, lat: 23.0712 },
    open_at: at(0, '19:00'),
    start_at: at(0, '19:30'),
    end_at: at(0, '22:00'),
    date_precision: 'exact',
    status: 'on_sale',
    status_note: null,
    price: { min: 120, max: 150, currency: 'CNY', is_free: false },
    age_limit: '全年龄',
    lineup: [
      { artist_id: 331, name: '星屑少女', role: 'headliner', order: 1 },
      { artist_id: 332, name: '月见少女', role: 'performer', order: 2 },
      { artist_id: 333, name: '甜梦计划', role: 'guest', order: 3 },
      { artist_id: 334, name: '晨曦计划', role: 'performer', order: 4 },
    ],
    tickets: [
      { name: '预售票', price: 120, channel: '秀动', status: 'on_sale', url: 'https://example.com/tickets/9002-pre' },
      { name: '现场票', price: 150, channel: '现场', status: 'unknown', url: null },
      { name: '特典券', price: 60, channel: '现场', status: 'unknown', url: null },
    ],
    extra: { 特典券: '¥60', チェキ: '¥50/张', 物贩: '18:00 起开放' },
    tokuten_note: '每张特典券可对谈约 60 秒或拍立得 1 张；特典券现场现金/微信购买，不线上销售。',
    confidence: 0.94,
    verified: true,
    sources: [
      { code: 'wechat', url: 'https://example.com/wechat/9002', fetched_at: at(-2, '21:05') },
      { code: 'weibo', url: 'https://example.com/weibo/9002', fetched_at: at(-1, '09:30') },
      { code: 'showstart', url: 'https://example.com/showstart/9002', fetched_at: at(0, '10:12') },
    ],
  },
  {
    id: 9003,
    event: { id: 803, title: '噪音拼盘 #37：三支本地乐队', series_key: '噪音拼盘', kind: 'taiban', is_idol: false, poster_thumb: null },
    city: '广州',
    venue: { id: 3, name: '191space', district: '越秀区', address: '越秀区广州大道中路 191 号', lng: 113.3069, lat: 23.1301 },
    open_at: at(1, '19:30'),
    start_at: at(1, '20:00'),
    date_precision: 'exact',
    status: 'on_sale',
    status_note: null,
    price: { min: 80, max: 100, currency: 'CNY', is_free: false },
    age_limit: '全年龄',
    lineup: [
      { artist_id: 21, name: '闷饼 MOONBAND', role: 'headliner', order: 1 },
      { artist_id: 22, name: '沙漏 SAALAU', role: 'performer', order: 2 },
      { artist_id: 23, name: '六道母', role: 'performer', order: 3 },
    ],
    tickets: [
      { name: '预售票', price: 80, channel: '秀动', status: 'on_sale', url: 'https://example.com/tickets/9003-pre' },
      { name: '现场票', price: 100, channel: '现场', status: 'unknown', url: null },
    ],
    extra: {},
    tokuten_note: null,
    confidence: 0.88,
    verified: true,
    sources: [{ code: 'showstart', url: 'https://example.com/showstart/9003', fetched_at: at(-1, '11:20') }],
  },
  {
    id: 9004,
    event: { id: 804, title: '★ 月见少女 生日祭 & 联合 Live', series_key: '月见少女生诞祭', kind: 'idol_birthday', is_idol: true, poster_thumb: null },
    city: '广州',
    venue: null,
    open_at: at(1, '18:30'),
    start_at: at(1, '19:00'),
    date_precision: 'exact',
    status: 'unknown',
    status_note: '场地与票价尚未在文案中写明',
    price: { min: null, max: null, currency: 'CNY', is_free: false },
    age_limit: '可能含 18+ 环节',
    lineup: [
      { artist_id: 332, name: '月见少女', role: 'headliner', order: 1 },
      { artist_id: 335, name: '待披露嘉宾', role: 'guest', order: 2 },
    ],
    tickets: [],
    extra: {},
    tokuten_note: null,
    confidence: 0.42,
    verified: false,
    sources: [{ code: 'weibo', url: 'https://example.com/weibo/9004', fetched_at: at(0, '08:15') }],
  },
  {
    id: 9005,
    event: { id: 805, title: '★ 甜梦计划 Oneman Live「第一次」', series_key: '甜梦计划 Oneman', kind: 'oneman', is_idol: true, poster_thumb: null },
    city: '广州',
    venue: { id: 4, name: 'MAO Livehouse 广州（中大店·二号馆）', district: '海珠区', address: '海珠区新港西路 82 号中大门', lng: 113.2938, lat: 23.0901 },
    open_at: at(2, '18:30'),
    start_at: at(2, '19:00'),
    date_precision: 'exact',
    status: 'sold_out',
    status_note: null,
    price: { min: 150, max: 150, currency: 'CNY', is_free: false },
    age_limit: '全年龄',
    lineup: [{ artist_id: 333, name: '甜梦计划', role: 'headliner', order: 1 }],
    tickets: [
      { name: '统一票', price: 150, channel: '微店', status: 'sold_out', url: 'https://example.com/tickets/9005' },
      { name: '特典券', price: 70, channel: '现场', status: 'unknown', url: null },
    ],
    extra: { 特典券: '¥70', 物贩: '18:30 起' },
    tokuten_note: '特典会按票号顺序排队，每人每次 60 秒。',
    confidence: 0.9,
    verified: true,
    sources: [
      { code: 'weidian', url: 'https://example.com/weidian/9005', fetched_at: at(-3, '20:00') },
      { code: 'showstart', url: 'https://example.com/showstart/9005', fetched_at: at(-1, '11:20') },
    ],
  },
  {
    id: 9006,
    event: { id: 806, title: '后摇之夜：海风与低频', series_key: '后摇之夜', kind: 'rock_live', is_idol: false, poster_thumb: null },
    city: '广州',
    venue: { id: 5, name: '声音共和 Livehouse（广州塔店）', district: '海珠区', address: '海珠区新滘中路 88 号海珠同创汇东一街 11 号', lng: 113.3312, lat: 23.0871 },
    open_at: at(2, '20:00'),
    start_at: at(2, '20:30'),
    date_precision: 'exact',
    status: 'on_sale',
    status_note: null,
    price: { min: 160, max: 200, currency: 'CNY', is_free: false },
    age_limit: '全年龄',
    lineup: [
      { artist_id: 31, name: '海风与低频', role: 'headliner', order: 1 },
      { artist_id: 32, name: '无信号区', role: 'guest', order: 2 },
    ],
    tickets: [
      { name: '预售票', price: 160, channel: '秀动', status: 'on_sale', url: 'https://example.com/tickets/9006-pre' },
      { name: '现场票', price: 200, channel: '现场', status: 'unknown', url: null },
    ],
    extra: {},
    tokuten_note: null,
    confidence: 0.92,
    verified: true,
    sources: [
      { code: 'showstart', url: 'https://example.com/showstart/9006', fetched_at: at(-1, '11:22') },
      { code: 'damai', url: 'https://example.com/damai/9006', fetched_at: at(-1, '15:02') },
    ],
  },
  {
    id: 9007,
    event: { id: 807, title: '午后不插电：露台音乐会', series_key: '午后不插电', kind: 'rock_live', is_idol: false, poster_thumb: null },
    city: '广州',
    venue: { id: 6, name: '游声场 FreeField', district: '番禺区', address: '番禺区汉溪大道东 366 号', lng: 113.3311, lat: 22.9945 },
    open_at: at(3, '13:30'),
    start_at: at(3, '14:00'),
    date_precision: 'exact',
    status: 'finished',
    status_note: null,
    price: { min: 0, max: 0, currency: 'CNY', is_free: true },
    age_limit: '全年龄',
    lineup: [{ artist_id: 41, name: '阿树与朋友', role: 'headliner', order: 1 }],
    tickets: [],
    extra: { 入场: '免费入场，无需预约' },
    tokuten_note: null,
    confidence: 0.85,
    verified: false,
    sources: [{ code: 'douban', url: 'https://example.com/douban/9007', fetched_at: at(-4, '09:00') }],
  },
  {
    id: 9008,
    event: { id: 808, title: '★ 星屑公演 Vol.13', series_key: '星屑公演', kind: 'idol_regular', is_idol: true, poster_thumb: null },
    city: '广州',
    venue: { id: 2, name: 'SD Livehouse（北场馆）', district: '海珠区', address: '海珠区南洲路 154 号侨建·HICITY 2F', lng: 113.3021, lat: 23.0712 },
    open_at: at(5, '19:00'),
    start_at: at(5, '19:30'),
    date_precision: 'exact',
    status: 'on_sale',
    status_note: null,
    price: { min: 120, max: 150, currency: 'CNY', is_free: false },
    age_limit: '全年龄',
    lineup: [
      { artist_id: 331, name: '星屑少女', role: 'headliner', order: 1 },
      { artist_id: 336, name: '宵崎计划', role: 'performer', order: 2 },
      { artist_id: 333, name: '甜梦计划', role: 'performer', order: 3 },
    ],
    tickets: [
      { name: '预售票', price: 120, channel: '秀动', status: 'on_sale', url: 'https://example.com/tickets/9008-pre' },
      { name: 'チェキ券', price: 50, channel: '现场', status: 'unknown', url: null },
    ],
    extra: { チェキ: '¥50/张' },
    tokuten_note: null,
    confidence: 0.9,
    verified: true,
    sources: [{ code: 'wechat', url: 'https://example.com/wechat/9008', fetched_at: at(-1, '21:00') }],
  },
  {
    id: 9009,
    event: { id: 809, title: '金属联合专场：铁的三种形态', series_key: '金属联合专场', kind: 'taiban', is_idol: false, poster_thumb: null },
    city: '广州',
    venue: { id: 7, name: 'TU 凸空间', district: '海珠区', address: '海珠区新港东路 1066 号中洲中心国茶荟负一层', lng: 113.3502, lat: 23.1021 },
    open_at: at(5, '19:30'),
    start_at: at(5, '20:00'),
    date_precision: 'exact',
    status: 'postponed',
    status_note: '改期至 12 月 5 日，原票继续有效',
    price: { min: 100, max: 160, currency: 'CNY', is_free: false },
    age_limit: '全年龄',
    lineup: [
      { artist_id: 51, name: '铁锈纪元', role: 'headliner', order: 1 },
      { artist_id: 52, name: '熔炉', role: 'performer', order: 2 },
    ],
    tickets: [{ name: '预售票', price: 100, channel: '秀动', status: 'on_sale', url: 'https://example.com/tickets/9009' }],
    extra: {},
    tokuten_note: null,
    confidence: 0.97,
    verified: true,
    sources: [{ code: 'venue_site', url: 'https://example.com/venue/9009', fetched_at: at(-1, '12:00') }],
  },
  {
    id: 9010,
    event: { id: 810, title: '南方潮湿 Vol.3', series_key: '南方潮湿', kind: 'rock_live', is_idol: false, poster_thumb: null },
    city: '广州',
    venue: { id: 8, name: '不大空间', district: '荔湾区', address: '荔湾区泫塘五约涌边街 16 号', lng: 113.2288, lat: 23.1132 },
    open_at: at(6, '18:30'),
    start_at: at(6, '19:00'),
    date_precision: 'exact',
    status: 'cancelled',
    status_note: '因场地档期冲突取消，票款已全额退回',
    price: { min: 60, max: 80, currency: 'CNY', is_free: false },
    age_limit: '全年龄',
    lineup: [{ artist_id: 61, name: '潮湿信号', role: 'headliner', order: 1 }],
    tickets: [{ name: '预售票', price: 60, channel: '秀动', status: 'cancelled', url: 'https://example.com/tickets/9010' }],
    extra: {},
    tokuten_note: null,
    confidence: 0.99,
    verified: true,
    sources: [{ code: 'showstart', url: 'https://example.com/showstart/9010', fetched_at: at(-1, '18:40') }],
  },
  {
    id: 9011,
    event: { id: 811, title: '★ 地偶拼盘「夏末观测」', series_key: '夏末观测', kind: 'idol_taiban', is_idol: true, poster_thumb: null },
    city: '深圳',
    venue: { id: 21, name: 'HOU LIVE 下沙店', district: '福田区', address: '福田区 KK ONE 负一层 B112a', lng: 114.0291, lat: 22.5302 },
    open_at: at(9, '18:00'),
    start_at: at(9, '18:30'),
    date_precision: 'exact',
    status: 'on_sale',
    status_note: null,
    price: { min: 100, max: 140, currency: 'CNY', is_free: false },
    age_limit: '全年龄',
    lineup: [
      { artist_id: 341, name: '海盐汽水', role: 'headliner', order: 1 },
      { artist_id: 342, name: '月光信标', role: 'performer', order: 2 },
      { artist_id: 343, name: '白日梦游', role: 'performer', order: 3 },
      { artist_id: 344, name: '星星回收站', role: 'guest', order: 4 },
    ],
    tickets: [
      { name: '预售票', price: 100, channel: 'B站会员购', status: 'on_sale', url: 'https://example.com/tickets/9011-pre' },
      { name: '现场票', price: 140, channel: '现场', status: 'unknown', url: null },
    ],
    extra: { 特典券: '¥80', 物贩: '17:30 起' },
    tokuten_note: '特典券 ¥80/张，含对谈 60 秒 + 全员合影 1 次。',
    confidence: 0.81,
    verified: false,
    sources: [
      { code: 'weibo', url: 'https://example.com/weibo/9011', fetched_at: at(-1, '22:10') },
      { code: 'showstart', url: 'https://example.com/showstart/9011', fetched_at: at(0, '10:12') },
    ],
  },
  {
    id: 9012,
    event: { id: 812, title: '潮湿南方 · 佛山站', series_key: '潮湿南方巡演', kind: 'tour_stop', is_idol: false, poster_thumb: null },
    city: '佛山',
    venue: { id: 31, name: '岭南天地 Livehouse', district: '禅城区', address: '禅城区祖庙大街岭南天地', lng: 113.1142, lat: 23.0281 },
    open_at: at(12, '19:00'),
    start_at: at(12, '19:30'),
    date_precision: 'exact',
    status: 'announced',
    status_note: '开票时间待公布',
    price: { min: 90, max: 120, currency: 'CNY', is_free: false },
    age_limit: '全年龄',
    lineup: [{ artist_id: 71, name: '潮湿南方', role: 'headliner', order: 1 }],
    tickets: [],
    extra: {},
    tokuten_note: null,
    confidence: 0.78,
    verified: false,
    sources: [{ code: 'huodong_com', url: 'https://example.com/huodong/9012', fetched_at: at(0, '09:00') }],
  },
];

/** 按开演时间升序（无时间则按开场时间）。 */
function sortItems(items) {
  return items.slice().sort((a, b) => {
    const ka = a.start_at || a.open_at || '';
    const kb = b.start_at || b.open_at || '';
    if (ka === kb) return Number(a.id) - Number(b.id);
    return ka < kb ? -1 : 1;
  });
}

/** 场地下拉用的示例数据（示例场次出现过的 + 几处常见场地）。 */
const EXTRA_VENUES = [
  { id: 41, name: 'B10 现场', city: '深圳' },
  { id: 42, name: 'NUBOND AIR', city: '深圳' },
  { id: 43, name: '原鼓 LIVE', city: '深圳' },
  { id: 44, name: '池沼 CHIZHAO LIVEHOUSE', city: '广州' },
  { id: 45, name: '广州音乐唐人馆', city: '广州' },
];

/** 统计口径（示例数值，仅用于页脚演示）。 */
const STATS = {
  occurrences: 863,
  events: 512,
  artists: 274,
  venues: 63,
  sources: [
    { code: 'showstart', last_ok_at: at(0, '10:12'), items: 512 },
    { code: 'douban', last_ok_at: at(0, '10:40'), items: 168 },
    { code: 'weibo', last_ok_at: at(0, '08:15'), items: 96 },
    { code: 'bilibili_show', last_ok_at: at(-1, '20:02'), items: 41 },
  ],
};

/** 全部示例场次（已排序，深拷贝防止调用方误改）。 */
export function getSampleOccurrences() {
  return sortItems(RAW_ITEMS).map((it) => JSON.parse(JSON.stringify(it)));
}

/** 示例场地列表（去重）。 */
export function getSampleVenues() {
  const seen = new Map();
  for (const it of RAW_ITEMS) {
    if (it.venue && it.venue.id) seen.set(it.venue.id, { id: it.venue.id, name: it.venue.name, city: it.city });
  }
  for (const v of EXTRA_VENUES) if (!seen.has(v.id)) seen.set(v.id, { ...v });
  return Array.from(seen.values());
}

export function getSampleStats() {
  return JSON.parse(JSON.stringify(STATS));
}

/** 示例月历计数：形状与 GET /api/calendar/counts 一致。 */
export function getSampleCounts(month, city = '') {
  const days = {};
  for (const it of RAW_ITEMS) {
    if (city && it.city !== city) continue;
    const date = isoDate(it.start_at || it.open_at);
    if (!date || date.slice(0, 7) !== month) continue;
    const key = isIdolItem(it) ? 'idol' : 'band';
    if (!days[date]) days[date] = { band: 0, idol: 0, followed: 0 };
    days[date][key] += 1;
  }
  return { days };
}

/** 本地时区的 ISO 串（demo 的 generated_at 用，保证「数据更新」显示的是本机时间）。 */
function localIso(d = new Date()) {
  const p = (n) => String(n).padStart(2, '0');
  const off = -d.getTimezoneOffset();
  const sign = off >= 0 ? '+' : '-';
  const abs = Math.abs(off);
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}${sign}${p(Math.floor(abs / 60))}:${p(abs % 60)}`;
}

/** 按筛选条件过滤示例数据（用于 ?demo=1 时模拟 /api/occurrences 的行为）。
 * 支持的参数与后端契约一致：from/to/city/kind/is_idol/venue_id/status/price_max/q。
 */
export function querySampleOccurrences(filters = {}) {
  const { from, to, city, kind, is_idol, venue_id, status, price_max, q } = filters;
  const needle = String(q || '').trim().toLowerCase();
  const items = getSampleOccurrences().filter((it) => {
    const date = isoDate(it.start_at || it.open_at);
    if (from && date < from) return false;
    if (to && date > to) return false;
    if (city && it.city !== city) return false;
    if (kind && it.event.kind !== kind) return false;
    if (is_idol === 'true' && !isIdolItem(it)) return false;
    if (is_idol === 'false' && isIdolItem(it)) return false;
    if (venue_id && String((it.venue && it.venue.id) || '') !== String(venue_id)) return false;
    if (status && it.status !== status) return false;
    if (price_max && !(typeof it.price.min === 'number' && it.price.min <= Number(price_max))) return false;
    if (needle) {
      const hay = [
        it.event.title,
        it.event.series_key,
        it.city,
        it.venue ? it.venue.name : '场地待定',
        (it.lineup || []).map((a) => a.name).join(' '),
      ].join(' ').toLowerCase();
      if (!hay.includes(needle)) return false;
    }
    return true;
  });
  return { items, total: items.length, generated_at: localIso() };
}

/**
 * labels.js — 枚举 → 中文/样式映射。
 *
 * 契约约定：`status` / `kind` 等字段用英文枚举，中文展示放前端（《01-总体设计.md》§5.2）。
 * 颜色不作唯一区分手段：地偶同时用「粉色条 + ★」双标识（无障碍要求）。
 */

/** 城市枚举（设计稿 §0 覆盖范围）。空串 = 全省。 */
export const CITIES = ['广州', '深圳', '佛山', '东莞', '珠海', '中山', '惠州', '汕头'];

/**
 * 场地类型 → 中文标签。
 *
 * 「免费场地」是本项目的重点：商场中庭 / 公园 / 高校这类场地**不上售票平台**，
 * 只能靠小红书、微博发现。所以 `free` 做成独立筛选项 ——
 * 后端把它展开为 mall / park / campus 三类，口径与 ICS、CSV 导出一致。
 */
export const VENUE_TYPES = [
  { value: '', label: '全部场地' },
  { value: 'free', label: '★ 免费场地' },
  { value: 'livehouse', label: 'Livehouse' },
  { value: 'convention', label: '会展 / 漫展馆' },
  { value: 'theater', label: '剧院 / 剧场' },
  { value: 'mall', label: '商场 / 广场' },
  { value: 'park', label: '公园 / 户外' },
  { value: 'campus', label: '高校 / 文化馆' },
  { value: 'bar', label: '酒吧 / Live Bar' },
  { value: 'unknown', label: '类型未知' },
];

/** 场次状态 → 中文 + 徽标样式。dashed=true 表示虚线边框（待确认）。 */
export const STATUS = {
  announced: { label: '待开票', cls: 'ghost' },
  on_sale: { label: '售票中', cls: 'ok' },
  sold_out: { label: '售罄', cls: 'bad' },
  postponed: { label: '已改期', cls: 'warn' },
  cancelled: { label: '已取消', cls: 'bad strike' },
  finished: { label: '已结束', cls: 'ghost' },
  unknown: { label: '待确认', cls: 'warn', dashed: true },
};

export function statusInfo(status) {
  const key = String(status || 'unknown');
  return { key, ...(STATUS[key] || { label: key, cls: 'ghost' }) };
}

/** 状态下拉选项（用于工具栏筛选）。 */
export const STATUS_OPTIONS = [
  { value: '', label: '全部状态' },
  { value: 'on_sale', label: '售票中' },
  { value: 'announced', label: '待开票' },
  { value: 'sold_out', label: '售罄' },
  { value: 'postponed', label: '已改期' },
  { value: 'cancelled', label: '已取消' },
  { value: 'unknown', label: '待确认' },
  { value: 'finished', label: '已结束' },
];

/** 活动细分类型（对应 DB 的 event.kind）。 */
export const KINDS = [
  { value: '', label: '全部细分' },
  { value: 'rock_live', label: '摇滚现场' },
  { value: 'doujin_live', label: '同人 Live' },
  { value: 'oneman', label: 'One Man' },
  { value: 'taiban', label: '拼盘' },
  { value: 'idol_regular', label: '地偶定期' },
  { value: 'idol_birthday', label: '地偶生诞' },
  { value: 'idol_taiban', label: '地偶拼盘' },
  { value: 'festival', label: '音乐节' },
  { value: 'tour_stop', label: '巡演站' },
  { value: 'other', label: '其他' },
];

export function kindLabel(kind, isIdol = false) {
  const hit = KINDS.find((k) => k.value === kind && k.value);
  if (hit) return hit.label;
  return isIdol ? '地偶' : '乐队';
}

/**
 * 独立分类标记（与「类型」正交，可多选）。
 *
 * 多选语义是**「与」**：勾「女子乐队」+「ACG」= 只看同时命中的场次。
 * 这些标记可以叠加在乐队/地偶之上（一个 ACG 女子乐队三个都为真）。
 */
export const FLAG_OPTIONS = [
  {
    key: 'girl_band',
    label: '女子乐队',
    cls: 'girl',
    title: '全女子编制的乐队（女子偶像属于「地偶」，不算女子乐队）',
  },
  {
    key: 'acg',
    label: 'ACG',
    cls: 'acg',
    title: 'ACG 音乐演出：anisong / ACG 乐队 / 同人 Live',
  },
  {
    key: 'doujin_expo',
    label: '漫展',
    cls: 'expo',
    title: '二次元漫展 / 同人展（周边与本子市集；通常没有演出阵容，'
      + '所以与「ACG 音乐演出」分开标注）',
  },
];

/** 由 event 对象算出该场次命中的标记 label 列表。 */
export function eventFlags(event) {
  if (!event) return [];
  const out = [];
  if (event.is_girl_band) out.push('女子乐队');
  if (event.is_acg) out.push('ACG');
  if (event.is_doujin_expo) out.push('漫展');
  return out;
}

/** 阵容角色（顺序敏感的番位）。 */
export const ROLES = {
  headliner: '主演',
  performer: '参演',
  guest: '嘉宾',
  host: '主办',
  dj: 'DJ',
  mc: 'MC',
};

export function roleLabel(role) {
  return ROLES[role] || role || '参演';
}

/** 来源 code → 展示名（可溯源是设计原则 §1.5）。 */
export const SOURCE_LABEL = {
  showstart: '秀动',
  douban: '豆瓣同城',
  bilibili_show: 'B站会员购',
  bilibili: 'B站动态',
  weibo: '微博',
  wechat: '公众号',
  wechat_mp: '公众号',
  venue_site: '场地官网',
  weidian: '微店',
  damai: '大麦',
  huodong_com: '活动行',
  manual: '人工录入',
  community: '社区投稿',
};

export function sourceLabel(code) {
  const key = String(code || '').toLowerCase();
  return SOURCE_LABEL[key] || code || '未知来源';
}

/** 分类：地偶 / 乐队（色条、标签、圆点都靠它）。 */
export function categoryOf(item) {
  return item && item.event && item.event.is_idol ? 'idol' : 'band';
}

export function isIdolItem(item) {
  return categoryOf(item) === 'idol';
}

/** 置信度低于 0.8 视为「自动解析，可能不准」（设计稿 §4.3 / 风险登记）。 */
export function isLowConfidence(item) {
  return typeof item.confidence === 'number' && item.confidence < 0.8;
}

/** 卡片是否需要虚线边框：待确认状态或低置信度。 */
export function needsDashed(item) {
  return statusInfo(item.status).dashed === true || isLowConfidence(item);
}

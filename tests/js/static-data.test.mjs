// tests/js/static-data.test.mjs
//
// 静态模式客户端筛选的行为测试。
//
// 为什么值得单独测：GitHub Pages 版本没有后端，所有筛选都在浏览器里算。
// 这里的语义**必须与后端 `/api/occurrences` 一致**，否则同一套界面在
// 本地服务与静态站上表现不同 —— 之前已经踩过两个相关的坑：
//   * `exclude_other` 默认过滤脱口秀/展览等非演出
//   * 过期判断按「天」不按「时刻」（只有日期的活动存当天 00:00）
//
// 运行：node --test tests/js/
//
// 注意：static-data.js 引用 `location`，用 file:// 语义即可（不影响纯筛选逻辑）。

import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.location = { protocol: 'file:', hostname: '', href: 'file:///index.html' };
globalThis.window = globalThis;

const { filterOccurrences, pickCounts, localDay } =
  await import('../../app/web/js/static-data.js');

/** 造一个 occurrence，字段形状与 /api/occurrences 一致 */
function occ({
  id = 1,
  title = '某乐队专场',
  day = '2026-11-10',
  time = '20:00:00',
  city = '广州',
  kind = 'rock_live',
  is_idol = false,
  is_girl_band = false,
  is_acg = false,
  status = 'on_sale',
  price = 120,
  venue = { id: 5, name: 'SDlivehouse', city: '广州' },
  lineup = [],
} = {}) {
  return {
    id,
    start_at: `${day}T${time}+08:00`,
    city,
    status,
    price: { min: price, max: price },
    venue,
    lineup,
    sources: [{ code: 'showstart' }],
    event: { id: id * 10, title, kind, is_idol, is_girl_band, is_acg },
  };
}

/** 未来日期，避免被「过期」规则过滤掉 */
function futureDay(offsetDays = 5) {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

test('localDay 按本地时区取日期，不因 toISOString 的 UTC 偏移跑偏', () => {
  assert.equal(localDay('2026-11-10T00:00:00+08:00'), '2026-11-10');
  assert.equal(localDay('2026-11-10T20:30:00+08:00'), '2026-11-10');
  assert.equal(localDay(''), '');
});

test('默认过滤掉 kind=other（与后端 exclude_other=true 一致）', () => {
  const items = [
    occ({ id: 1, kind: 'rock_live' }),
    occ({ id: 2, kind: 'other', title: '某脱口秀' }),
  ];
  const got = filterOccurrences(items, {});
  assert.equal(got.length, 1);
  assert.equal(got[0].id, 1);
});

test('exclude_other=false 时保留非演出内容', () => {
  const items = [occ({ id: 1 }), occ({ id: 2, kind: 'other' })];
  assert.equal(filterOccurrences(items, { exclude_other: 'false' }).length, 2);
});

test('⚠️ 当天的 date_only 活动不能被当成过期隐藏（按天判断）', () => {
  const today = futureDay(0);
  const items = [occ({ id: 1, day: today, time: '00:00:00' })];
  const got = filterOccurrences(items, {});
  assert.equal(got.length, 1, '当天 00:00 的活动在当天任何时刻都应可见');
});

test('昨天的活动默认过滤掉', () => {
  const items = [occ({ id: 1, day: futureDay(-1) })];
  assert.equal(filterOccurrences(items, {}).length, 0);
});

test('include_finished=true 时保留已过期场次', () => {
  const items = [occ({ id: 1, day: futureDay(-3) })];
  assert.equal(filterOccurrences(items, { include_finished: 'true' }).length, 1);
});

test('已取消场次默认不显示', () => {
  const items = [occ({ id: 1, status: 'cancelled' })];
  assert.equal(filterOccurrences(items, {}).length, 0);
});

test('城市 / 类型 / 状态筛选', () => {
  const items = [
    occ({ id: 1, city: '广州', kind: 'rock_live', status: 'on_sale' }),
    occ({ id: 2, city: '深圳', kind: 'oneman', status: 'sold_out' }),
  ];
  assert.deepEqual(filterOccurrences(items, { city: '深圳' }).map((x) => x.id), [2]);
  assert.deepEqual(filterOccurrences(items, { kind: 'oneman' }).map((x) => x.id), [2]);
  assert.deepEqual(filterOccurrences(items, { status: 'sold_out' }).map((x) => x.id), [2]);
});

test('布尔标记筛选（is_idol / is_girl_band / is_acg）', () => {
  const items = [
    occ({ id: 1 }),
    occ({ id: 2, is_idol: true }),
    occ({ id: 3, is_girl_band: true }),
    occ({ id: 4, is_acg: true }),
  ];
  assert.deepEqual(filterOccurrences(items, { is_idol: 'true' }).map((x) => x.id), [2]);
  assert.deepEqual(filterOccurrences(items, { is_idol: 'false' }).map((x) => x.id), [1, 3, 4]);
  assert.deepEqual(filterOccurrences(items, { is_girl_band: 'true' }).map((x) => x.id), [3]);
  assert.deepEqual(filterOccurrences(items, { is_acg: 'true' }).map((x) => x.id), [4]);
});

test('⚠️ flag 多值语义是「与」不是「或」', () => {
  const items = [
    occ({ id: 1 }),
    occ({ id: 2, is_girl_band: true }),
    occ({ id: 3, is_acg: true }),
    occ({ id: 4, is_girl_band: true, is_acg: true }),
  ];
  const got = filterOccurrences(items, { flag: ['女子乐队', 'acg'] });
  assert.deepEqual(got.map((x) => x.id), [4], '「与」：必须同时命中两个标记');
  assert.deepEqual(
    filterOccurrences(items, { flag: ['女子乐队'] }).map((x) => x.id), [2, 4],
  );
  // 英文别名也要认
  assert.deepEqual(filterOccurrences(items, { flag: ['girl_band'] }).map((x) => x.id), [2, 4]);
});

test('价格上限筛选：价格未知的场次不被排除（宁多勿漏）', () => {
  const items = [
    occ({ id: 1, price: 80 }),
    occ({ id: 2, price: 200 }),
    occ({ id: 3, price: null }),
  ];
  assert.deepEqual(filterOccurrences(items, { price_max: '100' }).map((x) => x.id), [1, 3]);
});

test('场地与艺人筛选', () => {
  const items = [
    occ({ id: 1, venue: { id: 5, name: 'A' }, lineup: [{ id: 1, name: '乐队甲' }] }),
    occ({ id: 2, venue: { id: 9, name: 'B' }, lineup: [{ id: 2, name: '乐队乙' }] }),
  ];
  assert.deepEqual(filterOccurrences(items, { venue_id: '9' }).map((x) => x.id), [2]);
  assert.deepEqual(filterOccurrences(items, { artist_id: '1' }).map((x) => x.id), [1]);
});

test('关键词搜索覆盖标题 / 场地 / 阵容', () => {
  const items = [
    occ({ id: 1, title: '民谣之夜', venue: { id: 1, name: '某某酒馆' }, lineup: [] }),
    occ({ id: 2, title: '摇滚拼盘', venue: { id: 2, name: 'SD' }, lineup: [{ id: 3, name: '碎梦飞跃' }] }),
  ];
  assert.deepEqual(filterOccurrences(items, { q: '民谣' }).map((x) => x.id), [1]);
  assert.deepEqual(filterOccurrences(items, { q: 'sd' }).map((x) => x.id), [2]);
  assert.deepEqual(filterOccurrences(items, { q: '碎梦' }).map((x) => x.id), [2]);
});

test('日期区间按天闭区间', () => {
  const items = [
    occ({ id: 1, day: '2026-11-09' }),
    occ({ id: 2, day: '2026-11-10' }),
    occ({ id: 3, day: '2026-11-11' }),
  ];
  const got = filterOccurrences(items, {
    from: '2026-11-10', to: '2026-11-11', include_finished: 'true',
  });
  assert.deepEqual(got.map((x) => x.id), [2, 3]);
});

test('结果按开演时间升序', () => {
  const items = [
    occ({ id: 1, day: futureDay(3) }),
    occ({ id: 2, day: futureDay(1) }),
    occ({ id: 3, day: futureDay(2) }),
  ];
  assert.deepEqual(filterOccurrences(items, {}).map((x) => x.id), [2, 3, 1]);
});

test('pickCounts 按城市取计数，缺城市时回退全省', () => {
  const file = {
    month: '2026-11',
    by_city: {
      '': { '2026-11-10': { band: 3, idol: 1, girl_band: 1, acg: 2 } },
      广州: { '2026-11-10': { band: 2, idol: 0, girl_band: 0, acg: 1 } },
    },
  };
  assert.equal(pickCounts(file, '广州')['2026-11-10'].band, 2);
  assert.equal(pickCounts(file, '')['2026-11-10'].band, 3);
  assert.equal(pickCounts(file, '不存在的城市')['2026-11-10'].band, 3);
  assert.deepEqual(pickCounts(null, '广州'), {});
});

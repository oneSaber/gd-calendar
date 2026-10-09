/**
 * 日期范围与艺人搜索的状态逻辑测试（纯函数，不需要浏览器）。
 *
 * 对应目标要求：「支持日期选择」与「艺人搜索」。
 */

import test from 'node:test';
import assert from 'node:assert/strict';

import { readState, toQuery, withRange, withPreset } from '../../app/web/js/state.js';

test('readState 读取 artist 参数', () => {
  const s = readState('?from=2026-10-01&to=2026-10-31&artist=恋音契约');
  assert.equal(s.artist, '恋音契约');
});

test('readState 缺 artist 时为空串（不是 undefined）', () => {
  const s = readState('?from=2026-10-01&to=2026-10-31');
  assert.equal(s.artist, '');
});

test('toQuery 写回 artist', () => {
  const s = readState('?from=2026-10-01&to=2026-10-31&artist=DigitalDuel');
  const q = toQuery(s);
  assert.ok(q.includes('artist=DigitalDuel'), q);
});

test('artist 与 q 互相独立', () => {
  const s = readState('?from=2026-10-01&to=2026-10-31&q=乐队&artist=恋音');
  assert.equal(s.q, '乐队');
  assert.equal(s.artist, '恋音');
  const q = toQuery(s);
  assert.ok(q.includes('q=') && q.includes('artist='));
});

test('withRange 设置日期范围并把 preset 归零', () => {
  const s = readState('?from=2026-10-01&to=2026-10-07&range=week');
  const next = withRange(s, '2026-11-01', '2026-11-30');
  assert.equal(next.from, '2026-11-01');
  assert.equal(next.to, '2026-11-30');
  assert.equal(next.preset, 'custom');
});

test('withRange 只改一端时另一端保留', () => {
  const s = readState('?from=2026-10-01&to=2026-10-31');
  const next = withRange(s, '', '2026-12-31');
  assert.equal(next.from, '2026-10-01', '起日应保留');
  assert.equal(next.to, '2026-12-31');
});

test('withRange 结束早于开始时以刚改的那端为准', () => {
  const s = readState('?from=2026-10-01&to=2026-10-31');
  // 用户把「到」改成 9/01（早于 from）→ 应把 from 拉到 9/01，而不是留非法区间
  const next = withRange(s, '2026-10-01', '2026-09-01');
  assert.ok(next.from <= next.to, `区间非法：${next.from} ~ ${next.to}`);
  assert.equal(next.to, '2026-09-01');
});

test('withRange 清掉范围外的选中日', () => {
  const s = { ...readState('?from=2026-10-01&to=2026-10-31'), d: '2026-10-15' };
  const next = withRange(s, '2026-11-01', '2026-11-30');
  assert.equal(next.d, '', '选中日移出范围应清空');
});

test('withRange 保留范围外的选中日之外的状态', () => {
  const s = { ...readState('?from=2026-10-01&to=2026-10-31&city=深圳'), artist: 'Yours' };
  const next = withRange(s, '2026-11-01', '2026-11-30');
  assert.equal(next.city, '深圳');
  assert.equal(next.artist, 'Yours');
});

test('withPreset 会清掉范围外的选中日', () => {
  const s = { ...readState('?from=2026-10-01&to=2026-10-31&range=month'), d: '2026-10-31' };
  const next = withPreset(s, 'week', '2026-12-01');
  assert.ok(next.d === '' || (next.d >= next.from && next.d <= next.to));
});

test('非法日期串被忽略（不写入状态）', () => {
  const next = withRange(readState('?from=2026-10-01&to=2026-10-31'), 'not-a-date', '2026-11-30');
  assert.equal(next.from, '2026-10-01', '非法起日应回退到原值');
});

test('往返：toQuery -> readState 稳定（artist 不丢）', () => {
  const s1 = readState('?from=2026-10-01&to=2026-10-31&artist=月匙Moon-Key&flags=acg');
  const s2 = readState(`?${toQuery(s1)}`);
  assert.equal(s2.artist, s1.artist);
  assert.deepEqual(s2.flags, s1.flags);
});

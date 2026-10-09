/**
 * artists.js — 艺人 / 团体搜索（带自动补全）。
 *
 * ## 为什么要单独做成组件
 *
 * 目标要求「支持艺人搜索」。它与顶栏的关键词框（`#q`）**不是一回事**：
 *   * `#q` 只搜**标题**；
 *   * 艺人搜索搜**演出阵容**（`occurrence_artist`）。
 * 实测差别很大 —— 「恋音契约」标题只匹配 1 场，阵容匹配 2 场。
 * 所以两者独立存在，结果可以叠加。
 *
 * ## 数据来源
 *
 * `/api/artists?with_upcoming=1` —— **只返回有未来场次的艺人**并按场次排序。
 * 不这么做的话，下拉里会出现库里 349 个艺人中的大部分，
 * 而其中很多没有 upcoming，点进去是空的。
 *
 * 静态站没有后端，所以补全功能只在本地可用；静态模式下退化为
 * 「按名字筛已渲染的场次」（`artist` 参数走本地过滤）。
 */

import * as api from './api.js';

const MAX_SUGGEST = 8;
/** 输入防抖：避免每敲一个字都打一次接口 */
const DEBOUNCE_MS = 180;

/** 名字 → 高亮匹配片段（返回 HTML 片段，调用方负责转义其余部分）。 */
function highlight(name, q) {
  const s = String(name || '');
  const needle = String(q || '').trim();
  if (!needle) return escapeHtml(s);
  const i = s.toLowerCase().indexOf(needle.toLowerCase());
  if (i < 0) return escapeHtml(s);
  return (
    escapeHtml(s.slice(0, i)) +
    '<mark>' + escapeHtml(s.slice(i, i + needle.length)) + '</mark>' +
    escapeHtml(s.slice(i + needle.length))
  );
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

export function createArtistPicker({ input, list, isStatic, onChange }) {
  let items = [];
  let activeIdx = -1;
  let timer = null;

  function close() {
    list.hidden = true;
    list.innerHTML = '';
    input.setAttribute('aria-expanded', 'false');
    activeIdx = -1;
  }

  function render(q) {
    if (!items.length) {
      // 明确告诉用户「没找到」而不是静默空着
      list.innerHTML = '<li class="ap-empty" role="option" aria-disabled="true">没有匹配的艺人</li>';
      list.hidden = false;
      input.setAttribute('aria-expanded', 'true');
      return;
    }
    list.innerHTML = items
      .map((a, i) => {
        const kind = a.kind === 'idol_group' ? '地偶'
          : a.kind === 'girl_band' ? '女子乐队'
          : a.kind === 'band' ? '乐队'
          : a.kind === 'idol_member' ? '成员' : '';
        const meta = [kind, a.upcoming ? `${a.upcoming} 场` : '']
          .filter(Boolean).join(' · ');
        return `<li role="option" data-artist="${escapeHtml(a.name)}" `
          + `aria-selected="${i === activeIdx ? 'true' : 'false'}">`
          + `<span class="ap-name">${highlight(a.name, q)}</span>`
          + (meta ? `<span class="ap-meta">${escapeHtml(meta)}</span>` : '')
          + '</li>';
      })
      .join('');
    list.hidden = false;
    input.setAttribute('aria-expanded', 'true');
  }

  async function search(q) {
    const kw = String(q || '').trim();
    if (kw.length < 1) { close(); return; }
    try {
      // 静态站没有后端：不请求，交给上层做本地过滤
      if (isStatic && isStatic()) {
        items = [];
        close();
        return;
      }
      const res = await api.loadArtists({ q: kw, withUpcoming: true, limit: 12 });
      items = (res && res.items) || [];
      activeIdx = items.length ? 0 : -1;
      render(kw);
    } catch {
      close();
    }
  }

  function commit(name) {
    close();
    input.value = name;
    onChange(name);
  }

  input.addEventListener('input', () => {
    clearTimeout(timer);
    const v = input.value;
    // 输入即改筛选（防抖），但下拉用同样的词查
    timer = setTimeout(() => {
      onChange(v.trim());
      search(v);
    }, DEBOUNCE_MS);
  });

  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (list.hidden || !items.length) return;
      e.preventDefault();
      const delta = e.key === 'ArrowDown' ? 1 : -1;
      activeIdx = (activeIdx + delta + items.length) % items.length;
      render(input.value);
      return;
    }
    if (e.key === 'Enter') {
      if (!list.hidden && activeIdx >= 0 && items[activeIdx]) {
        e.preventDefault();
        commit(items[activeIdx].name);
      }
      return;
    }
    if (e.key === 'Escape') { close(); input.blur(); }
  });

  list.addEventListener('mousedown', (e) => {
    // mousedown 而不是 click：input 的 blur 会先于 click 触发，导致选不中
    const li = e.target instanceof Element ? e.target.closest('[data-artist]') : null;
    if (!li) return;
    e.preventDefault();
    commit(li.dataset.artist);
  });

  input.addEventListener('blur', () => setTimeout(close, 120));
  input.addEventListener('focus', () => {
    if (String(input.value || '').trim()) search(input.value);
  });

  return {
    /** 外部状态变化时同步显示值（不触发 onChange）。 */
    setValue(v) {
      if (document.activeElement !== input) input.value = v || '';
    },
    close,
  };
}

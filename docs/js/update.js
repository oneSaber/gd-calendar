/**
 * 「更新数据」按钮：启动后台采集 → 轮询进度 → 完成后自动刷新列表。
 *
 * 为什么是「启动 + 轮询」而不是一个长请求：
 * 一轮采集要 1–3 分钟，同步 HTTP 会超时，而且没有进度可显示。
 * 后端把它做成进程内任务（app/jobs/update_job.py），这里负责展示与恢复状态。
 */

import * as api from './api.js';

const POLL_MS = 1500;
const MAX_POLL_ERRORS = 5;

let pollTimer = null;
let pollErrors = 0;
let currentJobId = '';
let bound = false;
let onFinished = null;

function el(id) {
  return document.getElementById(id);
}

function setBusy(busy, label) {
  const btn = el('update-btn');
  if (!btn) return;
  btn.disabled = busy;
  btn.classList.toggle('busy', busy);
  const span = el('update-label');
  if (span) span.textContent = label || (busy ? '更新中' : '更新数据');
}

function showBar(show) {
  const bar = el('update-bar');
  if (bar) bar.hidden = !show;
}

function render(snap) {
  const fill = el('update-fill');
  const bar = el('update-bar');
  const wrap = bar && bar.querySelector('.up-progress');
  const msg = el('update-msg');
  const pct = Math.max(0, Math.min(100, Number(snap.percent || 0)));
  if (fill) fill.style.width = `${pct}%`;
  if (wrap) wrap.setAttribute('aria-valuenow', String(pct));
  // 进度长时间不动时（单个来源要跑 1–2 分钟）切到「未定进度」动画条，
  // 否则 0% 静止不动看起来像卡死。
  if (bar) {
    const stuck = snap.running && pct === 0;
    bar.classList.toggle('indeterminate', Boolean(stuck));
  }
  if (msg) {
    const parts = [snap.message || '准备中…'];
    if (snap.state === 'running' && snap.total) {
      parts.push(`（${snap.index}/${snap.total}）`);
    }
    // 已用时长：给长任务一个「还在动」的证据
    if (snap.running && snap.elapsed > 3) parts.push(`已用 ${Math.round(snap.elapsed)} 秒`);
    msg.textContent = parts.join(' ');
    msg.classList.toggle('err', snap.state === 'error');
    msg.classList.toggle('ok', snap.state === 'done');
  }
  const logs = el('update-logs');
  const toggle = el('update-logs-toggle');
  if (logs && Array.isArray(snap.logs) && snap.logs.length) {
    logs.textContent = snap.logs.join('\n');
    if (toggle) toggle.hidden = false;
  }
}

function stopPolling() {
  if (pollTimer) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
}

async function poll(jobId) {
  try {
    const snap = await api.loadUpdateStatus(jobId);
    pollErrors = 0;
    render(snap);

    if (snap.running) {
      setBusy(true, `${snap.percent || 0}%`);
      pollTimer = setTimeout(() => poll(jobId), POLL_MS);
      return;
    }

    // 结束（完成 / 失败 / 取消）
    setBusy(false);
    currentJobId = '';
    if (snap.state === 'done') {
      const created = (snap.result && snap.result.created) || 0;
      const updated = (snap.result && snap.result.updated) || 0;
      setBusy(false, '更新数据');
      if (onFinished) onFinished(snap);
      // 完成提示保留一会儿再收起，让用户看到数字
      setTimeout(() => showBar(false), 12000);
      const msg = el('update-msg');
      if (msg) msg.textContent = `✅ ${snap.message}（新增 ${created} / 更新 ${updated}）`;
    } else if (snap.state === 'error') {
      const msg = el('update-msg');
      if (msg) msg.textContent = `❌ ${snap.message || '更新失败'}`;
      if (onFinished) onFinished(snap);
    } else {
      showBar(false);
    }
  } catch (err) {
    pollErrors += 1;
    if (pollErrors >= MAX_POLL_ERRORS) {
      setBusy(false);
      showBar(false);
      currentJobId = '';
      // 错误只在控制台与提示条说明，不弹窗打断用户
      const msg = el('update-msg');
      if (msg) msg.textContent = `轮询中断：${err.message}`;
      return;
    }
    pollTimer = setTimeout(() => poll(jobId), POLL_MS * 2);
  }
}

export async function startUpdate() {
  if (el('update-btn') && el('update-btn').disabled) return;
  setBusy(true, '启动中');
  showBar(true);
  const msg = el('update-msg');
  if (msg) {
    msg.textContent = '正在启动采集…';
    msg.classList.remove('err', 'ok');
  }
  const logs = el('update-logs');
  if (logs) logs.textContent = '';
  const toggle = el('update-logs-toggle');
  if (toggle) toggle.hidden = true;
  pollErrors = 0;

  try {
    const job = await api.startUpdate();
    currentJobId = job.job_id;
    render(job);
    poll(job.job_id);
  } catch (err) {
    setBusy(false);
    if (msg) {
      msg.textContent = `❌ ${err.message}`;
      msg.classList.add('err');
    }
  }
}

/** 页面加载时恢复状态：刷新到一半时按钮应继续显示进度。 */
export async function restore() {
  try {
    const data = await api.loadUpdateStatus();
    const snap = data.running ? data.current : null;
    if (snap && snap.running) {
      currentJobId = snap.job_id;
      showBar(true);
      render(snap);
      setBusy(true, `${snap.percent || 0}%`);
      poll(snap.job_id);
    }
  } catch (err) {
    // 恢复失败不影响使用：按钮保持「更新数据」可点状态
  }
}

export function bindUpdateButton({ onDone } = {}) {
  if (bound) return;
  bound = true;
  onFinished = onDone || null;

  const btn = el('update-btn');
  if (btn) btn.addEventListener('click', startUpdate);

  const toggle = el('update-logs-toggle');
  const logs = el('update-logs');
  if (toggle && logs) {
    toggle.addEventListener('click', () => {
      logs.hidden = !logs.hidden;
      toggle.textContent = logs.hidden ? '查看详情' : '收起详情';
    });
  }

  restore();
}

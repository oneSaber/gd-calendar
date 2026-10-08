/**
 * static-data.js — 静态模式（GitHub Pages）下的客户端查询。
 *
 * 目标：**与后端 `/api/occurrences` 的语义保持一致**，这样同一套前端代码
 * 在本地服务与静态站点上表现相同。已经踩过的坑都在这条链路上复刻一遍：
 *
 *   * `exclude_other` 默认 true（过滤脱口秀/展览等非演出）
 *   * 过期判断按「天」而不是「时刻」—— 只有日期的活动存当天 00:00，
 *     若按时刻过滤，当天下午它就会消失
 *   * `flag` 多值语义是**「与」**
 *
 * 做不到的（静态站固有限制）：分页只支持「取前 N 条」，没有真分页；
 * 但前端本来就是一次取 200 条再自己渲染，够用。
 */

/** 取本地日期串（YYYY-MM-DD），避免 toISOString 的 UTC 偏移 */
export function localDay(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso).slice(0, 10);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function normBool(v) {
  return v === true || v === 'true' || v === '1' || v === 1;
}

/**
 * 客户端筛选，参数与后端查询参数同名同义。
 * @param {Array} items data/occurrences.json 的 items
 * @param {Object} p 查询参数
 */
export function filterOccurrences(items, p = {}) {
  const today = localDay(new Date().toISOString());
  const excludeOther = p.exclude_other === undefined ? true : normBool(p.exclude_other);
  const includeFinished = normBool(p.include_finished);

  // 分类标记：多值为「与」
  const flags = []
    .concat(p.flag || [])
    .map((f) => String(f).trim().toLowerCase())
    .filter(Boolean);

  const city = p.city || '';
  const kind = p.kind || '';
  const status = p.status || '';
  const q = (p.q || '').trim().toLowerCase();
  const priceMax = p.price_max === undefined || p.price_max === '' ? null : Number(p.price_max);
  const venueId = p.venue_id ? String(p.venue_id) : '';
  const artistId = p.artist_id ? String(p.artist_id) : '';
  const isIdol = p.is_idol === undefined || p.is_idol === '' ? null : normBool(p.is_idol);
  const isGirl = p.is_girl_band === undefined || p.is_girl_band === '' ? null : normBool(p.is_girl_band);
  const isAcg = p.is_acg === undefined || p.is_acg === '' ? null : normBool(p.is_acg);

  const from = p.from || '';
  const to = p.to || '';

  const out = items.filter((it) => {
    const ev = it.event || {};
    const day = localDay(it.start_at);

    // 日期区间
    if (from && day < from) return false;
    if (to && day > to) return false;

    // ⚠️ 过期按「天」判断：只有日期的活动存当天 00:00，
    // 按时刻过滤会让它在当天下午就消失（本地服务端已修过同样的 bug）
    if (!includeFinished) {
      if (day && day < today) return false;
      if (it.status === 'cancelled') return false;
    }

    if (excludeOther && ev.kind === 'other') return false;
    if (city && it.city !== city) return false;
    if (kind && ev.kind !== kind) return false;
    if (status && it.status !== status) return false;
    if (isIdol !== null && Boolean(ev.is_idol) !== isIdol) return false;
    if (isGirl !== null && Boolean(ev.is_girl_band) !== isGirl) return false;
    if (isAcg !== null && Boolean(ev.is_acg) !== isAcg) return false;
    if (venueId && String((it.venue || {}).id || '') !== venueId) return false;
    if (artistId && !(it.lineup || []).some((a) => String(a.id || '') === artistId)) return false;

    if (priceMax !== null && !Number.isNaN(priceMax)) {
      const pmin = it.price && it.price.min;
      // 价格未知的场次不因价格筛选被排除（与后端一致：宁多勿漏）
      if (pmin !== null && pmin !== undefined && Number(pmin) > priceMax) return false;
    }

    // 分类标记「与」
    for (const f of flags) {
      const need =
        f === '女子乐队' || f === 'girl_band' ? ev.is_girl_band
          : f === '地偶' || f === 'idol' ? ev.is_idol
            : f === 'acg' ? ev.is_acg : true;
      if (!need) return false;
    }

    if (q) {
      const hay = [
        ev.title, it.venue && it.venue.name, it.city,
        (it.lineup || []).map((a) => a.name).join(' '),
        (it.sources || []).map((s) => s.code).join(' '),
      ].filter(Boolean).join(' ').toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  out.sort((a, b) => String(a.start_at).localeCompare(String(b.start_at)));
  return out;
}

/** 月历计数：直接把构建时算好的结果返回，前端零计算。 */
export function pickCounts(monthFile, city) {
  if (!monthFile) return {};
  const byCity = monthFile.by_city || {};
  return byCity[city || ''] || byCity[''] || {};
}

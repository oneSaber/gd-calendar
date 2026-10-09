"""探测：微博 m 站主页 HTML 里是否有博文数据（$render_data）。

如果 SSR 带数据，就能**纯 HTTP** 按账号采集，不用浏览器、不用登录。
"""

from __future__ import annotations

import json
import re

import httpx

UID = "5861861144"  # Garry_Liu_
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


def main() -> None:
    url = f"https://m.weibo.cn/u/{UID}"
    headers = {"User-Agent": MOBILE_UA, "Referer": "https://m.weibo.cn/"}
    with httpx.Client(timeout=25, headers=headers, follow_redirects=True) as c:
        r = c.get(url)
    html = r.text
    print(f"  HTTP {r.status_code}  {len(html)} 字节")

    # 1) 是否有 $render_data
    m = re.search(r"var \$render_data\s*=\s*(\[.*?\])\s*;?\s*</script>", html, re.S)
    if not m:
        m = re.search(r"\$render_data\s*=\s*(\[.*?\])\s*;", html, re.S)
    print(f"  找到 $render_data: {bool(m)}")
    if m:
        try:
            data = json.loads(m.group(1))
            print(f"  顶层元素: {len(data)}")
            for i, blk in enumerate(data[:3]):
                if not isinstance(blk, dict):
                    continue
                cards = blk.get("cards") or []
                print(f"    [{i}] keys={list(blk.keys())[:8]}  cards={len(cards)}")
                for card in cards[:4]:
                    if not isinstance(card, dict):
                        continue
                    typ = card.get("card_type")
                    mb = card.get("mblog")
                    title = card.get("title") if not mb else None
                    if mb:
                        txt = re.sub(r"<[^>]+>", "", mb.get("text") or "")
                        print(f"        card={typ} mblog: {txt[:90]}")
                    else:
                        print(f"        card={typ} title={str(title)[:60]}")
        except Exception as exc:  # noqa: BLE001
            print(f"  JSON 解析失败: {exc}")
            print(f"  片段: {m.group(1)[:200]}")

    # 2) 兜底：看有没有关键痕迹
    for kw in ("render_data", "login", "登录", "card_type", "mblog"):
        print(f"  含「{kw}」: {html.count(kw)}")


main()

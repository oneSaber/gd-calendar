"""探测：微博账号主页/接口是否能读到内容（决定能否「按账号采集」）。

背景：`app/collectors/weibo.py` 注释说「个人主页强制跳登录，纯 HTTP 打
/api/container 返回 432」。现在重测，因为有两条新路：
  1. 直接 HTTP 打 m 站接口（无需浏览器）
  2. 用**持久 profile 的登录态**（XHS 那套）渲染

可读 → 就能做「按账号采集」，拿到图鉴作者 Garry_Liu_ 的完整排期贴。
"""

from __future__ import annotations

import json

import httpx

UID = "5861861144"  # Garry_Liu_（广州地偶图鉴作者）

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

URLS = [
    f"https://m.weibo.cn/api/container/getIndex?type=uid&value={UID}",
    f"https://m.weibo.cn/api/container/getIndex?type=uid&value={UID}"
    "&containerid=107603" + UID,
    f"https://m.weibo.cn/u/{UID}",
]


def main() -> None:
    headers = {
        "User-Agent": MOBILE_UA,
        "Accept": "application/json, text/plain, */*",
        "Referer": f"https://m.weibo.cn/u/{UID}",
        "X-Requested-With": "XMLHttpRequest",
        "MWeibo-Pwa": "1",
    }
    for u in URLS:
        try:
            with httpx.Client(timeout=25, headers=headers, follow_redirects=True) as c:
                r = c.get(u)
            body = r.text or ""
            info = f"HTTP {r.status_code}  {len(body):7} 字节"
            # 尝试解析 JSON 里的 card 数
            extra = ""
            if body.lstrip().startswith("{"):
                try:
                    j = json.loads(body)
                    extra = f" ok={j.get('ok')} cards={len(((j.get('data') or {}).get('cards')) or [])}"
                except Exception:  # noqa: BLE001
                    extra = " (JSON 解析失败)"
            print(f"  {info}{extra}")
            print(f"    {u[:100]}")
            print(f"    开头: {body[:150].replace(chr(10), ' ')}")
            print()
        except Exception as exc:  # noqa: BLE001
            print(f"  ERR {u[:100]}")
            print(f"    {str(exc)[:80]}")
            print()


main()

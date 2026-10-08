"""探测 B站会员购票务 API：支持哪些参数、分页有多深。

目的：把 B站做成主力数据源，需要知道它能给多少数据、能不能按关键词搜。
合规：只调用公开列表接口，不逆向 wbi 签名、不绕登录。
"""

from __future__ import annotations

import json

import httpx

BASE = "https://show.bilibili.com/api/ticket/project/listV2"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://show.bilibili.com/platform/home.html",
    "Origin": "https://show.bilibili.com",
}


def probe(label: str, params: dict) -> dict | None:
    try:
        with httpx.Client(timeout=25, headers=HEADERS) as c:
            r = c.get(BASE, params=params)
        if r.status_code != 200:
            print(f"  {label:34} HTTP {r.status_code}")
            return None
        j = r.json()
        if not j.get("success"):
            print(f"  {label:34} success=False msg={str(j.get('message'))[:40]}")
            return None
        data = j.get("data") or {}
        res = data.get("result") or []
        total = data.get("total") or data.get("totalCount")
        print(f"  {label:34} 返回 {len(res):3} 条  total={total}")
        return j
    except Exception as e:  # noqa: BLE001
        print(f"  {label:34} 失败: {str(e)[:60]}")
        return None


def base_params(**over) -> dict:
    p = {
        "version": 134, "page": 1, "pagesize": 20, "area": "440100",
        "platform": "web", "p_type": "", "project_id": "", "season_id": "",
        "sort_type": "", "tab_id": "", "keyword": "", "category": "",
    }
    p.update(over)
    return p


print("=== 1) 基础列表：广州（area=440100）分页深度 ===")
for size in (20, 50, 100):
    probe(f"pagesize={size} page=1", base_params(pagesize=size))
for page in (2, 3, 5, 10):
    probe(f"pagesize=50 page={page}", base_params(pagesize=50, page=page))

print()
print("=== 2) 关键词搜索是否支持 ===")
for kw in ("地偶", "偶像", "ACG", "同人", "Only", "乐队", "二次元"):
    probe(f"keyword={kw}", base_params(keyword=kw, pagesize=20))

print()
print("=== 3) 广东各市 area 码 ===")
for city, code in (("广州", "440100"), ("深圳", "440300"), ("珠海", "440400"),
                   ("佛山", "440600"), ("东莞", "441900"), ("中山", "442000"),
                   ("惠州", "441300"), ("汕头", "440500")):
    probe(f"{city} area={code}", base_params(area=code, pagesize=20))

print()
print("=== 4) 看一条完整字段，确认有没有演出人员/分类等可用信息 ===")
j = probe("广州 详情样本", base_params(pagesize=3))
if j:
    for it in (j.get("data") or {}).get("result", [])[:2]:
        print("  ---")
        for k in sorted(it.keys()):
            v = str(it[k])
            print(f"    {k:26} {v[:70]}")

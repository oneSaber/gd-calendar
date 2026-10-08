"""探测：B站会员购的城市选择器完整列表 + 搜索框输入行为。

目的：确认能否覆盖广东 8 城（当前只映射了 5 城），以及搜索是否可用于垂类发现。
"""

import json
import sys
import time

sys.path.insert(0, r"D:\AI\gd-calendar\scripts")
from cdp_fetch import Browser  # noqa: E402

SHOW_HOME = "https://show.bilibili.com/platform/home.html?msource=pc_web"

CLICK_CITY_SELECTOR = r"""
(function(){
  var nodes = [].slice.call(document.querySelectorAll('*'));
  var n = nodes.find(function(e){
    return (e.innerText || '').trim() === '城市:' && e.children.length <= 2;
  });
  if(!n) return 'no-city-label';
  var clickable = n.closest('div,a,li') || n;
  clickable.click();
  return 'clicked:' + clickable.tagName;
})()
"""

READ_CITY_PANEL = r"""
(function(){
  var txt = document.body.innerText;
  var nodes = [].slice.call(document.querySelectorAll('a,li,span,div,button'));
  function uniq(a){ var s={},o=[]; a.forEach(function(x){ if(!s[x]){s[x]=1;o.push(x);} }); return o; }
  var words = uniq(nodes.map(function(e){ return (e.innerText||'').trim(); })
                        .filter(function(t){ return t && t.length<=8; }));
  return JSON.stringify({
    cities: words.filter(function(t){
      return /^(全国|北京|上海|广州|深圳|杭州|武汉|天津|成都|南京|重庆|西安|长沙|郑州|苏州|青岛|厦门|福州|昆明|合肥|济南|大连|沈阳|哈尔滨|长春|石家庄|太原|南昌|贵阳|南宁|兰州|银川|西宁|乌鲁木齐|呼和浩特|海口|三亚|中山|惠州|汕头|佛山|东莞|珠海|江门|湛江)$/.test(t);
    }),
    tail: txt.replace(/\n+/g,' | ').slice(0, 600)
  });
})()
"""


def main() -> None:
    br = Browser(headless=True, width=1500, height=1700)
    try:
        br.cmd("Network.enable")
        br.cmd("Runtime.enable")
        br.render(SHOW_HOME, wait=10)
        time.sleep(4)

        print("  点城市选择器:",
              br.cmd("Runtime.evaluate", {"expression": CLICK_CITY_SELECTOR,
                                          "returnByValue": True}).get("result", {}).get("value"))
        time.sleep(3)
        d = json.loads(br.cmd("Runtime.evaluate", {"expression": READ_CITY_PANEL,
                                                   "returnByValue": True})["result"]["value"])
        print("  可选城市:", d["cities"])
        print("  面板文本:", d["tail"][:400])
    finally:
        br.close()


main()

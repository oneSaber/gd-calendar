"""探测 B站会员购搜索的可用触发方式（换几种交互再试）。"""

import json
import sys
import time

sys.path.insert(0, r"D:\AI\gd-calendar\scripts")
from cdp_fetch import Browser  # noqa: E402

SET_AND_ENTER = r"""
(function(){
  var i = [].slice.call(document.querySelectorAll('input'))
    .find(function(x){ return /搜/.test(x.placeholder || ''); });
  if(!i) return 'no-input';
  var setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, 'value').set;
  i.focus(); setter.call(i, '地偶');
  i.dispatchEvent(new Event('input', {bubbles: true}));
  i.dispatchEvent(new Event('change', {bubbles: true}));
  return 'typed';
})()
"""

FIND_BUTTONS = r"""
(function(){
  var out = [];
  [].slice.call(document.querySelectorAll('button,a,i,span,div')).forEach(function(e){
    var t = (e.innerText || '').trim();
    var c = String(e.className || '');
    if(/搜|search/i.test(c) || t === '搜索'){
      out.push({tag: e.tagName, cls: c.slice(0, 50), text: t.slice(0, 20)});
    }
  });
  return JSON.stringify(out.slice(0, 10));
})()
"""

CLICK_SEARCH = r"""
(function(){
  var nodes = [].slice.call(document.querySelectorAll('button,a,i,span,div'));
  var n = nodes.find(function(e){
    var c = String(e.className || '');
    return /search/i.test(c) && e.tagName !== 'INPUT';
  });
  if(!n) return 'no-search-btn';
  n.click();
  return 'clicked:' + n.tagName + '.' + String(n.className).slice(0, 30);
})()
"""

READ = r"""
(function(){
  return JSON.stringify({
    url: location.href,
    cards: document.querySelectorAll('[class*=card],[class*=item]').length,
    text: document.body.innerText.replace(/\n+/g, ' | ').slice(0, 380)
  });
})()
"""


def main() -> None:
    br = Browser(headless=True, width=1440, height=1600)
    try:
        br.cmd("Network.enable")
        br.cmd("Runtime.enable")
        br.render("https://show.bilibili.com/platform/home.html?msource=pc_web", wait=10)
        time.sleep(4)

        print("  搜索结果按钮候选:",
              br.cmd("Runtime.evaluate", {"expression": FIND_BUTTONS,
                                          "returnByValue": True})["result"]["value"][:260])
        print("  输入:",
              br.cmd("Runtime.evaluate", {"expression": SET_AND_ENTER,
                                          "returnByValue": True})["result"]["value"])
        time.sleep(2.5)
        print("  点搜索按钮:",
              br.cmd("Runtime.evaluate", {"expression": CLICK_SEARCH,
                                          "returnByValue": True})["result"]["value"])
        time.sleep(6)
        d = json.loads(br.cmd("Runtime.evaluate", {"expression": READ,
                                                   "returnByValue": True})["result"]["value"])
        print("  结果 URL:", d["url"][:130])
        print("  结果卡片数:", d["cards"])
        print("  结果内容:", d["text"][:300])
    finally:
        br.close()


main()

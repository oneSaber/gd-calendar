"""探测：B站会员购「更多城市」里有哪些城市可点，以及分类筛选的选项。

目的：扩展 area 覆盖（当前只有 5 城）与利用「only同人展」分类。
"""

import json
import sys
import time

sys.path.insert(0, r"D:\AI\gd-calendar\scripts")
from cdp_fetch import Browser  # noqa: E402

READ = r"""
(function(){
  var txt = document.body.innerText;
  var nodes = [].slice.call(document.querySelectorAll('a,li,span,div,button'));
  function uniq(a){ var s={}, o=[]; a.forEach(function(x){ if(!s[x]){s[x]=1;o.push(x);} }); return o; }
  var words = uniq(nodes.map(function(e){ return (e.innerText || '').trim(); })
                        .filter(function(t){ return t && t.length <= 10; }));
  return JSON.stringify({
    text: txt.replace(/\n+/g, ' | ').slice(0, 700),
    cityWords: words.filter(function(t){
      return /广州|深圳|珠海|佛山|东莞|中山|惠州|汕头|江门|肇庆|湛江|茂名|清远|韶关|梅州|河源|阳江|云浮|潮州|揭阳|汕尾|更多城市/.test(t);
    }),
    typeWords: words.filter(function(t){
      return /^全部$|展览|演出|赛事|本地生活|only同人展|漫展/.test(t);
    })
  });
})()
"""


def main() -> None:
    br = Browser(headless=True, width=1600, height=1800)
    try:
        br.cmd("Network.enable")
        br.cmd("Runtime.enable")
        br.render("https://show.bilibili.com/platform/home.html?msource=pc_web", wait=10)
        time.sleep(4)

        # 点「更多城市」看完整列表
        click = br.cmd("Runtime.evaluate", {"expression": r"""
        (function(){
          var n = [].slice.call(document.querySelectorAll('a,li,span,div,button'))
            .find(function(e){ return (e.innerText || '').trim() === '更多城市'; });
          if(!n) return 'no-more';
          n.click();
          return 'clicked';
        })()
        """, "returnByValue": True})
        print("  点击「更多城市」:", click.get("result", {}).get("value"))
        time.sleep(3)

        st = br.cmd("Runtime.evaluate", {"expression": READ, "returnByValue": True})
        d = json.loads(st["result"]["value"])
        print("  城市候选:", d["cityWords"][:30])
        print("  类型候选:", d["typeWords"][:12])
        print("  正文:", d["text"][:520])
    finally:
        br.close()


main()

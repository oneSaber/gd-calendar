"""探测 B站会员购搜索框是否可用（决定能否按「地偶/偶像/ACG」直接搜）。"""

import json
import sys
import time

sys.path.insert(0, r"D:\AI\gd-calendar\scripts")
from cdp_fetch import Browser  # noqa: E402

FIND_INPUT = r"""
(function(){
  var ins = [].slice.call(document.querySelectorAll('input'));
  return JSON.stringify(ins.map(function(i){
    return {type: i.type, ph: i.placeholder || '', id: i.id || '',
            cls: String(i.className || '').slice(0, 40)};
  }));
})()
"""

DO_SEARCH = r"""
(function(){
  var ins = [].slice.call(document.querySelectorAll('input'));
  var i = ins.find(function(x){ return /搜/.test(x.placeholder || ''); });
  if(!i){ i = ins.find(function(x){ return x.type === 'text'; }); }
  if(!i){ return 'no-input'; }
  i.focus();
  var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  setter.call(i, '地偶');
  i.dispatchEvent(new Event('input', {bubbles: true}));
  i.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', keyCode: 13, bubbles: true}));
  i.dispatchEvent(new KeyboardEvent('keyup', {key: 'Enter', keyCode: 13, bubbles: true}));
  return 'searched:' + i.placeholder;
})()
"""

READ_STATE = r"""
(function(){
  return JSON.stringify({
    url: location.href,
    text: document.body.innerText.replace(/\n+/g, ' | ').slice(0, 420)
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

        ins = br.cmd("Runtime.evaluate", {"expression": FIND_INPUT, "returnByValue": True})
        print("  输入框:", ins["result"]["value"][:300])

        r = br.cmd("Runtime.evaluate", {"expression": DO_SEARCH, "returnByValue": True})
        print("  搜索动作:", r.get("result", {}).get("value"))
        time.sleep(6)

        st = br.cmd("Runtime.evaluate", {"expression": READ_STATE, "returnByValue": True})
        d = json.loads(st["result"]["value"])
        print("  搜索后 URL:", d["url"][:120])
        print("  搜索后内容:", d["text"][:340])
    finally:
        br.close()


main()

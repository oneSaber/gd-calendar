# 推广素材包（promo）

为推广本项目准备的两份文案 + 一组界面演示截图。截图全部是**真实数据**（本地跑起来的
应用，不是 `?demo=1` 的合成示例），时间：2026-10-09。

## 内容

| 文件 | 说明 |
| --- | --- |
| [`抖音文案.md`](抖音文案.md) | 抖音：主推 + 备用两篇，含标题/正文/口播稿/封面文案/画面顺序/话题标签 |
| [`小红书文案.md`](小红书文案.md) | 小红书：主推 + 备用两篇，含标题/正文/配图顺序/事实核对表 |
| `screenshots/` | 11 张截图，三种画幅（3:4 小红书 / 9:16 抖音 / 16:10 备用） |

## 截图清单

| 文件 | 画幅 | 内容 |
| --- | --- | --- |
| `01-list-desktop-3x4.png` | 1440×1920 | 桌面 · 本月列表（8 城筛选 + 类型/标记筛选） |
| `02-month-calendar-3x4.png` | 1440×1920 | 桌面 · 月历视图 + 点开 10 月 10 日（当天 6 场） |
| `03-idol-detail-3x4.png` | 1440×1920 | 桌面 · 只看地偶 + 详情（阵容 / 票务 / 订阅） |
| `03b-poster-detail-3x4.png` | 1440×1920 | 桌面 · 带海报的 ACG 场次详情（**推荐首图**） |
| `04-list-desktop-full.png` | 1440×8000 | 桌面 · 列表整页长图（可裁切） |
| `05-month-full.png` | 1440×1200 | 桌面 · 月历整页 |
| `06-mobile-list-9x16.png` | 860×1864 | 手机 · 列表 + 顶栏（含「订阅 / 更新数据」） |
| `07-mobile-detail-9x16.png` | 860×1864 | 手机 · 地偶详情（阵容 4 组） |
| `08-mobile-month-9x16.png` | 860×1864 | 手机 · 月历点阵 + 图例 |
| `09-api-docs.png` | 1440×1080 | `/docs` 接口文档（技术向） |
| `10-ics-subscribe-toast.png` | 1440×1080 | 点「＋订阅这个日历」后的 .ics 订阅链接 toast |
| `11-about-sources.png` | 1440×1080 | 「数据来源与免责声明」弹窗（合规口径） |

## 复现 / 重拍

```bat
:: 1) 起服务（默认 8000；库里已有真实数据）
启动.bat

:: 2) 重拍全部截图（纯标准库 CDP，用本机 Edge/Chrome，无 Playwright 依赖）
.venv\Scripts\python.exe scripts\capture_shots.py --out promo\screenshots

:: 只重拍某几个镜头
.venv\Scripts\python.exe scripts\capture_shots.py --only 03,07
```

支持的镜头号：`01 02 03 03b 04 05 06 07 08 09 10 11`。

### 截图时的两个前提（README 里也记一笔）

1. **功能开关要打开**，否则顶栏的「＋订阅这个日历 / ↻更新数据」不显示：

   ```bat
   set FEATURE_ICS=true && set FEATURE_UPDATE=true && 启动.bat
   ```

   （等价做法：在 `.env` 里写 `FEATURE_ICS=true` / `FEATURE_UPDATE=true`。默认是 `false`。）

2. **`10-ics-subscribe-toast.png` 的剪贴板是桩掉的**：无头浏览器没有剪贴板权限，
   脚本把 `navigator.clipboard.writeText` 换成 resolve，让 toast 走成功分支。
   真实浏览器里（localhost 属安全上下文 + 真实点击手势）本来就是成功的。

## 顺手修掉的一个 UI bug

截图时发现：`FEATURE_ICS` / `FEATURE_UPDATE` 通过**后端** `/api/features` 打开时，
顶栏那两个按钮仍然是隐藏的。原因是 `applyFeatureFlags()` 会被调用两次
（先按静态值、再按后端值），而它只在「关闭」方向写 `el.hidden = true`，
第一次留下的 `true` 永远不会被撤销。

- 影响：`.env` 里打开开关 → 详情面板里的 `.ics` 按钮正常（走 CSS class），
  但顶栏的「＋订阅这个日历」「↻更新数据」看不见。
- 修法：`app/web/js/app.js` 里按钮改为**双向**设置 `hidden`
  （进度条 `update-bar` 仍只在关闭时隐藏，因为它的显隐由 `update.js` 按任务状态控制）。
- 静态站（GitHub Pages）不受影响 —— 那边 `__FEATURES__` 在 `app.js` 之前就被注入成权威值。

---

## 小红书发帖自动化（`scripts/xhs_publish.py`）

**只在你自己机器上、用你自己的账号、走平台自己的 UI。** 不注入 Cookie、不伪造签名、
不逆向接口、不加任何反检测指纹开关 —— 登录是你自己扫码，操作是真实鼠标/键盘事件。

```bat
:: 1) 起一个可见的 Edge（持久 profile，端口 9222），窗口别关
.venv\Scripts\python.exe scripts\xhs_publish.py launch

:: 2) 扫码登录（轮询到登录成功为止，默认等 900 秒）
.venv\Scripts\python.exe scripts\xhs_publish.py login --timeout 900

:: 3) 看当前状态 / 调选择器
.venv\Scripts\python.exe scripts\xhs_publish.py status
.venv\Scripts\python.exe scripts\xhs_publish.py dump --publish --click 上传图文 --verbose

:: 4) 填标题+正文+图（标题正文直接从 promo/小红书文案.md 解析，单一来源）
.venv\Scripts\python.exe scripts\xhs_publish.py post --dry-run   :: 只填不发
.venv\Scripts\python.exe scripts\xhs_publish.py post --draft     :: 填完点「暂存离开」
.venv\Scripts\python.exe scripts\xhs_publish.py post --publish   :: 填完点「发布」

:: 5) 用完关掉
.venv\Scripts\python.exe scripts\xhs_publish.py close
```

登录态与 cookie 落在仓库**外面**（避免误提交）：

- profile：`%LOCALAPPDATA%\gd-calendar\xhs-profile\`
- cookie 清单：`%LOCALAPPDATA%\gd-calendar\xhs-cookies.json`（只导出，日志里不打印值）

### 实测踩到的四个坑（都已在脚本里处理）

| 现象 | 真实原因 | 处理 |
| --- | --- | --- |
| 按文本点「上传图文」点到整页容器，没反应 | 同一标签在页面里挂了两份（一份渲染在视口外 x≈-9710） | 先按文本筛、再优先视口内、取**面积最小**的叶子节点 |
| 真实鼠标事件点标签页无效 | 坐标落在装饰性副本上 | 切标签用 DOM `click()`（浏览器内部正常点击，不是反检测手段） |
| 标题填完是空的 | React **受控**输入：`Input.insertText` 不更新 value tracker，re-render 就清空 | 用原生 value setter + 派发 `input`/`change` 事件，并回读校验 |
| 找不到「发布 / 暂存离开」按钮 | 它们在 `<xhs-publish-btn>` 的**封闭 shadow root** 里（`el.shadowRoot` 为 null，`querySelectorAll` 也看不见） | CDP DOM 域穿透：`describeNode(pierce:true)` 找按钮 → `getBoxModel` 拿坐标 → 派发真实鼠标事件 |

另外两个约束（文案里已按此调整）：

- **正文上限 1000 字**：原正文 1177 字会被截断/判不合规，已压到 936 字（编辑器计数 916/1000）。
- **标题上限 20 字**：`广东看演出必备｜地偶ACG都在这一个日历` 正好 20 字。

### 本次执行结果（2026-10-09）

- 登录成功（创作者中心 cookie 17 条，登录态持久化在 profile 里），**未发布任何公开内容**。
- 最后一次 `post --draft`：标题 20 字 + 正文 936 字 + 4 张图 → 点「暂存离开」→
  页面提示 **保存成功**，草稿箱 `图文笔记(4)`。
- 浏览器会话中断过一次（调试端口的页面被关掉，CDP 目标数变成 0），重新建页面后复核：
  **登录态还在**，草稿箱 `图文笔记(3)`，标题为
  `广东看演出必备｜地偶ACG都在这一个日历` 的那条（19:27:52）点「编辑」进去核对：
  **标题 20 字 + 正文计数 916/1000 + 4 张图 + 完整话题标签** —— 内容完整、可直接发布。
- ⚠️ 计数从 4 变 3 的原因：当时那条 19:28:20 是**正在编辑的会话**，页面关闭后就从列表里消失了；
  真正落盘的草稿还在。**结论：草稿能用，但别只靠「保存成功」的 toast 判断，要重新打开核对**
  （`scripts/_xhs_draftopen.py` 那类探针就是干这个的，验证完已删）。
- ⚠️ 页面明确写了：**草稿存在当前浏览器本地**，清浏览器数据即失效。
  所以要在**这个窗口 / 这个 profile** 里点发布，手机 App 上看不到这条草稿；建议尽快发掉。
- 草稿箱里另外 2 条（19:27:16 同名、19:25:32 无标题）是我迭代时的自动存档，可以点「删除」清掉。
- 过程截图在 `promo/xhs-run/`（`06-draft-opened.png` 是复核草稿内容那张；截图含账号名，外发前注意）。



"""把仓库里的 Markdown 文档转成静态站可访问的 HTML。

## 为什么要转

GitHub Pages 开着 `.nojekyll`，所以 `docs/*.md` **不会被处理**，
直接访问会 404（实测确认）。而数据源清单这类文档应该能从站上直接看到，
所以构建时把 Markdown 渲染成 HTML 一起导出。

## 为什么不用第三方库

只需要一个**受限子集**（标题/列表/表格/代码块/粗体/行内代码/链接），
自己实现比引入 markdown 依赖更省事，也避免依赖漂移。
渲染前对 `<` `>` `&` 做转义，防止文档里的尖括号破坏页面结构。
"""

from __future__ import annotations

import html as _html
import re
from pathlib import Path

# 页面外壳：与 docs.html 用同一套配色，保证站内观感一致
_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · 广东地下演出日历</title>
<style>
  :root {{ --fg:#1a1a1a; --dim:#666; --line:#e5e5e5; --accent:#c8102e;
           --bg:#fff; --code:rgba(128,128,128,.14); }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --fg:#e8e8e8; --dim:#9a9a9a; --line:#333; --bg:#161616; }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:32px 20px 80px; background:var(--bg); color:var(--fg);
         font:15px/1.75 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }}
  main {{ max-width:900px; margin:0 auto; }}
  a {{ color:var(--accent); text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  h1 {{ font-size:23px; margin:0 0 14px; }}
  h2 {{ font-size:18px; margin:34px 0 12px; padding-bottom:6px;
        border-bottom:1px solid var(--line); }}
  h3 {{ font-size:15.5px; margin:24px 0 8px; }}
  code {{ background:var(--code); padding:1px 5px; border-radius:4px;
          font:13px/1 ui-monospace,Consolas,monospace; }}
  pre {{ background:var(--code); padding:12px 14px; border-radius:8px;
         overflow-x:auto; }}
  pre code {{ background:none; padding:0; }}
  table {{ border-collapse:collapse; width:100%; margin:12px 0; font-size:14px; }}
  th, td {{ border:1px solid var(--line); padding:7px 10px; text-align:left;
            vertical-align:top; }}
  th {{ background:var(--code); font-weight:600; }}
  blockquote {{ margin:12px 0; padding:8px 14px; border-left:3px solid var(--accent);
                background:rgba(200,16,46,.06); color:var(--dim); }}
  ul, ol {{ padding-left:22px; }}
  hr {{ border:none; border-top:1px solid var(--line); margin:28px 0; }}
  .back {{ display:inline-block; margin-bottom:18px; font-size:13.5px; }}
</style>
</head>
<body>
<main>
<a class="back" href="./docs.html">← 文档索引</a>
{body}
</main>
</body>
</html>
"""


def _inline(text: str) -> str:
    """行内渲染：先转义，再处理行内代码/粗体/链接/自动链接。"""
    out = _html.escape(text, quote=False)
    # 行内代码优先（内容不再处理）
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    # 粗体
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    # Markdown 自动链接 <https://…>：必须**在**转义之后处理，
    # 否则 `&lt;…&gt;` 会原样显示成尖括号（实测踩过）。
    out = re.sub(
        r"&lt;(https?://[^\s&]+)&gt;",
        r'<a href="\1">\1</a>',
        out,
    )
    # 链接 [文字](地址)
    out = re.sub(
        r"\[([^\]]+)\]\(([^)\s]+)\)",
        r'<a href="\2">\1</a>',
        out,
    )
    return out


def md_to_html(md: str, title: str = "") -> str:
    """把 Markdown 子集渲染成带外壳的 HTML 页面。"""
    lines = md.splitlines()
    body: list[str] = []
    i = 0

    def flush_para(buf: list[str]) -> None:
        if buf:
            body.append("<p>" + _inline(" ".join(buf)) + "</p>")
            buf.clear()

    para: list[str] = []
    in_code = False
    code_buf: list[str] = []
    list_kind = ""       # ul | ol
    in_table = False
    table_rows: list[list[str]] = []

    def close_list() -> None:
        nonlocal list_kind
        if list_kind:
            body.append(f"</{list_kind}>")
            list_kind = ""

    def flush_table() -> None:
        nonlocal in_table, table_rows
        if not in_table or not table_rows:
            in_table = False
            table_rows = []
            return
        body.append("<table>")
        for idx, cells in enumerate(table_rows):
            tag = "th" if idx == 0 else "td"
            body.append(
                "<tr>" + "".join(f"<{tag}>{_inline(c)}</{tag}>" for c in cells) + "</tr>"
            )
        body.append("</table>")
        in_table = False
        table_rows = []

    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()

        # 代码块
        if line.startswith("```"):
            if in_code:
                body.append("<pre><code>" + _html.escape("\n".join(code_buf)) + "</code></pre>")
                code_buf.clear()
                in_code = False
            else:
                flush_para(para)
                close_list()
                flush_table()
                in_code = True
            i += 1
            continue
        if in_code:
            code_buf.append(raw)
            i += 1
            continue

        # 表格
        if line.startswith("|") and line.endswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            # 分隔行（|---|---|）
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                i += 1
                continue
            if not in_table:
                flush_para(para)
                close_list()
                in_table = True
            table_rows.append(cells)
            i += 1
            continue
        if in_table:
            flush_table()

        # 标题
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            flush_para(para)
            close_list()
            lvl = len(m.group(1))
            body.append(f"<h{lvl}>{_inline(m.group(2))}</h{lvl}>")
            i += 1
            continue

        # 分隔线
        if re.fullmatch(r"-{3,}|\*{3,}", line.strip()):
            flush_para(para)
            close_list()
            body.append("<hr>")
            i += 1
            continue

        # 引用
        if line.startswith(">"):
            flush_para(para)
            close_list()
            q = [line.lstrip("> ").strip()]
            while i + 1 < len(lines) and lines[i + 1].startswith(">"):
                i += 1
                q.append(lines[i].lstrip("> ").strip())
            body.append("<blockquote>" + _inline(" ".join(q)) + "</blockquote>")
            i += 1
            continue

        # 列表
        m = re.match(r"^\s*[-*+]\s+(.*)$", line)
        if m:
            flush_para(para)
            if list_kind != "ul":
                close_list()
                body.append("<ul>")
                list_kind = "ul"
            body.append("<li>" + _inline(m.group(1)) + "</li>")
            i += 1
            continue
        m = re.match(r"^\s*\d+\.\s+(.*)$", line)
        if m:
            flush_para(para)
            if list_kind != "ol":
                close_list()
                body.append("<ol>")
                list_kind = "ol"
            body.append("<li>" + _inline(m.group(1)) + "</li>")
            i += 1
            continue

        close_list()

        if not line.strip():
            flush_para(para)
            i += 1
            continue

        para.append(line.strip())
        i += 1

    if in_code and code_buf:
        body.append("<pre><code>" + _html.escape("\n".join(code_buf)) + "</code></pre>")
    flush_para(para)
    close_list()
    flush_table()

    return _PAGE.format(title=_html.escape(title or "文档"), body="\n".join(body))


def render_file(src: Path, dst: Path, *, title: str = "") -> bool:
    """把 src（Markdown）渲染到 dst（HTML）。返回是否成功。"""
    if not src.exists():
        return False
    md = src.read_text(encoding="utf-8")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(md_to_html(md, title or src.stem), encoding="utf-8")
    return True

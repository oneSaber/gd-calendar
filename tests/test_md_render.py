"""文档渲染器测试（Markdown 子集 → HTML）。

覆盖实测踩过的两个坑：
  1. `docs/*.md` 在开着 `.nojekyll` 的 Pages 上**不生效**（404）→ 必须转 HTML
  2. `<https://…>` 自动链接必须在**转义之后**处理，
     否则显示成 `&lt;…&gt;`（尖括号原样可见）
"""

from __future__ import annotations

import pytest

from app.md_render import md_to_html


class TestHeadings:
    def test_h1_h2_h3(self):
        h = md_to_html("# 一\n\n## 二\n\n### 三", "T")
        assert "<h1>一</h1>" in h
        assert "<h2>二</h2>" in h
        assert "<h3>三</h3>" in h

    def test_title_in_page(self):
        h = md_to_html("# 内容", "我的标题")
        assert "<title>我的标题 · 广东地下演出日历</title>" in h


class TestTables:
    def test_table_rendered(self):
        md = "| 源 | 场次 |\n| --- | --- |\n| 秀动 | 287 |"
        h = md_to_html(md, "T")
        assert "<table>" in h
        assert "<th>源</th>" in h and "<th>场次</th>" in h
        assert "<td>秀动</td>" in h and "<td>287</td>" in h

    def test_separator_row_not_emitted(self):
        md = "| a | b |\n| --- | --- |\n| 1 | 2 |"
        h = md_to_html(md, "T")
        assert "<td>---</td>" not in h

    def test_three_column_table(self):
        md = "| a | b | c |\n| --- | --- | --- |\n| 1 | 2 | 3 |"
        h = md_to_html(md, "T")
        assert h.count("<td>") == 3


class TestCodeBlocks:
    def test_fenced_code_escaped(self):
        h = md_to_html("```\n<script>alert(1)</script>\n```", "T")
        assert "<pre><code>" in h
        # 代码内容必须被转义，不能真的注入
        assert "<script>alert(1)" not in h
        assert "&lt;script&gt;" in h

    def test_inline_code(self):
        h = md_to_html("用 `python -m app.cli` 跑", "T")
        assert "<code>python -m app.cli</code>" in h


class TestInline:
    def test_bold(self):
        assert "<strong>重点</strong>" in md_to_html("**重点**", "T")

    def test_link(self):
        h = md_to_html("[文字](https://x.com)", "T")
        assert '<a href="https://x.com">文字</a>' in h

    def test_autolink_after_escape(self):
        """⚠️ 实测坑：`<https://…>` 必须在转义**之后**处理。

        不这么做会渲染成 `&lt;https://…&gt;`，页面上直接看到尖括号。
        """
        h = md_to_html("线上：<https://example.com/a>", "T")
        assert '<a href="https://example.com/a">' in h
        assert "&lt;https://" not in h

    def test_plain_angle_brackets_still_escaped(self):
        """非 URL 的尖括号仍要正常转义（防止破坏页面结构）。"""
        h = md_to_html("比较 a < b 与 c > d", "T")
        assert "a &lt; b" in h and "c &gt; d" in h

    def test_html_injection_escaped(self):
        h = md_to_html('标题 <img src=x onerror=alert(1)>', "T")
        assert "<img" not in h
        assert "&lt;img" in h


class TestBlocks:
    def test_unordered_list(self):
        h = md_to_html("- 甲\n- 乙", "T")
        assert "<ul>" in h and h.count("<li>") == 2

    def test_ordered_list(self):
        h = md_to_html("1. 甲\n2. 乙", "T")
        assert "<ol>" in h and h.count("<li>") == 2

    def test_blockquote(self):
        h = md_to_html("> 注意\n> 第二行", "T")
        assert "<blockquote>" in h
        assert "注意 第二行" in h        # 多行合并

    def test_hr(self):
        assert "<hr>" in md_to_html("---", "T")

    def test_paragraph_joined(self):
        h = md_to_html("第一行\n第二行", "T")
        assert "<p>第一行 第二行</p>" in h

    def test_back_link_present(self):
        """每页都要有回文档索引的链接。"""
        assert 'href="./docs.html"' in md_to_html("# T", "T")


class TestRealDocs:
    """用仓库里真实的文档跑一遍，确保不会渲染出错。"""

    @pytest.mark.parametrize("name", ["SPEC.md", "README.md", "docs/DATASOURCES.md"])
    def test_renders_without_error(self, name):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        p = root / name
        if not p.exists():
            pytest.skip(f"{name} 不存在")
        h = md_to_html(p.read_text(encoding="utf-8"), p.stem)
        assert "<main>" in h and "</html>" in h
        assert len(h) > 500

"""海报代理 / 来源跳转测试。

这里的断言全部来自实测结论，不是假设：
  * 豆瓣图片有防盗链：不带 Referer 返回 **418**，带 `Referer: https://www.douban.com/` 才 200。
    → 所以前端必须走本站代理，且代理必须补 Referer。
  * B站封面是**协议相对 URL**（`//i2.hdslb.com/...`），不补 `https:` 会加载失败。
"""

from __future__ import annotations

import pytest

from app.api import images
from app.api.schemas import poster_display_url


class TestNormalizeUrl:
    def test_protocol_relative_gets_https(self):
        """B站实测返回 `//i2.hdslb.com/...`。"""
        assert images.normalize_image_url("//i2.hdslb.com/bfs/x.jpg") == \
            "https://i2.hdslb.com/bfs/x.jpg"

    def test_http_upgraded_to_https(self):
        """避免混合内容被浏览器拦截。"""
        assert images.normalize_image_url("http://img3.doubanio.com/a.jpg") == \
            "https://img3.doubanio.com/a.jpg"

    def test_https_kept(self):
        u = "https://img3.doubanio.com/pview/event_poster/median/public/x.jpg"
        assert images.normalize_image_url(u) == u

    @pytest.mark.parametrize("bad", [None, "", "   ", "ftp://x/a.jpg", "not-a-url", "/local/x.jpg"])
    def test_rejects_non_http(self, bad):
        assert images.normalize_image_url(bad) is None


class TestHostWhitelist:
    def test_allowed_cdns(self):
        assert images.host_allowed("https://img3.doubanio.com/a.jpg")
        assert images.host_allowed("https://i2.hdslb.com/a.jpg")
        assert images.host_allowed("https://img04.showstart.com/a.jpg")

    def test_rejects_unknown_host(self):
        """不能退化成任意 URL 的开放代理（SSRF 风险）。"""
        assert not images.host_allowed("https://evil.com/a.jpg")
        assert not images.host_allowed("https://doubanio.com.evil.com/a.jpg")

    def test_localhost_rejected(self):
        assert not images.host_allowed("http://127.0.0.1:8000/secret")
        assert not images.host_allowed("http://localhost/x.jpg")


class TestReferer:
    def test_douban_needs_referer(self):
        """实测：豆瓣不带 Referer → 418。"""
        assert images.referer_for("https://img3.doubanio.com/a.jpg") == "https://www.douban.com/"

    def test_bilibili_referer(self):
        assert images.referer_for("https://i2.hdslb.com/a.jpg") == "https://show.bilibili.com/"

    def test_showstart_referer(self):
        assert images.referer_for("https://img04.showstart.com/a.jpg") == "https://www.showstart.com/"

    def test_unknown_host_no_referer(self):
        assert images.referer_for("https://example.com/a.jpg") is None


class TestPosterDisplayUrl:
    def test_whitelisted_goes_through_proxy(self):
        got = poster_display_url("https://img3.doubanio.com/pview/x.jpg")
        assert got is not None
        assert got.startswith("/api/archive/poster?url=")
        # 原始 URL 必须被编码，否则 query 会被 & 截断
        assert "https%3A%2F%2Fimg3.doubanio.com" in got

    def test_protocol_relative_handled(self):
        got = poster_display_url("//i2.hdslb.com/bfs/x.jpg")
        assert got is not None and got.startswith("/api/archive/poster?url=")
        assert "i2.hdslb.com" in got

    def test_non_whitelisted_passthrough(self):
        u = "https://cdn.example.com/a.jpg"
        assert poster_display_url(u) == u

    @pytest.mark.parametrize("bad", [None, "", "   ", "javascript:alert(1)", "data:image/png;base64,xxx"])
    def test_rejects_dangerous(self, bad):
        """javascript: / data: 绝不能被当作图片地址输出到前端。"""
        assert poster_display_url(bad) is None

    def test_content_type_extension_guess(self):
        assert images._guess_ext("image/jpeg", "https://x/a.jpg") == ".jpg"
        assert images._guess_ext("image/png", "https://x/a") == ".png"
        # 未知类型退回 URL 后缀
        assert images._guess_ext("", "https://x/a.webp") == ".webp"


class TestPosterApi:
    """接口层：白名单与错误码。"""

    @pytest.fixture
    async def client(self):
        import httpx

        from app.api.main import app

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    async def test_rejects_non_whitelisted_host(self, client):
        r = await client.get("/api/archive/poster", params={"url": "https://evil.com/x.jpg"})
        assert r.status_code == 403

    async def test_rejects_invalid_url(self, client):
        r = await client.get("/api/archive/poster", params={"url": "not-a-url"})
        assert r.status_code == 400

    async def test_missing_param(self, client):
        r = await client.get("/api/archive/poster")
        assert r.status_code == 422


class TestGoRedirect:
    @pytest.fixture
    async def client(self):
        import httpx

        from app.api.main import app

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    async def test_redirects_to_source(self, client):
        r = await client.get(
            "/api/go",
            params={"url": "https://www.showstart.com/event/309486"},
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert r.headers["location"] == "https://www.showstart.com/event/309486"

    async def test_does_not_tamper_with_url(self, client):
        """不追加任何参数（不篡改对方链接）。"""
        src = "https://www.douban.com/event/37588338/?from=xx"
        r = await client.get("/api/go", params={"url": src}, follow_redirects=False)
        assert r.headers["location"] == src

    async def test_protocol_relative(self, client):
        r = await client.get("/api/go", params={"url": "//www.douban.com/event/1"},
                             follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"].startswith("https://")

    @pytest.mark.parametrize("bad", ["javascript:alert(1)", "file:///etc/passwd", "ftp://x/y", ""])
    async def test_rejects_dangerous_schemes(self, client, bad):
        """开放重定向防护：只允许 http/https。"""
        r = await client.get("/api/go", params={"url": bad}, follow_redirects=False)
        assert r.status_code in (400, 422)


class TestShowstartPosterExtraction:
    """秀动详情页海报抽取规则。

    注意：这些规则**对目前的秀动实测无效**（详情页是 SPA 壳，无 og:image），
    保留它们是为了站点改回 SSR 时能直接生效，同时也是
    `enrich_posters` 判断「是否已支持静态抓图」的探针。
    """

    def test_og_image_forward(self):
        from app.collectors.showstart import extract_poster_url

        html = '<meta property="og:image" content="https://img04.showstart.com/upload/x.jpg">'
        assert extract_poster_url(html) == "https://img04.showstart.com/upload/x.jpg"

    def test_og_image_reversed_and_protocol_relative(self):
        from app.collectors.showstart import extract_poster_url

        html = '<meta content="//img04.showstart.com/upload/y.jpg" property="og:image">'
        assert extract_poster_url(html) == "https://img04.showstart.com/upload/y.jpg"

    def test_json_poster_field(self):
        from app.collectors.showstart import extract_poster_url

        assert extract_poster_url('{"poster":"https://img04.showstart.com/z.jpg"}') == \
            "https://img04.showstart.com/z.jpg"

    def test_filters_site_assets(self):
        """站点 logo / 默认图不能被当成海报。"""
        from app.collectors.showstart import extract_poster_url

        for bad in (
            '<meta property="og:image" content="https://img04.showstart.com/img/logo.png">',
            '<meta property="og:image" content="https://img04.showstart.com/img/default.png">',
            '<meta property="og:image" content="https://img04.showstart.com/img/avatar.png">',
        ):
            assert extract_poster_url(bad) is None

    def test_no_poster(self):
        from app.collectors.showstart import extract_poster_url

        assert extract_poster_url("<html>没有海报</html>") is None
        assert extract_poster_url("") is None

"""情报账号清单与知识库账号字段的测试。"""

from __future__ import annotations

import pytest

from app.normalize import info_accounts as ia
from app.normalize.artist_kb import SEED_ARTISTS


class TestInfoAccounts:
    """情报账号清单：不能混进演出团体，且必须有可复核的依据。"""

    def test_has_both_platforms(self):
        assert ia.by_platform("xhs"), "缺小红书情报账号"
        assert ia.by_platform("weibo"), "缺微博情报账号"

    def test_aggregators_present(self):
        """聚合号（图鉴/速览）是性价比最高的一类，必须至少有。"""
        assert ia.aggregator_names("xhs"), "缺小红书聚合号"
        assert "广州地偶图鉴" in ia.aggregator_names("xhs")
        assert "Garry_Liu_" in ia.aggregator_names("weibo")

    @pytest.mark.parametrize("acct", [
        *ia.XHS_INFO_ACCOUNTS, *ia.WEIBO_INFO_ACCOUNTS,
    ])
    def test_every_account_has_evidence(self, acct):
        """每个账号都要有发现依据 —— 否则无法复核它为什么值得追。"""
        assert acct.evidence and len(acct.evidence) >= 8, f"{acct.name} 缺依据"
        assert acct.platform in ("xhs", "weibo")
        assert acct.kind in ("aggregator", "venue", "tool", "organizer")

    def test_weibo_accounts_have_uid_when_known(self):
        """有 uid 的必须是纯数字（用于搜索页定位）。"""
        for a in ia.WEIBO_INFO_ACCOUNTS:
            if a.uid:
                assert a.uid.isdigit(), f"{a.name} 的 uid 不是数字：{a.uid}"


class TestKbAccountFields:
    """知识库条目的官方账号字段：只填**交叉验证过**的。"""

    def test_entries_have_accounts(self):
        with_xhs = [e for e in SEED_ARTISTS if e.xhs]
        assert len(with_xhs) >= 3, f"带官方账号的条目太少：{len(with_xhs)}"

    @pytest.mark.parametrize("name", ["恋时青空", "ReaLume", "恋音契约", "山海誓约"])
    def test_verified_accounts_present(self, name):
        e = next((x for x in SEED_ARTISTS if x.name == name), None)
        assert e is not None, f"知识库缺 {name}"
        assert e.xhs, f"{name} 缺小红书官方账号"

    def test_account_name_relates_to_group(self):
        """官方账号名必须与团体名相关 —— 防止把粉丝号/摄影号当官方号。

        实测教训：反向搜索会返回摄影师、粉丝、修图师等账号。
        只接受名字相关的（如 ReaLume ↔ ReaLume_Official）。
        """
        for e in SEED_ARTISTS:
            if not e.xhs:
                continue
            key = e.name.replace(" ", "").lower()
            acct = e.xhs.replace(" ", "").lower()
            # 取团体名前 3 字符做匹配（中文团名够独特）
            assert key[:3] in acct or acct in key, (
                f"{e.name} 的账号「{e.xhs}」与团体名不相关，疑似粉丝号"
            )

    def test_info_accounts_not_in_artist_kb(self):
        """情报账号不能混进 SEED_ARTISTS —— 那会影响「按阵容判定分类」。"""
        artist_names = {e.name for e in SEED_ARTISTS}
        for a in (*ia.XHS_INFO_ACCOUNTS, *ia.WEIBO_INFO_ACCOUNTS):
            assert a.name not in artist_names, (
                f"情报账号「{a.name}」混进了演出团体知识库"
            )

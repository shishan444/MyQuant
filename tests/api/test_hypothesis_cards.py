"""GET /api/research/hypothesis-cards 契约测试（add-hypothesis-cards B1）。

spec delta「主任务聚合查询」五 Scenario+A1 组 L1 用例锚（tester 方案
A 面）：五态筛选/422 结构化/q 转义/三 null 语义/cursor 优先级/adhoc
双端点对照/GET 幂等/next_cursor 两态/排序确定。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient


def _ts(days: int = 0) -> str:
    """种子时刻相对化（时间炸弹修复）：days 偏移运行时刻的 ISO 串，
    与 register 日志时刻（utc_now_iso 同族 isoformat）字典序可比。

    hyp_b 排序首位用 +2（恒新于 register 运行时刻日志），其余种子按
    原相对序平移到负偏移——「register 运行时刻 vs 种子时刻」先后关系
    恒定，不随日历漂移。
    """
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _seed(db):
    """五假设×状态/验证/日志的确定性种子（ORM 直插+register 混合）。

    - hyp_a active+2 验证（最新 fail）+日志最新 now-1d
    - hyp_b refuted+1 验证（pass）+日志最新 now+2d（排序首位，恒压
      register 运行时刻日志）
    - hyp_c abandoned 零验证（三 null 语义）+日志 now-6d
    - hyp_d supported 零日志（recent_activity_at=null——ORM 直插不经
      register，构造无日志行防御位）
    - statement 含 % 字面（q 转义用例）
    - adhoc 验证记录一条（假设表零父行——双端点对照）
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from core.analysis.hypothesis_service import register_hypothesis
    from core.persistence.repository import research as rr
    from core.persistence.orm.models import (
        ResearchHypothesis, ResearchLog, ValidationRecord)

    conds = {"when_conditions": [{"subject": "close", "action": "gt",
                                  "target": 0}],
             "then_conditions": [{"subject": "close", "action": "gt",
                                  "target": -1}]}
    rng = [{"symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-02-01T00:00:00Z"}]
    register_hypothesis(
        db, conditions=conds, statement="BTC 动量探索 100%",
        strategy_params={"stop_loss": 0.05}, data_ranges=rng)
    Session = sessionmaker(bind=create_engine(f"sqlite:///{db}"))
    with Session() as sess:
        # 三条终态/空态假设直插（statement 覆盖子串与 % 字面）
        sess.add(ResearchHypothesis(
            hypothesis_id="hyp_a", conditions="{}", conditions_hash="c1",
            statement="RSI 超卖反弹", data_ranges="[]", ranges_hash="r1",
            status="active", created_at=_ts(-8),
            updated_at=_ts(-8)))
        sess.add(ResearchHypothesis(
            hypothesis_id="hyp_b", conditions="{}", conditions_hash="c2",
            statement="MACD 背离", data_ranges="[]", ranges_hash="r2",
            status="refuted", created_at=_ts(-7),
            updated_at=_ts(-7)))
        sess.add(ResearchHypothesis(
            hypothesis_id="hyp_c", conditions="{}", conditions_hash="c3",
            statement="布林带收窄突破", data_ranges="[]", ranges_hash="r3",
            status="abandoned", created_at=_ts(-6),
            updated_at=_ts(-6)))
        sess.add(ResearchHypothesis(
            hypothesis_id="hyp_d", conditions="{}", conditions_hash="c4",
            statement="假设D", data_ranges="[]", ranges_hash="r4",
            status="supported", created_at=_ts(-5),
            updated_at=_ts(-5)))
        # 验证记录：hyp_a 两条（now-7d pass / now-1d fail——最新=fail）；
        # hyp_b 一条（now-6d pass）；adhoc 一条（假设表零父行）
        for rid, hid, verdict, ts in (
                ("rec_a1", "hyp_a", "pass", _ts(-7)),
                ("rec_a2", "hyp_a", "fail", _ts(-1)),
                ("rec_b1", "hyp_b", "pass", _ts(-6)),
                ("rec_ad1", "adhoc", "pass", _ts(-4))):
            sess.add(ValidationRecord(
                record_id=rid, hypothesis_id=hid, strategy_id="st_x",
                level="isolated_backtest", segmentation="", verdict=verdict,
                range_hash=f"rh_{rid}", params="{}", result="{}",
                created_at=ts))
        # 日志：register 假设自带一条（hyp_reg，时刻=运行时刻 now）；
        # 手工补 hyp_a/hyp_b/hyp_c 各一条（相对偏移构造排序——hyp_b
        # +2d 恒压 register 运行时刻，保排序首位断言不随日历漂移）
        for hid, ts in (("hyp_a", _ts(-1)), ("hyp_b", _ts(2)),
                        ("hyp_c", _ts(-6))):
            sess.add(ResearchLog(
                log_id=f"lg_{hid}", hypothesis_id=hid, entry_type="validate",
                ref_id="rec_x", summary="s", created_at=ts))
        sess.commit()


@pytest.fixture()
def client(tmp_path: Path):
    from core.persistence.db_ext import init_db_ext
    from core.analysis.executor import ensure_research_tables
    from api.app import create_app
    db = tmp_path / "q.db"
    init_db_ext(db)
    ensure_research_tables(db)
    _seed(db)
    app = create_app(db_path=db, data_dir=tmp_path / "data")
    with TestClient(app, raise_server_exceptions=False) as c:
        c._db = db  # type: ignore[attr-defined]
        yield c


class TestFiveStatusFilter:
    """A1-1~A1-5 五态筛选+空态+422。"""

    @pytest.mark.parametrize("status,expected", [
        ("active", {"hyp_a", "REGISTERED"}),
        ("supported", {"hyp_d"}),
        ("refuted", {"hyp_b"}),
        ("abandoned", {"hyp_c"}),
    ])
    def test_status_filter_returns_matching_set(
            self, client, status, expected):
        body = client.get("/api/research/hypothesis-cards",
                          params={"status": status}).json()
        ids = {c["hypothesis_id"] for c in body["items"]}
        if "REGISTERED" in expected:
            # register 产物（动态 id）同为 active——允许一个额外动态成员
            extra = ids - (expected - {"REGISTERED"})
            assert len(extra) == 1 and ids - extra == (expected - {"REGISTERED"})
        else:
            assert ids == expected
        assert all(c["status"] == status for c in body["items"])

    def test_superseded_empty_state_200(self, client):
        """superseded 空态：值域是状态机事实（与数据分布无关）——零
        存量仍 200 合法空态（total=0/items=[]/next_cursor=null）。种子
        无 superseded 行；supported 非空筛选由上方 parametrize 承载。"""
        body = client.get("/api/research/hypothesis-cards",
                          params={"status": "superseded"}).json()
        assert body["total"] == 0 and body["items"] == []
        assert body["next_cursor"] is None

    def test_invalid_status_422_structured(self, client):
        r = client.get("/api/research/hypothesis-cards",
                       params={"status": "promoted"})
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert detail["code"] == "RESEARCH_PARAM_INVALID"
        assert detail["detail"]["field"] == "status"
        assert detail["detail"]["value"] == "promoted"
        assert set(detail["detail"]["legal_range"]) == {
            "active", "supported", "refuted", "superseded", "abandoned"}


class TestSearchAndNullSemantics:
    """A1-6~A1-10 q 搜索+三 null 语义。"""

    def test_q_substring_filter(self, client):
        body = client.get("/api/research/hypothesis-cards",
                          params={"q": "动量"}).json()
        # 仅 register 产物（statement 含「动量」）命中——id 动态不断言
        assert body["total"] == 1
        assert len(body["items"]) == 1
        assert "动量" in body["items"][0]["statement"]

    def test_q_percent_literal_not_wildcard(self, client):
        """q=% 按字面命中（LIKE 案义字符转义——'100%' statement 命中，
        不作通配符匹配全部行）。"""
        body = client.get("/api/research/hypothesis-cards",
                          params={"q": "100%"}).json()
        statements = [c["statement"] for c in body["items"]]
        assert statements and all("100%" in s for s in statements)
        total_all = client.get("/api/research/hypothesis-cards").json()
        assert body["total"] < total_all["total"], \
            "q=100% 不得按 % 通配符匹配全部行"

    def test_empty_q_no_filter(self, client):
        all_total = client.get("/api/research/hypothesis-cards").json()
        for empty in ("", "   "):
            body = client.get("/api/research/hypothesis-cards",
                              params={"q": empty}).json()
            assert body["total"] == all_total["total"]

    def test_q_backslash_literal_not_escape_eaten(self, client):
        """q 含 \\（LIKE escape 字符本身）按字面命中——转义实现三
        案义字符（%/_/\\）端点面齐备：statement 含 \\ 的行被 q=\\ 命中，
        \\ 不被当作 escape 前缀吞掉后续字符。"""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from core.persistence.orm.models import ResearchHypothesis
        S = sessionmaker(bind=create_engine(f"sqlite:///{client._db}"))
        with S() as sess:
            for hid, stmt in (("hyp_bs", "趋势\\反转"), ("hyp_bn", "趋势反转")):
                sess.add(ResearchHypothesis(
                    hypothesis_id=hid, conditions="{}",
                    conditions_hash=f"c{hid}", statement=stmt,
                    data_ranges="[]", ranges_hash=f"r{hid}",
                    status="superseded", created_at=_ts(-8),
                    updated_at=_ts(-8)))
            sess.commit()
        body = client.get("/api/research/hypothesis-cards",
                          params={"q": "\\"}).json()
        assert [c["statement"] for c in body["items"]] == ["趋势\\反转"]
        body2 = client.get("/api/research/hypothesis-cards",
                           params={"q": "趋势\\"}).json()
        assert [c["statement"] for c in body2["items"]] == ["趋势\\反转"], \
            "对照行「趋势反转」（无 \\）不得被 \\ 前缀语义吞并命中"

    def test_q_underscore_literal_not_wildcard(self, client):
        """q 含 _ 按字面命中（LIKE 案义字符转义——'_' 不作单字符
        通配符）。对照行 '布林带X收窄'（X 占据 _ 位置）若 _ 被按
        通配符解读会同页命中——字面语义下只命中 '布林带_收窄'。"""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from core.persistence.orm.models import ResearchHypothesis
        S = sessionmaker(bind=create_engine(f"sqlite:///{client._db}"))
        with S() as sess:
            for hid, stmt in (("hyp_us", "布林带_收窄"),
                              ("hyp_ux", "布林带X收窄")):
                sess.add(ResearchHypothesis(
                    hypothesis_id=hid, conditions="{}",
                    conditions_hash=f"c{hid}", statement=stmt,
                    data_ranges="[]", ranges_hash=f"r{hid}",
                    status="superseded", created_at=_ts(-8),
                    updated_at=_ts(-8)))
            sess.commit()
        body = client.get("/api/research/hypothesis-cards",
                          params={"q": "布林带_"}).json()
        assert body["total"] == 1
        assert [c["statement"] for c in body["items"]] == ["布林带_收窄"]

    def test_total_status_q_combined_filter(self, client):
        """total=过滤口径真总数：status+q 双过滤同传取交集（非任一
        满足）——active∩「动量」仅 register 产物一条。"""
        body = client.get("/api/research/hypothesis-cards",
                          params={"status": "active", "q": "动量"}).json()
        assert body["total"] == 1
        assert len(body["items"]) == 1
        # 交集空组合：refuted∩「动量」零命中（total=0 合法空态）
        empty = client.get("/api/research/hypothesis-cards",
                           params={"status": "refuted", "q": "动量"}).json()
        assert empty["total"] == 0 and empty["items"] == []

    def test_card_row_seven_fields(self, client):
        body = client.get("/api/research/hypothesis-cards").json()
        for card in body["items"]:
            assert set(card) == {
                "hypothesis_id", "statement", "status", "validation_count",
                "latest_verdict", "latest_record_id", "recent_activity_at"}

    def test_latest_verdict_takes_newest_and_nulls(self, client):
        """hyp_a 最新记录=fail（now-1d 晚于 now-7d 的 pass）；hyp_c 零
        验证→latest_*=null、count=0；hyp_d 无日志→recent_activity_at
        =null。"""
        by_id = {c["hypothesis_id"]: c
                 for c in client.get("/api/research/hypothesis-cards")
                 .json()["items"]}
        # statement 任务命名原文透传（Scenario「卡片行字段构成」）
        assert by_id["hyp_a"]["statement"] == "RSI 超卖反弹"
        assert by_id["hyp_a"]["latest_verdict"] == "fail"
        assert by_id["hyp_a"]["latest_record_id"] == "rec_a2"
        assert by_id["hyp_a"]["validation_count"] == 2
        assert by_id["hyp_c"]["latest_verdict"] is None
        assert by_id["hyp_c"]["latest_record_id"] is None
        assert by_id["hyp_c"]["validation_count"] == 0
        assert by_id["hyp_d"]["recent_activity_at"] is None


class TestPagingOrderAndAdhoc:
    """A1-11~A1-14 排序/cursor/adhoc/幂等。"""

    def test_order_recent_activity_desc(self, client):
        """recent_activity_at 降序（null 落末位）；now+2d(hyp_b) 首位
        （恒压 register 运行时刻日志——相对化种子后不随日历漂移）。"""
        items = client.get("/api/research/hypothesis-cards").json()["items"]
        assert items[0]["hypothesis_id"] == "hyp_b"
        acts = [c["recent_activity_at"] for c in items
                if c["recent_activity_at"] is not None]
        assert acts == sorted(acts, reverse=True)

    def test_cursor_takes_priority_over_page(self, client):
        page1 = client.get("/api/research/hypothesis-cards",
                           params={"page_size": 2}).json()
        cur = page1["next_cursor"]
        assert cur is not None
        # 两侧同窗 page_size=2（CARD-01 修复后 null 活动时间行 cursor
        # 路径可达，续页剩余行数>2——不同窗的全等是修复前 NULL 漏行
        # 下的侥幸对齐；page=99 若被采纳 offset=196 应空，同窗相等
        # 即证 page 被忽略且续页起点正确）
        by_cursor = client.get("/api/research/hypothesis-cards",
                               params={"cursor": cur, "page": 99,
                                       "page_size": 2}).json()
        direct = client.get("/api/research/hypothesis-cards",
                            params={"page_size": 2, "page": 2}).json()
        assert [c["hypothesis_id"] for c in by_cursor["items"]] == \
               [c["hypothesis_id"] for c in direct["items"]], \
            "cursor 提供时 page 被忽略——与 page=2 起点同结果"

    def test_adhoc_excluded_from_cards_visible_in_records(self, client):
        """adhoc 双端点对照：卡片端点无 adhoc 卡（零父行结构排除）；
        既有 validation-records?hypothesis_id=adhoc 可见。"""
        cards = client.get("/api/research/hypothesis-cards").json()
        assert all(c["hypothesis_id"] != "adhoc" for c in cards["items"])
        recs = client.get("/api/research/validation-records",
                          params={"hypothesis_id": "adhoc"}).json()
        assert any(r["record_id"] == "rec_ad1" for r in recs["records"])

    def test_get_idempotent_and_response_shape(self, client):
        r1 = client.get("/api/research/hypothesis-cards",
                        params={"page_size": 3})
        r2 = client.get("/api/research/hypothesis-cards",
                        params={"page_size": 3})
        assert r1.json() == r2.json()
        body = r1.json()
        assert set(body) == {"items", "total", "page", "page_size",
                             "next_cursor"}
        assert body["next_cursor"] is not None  # 有 items 时非空

    def test_next_cursor_null_on_empty(self, client):
        body = client.get("/api/research/hypothesis-cards",
                          params={"q": "绝不存在的词"}).json()
        assert body == {"items": [], "total": 0, "page": 1,
                        "page_size": 50, "next_cursor": None}

    def test_cursor_traversal_collects_all_no_drift(self, client):
        """契约锚（specs「cursor keyset 续页无漂移」×「无日志行
        recent_activity_at=null 容缺」组合）：next_cursor 驱动遍历
        至终页，收集并集==total 且无重复、无漏行。种子含 hyp_d
        （null 活动时间，DESC 落末位）——null 容缺行在 cursor 路径
        必须同样可达。〔缺陷锚：探针证实实现 keyset 条件 NULL<时间串
        恒假，null 行漏页——本用例按契约断言，修复前红。〕"""
        collected: list[str] = []
        cursor = None
        for _ in range(20):  # 防死循环上限（漂移形态=重复页时不空转）
            params = {"page_size": 2}
            if cursor is not None:
                params["cursor"] = cursor
            body = client.get("/api/research/hypothesis-cards",
                              params=params).json()
            collected.extend(c["hypothesis_id"] for c in body["items"])
            cursor = body["next_cursor"]
            if cursor is None:
                break
        assert len(collected) == len(set(collected)), "续页不得重复行"
        assert len(collected) == 5, \
            "全集 5 行（含 null 活动时间 hyp_d）须全部可达"
        assert {"hyp_a", "hyp_b", "hyp_c", "hyp_d"} <= set(collected), \
            "null 活动时间行 hyp_d 在 cursor 路径同样可达"

    def test_cursor_null_tail_page_no_repeat(self, client):
        """缺陷锚 B 形态（specs「keyset 续页无漂移」×「null 容缺」
        组合的另一表现）：首页末行为 null 活动时间行时，next_cursor
        编码该行（实现现状 str(None)="None|id"），续页按契约必须为
        空（首页已含全集）——不得重复任何已返回行。〔修复前红：
        "None" 与 ISO 时间串比较 '2'<'N' 恒真，续页回到时间流头部
        重复全部正常行。A 形态（NULL 行 cursor 路径漏页）由上方
        遍历用例锚定。〕"""
        first = client.get("/api/research/hypothesis-cards",
                           params={"page_size": 5}).json()
        assert len(first["items"]) == 5, "全集 5 行首页一次取全"
        assert first["next_cursor"] is not None, "items 非空须给续页游标"
        second = client.get("/api/research/hypothesis-cards",
                            params={"cursor": first["next_cursor"],
                                    "page_size": 5}).json()
        assert second["items"] == [], \
            "首页已含全集（末行 null 活动时间）——续页不得返回任何行"
        ids1 = {c["hypothesis_id"] for c in first["items"]}
        ids2 = {c["hypothesis_id"] for c in second["items"]}
        assert not (ids1 & ids2), "已消费行不得重复出现"

    def test_get_no_side_effects_on_tables(self, client):
        """零持久化零副作用（Requirement 正文）：GET 前后三源表行数
        不变（响应等价的幂等断言不排除写库——本用例直接证零写入）。"""
        from sqlalchemy import create_engine, func, select
        from sqlalchemy.orm import sessionmaker
        from core.persistence.orm.models import (
            ResearchHypothesis, ResearchLog, ValidationRecord)
        S = sessionmaker(bind=create_engine(f"sqlite:///{client._db}"))

        def _counts():
            with S() as sess:
                return tuple(
                    sess.scalar(select(func.count()).select_from(m))
                    for m in (ResearchHypothesis, ValidationRecord,
                              ResearchLog))
        before = _counts()
        client.get("/api/research/hypothesis-cards")
        client.get("/api/research/hypothesis-cards",
                   params={"status": "active", "q": "动量"})
        assert _counts() == before

    def test_page_and_size_clamped_to_default(self, client):
        """钳制特征（实现自述「沿 /hypotheses 同款」）：page<1→1、
        page_size 越界→50——非法分页参数不产 422，钳制后回显。"""
        body = client.get("/api/research/hypothesis-cards",
                          params={"page": 0, "page_size": 9999}).json()
        assert body["page"] == 1
        assert body["page_size"] == 50
        body_neg = client.get("/api/research/hypothesis-cards",
                              params={"page": -3, "page_size": 0}).json()
        assert body_neg["page"] == 1 and body_neg["page_size"] == 50

    def test_huge_page_clamped_no_500(self, client):
        """特征锚（CARD-03）：巨 page 钳到 _MAX_PAGE=10^15——修复前
        offset=(page-1)*page_size 超 SQLite 64 位上界，参数绑定
        OverflowError 裸穿 500；钳后 200 空页并回显钳制值（同
        「page<1 钳到 1」边界族，不回退缺省）。"""
        r = client.get("/api/research/hypothesis-cards",
                       params={"page": 10 ** 18, "page_size": 200})
        assert r.status_code == 200, "巨 page 不得 500（offset 溢出防御）"
        body = r.json()
        assert body["items"] == [], "钳后 offset 仍远超全集——空页"
        assert body["page"] == 10 ** 15, "回显钳制值而非原值"

    def test_huge_q_truncated_no_422(self, client):
        """特征锚（CARD-03）：巨 q 截断到 200 字符不产 422——600
        字符唯一串截断后仍不命中任何种子 statement（200 空结果）；
        截断静默（超长精确查询语义放宽为前缀子串，不报错）。"""
        r = client.get("/api/research/hypothesis-cards",
                       params={"q": "巨" * 600})
        assert r.status_code == 200, "巨 q 截断不得 422"
        assert r.json()["total"] == 0


@pytest.fixture()
def sbcards_env(tmp_path: Path):
    """沙盒卡片排除契约库（add-sandbox-records-unified-flow C5）：
    H 两正式验证（最新 fail）+H2 空+策略与合成数据（可跑真实沙盒）。"""
    import numpy as np
    import pandas as pd
    from core.analysis.executor import ensure_research_tables
    from core.persistence.db_ext import init_db_ext
    from core.analysis.hypothesis_service import register_hypothesis
    from core.persistence.repository.strategy import save_strategy
    from core.strategy.dna import (
        ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
        StrategyDNA)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from core.persistence.orm.models import ValidationRecord
    from api.app import create_app

    db = tmp_path / "q.db"
    init_db_ext(db)
    ensure_research_tables(db)
    d = tmp_path / "market"
    d.mkdir()
    rng = np.random.default_rng(11)
    idx = pd.date_range("2022-10-01", periods=2000, freq="4h", tz="UTC",
                        name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 110, 2000))
    pd.DataFrame({"open": close, "high": close + 60, "low": close - 60,
                  "close": close, "volume": rng.uniform(100, 800, 2000),
                  "trades": 40}, index=idx).to_parquet(
        d / "BTCUSDT_4h.parquet")
    dna = StrategyDNA(
        signal_genes=[SignalGene("RSI", {"period": 14},
                                 SignalRole.ENTRY_TRIGGER, "rsi_14",
                                 {"type": "lt", "threshold": 35}),
                      SignalGene("RSI", {"period": 14},
                                 SignalRole.EXIT_TRIGGER, "rsi_14",
                                 {"type": "gt", "threshold": 1e9})],
        logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes())
    save_strategy(db, strategy_id="st_cards", dna_json=dna.to_json(),
                  symbol="BTCUSDT", timeframe="4h")
    conds = {"when_conditions": [
        {"subject": {"indicator": "RSI", "params": {"period": 14}},
         "action": "lt", "target": 35}],
        "then_conditions": [{"subject": "close", "action": "gt",
                             "target": 0}]}
    rng_h = [{"symbol": "BTCUSDT", "timeframe": "4h",
              "start": "2023-01-01T00:00:00Z",
              "end": "2023-02-01T00:00:00Z"}]
    hid = register_hypothesis(
        db, conditions=conds, statement="卡片排除 H",
        data_ranges=rng_h, strategy_params={"stop_loss": 0.05}
    )["hypothesis_id"]
    # H2 独立身份（conditions target 36≠35——register 身份幂等下同
    # conditions+ranges 会返回 H 原 id，卡片断言对象错位）
    hid2 = register_hypothesis(
        db, conditions={
            "when_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 14}},
                 "action": "lt", "target": 36}],
            "then_conditions": [{"subject": "close", "action": "gt",
                                 "target": 0}]},
        statement="卡片排除 H2",
        data_ranges=rng_h, strategy_params={"stop_loss": 0.05}
    )["hypothesis_id"]
    S = sessionmaker(bind=create_engine(f"sqlite:///{db}"))
    with S() as sess:
        for rid, verdict, ts in (("rec_f1", "pass", _ts(-7)),
                                 ("rec_f2", "fail", _ts(-1))):
            sess.add(ValidationRecord(
                record_id=rid, hypothesis_id=hid, strategy_id="st_cards",
                level="isolated_backtest", segmentation="", verdict=verdict,
                range_hash=f"rh_{rid}", params="{}", result="{}",
                created_at=ts))
        sess.commit()
    app = create_app(db_path=db, data_dir=d)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c, db, d, hid, hid2


def _card(client, hid: str) -> dict:
    items = client.get("/api/research/hypothesis-cards",
                       params={"page_size": 200}).json()["items"]
    return next(c for c in items if c["hypothesis_id"] == hid)


def _run_sbx(db: Path, d: Path, hid, n: int = 1) -> list:
    from core.analysis.validation_service import run_sandbox_backtest
    return [run_sandbox_backtest(
        db, d, strategy_id="st_cards", symbol="BTCUSDT", timeframe="4h",
        start="2023-03-01", end="2023-03-10", hypothesis_id=hid)
        for _ in range(n)]


class TestCardsSandboxExclusion:
    """TC-3-1~3-4：主任务卡口径=正式验证（level≠sandbox_backtest）——
    沙盒不推高计数、不占据最新结论位；composite 死值域照现状计入；
    下钻组合查询分页取尽（A6 数据链路）。"""

    def test_cards_three_numbers_unpolluted_by_sandbox(
            self, sbcards_env):
        """TC-3-1：三数字沙盒前后快照一致；recent_activity_at 推进。"""
        client, db, d, hid, _hid2 = sbcards_env
        before = _card(client, hid)
        assert (before["validation_count"], before["latest_verdict"],
                before["latest_record_id"]) == (2, "fail", "rec_f2")
        _run_sbx(db, d, hid, n=3)
        after = _card(client, hid)
        assert after["validation_count"] == 2
        assert after["latest_verdict"] == "fail"
        assert after["latest_record_id"] == "rec_f2"
        # 沙盒日志照常推进活动时间（沙盒可视性走记录流不走卡片）
        assert after["recent_activity_at"] > before["recent_activity_at"]

    def test_cards_sandbox_only_hypothesis_zero_counts(
            self, sbcards_env):
        """TC-3-2：全沙盒假设零计数不捏造（G7）。"""
        client, db, d, _hid, hid2 = sbcards_env
        _run_sbx(db, d, hid2, n=1)
        card = _card(client, hid2)
        assert card["validation_count"] == 0
        assert card["latest_verdict"] is None
        assert card["latest_record_id"] is None
        assert card["recent_activity_at"] is not None  # 沙盒日志照常

    def test_cards_composite_still_counted(self, sbcards_env):
        """TC-3-3：排除面恰 sandbox_backtest 一值——composite 照现状计入。"""
        client, db, d, hid, _hid2 = sbcards_env
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from core.persistence.orm.models import ValidationRecord
        S = sessionmaker(bind=create_engine(f"sqlite:///{db}"))
        with S() as sess:
            sess.add(ValidationRecord(
                record_id="rec_cp1", hypothesis_id=hid,
                strategy_id="st_cards", level="composite",
                segmentation="", verdict="pass", range_hash="rh_cp1",
                params="{}", result="{}", created_at=_ts(-3)))
            sess.commit()
        card = _card(client, hid)
        assert card["validation_count"] == 3  # 2 正式+1 composite

    def test_drill_down_combined_query_exhausts(self, sbcards_env):
        """TC-3-4：?hypothesis_id&level=sandbox_backtest total=全数
        分页取尽无重复无遗漏（A6/V16）。"""
        client, db, d, hid, _hid2 = sbcards_env
        outs = _run_sbx(db, d, hid, n=5)
        ids, offset = set(), 0
        while True:
            body = client.get(
                "/api/research/validation-records",
                params={"hypothesis_id": hid,
                        "level": "sandbox_backtest",
                        "limit": 1, "offset": offset}).json()
            ids.update(x["record_id"] for x in body["records"])
            if not body["has_more"]:
                break
            offset += 1
        assert body["total"] == 5
        assert ids == {o["record_id"] for o in outs}

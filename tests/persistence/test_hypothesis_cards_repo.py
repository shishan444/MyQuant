"""list_hypothesis_cards 仓储级确定断言（add-hypothesis-cards 任务 2，
tester A1 组 L2 用例：A1-15/16/17）。

三表联查取值确定性/排序确定次序/total 口径——ORM 直插确定性种子
（不经 register——created_at 全受控）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.analysis.executor import ensure_research_tables
from core.persistence.db_ext import init_db_ext
from core.persistence.orm.models import (
    ResearchHypothesis, ResearchLog, ValidationRecord)
from core.persistence.repository import research as rr


def _add_hyp(sess, hid, statement, status, created):
    sess.add(ResearchHypothesis(
        hypothesis_id=hid, conditions="{}", conditions_hash=f"c{hid}",
        statement=statement, data_ranges="[]", ranges_hash=f"r{hid}",
        status=status, created_at=created, updated_at=created))


def _add_rec(sess, rid, hid, verdict, created):
    sess.add(ValidationRecord(
        record_id=rid, hypothesis_id=hid, strategy_id=None,
        level="isolated_backtest", segmentation="", verdict=verdict,
        range_hash=f"rh_{rid}", params="{}", result="{}",
        created_at=created))


def _add_log(sess, lid, hid, created):
    sess.add(ResearchLog(
        log_id=lid, hypothesis_id=hid, entry_type="validate",
        ref_id="rec_x", summary="s", created_at=created))


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    ensure_research_tables(d)
    Session = sessionmaker(bind=create_engine(f"sqlite:///{d}"))
    with Session() as sess:
        _add_hyp(sess, "hyp_p", "P 假设", "active", "2026-10-01T00:00:00Z")
        _add_hyp(sess, "hyp_q", "Q 假设", "refuted", "2026-10-02T00:00:00Z")
        # A1-15 乱序插入验证记录（created_at 降序取一与插入序无关）
        _add_rec(sess, "rec_p_new", "hyp_p", "fail", "2026-10-20T00:00:00Z")
        _add_rec(sess, "rec_p_old", "hyp_p", "pass", "2026-10-05T00:00:00Z")
        # A1-16 同活动时间双假设（次序键 hypothesis_id 保证确定次序）
        _add_log(sess, "lg_p", "hyp_p", "2026-10-20T00:00:00Z")
        _add_log(sess, "lg_q", "hyp_q", "2026-10-20T00:00:00Z")
        sess.commit()
    return d


class TestCardsDeterminism:
    """A1-15/16/17：联查取值/排序/total 口径。"""

    def test_latest_from_newest_created_at(self, db):
        """A1-15：最新验证结论取 created_at 最新行（乱序插入不扰取值）——
        rec_p_new(fail, 10-20) 晚于 rec_p_old(pass, 10-05)。"""
        cards = {c["hypothesis_id"]: c
                 for c in rr.list_hypothesis_cards(db)}
        assert cards["hyp_p"]["latest_verdict"] == "fail"
        assert cards["hyp_p"]["latest_record_id"] == "rec_p_new"
        assert cards["hyp_p"]["validation_count"] == 2
        # 无验证假设缺失不捏造
        assert cards["hyp_q"]["latest_verdict"] is None
        assert cards["hyp_q"]["validation_count"] == 0

    def test_tie_broken_by_hypothesis_id(self, db):
        """A1-16：同 recent_activity_at 双假设→hypothesis_id 次序确定
        （desc 次序键取字典序降序——hyp_q 后于 hyp_p）。"""
        cards = rr.list_hypothesis_cards(db)
        assert [c["hypothesis_id"] for c in cards] == ["hyp_q", "hyp_p"]

    def test_q_escape_literal_semantics_unit(self, db):
        """④属性补强+⑤层级补全：q 案义三字符（%/_/\\）字面语义在
        仓储层 unit 直构断言（不经 HTTP 层——转义是 _cards_filters
        纯计算面）。对照行 AxB（X 占据 _ 位置）：q='A_B' 字面只命中
        A_B，若 _ 按通配符解读则 AxB 同页命中。"""
        with sessionmaker(bind=create_engine(f"sqlite:///{db}"))() as sess:
            for hid, stmt in (("hyp_pc", "A%B"), ("hyp_us", "A_B"),
                              ("hyp_bs", "A\\B"), ("hyp_xx", "AxB"),
                              ("hyp_pl", "普通")):
                sess.add(ResearchHypothesis(
                    hypothesis_id=hid, conditions="{}",
                    conditions_hash=f"c{hid}", statement=stmt,
                    data_ranges="[]", ranges_hash=f"r{hid}",
                    status="active", created_at="2026-10-01T00:00:00Z",
                    updated_at="2026-10-01T00:00:00Z"))
            sess.commit()
        got = {c["hypothesis_id"]
               for c in rr.list_hypothesis_cards(db, q="%")}
        assert got == {"hyp_pc"}, "% 字面只命中含 % 的行"
        got = {c["hypothesis_id"]
               for c in rr.list_hypothesis_cards(db, q="_")}
        assert got == {"hyp_us"}, "_ 字面只命中含 _ 的行（AxB 不命中）"
        got = {c["hypothesis_id"]
               for c in rr.list_hypothesis_cards(db, q="\\")}
        assert got == {"hyp_bs"}, "\\（escape 字符本身）字面只命中含 \\ 的行"
        got = {c["hypothesis_id"]
               for c in rr.list_hypothesis_cards(db, q="A_B")}
        assert got == {"hyp_us"}, "组合子串含 _ 仍字面语义（对照行 AxB 排除）"

    def test_total_is_filter_true_count(self, db):
        """A1-17：total=过滤口径真总数（跨页收集对账）。"""
        assert rr.count_hypothesis_cards(db) == 2
        assert rr.count_hypothesis_cards(db, status="active") == 1
        assert rr.count_hypothesis_cards(db, q="Q") == 1
        # status+q 双过滤同传取交集（任务 2 完成标准⑥——非任一满足）
        assert rr.count_hypothesis_cards(db, status="active", q="Q") == 0
        assert rr.count_hypothesis_cards(db, status="refuted", q="Q") == 1
        # 跨页收集：page_size=1 两页合计==total（cursor 续页时 offset=0
        # ——与端点语义一致，keyset 本身承担翻页位移）
        page1 = rr.list_hypothesis_cards(db, limit=1)
        page2 = rr.list_hypothesis_cards(
            db, offset=0, limit=1,
            cursor=(page1[0]["recent_activity_at"],
                    page1[0]["hypothesis_id"]))
        assert len(page1) + len(page2) == rr.count_hypothesis_cards(db)

"""判定区间解析器与分页迭代器特征测试（修复期特征网：记录现状行为）。

isolation._iter_ranges 是 judged_ranges_union 的解析底座（data_ranges
JSON → (start, end, 原始条目串) 三元组列表），容错分支（非法 JSON/
非列表/条目缺键/时间不可解析）此前零覆盖。judged_ranges_union 公开面
由 test_screen_lineage.py 承接，本文件补解析层分支。

CARD-01 修复后新增：iter_all_pages 分页迭代器（repository 升序 +
limit 截断的首屏语义之上取全量）与判定并集超页可见性——正确行为
= 全量语义（四消费方同模式回归锚之一）。
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.isolation import _iter_ranges, iter_all_pages, judged_ranges_union


class TestIterRanges:
    def test_空输入_None与空串返回空(self) -> None:
        assert _iter_ranges(None) == []
        assert _iter_ranges("") == []

    def test_非法JSON_返回空(self) -> None:
        assert _iter_ranges("{not json") == []

    def test_JSON非列表_返回空(self) -> None:
        # 合法 JSON 但顶层不是列表（如 dict/str）→ 空结果
        assert _iter_ranges('{"start": 1}') == []
        assert _iter_ranges('"text"') == []

    def test_列表含非dict条目_跳过(self) -> None:
        raw = '[1, "x", {"start": "2024-01-01T00:00:00Z", "end": "2024-01-02T00:00:00Z"}]'
        out = _iter_ranges(raw)
        assert len(out) == 1
        assert out[0][0].year == 2024

    def test_条目缺键_跳过(self) -> None:
        raw = '[{"start": "2024-01-01T00:00:00Z"}, {"end": "2024-01-02T00:00:00Z"}, {}]'
        assert _iter_ranges(raw) == []

    def test_条目时间不可解析_跳过(self) -> None:
        raw = '[{"start": "not-a-time", "end": "2024-01-02T00:00:00Z"}]'
        assert _iter_ranges(raw) == []

    def test_正常条目_解析为UTC时间对与原始串(self) -> None:
        raw = ('[{"start": "2024-01-01T00:00:00Z", "end": "2024-01-02T00:00:00Z",'
               ' "symbol": "BTCUSDT"}]')
        out = _iter_ranges(raw)
        assert len(out) == 1
        start, end, origin = out[0]
        assert (start.year, start.month, start.day) == (2024, 1, 1)
        assert (end.year, end.month, end.day) == (2024, 1, 2)
        assert start.tzinfo is not None and end.tzinfo is not None
        # 第三元 = 条目 dict 的 str()（repr 形，保留额外键——并集去重
        # 与溯源用）
        assert origin.startswith("{'start'")
        assert "'symbol': 'BTCUSDT'" in origin

    def test_naive时间戳_补UTC(self) -> None:
        out = _iter_ranges('[{"start": "2024-01-01T00:00:00", "end": "2024-01-02T00:00:00"}]')
        start, end, _ = out[0]
        assert start.utcoffset().total_seconds() == 0
        assert end.utcoffset().total_seconds() == 0

    def test_Z后缀与加号偏移_统一为可比较时间(self) -> None:
        out = _iter_ranges('[{"start": "2024-01-01T00:00:00+00:00", "end": "2024-01-02T00:00:00Z"}]')
        start, end, _ = out[0]
        assert (end - start).total_seconds() == 86400


class TestIterAllPages:
    """CARD-01 分页迭代器本体：offset 递增翻页、不足一页即止。"""

    def test_不足一页_单次调用即止(self) -> None:
        calls = []

        def fetch(off, lim):
            calls.append((off, lim))
            return [{"i": i} for i in range(off, off + 7)]

        out = list(iter_all_pages(fetch, page_size=200))
        assert [r["i"] for r in out] == list(range(7))
        assert calls == [(0, 200)]

    def test_恰好整页_再探一空页止(self) -> None:
        calls = []

        def fetch(off, lim):
            calls.append((off, lim))
            return [{"i": i} for i in range(off, min(off + lim, 200))]

        out = list(iter_all_pages(fetch, page_size=200))
        assert len(out) == 200
        assert calls == [(0, 200), (200, 200)]

    def test_多页拼接保序(self) -> None:
        global_rows = [{"i": i} for i in range(450)]

        def fetch(off, lim):
            return global_rows[off:off + lim]

        out = list(iter_all_pages(fetch, page_size=200))
        assert [r["i"] for r in out] == [r["i"] for r in global_rows]
        assert len(out) == 450

    def test_空结果_立即止(self) -> None:
        def fetch(off, lim):
            return []

        assert list(iter_all_pages(fetch)) == []

    def test_offset按页宽递增(self) -> None:
        calls = []

        def fetch(off, lim):
            calls.append((off, lim))
            return [] if off > 0 else [{"i": i} for i in range(off, off + lim)]

        list(iter_all_pages(fetch, page_size=50))
        assert calls == [(0, 50), (50, 50)]


@pytest.fixture()
def _db(tmp_path: Path) -> Path:
    from core.analysis.executor import ensure_research_tables
    from core.persistence.db_ext import init_db_ext
    d = tmp_path / "p.db"
    init_db_ext(d)
    ensure_research_tables(d)
    return d


def _insert_logs(db: Path, n: int, *, entry_type: str, hypothesis_id=None,
                 data_ranges_of=None, summary_of=None) -> None:
    """直插 n 条日志，created_at 严格递增（升序位次=写入位次）。"""
    from sqlalchemy import insert
    from core.persistence.orm.models import ResearchLog
    from core.persistence.repository.dataset_meta import _engine_for
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    with _engine_for(db).begin() as conn:
        for i in range(n):
            conn.execute(insert(ResearchLog).values(
                log_id=f"log_{i:04d}", hypothesis_id=hypothesis_id,
                entry_type=entry_type, ref_id=None,
                data_ranges=data_ranges_of(i) if data_ranges_of else None,
                summary=summary_of(i) if summary_of else None,
                created_at=(base + timedelta(seconds=i)).isoformat()))


class TestJudgedRangesUnionPagination:
    """CARD-01 链路 A：判定并集分页取全量（原 limit=200 单页截断）。"""

    def test_超页日志末尾区间_仍入判定并集(self, _db) -> None:
        db = _db
        # 250 条日志各带唯一单日区间；第 250 条（升序末尾）在旧单页
        # 截断（limit=200）之外
        day0 = datetime(2024, 1, 1, tzinfo=timezone.utc)

        def ranges_of(i: int) -> str:
            s = day0 + timedelta(days=i)
            return json.dumps([{
                "symbol": "BTCUSDT", "timeframe": "4h",
                "start": s.isoformat(), "end": (s + timedelta(hours=1)).isoformat()}])

        _insert_logs(db, 250, entry_type="validate", hypothesis_id="hyp_u",
                     data_ranges_of=ranges_of)

        union = judged_ranges_union(db, "hyp_u")

        # 全量：250 个互异区间全部入并集（截断则只余前 200）
        assert len(union) == 250
        # 末尾条目（第 250 天）可见——超页可见性锚
        last_start = (day0 + timedelta(days=249)).isoformat()
        assert any(r["start"] == last_start for r in union)
        # 输出按 start 升序（既有排序契约不因分页改变）
        starts = [r["start"] for r in union]
        assert starts == sorted(starts)

"""判定区间解析器特征测试（修复期特征网：记录现状行为）。

isolation._iter_ranges 是 judged_ranges_union 的解析底座（data_ranges
JSON → (start, end, 原始条目串) 三元组列表），容错分支（非法 JSON/
非列表/条目缺键/时间不可解析）此前零覆盖。judged_ranges_union 公开面
由 test_screen_lineage.py 承接，本文件补解析层分支。
"""

from datetime import timezone

import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.isolation import _iter_ranges


def _utc(*ymd) -> timezone:
    return timezone.utc


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

"""分析结果三要素构造器特征测试（修复期特征网：记录现状行为）。

result.build_result 是全部模板出口的统一构造器（三要素强制：样本量/
置信/数据区间）。既有覆盖（test_condition_stats.py::TestBuildResult-
ThreeElements）缺以下分支，本文件补齐并锚定 AnalysisResult.to_dict
（此前零调用）与默认值语义。
"""

import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.result import (
    MIN_SAMPLES_DEFAULT,
    AnalysisResult,
    build_result,
)

RANGES = [{"start": "2024-01-01T00:00:00Z", "end": "2024-02-01T00:00:00Z"}]


class TestBuildResultGates:
    def test_template空_拒绝(self) -> None:
        with pytest.raises(ValueError, match="template 必填"):
            build_result("", sample_size=100,
                         confidence={"method": "m", "interval": [0, 1]},
                         data_ranges=RANGES)

    def test_sample_size非整数_拒绝(self) -> None:
        # isinstance(int) 检查：float / str 一律拒（防 100.0 静默放行）。
        # CARD-10 拆分：类型非法属编程错误保持 ValueError（match 锚
        # 随收编改字——低于下限已转 422 走 ResearchError）
        for bad in (100.5, "100"):
            with pytest.raises(ValueError, match="须为 int"):
                build_result("t", sample_size=bad,
                             confidence={"method": "m", "interval": [0, 1]},
                             data_ranges=RANGES)

    def test_confidence带level_通过(self) -> None:
        r = build_result("t", sample_size=100,
                         confidence={"method": "bonferroni", "level": 0.05},
                         data_ranges=RANGES)
        assert r.confidence == {"method": "bonferroni", "level": 0.05}

    def test_confidence带p_value_通过(self) -> None:
        r = build_result("t", sample_size=100,
                         confidence={"method": "t_test", "p_value": 0.03},
                         data_ranges=RANGES)
        assert r.confidence["p_value"] == 0.03

    def test_confidence缺method_拒绝(self) -> None:
        with pytest.raises(ValueError, match="method"):
            build_result("t", sample_size=100,
                         confidence={"interval": [0, 1]},
                         data_ranges=RANGES)

    def test_min_samples自定义_两侧(self) -> None:
        kwargs = dict(confidence={"method": "m", "interval": [0, 1]},
                      data_ranges=RANGES)
        # 下限放宽后 10 可构造
        assert build_result("t", sample_size=10, min_samples=5,
                            **kwargs).sample_size == 10
        # 下限收紧后 100 不可构造（CARD-10 收编：业务拒绝 422）
        from core.analysis.hypothesis_service import ResearchError
        with pytest.raises(ResearchError, match="小样本") as ei:
            build_result("t", sample_size=100, min_samples=200, **kwargs)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["min_samples"] == 200

    def test_缺省下限为30(self) -> None:
        assert MIN_SAMPLES_DEFAULT == 30


class TestAnalysisResultPayload:
    def test_缺省_payload空_hypothesis_none(self) -> None:
        r = build_result("t", sample_size=100,
                         confidence={"method": "m", "interval": [0, 1]},
                         data_ranges=RANGES)
        assert r.payload == {}
        assert r.hypothesis_id is None

    def test_显式_payload与hypothesis透传(self) -> None:
        r = build_result("t", sample_size=100,
                         confidence={"method": "m", "interval": [0, 1]},
                         data_ranges=RANGES,
                         payload={"hit_rate": 0.5}, hypothesis_id="hyp_1")
        assert r.payload == {"hit_rate": 0.5}
        assert r.hypothesis_id == "hyp_1"

    def test_to_dict六键完整值对应(self) -> None:
        r = build_result("t", sample_size=100,
                         confidence={"method": "m", "interval": [0, 1]},
                         data_ranges=RANGES,
                         payload={"k": 1}, hypothesis_id="hyp_2")
        d = r.to_dict()
        assert d == {
            "template": "t",
            "sample_size": 100,
            "confidence": {"method": "m", "interval": [0, 1]},
            "data_ranges": RANGES,
            "payload": {"k": 1},
            "hypothesis_id": "hyp_2",
        }

    def test_to_dict缺省字段序列化(self) -> None:
        r = build_result("t", sample_size=100,
                         confidence={"method": "m", "level": 0.05},
                         data_ranges=RANGES)
        d = r.to_dict()
        assert d["payload"] == {}
        assert d["hypothesis_id"] is None

    def test_dataclass直构合法_与build_result产物同构(self) -> None:
        # AnalysisResult 是 dataclass：绕过 build_result 直构不设防
        #（守卫职责在 build_result——现状特征）
        direct = AnalysisResult(
            template="t", sample_size=1,
            confidence={"method": "m", "interval": [0, 1]},
            data_ranges=RANGES)
        assert direct.sample_size == 1
        assert set(direct.to_dict()) == {
            "template", "sample_size", "confidence", "data_ranges",
            "payload", "hypothesis_id"}

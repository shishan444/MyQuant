"""event_policy 权威变换锚：策略形制校验归一与三模式事件语义。

trigger-policy 能力的纯函数单元锚（卡1）——策略登记/翻译/五链消费
的语义底座在此定标：① validate_policy 归一契约（缺省/显式 none →
None，有效形原样，非法形 422 附合法枚举与参数要求）；② mask_to_events
三模式人工核算样例（连续段/间隔段/全 True/全 False/空，抑制窗
N 与 N±1 两侧）；③ 属性锚（确定性逐字节一致、严格升序、计数
上界链 first_true ≤ 段数 ≤ none、suppress_bars ≤ ceil(True 数/N)）。
跨链一致性（五消费面同源）归 test_event_policy_cross_chain.py
（8.2），本文件只锚纯函数本体。
"""

import numpy as np
import pytest

from core.analysis.event_policy import mask_to_events, validate_policy
from core.analysis.hypothesis_service import ResearchError


class TestValidatePolicy归一契约:
    def test_缺省_None_归一(self) -> None:
        assert validate_policy(None) is None

    def test_显式_none_与缺省同归一(self) -> None:
        assert validate_policy({"mode": "none"}) is None

    def test_first_true_原样归一(self) -> None:
        assert validate_policy({"mode": "first_true"}) == {"mode": "first_true"}

    def test_suppress_bars_归一保留_bars(self) -> None:
        assert validate_policy({"mode": "suppress_bars", "bars": 48}) == {
            "mode": "suppress_bars", "bars": 48}

    def test_bars_为一_合法下界(self) -> None:
        assert validate_policy({"mode": "suppress_bars", "bars": 1}) == {
            "mode": "suppress_bars", "bars": 1}


class TestValidatePolicy非法拒绝:
    """非法形 422 RESEARCH_PARAM_INVALID 附合法枚举与参数要求（错误即文档）。"""

    @pytest.mark.parametrize("bad", [
        "first_true",          # 字符串形
        ["first_true"],        # 列表形
        48,                    # 数值形
    ])
    def test_非对象形拒绝(self, bad) -> None:
        with pytest.raises(ResearchError) as ei:
            validate_policy(bad)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.status == 422

    def test_mode_枚举外拒绝附合法枚举(self) -> None:
        with pytest.raises(ResearchError) as ei:
            validate_policy({"mode": "cooldown"})
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["legal_range"] == "none|first_true|suppress_bars"

    def test_mode_缺失拒绝(self) -> None:
        with pytest.raises(ResearchError):
            validate_policy({"bars": 48})

    def test_first_true_携带多余键拒绝(self) -> None:
        with pytest.raises(ResearchError):
            validate_policy({"mode": "first_true", "bars": 4})

    def test_none_携带多余键拒绝(self) -> None:
        with pytest.raises(ResearchError):
            validate_policy({"mode": "none", "bars": 4})

    def test_未知多余键拒绝(self) -> None:
        with pytest.raises(ResearchError):
            validate_policy({"mode": "first_true", "extra": 1})

    def test_suppress_bars_缺_bars_拒绝(self) -> None:
        with pytest.raises(ResearchError):
            validate_policy({"mode": "suppress_bars"})

    @pytest.mark.parametrize("bad_bars", [0, -1, True, False, "48", 48.0, None])
    def test_bars_非正整数拒绝_含_bool_陷阱(self, bad_bars) -> None:
        with pytest.raises(ResearchError) as ei:
            validate_policy({"mode": "suppress_bars", "bars": bad_bars})
        assert ei.value.code == "RESEARCH_PARAM_INVALID"


# ---------------------------------------------------------------- 掩码样例

# 连续段与间隔段混合：段 [0,3)、{5}、段 [8,10)、{14}
_MASK_MIXED = np.array(
    [1, 1, 1, 0, 0, 1, 0, 0, 1, 1, 0, 0, 0, 0, 1], dtype=bool)
_MASK_ALL_TRUE = np.ones(12, dtype=bool)
_MASK_ALL_FALSE = np.zeros(12, dtype=bool)


class TestMaskToEvents三模式语义:
    def test_none_全部_True_位置_与直译逐字节等价(self) -> None:
        events = mask_to_events(_MASK_MIXED, None)
        assert events.tolist() == [0, 1, 2, 5, 8, 9, 14]
        assert np.array_equal(events, np.flatnonzero(_MASK_MIXED))

    def test_none_显式_mode_同缺省(self) -> None:
        assert np.array_equal(
            mask_to_events(_MASK_MIXED, {"mode": "none"}),
            mask_to_events(_MASK_MIXED, None))

    def test_first_true_极大连续段首根(self) -> None:
        events = mask_to_events(_MASK_MIXED, {"mode": "first_true"})
        assert events.tolist() == [0, 5, 8, 14]

    def test_first_true_第零根段首(self) -> None:
        assert mask_to_events(
            np.array([1, 1, 0, 1]), {"mode": "first_true"}).tolist() == [0, 3]

    def test_first_true_全_True_单事件(self) -> None:
        assert mask_to_events(
            _MASK_ALL_TRUE, {"mode": "first_true"}).tolist() == [0]

    def test_first_true_全_False_零事件(self) -> None:
        assert mask_to_events(
            _MASK_ALL_FALSE, {"mode": "first_true"}).tolist() == []

    def test_空掩码_零事件_三模式(self) -> None:
        empty = np.array([], dtype=bool)
        for policy in (None, {"mode": "first_true"},
                       {"mode": "suppress_bars", "bars": 3}):
            assert mask_to_events(empty, policy).tolist() == []

    def test_suppress_bars_间隔恰_N_产生新事件(self) -> None:
        # 位置 0 与 3：差值 3 = bars → 新事件（间隔不变量下界）
        events = mask_to_events(
            np.array([1, 0, 0, 1]), {"mode": "suppress_bars", "bars": 3})
        assert events.tolist() == [0, 3]

    def test_suppress_bars_间隔_N_减一_抑制(self) -> None:
        # 位置 0 与 2：差值 2 < bars=3 → 合并为单事件
        events = mask_to_events(
            np.array([1, 0, 1]), {"mode": "suppress_bars", "bars": 3})
        assert events.tolist() == [0]

    def test_suppress_bars_连续段_按事件间距产出(self) -> None:
        # 全 True、bars=3：事件 0 后 1、2 抑制，3 与 0 差值 3 → 新事件
        events = mask_to_events(
            _MASK_ALL_TRUE, {"mode": "suppress_bars", "bars": 3})
        assert events.tolist() == [0, 3, 6, 9]

    def test_suppress_bars_混合掩码_跨段抑制(self) -> None:
        # 事件 0 → 5 差值 5 ≥ 4 事件 → 8 差值 3 < 4 抑制（跨段）→
        # 9 差值 4 ≥ 4 事件（段内第二根）→ 14 差值 5 ≥ 4 事件
        events = mask_to_events(_MASK_MIXED, {"mode": "suppress_bars", "bars": 4})
        assert events.tolist() == [0, 5, 9, 14]

    def test_suppress_bars_首个_True_无条件事件(self) -> None:
        events = mask_to_events(
            np.array([0, 0, 0, 1, 1]), {"mode": "suppress_bars", "bars": 2})
        assert events.tolist() == [3]


class TestMaskToEvents属性锚:
    """纯函数不变量：确定性、升序互异、计数上界链。"""

    @pytest.mark.parametrize("policy", [
        None, {"mode": "first_true"}, {"mode": "suppress_bars", "bars": 3},
    ])
    def test_同输入重复调用逐字节一致(self, policy) -> None:
        rng = np.random.default_rng(42)
        mask = rng.random(500) > 0.55
        first = mask_to_events(mask, policy)
        for _ in range(3):
            assert mask_to_events(mask, policy).tobytes() == first.tobytes()

    @pytest.mark.parametrize("policy", [
        None, {"mode": "first_true"}, {"mode": "suppress_bars", "bars": 7},
    ])
    def test_事件位置严格升序互异(self, policy) -> None:
        rng = np.random.default_rng(7)
        mask = rng.random(300) > 0.4
        events = mask_to_events(mask, policy)
        assert np.all(np.diff(events) > 0)

    def test_输出_dtype_为_int64(self) -> None:
        for policy in (None, {"mode": "first_true"},
                       {"mode": "suppress_bars", "bars": 3}):
            assert mask_to_events(_MASK_MIXED, policy).dtype == np.int64

    def test_计数上界链_first_true_不超过段数与_True_数(self) -> None:
        rng = np.random.default_rng(2026)
        mask = rng.random(400) > 0.35
        n_true = int(mask.sum())
        first = len(mask_to_events(mask, {"mode": "first_true"}))
        none = len(mask_to_events(mask, None))
        # first_true ≤ True 段数 ≤ True 数 = none 计数
        assert first <= none == n_true

    def test_计数上界_suppress_bars_双重上界(self) -> None:
        # 事件位置轴间隔 ≥ bars 的推论：事件数 ≤ floor((L-1)/N)+1；
        # 且事件必为 True 子集：事件数 ≤ True 数。稀疏 True 时长度轴
        # 上界生效、连续 True 时 True 数轴生效（原 ceil(T/N) 公式隐含
        # True 连续假设，稀疏形态不成立——design 笔误修正，specs 间隔
        # 不变量为权威锚）。
        rng = np.random.default_rng(2026)
        mask = rng.random(400) > 0.35
        n_true = int(mask.sum())
        length = len(mask)
        for bars in (1, 3, 10):
            n_events = len(mask_to_events(
                mask, {"mode": "suppress_bars", "bars": bars}))
            assert n_events <= (length - 1) // bars + 1
            assert n_events <= n_true

    def test_随机掩码_suppress_bars_间隔不变量(self) -> None:
        rng = np.random.default_rng(99)
        mask = rng.random(1000) > 0.3
        bars = 5
        events = mask_to_events(mask, {"mode": "suppress_bars", "bars": bars})
        assert np.all(np.diff(events) >= bars)

"""signal_sources 条目结构校验红锚（signal-sources-structure-gate 0.1）。

契约出处（design 卡 V，2026-10-19 方案放行）：validate_dna 新增
signal_sources 最小结构判据——顶层须 list（None 合法退化）；条目
须 dict；type 须属 {formula, model, rule} 白名单（缺 type 键同拒）；
formula/model 须带 ref（rule 豁免）。消息模板（逐字节契约）：
- 顶层非 list：``signal_sources must be a list, got {类型名}``
- R-1a 非 dict 条目：``signal_sources[{i}] must be a dict, got {类型名}``
- R-1b 白名单外 type：``signal_sources[{i}]: type must be one of
  ('formula', 'model', 'rule'), got {值!r}``
- R-1c 缺 ref：``signal_sources[{i}]: type {type!r} requires 'ref'``

红锚语义：拒绝侧（AC-1/2/3）在判据落地前为红——现状四种坏形态
分别是静默放行（is_valid=True）或 TypeError 炸穿；护栏（AC-4）双态
绿（判据落地前后都绿，防误伤存量的报警面）。断言全部从 design 卡 V
推导（不从实现反推）。

骨架纪律：合法骨架（ENTRY+EXIT 基因齐全+缺省 risk_genes）保证
entry/exit/值域判据全绿，畸形只放 signal_sources——单错误面定位，
拒绝归因不串位。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

from core.strategy.dna import (
    RiskGenes, SignalGene, SignalRole, StrategyDNA,
)
from core.strategy.validator import validate_dna


def _exit_gene() -> SignalGene:
    """出场基因（对齐库存插件源策略实测形态：RSI cross_above 50）。"""
    return SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                      "rsi", {"type": "cross_above", "threshold": 50.0})


def _entry_gene() -> SignalGene:
    return SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                      "rsi", {"type": "lt", "threshold": 35})


def _skeleton_dna(signal_sources) -> StrategyDNA:
    """合法骨架（entry/exit 齐全+缺省值域）——畸形只放 signal_sources。"""
    return StrategyDNA(
        signal_genes=[_entry_gene(), _exit_gene()],
        signal_sources=signal_sources,
        risk_genes=RiskGenes(),
    )


# ===========================================================================
# AC-1：type 白名单判据（拒绝侧——提交时红：现状静默放行）
# ===========================================================================

class TestTypeWhitelistReject:
    """白名单外 type（拼错/大小写/空串/缺键）→ is_valid=False+消息点名。"""

    @pytest.mark.parametrize(
        ("stype", "rendered"),
        [
            pytest.param("fomula", "'fomula'", id="拼错型fomula"),
            pytest.param("Formula", "'Formula'", id="大小写变体"),
            pytest.param("", "''", id="空串"),
            pytest.param(None, "None", id="缺type键(got None)"),
        ],
    )
    def test_bad_type_rejected_with_named_value(self, stype, rendered):
        """白名单外 type（含缺键）→ 拒绝+D-5 点名（序号子串+值带引号
        渲染——``got 'fomula'`` 而非 ``got fomula``，repr 形制）。"""
        entry = {"ref": "fx_demo"} if stype is not None else {}
        # 缺 type 键形态：条目无 type；其余形态：type 键取坏值
        source = dict(entry) if stype is None else {"type": stype}
        result = validate_dna(_skeleton_dna([source]))
        assert not result.is_valid, (
            f"白名单外 type={stype!r} 被静默放行（判据未生效）："
            f"errors={result.errors}")
        named = [e for e in result.errors if "signal_sources[0]" in e]
        assert named, f"消息未点名条目序号：errors={result.errors}"
        assert f"got {rendered}" in named[0], (
            f"消息未按 repr 形制点名实际值（期望含 got {rendered}）："
            f"{named[0]}")

    def test_whitelist_message_literal(self):
        """R-1b 消息逐字节锚：白名单三元组渲染形制（tuple 字面量）。"""
        result = validate_dna(
            _skeleton_dna([{"type": "fomula"}]))
        assert ("signal_sources[0]: type must be one of "
                "('formula', 'model', 'rule'), got 'fomula'") in result.errors

    @pytest.mark.parametrize(
        "stype", ["formula", "model", "rule"], ids=["formula", "model", "rule"])
    def test_three_legal_types_pass(self, stype):
        """三值正向锚（绿态护栏）：白名单三值各自骨架齐全时放行——
        防白名单实现漏值（判据落地前后都应绿）。"""
        source = ({"type": stype, "ref": "fx_demo"}
                  if stype != "rule" else {"type": "rule"})
        result = validate_dna(_skeleton_dna([source]))
        assert result.is_valid, result.errors

    def test_elif_single_message_semantics(self):
        """elif 单报锚：同一条目「白名单外 type+缺 ref」→ 只产出 R-1b
        一条（b 拒后不查 c）——每条目至多一条结构判据消息。"""
        result = validate_dna(
            _skeleton_dna([{"type": "fomula"}]))
        ss_errors = [e for e in result.errors if "signal_sources" in e]
        assert len(ss_errors) == 1, (
            f"单条目产出多条结构消息（elif 语义破坏）：{ss_errors}")
        assert "requires 'ref'" not in "".join(ss_errors)

    def test_index_points_at_actual_position(self):
        """序号正确性锚：三条列表 [好, 坏, 好] → 点名 [1]，[0]/[2]
        不出现在任何 errors。"""
        result = validate_dna(_skeleton_dna([
            {"type": "rule"},
            {"type": "fomula"},
            {"type": "formula", "ref": "fx_demo"},
        ]))
        assert not result.is_valid
        assert any("signal_sources[1]" in e for e in result.errors), \
            result.errors
        assert not any("signal_sources[0]" in e for e in result.errors), \
            result.errors
        assert not any("signal_sources[2]" in e for e in result.errors), \
            result.errors


# ===========================================================================
# AC-2：非 dict 条目与顶层形制（拒绝侧——提交时红：str 放行/int 炸穿）
# ===========================================================================

class TestNonDictEntryAndTopLevelReject:
    """非 dict 条目与顶层非 list → 拒绝+类型名点名，校验不抛裸异常。"""

    @pytest.mark.parametrize(
        ("bad", "type_name"),
        [
            pytest.param("x", "str", id="str条目"),
            pytest.param(None, "NoneType", id="null条目"),
            pytest.param(5, "int", id="int条目"),
        ],
    )
    @pytest.mark.parametrize("position", [0, 1], ids=["位置0", "位置1"])
    def test_non_dict_entry_rejected_with_type_name(
            self, bad, type_name, position):
        """非 dict 条目（JSON null/字符串/数值）× 位置 → R-1a 消息
        点名序号与实际类型名。"""
        sources = [{"type": "rule"}, {"type": "rule"}]
        sources[position] = bad
        result = validate_dna(_skeleton_dna(sources))
        assert not result.is_valid, (
            f"非 dict 条目 {bad!r} 被静默放行：errors={result.errors}")
        expected = f"signal_sources[{position}] must be a dict, got {type_name}"
        assert expected in result.errors, (
            f"R-1a 消息缺失或形制偏差（期望 {expected!r}）：{result.errors}")

    def test_top_level_str_rejected(self):
        """顶层非 list（str）→ 顶层消息点名实际类型；不再迭代出字符
        怪消息。——现状放行（字符被 isinstance 过滤），红锚断言拒绝。"""
        result = validate_dna(_skeleton_dna("abc"))
        assert not result.is_valid, (
            "顶层 str 形态被静默放行（判据未生效）")
        assert ("signal_sources must be a list, got str"
                in result.errors), result.errors

    def test_top_level_int_no_crash(self):
        """顶层非 list（int）→ 不抛未包装异常（现状 TypeError 炸穿
        ——无 ENTRY 基因时 _entry_plugin_sources 迭代崩溃）+顶层消息
        拒绝。——AC-7「全程无 500」的判据面前置。"""
        dna = StrategyDNA(
            signal_genes=[_exit_gene()],  # 无 ENTRY 基因：现状触发炸穿
            signal_sources=5,
            risk_genes=RiskGenes(),
        )
        result = validate_dna(dna)  # 现状此行 TypeError → 测试红
        assert not result.is_valid
        assert ("signal_sources must be a list, got int"
                in result.errors), result.errors

    @pytest.mark.parametrize(
        ("sources", "expected_msg"),
        [
            pytest.param(["x", None],
                         "signal_sources[0] must be a dict, got str",
                         id="直构通道"),
        ],
    )
    def test_from_json_channel_same_verdict(self, sources, expected_msg):
        """构造通道双证明：from_json（直改库→读库的原样透传通道）
        与直构同判同消息——dna.py from_json 对 signal_sources 原样
        透传是本判据可达性之根。"""
        dna = _skeleton_dna(sources)
        roundtrip = StrategyDNA.from_json(dna.to_json())
        assert roundtrip.signal_sources == sources, (
            "from_json 未原样透传（测试前提失效，须复查 dna.py）")
        result = validate_dna(roundtrip)
        assert not result.is_valid, (
            f"from_json 通道放行了直构通道拒绝的形态：{result.errors}")
        assert expected_msg in result.errors, result.errors


# ===========================================================================
# AC-3：ref 判据（拒绝侧——提交时红：现状静默放行/错误码错位）
# ===========================================================================

class TestRefRequirementReject:
    """formula/model 缺 ref → 拒绝+type 点名；rule 豁免；ref 值形制不判。"""

    @pytest.mark.parametrize(
        "stype", ["formula", "model"], ids=["formula缺ref", "model缺ref"])
    def test_plugin_type_missing_ref_rejected(self, stype):
        """type∈{formula,model} 且条目缺 ref 键 → R-1c 消息点名序号
        与 type。"""
        result = validate_dna(_skeleton_dna([
            {"type": stype, "threshold": 0.5}]))
        assert not result.is_valid, (
            f"{stype} 缺 ref 被静默放行：errors={result.errors}")
        expected = f"signal_sources[0]: type '{stype}' requires 'ref'"
        assert expected in result.errors, (
            f"R-1c 消息缺失或形制偏差（期望 {expected!r}）：{result.errors}")

    def test_rule_bare_entry_exempt(self):
        """rule 裸条目（仅 type 键）不因缺 ref 报错（绿态护栏——存量
        生产形态，判据落地前后都绿）。"""
        result = validate_dna(_skeleton_dna([{"type": "rule"}]))
        assert result.is_valid, result.errors

    def test_role_key_does_not_exempt_ref(self):
        """role 不豁免：formula+role:"entry" 缺 ref → 仍拒（role 键是
        角色解释不是结构豁免）。"""
        result = validate_dna(_skeleton_dna([
            {"type": "formula", "role": "entry"}]))
        assert not result.is_valid, result.errors
        assert ("signal_sources[0]: type 'formula' requires 'ref'"
                in result.errors), result.errors

    @pytest.mark.parametrize(
        ("ref_value", "label"),
        [pytest.param("", "空串", id="ref空串"),
         pytest.param(123, "整数", id="ref非字符串")],
    )
    def test_ref_value_form_not_judged(self, ref_value, label):
        """ref 值形制不判（ref=""/ref=123 判据层放行）——最小结构校验
        边界：键在场即过，值合法性归执行侧既有 RESEARCH_SIGNAL_SOURCE_
        INVALID 防线（绿态护栏——防判据越界扩面）。"""
        result = validate_dna(_skeleton_dna([
            {"type": "formula", "ref": ref_value}]))
        assert result.is_valid, (
            f"ref 值形制 {label} 被判据层拒绝（越界扩面）：{result.errors}")

    def test_rule_with_ref_passes(self):
        """rule 带 ref 放行（绿态护栏——键空间超集不拒）。"""
        result = validate_dna(_skeleton_dna([
            {"type": "rule", "ref": "anything"}]))
        assert result.is_valid, result.errors


# ===========================================================================
# AC-4：存量不误伤护栏（双态绿——判据落地前后都绿）
# ===========================================================================

class TestLegacyFormsGuardrail:
    """存量合法形态零迁移：None/空列表/合法混合放行；多判据共存不吞并；
    幂等。"""

    @pytest.mark.parametrize(
        ("sources", "label"),
        [
            pytest.param(None, "None退化", id="None"),
            pytest.param([], "空列表退化", id="空列表"),
            pytest.param([{"type": "rule"},
                          {"type": "formula", "ref": "fx_demo"},
                          {"type": "model", "ref": "mx_demo"}],
                         "合法混合", id="合法混合"),
        ],
    )
    def test_legacy_forms_remain_valid(self, sources, label):
        """None/[]/合法混合 → is_valid 维持（不因本判据新增拒绝——
        R-1d 合法退化语义）。"""
        result = validate_dna(_skeleton_dna(sources))
        assert result.is_valid, (
            f"存量形态 {label} 被新判据误拒：{result.errors}")

    def test_coexisting_errors_not_swallowed(self):
        """同批共存锚：stop_loss 越域（值域判据）与拼错条目（结构判据）
        同批报出互不吞并——errors 收集形制的多判据面。"""
        dna = StrategyDNA(
            signal_genes=[_entry_gene(), _exit_gene()],
            signal_sources=[{"type": "fomula"}],
            risk_genes=RiskGenes(stop_loss=0.5),  # pct 域 [0.005, 0.20] 外
        )
        result = validate_dna(dna)
        assert not result.is_valid
        assert any("signal_sources[0]" in e for e in result.errors), (
            f"结构判据消息被吞并：{result.errors}")
        assert any("Stop loss" in e for e in result.errors), (
            f"值域判据消息被吞并：{result.errors}")

    def test_idempotent_verdict(self):
        """幂等锚：同 DNA 两次调用 errors 相等（纯函数——R-2）。"""
        dna = _skeleton_dna([{"type": "fomula"}])
        first = validate_dna(dna)
        second = validate_dna(dna)
        assert first.errors == second.errors
        assert first.is_valid == second.is_valid

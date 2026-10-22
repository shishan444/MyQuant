"""守卫 helper 收敛静态锚（signal-sources-structure-gate 3.1·卡 G；
CARD-05 扩约 evolution 第七守卫文件）。

契约出处（design 卡 G，R-8 逐字节基准+A-1 排除面）：
- 七调用点（backtest 读库/内联、batch、verify、compare、live 任务
  创建、evolution 任务创建）的两段拒绝表达收敛为
  _common._reject_invalid_or_plugin_dna——七调用点文件不再内联出现
  两错误码的代码字符串字面量；判据单源在 core/strategy/validator
  不动。evolution 面历史注记：Q-3 件⑤加插件段时形成手写两段未移出
  排除面（静态守卫漏防），CARD-05 收敛后移入守卫面（回潮即红）。
- 排除面（A-1）：crud.py 的 validate_dna 单段内联块**不在收敛面**
  （无插件能力判据的第一段）——显式列名防扫描误伤。
- 匹配形制：带双引号的代码字符串字面量 ``"DNA_VALIDATION_FAILED"``
  /``"PLUGIN_SOURCES_UNSUPPORTED"``——注释中的裸词不命中
  （live_trading.py:54 威胁模型注释含裸词 PLUGIN_SOURCES_
  UNSUPPORTED 属翻案记录保留，子串扫描会恒红——reviewer 发现修订）。
- _common.py 内两码各恰一次（helper 是唯一表达点）。
- helper 参数轴对拍：(library_dna, channel) 组合与 channel 全映射的
  message 逐字节==design 4.3 表+Q-3 件⑤ evolution 条目（采集基线
  同源字面量——卡 G 前后双点绿的锚面）。
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

from fastapi import HTTPException

from api.routes.strategies import _common
from api.routes.strategies._common import _reject_invalid_or_plugin_dna
from core.strategy.dna import RiskGenes, SignalGene, SignalRole, StrategyDNA

_REPO_ROOT = Path(__file__).resolve().parents[2]
_STRAT_DIR = _REPO_ROOT / "api" / "routes" / "strategies"

# 七调用点所在文件（#1-5 同目录；#6/#7 跨目录——显式清单不依赖 grep
# head 截断）
GUARDED_FILES = [
    _STRAT_DIR / "backtest.py",
    _STRAT_DIR / "batch.py",
    _STRAT_DIR / "verify.py",
    _STRAT_DIR / "compare.py",
    _REPO_ROOT / "api" / "routes" / "live_trading.py",
    _REPO_ROOT / "api" / "routes" / "evolution.py",
]

# 排除面（A-1）：validate_dna 单段内联块保留的文件——显式列名
EXCLUDED_FILES = [_STRAT_DIR / "crud.py"]


def _dna(signal_sources=None) -> StrategyDNA:
    """合法骨架（entry/exit 齐全）+指定 signal_sources——三态构造器
    （review Q-3 收敛：对齐姊妹文件 test_strategy_routes_signal_
    sources_gate.py 的 _dna_with_sources 形制）。"""
    return StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                       "rsi", {"type": "lt", "threshold": 35}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                       "rsi", {"type": "cross_above", "threshold": 50.0}),
        ],
        signal_sources=signal_sources,
        risk_genes=RiskGenes(),
    )


def _valid_plain_dna() -> StrategyDNA:
    """合法纯基因 DNA（两段全过——不 raise 面）。"""
    return _dna()


def _invalid_dna() -> StrategyDNA:
    """结构坏 DNA（缺 ref——第一段命中面）。"""
    return _dna([{"type": "formula", "threshold": 0.5}])


def _pure_plugin_dna() -> StrategyDNA:
    """结构合法纯插件 DNA（第二段命中面）。"""
    return _dna([{"type": "formula", "ref": "fx_demo"}])


class TestInlineScan:
    """七调用点文件两码零内联（带引号代码字面量匹配）。"""

    @pytest.mark.parametrize(
        "guarded", GUARDED_FILES, ids=[f.name for f in GUARDED_FILES])
    def test_no_inline_error_code_literals(self, guarded):
        """收敛后七文件不再内联出现两码的代码字符串字面量——
        后续新增守卫若再内联（复制回潮）此锚即红。注释裸词不命中。"""
        text = guarded.read_text(encoding="utf-8")
        assert '"DNA_VALIDATION_FAILED"' not in text, (
            f"{guarded.name} 内联出现 DNA_VALIDATION_FAILED 代码字面量"
            "（收敛回潮——应改用 _common helper）")
        assert '"PLUGIN_SOURCES_UNSUPPORTED"' not in text, (
            f"{guarded.name} 内联出现 PLUGIN_SOURCES_UNSUPPORTED 代码"
            "字面量（收敛回潮——应改用 _common helper）")

    def test_excluded_files_keep_inline_first_segment(self):
        """排除面（A-1）：crud 的单段内联块保留——文件仍在
        （扫描不误伤的对照锚；非行为断言，形制在场性检查）。"""
        for excluded in EXCLUDED_FILES:
            assert excluded.exists(), f"排除面文件缺席：{excluded}"

    def test_common_holds_each_code_exactly_once(self):
        """_common.py 两码各恰一次（helper 是唯一表达点——多次出现
        意味着 helper 外又长出表达分叉）。"""
        text = (_REPO_ROOT / "api" / "routes" / "strategies"
                / "_common.py").read_text(encoding="utf-8")
        assert text.count('"DNA_VALIDATION_FAILED"') == 1, (
            "DNA_VALIDATION_FAILED 在 _common.py 应恰一次（helper 内）")
        assert text.count('"PLUGIN_SOURCES_UNSUPPORTED"') == 1, (
            "PLUGIN_SOURCES_UNSUPPORTED 在 _common.py 应恰一次（helper 内）")


class TestHelperParamAxis:
    """参数轴对拍：(library_dna, channel) 组合与 channel 全映射的
    message 逐字节==design 4.3 表+Q-3 件⑤ evolution 条目（与 1.2
    采集基线同源字面量）。"""

    @pytest.mark.parametrize(
        ("library_dna", "channel", "expected"),
        [
            (True, "backtest",
             "Library DNA invalid（库内 DNA 结构/值域非法）"),
            (True, "verify",
             "Library DNA invalid（库内 DNA 结构/值域非法）"),
            (True, "batch",
             "Library DNA invalid（库内 DNA 结构/值域非法）"),
            (True, "compare",
             "Library DNA invalid（库内 DNA 结构/值域非法）"),
            (False, "backtest", "DNA 值域/结构校验未通过"),
            (False, "live", "DNA 值域/结构校验未通过"),
            (False, "evolution", "DNA 值域/结构校验未通过"),
        ],
        ids=["读库-backtest", "读库-verify", "读库-batch",
             "读库-compare", "直传-backtest内联", "直传-live",
             "直传-evolution"])
    def test_dna_message_axis(self, library_dna, channel, expected):
        """第一段 message 两态×通道七格（结构坏 DNA 命中；evolution
        格=Q-3 件⑤初始入口拒绝，CARD-05 收敛后同 helper 承载）。"""
        with pytest.raises(HTTPException) as ei:
            _reject_invalid_or_plugin_dna(
                _invalid_dna(), library_dna=library_dna, channel=channel)
        assert ei.value.status_code == 422
        assert ei.value.detail["message"] == expected

    @pytest.mark.parametrize(
        ("channel", "expected"),
        [
            ("backtest",
             "策略含插件信号源，REST 回测通道不支持"
             "（引擎该通道未接插件信号集，结果将为零"
             "交易）——请经研究验证链沙盒回测"),
            ("verify",
             "策略含插件信号源，REST 回测通道不支持"
             "（引擎该通道未接插件信号集，结果将为"
             "零交易）——请经研究验证链沙盒回测"),
            ("batch",
             "策略含插件信号源，REST 批量回测通道"
             "不支持（引擎该通道未接插件信号集，"
             "结果将为零交易）——请经研究验证链"
             "沙盒回测"),
            ("compare",
             "策略含插件信号源，REST 对比回测通道不支持"
             "（引擎该通道未接插件信号集，结果将为零"
             "交易）——请经研究验证链沙盒回测"),
            ("live",
             "执行环境不支持插件信号源（live 链未接入插件"
             "信号组装）——请经研究验证链沙盒回测"),
            ("evolution",
             "执行环境不支持插件信号源（进化评估链未接入插件"
             "信号组装，评估将失真）——请经研究验证链沙盒回测"),
        ],
        ids=["backtest", "verify", "batch", "compare", "live",
             "evolution"])
    def test_plugin_message_axis(self, channel, expected):
        """第二段 message channel 全映射六格（纯插件 DNA 命中；
        evolution 格=Q-3 件⑤ _PLUGIN_REJECT_MESSAGES 进化条目）。"""
        with pytest.raises(HTTPException) as ei:
            _reject_invalid_or_plugin_dna(
                _pure_plugin_dna(), library_dna=True, channel=channel)
        assert ei.value.status_code == 422
        assert ei.value.detail["message"] == expected


class TestHelperUnitBehavior:
    """helper 单元行为：两码 detail 键集/全过不 raise/channel 校验。"""

    def test_invalid_dna_detail_has_errors_key(self):
        """结构坏 → 422+code+detail 含 errors 键（判据明细透传）。"""
        with pytest.raises(HTTPException) as ei:
            _reject_invalid_or_plugin_dna(
                _invalid_dna(), library_dna=True, channel="backtest")
        d = ei.value.detail
        assert d["code"] == "DNA_VALIDATION_FAILED"
        assert set(d) == {"code", "message", "errors"}
        assert any("signal_sources" in e for e in d["errors"])

    def test_plugin_dna_detail_has_no_errors_key(self):
        """插件源 → 422+code+detail 无 errors 键（键集不对称是既有
        契约基准的一部分——六处现状均无）。"""
        with pytest.raises(HTTPException) as ei:
            _reject_invalid_or_plugin_dna(
                _pure_plugin_dna(), library_dna=True, channel="backtest")
        d = ei.value.detail
        assert d["code"] == "PLUGIN_SOURCES_UNSUPPORTED"
        assert set(d) == {"code", "message"}

    def test_valid_dna_passes_silently(self):
        """合法 DNA → 不 raise（return None）。"""
        assert _reject_invalid_or_plugin_dna(
            _valid_plain_dna(), library_dna=True, channel="backtest") is None

    def test_invalid_channel_raises_value_error(self):
        """channel 不在合法集 → ValueError（编程错误——沿
        validate_trading_compatibility 的 link 校验先例）。"""
        with pytest.raises(ValueError, match="channel"):
            _reject_invalid_or_plugin_dna(
                _valid_plain_dna(), library_dna=True, channel="paper")

    def test_invalid_channel_message_renders_keyset(self):
        """ValueError 消息键集渲染单源锚（CARD-06）：消息列出
        _PLUGIN_REJECT_MESSAGES 全部键（f-string join 渲染——随表
        扩容自动同步）。改回手写清单漏键（如漏 evolution）即红。"""
        with pytest.raises(ValueError) as ei:
            _reject_invalid_or_plugin_dna(
                _valid_plain_dna(), library_dna=True, channel="paper")
        msg = str(ei.value)
        for key in ("backtest", "verify", "batch", "compare", "live",
                    "evolution"):
            assert key in msg, (
                f"ValueError 消息未含映射键 {key}（键集渲染单源被破坏"
                f"——手写清单回潮）：{msg}")

    def test_signature_unchanged_weak_anchor(self):
        """签名未动弱锚（design 卡 G 契约形制）：keyword-only 双参数
        library_dna/channel。"""
        sig = inspect.signature(_reject_invalid_or_plugin_dna)
        params = list(sig.parameters.values())
        assert [p.name for p in params] == ["dna", "library_dna", "channel"]
        assert params[1].kind is inspect.Parameter.KEYWORD_ONLY
        assert params[2].kind is inspect.Parameter.KEYWORD_ONLY


class TestJudgementOrderInternalized:
    """两段判定顺序内化：结构坏优先于能力边界。"""

    def test_structural_bad_precedes_capability(self):
        """结构坏+合法插件源并存 → 报 DNA_VALIDATION_FAILED（第一段
        先判——「策略坏」优先于「环境不支持」，判定顺序不变量单点化
        的 helper 内证明）。"""
        dna = _dna([{"type": "fomula"},
                    {"type": "formula", "ref": "fx_demo"}])
        with pytest.raises(HTTPException) as ei:
            _reject_invalid_or_plugin_dna(
                dna, library_dna=True, channel="backtest")
        assert ei.value.detail["code"] == "DNA_VALIDATION_FAILED"

    def test_module_docstring_mentions_contract(self):
        """D-6 扩约落位弱锚：模块 docstring 声明兼载范围与判据单源
        归属（跨目录消费方契约的文档面）。"""
        assert "signal-sources-structure-gate" in _common.__doc__
        assert "判据单源" in _common.__doc__

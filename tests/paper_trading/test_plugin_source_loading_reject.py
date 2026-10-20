"""模拟盘装载插件源拒绝测试（recognize-plugin-source-dna 任务 4.1）。

契约出处（design 卡 T4·第一问方向二，2026-10-19 用户裁决生效）：
paper 链未接入插件信号组装，插件源在场即拒（含混合形态）。拒绝经
task error_detail 通道（pending→error 迁移，沿 _reject 既有形制），
错误码分立（补丁①）：PLUGIN_SOURCES_UNSUPPORTED=「执行环境不支持」
而非「策略无效」——结构校验（validate_dna→invalid_dna）与能力边界
判据（plugin_sources_unsupported）是两个正交判定，分码是语义区分的
载体。

断言全部从 design T4 契约推导（不从实现反推）：
- 主格：纯插件策略（signal_genes 仅 EXIT 角色+signal_sources 含
  formula/model 源——T2 判据下结构合法）装载拒绝，error_detail
  携带 plugin_sources_unsupported 错误码+「不支持」语义短语，不含
  invalid_dna（策略不是坏的，是环境不支持）；pending→error 迁移+
  task_error 推送（_reject 通道三件套）+不进 running。
- 在场即拒边界：混合形态（内置 ENTRY 基因+插件源）与 role:"exit"
  源在场（结构合法下）同样拒绝——判据字面是「在场」不是「仅纯插件」。
- 对照组：普通策略（无插件源）装载不受新判据影响（数据齐备→通过；
  数据缺失→归因 no_data 而非 plugin_sources_unsupported——拒绝
  归因不串位）。
- 顺位锚：插件源在场但值域损坏→invalid_dna 前缀（结构校验先行，
  「坏策略」优先于「环境不支持」报出）。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.paper_trading.runtime.executor import PaperTaskExecutor
from core.paper_trading.runtime.feed import FeedRegistry, FeedSubscription
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import paper
from core.strategy.dna import RiskGenes, SignalGene, SignalRole, StrategyDNA
from tests.helpers.data_factory import make_dna, make_ohlcv

_N_BARS = 200
_SEED = 7
_REPLAY_ALL = "2000-01-01T00:00:00+00:00"

# 字节基线（signal-sources-structure-gate 1.2 补强，采集时点=卡 V 判据
# 落地后、卡 G 收敛前〔组 1 工作区，基 commit 79642c7〕；paper 面
# error_detail 字符串形制〔前缀+文案〕的字面量锚——_reject 形制不在
# 卡 G 收敛面，本锚锁 paper 面拒绝表达零漂移）
PAPER_PLUGIN_DETAIL = ("plugin_sources_unsupported: 执行环境不支持插件"
                       "信号源（paper 链未接入插件信号组装）——请经研究"
                       "验证链沙盒回测")
PAPER_INVALID_VALUE_DETAIL = (
    "invalid_dna: DNA 值域校验未过（"
    "Position size 2.0 out of range [0.10, 1.0]）")


def _exit_gene() -> SignalGene:
    """出场基因（对齐库存插件源策略实测形态：RSI cross_above 50）。"""
    return SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                      "rsi", {"type": "cross_above", "threshold": 50.0})


def _entry_gene() -> SignalGene:
    return SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                      "rsi", {"type": "lt", "threshold": 35})


def _plugin_source(source_type: str = "formula", **extra) -> dict:
    """插件源条目（对齐 signal_assembly 消费链键空间；缺省不写 role 键
    ——存量 11 行零 role 键形态，读取侧按 entry 解释）。"""
    src = {"type": source_type, "ref": "fx_fx_trend_pullback",
           "threshold": 0.5, "operator": ">"}
    src.update(extra)
    return src


def _empty_funding_frame() -> pd.DataFrame:
    idx = pd.DatetimeIndex(pd.to_datetime([], utc=True), name="timestamp")
    return pd.DataFrame({"funding_rate": []}, index=idx)


class _Harness:
    """装载拒绝测试装配（沿 test_paper_executor._Harness 形制收窄：
    只需装载链，无需回放）。"""

    def __init__(self, tmp_path: Path, dna: StrategyDNA, *,
                 with_data: bool = True):
        from core.data.storage import save_parquet

        self.data_dir = tmp_path / "market"
        self.data_dir.mkdir()
        self.df = make_ohlcv(n=_N_BARS, freq="4h", seed=_SEED,
                             base_price=100.0)
        if with_data:
            save_parquet(self.df, self.data_dir / "BTCUSDT_4h.parquet")

        self.db = tmp_path / "paper.db"
        init_db_ext(self.db)
        self.task_id = paper.create_task(
            self.db, strategy_id="strat-plugin",
            dna_snapshot_json=dna.to_json(),
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified",
            initial_cash=10_000.0, leverage=1, window_bars=50)

        self.registry = FeedRegistry(
            self.data_dir,
            fetcher=lambda **k: self.df,
            funding_refresher=lambda **k: _empty_funding_frame())
        # 预建 feed（executor 装载链查费率面；dummy 订阅防回收）
        self._dummy = FeedSubscription(on_frames=lambda df, idx: None)
        self.feed = self.registry.acquire("BTCUSDT", "4h", self._dummy)
        self.feed.refresh()
        self.pushes: list[dict] = []

    def make_executor(self) -> PaperTaskExecutor:
        row = paper.get_task(self.db, self.task_id)
        return PaperTaskExecutor(
            self.db, row,
            registry=self.registry,
            push=lambda tid, p: self.pushes.append(p),
            start_after=_REPLAY_ALL)

    def task(self) -> dict:
        return paper.get_task(self.db, self.task_id)


class TestPluginSourceLoadingReject:
    """纯插件/含插件源策略装载拒绝（错误码分立+error 通道三件套）。"""

    @pytest.mark.parametrize("source_type", ["formula", "model"],
                             ids=["formula源", "model源"])
    def test_pure_plugin_dna_rejected_env_unsupported(
            self, tmp_path, source_type):
        """主格：纯插件策略（EXIT 基因+插件源——T2 下结构合法）装载
        拒绝：prepare False+pending→error+error_detail 前缀
        plugin_sources_unsupported+含「不支持」+不含 invalid_dna
        （分码语义：环境不支持≠策略无效）+task_error 推送。"""
        dna = StrategyDNA(
            signal_genes=[_exit_gene()],
            signal_sources=[_plugin_source(source_type)],
            risk_genes=RiskGenes(),
        )
        h = _Harness(tmp_path, dna)
        ex = h.make_executor()

        assert ex.prepare() is False

        task = h.task()
        assert task["status"] == "error"  # pending→error 迁移
        assert task["started_at"] is None  # 不进 running
        detail = task["error_detail"]
        # 错误码字段断言（前缀形制——task error_detail 的码位）
        assert detail.startswith("plugin_sources_unsupported"), detail
        assert "不支持" in detail, detail
        assert "invalid_dna" not in detail, (
            f"分码语义破坏：环境不支持误报为策略无效——{detail}")
        # 二级断言（字节基线）：error_detail 逐字节
        assert detail == PAPER_PLUGIN_DETAIL, detail
        # _reject 通道三件套之推送面（update_task/error 日志之外）
        assert any(p["type"] == "task_error"
                   and p["error_detail"] == detail for p in h.pushes), (
            h.pushes)

    def test_hybrid_builtin_genes_plus_plugin_source_rejected(
            self, tmp_path):
        """在场即拒（混合形态）：内置 ENTRY/EXIT 基因齐全+插件源在场
        → 同样拒绝——防误放「混合可跑」（混合形态插件源被静默忽略、
        结果只反映内置基因，比零交易更隐蔽；design T4 判据字面=
        在场即拒）。"""
        dna = StrategyDNA(
            signal_genes=[_entry_gene(), _exit_gene()],
            signal_sources=[{"type": "rule"}, _plugin_source()],
            risk_genes=RiskGenes(),
        )
        h = _Harness(tmp_path, dna)
        ex = h.make_executor()

        assert ex.prepare() is False
        assert h.task()["error_detail"].startswith(
            "plugin_sources_unsupported")

    def test_role_exit_source_presence_rejected(self, tmp_path):
        """在场即拒（不分 role）：结构合法（ENTRY/EXIT 基因齐全）+
        仅 role:"exit" 源在场 → 装载仍拒——能力边界判据的字面是
        「插件源在场」（type∈{formula,model}），不因 role 标记豁免
        （与结构校验的 role 解释正交）。"""
        dna = StrategyDNA(
            signal_genes=[_entry_gene(), _exit_gene()],
            signal_sources=[_plugin_source(role="exit")],
            risk_genes=RiskGenes(),
        )
        h = _Harness(tmp_path, dna)
        ex = h.make_executor()

        assert ex.prepare() is False
        assert h.task()["error_detail"].startswith(
            "plugin_sources_unsupported")


class TestPlainStrategyControlGroup:
    """对照组：普通策略（无插件源）装载不受新判据影响。"""

    def test_plain_strategy_prepare_still_passes(self, tmp_path):
        """普通策略数据齐备：装载通过，状态保持 pending（拒绝才写
        error——新判据零误伤面）。"""
        h = _Harness(tmp_path, make_dna())
        ex = h.make_executor()

        assert ex.prepare() is True
        assert h.task()["status"] == "pending"
        assert h.task()["error_detail"] is None

    def test_plain_strategy_data_missing_other_prefix(self, tmp_path):
        """普通策略数据缺失：拒绝归因 no_data（非
        plugin_sources_unsupported）——拒绝原因不串位，插件判据未
        越权抢占其他装载失败面的报错。"""
        h = _Harness(tmp_path, make_dna(), with_data=False)
        ex = h.make_executor()

        assert ex.prepare() is False
        detail = h.task()["error_detail"]
        assert detail.startswith("no_data"), detail
        assert "plugin_sources_unsupported" not in detail, detail


class TestErrorCodePrecedence:
    """顺位锚：结构校验（invalid_dna）先于能力边界判据报出。"""

    def test_invalid_value_with_plugin_source_reports_invalid_dna(
            self, tmp_path):
        """插件源在场+值域损坏（position_size 越上界）→ invalid_dna
        前缀——「策略坏」优先于「环境不支持」报出（分码在装载面的
        顺位对应；两码不同值的装载面证明）。"""
        dna = StrategyDNA(
            signal_genes=[_exit_gene()],
            signal_sources=[_plugin_source()],
            risk_genes=RiskGenes(position_size=2.0),  # 越上界 1.0
        )
        h = _Harness(tmp_path, dna)
        ex = h.make_executor()

        assert ex.prepare() is False
        detail = h.task()["error_detail"]
        assert detail.startswith("invalid_dna"), detail
        assert "plugin_sources_unsupported" not in detail, detail
        # 二级断言（字节基线）：error_detail 逐字节（值域形态）
        assert detail == PAPER_INVALID_VALUE_DETAIL, detail


class TestStructureGateMalformedSources:
    """signal_sources 结构判据在 paper 装载面的生效
    （signal-sources-structure-gate 0.4·paper 面——新增 class，
    既有断言零调整）。

    - 拼错型：从静默放行（插件条件一字未生效地跑）→ invalid_dna
      拒绝+点名串（条目序号+实际值）入 error_detail。
    - 缺 ref 型：error_detail 从 plugin_sources_unsupported 分支迁到
      invalid_dna 分支（D-3 在 paper 面的体现——「结构坏」先于
      「环境不支持」报出，与 TestErrorCodePrecedence 同顺位）。
    - 对照（零行为锚）：合法混合形态仍 plugin_sources_unsupported
      ——新判据不改变能力边界判据的行为面。
    """

    def test_mistyped_source_rejected_as_invalid_dna(self, tmp_path):
        """拼错型（type="fomula"）→ prepare False+invalid_dna 前缀
        +点名串（signal_sources[0] 与实际值）入 error_detail+不含
        plugin_sources_unsupported（现状静默放行——prepare True，
        红）。"""
        dna = StrategyDNA(
            signal_genes=[_entry_gene(), _exit_gene()],
            signal_sources=[{"type": "fomula"}],
            risk_genes=RiskGenes(),
        )
        h = _Harness(tmp_path, dna)
        ex = h.make_executor()

        assert ex.prepare() is False, (
            "拼错型被静默放行（结构判据未生效——装载即跑、插件条件"
            "一字未生效的静默失真形态）")
        detail = h.task()["error_detail"]
        assert detail.startswith("invalid_dna"), detail
        assert "signal_sources[0]" in detail, detail
        assert "fomula" in detail, detail
        assert "plugin_sources_unsupported" not in detail, (
            f"拼错型误报能力边界码：{detail}")

    def test_missing_ref_rejected_as_invalid_dna(self, tmp_path):
        """缺 ref 型 → invalid_dna 前缀+requires 'ref' 点名（D-3
        迁移后目标态——现状 plugin_sources_unsupported 前缀，红）。"""
        dna = StrategyDNA(
            signal_genes=[_entry_gene(), _exit_gene()],
            signal_sources=[{"type": "formula", "threshold": 0.5}],
            risk_genes=RiskGenes(),
        )
        h = _Harness(tmp_path, dna)
        ex = h.make_executor()

        assert ex.prepare() is False
        detail = h.task()["error_detail"]
        assert detail.startswith("invalid_dna"), (
            f"缺 ref 型应走结构拒绝分支（D-3 归位）：{detail}")
        assert "requires 'ref'" in detail, detail
        assert "plugin_sources_unsupported" not in detail, detail

    def test_legal_mixed_still_plugin_sources_unsupported(self, tmp_path):
        """对照（零行为锚）：合法混合形态（rule 裸+formula 带 ref）
        → 仍 plugin_sources_unsupported——结构判据放行后能力边界
        判据接管，与既有 TestPluginSourceLoadingReject 主格行为
        逐位一致（新判据对合法形态的行为零变化）。"""
        dna = StrategyDNA(
            signal_genes=[_entry_gene(), _exit_gene()],
            signal_sources=[{"type": "rule"}, _plugin_source()],
            risk_genes=RiskGenes(),
        )
        h = _Harness(tmp_path, dna)
        ex = h.make_executor()

        assert ex.prepare() is False
        detail = h.task()["error_detail"]
        assert detail.startswith("plugin_sources_unsupported"), detail
        assert "invalid_dna" not in detail, detail

"""做空假设通道——双方向对照引擎实证（add-direction-channel A5，A4-1~A4-5）。

不新增生产代码的行为级验收（tasks A5）：合成 4h 数据带 open_interest
列——下跌段 OI 阶梯上行（OI_ChangeRate 周期触发）+ 价格阶梯下行，
上涨段价格镜像；short/long 对照假设同 strategy_params={"leverage":2}
（leverage=1 无借入 funding 恒零——实证空跑）。全部走 TestClient API
面 /api/research/validate-backtest（L3 链路）。

实证五项（design 卡 A-4）：
1. 方向翻转：short 下跌段 total_return>0 且触发笔数>0、上涨段为负、
   long 对照符号相反（镜像性）
2. win_rate 口径：short 下跌段显著高于 long 对照（winning 按 pnl>0——
   空头盈利交易 pnl 为正数无需翻转）
3. funding 计收：leverage=2 下 total_funding_cost>0 且 short/long 对照
   同持仓窗口金额容差内相等（固定正费率、与持仓方向无关的直接证据）；
   leverage=1 零费=合法边界（无借入即无资金费，非缺陷）
4. benchmark 方向语义：下跌段 benchmark_return<0 恒多头口径、
   excess_return=total_return−benchmark_return 数字自洽
5. 双实体隔离：short/long 各自验证记录、判定并集按假设分族互不串扰
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]

from core.analysis.hypothesis_to_strategy import translate_hypothesis
from core.analysis.hypothesis_service import register_hypothesis
from core.persistence.db_ext import init_db_ext
from core.persistence.repository.strategy import get_strategy, save_strategy
from core.strategy.dna import (
    ExecutionGenes,
    LogicGenes,
    RiskGenes,
    SignalGene,
    SignalRole,
    StrategyDNA,
)

N = 900
STEP = 14
DOWN_START, UP_START = 300, 600
ISO_DOWN = ("2024-02-20T00:00:00Z", "2024-04-10T00:00:00Z")   # bar 300-599
ISO_UP = ("2024-04-10T00:00:00Z", "2024-05-30T00:00:00Z")     # bar 600-899

WHEN = [{"subject": {"indicator": "OI_ChangeRate", "params": {"period": STEP},
                     "field": "oi_change_rate"},
         "action": "gt", "target": 0.05}]
# 出场恒假（RSI 永不大于 999——指标列数值 target 在出场角色可表达，
# 基础列数值 target 不可表达是既有契约）——触发后持仓至段末，方向段
# 收益=全段浮盈浮亏（方向翻转的决定性差异）
THEN = [{"subject": {"indicator": "RSI", "params": {"period": 14},
                     "field": "rsi"}, "action": "gt", "target": 999}]
CONDITIONS = {"when_conditions": WHEN, "then_conditions": THEN}
# 登记自报区间=warmup 段（登记自报直接进判定并集——验证请求区间须
# 在自报段之外，研究协议既有语义）
RANGES = [{"symbol": "BTCUSDT", "timeframe": "4h",
           "start": "2024-01-01T00:00:00Z", "end": "2024-02-20T00:00:00Z"}]


def _synth_df() -> pd.DataFrame:
    """确定性合成：warmup 平稳 → 下跌段价格每 14 根 ×0.98 → 上涨段
    每 14 根 ×1.02（段界价格连续衔接——防段界跳空污染段末估值与
    基准口径）；OI 两段均每 14 根 ×1.10（段首不换档——段界不产生
    跨段触发脉冲）。"""
    idx = pd.date_range("2024-01-01", periods=N, freq="4h", tz="UTC",
                        name="timestamp")
    price = np.empty(N)
    oi = np.empty(N)
    p, o = 60000.0, 1_000_000.0
    for i in range(N):
        if DOWN_START < i < UP_START and (i - DOWN_START) % STEP == 0:
            p *= 0.98
            o *= 1.10
        elif i > UP_START and (i - UP_START) % STEP == 0:
            p *= 1.02
            o *= 1.10
        price[i] = p
        oi[i] = o
    return pd.DataFrame({
        "open": price, "high": price * 1.001, "low": price * 0.999,
        "close": price, "volume": np.full(N, 500.0), "trades": 10,
        "open_interest": oi,
    }, index=idx)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """合成数据 + 双方向假设与 draft + leverage=1 对照 draft + client。"""
    tmp = tmp_path_factory.mktemp("dir_parity")
    data_dir = tmp / "market"
    data_dir.mkdir()
    _synth_df().to_parquet(data_dir / "BTCUSDT_4h.parquet")
    db = tmp / "q.db"
    init_db_ext(db)

    out = {}
    for key, direction in (("short", "short"), ("long", None)):
        h = register_hypothesis(
            db, conditions=json.loads(json.dumps(CONDITIONS)),
            statement=f"OI 流入-{key}", data_ranges=RANGES,
            strategy_params={"leverage": 2}, direction=direction)
        t = translate_hypothesis(db, hypothesis_id=h["hypothesis_id"])
        out[f"hyp_{key}"] = h["hypothesis_id"]
        out[f"st_{key}"] = t["strategy_id"]

    # leverage=1 对照：手工 short draft（RiskGenes 平台默认 leverage=1）
    h1 = register_hypothesis(
        db, conditions=json.loads(json.dumps(CONDITIONS)),
        statement="OI 流入-lev1", data_ranges=RANGES, direction="short")
    gene = SignalGene(
        indicator="OI_ChangeRate", params={"period": STEP},
        role=SignalRole.ENTRY_TRIGGER, field_name=f"oi_change_rate_{STEP}",
        condition={"type": "gt", "threshold": 0.05})
    # 卡 R 起装载挂全量 validate_dna（Q-1）：手工构造补恒假 exit
    # （gt 1e9 永不触发——行为等效原无 exit 默认路径）
    _exit_g = SignalGene(
        indicator="OI_ChangeRate", params={"period": STEP},
        role=SignalRole.EXIT_TRIGGER, field_name=f"oi_change_rate_{STEP}",
        condition={"type": "gt", "threshold": 1e9})
    dna1 = StrategyDNA(
        signal_genes=[gene, _exit_g], logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes(direction="short"))
    save_strategy(db, strategy_id="st_lev1", dna_json=dna1.to_json(),
                  symbol="BTCUSDT", timeframe="4h", name="short-lev1")
    out["hyp_lev1"] = h1["hypothesis_id"]
    out["st_lev1"] = "st_lev1"

    # funding 对称对照：无 SL 双方向（stop_loss=0 禁用——引擎 sl_stop>0
    # 才启用），双方向同持仓窗口（均 1 笔持有到段末）——固定正费率与
    # 方向无关的判定前提
    for key, direction in (("fund_s", "short"), ("fund_l", "long")):
        # 卡 R 起装载挂全量 validate_dna（Q-1）：stop_loss=0.0「禁用」
        # 自造形态在值域 [0.005, 0.20] 外不再可用——取域上界 0.20
        # （段内波动远低于 20% 阈，近似无 SL 的对照语义保持）；补
        # 恒假 exit（行为等效原无 exit 默认路径）
        dna_f = StrategyDNA(
            signal_genes=[gene, _exit_g], logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes(direction=direction, leverage=2,
                                 stop_loss=0.20))
        save_strategy(db, strategy_id=f"st_{key}", dna_json=dna_f.to_json(),
                      symbol="BTCUSDT", timeframe="4h", name=f"fund-{key}")
        out[f"st_{key}"] = f"st_{key}"

    from MyQuant.api.app import create_app
    from fastapi.testclient import TestClient
    app = create_app(db_path=db, data_dir=data_dir)
    out["db"] = db
    out["data_dir"] = data_dir
    with TestClient(app) as client:
        out["client"] = client
        yield out


def _run_iso(world, hyp_key, st_key, seg):
    start, end = ISO_DOWN if seg == "down" else ISO_UP
    resp = world["client"].post("/api/research/validate-backtest", json={
        "hypothesis_id": world[hyp_key], "strategy_id": world[st_key],
        "request_range": {"symbol": "BTCUSDT", "timeframe": "4h",
                          "start": start, "end": end}})
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture(scope="module")
def metrics(world):
    """module 级一次跑齐四组回测（numba JIT 摊销），用例共享只读。"""
    return {
        "short_down": _run_iso(world, "hyp_short", "st_short", "down"),
        "short_up": _run_iso(world, "hyp_short", "st_short", "up"),
        "long_down": _run_iso(world, "hyp_long", "st_long", "down"),
        "lev1_down": _run_iso(world, "hyp_lev1", "st_lev1", "down"),
    }


class TestA41DirectionFlip:
    def test_short_down_positive_with_trades(self, metrics):
        m = metrics["short_down"]["metrics"]
        assert m["total_trades"] > 0, "零触发实证无效"
        assert m["total_return"] > 0

    def test_short_up_negative(self, metrics):
        assert metrics["short_up"]["metrics"]["total_return"] < 0

    def test_long_down_opposite_sign(self, metrics):
        """镜像性：同数据同信号 long 对照符号相反。"""
        m_l = metrics["long_down"]["metrics"]
        assert m_l["total_return"] < 0
        assert m_l["total_trades"] > 0


class TestA42WinRateConvention:
    def test_short_win_rate_higher_than_long(self, metrics):
        m_s = metrics["short_down"]["metrics"]
        m_l = metrics["long_down"]["metrics"]
        # winning 按 pnl>0（空头盈利 pnl 为正——无需翻转的证据）
        assert m_s["win_rate"] > m_l["win_rate"]
        assert m_s["win_rate"] >= 0.5


class TestA43FundingSymmetry:
    """funding 断言面=引擎直测（API 面 metrics 不披露 funding 键——
    逐笔明细与 funding 的直接证据归引擎面，design 卡 A-5 同款分工）。"""

    @pytest.fixture(scope="class")
    def engine_funding(self, world):
        """引擎直调跑三组（无 SL 双方向 lev2 / short lev1）下跌段，
        取 total_funding_cost。"""
        import pandas as pd
        from core.analysis.validation_service import _load_strategy_dna
        from core.backtest.engine import BacktestEngine
        from core.data.mtf_loader import load_and_prepare_df

        enhanced = load_and_prepare_df(
            world["data_dir"], "BTCUSDT", "4h",
            data_start="2024-01-21T00:00:00Z",
            data_end="2024-04-10T00:00:00Z")
        # 卡 R 起装载挂全量 validate_dna（Q-1）：stop_loss=0「禁用」
        # 自造形态不再合法（值域 [0.005, 0.20]），st_fund_* 取域上界
        # 0.20——跑窗后移至下跌段尾（剩余回撤 ≈3.9%<20% 不触发，
        # 「各 1 笔持有到段末」前提保持；窗口仍在下跌段，费率对称
        # 断言语义不变）
        bt_df = enhanced[enhanced.index
                         >= pd.Timestamp("2024-04-04T00:00:00Z")]
        out = {}
        for key, st in (("fund_s", "st_fund_s"), ("fund_l", "st_fund_l"),
                        ("lev1", "st_lev1")):
            dna, _ = _load_strategy_dna(world["db"], world[st])
            result = BacktestEngine().run(
                dna, bt_df, dfs_by_timeframe={"4h": bt_df}, signal_set=None)
            out[key] = (float(result.total_funding_cost),
                        int(result.total_trades))
        return out

    def test_funding_positive_and_direction_symmetric(self, engine_funding):
        """固定正费率与方向无关：无 SL 双方向同持仓窗口（各 1 笔持有
        到段末）金额同号同量级（D-4 修复卡的回归锚——修复前 short 为
        负收入即符号翻转+35% 量级差；修复后残余差异<1%，来自空头
        cash 结构下逐 bar 名义值的路径差异，非方向性计费）。"""
        (f_s, n_s), (f_l, n_l) = (engine_funding["fund_s"],
                                   engine_funding["fund_l"])
        assert n_s == n_l == 1, "同持仓窗口前提（各 1 笔持有到段末）"
        assert f_s > 0, \
            "leverage=2 下 short 路径 funding 为零/为负 → 缺陷（D-4）"
        assert f_l > 0
        assert abs(f_s - f_l) < 0.01 * max(1.0, abs(f_l))

    def test_leverage_one_zero_funding_legal_boundary(self, engine_funding):
        """leverage=1 无借入直返零费——引擎既有合法边界，非缺陷。"""
        assert engine_funding["lev1"][0] == 0.0


class TestA44BenchmarkSemantics:
    def test_benchmark_stays_long_convention(self, metrics):
        m = metrics["short_down"]["metrics"]
        assert m["benchmark_return"] is not None
        assert m["benchmark_return"] < 0, "下跌段多头基准照跌（口径不随方向翻转）"

    def test_excess_return_self_consistent(self, metrics):
        m = metrics["short_down"]["metrics"]
        assert abs(m["excess_return"]
                   - (m["total_return"] - m["benchmark_return"])) < 1e-9


class TestA45EntityIsolation:
    def test_judged_union_partitioned_by_hypothesis(self, metrics, world):
        """判定并集按假设分族：long/lev1 对照在 short 已判区间上均
        运行成功（fixture 内 status==200 即隔离证据——不 409）；short
        自身同区间重放走幂等命中（并集/幂等都生效于本假设身份）。"""
        resp = world["client"].post("/api/research/validate-backtest", json={
            "hypothesis_id": world["hyp_short"],
            "strategy_id": world["st_short"],
            "request_range": {"symbol": "BTCUSDT", "timeframe": "4h",
                              "start": ISO_DOWN[0], "end": ISO_DOWN[1]}})
        assert resp.status_code == 200
        assert resp.json()["duplicated"] is True

    def test_records_bound_to_own_hypothesis(self, metrics, world):
        """A4-5：两条独立验证记录分别挂各自假设（family 按 hash 分族）。"""
        from core.persistence.repository import research as rr
        from core.analysis.hypothesis_service import hash_json
        row_s = rr.get_hypothesis(world["client"].app.state.db_path,
                                  world["hyp_short"])
        row_l = rr.get_hypothesis(world["client"].app.state.db_path,
                                  world["hyp_long"])
        c_s = json.loads(row_s["conditions"])
        c_l = json.loads(row_l["conditions"])
        assert c_s.get("direction") == "short"
        assert "direction" not in c_l
        assert hash_json(c_s) != hash_json(c_l)

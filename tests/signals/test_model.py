"""模型管道单测（5.3）：时序切分内置与前视陷阱拦截。"""

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.signals.model import (
    ModelPluginConfig,
    ModelPipelineError,
    fit_predict_walk_forward,
)


def _df(n=800, seed=5):
    """构造：下一 bar 收益可由 2 bar 前的动量部分预测（有信号数据）。"""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC", name="timestamp")
    mom = rng.normal(0, 1, n)
    # ret(t+1) 受 mom(t-2) 驱动 → 特征(t)=mom(t) 经 shift(1) 后即 mom(t-1)，
    # 预测 y(t)=sign(fwd) 含 mom(t-2..)；信息链真实可得
    ret = np.roll(mom, 2) * 0.004 + rng.normal(0, 0.002, n)
    ret[:2] = 0
    close = 100 * np.cumprod(1 + ret)
    return pd.DataFrame({
        "close": close, "volume": rng.uniform(10, 100, n), "mom": mom,
    }, index=idx)


CFG = dict(name="m1", features=["mom"], template="rolling_logistic",
           params={"window": 0, "step": 20}, target={"type": "return_sign", "horizon": 1})


class TestWalkForward:
    def test_produces_oos_signal_and_summary(self) -> None:
        df = _df()
        res = fit_predict_walk_forward(df, ModelPluginConfig(**CFG))
        assert res.signal.notna().sum() > 300
        assert res.oos_summary["n_oos"] > 300
        assert 0.0 <= res.oos_summary["oos_accuracy"] <= 1.0
        assert res.n_refits >= 10

    def test_training_never_sees_future(self) -> None:
        """前视陷阱：把目标泄漏进特征，管道必须因时序切分不产生虚高。

        构造：特征 = 精确的未来目标。若管道泄漏（训练含未来/特征未
        shift），OOS accuracy 会显著 >0.9；walk-forward + t-1 化后，
        泄漏特征经 shift(1) 变成"上一期目标"，预测力大幅衰减。
        """
        df = _df(n=800, seed=5)
        fwd = df["close"].pct_change(1).shift(-1)
        df["leaky"] = np.sign(fwd).fillna(0)  # 纯未来信息
        cfg = ModelPluginConfig(
            name="leak", features=["leaky"], template="rolling_logistic",
            params={"window": 0, "step": 20},
            target={"type": "return_sign", "horizon": 1})
        res = fit_predict_walk_forward(df, cfg)
        # t-1 化后特征是"上一期收益符号"（真实可得）——预测力天然有限，
        # 不可能复现泄漏精度（~1.0）；上界断言拦截全样本拟合泄漏
        assert res.oos_summary["oos_accuracy"] < 0.95, "疑似泄漏：未来信息进入特征"

    def test_signal_only_after_min_train(self) -> None:
        """前 min_train=200 bar 无信号（初始训练窗未建立）。"""
        df = _df(n=400)
        res = fit_predict_walk_forward(df, ModelPluginConfig(**CFG))
        first_valid = res.signal.first_valid_index()
        pos = df.index.get_loc(first_valid)
        assert pos >= 200

    def test_insufficient_data_rejected(self) -> None:
        df = _df(n=150)
        with pytest.raises(ModelPipelineError, match="数据不足"):
            fit_predict_walk_forward(df, ModelPluginConfig(**CFG))

    def test_missing_feature_rejected(self) -> None:
        df = _df(n=300)
        cfg = ModelPluginConfig(**{**CFG, "features": ["nope"]})
        with pytest.raises(ModelPipelineError, match="特征列缺失"):
            fit_predict_walk_forward(df, cfg)

    def test_linear_template(self) -> None:
        df = _df()
        cfg = ModelPluginConfig(**{**CFG, "template": "rolling_linear",
                                     "target": {"type": "return", "horizon": 1}})
        res = fit_predict_walk_forward(df, cfg)
        assert "oos_correlation" in res.oos_summary

    def test_invalid_config_rejected(self) -> None:
        with pytest.raises(ModelPipelineError, match="模板"):
            ModelPluginConfig(name="x", features=["a"], template="gbdt").validate()
        with pytest.raises(ModelPipelineError, match="horizon"):
            ModelPluginConfig(name="x", features=["a"],
                              target={"type": "return", "horizon": 0}).validate()

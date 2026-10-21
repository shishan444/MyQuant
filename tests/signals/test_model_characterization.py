"""model 特征测试网（fix/data-signals-review 修复期安全网）。

现状记录语义：期望值从当前实现行为推导钉住。建网时缺口：
ModelPluginConfig.validate 各拒绝分支 / _make_target 手工核算样例 /
未知目标类型经公开入口拒绝 / 训练失败包装 / 训练窗不足跳过与
无样本外样本拒绝。
"""

import pytest

pytestmark = [pytest.mark.unit]

import numpy as np
import pandas as pd

from core.signals.model import (
    ModelPipelineError,
    ModelPluginConfig,
    _make_target,
    fit_predict_walk_forward,
)


def make_df(n: int) -> pd.DataFrame:
    """交替涨跌的价格序列（return_sign 目标正负交替，可训练）。"""
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    steps = np.where(np.arange(n) % 2 == 0, 1.0, -0.5)
    return pd.DataFrame({"close": 100.0 + np.cumsum(steps)}, index=idx)


class TestConfigValidate:
    """配置校验各拒绝分支与合法直通。"""

    def test_empty_features_rejected(self) -> None:
        cfg = ModelPluginConfig(name="m", features=[])
        with pytest.raises(ModelPipelineError, match="features 不能为空"):
            cfg.validate()

    def test_unknown_template_rejected(self) -> None:
        cfg = ModelPluginConfig(name="m", features=["f1"], template="xgboost")
        with pytest.raises(ModelPipelineError, match="未知模板"):
            cfg.validate()

    @pytest.mark.parametrize(
        "params",
        [
            pytest.param({"window": -1, "step": 20}, id="window 负"),
            pytest.param({"window": 0, "step": 0}, id="step 零"),
        ],
    )
    def test_bad_window_step_rejected(self, params) -> None:
        cfg = ModelPluginConfig(name="m", features=["f1"], params=params)
        with pytest.raises(ModelPipelineError, match="params"):
            cfg.validate()

    def test_bad_horizon_rejected(self) -> None:
        cfg = ModelPluginConfig(
            name="m", features=["f1"],
            target={"type": "return_sign", "horizon": 0},
        )
        with pytest.raises(ModelPipelineError, match="horizon"):
            cfg.validate()

    def test_valid_config_passes(self) -> None:
        cfg = ModelPluginConfig(
            name="m", features=["f1"], template="rolling_linear",
            params={"window": 50, "step": 20},
            target={"type": "return", "horizon": 2},
        )
        cfg.validate()  # 不抛即通过


class TestMakeTarget:
    """目标构造手工核算：close=[100,100,121,133]、horizon=2 时
    pct_change(2)=[nan,nan,0.21,0.33]，shift(-2) 后 t0/t1 取 0.21/0.33。"""

    @pytest.fixture
    def df(self) -> pd.DataFrame:
        return pd.DataFrame({"close": [100.0, 100.0, 121.0, 133.0]})

    def test_return_sign_manual(self, df) -> None:
        out = _make_target(df, {"type": "return_sign", "horizon": 2})
        assert out.iloc[0] == 1.0
        assert out.iloc[1] == 1.0
        assert pd.isna(out.iloc[2])
        assert pd.isna(out.iloc[3])

    def test_return_keeps_magnitude(self, df) -> None:
        out = _make_target(df, {"type": "return", "horizon": 2})
        assert out.iloc[0] == pytest.approx(0.21)
        assert out.iloc[1] == pytest.approx(0.33)
        assert pd.isna(out.iloc[2])

    def test_negative_sign(self) -> None:
        df = pd.DataFrame({"close": [100.0, 100.0, 90.0, 80.0]})
        out = _make_target(df, {"type": "return_sign", "horizon": 2})
        # t0 前向收益 (90-100)/100 = -0.1 → sign=-1
        assert out.iloc[0] == -1.0


class TestDataInsufficiencyMessage:
    """数据量门槛动态化（CARD-12）：文案与判断条件同源 min_train+step+5。

    修复前文案硬编码 225——非默认 step 时误报（step=50 实需 255）。
    """

    def test_default_step_message_225(self) -> None:
        df = make_df(220)  # < 200+20+5
        df["f1"] = 1.0
        cfg = ModelPluginConfig(name="m", features=["f1"])
        with pytest.raises(ModelPipelineError, match=r"需 >= 225"):
            fit_predict_walk_forward(df, cfg)

    def test_custom_step_message_reflects_step(self) -> None:
        df = make_df(230)  # < 200+50+5=255（默认门槛下反而够）
        df["f1"] = 1.0
        cfg = ModelPluginConfig(name="m", features=["f1"], params={"step": 50})
        with pytest.raises(ModelPipelineError, match=r"需 >= 255"):
            fit_predict_walk_forward(df, cfg)

    def test_custom_step_threshold_boundary(self) -> None:
        """n=255（恰过 step=50 门槛）不再报数据不足——落入后续校验。"""
        df = make_df(255)
        df["f1"] = np.nan  # 走到"无样本外"而非数据不足
        cfg = ModelPluginConfig(name="m", features=["f1"], params={"step": 50})
        with pytest.raises(ModelPipelineError, match="无样本外评估样本"):
            fit_predict_walk_forward(df, cfg)


class TestWalkForwardEdges:
    """walk-forward 边界：未知目标类型 / inf 训练失败 / NaN 特征两条路径。"""

    def test_unknown_target_type_rejected(self) -> None:
        # validate 不校验 target.type：未知类型在目标构造处显式拒绝
        df = make_df(230)
        df["f1"] = 1.0
        cfg = ModelPluginConfig(
            name="m", features=["f1"], target={"type": "bogus", "horizon": 4}
        )
        with pytest.raises(ModelPipelineError, match="未知目标类型"):
            fit_predict_walk_forward(df, cfg)

    @pytest.mark.parametrize("bad", [np.inf, -np.inf],
                             ids=["正inf", "负inf"])
    def test_inf_feature_raises_training_failure(self, bad) -> None:
        # inf 不被 notna 过滤，进入 sklearn（fit_transform 与 fit）触发
        # ValueError → 「训练失败」包装上抛（阶段四属性补强：正负 inf
        # 对称——任一方向非有限值都不得绕过包装裸抛）
        df = make_df(230)
        df["f1"] = 1.0
        df.loc[df.index[150], "f1"] = bad
        cfg = ModelPluginConfig(name="m", features=["f1"], params={"step": 20})
        with pytest.raises(ModelPipelineError, match="训练失败"):
            fit_predict_walk_forward(df, cfg)

    def test_all_nan_feature_skips_training_no_oos(self) -> None:
        # 特征全 NaN：每个训练窗有效样本 0 < 50 全部跳过；无信号 → 无 OOS 样本
        df = make_df(230)
        df["f1"] = np.nan
        cfg = ModelPluginConfig(name="m", features=["f1"], params={"step": 20})
        with pytest.raises(ModelPipelineError, match="无样本外评估样本"):
            fit_predict_walk_forward(df, cfg)

    def test_feature_nan_tail_fits_but_never_predicts(self) -> None:
        # 训练窗有效、预测段（shift 后）全 NaN：拟合发生但无段可预测
        df = make_df(230)
        f1 = np.ones(230)
        f1[199:] = np.nan  # X=shift(1) → X[200:] 全 NaN，两个预测段均无效
        df["f1"] = f1
        cfg = ModelPluginConfig(
            name="m", features=["f1"], template="rolling_linear",
            params={"step": 20},
        )
        with pytest.raises(ModelPipelineError, match="无样本外评估样本"):
            fit_predict_walk_forward(df, cfg)

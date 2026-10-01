"""模型插件管道：滚动回归模板 + 时序切分内置（5.3）。

防作弊契约（spec strategy-expression / D6）：
- 特征只用 t-1 及以前：特征矩阵整体 shift(1) 后进模型（INV-R5）；
- 训练不含未来：walk-forward 滚动拟合——初始窗拟合 → 预测下一段 →
  扩窗重拟合 → 再预测，拼接产出全序列**样本外**信号（回测用的就是
  这个序列，不存在全样本拟合信号）；
- 模板参数化：rolling_logistic（方向分类）/ rolling_linear（收益回归），
  基于 scikit-learn（已装），不引入 statsmodels。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.signals.formula import FormulaError


class ModelPipelineError(ValueError):
    """模型管道错误（配置非法 / 数据不足 / 前视陷阱）。"""


@dataclass
class ModelPluginConfig:
    """模型插件配置（附.4：{name, features[], template, params, target}）。"""

    name: str
    features: List[str]
    template: str = "rolling_logistic"   # rolling_logistic | rolling_linear
    params: Dict[str, float] = field(default_factory=lambda: {"window": 0, "step": 20})
    target: Dict[str, object] = field(
        default_factory=lambda: {"type": "return_sign", "horizon": 4})

    def validate(self) -> None:
        if not self.features:
            raise ModelPipelineError("features 不能为空")
        if self.template not in ("rolling_logistic", "rolling_linear"):
            raise ModelPipelineError(f"未知模板: {self.template}")
        window = int(self.params.get("window", 0))
        step = int(self.params.get("step", 20))
        if window < 0 or step < 1:
            raise ModelPipelineError("params.window>=0 且 params.step>=1")
        horizon = int(self.target.get("horizon", 4))
        if horizon < 1:
            raise ModelPipelineError("target.horizon >= 1")


@dataclass
class ModelFitResult:
    """fit/predict 产物：样本外信号列 + OOS 摘要（缓存，权威在验证记录）。"""

    signal: pd.Series                 # 样本外信号（方向：-1/0/1 或回归值）
    oos_summary: Dict[str, float]     # 样本外表现摘要
    bars_trained: int = 0
    n_refits: int = 0


def _make_target(df: pd.DataFrame, target: Dict[str, object]) -> pd.Series:
    """构造学习目标（labels 只用于训练窗内的过去样本——切分见 walk_forward）。"""
    ttype = target.get("type", "return_sign")
    horizon = int(target.get("horizon", 4))
    fwd = df["close"].pct_change(horizon).shift(-horizon)  # t→t+h 前向收益
    if ttype == "return_sign":
        return np.sign(fwd)
    if ttype == "return":
        return fwd
    raise ModelPipelineError(f"未知目标类型: {ttype}")


def fit_predict_walk_forward(
    df: pd.DataFrame,
    config: ModelPluginConfig,
) -> ModelFitResult:
    """walk-forward 滚动拟合预测，产出全序列样本外信号。

    特征 t-1 化：X = df[features].shift(1)（INV-R5——t 行特征取 t-1 值）。
    滚动：初始窗（min_train，默认 200 bar）拟合 → 预测下 step 段 →
    扩窗（或滑窗 window>0 时）→ 重拟合 → 拼接。
    """
    config.validate()
    missing = [c for c in config.features if c not in df.columns]
    if missing:
        raise ModelPipelineError(f"特征列缺失: {missing}")

    n = len(df)
    min_train = 200
    step = int(config.params.get("step", 20))
    if n < min_train + step + 5:
        raise ModelPipelineError(f"数据不足（{n} bar；需 >= {min_train + step + 5}）")

    X_raw = df[config.features].astype(float)
    X = X_raw.shift(1)  # 特征只用 t-1 及以前（防前视第一道）
    y = _make_target(df, config.target)

    window = int(config.params.get("window", 0))  # 0=expanding，>0=滑动窗

    signal = pd.Series(np.nan, index=df.index, name=f"model_{config.name}")
    n_refits = 0

    from sklearn.linear_model import LinearRegression, LogisticRegression

    start = min_train
    while start < n:
        train_end = start
        train_lo = max(0, train_end - window) if window > 0 else 0
        X_tr = X.iloc[train_lo:train_end]
        y_tr = y.iloc[train_lo:train_end]
        mask = X_tr.notna().all(axis=1) & y_tr.notna()
        X_tr, y_tr = X_tr[mask], y_tr[mask]
        if len(X_tr) < 50:
            start += step
            continue

        model = (
            LogisticRegression(max_iter=200)
            if config.template == "rolling_logistic"
            else LinearRegression()
        )
        try:
            model.fit(X_tr.values, y_tr.values)
        except ValueError as exc:
            raise ModelPipelineError(f"训练失败: {exc}") from exc

        # 预测下一段（严格在未来样本上）
        seg = slice(start, min(start + step, n))
        X_seg = X.iloc[seg]
        valid = X_seg.notna().all(axis=1)
        if valid.any():
            pred = model.predict(X_seg[valid].values)
            # 单步赋值（chained assignment 在 CoW 语义下不写回——弃用写法）
            seg_positions = np.arange(start, min(start + step, n))[valid.values]
            signal.iloc[seg_positions] = pred
        n_refits += 1
        start += step

    # OOS 摘要（信号与真实结果对齐：仅评估有信号样本）
    oos = signal.notna() & y.notna()
    n_oos = int(oos.sum())
    if n_oos == 0:
        raise ModelPipelineError("无样本外评估样本")
    if config.template == "rolling_logistic":
        acc = float((np.sign(signal[oos]) == y[oos]).mean())
        summary = {"oos_accuracy": round(acc, 4), "n_oos": n_oos}
    else:
        corr = float(np.corrcoef(signal[oos], y[oos])[0, 1])
        summary = {"oos_correlation": round(corr, 4), "n_oos": n_oos}

    return ModelFitResult(signal=signal, oos_summary=summary,
                          bars_trained=n, n_refits=n_refits)

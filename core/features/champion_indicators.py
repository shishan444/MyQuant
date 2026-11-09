"""冠军指标实现（add-champion-indicators——R216 实测五件冠军内置）。

五件的计算实现：ShannonEntropy/PermutationEntropy（信息熵族）/
EfficiencyRatio（Kaufman 效率比）——information 类目；CUSUM（双边
累积和变点）/GARCHFilter（GARCH(1,1) 条件波动率滤波）——structure
类目。自 indicators.py 拆出（structure_indicators 先例）；分派仍单点
（indicators._compute_indicator elif 链）。

逐列口径三分类（design D2 总则——本文件各 docstring 逐列标注）：
- 结构统计列（entropy/pe/er）：rolling 窗含当根（当根行情合法参与
  「截至当根的行情结构」描述）；
- 分位基准列（*_pct）：同列 rolling(rank_window).rank(pct=True).shift(1)
  ——前一根取值，供当根收盘比较防自引用（BoxChannel 同构）；
- 递推状态列（s_pos/s_neg/cp_*/cond_vol）：每根只用当根及历史数据。

复算锚（works/research/consensus-ema 冻结脚本，公式在本 docstring 族
与测试内独立重演、不进生产 import）：香农熵 r9_d2_arms.py:34、排列熵
r7_bench.py:86、ER r7_bench.py:137＋r9_d2_arms.py:45、CUSUM
r7_bench.py:141、GARCH r7_breakout.py:116-125。

熵族窗口口径注记（2026-11-09 裁决 a）：冻结 demo 循环窗 sv[i-w:i]
不含当根（研究脚本防前视一格保守习惯），本实现按 D2 总则取 rolling
含当根窗（spec MUST Requirement 正文为行为权威、demo 为研究脚本参照
——结构统计列族统一含当根，BoxRegime width 先例保总则一致）；同一
公式、窗右端差一根：内置 entropy[i] ≡ demo entropy[i+1]（错位性质
断言入测试）。
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd


def _tri_state(close: pd.Series) -> pd.Series:
    """三态符号序列：sign(close.diff()) → {0,1,2}（跌/平/涨；首位
    diff NaN 按 0 处理为平——demo r9_d2_arms.py:35 形制）。"""
    return np.sign(close.diff()).fillna(0) + 1


def _shannon_entropy_core(win: np.ndarray) -> float:
    """窗内三态分布熵 ÷ log(3) 归一（demo r9_d2_arms.py:40-42 公式：
    非零概率的 -Σp·ln(p)，除以 log(3)；值域 [0,1]）。"""
    ps = [(win == k).mean() for k in (0, 1, 2)]
    ps = [p for p in ps if p > 0]
    return -sum(p * np.log(p) for p in ps) / np.log(3)


def _pct_rank(series: pd.Series, rank_window: int) -> pd.Series:
    """分位基准列：同列 rolling(rank_window).rank(pct=True).shift(1)
    （前一根取值——防当根自引用；BoxChannel/r9_d2_arms.py:43 同构）。"""
    return series.rolling(rank_window).rank(pct=True).shift(1)


def compute_shannon_entropy(
    df: pd.DataFrame, name: str, params: Dict[str, Any],
) -> Dict[str, pd.Series]:
    """ShannonEntropy：滚动窗三态符号分布熵（信息论结构度量）。

    公式（demo r9_d2_arms.py:34-43 同公式、窗含当根——D2① 结构统计
    列）：s=sign(close.diff())+1 三态 {0,1,2}；entropy = 窗内 s 分布
    熵 ÷ log(3) 归一（值域 [0,1]：恒定/单态=0，均匀三态=1）；窗不足期
    输出 NaN（非零填充）。entropy_pct = entropy 列 rolling(rank_window)
    百分位排名后 shift(1)（D2② 分位基准列——前一根取值，供当根收盘
    比较防自引用）。

    防前视论证：entropy 为 rolling 后顾窗（含当根不含未来根）——bar t
    只依赖 [t−window+1, t]；entropy_pct 取 rank 后 shift(1)——t 根值
    由 [t−rank_window, t−1] 的 entropy 决定，修改当根收盘不改变 t 根
    entropy_pct（只改 t+1 起）。修改 t+1 及之后数据不改变 t 及之前
    输出（截断不变量）。

    语义分诊（模式一环境开关件）：低熵=近期涨跌方向分布集中=行情有
    结构（可识别状态）；高熵=方向接近等概率=噪声态。demo gate 消费
    形制：entropy_pct lt 0.35 放行（低熵分位）。与 FractalEntropy
    （ml 类 mfe_score）的分诊：分形维数熵≠信息论三态熵——R245 探针
    实证 FractalEntropy 内置可用但判别力弱于本地香农熵（journal
    R217 :239）；结构方向分布度量用本件，分形自相似度量用 FractalEntropy。

    R216 适用域（研究档案 R217 段）：状态件全场领先（t+6.71/+7.79）
    ——状态内收益区分度语义，非入场触发；适合环境开关/条件过滤。
    """
    window = int(params["window"])
    rank_window = int(params["rank_window"])

    s = _tri_state(df["close"])
    entropy = s.rolling(window).apply(_shannon_entropy_core, raw=True)
    entropy_pct = _pct_rank(entropy, rank_window)

    return {
        f"shan_{window}_{rank_window}_entropy": entropy,
        f"shan_{window}_{rank_window}_entropy_pct": entropy_pct,
    }


def _perm_entropy_core(win: np.ndarray, m: int) -> float:
    """窗内 pattern_len 长度次序模式计数熵 ÷ log(实际出现模式数)
    （demo r7_bench.py:90-96 公式——归一分母为实际出现模式数而非
    理论上限；单模式窗熵=0——数学上退化分布熵为零，demo 形制下
    log(1)=0 分母退化，本实现按零处理）。"""
    pats: Dict[tuple, int] = {}
    for j in range(len(win) - m + 1):
        p = tuple(win[j:j + m])
        pats[p] = pats.get(p, 0) + 1
    if len(pats) <= 1:
        return 0.0
    p = np.array(list(pats.values())) / (len(win) - m + 1)
    return float(-(p * np.log(p)).sum() / np.log(len(pats)))


def compute_permutation_entropy(
    df: pd.DataFrame, name: str, params: Dict[str, Any],
) -> Dict[str, pd.Series]:
    """PermutationEntropy：滚动窗排列熵（次序模式复杂度）。

    公式（demo r7_bench.py:86-97 同公式、窗含当根——D2① 结构统计
    列）：lr=sign(log(close).diff())+1 三态；窗内 lr 序列的
    pattern_len 长度模式（次序元组）计数分布熵，归一分母 = log(实际
    出现模式数)（demo :95 形制——只出现 2 种模式时熵 ÷ log(2)；与
    理论上限分母的差异在低熵段显著，docstring 口径注记）；单模式窗
    熵=0。pe_pct = pe 列 rolling(window).rank(pct=True).shift(1)
    （D2② 分位基准列——rank 窗复用 window：本件两参数形制，无独立
    rank_window；pattern_len 非周期参数不进 warmup 白名单）。

    防前视论证：与香农熵件同构——pe 为 rolling 后顾窗（含当根），
    pe_pct shift(1) 前一根取值；截断不变量同款。

    语义分诊：低排列熵=窗内涨跌次序模式重复=存在可识别的节奏结构；
    高排列熵=次序接近随机。与香农熵的分诊：香农熵看方向分布的「成分
    比」、排列熵看次序模式的「排列比」——后者对时序结构更敏感
    （R216 排列熵 +5.75 亦为状态件）。
    """
    window = int(params["window"])
    pattern_len = int(params["pattern_len"])

    lr = np.sign(np.log(df["close"]).diff()).fillna(0) + 1
    pe = lr.rolling(window).apply(
        _perm_entropy_core, raw=True, kwargs={"m": pattern_len})
    pe_pct = _pct_rank(pe, window)

    return {
        f"perm_{window}_{pattern_len}_pe": pe,
        f"perm_{window}_{pattern_len}_pe_pct": pe_pct,
    }


def compute_efficiency_ratio(
    df: pd.DataFrame, name: str, params: Dict[str, Any],
) -> Dict[str, pd.Series]:
    """EfficiencyRatio：Kaufman 效率比（净位移 ÷ 路径长度）。

    公式（demo r7_bench.py:137-138 原式、rolling 含当根——D2① 结构
    统计列）：er = |close.diff(window)| ÷ close.diff().abs().rolling
    (window).sum()（值域 [0,1]：单调序列=1，交替锯齿→0）。er_pct =
    er 列 rolling(rank_window).rank(pct=True).shift(1)（D2② 分位基准
    列）。

    防前视论证：分子分母均为 rolling 后顾窗（含当根不含未来根）；
    er_pct shift(1) 前一根取值——修改当根收盘不改变 t 根 er_pct；
    截断不变量同熵族。

    双形制消费（design D1）：主列默认 w=96（R216 面板冻结值）；demo
    gate 的 w=24（r9_d2_arms.py:45）为消费侧传参复现——window 参数
    显式传 24 时 er_pct 与 demo 逐位一致（复算锚）。

    语义分诊（模式一环境开关件）：高 ER=净位移占比大=走势干净（趋势
    有效）；低 ER=路径曲折（震荡）。demo gate 消费形制：er_pct gt
    0.65 放行。R216 适用域：状态件（t+4.60「最稳状态件」）——环境
    开关/趋势过滤，非入场触发。
    """
    window = int(params["window"])
    rank_window = int(params["rank_window"])

    close = df["close"]
    er = close.diff(window).abs() / close.diff().abs().rolling(window).sum()
    er_pct = _pct_rank(er, rank_window)

    return {
        f"erf_{window}_{rank_window}_er": er,
        f"erf_{window}_{rank_window}_er_pct": er_pct,
    }


def _cusum_core(
    z: np.ndarray, drift: float, threshold: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """双边累积和递推核（demo r7_bench.py:141-148 递推形制——
    输入注入面：测试与下游 Python 代码经显式 import 直调本核注入
    标准化序列）。

    NaN 输入：s 置 0、cp=False（未标准化期无累积）。返回
    (s_pos, s_neg, cp_up, cp_down)；cp 列为 1.0/0.0 数值布尔。
    """
    n = len(z)
    s_pos = np.zeros(n)
    s_neg = np.zeros(n)
    cp_up = np.zeros(n)
    cp_down = np.zeros(n)
    sp, sn = 0.0, 0.0
    for i in range(n):
        zi = z[i]
        if np.isnan(zi):
            sp, sn = 0.0, 0.0
            continue
        sp = max(0.0, sp + zi - drift)
        sn = min(0.0, sn + zi + drift)
        if sp > threshold:
            cp_up[i] = 1.0
            sp = 0.0
        elif sn < -threshold:
            cp_down[i] = 1.0
            sn = 0.0
        s_pos[i] = sp
        s_neg[i] = sn
    return s_pos, s_neg, cp_up, cp_down


def compute_cusum(
    df: pd.DataFrame, name: str, params: Dict[str, Any],
) -> Dict[str, pd.Series]:
    """CUSUM：双边累积和变点检测（rolling zscore 标准化内置）。

    公式（demo r7_bench.py:141-148 递推形制）：输入标准化 z=对数收益
    r 的 rolling(sd_window) zscore（(r−μ)/σ，件内自包含）；递推
    s_pos = max(0, s_pos + z − drift)、s_neg = min(0, s_neg + z +
    drift)；s_pos > threshold（严格大于，恰等不触发）当根 cp_up=True
    且 s_pos 归零；s_neg < −threshold 镜像（elif——同一根 z 不能同时
    大到触发上侧又小到触发下侧，与 demo 分支序一致）。NaN 期（zscore
    窗未满）s 置 0、cp=False（未标准化期无累积，语义正确而非数据
    缺失）。

    四列口径（D2③ 递推状态列）：s_pos/s_neg 为递推状态（每根只用
    当根及历史数据——修改 t+1 及之后数据不改变 t 及之前输出）；
    cp_up/cp_down 为当根触发标志布尔数值列（True=1.0/False=0.0，
    pattern 族先例——条件引用推荐 eq 1/gt 0.5；「cp_up gt 1」恒 False
    勿踩）。

    防前视论证：递推只读 ≤当根数据（z[t] 由 [t−sd_window+1, t] 的
    对数收益标准化，s 由历史 s 与当根 z 产生）——递推列截断不变量：
    前 k 根输出与全序列前 k 根逐位一致。

    跨指标组合表达路径（披露——不在件内内置）：demo E 族「CUSUM of
    GARCH-z」（对 GARCH 条件波动率标准化的残差做变点检测）的组合
    消费当前形态=模块内递推核 _cusum_core（私有实现——测试与下游
    Python 代码经显式 import 直调注入标准化序列，如对 GARCHFilter
    cond_vol 标准化的残差——demo E 族 r7_breakout.py 形制）；
    formula.py 表达式组合属未来扩展（fx 白名单现无 cusum 词——
    design Non-Goals「rolling_rank 进公式白名单」同族），不在当前
    能力面；本件仅内置 rolling zscore 自包含形制。

    R216 适用域：上漂 −4.34（反向用——变点后动量衰减语义）；变点
    事件消费（cp_up eq 1 = 当根检出上漂变点）＋within_bars_after
    时序动作（变点后 N 根内）。
    """
    sd_window = int(params["sd_window"])
    drift = float(params["drift"])
    threshold = float(params["threshold"])

    r = np.log(df["close"]).diff()
    mu = r.rolling(sd_window).mean()
    sd = r.rolling(sd_window).std()
    z = ((r - mu) / sd).to_numpy(dtype=float)

    s_pos, s_neg, cp_up, cp_down = _cusum_core(z, drift, threshold)

    idx = df.index
    return {
        f"cus_{sd_window}_{drift}_{threshold}_s_pos":
            pd.Series(s_pos, index=idx),
        f"cus_{sd_window}_{drift}_{threshold}_s_neg":
            pd.Series(s_neg, index=idx),
        f"cus_{sd_window}_{drift}_{threshold}_cp_up":
            pd.Series(cp_up, index=idx),
        f"cus_{sd_window}_{drift}_{threshold}_cp_down":
            pd.Series(cp_down, index=idx),
    }


def compute_garch_filter(
    df: pd.DataFrame, name: str, params: Dict[str, Any],
) -> Dict[str, pd.Series]:
    """GARCHFilter：GARCH(1,1) 条件波动率滤波（固定参数递推）。

    公式（demo r7_breakout.py:116-125 garch_run 滤波形制——design D1
    取用裁定）：r = log(close).diff()（首位按 0）；σ²₀ 与 ω = 装载
    头部 init_window 根 r 的样本方差（np.var 总体方差，ddof=0——demo
    形制）× (1−α−β) 平稳锚定；递推 s2[i] = ω + α·r[i−1]² + β·
    s2[i−1]（s2[0]=σ²₀）；cond_vol = √s2。递推天然防前视（D2③ 递推
    状态列：s2[i] 只用 r[i−1] 与 s2[i−1]——当根及历史）。

    输出面（消费语义披露）：本函数对全装载序列输出（demo 同形——
    init 窗内亦有递推值）；「init 窗只用不算」指装载消费面——装载层
    按 derive_warmup 前扩（init_window.max 500×3=1500），请求区间内
    的值已过锚定窗，init 前缀段不被消费。跨指标组合消费
    （r7_bench.py:33 pred_garch 滚动校准分位带）是本列的下游组合，
    不入件。

    参数估计边界：固定参数递推（α/β/init_window 显式传参）；MLE 参数
    估计不在本件（数学公式清单边界区 B1 未裁决收录）。

    平稳性与收敛（cross_params 拒绝面＋全域性质）：α+β≥1 经注册表
    cross_params 结构化拒绝（平稳性——含等号）；初值冲击衰减 β^k<1%
    所需 k=log(0.01)/log(β)（β 上界 0.99 → k≈458）≤ 收敛尾 2×
    init_window 域内最小值（2×250=500）——init_window 下界 250 的
    设定使全域收敛断言成立（性质断言入测试）。敏感性披露：β 越大
    收敛越慢（β=0.90 → k≈44；β=0.99 → k≈458），高 β 参数下前 500
    根锚定段冲击残留 <1%。

    复算路径披露：demo init=2000 非 250-500 域内值——纯核直调对账
    路径（compute_garch_filter 直传 init_window=2000，参数域校验在
    登记/装载层不在 compute 层），与 garch_run([]) 逐位一致（测试锚）。

    R216 适用域：波幅预判辅助与状态开关（校准 0.830）——止损距离
    自适应与仓位调节的辅助输入；不当入场信号（连续正值列语义）。
    supported_conditions=[gt,lt]（阈值比较）。
    """
    alpha = float(params["alpha"])
    beta = float(params["beta"])
    init_window = int(params["init_window"])

    r = np.log(df["close"]).diff().fillna(0).to_numpy(dtype=float)
    var0 = float(np.var(r[:init_window]))
    omega = var0 * (1.0 - alpha - beta)

    n = len(r)
    s2 = np.zeros(n)
    s2[0] = var0
    for i in range(1, n):
        s2[i] = omega + alpha * r[i - 1] ** 2 + beta * s2[i - 1]
    cond_vol = pd.Series(np.sqrt(s2), index=df.index)

    return {
        f"garch_{alpha}_{beta}_{init_window}_cond_vol": cond_vol,
    }

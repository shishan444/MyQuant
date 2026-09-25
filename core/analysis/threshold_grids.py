"""条件阈值刻度表（分析域共享常量；add-screen-patterns D7 延迟决策兑现）。

0.2.5 全谱扫描时为模板内私有常量（「第二个消费者出现时提升注册表」）；
add-param-sweep 成为第二个消费者，提升为分析域共享模块——扫描域与
检查域的网格 MUST 同源（同一条件在扫描时的刻度集合与参数稳健性检查
时的邻域刻度出自同一份事实，属正确性要求而非复用便利）。

形制：指标名 -> 列选择器（None=单输出主列；str=列名包含片段）-> 动作 -> 刻度
"""
from __future__ import annotations

from typing import Dict, List, Optional

VALUE_GRIDS: Dict[str, Dict[Optional[str], Dict[str, List[float]]]] = {
    # momentum / 有界振荡类
    "RSI": {None: {"lt": [20, 25, 30, 35], "gt": [65, 70, 75, 80]}},
    "MFI": {None: {"lt": [20, 25, 30], "gt": [70, 75, 80]}},
    "Williams %R": {None: {"lt": [-90, -80], "gt": [-20, -10]}},
    "CMO": {None: {"lt": [-40, -25], "gt": [25, 40]}},
    "CCI": {None: {"lt": [-150, -100], "gt": [100, 150]}},
    # trend strength / 方向
    "ADX": {None: {"gt": [20, 25, 30], "lt": [15, 20]}},
    "Aroon": {"aroon_up": {"gt": [70, 80]},
              "aroon_down": {"gt": [70, 80]},
              "aroon_osc": {"gt": [50, 70], "lt": [-70, -50]}},
    "ROC": {None: {"lt": [-5, -3], "gt": [3, 5]}},
    "TRIX": {None: {"lt": [0], "gt": [0]}},
    "MACD": {"macd_histogram": {"lt": [0], "gt": [0]}},
    "Stochastic": {"stoch_k": {"lt": [20, 25], "gt": [75, 80]},
                   "stoch_d": {"lt": [20, 25], "gt": [75, 80]}},
    # volume / 量能
    "RVOL": {None: {"gt": [1.5, 2.0, 3.0], "lt": [0.5, 0.8]}},
    "VROC": {None: {"gt": [50, 100], "lt": [-50, -100]}},
    "CMF": {None: {"gt": [0.1, 0.2], "lt": [-0.1, -0.2]}},
    # derivatives（数据缺列时整指标跳过并声明）
    "OI_ZScore": {None: {"lt": [-2.0], "gt": [2.0]}},
    "FundingZScore": {None: {"lt": [-2.0], "gt": [2.0]}},
    "OI_ChangeRate": {None: {"lt": [-10, -5], "gt": [5, 10]}},
    "OIPriceDivergence": {None: {"lt": [-0.3, -0.2], "gt": [0.2, 0.3]}},
}

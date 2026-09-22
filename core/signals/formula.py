"""受限公式语言：求值器 + 白名单函数库 + 静态校验三关（5.1）。

方案 C 公式层（D6）：AI 用受限表达式定义新指标——白名单函数仅含
"看过去"的算子，静态校验三关先行（无外部访问 / 列引用合法 / 禁
负向 shift），通过后才可注册与求值。求值器本身无 IO 能力。

DSL：数值常量 + 列名（来自可用信号集）+ 白名单函数调用 + 四则与
一元负号。输出与输入 df 等长的 Series（滚动窗口头部自然为 NaN，
由消费端按需处理）。
"""
from __future__ import annotations

import ast
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd


class FormulaError(ValueError):
    """公式静态校验/求值错误（附位置信息）。"""


# ---------------------------------------------------------------- 白名单函数

def _f_shift(s: pd.Series, n: int) -> pd.Series:
    n = int(n)
    if n < 0:
        raise FormulaError("shift 仅允许非负参数（负值=看向未来）")
    return s.shift(n)


def _f_diff(s: pd.Series, n: int) -> pd.Series:
    return s - _f_shift(s, n)


def _f_rolling(fn_name: str, s: pd.Series, n: int) -> pd.Series:
    n = int(n)
    if n < 1:
        raise FormulaError(f"{fn_name} 窗口须 >= 1")
    return getattr(s.rolling(n, min_periods=1), fn_name)()


def _f_zscore(s: pd.Series, n: int) -> pd.Series:
    mean = _f_rolling("mean", s, n)
    std = _f_rolling("std", s, n)
    std = std.replace(0, np.nan)
    return ((s - mean) / std).fillna(0.0)


def _f_ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=int(n), min_periods=1).mean()


def _f_corr(a: pd.Series, b: pd.Series, n: int) -> pd.Series:
    return a.rolling(int(n), min_periods=2).corr(b)


#: 白名单函数库（全部只看过去；shift/diff 的负参被动态拦截，静态先拦）
FORMULA_FUNCTIONS: Dict[str, Callable[..., pd.Series]] = {
    "shift": _f_shift,
    "diff": _f_diff,
    "rolling_mean": lambda s, n: _f_rolling("mean", s, n),
    "rolling_std": lambda s, n: _f_rolling("std", s, n),
    "rolling_max": lambda s, n: _f_rolling("max", s, n),
    "rolling_min": lambda s, n: _f_rolling("min", s, n),
    "rolling_sum": lambda s, n: _f_rolling("sum", s, n),
    "zscore": _f_zscore,
    "ema": _f_ema,
    "corr": _f_corr,
    "abs": lambda s: s.abs(),
}

#: 各函数的参数个数（静态校验用）
_FORMULA_ARITY = {
    "shift": 2, "diff": 2, "rolling_mean": 2, "rolling_std": 2,
    "rolling_max": 2, "rolling_min": 2, "rolling_sum": 2,
    "zscore": 2, "ema": 2, "corr": 3, "abs": 1,
}

# 负向 shift 静态拦截的函数（第 2 参须为 >= 0 常量）
_SHIFT_LIKE = {"shift", "diff"}

#: 各白名单函数的回看窗口参数位置（0 起；abs 无窗口项不在此表）。
#: rolling(n) 头部 NaN 需 n-1 根前置、shift/diff(n) 需 n 根、ema(span=n)
#: 指数衰减近似 n、zscore/corr 同 rolling——warmup 推导取参数值本身
#: （保守取向，宁可多预热）。
_WINDOW_PARAM_INDEX = {
    "shift": 1, "diff": 1,
    "rolling_mean": 1, "rolling_std": 1, "rolling_max": 1,
    "rolling_min": 1, "rolling_sum": 1,
    "zscore": 1, "ema": 1, "corr": 2,
}

# ---------------------------------------------------------------- 静态校验

_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Call, ast.Name,
    ast.Load, ast.Constant, ast.operator, ast.unaryop,
)


def _const_value(node: ast.AST) -> tuple[bool, float]:
    """提取常量数值（处理 -3 的 UnaryOp(USub, Constant) 形式）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return True, float(node.value)
    if (isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub)
            and isinstance(node.operand, ast.Constant)
            and isinstance(node.operand.value, (int, float))):
        return True, -float(node.operand.value)
    return False, 0.0


def formula_lookback(expression: str) -> int:
    """表达式回看窗口静态推导：全部窗口参数字面量取 max，无窗口项 0。

    供验证回测 warmup 扩展（与内置 derive_warmup 取大、floor 兜底在
    调用处）。嵌套调用经 ast.walk 全树覆盖——
    rolling_mean(rolling_std(close, 30), 60) 取 60。语法错误返回 0
    （非法表达式到不了求值层，注册静态校验先行拦截）。
    """
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return 0
    lookback = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        param_idx = _WINDOW_PARAM_INDEX.get(node.func.id)
        if param_idx is None or len(node.args) <= param_idx:
            continue
        ok, val = _const_value(node.args[param_idx])
        if ok:
            lookback = max(lookback, int(val))
    return lookback


def validate_formula(expr: str, allowed_columns: set) -> List[str]:
    """静态校验三关，返回错误清单（空 = 通过）。

    1. 无外部访问：ast 节点白名单（Attribute/Subscript/Lambda/
       comprehension 等一律拒绝——表达式无对象模型）；
    2. 列引用合法：非函数名的 Name 必须在 allowed_columns；
    3. 禁负向 shift：shift/diff 第二参数必须是 >= 0 的整常量。
    """
    errors: List[str] = []
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        return [f"语法错误（位置 {exc.lineno}:{exc.offset}）: {exc.msg}"]

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            errors.append(
                f"不允许的语法构造 {type(node).__name__}（外部访问被禁止）")
            continue
        if isinstance(node, ast.BinOp) and not isinstance(
                node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            errors.append(f"不允许的运算符 {type(node.op).__name__}（仅四则）")
        if isinstance(node, ast.UnaryOp) and not isinstance(node.op, ast.USub):
            errors.append("仅允许一元负号")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            errors.append(f"非常量类型: {type(node.value).__name__}")

        if isinstance(node, ast.Call):
            # 函数调用：Name 限定、参数个数、负向 shift
            if not isinstance(node.func, ast.Name):
                errors.append("调用目标必须是白名单函数名")
                continue
            fname = node.func.id
            if fname not in FORMULA_FUNCTIONS:
                errors.append(f"非白名单函数: {fname}（可用: {sorted(FORMULA_FUNCTIONS)}）")
                continue
            if len(node.args) != _FORMULA_ARITY[fname]:
                errors.append(
                    f"{fname} 需要 {_FORMULA_ARITY[fname]} 个参数，实得 {len(node.args)}")
            if node.keywords:
                errors.append("不支持关键字参数")
            if len(node.args) >= 2:
                ok, val = _const_value(node.args[1])
                if fname in _SHIFT_LIKE:
                    if not ok:
                        errors.append(f"{fname} 第二参数必须是常量（静态可校验）")
                    elif val < 0:
                        errors.append(f"{fname} 负向平移被禁止（看向未来）: {val}")
                elif _FORMULA_ARITY[fname] == 2:
                    # 窗口类函数第二参同样须 >= 1 常量
                    if ok and val < 1:
                        errors.append(f"{fname} 窗口须 >= 1")

    # 列引用（非函数名的 Name）
    called_funcs = {
        n.func.id for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in called_funcs:
            if node.id not in allowed_columns:
                errors.append(f"列引用不存在: {node.id}（可用列经 /api/research/signals 查询）")

    return errors


# ---------------------------------------------------------------- 求值

def evaluate_formula(expr: str, df: pd.DataFrame,
                     extra_columns: Optional[Dict[str, pd.Series]] = None) -> pd.Series:
    """在 df 上求值（假定已通过 validate_formula）。

    Args:
        extra_columns: 求值上下文附加列（如既有公式插件的输出列）。
    """
    tree = ast.parse(expr, mode="eval")
    env = dict(extra_columns or {})

    def _ev(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return _ev(node.body)
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.BinOp):
            left, right = _ev(node.left), _ev(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            raise FormulaError(f"不允许的运算符 {type(node.op).__name__}")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -_ev(node.operand)
        if isinstance(node, ast.Name):
            if node.id in env:
                return env[node.id]
            if node.id in df.columns:
                return df[node.id].astype(float)
            raise FormulaError(f"列引用不存在: {node.id}")
        if isinstance(node, ast.Call):
            fname = node.func.id  # 已由静态校验保证
            args = [_ev(a) for a in node.args]
            try:
                return FORMULA_FUNCTIONS[fname](*args)
            except (TypeError, ValueError) as exc:
                raise FormulaError(f"{fname} 求值失败: {exc}") from exc
        raise FormulaError(f"不允许的节点 {type(node).__name__}")

    result = _ev(tree)
    if not isinstance(result, pd.Series):
        result = pd.Series(float(result), index=df.index)
    return result

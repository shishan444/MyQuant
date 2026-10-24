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


#: 表达式长度上限（资源限制关，10KB）：超长链使 ast.parse 与求值器
#: 递归深度随长度线性增长（深嵌套可在 10KB 内触顶，由 RecursionError
#: 转译兜底）；形制对齐 plugin_service 注册关的上限拦截口径
_MAX_EXPRESSION_CHARS = 10 * 1024


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


# ------------------------------------------------ 0.2.12 时序结构与逐元素族

def _shift1(x):
    """时序结构词的内部滞后（首元素 NaN 参与比较产 False——保守不触发）。

    常量参数滞后不变（crossover(close, 10) 的阈值线无时序——上穿固定
    阈值是常用结构，两参对称支持常量与 max/gt 的广播语义一致）。"""
    if not isinstance(x, pd.Series):
        return x
    return x.shift(1)


def _f_crossover(a, b) -> pd.Series:
    """a 上穿 b：当前 a>b 且前一根 a<=b（布尔直出）。"""
    return (a > b) & (_shift1(a) <= _shift1(b))


def _f_crossunder(a, b) -> pd.Series:
    """a 下穿 b：当前 a<b 且前一根 a>=b（对称词）。"""
    return (a < b) & (_shift1(a) >= _shift1(b))


def _to_bool(x) -> pd.Series:
    """统一布尔化（R5）：fillna(False) 前置——astype(bool) 会把 NaN 变
    True，破坏「缺口保守不触发」语义。数值输入按非零为真。"""
    if isinstance(x, pd.Series):
        return x.fillna(False).astype(bool)
    return bool(x)


#: 白名单函数库（全部只看过去；shift/diff 的负参被动态拦截，静态先拦）。
#: 0.2.12 扩至 21：时序结构族 crossover/crossunder（穿越时点布尔直出）
#: 与逐元素族 max/min/gt/lt/eq/logical_and/logical_or/logical_not
#: （比较与逻辑产布尔列，与数值混算按 0/1 参与）。
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
    # ---- 0.2.12 时序结构族（布尔直出）----
    "crossover": _f_crossover,
    "crossunder": _f_crossunder,
    # ---- 0.2.12 逐元素族（max/min 广播产数值；比较/逻辑布尔直出）----
    "max": np.maximum,
    "min": np.minimum,
    "gt": lambda a, b: a > b,
    "lt": lambda a, b: a < b,
    "eq": lambda a, b: a == b,
    "logical_and": lambda a, b: _to_bool(a) & _to_bool(b),
    "logical_or": lambda a, b: _to_bool(a) | _to_bool(b),
    "logical_not": lambda a: ~_to_bool(a),
}

#: 各函数的参数个数（静态校验用）
_FORMULA_ARITY = {
    "shift": 2, "diff": 2, "rolling_mean": 2, "rolling_std": 2,
    "rolling_max": 2, "rolling_min": 2, "rolling_sum": 2,
    "zscore": 2, "ema": 2, "corr": 3, "abs": 1,
    "crossover": 2, "crossunder": 2,
    "max": 2, "min": 2, "gt": 2, "lt": 2, "eq": 2,
    "logical_and": 2, "logical_or": 2, "logical_not": 1,
}

#: 结构固有回看贡献（0.2.12 R2——无参数位的时序结构词；与
#: _WINDOW_PARAM_INDEX 参数位贡献取 max）
_FIXED_LOOKBACK = {
    "crossover": 1,
    "crossunder": 1,
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

#: 公式插件回看窗口静态上限（bar 数）。防 warmup 换算溢出：窗口参数
#: 无上限时 formula_lookback 可达任意大，bar 数 × 周期秒数换算
#: pd.Timedelta 超上限（~9.2e9s，即 106751 天）即抛 OutOfBoundsTimedelta
#: （未捕获）。换算值域为 TF_MINUTES 周期表（最大 3d），1w 等高周期
#: 走 high_tf_warmup_span 不涉插件 lookback；10_000 对最苛刻的 1w
#: 防御边界（约 15_250 bar）留 34% 裕度，3d/1d 边界（35_584/106_752
#: bar）更宽，且远超实际指标窗口需求。plugin_service 注册关与
#: validate_formula 静态关共用此单一常量（F3 下沉）。
MAX_PLUGIN_LOOKBACK_BARS = 10_000

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
    except (RecursionError, MemoryError):
        # 对齐 validate_formula 解析收口面：深嵌套在 10KB 内可触顶
        # 解析递归——非法表达式到不了求值层（注册静态校验先行拦截），
        # lookback 按无窗口贡献 0（与语法错误分支同语义）
        return 0
    lookback = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        # 结构固有回看（crossover/crossunder 恒 1——0.2.12 R2）
        fixed = _FIXED_LOOKBACK.get(node.func.id)
        if fixed is not None:
            lookback = max(lookback, fixed)
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
    if len(expr) > _MAX_EXPRESSION_CHARS:
        return [f"表达式超长: {len(expr)} 字符（上限 {_MAX_EXPRESSION_CHARS}）"]
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        return [f"语法错误（位置 {exc.lineno}:{exc.offset}）: {exc.msg}"]
    except (RecursionError, MemoryError):
        # MemoryError：CPython C 级解析器栈溢出（"source too complex"）
        return ["表达式嵌套过深（解析递归超限，上限 10KB 内请减少嵌套）"]

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
                else:
                    # 窗口按 _WINDOW_PARAM_INDEX 实际参数位取参（对齐
                    # formula_lookback 既有写法；0.2.12 R1 边界保留——逐元素族
                    # 无窗口位不校验，gt(a, 0.5)/max(a, 0) 合法阈值用法）。
                    # 此前硬编码 args[1] 漏检 corr（窗口位 2）零窗口：
                    # corr(x, y, 0) 静态放行、注册成功、求值才炸。
                    param_idx = _WINDOW_PARAM_INDEX.get(fname)
                    if param_idx is not None and len(node.args) > param_idx:
                        ok, val = _const_value(node.args[param_idx])
                        if ok and val < 1:
                            errors.append(f"{fname} 窗口须 >= 1")
                        elif ok and val > MAX_PLUGIN_LOOKBACK_BARS:
                            errors.append(
                                f"{fname} 窗口超上限: {val:g} bar"
                                f"（上限 {MAX_PLUGIN_LOOKBACK_BARS}"
                                "——换算 warmup 时长将溢出时间表示）")

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
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        # 收口对齐 validate_formula :227-228（位置信息同形制）：
        # 不经静态校验直调求值器时语法错误不再裸穿
        raise FormulaError(
            f"语法错误（位置 {exc.lineno}:{exc.offset}）: {exc.msg}") from exc
    except (RecursionError, MemoryError) as exc:
        # MemoryError：CPython C 级解析器栈溢出（"source too complex"）
        raise FormulaError("表达式嵌套过深（解析递归超限）") from exc
    env = dict(extra_columns or {})

    def _ev(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return _ev(node.body)
        if isinstance(node, ast.Constant):
            # 静态关「非常量类型」同款防御（文案对齐）：str/None 等
            # 常量不经校验直落此处 float() 炸 TypeError/ValueError
            # 裸穿——收口为 FormulaError（上层捕获面可接住）
            if not isinstance(node.value, (int, float)):
                raise FormulaError(
                    f"非常量类型: {type(node.value).__name__}")
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
            # 防御面（SEC-4）：静态校验先行保证正常入口 func 必为 Name；
            # 不经 validate_formula 直调求值器时，func 非 Name（如 a.b(1)
            # 的 Attribute / a(1)(2) 的 Call）下 node.func.id 是裸
            # AttributeError——此处统一为 FormulaError（ValueError 子类，
            # 上层既有 except (TypeError, ValueError) 捕获面可接住）
            if not isinstance(node.func, ast.Name):
                raise FormulaError(
                    f"调用目标必须是函数名，得到 {type(node.func).__name__}")
            fname = node.func.id  # 已由静态校验保证
            args = [_ev(a) for a in node.args]
            try:
                return FORMULA_FUNCTIONS[fname](*args)
            except (TypeError, ValueError) as exc:
                raise FormulaError(f"{fname} 求值失败: {exc}") from exc
        raise FormulaError(f"不允许的节点 {type(node).__name__}")

    try:
        result = _ev(tree)
    except RecursionError as exc:
        raise FormulaError("表达式嵌套过深（求值递归超限）") from exc
    if not isinstance(result, pd.Series):
        result = pd.Series(float(result), index=df.index)
    return result

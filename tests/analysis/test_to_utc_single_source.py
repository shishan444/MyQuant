"""to_utc 单一实现防再内联锚（CARD-06 收敛守护）。

warmup_loader.to_utc 是 analysis 域时间参数归一的单一实现（naive 补
UTC、aware 转 UTC），九消费文件（indicator_query / market_snapshot /
case_inspection / validation_service / pattern_mining / condition_
stats / param_sweep / regime_split / screen_patterns）原十处本地
实现与内联已收敛。行为面由既有用例覆盖（逐字等价 + 半变体统一等价
锚见 test_event_and_pattern.py）；本锚守护收敛本身——消费文件再出现
本地时间归一定义即红（对齐 test_safe_symbol_source.py 形制）。
"""

import ast
import sys
from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

import core.analysis.indicator_query as iq
import core.analysis.market_snapshot as ms
import core.analysis.case_inspection as ci
import core.analysis.validation_service as vs

# templates/__init__ 对全部模板子模块绑定同名模板函数（遮蔽模块
# 属性），模块对象须经 sys.modules 取（test_threshold_grids 同款）
import core.analysis.templates  # noqa: F401 触发包初始化载入
cst = sys.modules["core.analysis.templates.condition_stats"]
psw = sys.modules["core.analysis.templates.param_sweep"]
pmn = sys.modules["core.analysis.templates.pattern_mining"]
rsp = sys.modules["core.analysis.templates.regime_split"]
sp = sys.modules["core.analysis.templates.screen_patterns"]

_CONSUMERS = [iq, ms, ci, vs, cst, psw, pmn, rsp, sp]


class TestToUtcSingleSource:
    def test_九消费文件无本地时间归一定义(self) -> None:
        for mod in _CONSUMERS:
            tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
            local_defs = [
                n.name for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef)
                and n.name in ("_to_utc", "to_utc", "_ts_utc")]
            # 薄壳豁免：regime_split._ts_utc 是 to_utc + 任意值构造的
            # 本地语义薄壳（五处 str/datetime 混合调用），允许存在但
            # 体内必须委托 to_utc
            allowed = {"_ts_utc"} if mod is rsp else set()
            illegal = set(local_defs) - allowed
            assert not illegal, (
                f"{mod.__name__} 出现本地时间归一定义 {illegal}——"
                "内联回潮，应 import core.analysis.warmup_loader.to_utc")
            if rsp is mod and "_ts_utc" in local_defs:
                src = Path(mod.__file__).read_text(encoding="utf-8")
                assert "to_utc(pd.Timestamp(v))" in src, (
                    "regime_split._ts_utc 薄壳须委托 to_utc")

    def test_正牌行为_三种输入形(self) -> None:
        from core.analysis.warmup_loader import to_utc
        naive = pd.Timestamp("2024-01-01T00:00:00")
        assert to_utc(naive).tzinfo is not None
        assert to_utc(naive).utcoffset().total_seconds() == 0
        aware = pd.Timestamp("2024-01-01T08:00:00+08:00")
        assert to_utc(aware) == pd.Timestamp("2024-01-01T00:00:00Z")
        utc = pd.Timestamp("2024-01-01T00:00:00Z")
        assert to_utc(utc) == utc


class TestHashJsonSingleSource:
    """CARD-11 收敛守护：hash_json 公开导出（hypothesis_service）为
    幂等身份键统一底座（假设 conditions/ranges_hash、模型插件
    config_hash 共用）——plugin_service 的逐字双份已删，防再内联。"""

    def test_plugin_service无本地指纹实现(self) -> None:
        import core.analysis.plugin_service as ps
        tree = ast.parse(Path(ps.__file__).read_text(encoding="utf-8"))
        local_defs = [
            n.name for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef)
            and n.name in ("_hash_json", "hash_json")]
        assert not local_defs, (
            "plugin_service 出现本地指纹实现——内联回潮，应 import "
            "core.analysis.hypothesis_service.hash_json")
        has_import = any(
            isinstance(n, ast.ImportFrom)
            and n.module == "core.analysis.hypothesis_service"
            and any(a.name == "hash_json" for a in n.names)
            for n in ast.walk(tree))
        assert has_import, "plugin_service 未自 hypothesis_service 导入 hash_json"

    def test_正牌行为_确定性与键序归一(self) -> None:
        from core.analysis.hypothesis_service import hash_json
        # 键序无关（sort_keys=True）
        assert hash_json({"a": 1, "b": 2}) == hash_json({"b": 2, "a": 1})
        # 同对象恒同指纹（确定性）
        assert hash_json([1, "x", None]) == hash_json([1, "x", None])
        # 非 ASCII 保真（ensure_ascii=False——逐字节不同于 ascii 转义形）
        assert hash_json({"k": "中"}) != hash_json({"k": "中".encode(
            "unicode_escape").decode("ascii")})
        # 与手写实现逐字节一致（收敛前双份的等价锚）
        import hashlib, json
        manual = hashlib.sha256(json.dumps(
            {"a": [1, 2]}, sort_keys=True, ensure_ascii=False
        ).encode("utf-8")).hexdigest()
        assert hash_json({"a": [1, 2]}) == manual

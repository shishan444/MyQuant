"""safe_symbol 单一实现防再内联锚（CARD-05 收敛守护）。

case_inspection / market_snapshot / indicator_query / validation_service
四模块的内联手写正则已收敛为 import mtf_loader.safe_symbol（正牌：
find_parquet 契约的单一实现）。行为面由既有用例覆盖（正则逐字等价、
零行为变化）；本锚守护收敛本身——任何模块再出现 safe_symbol 本地
赋值（内联回潮）即红。

AST 形制说明：四模块的 import 是函数内局部 import（延迟导入形制），
模块级无 safe_symbol 属性，身份断言（is）不可行——用语法树检查：
① 禁止 safe_symbol 的本地赋值；② 要求自 mtf_loader 导入。
"""

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

import core.analysis.case_inspection as ci
import core.analysis.indicator_query as iq
import core.analysis.market_snapshot as ms
import core.analysis.validation_service as vs

_CONSUMERS = [ci, iq, ms, vs]


class TestSafeSymbolSingleSource:
    def test_四模块无本地赋值_且经mtf_loader导入(self) -> None:
        for mod in _CONSUMERS:
            tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
            assigns = [
                n for n in ast.walk(tree)
                if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "safe_symbol"
                        for t in n.targets)]
            assert not assigns, (
                f"{mod.__name__} 出现 safe_symbol 本地赋值——内联回潮，"
                "应 import core.data.mtf_loader.safe_symbol（单一实现）")
            imports = [
                n for n in ast.walk(tree)
                if isinstance(n, ast.ImportFrom)
                and n.module == "core.data.mtf_loader"
                and any(a.name == "safe_symbol" for a in n.names)]
            assert imports, (
                f"{mod.__name__} 未自 mtf_loader 导入 safe_symbol")

    def test_正牌实现_剥离非字母数字(self) -> None:
        from core.data.mtf_loader import safe_symbol
        assert safe_symbol("BTC-USDT") == "BTCUSDT"
        assert safe_symbol("btc/usdt@spot") == "btcusdtspot"
        assert safe_symbol("BTCUSDT") == "BTCUSDT"

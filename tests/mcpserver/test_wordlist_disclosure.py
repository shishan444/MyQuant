"""U1-3 披露一致性（add-math-formula-vocabulary；CARD-08 计数披露退役）。

读 mcpserver/tools.py 与 mcpserver/capability.py 源文本。CARD-08 起
capability 面词表披露纪律翻转：静态文本引运行时计数会随扩容腐化
（枚举扩容计数残留实录），故计数披露消亡、改权威源指向（FORMULA_
FUNCTIONS 现算 + /api/research/vocabulary 端点）；归族披露保留
（杀变口径价值——词挪出族句即红）。tools.py 侧 26 词披露属卡外
残留（待裁决），本文件照旧锚其现状。spec.md 文本断言不进 pytest
（工程外路径），转 L3 交付检查。
"""

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

_TOOLS = Path(__file__).resolve().parents[2] / "mcpserver" / "tools.py"
_CAPABILITY = Path(__file__).resolve().parents[2] / "mcpserver" / "capability.py"


def _tools_text() -> str:
    return _TOOLS.read_text(encoding="utf-8")


def _capability_text() -> str:
    return _CAPABILITY.read_text(encoding="utf-8")


class TestWordlistDisclosure:
    """26 词披露三面：词数一致+归族出现+零残留。"""

    #: 新五词归族口径（design §4）：词名 → 应归入的族句
    _FAMILY_WORDS = {
        "时序变换": ("rolling_quantile",),
        "逐元素": ("sqrt", "log"),
        "卡尔曼封装": ("kalman", "kalman_slope"),
    }

    #: capability 族句锚 per-family（形制不统一——「时序变换族」
    #: 「逐元素族」带族字后缀防前文非族句误锚（如「时序结构与
    #: 逐元素逻辑」），「卡尔曼封装」全文唯一裸用）
    _CAPABILITY_ANCHOR = {
        "时序变换": "时序变换族",
        "逐元素": "逐元素族",
        "卡尔曼封装": "卡尔曼封装",
    }

    @staticmethod
    def _family_clause(text: str, start_pattern: str, seps: str) -> str:
        """提取族句段：start_pattern 起至 seps 中任一分隔符止
        （跨行文本，[^sep] 含换行）。capability 侧族句前另有非族句
        出现（如「时序结构与逐元素逻辑」），start_pattern 须带族句
        特征（tools 侧族名直起、capability 侧族名带「族」后缀）防
        误锚。"""
        m = re.search(start_pattern, text)
        assert m, f"族句段锚未命中: {start_pattern}"
        tail = text[m.start():]
        end = re.search(f"[{re.escape(seps)}]", tail)
        return tail[:end.start()] if end else tail

    def test_tools_docstring_authority_pointer(self):
        """tools 面权威源指向（卡外三处裁决落地——同 CARD-08 纪律）：
        计数披露消亡、注册表现算指向 + 计数形制防漂（回潮即红）。"""
        text = _tools_text()
        assert "白名单函数四族（注册表现算）" in text, \
            "tools 词表段缺注册表现算指向"
        assert not re.search(r"白名单函数? \d+ 词", text), \
            "tools 面计数披露形式残留（同 CARD-08 退役纪律）"

    def test_capability_wordlist_authority_pointer(self):
        """CARD-08 计数披露退役后的权威源指向（双要素）：现算权威源
        （FORMULA_FUNCTIONS）+查询端点（/api/research/vocabulary）。
        静态计数会随词表扩容腐化，指向单源即永不滞后。"""
        text = _capability_text()
        assert "权威源 FORMULA_FUNCTIONS 现算" in text, \
            "capability 词表段缺现算权威源指向"
        assert "/api/research/vocabulary" in text, \
            "capability 词表段缺词表端点指向"

    def test_capability_four_family_claim_and_no_count_residue(self):
        """「四族分组」声称与四族名齐备一致 + 计数披露形制消亡
        （「白名单 N 词」形制回归即红——CARD-08 退役纪律的防漂面：
        静态计数一旦回潮，扩容腐化问题随之复活）。"""
        text = _capability_text()
        assert "四族分组" in text, "四族分组声称缺席"
        for family in ("时序变换", "时序结构", "逐元素", "卡尔曼封装"):
            assert family in text, f"族名 {family} 缺席"
        assert not re.search(r"白名单 \d+ 词", text), \
            "capability 面计数披露形式残留（CARD-08 已退役）"

    def test_new_five_words_classified_in_disclosure(self):
        """新五词按归族口径在两披露面正确归类出现：词名与族名同句段
        共现（句段=族句起至下一分隔符——tools 以「；」切、capability
        以「+」切；CARD-08 后计数删，capability 族句锚 per-family
        见 _CAPABILITY_ANCHOR）；词边界匹配防 logical_* 对 log 的
        子串误命中与 kalman_slope 对 kalman 的前缀误命中。
        杀变口径：单删任一族的任一新词披露（或挪出族句）本用例必红。"""
        for text, anchor_of, seps in (
                (_tools_text(), lambda f: f, "；"),
                (_capability_text(), self._CAPABILITY_ANCHOR.__getitem__, "+")):
            for family, words in self._FAMILY_WORDS.items():
                clause = self._family_clause(text, anchor_of(family), seps)
                for w in words:
                    assert re.search(rf"\b{w}\b", clause), \
                        f"{w} 未归入 {family} 族句段"

    def test_no_old_word_count_residue(self):
        """扩容后披露面零旧词数披露字样残留（design §4 同步纪律；
        断言串拼接书写防本文件自身成为 grep 命中源）。"""
        old_phrase = "21" + " 词"
        for text in (_tools_text(), _capability_text()):
            assert old_phrase not in text

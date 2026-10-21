"""披露面锚清单全检断言引擎（锚数据见 _disclosure_anchors.py）。

按锚清单遍历断言：token_set（要素在场）/list_members（清单成员集）/
negative_clear（过期语义清零）+ timeframe 能力域互拍（描述面×事实面
动态提取）+ 完备性元断言双向相等（模板 12 /能力自述条目 28——新
披露未登记即红，删登记行即红）。

取材纪律（两断言源）：tools.py 走 Path 源码文本（@mcp.tool() 闭包
无模块属性）；CAPABILITY_DOC 走模块属性直读（同 test_contract_parity
取材纪律）。事实面（代码行为）从 core/analysis/templates 模板源码
动态提取——提取器内置真值样例集自校验（事实面读 core 不可变异动
core 求红，自校验为其「红态先行」等价物——防提取器写错恒绿）。
"""
from __future__ import annotations

import re
from itertools import chain
from pathlib import Path

import pytest

from mcpserver import tools as tools_mod
from mcpserver.capability import CAPABILITY_DOC

from _disclosure_anchors import (
    ALL_ANCHORS, CAPABILITY_ENTRY_ANCHORED, CAPABILITY_ENTRY_WHITELIST,
    NEGATIVE_CLEAR_TOKENS, REGISTERED_TEMPLATES, TIMEFRAME_FACES,
    DisclosureAnchor, _SEG_COUNTER, _SEG_EXIT, _SEG_REQUIRED, _SEG_SEGMENT,
)

pytestmark = [pytest.mark.integration]


# ---------------------------------------------------------------------------
# 取材与引擎
# ---------------------------------------------------------------------------

_TOOLS_SQUASH = "".join(
    Path(tools_mod.__file__).read_text(encoding="utf-8").split())
_CAP_SQUASH = "".join(CAPABILITY_DOC.split())


def _source_squash(source: str) -> str:
    """两断言源取材：tools=源码文本 / capability=模块属性直读。"""
    return _TOOLS_SQUASH if source == "tools" else _CAP_SQUASH


def _segment(squashed: str, start: str, end: str) -> str:
    """squash 域内截段：[start 锚起点, end 锚起点)，两锚均须在场。"""
    i = squashed.find(start)
    assert i != -1, f"截段起点锚不在场: {start}"
    j = squashed.find(end, i)
    assert j != -1, f"截段终点锚不在场: {end}"
    return squashed[i:j]


def _anchor_domain(anchor: DisclosureAnchor) -> str:
    """锚段域：空起止锚=全文件 squash 域（仅清零锚语义）。

    F1 guard（reviewer 五维检测）：非 negative_clear 锚段域必填——
    「往清单加行」漏写段域时静默退化全文件扫描=守护变松不红；end
    缺失同禁（_segment 对空终点 find("")==i 恒命中产空段域，报错
    「段域缺披露要素」误导归因为披露缺失）。
    """
    sq = _source_squash(anchor.source)
    if anchor.kind != "negative_clear":
        assert anchor.seg_start and anchor.seg_end, (
            f"[{anchor.anchor_id}] {anchor.kind} 锚数据缺段域锚"
            "（seg_start/seg_end 必填——空段域=全文件域仅清零锚语义）")
    else:
        # 对称 guard（reviewer 随记）：清零锚=逐文件断言，误填段域会
        # 使清零扫描域收窄（段外旧语义残留漏检不红）
        assert not anchor.seg_start and not anchor.seg_end, (
            f"[{anchor.anchor_id}] negative_clear 锚须空段域"
            "（清零=逐文件断言——填段域致扫描域收窄漏检）")
        return sq
    return _segment(sq, anchor.seg_start, anchor.seg_end)


# ---------------------------------------------------------------------------
# 通用锚断言（组 1/组 3/清零——parametrize 遍历锚清单）
# ---------------------------------------------------------------------------

def _assert_anchor(anchor: DisclosureAnchor) -> None:
    if anchor.kind == "token_set":
        seg = _anchor_domain(anchor)
        for token in anchor.tokens:
            assert token in seg, \
                f"[{anchor.anchor_id}] 段域缺披露要素: {token}"
    elif anchor.kind == "list_members":
        seg = _anchor_domain(anchor)
        mo = re.search(anchor.list_pattern, seg)
        assert mo is not None, \
            f"[{anchor.anchor_id}] 清单串锚不在场（披露形制漂移）"
        members = set(mo.group(0).removesuffix("支持conditions").split("/")) \
            if "支持conditions" in anchor.list_pattern \
            else set(mo.group(1).split("/"))
        missing = set(anchor.members) - members
        extra = members - set(anchor.members)
        assert not missing, f"[{anchor.anchor_id}] 清单缺成员: {sorted(missing)}"
        assert not extra, f"[{anchor.anchor_id}] 清单多出成员: {sorted(extra)}"
    elif anchor.kind == "negative_clear":
        sq = _source_squash(anchor.source)
        for token in anchor.tokens:
            assert token not in sq, \
                f"[{anchor.anchor_id}] 过期语义残留（清零判据命中）: {token}"
    else:  # pragma: no cover — 数据模块 kind 封闭枚举
        raise AssertionError(f"未知锚类型: {anchor.kind}")


@pytest.mark.parametrize("anchor", ALL_ANCHORS, ids=lambda a: a.anchor_id)
def test_disclosure_anchor(anchor: DisclosureAnchor) -> None:
    """锚清单全检：每条锚按 kind 断言（披露面文本漂移即红）。"""
    _assert_anchor(anchor)


# ---------------------------------------------------------------------------
# 组 2：事实面提取器（三类提取形制+排除规则）+ 自校验 + 互拍
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(tools_mod.__file__).resolve().parents[1] \
    / "core" / "analysis" / "templates"
_MASK_RE = re.compile(r"mask_for(?:_all)?\([^)]*mtf\s*=", re.S)
_TRANSLATE_RE = re.compile(r"build_signal_genes\([^)]*strategy_timeframe\s*=",
                           re.S)


def fact_supports_timeframe(template: str) -> bool:
    """事实面提取：该模板的用户条件是否支持 timeframe 键。

    三类提取形制（与 design/任务书口径一致）：
    ①掩码路径型——模板源码 mask_for/mask_for_all 调用带 mtf= 形参；
    ②翻译链型——exit_sweep 走 build_signal_genes(strategy_timeframe=)
      传参（不走模板掩码消费，掩码型对其恒空，在场=支持）；
    ③precheck 型——precheck_hypothesis 源码 MtfEvalContext 符号在场。
    排除规则：screen_patterns 的 mask_for 消费是内部 probes（候选
    恒基准周期形制）——不算用户条件支持（A5「互拍代理不精确产假
    漂移」教训防御）。
    """
    if template == "screen_patterns":
        return False
    src = (_TEMPLATES_DIR / f"{template}.py").read_text(encoding="utf-8")
    if template == "exit_sweep":
        return bool(_TRANSLATE_RE.search(src))
    if template == "precheck_hypothesis":
        return "MtfEvalContext(" in src
    return bool(_MASK_RE.search(src))


# 真值样例集（自校验基准——锁定提取器三形制+排除规则实现正确）
_FACT_TRUTH_SAMPLE: dict = {
    "condition_stats": True, "event_study": True, "regime_split": True,
    "counter_profile": True, "param_sweep": True, "exit_sweep": True,
    "precheck_hypothesis": True, "screen_patterns": False,
}


def test_fact_extractor_selfcheck() -> None:
    """事实面提取器自校验：输出与真值样例集逐项比对。

    事实面读 core 代码不可变异动 core 求红——自校验为其「红态先行」
    等价物：提取器形制写错（如漏翻译链型致 exit_sweep 恒 False、或
    漏排除规则致 screen_patterns 误 True）即红，防提取器写错恒绿。
    """
    for template, truth in _FACT_TRUTH_SAMPLE.items():
        got = fact_supports_timeframe(template)
        assert got == truth, (
            f"事实面提取器与真值不符: {template} 提取={got} 真值={truth}"
            "（提取形制实现错误——检查三类形制与排除规则）")


@pytest.mark.parametrize("face", TIMEFRAME_FACES, ids=lambda f: f.face_id)
def test_timeframe_face_cross_check(face) -> None:
    """timeframe 能力域互拍（方案甲核心）：描述面自述 ⟺ 事实面支持。

    描述面=该落点段域内支持语义 token 集齐备（教「条件支持
    timeframe」）；事实面=fact_templates 全部经代码行为提取为支持。
    任一侧漂移即红：教了实际不支持（假披露）/支持了不教（能力
    静默——TD-3 病灶类别）。
    """
    # F1 预放（reviewer）：TimeframeFace 段域必填——落点无全文件语义
    assert face.seg_start and face.seg_end, (
        f"[{face.face_id}] face 数据缺段域锚（落点截段必填）")
    seg = _segment(_source_squash(face.source), face.seg_start, face.seg_end)
    disclosed = all(t in seg for t in face.tokens)
    fact = all(fact_supports_timeframe(t) for t in face.fact_templates)
    assert disclosed == fact, (
        f"[{face.face_id}] 描述面({disclosed})与事实面({fact})漂移——"
        f"token={face.tokens} 模板={face.fact_templates}"
        "（教了不支持/支持了不教，均属披露缺陷）")


# ---------------------------------------------------------------------------
# 完备性元断言（双向相等——「全检」名实相符的机制核心）
# ---------------------------------------------------------------------------

def _docstring_template_extraction() -> set:
    """run_analysis docstring 模板枚举提取（squash 后 template∈a|b|c）。"""
    mo = re.search(r"template∈([\w|]+)", _TOOLS_SQUASH)
    assert mo is not None, "模板枚举串锚不在场（docstring 形制漂移）"
    return set(mo.group(1).split("|"))


def _capability_entry_extraction() -> set:
    """CAPABILITY_DOC 条目标题提取（28 条，squash 后 `- **xxx**：`
    形制，首短语归一——取「（」前部分：括注版本变化不致漂移）。"""
    titles = re.findall(r"-\*\*([^*]+)\*\*：", _CAP_SQUASH)
    return {t.split("（")[0] for t in titles}


def test_meta_template_registry_equals_docstring() -> None:
    """元断言①：模板披露登记键集 == docstring 模板枚举提取集。

    新模板进 docstring 枚举而未登记锚清单即红（防逃逸口）；删
    登记行即红（变异⑫红界——登记面退化为人工记得的防线）。
    """
    assert set(REGISTERED_TEMPLATES) == _docstring_template_extraction(), (
        "模板披露登记键集与 docstring 枚举提取集不等——新模板披露"
        "未登记锚清单，或登记集漂移（维护动作=向锚清单加行）")


def test_meta_registered_templates_covered_by_anchors() -> None:
    """登记集内部一致性：每登记模板必有锚条目/互拍落点覆盖。

    防登记集与锚条目脱节（登记了模板名而无任何披露锚承载）。
    """
    covered = set(chain.from_iterable(a.covered for a in ALL_ANCHORS))
    covered |= set(chain.from_iterable(f.fact_templates
                                       for f in TIMEFRAME_FACES))
    only_registered = sorted(set(REGISTERED_TEMPLATES) - covered)
    only_covered = sorted(covered - set(REGISTERED_TEMPLATES))
    assert covered == set(REGISTERED_TEMPLATES), (
        f"登记模板与锚覆盖面脱节: 仅登记={only_registered} "
        f"仅锚覆盖={only_covered}")


def test_meta_capability_entries_bidirectional() -> None:
    """元断言②：条目登记键集 ∪ 白名单 == CAPABILITY_DOC 提取集（28）。

    双向：能力自述新增条目未登记/未加白名单即红（防逃逸口）；
    登记域或白名单删键即红；两侧重复登记即红（登记域假扩充）。
    白名单显式入数据模块——变更须显式改文件。
    """
    extracted = _capability_entry_extraction()
    anchored, whitelist = set(CAPABILITY_ENTRY_ANCHORED), \
        set(CAPABILITY_ENTRY_WHITELIST)
    both = sorted(anchored & whitelist)
    uncovered = sorted(extracted - anchored - whitelist)
    ghost = sorted((anchored | whitelist) - extracted)
    assert not both, f"同一键同时进登记域与白名单: {both}"
    assert anchored | whitelist == extracted, (
        f"条目登记∪白名单与提取集不等: 未覆盖={uncovered} "
        f"幽灵键={ghost}（新条目披露=登记锚或白名单二选一，防逃逸口）")


# ---------------------------------------------------------------------------
# 组 3 专项互拍（动态双向——模板枚举×路由分支/必填键×键组表）
# ---------------------------------------------------------------------------

def test_g3_template_enum_matches_route_branches() -> None:
    """模板枚举互拍：docstring 竖线枚举集 == 路由 template 分支集。

    路由新增分支忘披露/docstring 多写不存在的模板均红。
    """
    from api.routes.research import analysis as analysis_mod
    route_src = Path(analysis_mod.__file__).read_text(encoding="utf-8")
    route_branches = set(re.findall(r'template == "(\w+)"', route_src))
    drift = sorted(_docstring_template_extraction() ^ route_branches)
    assert _docstring_template_extraction() == route_branches, (
        f"模板枚举与路由分支集漂移: {drift}")


def test_g3_required_keys_match_route_table() -> None:
    """必填键互拍：键组表每模板键组的键在披露段在场；无必填键模板
    声明在场；键组表模板集 ⊆ 模板枚举集。

    键名在场为弱形制——形制级二选一披露由 G1 段落锚（condition|
    conditions token）承载，本断言补「键组表键↔披露段」成员级互拍。
    """
    from api.routes.research import analysis as analysis_mod
    table = analysis_mod._TEMPLATE_REQUIRED_KEYS
    enum = _docstring_template_extraction()
    assert set(table) <= enum, \
        f"键组表含未披露模板: {sorted(set(table) - enum)}"

    # F2 收敛（reviewer 五维检测）：段域定义单真相源——复用锚数据
    # _SEG_* 常量，消互拍内联字面串双源（内联版 exit_sweep 终点
    # 「走202+轮询」还把该被守护要素右开排除在互拍段域外——
    # _SEG_EXIT=body={ 的注释依据见 _disclosure_anchors.py:55-57）
    seg_required = _segment(_TOOLS_SQUASH, *_SEG_REQUIRED)
    seg_map = {
        "counter_profile": _segment(_TOOLS_SQUASH, *_SEG_COUNTER),
        "exit_sweep": _segment(_TOOLS_SQUASH, *_SEG_EXIT),
        "segment_narrative": _segment(_TOOLS_SQUASH, *_SEG_SEGMENT),
    }
    for tpl, groups in table.items():
        seg = seg_map.get(tpl, seg_required)
        for group in groups:
            for key in group:
                assert key in seg, \
                    f"{tpl} 键组表键 {key} 未在其披露段在场（互拍断裂）"
    # 无必填键三模板的声明在场
    assert "screen_patterns/regime_split无必填键" in seg_required
    assert "无必填键" in seg_map["segment_narrative"]


# ---------------------------------------------------------------------------
# 清零锚直测（与 parametrize 并存——独立测试便于变异复跑定位）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source", ["tools", "capability"])
def test_negative_clear_by_source(source: str) -> None:
    """过期语义清零直测：两声明文件各自独立实证（变异⑦a/⑦b 红界）。

    四判据封闭枚举见 _disclosure_anchors.NEGATIVE_CLEAR_TOKENS；
    排除「掩码路径支持」假红词（新能力域句「经掩码投影」不含该
    四字连写）。
    """
    sq = _source_squash(source)
    for token in NEGATIVE_CLEAR_TOKENS:
        assert token not in sq, \
            f"[{source}] 过期语义残留（清零判据命中）: {token}"

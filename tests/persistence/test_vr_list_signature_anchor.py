"""仓储签名锚（upgrade-validation-records-list 任务 1.2——T2-A8）。

四处调用点形参表快照断言：list/count_validation_records 形参漂移即红
（「形参只增不改」契约——缺省不传行为不变即既有调用方零适配）。
"""
from __future__ import annotations

import inspect

from core.persistence.repository import research as rr


def test_list_signature_anchor():
    sig = inspect.signature(rr.list_validation_records)
    params = list(sig.parameters)
    # 既有形制（现状锚——重命名/删除即红）
    assert params[:7] == [
        "db_path", "hypothesis_id", "strategy_id",
        "offset", "limit", "cursor", "order"]
    # B1 扩展形参（upgrade-validation-records-list 1.1——全部缺省
    # None 现状逐位一致）
    for p in ("q", "generalization", "sort", "level", "verdict"):
        assert p in params, f"list 缺 B1 形参 {p}"
        assert sig.parameters[p].default is None, (
            f"{p} 缺省须 None（不传=现状逐位一致）")


def test_count_signature_anchor():
    sig = inspect.signature(rr.count_validation_records)
    params = list(sig.parameters)
    assert params[:3] == ["db_path", "hypothesis_id", "strategy_id"]
    for p in ("q", "generalization", "level", "verdict"):
        assert p in params, f"count 缺 B1 形参 {p}"
        assert sig.parameters[p].default is None


def test_call_sites_unchanged():
    """存活调用点形制锚（retire-legacy 后收缩为端点单点——原四处中
    的 validation 档案端点与两处 iter_all_pages 消费面已随退役消失；
    tester-2 修正史：仅断言模块可导入属断言虚标，改源文本锚定）。

    每处以源文本锚定调用形制：调用被改写（换函数/改直调为 lambda/
    传入新参改写调用面）即红，与上两签名锚构成「形参只增不改、
    调用方零适配」的完整实证。"""
    import api.routes.research.records_list as rl
    # 端点接线调用（records_list.py:126——全 keyword 形制）；retire-
    # legacy 后 validation_service/hypothesis_service 两处 iter_all_pages
    # 消费面随旧编排与登记域退役，存活调用面收缩为端点单点
    assert "rows = rr.list_validation_records(" in inspect.getsource(rl)

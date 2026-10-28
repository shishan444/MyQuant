"""TaskController pause/stop 信号面 unit 直构（阶段四闸门第 5 步——
层级补全：CARD-01 新增 pause 面此前仅经 integration 链覆盖，纯组件
行为补 unit 直构层）。

被测：core/concurrency/task_control.py（evolution+paper 双域共享的
协作取消组件——stop/pause 两信号面正交性是核心契约）。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

from core.concurrency.task_control import (
    TaskController, TaskPauseRequested, TaskStopRequested,
)


def test_stop_signal_raises_check_stop():
    c = TaskController()
    c.check_stop()  # 未置位不抛
    assert not c.stop_requested
    c.request_stop()
    assert c.stop_requested
    with pytest.raises(TaskStopRequested):
        c.check_stop()


def test_pause_signal_raises_check_pause():
    c = TaskController()
    c.check_pause()  # 未置位不抛
    assert not c.pause_requested
    c.request_pause()
    assert c.pause_requested
    with pytest.raises(TaskPauseRequested):
        c.check_pause()


def test_pause_does_not_trigger_stop_and_vice_versa():
    """两信号面正交：pause 置位不影响 check_stop，stop 置位不影响
    check_pause（消费方按语义分点检查——CARD-01 修复设计 6 的
    时序自洽基础）。"""
    c = TaskController()
    c.request_pause()
    c.check_stop()  # 不抛
    with pytest.raises(TaskPauseRequested):
        c.check_pause()
    c2 = TaskController()
    c2.request_stop()
    c2.check_pause()  # 不抛
    with pytest.raises(TaskStopRequested):
        c2.check_stop()


def test_both_signals_set_stop_wins_at_stop_checkpoint():
    """双信号并置：各自检查点独立触发（调用方序决定谁先抛——
    runner 链 stop 检查点在代内多处、pause 在代末，先遇 stop 即
    TaskStopRequested——stop 优先语义的组件层基础）。"""
    c = TaskController()
    c.request_pause()
    c.request_stop()
    with pytest.raises(TaskStopRequested):
        c.check_stop()
    with pytest.raises(TaskPauseRequested):
        c.check_pause()

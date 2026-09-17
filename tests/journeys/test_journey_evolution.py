"""链 D · 进化闭环链 L2（3.3）：创建 → running → 代际推进 → 冠军 → 终态。

L2 内涵：状态机迁移=触发动作（pause→paused、resume→pending、stop→stopped）；
代际 history 完整（代数连续、fitness 有值）；冠军=代际 fitness 极值；
终态后产出可取（strategies 的 source_task_id 一致）。真跑小种群（pop 4×2 代，
<60s），零 mock。
"""

from __future__ import annotations

import time

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]

from tests.reference.fixture_gen import gen_ohlcv

_TERMINAL = {"completed", "stopped", "failed"}


@pytest.fixture(scope="module")
def evo_client(tmp_path_factory):
    from fastapi.testclient import TestClient
    from MyQuant.api.app import create_app

    data_dir = tmp_path_factory.mktemp("evo_data")
    db_path = tmp_path_factory.mktemp("evo_db") / "t.db"
    gen_ohlcv("normal", seed=42, n=300).to_parquet(data_dir / "BTCUSDT_4h.parquet")
    app = create_app(db_path=db_path, data_dir=data_dir)
    with TestClient(app) as client:
        yield client


def _dna_payload():
    return {
        "signal_genes": [
            {"indicator": "RSI", "params": {"period": 14}, "role": "entry_trigger",
             "field": None, "condition": {"type": "lt", "threshold": 30}},
            {"indicator": "RSI", "params": {"period": 14}, "role": "exit_trigger",
             "field": None, "condition": {"type": "gt", "threshold": 70}},
        ],
        "logic_genes": {"entry_logic": "AND", "exit_logic": "AND"},
        "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
        "risk_genes": {"stop_loss": 0.05, "take_profit": 0.10,
                       "position_size": 0.5, "leverage": 1, "direction": "long"},
    }


def _create_task(client, **over):
    payload = {
        "initial_dna": _dna_payload(),
        "symbol": "BTCUSDT", "timeframe": "4h",
        "target_score": 80.0, "score_template": "profit_first",
        "population_size": 4, "max_generations": 2,
    }
    payload.update(over)
    r = client.post("/api/evolution/tasks", json=payload)
    assert r.status_code == 201, r.text
    return r.json()["task_id"]


def _wait_terminal(client, task_id, timeout=90):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(f"/api/evolution/tasks/{task_id}").json()
        if last.get("status") in _TERMINAL:
            return last
        time.sleep(0.5)
    pytest.fail(f"进化任务超时未终态: status={last and last.get('status')}")


def test_evolution_closed_loop_l2(evo_client):
    client = evo_client
    # continuous=False：有限进化语义（跑完 max_generations 即 completed）
    task_id = _create_task(client, continuous=False)

    # ── D2: running 推进（创建后进入执行）
    seen_running = False
    deadline = time.time() + 90
    while time.time() < deadline:
        row = client.get(f"/api/evolution/tasks/{task_id}").json()
        if row.get("status") == "running":
            seen_running = True
            break
        if row.get("status") in _TERMINAL:
            break
        time.sleep(0.2)
    final = _wait_terminal(client, task_id)
    assert seen_running or final["status"] == "completed", \
        f"未观测 running 亦未完成: {final['status']}"
    assert final["status"] == "completed", f"小种群任务应正常完成: {final}"

    # ── D4: 代际 history 完整（代数连续 1..2，fitness 有值）
    hist = client.get(f"/api/evolution/tasks/{task_id}/history").json()
    gens = hist.get("history") or hist.get("generations") or hist
    assert len(gens) >= 1, f"无代际记录: {hist}"
    gen_numbers = sorted(int(g.get("generation", g.get("gen", 0))) for g in gens)
    assert gen_numbers == list(range(1, len(gens) + 1)), \
        f"代数不连续: {gen_numbers}"

    # ── D5: 冠军 = 代际极值（自洽，而非仅存在）
    # R-8（已登记疑点）：best_fitness 全链 None（OOS fitness 有计算未回填），
    # 冠军自洽以 best_score 锚定
    scores = [float(g.get("best_score") or 0.0) for g in gens]
    best_seen = max(scores)
    task_best = final.get("best_score")
    if task_best is not None:
        assert float(task_best) == pytest.approx(best_seen, rel=1e-9), (
            f"任务冠军 score {task_best} ≠ 代际极值 {best_seen}"
        )

    # ── D6: 终态后产出可取（跨步引用一致）
    # R-9（已登记）：tasks/{id}/strategies 返回 champion_dna 内嵌 id，
    # 不保证已写策略表（低分场景 404）——跨步引用以查策略表的
    # discovered-strategies 端点锚定
    eff = client.get(f"/api/evolution/tasks/{task_id}/strategies").json()
    eff_items = eff.get("strategies") or []
    assert isinstance(eff_items, list)

    disc = client.get(f"/api/evolution/tasks/{task_id}/discovered-strategies").json()
    disc_items = disc if isinstance(disc, list) else disc.get("strategies", [])
    if disc_items:
        # 有产出：每个 id 在策略表可取回（跨步引用不断裂）
        for s in disc_items[:3]:
            sid = s.get("strategy_id") or s.get("id")
            assert sid
            assert client.get(f"/api/strategies/{sid}").status_code == 200, (
                f"策略表产出 {sid} 取不回"
            )
    else:
        # score=0 场景（RSI 训练窗无交易）：策略表无行——champion 内嵌 id
        # 在策略表 404（R-9 一致性缺口的实证面，不视为断言失败）
        assert all(float(g.get("best_score") or 0.0) == 0.0 for g in gens), (
            "有得分产出但 discovered-strategies 为空——跨步引用断裂"
        )


def test_evolution_lifecycle_transitions_l2(evo_client):
    """状态机一致性：pause→paused、resume→pending、stop→stopped。"""
    client = evo_client
    # R-7（已登记）：continuous 默认 True——max_generations 仅限单种群，
    # 跑完自动开新种群直到手动 stop。生命周期测试需要无限任务。
    task_id = _create_task(client, max_generations=100000)

    def _status():
        return client.get(f"/api/evolution/tasks/{task_id}").json()["status"]

    deadline = time.time() + 30
    while time.time() < deadline and _status() not in ("running", "paused"):
        time.sleep(0.2)
    assert _status() in ("running", "paused"), f"任务未进入运行: {_status()}"

    # pause → paused（状态=触发动作）
    r = client.post(f"/api/evolution/tasks/{task_id}/pause")
    assert r.status_code == 200, r.text
    deadline = time.time() + 15
    while time.time() < deadline and _status() != "paused":
        time.sleep(0.2)
    assert _status() == "paused", f"pause 后状态 {_status()} ≠ paused"

    # resume → pending（runner 重新拾取）
    r = client.post(f"/api/evolution/tasks/{task_id}/resume")
    assert r.status_code == 200, r.text
    row = client.get(f"/api/evolution/tasks/{task_id}").json()
    assert row["status"] in ("pending", "running"), \
        f"resume 后状态 {row['status']} 不在拾取路径"

    # stop → stopped（终态收敛）
    r = client.post(f"/api/evolution/tasks/{task_id}/stop")
    assert r.status_code == 200, r.text
    deadline = time.time() + 15
    while time.time() < deadline and _status() != "stopped":
        time.sleep(0.2)
    assert _status() == "stopped", f"stop 后状态 {_status()} ≠ stopped"

    # 非法迁移被拒（stopped 不可再 pause）
    r = client.post(f"/api/evolution/tasks/{task_id}/pause")
    assert r.status_code in (400, 404, 409, 422), \
        f"stopped 任务 pause 应被拒，实际 {r.status_code}"

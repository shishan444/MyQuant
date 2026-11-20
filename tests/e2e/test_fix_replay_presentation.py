"""回放详情呈现链路 e2e（fix-replay-presentation-pipeline 任务 4.3）。

种子=确定性 sqlite 直插临时库，镜像生产 3 条真实 replay 记录形制
（BTCUSDT 1h 2025-04-01→2025-10-01、544 笔——超单页上限 500 的原始
案例级别；时间形制=带偏移 ISO，逐库实锚对齐）。链路全真：/verify
列表→点击 replay 卡片→ReplayDetailPanel 经真实 REST 分页拉取
fills/signals（544 笔=两页 500+44 聚合）→四件套渲染。

断言面（锚④后半自动证据）：544 行全量无尾部截断、「无成交」伪空态
不出现、「明细源不可达」降级不出现、词表全覆盖（八值 kind 种子零
「未识别动作」）、行点击互达零 console error。
"""
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from playwright.sync_api import Page

BASE = os.environ.get("E2E_WEB_URL", "http://localhost:8080")

RECORD_ID = "vr_e2e_replay_544"
TID = "e2ereplay544tid"
N_FILLS = 544
# 八值 kind 全覆盖（含 exit_max_hold——词表值域完备验收面）
KINDS = ["open", "add", "reduce", "close",
         "exit_sl", "exit_tp", "exit_max_hold", "exit_time"]


def _iso(i: int) -> str:
    """带偏移 ISO（与生产 pt_fill.bar_time 实锚同构）"""
    return (datetime(2025, 4, 1, tzinfo=timezone.utc)
            + timedelta(hours=i)).isoformat()


def _seed(db_path) -> None:
    now = "2026-11-20T00:00:00+00:00"
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    # 策略闸行：/verify 页在策略库为空时早退（RecordsBrowser 不渲染）
    # ——补一条 active 策略通过空态闸（dna_json 坏值端点优雅降级 dna=None）
    cur.execute(
        "INSERT INTO strategy (strategy_id, name, dna_json, status,"
        " source, symbol, timeframe, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        ("st_e2eseed0001", "e2e 种子策略", "{}", "active", "manual",
         "BTCUSDT", "1h", now, now))
    cur.execute(
        "INSERT INTO research_hypotheses (hypothesis_id, conditions,"
        " conditions_hash, statement, data_ranges, ranges_hash, status,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("hyp_e2e_replay", "[]", "e2e-cond-hash",
         "e2e 回放呈现链路种子假设（确定性）", "[]", "e2e-ranges-hash",
         "active", now, now))
    params = {"symbol": "BTCUSDT", "timeframe": "1h",
              "start": "2025-04-01", "end": "2025-10-01",
              "fee": 0.001, "slippage": 0.0005,
              "init_cash": 100000.0, "leverage": 1}
    result = {"replay": {"task_id": TID, "status": "completed",
                          "total_trades": N_FILLS, "total_pnl": -1.2,
                          "final_balance": 99880.0,
                          "final_equity": 99880.0}}
    cur.execute(
        "INSERT INTO validation_records (record_id, hypothesis_id,"
        " strategy_id, level, segmentation, params, result, verdict,"
        " range_hash, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (RECORD_ID, "hyp_e2e_replay", None, "replay", "",
         json.dumps(params), json.dumps(result), "", "e2e-vr-hash", now))
    cur.execute(
        "INSERT INTO pt_task (task_id, status, strategy_id,"
        " dna_snapshot_json, symbol, timeframe, initial_cash, fee,"
        " slippage, leverage, bars_processed, total_trades, win_count,"
        " loss_count, total_pnl, research_status_snapshot, created_at,"
        " updated_at, stop_reason) VALUES"
        " (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (TID, "completed", "st_e2eseed", "{}", "BTCUSDT", "1h",
         100000.0, 0.001, 0.0005, 1, N_FILLS, N_FILLS, 1, 1, -1.2,
         "draft", now, now, "window_complete"))
    cur.executemany(
        "INSERT INTO pt_fill (task_id, bar_time, seq, kind, side, price,"
        " quantity, fee, realized_pnl, reason) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(TID, _iso(i), i + 1, KINDS[i % len(KINDS)],
          "long" if i % 2 == 0 else "short",
          100.0 + (i % 10), 0.5, 0.01,
          -0.5 if i % 3 == 0 else None, None)
         for i in range(N_FILLS)])
    gene = json.dumps([{"gene_index": 0, "indicator": "RSI",
                        "field": "rsi", "condition": "lt", "target": 30,
                        "role": "open", "timeframe": "1h"}])
    cur.executemany(
        "INSERT INTO pt_signal (task_id, strategy_id, bar_time,"
        " exec_bar_time, seq, signal_kind, direction, size_fraction,"
        " gene_hits, indicator_snapshot, outcome, reject_reason, fill_seq,"
        " capped, equity, balance, unrealized_pnl, position_side,"
        " position_quantity, position_margin, created_at) VALUES"
        " (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(TID, "st_e2eseed", _iso(max(i - 1, 0)), _iso(i), 1,
          "open" if i == 0 else "add", "long", 0.1, gene, "{}",
          "filled", None, i + 1, None, 100000.0, 100000.0, 0.0,
          None, None, None, now) for i in range(3)])
    con.commit()
    con.close()


def _wait_react(page: Page, timeout: int = 8000):
    """轮询 #root 内容量判定 React 水合完成（v014 同款）。"""
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        if len(page.locator("#root").inner_html()) > 50:
            return
        page.wait_for_timeout(500)
    raise TimeoutError("React did not hydrate in time")


class TestReplayDetailPipeline:
    """回放四件套全链路（列表→卡片→分页聚合→呈现→互达）。"""

    def test_replay_record_full_chain(self, page: Page, e2e_db):
        _seed(e2e_db)
        errors: list[str] = []
        page.on(
            "console",
            lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(BASE + "/verify", timeout=15000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        _wait_react(page)

        card = page.locator(f'[data-testid="vr-card-{RECORD_ID}"]')
        card.wait_for(state="visible", timeout=15000)
        card.click()

        panel_rows = page.locator('[data-testid^="replay-flow-row-"]')
        # 两页聚合（500+44）完成后一次性渲染——等满 544 行
        panel_rows.first.wait_for(state="visible", timeout=20000)
        page.wait_for_function(
            f'document.querySelectorAll(\'[data-testid^="replay-flow-row-"]\')'
            f'.length === {N_FILLS}', timeout=20000)
        assert panel_rows.count() == N_FILLS

        body = page.locator(
            '[data-testid="replay-detail-panel"]').inner_text()
        assert "无成交" not in body, "伪空态出现（FE-1 病灶回归）"
        assert "明细源不可达" not in body, "失败降级误触发"
        assert "未识别动作" not in body, "词表值域缺口（八值种子全覆盖）"

        # 互达：点击首行（选中高亮+基因描述展开面）
        page.locator('[data-testid="replay-flow-row-0"]').click()
        page.wait_for_timeout(500)

        filtered = [
            e for e in errors
            if "favicon" not in e.lower()
            and "devtools" not in e.lower()
            and "net::" not in e.lower()
            and "failed to load resource" not in e.lower()
        ]
        assert filtered == [], f"console errors: {filtered}"

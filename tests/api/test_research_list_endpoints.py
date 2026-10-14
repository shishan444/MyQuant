"""研究域列表端点特征网（fix/mcp-research-upstream-review）。

修复期临时安全网：钉住 GET /hypotheses、/log、/feature-requests
三端点的分页契约（CARD-05 统一 clamp；CARD-07 起 desc 最新在前 +
cursor keyset 续页 + total=过滤口径真总数 + next_cursor）、过滤
组合、404 错误通道与 translate 无 body 分支。
"""

import pytest

pytestmark = [pytest.mark.integration]

from core.persistence.repository import research as rr

CONDITIONS = {
    "when_conditions": [{"subject": "close", "action": "gt", "target": 0}],
    "then_conditions": [{"subject": "close", "action": "gt", "target": -1}],
}


def _register(client, start: str, statement: str = "特征网假设"):
    return client.post("/api/research/hypotheses", json={
        "conditions": CONDITIONS, "statement": statement,
        "data_ranges": [{"symbol": "BTCUSDT", "timeframe": "4h",
                         "start": start, "end": "2024-03-01T00:00:00Z"}]})


class TestHypothesesPagination:
    """钳制语义：page<1→1；page_size 越界（<1 或 >200）→50；total=当前页长度。"""

    def test_page_zero_clamped_to_one(self, api_client):
        _register(api_client, "2024-01-01T00:00:00Z")
        body = api_client.get("/api/research/hypotheses",
                              params={"page": 0}).json()
        assert body["page"] == 1 and body["page_size"] == 50
        assert len(body["items"]) == 1 and body["total"] == 1

    @pytest.mark.parametrize("page_size", [0, -1, 201])
    def test_pagesize_out_of_range_clamped_to_50(self, api_client, page_size):
        _register(api_client, "2024-01-01T00:00:00Z")
        body = api_client.get("/api/research/hypotheses",
                              params={"page_size": page_size}).json()
        assert body["page_size"] == 50, f"page_size={page_size} 应钳制为 50"

    def test_pagesize_cap_boundary_kept(self, api_client):
        body = api_client.get("/api/research/hypotheses",
                              params={"page_size": 200}).json()
        assert body["page_size"] == 200, "上限 200 本身合法，不应被改写"

    def test_beyond_last_page_empty_with_true_total(self, api_client):
        """CARD-07 后：超尾页 items 空，total 为过滤口径真总数（非 0），
        next_cursor 空页为 None。"""
        _register(api_client, "2024-01-01T00:00:00Z")
        body = api_client.get("/api/research/hypotheses",
                              params={"page": 99}).json()
        assert body["items"] == [] and body["total"] == 1
        assert body["next_cursor"] is None

    def test_pagination_slices_items(self, api_client):
        """CARD-07 后：total=真总数（不再随页变）；缺省 desc 最新在前。"""
        ids_in_order = []
        for i in range(3):
            resp = _register(api_client, f"2024-01-0{i + 1}T00:00:00Z")
            assert resp.status_code == 200, resp.text
            ids_in_order.append(resp.json()["hypothesis_id"])

        page1 = api_client.get("/api/research/hypotheses",
                               params={"page_size": 2}).json()
        page2 = api_client.get("/api/research/hypotheses",
                               params={"page": 2, "page_size": 2}).json()
        assert len(page1["items"]) == 2 and page1["total"] == 3
        assert len(page2["items"]) == 1 and page2["total"] == 3
        ids1 = [h["hypothesis_id"] for h in page1["items"]]
        ids2 = {h["hypothesis_id"] for h in page2["items"]}
        assert not set(ids1) & ids2, "分页窗口不应重叠"
        assert ids1[0] == ids_in_order[-1], "缺省 desc：最后登记（最新）在前"

    def test_status_filter(self, api_client):
        r = _register(api_client, "2024-01-01T00:00:00Z").json()
        active = api_client.get("/api/research/hypotheses",
                                params={"status": "active"}).json()
        assert any(h["hypothesis_id"] == r["hypothesis_id"]
                   for h in active["items"])
        refuted = api_client.get("/api/research/hypotheses",
                                 params={"status": "refuted"}).json()
        assert refuted["items"] == []

    def test_items_decode_structured_json_fields(self, api_client):
        _register(api_client, "2024-01-01T00:00:00Z")
        body = api_client.get("/api/research/hypotheses").json()
        item = body["items"][0]
        assert isinstance(item["conditions"], dict), "conditions 库存 JSON 串应在响应解码"
        assert isinstance(item["data_ranges"], list)


class TestResearchLogEndpoint:
    """entry_type / hypothesis_id 过滤组合与脏 data_ranges 容错。"""

    def test_entry_type_filter_hit_and_miss(self, api_client):
        r = _register(api_client, "2024-01-01T00:00:00Z").json()
        hit = api_client.get("/api/research/log",
                             params={"entry_type": "register"}).json()
        assert any(e["hypothesis_id"] == r["hypothesis_id"]
                   for e in hit["items"])
        miss = api_client.get("/api/research/log",
                              params={"entry_type": "nope"}).json()
        assert miss["items"] == [] and miss["total"] == 0

    def test_combined_filter(self, api_client):
        r = _register(api_client, "2024-01-01T00:00:00Z").json()
        body = api_client.get("/api/research/log", params={
            "hypothesis_id": r["hypothesis_id"],
            "entry_type": "register"}).json()
        assert len(body["items"]) == 1
        empty = api_client.get("/api/research/log", params={
            "hypothesis_id": "hyp_none", "entry_type": "register"}).json()
        assert empty["items"] == []

    @pytest.mark.parametrize("page_size", [0, 201])
    def test_page_size_out_of_range_clamped(self, api_client, page_size):
        """/log 的 page_size 越界钳 50（闸门②补强：此前只测 page 维度）。"""
        _register(api_client, "2024-01-01T00:00:00Z")
        body = api_client.get("/api/research/log",
                              params={"page_size": page_size}).json()
        assert body["page_size"] == 50 and len(body["items"]) == 1

    def test_page_clamped_and_beyond_tail(self, api_client):
        """CARD-07 后：超尾页 items 空、total=过滤口径真总数、cursor None。"""
        _register(api_client, "2024-01-01T00:00:00Z")
        clamped = api_client.get("/api/research/log",
                                 params={"page": 0}).json()
        assert clamped["page"] == 1
        tail = api_client.get("/api/research/log",
                              params={"page": 99}).json()
        assert tail["items"] == [] and tail["total"] == 1
        assert tail["next_cursor"] is None

    def test_malformed_data_ranges_passed_through_verbatim(self, api_client, db_path):
        """库内 data_ranges 非法 JSON：端点不炸，原样字符串回传（容错分支）。"""
        rr.append_research_log(db_path, hypothesis_id=None,
                               entry_type="analysis",
                               data_ranges="not-json-{broken")
        body = api_client.get("/api/research/log",
                              params={"entry_type": "analysis"}).json()
        dirty = [e for e in body["items"]
                 if e["data_ranges"] == "not-json-{broken"]
        assert dirty, "脏 data_ranges 应原样回传且不影响端点可用性"


class TestFeatureRequestsEndpoint:
    """分页钳制与 /hypotheses、/log 同款（CARD-05 后统一）：
    page<1→1；page_size 出 [1,200]→50；total=当前页长度。"""

    def test_create_and_list_roundtrip(self, api_client):
        for i in range(2):
            resp = api_client.post("/api/research/feature-requests", json={
                "title": f"需求{i}", "payload": {"k": i}})
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["request_id"].startswith("fr_")
            assert body["status"] == "open"

        listed = api_client.get("/api/research/feature-requests").json()
        assert len(listed["items"]) == 2 and listed["total"] == 2
        payloads = {r["payload"]["k"] for r in listed["items"]}
        assert payloads == {0, 1}, "payload 库存 JSON 串应在响应解码"

    def test_status_filter(self, api_client):
        api_client.post("/api/research/feature-requests", json={
            "title": "t", "payload": {}})
        open_fr = api_client.get("/api/research/feature-requests",
                                 params={"status": "open"}).json()
        assert len(open_fr["items"]) == 1
        closed = api_client.get("/api/research/feature-requests",
                                params={"status": "closed"}).json()
        assert closed["items"] == []

    @pytest.mark.parametrize("page", [0, -1])
    def test_non_positive_page_clamped_to_one(self, api_client, page):
        """page<=0 钳制为 1：回第一页数据且 echo page=1（CARD-05 前为
        负 OFFSET 回首页 + echo 原值的不一致形制）。"""
        api_client.post("/api/research/feature-requests", json={
            "title": "t", "payload": {}})
        body = api_client.get("/api/research/feature-requests",
                              params={"page": page}).json()
        assert len(body["items"]) == 1 and body["total"] == 1
        assert body["page"] == 1

    def test_page_size_zero_clamped_to_default(self, api_client):
        """page_size=0 钳制为 50 缺省（CARD-05 前为 LIMIT 0 假空页）。"""
        api_client.post("/api/research/feature-requests", json={
            "title": "t", "payload": {}})
        body = api_client.get("/api/research/feature-requests",
                              params={"page_size": 0}).json()
        assert body["page_size"] == 50 and len(body["items"]) == 1

    def test_page_size_over_cap_clamped_to_default(self, api_client):
        """page_size 超上限 200 钳制为 50 缺省（CARD-05 前原值透传）。"""
        api_client.post("/api/research/feature-requests", json={
            "title": "t", "payload": {}})
        body = api_client.get("/api/research/feature-requests",
                              params={"page_size": 201}).json()
        assert body["page_size"] == 50 and len(body["items"]) == 1

    def test_beyond_last_page_empty_with_true_total(self, api_client):
        """CARD-07 后：超尾页 items 空、total=真总数、next_cursor None。"""
        api_client.post("/api/research/feature-requests", json={
            "title": "t", "payload": {}})
        body = api_client.get("/api/research/feature-requests",
                              params={"page": 99}).json()
        assert body["items"] == [] and body["total"] == 1
        assert body["next_cursor"] is None


class TestKeysetCursorPagination:
    """CARD-07 修复资产：三端点 keyset 续页契约。

    分页双轨：page/offset 翻页照旧；cursor（上一响应 next_cursor 回传）
    keyset 续页深翻页无漂移，提供时 page 被忽略；total 恒为过滤口径
    真总数；缺省 desc 最新在前；坏 cursor 结构化 422 不静默回首页。
    """

    def _create_five(self, api_client):
        ids = []
        for i in range(5):
            resp = api_client.post("/api/research/feature-requests", json={
                "title": f"t{i}", "payload": {"k": i}})
            assert resp.status_code == 200, resp.text
            ids.append(resp.json()["request_id"])
        return ids

    def test_cursor_walk_covers_all_without_overlap(self, api_client):
        """翻页终止契约：next_cursor 仅空页为 None（当页含末条仍发游标，
        客户端以空页为终止信号——多一次空往返换实现无状态判定）。"""
        ids = self._create_five(api_client)

        seen = []
        cursor = None
        for _ in range(5):  # 2+2+1+空页，防死循环上限
            params = {"page_size": 2}
            if cursor is not None:  # 首页不带 cursor 键（空串会被 422 拒）
                params["cursor"] = cursor
            body = api_client.get("/api/research/feature-requests",
                                  params=params).json()
            assert body["total"] == 5, "total 恒为真总数，不随页变"
            seen.extend(r["request_id"] for r in body["items"])
            cursor = body["next_cursor"]
            if not body["items"]:
                break
        assert body["items"] == [] and cursor is None, \
            "空页 next_cursor=None（终止信号）"
        assert sorted(seen) == sorted(ids), "翻页无重无漏"

    def test_desc_order_newest_first(self, api_client):
        ids = self._create_five(api_client)
        body = api_client.get("/api/research/feature-requests",
                              params={"page_size": 2}).json()
        first_two = {r["request_id"] for r in body["items"]}
        assert first_two == set(ids[-2:]), "缺省 desc：最新两条在首页"
        assert body["items"][0]["request_id"] == ids[-1], "最新者居首"

    def test_cursor_supplies_page_is_ignored(self, api_client):
        """cursor 提供时 page 被忽略（offset=0）——page=99 不产生空页。"""
        self._create_five(api_client)
        first = api_client.get("/api/research/feature-requests",
                               params={"page_size": 2}).json()
        cursor = first["next_cursor"]

        with_page = api_client.get("/api/research/feature-requests", params={
            "page_size": 2, "cursor": cursor, "page": 99}).json()
        without_page = api_client.get("/api/research/feature-requests", params={
            "page_size": 2, "cursor": cursor}).json()
        assert with_page["items"] == without_page["items"]
        assert len(with_page["items"]) == 2, "page=99 未被采用（否则空页）"

    def test_malformed_cursor_rejected_structured(self, api_client):
        for bad in ("no-pipe", "lead|", "|tail", ""):
            resp = api_client.get("/api/research/feature-requests",
                                  params={"cursor": bad})
            assert resp.status_code == 422, f"cursor={bad!r} 应 422"
            detail = resp.json()["detail"]
            assert detail["code"] == "RESEARCH_PARAM_INVALID"
            assert detail["detail"]["legal_form"] == "created_at|id"

    @pytest.mark.parametrize(("endpoint", "setup"), [
        pytest.param("/api/research/feature-requests",
                     lambda c: [c.post("/api/research/feature-requests",
                                       json={"title": f"t{i}", "payload": {}})
                                for i in range(5)], id="需求单"),
        pytest.param("/api/research/hypotheses",
                     lambda c: [_register(c, f"2024-01-0{i + 1}T00:00:00Z")
                                for i in range(5)], id="假设"),
        pytest.param("/api/research/log",
                     lambda c: [_register(c, f"2024-01-0{i + 1}T00:00:00Z")
                                for i in range(5)], id="日志"),
    ])
    def test_cursor_walk_property_all_endpoints(self, api_client, endpoint,
                                                setup):
        """闸门④属性补强：cursor 翻页无重无漏性质三端点同持
        （_apply_keyset 共用，端点层 encode/decode/组装各自实现——
        性质须逐端点独立成立，不能由共用推定）。"""
        setup(api_client)

        seen = []
        cursor = None
        for _ in range(7):
            params = {"page_size": 2}
            if cursor is not None:
                params["cursor"] = cursor
            body = api_client.get(endpoint, params=params).json()
            assert body["total"] == 5, "total 恒为真总数"
            seen.extend((r.get("request_id") or r.get("hypothesis_id")
                         or r.get("log_id")) for r in body["items"])
            cursor = body["next_cursor"]
            if not body["items"]:
                break
        assert body["items"] == [] and cursor is None
        assert len(seen) == len(set(seen)) == 5, "翻页无重无漏"

    def test_hypotheses_and_log_same_contract(self, api_client):
        """契约同款抽查：desc 首页 + cursor 续页 + total 真总数。"""
        for i in range(3):
            resp = _register(api_client, f"2024-01-0{i + 1}T00:00:00Z")
            assert resp.status_code == 200, resp.text

        hyp1 = api_client.get("/api/research/hypotheses",
                              params={"page_size": 1}).json()
        assert hyp1["total"] == 3 and len(hyp1["items"]) == 1
        hyp2 = api_client.get("/api/research/hypotheses", params={
            "page_size": 1, "cursor": hyp1["next_cursor"]}).json()
        assert len(hyp2["items"]) == 1 and hyp2["total"] == 3
        assert (hyp2["items"][0]["hypothesis_id"]
                != hyp1["items"][0]["hypothesis_id"]), "续页前进"

        log1 = api_client.get("/api/research/log",
                              params={"entry_type": "register",
                                      "page_size": 2}).json()
        assert log1["total"] == 3, "entry_type 过滤口径真总数"
        assert len(log1["items"]) == 2 and log1["next_cursor"]


class TestFeatureRequestSizeCaps:
    """CARD-13 修复资产：title/payload 大小前置限（超大结构直入 DB
    前拒绝）。基准：title 字符数 200；payload json.dumps(ensure_ascii=
    False) UTF-8 字节数 64KB。"""

    def test_title_over_200_rejected(self, api_client):
        resp = api_client.post("/api/research/feature-requests", json={
            "title": "超" * 201, "payload": {}})
        assert resp.status_code == 422
        assert any(e["loc"] == ["body", "title"]
                   for e in resp.json()["detail"])

    def test_payload_over_64kb_rejected(self, api_client):
        resp = api_client.post("/api/research/feature-requests", json={
            "title": "t", "payload": {"blob": "x" * 70000}})
        assert resp.status_code == 422
        assert any(e["loc"] == ["body", "payload"]
                   for e in resp.json()["detail"])

    def test_boundary_within_caps_accepted(self, api_client):
        """边界内通过：title 恰 200 字符、payload 恰在 64KB 限内。"""
        resp = api_client.post("/api/research/feature-requests", json={
            "title": "正" * 200, "payload": {"blob": "y" * 60000}})
        assert resp.status_code == 200, resp.text

    @pytest.mark.parametrize(("target", "expect_422"), [
        (65536, False), (65537, True),
    ])
    def test_payload_exact_byte_boundary(self, api_client, target, expect_422):
        """精确字节边界：序列化恰 65536B 通过（> 判定不含等号）、65537B 拒。"""
        import json as _json

        base = len(_json.dumps({"blob": ""},
                               ensure_ascii=False).encode("utf-8"))
        payload = {"blob": "x" * (target - base)}
        assert len(_json.dumps(payload, ensure_ascii=False)
                   .encode("utf-8")) == target, "构造自校验"

        resp = api_client.post("/api/research/feature-requests", json={
            "title": "t", "payload": payload})
        assert (resp.status_code == 422) is expect_422


class TestCorruptedJsonPassthrough:
    """CARD-12 修复资产：库存 JSON 列损坏时端点不 500。

    四端点兜底语义：hypotheses/log/plugins 损坏保留原字符串（审计
    可读）；validation report 的 params 损坏归 {}（range=None——原始
    串不泄入人话摘要，同文件 _parse 先例形制）。
    """

    def test_hypotheses_list_and_detail_keep_raw(self, api_client, db_path):
        rr.save_hypothesis_with_log(
            db_path, hypothesis_id="hyp_dirty", conditions="{corrupted",
            conditions_hash="cd", statement="脏数据",
            data_ranges="not-json", ranges_hash="rd")
        listed = api_client.get("/api/research/hypotheses").json()
        dirty = next(h for h in listed["items"]
                     if h["hypothesis_id"] == "hyp_dirty")
        assert dirty["conditions"] == "{corrupted", "列表损坏列保留原串"
        assert dirty["data_ranges"] == "not-json"

        detail = api_client.get("/api/research/hypotheses/hyp_dirty").json()
        assert detail["conditions"] == "{corrupted"
        assert detail["data_ranges"] == "not-json"

    def test_feature_request_payload_keeps_raw(self, api_client, db_path):
        rr.save_feature_request(db_path, request_id="fr_dirty", title="t",
                                payload="{corrupted", status="open",
                                created_at="2026-10-01T00:00:00")
        listed = api_client.get("/api/research/feature-requests").json()
        dirty = next(r for r in listed["items"] if r["request_id"] == "fr_dirty")
        assert dirty["payload"] == "{corrupted"

    def test_formula_output_columns_keeps_raw(self, api_client, db_path):
        rr.save_plugin_with_registry(
            db_path, source="formula", plugin_id="p_dirty", name="dirty",
            version=1, expression="zscore(close, 20)",
            output_columns="{corrupted", entry_id="e_dirty",
            column_name="dirty_col")
        listed = api_client.get("/api/research/plugins/formula").json()
        dirty = next(r for r in listed["items"] if r["plugin_id"] == "p_dirty")
        assert dirty["output_columns"] == "{corrupted"

    def test_formula_output_columns_empty_string_kept(self, api_client, db_path):
        """output_columns 空串（终验消解）：falsy 不进解析、原样保留。
        None 形态被 NOT NULL 约束排除（不可达），空串是唯一 falsy 形态。"""
        rr.save_plugin_with_registry(
            db_path, source="formula", plugin_id="p_nocol", name="nocol",
            version=1, expression="zscore(close, 20)",
            output_columns="", entry_id="e_nocol", column_name="nocol_col")
        listed = api_client.get("/api/research/plugins/formula").json()
        row = next(r for r in listed["items"]
                   if r["plugin_id"] == "p_nocol")
        assert row["output_columns"] == ""

    def test_validation_report_params_corrupt_range_none(
            self, api_client, db_path):
        """params 损坏归 {}：report 200、range=None（原始串不入人话摘要）。"""
        rr.save_hypothesis_with_log(db_path, **dict(
            hypothesis_id="hyp_v", conditions="{}", conditions_hash="cv",
            statement="v", data_ranges="[]", ranges_hash="rv"))
        # B2 两态判定序（变更一）：补建策略实体使本用例聚焦损坏
        # params 容错（实体缺失 404 有专测覆盖）
        from core.persistence.repository.strategy import save_strategy
        save_strategy(db_path, strategy_id="st_dirty", dna_json="{}",
                      symbol="BTCUSDT", timeframe="4h", name="dirty",
                      source="manual")
        rr.save_validation_with_log(
            db_path, record_id="rec_dirty", hypothesis_id="hyp_v",
            strategy_id="st_dirty", level="isolated_backtest",
            params="{corrupted", result=None, verdict="pass",
            range_hash="rkv")
        resp = api_client.get("/api/research/validation/st_dirty/report")
        assert resp.status_code == 200, "损坏 params 不应 500"
        iso = resp.json()["isolated_backtests"][0]
        assert iso["record_id"] == "rec_dirty"
        assert iso["range"] is None, "params 损坏→{}，range 取值 None"


class TestNotFoundChannels:
    """三处 404 错误通道的结构化 code。"""

    def test_hypothesis_not_found(self, api_client):
        resp = api_client.get("/api/research/hypotheses/hyp_nope")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "RESEARCH_HYPOTHESIS_NOT_FOUND"

    def test_validation_report_not_found(self, api_client):
        """B2 两态判定序（变更一）：实体不存在 → 404
        RESEARCH_STRATEGY_NOT_FOUND（旧码 RESEARCH_REPORT_NOT_FOUND
        退役——按场景语义归位：本用例查的是不存在的策略实体）。"""
        resp = api_client.get("/api/research/validation/st_nope/report")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "RESEARCH_STRATEGY_NOT_FOUND"

    def test_analysis_task_not_found(self, api_client):
        resp = api_client.get("/api/research/analysis/tasks/ant_nope")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "RESEARCH_TASK_NOT_FOUND"


class TestTranslateBodyOmitted:
    def test_translate_without_body_reaches_service_layer(self, api_client):
        """payload 可整体省略（None 缺省）：路由层不炸，404 由服务层给出。"""
        resp = api_client.post("/api/research/translate/hyp_nope")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "RESEARCH_HYPOTHESIS_NOT_FOUND"


class TestFeatureRequestPatch:
    """G1 组（变更二 4.2）：慢通道需求单回销 PATCH——部分更新三规则、
    404/422/幂等/联动清空/必填校验各一锚。

    通道隔离：回销是工程侧动作经 REST；研究侧 MCP 面不设回销工具
    （G1-T09 负向断言在 tests/mcpserver/_expected_tools 面）。
    """

    def _create(self, api_client, title="t"):
        return api_client.post("/api/research/feature-requests", json={
            "title": title, "payload": {}}).json()["request_id"]

    def test_g1_t01_resolved_with_resolution(self, api_client):
        """G1-T01 正向：resolved+resolution → 200 响应含四键
        {source,note,at,by}（at/by 服务端补齐）。"""
        rid = self._create(api_client)
        resp = api_client.patch(f"/api/research/feature-requests/{rid}",
                                json={"status": "resolved",
                                      "resolution": {
                                          "source": "fix-x",
                                          "note": "n"}})
        assert resp.status_code == 200, resp.text
        res = resp.json()["resolution"]
        assert res["source"] == "fix-x" and res["note"] == "n"
        assert res["at"] and res["by"] == "engineering"

    def test_g1_t02_invalid_status_422(self, api_client):
        """G1-T02：值域外 status → 422 detail 披露合法值域。"""
        rid = self._create(api_client)
        resp = api_client.patch(f"/api/research/feature-requests/{rid}",
                                json={"status": "done"})
        assert resp.status_code == 422
        d = resp.json()["detail"]
        assert d["code"] == "RESEARCH_PARAM_INVALID"
        assert d["detail"]["param"] == "status"
        assert d["detail"]["legal_range"] == ["open", "resolved", "wont_fix"]

    def test_g1_t03_not_found_404(self, api_client):
        """G1-T03：不存在 request_id → 404 FEATURE_REQUEST_NOT_FOUND
        （HINTS 新条目——registry_covers_all_raised_codes 机制覆盖）。"""
        resp = api_client.patch(
            "/api/research/feature-requests/fr_nope",
            json={"status": "resolved",
                  "resolution": {"source": "x"}})
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "FEATURE_REQUEST_NOT_FOUND"

    def test_g1_t04_idempotent_same_body(self, api_client):
        """G1-T04 幂等：同 body 重复 PATCH → 200（内容全等——
        resolution.at 不因重复请求刷新）。"""
        rid = self._create(api_client)
        body = {"status": "resolved",
                "resolution": {"source": "fix-y", "note": "m"}}
        r1 = api_client.patch(f"/api/research/feature-requests/{rid}",
                              json=body).json()
        r2 = api_client.patch(f"/api/research/feature-requests/{rid}",
                              json=body).json()
        assert r1 == r2, "幂等短路不重写（at 恒定）"

    def test_g1_t05_open_resets_resolution(self, api_client):
        """G1-T05：回退 status=open → resolution 自动置 null（联动清空）。"""
        rid = self._create(api_client)
        api_client.patch(f"/api/research/feature-requests/{rid}",
                         json={"status": "resolved",
                               "resolution": {"source": "z"}})
        resp = api_client.patch(f"/api/research/feature-requests/{rid}",
                                json={"status": "open"})
        assert resp.status_code == 200
        assert resp.json()["resolution"] is None

    def test_g1_t06_partial_update_keeps_resolution(self, api_client):
        """G1-T06：body 不带 resolution 键=不触碰现值（部分更新规则①）。"""
        rid = self._create(api_client)
        api_client.patch(f"/api/research/feature-requests/{rid}",
                         json={"status": "wont_fix",
                               "resolution": {"source": "keep"}})
        resp = api_client.patch(f"/api/research/feature-requests/{rid}",
                                json={"status": "wont_fix"})
        assert resp.status_code == 200
        assert resp.json()["resolution"]["source"] == "keep"

    def test_g1_t07_legacy_rows_parse_null(self, api_client):
        """G1-T07 回归：list 对无 resolution 值（存量行 null）解析容缺
        不炸（PATCH 响应与 list 条目并列 resolution 键）。"""
        rid = self._create(api_client)
        listed = api_client.get("/api/research/feature-requests").json()
        row = next(r for r in listed["items"] if r["request_id"] == rid)
        assert row["resolution"] is None

    def test_g1_t11_resolved_requires_resolution(self, api_client):
        """G1-T11：PATCH status=resolved 不带 resolution → 422
        detail.param=resolution（resolved 语义=已实现并附出处）。"""
        rid = self._create(api_client)
        resp = api_client.patch(f"/api/research/feature-requests/{rid}",
                                json={"status": "resolved"})
        assert resp.status_code == 422
        d = resp.json()["detail"]
        assert d["code"] == "RESEARCH_PARAM_INVALID"
        assert d["detail"]["param"] == "resolution"

    def test_g1_t12_wont_fix_optional_resolution(self, api_client):
        """G1-T12：wont_fix 不带 resolution → 200 合法（裁决依据=
        用户终局裁决；note 建议不强制）。"""
        rid = self._create(api_client)
        resp = api_client.patch(f"/api/research/feature-requests/{rid}",
                                json={"status": "wont_fix"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "wont_fix"

    def test_g1_chain_reset_then_resolved_requires_again(self, api_client):
        """G1-T05/T11 组合：resolved→open（自动清）→再 PATCH resolved
        不带 resolution → 422（清空后再标 resolved 须重附出处——
        自动清与必填校验的衔接断言）。"""
        rid = self._create(api_client)
        api_client.patch(f"/api/research/feature-requests/{rid}",
                         json={"status": "resolved",
                               "resolution": {"source": "a"}})
        api_client.patch(f"/api/research/feature-requests/{rid}",
                         json={"status": "open"})
        resp = api_client.patch(f"/api/research/feature-requests/{rid}",
                                json={"status": "resolved"})
        assert resp.status_code == 422
        assert resp.json()["detail"]["detail"]["param"] == "resolution"

    def test_g3_t01_docs_restart_discipline_locatable(self):
        """G3-T01：两文档纪律条目 grep 可定位+语义覆盖。"""
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        map_md = (root / "docs" / "MAP.md").read_text(encoding="utf-8")
        guide = (root / "docs" / "AI_CODING_GUIDE.md").read_text(
            encoding="utf-8")
        assert "重启纪律" in map_md and "旧代码" in map_md
        assert "回销对应 feature_requests" in guide
        assert "restart.sh" in guide

    def test_g3_t02_no_start_services_script(self):
        """G3-T02 负向：scripts/ 下无 start_services.sh（议题 A 闭环
        裁定——零新建脚本）。"""
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        assert not (root / "scripts" / "start_services.sh").exists()

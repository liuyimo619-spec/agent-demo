"""离线测试：不联网、不连数据库、不调用模型。

覆盖纯逻辑与边界：工具名合并、历史裁剪、工具闸门、超步计数、
select_tools 校验、返回瘦身、认证哈希、异常归因、计划表结构与入库流水线。

运行（项目根目录）：
    uv run python -m unittest discover -s tests -v
"""

import asyncio
import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain.agents.middleware import ToolCallRequest
from pydantic import ValidationError

import middleware as mw
import memory.plan_manager as pm
import tools as tl
from errors import identify_module, should_retry
from security import (
    generate_session_token,
    hash_password,
    hash_session_token,
    verify_password,
)
from memory.plan_manager import Activity, DayPlan, PlanData


class TestToolNameMerge(unittest.TestCase):
    def test_dedupe_keeps_order(self):
        self.assertEqual(mw.merge_tool_names(["a", "b"], ["b", "c"]), ["a", "b", "c"])

    def test_counts_accumulate(self):
        self.assertEqual(mw.merge_tool_counts({"a": 1}, {"a": 1, "b": 1}), {"a": 2, "b": 1})

    def test_counts_none_default(self):
        self.assertEqual(mw.merge_tool_counts(None, {"x": 1}), {"x": 1})


class TestTrimHistory(unittest.TestCase):
    def _state(self, pairs):
        msgs = []
        for i in range(pairs):
            msgs.append(HumanMessage(content=f"u{i}", id=f"h{i}"))
            msgs.append(AIMessage(content=f"a{i}", id=f"a{i}"))
        return {"messages": msgs}

    def test_no_trim_under_limit(self):
        self.assertIsNone(mw.trim_history_middleware.before_model(self._state(20), None))

    def test_trim_keeps_last_20_rounds(self):
        state = self._state(25)
        res = mw.trim_history_middleware.before_model(state, None)
        removed_ids = {r.id for r in res["messages"]}
        self.assertEqual(len(removed_ids), 10)
        kept = [msg for msg in state["messages"] if msg.id not in removed_ids]
        self.assertEqual(len(kept), 40)
        self.assertIsInstance(kept[0], HumanMessage)
        self.assertEqual(kept[0].content, "u5")


class TestToolCallGuards(unittest.TestCase):
    def _req(self, name="maps_geo", tool_counts=None, step_count=10, loaded=None):
        return ToolCallRequest(
            tool_call={"name": name, "id": "tc1", "args": {}},
            tool=SimpleNamespace(),
            state={"loaded_tools": loaded or [], "tool_counts": tool_counts or {}, "step_count": step_count},
            runtime=None,
        )

    def test_tool_cap_blocks_execution(self):
        req = self._req(tool_counts={"maps_geo": mw.TOOL_LIMIT["maps_geo"]})

        async def handler(_):
            raise AssertionError("已达上限仍执行")

        res = asyncio.run(mw.auto_load_tool_middleware.awrap_tool_call(req, handler))
        msg = res.update["messages"][0]
        self.assertEqual(msg.status, "error")
        self.assertIn("上限", msg.content)

    def test_under_cap_counts_and_loads(self):
        req = self._req()

        async def handler(_):
            return ToolMessage(content="ok", tool_call_id="tc1")

        res = asyncio.run(mw.auto_load_tool_middleware.awrap_tool_call(req, handler))
        self.assertEqual(res.update["tool_counts"], {"maps_geo": 1})
        self.assertEqual(res.update["loaded_tools"], ["maps_geo"])

    def test_hard_limit_blocks_tool(self):
        req = self._req(step_count=mw.HARD_LIMIT_STEP)

        async def handler(_):
            raise AssertionError("超步硬上限仍执行工具")

        res = asyncio.run(mw.auto_load_tool_middleware.awrap_tool_call(req, handler))
        self.assertIn("超出步数上限", res.update["messages"][0].content)


class TestStepCounter(unittest.TestCase):
    def test_increment(self):
        res = asyncio.run(mw.track_step_count_middleware.abefore_model({"step_count": 10}, None))
        self.assertEqual(res["step_count"], 11)

    def test_missing_counter_backfilled(self):
        res = asyncio.run(mw.track_step_count_middleware.abefore_model({}, None))
        self.assertEqual(res["step_count"], 0)

    def test_hard_limit_jumps_to_end(self):
        res = asyncio.run(
            mw.track_step_count_middleware.abefore_model({"step_count": mw.HARD_LIMIT_STEP}, None)
        )
        self.assertEqual(res.goto, "__end__")
        self.assertEqual(res.update["messages"][0].name, "step_limit_notice")


class TestSelectTools(unittest.TestCase):
    def setUp(self):
        fake_tools = [
            SimpleNamespace(name="maps_geo", description="地理编码"),
            SimpleNamespace(name="maps_distance", description="距离"),
        ]
        self.selector = tl.build_tool_selector(fake_tools)
        self.runtime = SimpleNamespace(tool_call_id="tc_sel")

    def test_valid_names(self):
        res = asyncio.run(self.selector.coroutine(name=["maps_geo"], runtime=self.runtime))
        self.assertEqual(res.update["loaded_tools"], ["maps_geo"])

    def test_invalid_name_rejected(self):
        res = asyncio.run(self.selector.coroutine(name=["not_exist"], runtime=self.runtime))
        self.assertEqual(res.status, "error")
        self.assertIn("无效的工具名称", res.content)


class TestCleanPayload(unittest.TestCase):
    def test_blacklist_removed_recursively(self):
        payload = {"name": "x", "tel": "110", "photos": ["p"], "child": {"tel": "119", "name": "y"}}
        self.assertEqual(tl.recursive_clean(payload), {"name": "x", "child": {"name": "y"}})

    def test_children_key_removed_whole(self):
        payload = {"name": "x", "children": [{"name": "y"}]}
        self.assertEqual(tl.recursive_clean(payload), {"name": "x"})


class TestSecurity(unittest.TestCase):
    def test_token_hash_deterministic(self):
        token, token_hash = generate_session_token()
        self.assertEqual(hash_session_token(token), token_hash)
        self.assertEqual(len(token_hash), 64)
        self.assertNotEqual(generate_session_token()[0], token)

    def test_password_roundtrip(self):
        h = hash_password("abc123456")
        self.assertTrue(verify_password("abc123456", h))
        self.assertFalse(verify_password("wrong", h))


class TestErrorClassification(unittest.TestCase):
    def test_retryable(self):
        self.assertTrue(should_retry(Exception("CUQPS limit exceeded")))
        self.assertTrue(should_retry(Exception("Read timed out")))

    def test_not_retryable(self):
        self.assertFalse(should_retry(Exception("model_not_found")))
        self.assertIn("高德", identify_module(Exception("cuqps")))


class TestPlanSchema(unittest.TestCase):
    def _plan(self, slot="上午"):
        return PlanData(
            city="成都",
            days=[DayPlan(day_index=1, day_of_week="周一", activities=[
                Activity(time_slot=slot, place="武侯祠", meal="无", place_cost=Decimal("50"),
                         meal_cost=Decimal("0"), transport_cost=Decimal("5"), note=""),
            ])],
        )

    def test_defaults_not_llm_filled(self):
        plan = self._plan()
        self.assertEqual(plan.cost, 0)
        self.assertEqual(plan.remarks, "无")
        self.assertEqual(plan.days[0].day_cost, 0)

    def test_bad_time_slot_rejected(self):
        with self.assertRaises(ValidationError):
            self._plan("中午")

    def test_day_index_bounds(self):
        with self.assertRaises(ValidationError):
            DayPlan(day_index=0, day_of_week="周一", activities=[])

    def test_json_roundtrip_keeps_decimal(self):
        raw = self._plan().model_dump_json()
        self.assertEqual(Decimal(json.loads(raw)["days"][0]["activities"][0]["place_cost"]), Decimal("50"))


class TestSavePlanPipeline(unittest.TestCase):
    def setUp(self):
        self._orig_critic = pm.run_plan_critic
        self._orig_key = pm._generate_plan_key
        self._orig_session = pm.SessionLocal
        self.inserted = []

    def tearDown(self):
        pm.run_plan_critic = self._orig_critic
        pm._generate_plan_key = self._orig_key
        pm.SessionLocal = self._orig_session

    def _patch(self, verdict):
        async def fake_critic(_msgs):
            self.critic_input = _msgs
            return verdict

        async def fake_key(_uid, _city):
            return 1

        inserted = self.inserted

        class FakeBegin:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute(self, stmt):
                inserted.append(stmt)

        class FakeSessionLocal:
            def begin(self):
                return FakeBegin()

        pm.run_plan_critic = fake_critic
        pm._generate_plan_key = fake_key
        pm.SessionLocal = FakeSessionLocal()

    def _runtime(self):
        detail = ToolMessage(
            content=[{"type": "text", "text": json.dumps({"name": "武侯祠", "opentime2": "09:00-18:00"})}],
            name="maps_search_detail",
            tool_call_id="t0",
        )
        return SimpleNamespace(
            state={"user_id": "u1", "messages": [HumanMessage(content="2人成都2天"), detail]},
            tool_call_id="tc_save",
        )

    def _plan(self):
        return PlanData(city="成都", days=[DayPlan(day_index=1, day_of_week="周一", activities=[
            Activity(time_slot="上午", place="武侯祠", meal="无", place_cost=Decimal("50"),
                     meal_cost=Decimal("0"), transport_cost=Decimal("5"), note=""),
            Activity(time_slot="下午", place="锦里", meal="火锅", place_cost=Decimal("0"),
                     meal_cost=Decimal("80"), transport_cost=Decimal("10"), note=""),
        ])])

    def test_approved_writes_and_emits_card(self):
        self._patch(SimpleNamespace(decision="合理", reason="ok"))
        cmd = asyncio.run(pm._save_plan(self._runtime(), self._plan(), "初版"))
        self.assertEqual(len(self.inserted), 1)
        msg = cmd.update["messages"][0]
        payload = json.loads(msg.artifact["markdown"])
        self.assertEqual(Decimal(str(payload["cost"])), Decimal("145"))
        self.assertIn("武侯祠", self.critic_input[0].content)

    def test_rejected_does_not_write(self):
        self._patch(SimpleNamespace(decision="不合理", reason="周一闭馆"))
        cmd = asyncio.run(pm._save_plan(self._runtime(), self._plan(), "初版"))
        msg = cmd.update["messages"][0]
        self.assertEqual(msg.status, "error")
        self.assertIn("周一闭馆", msg.content)
        self.assertEqual(self.inserted, [])

    def test_critic_error_does_not_write(self):
        self._patch("系统错误")
        cmd = asyncio.run(pm._save_plan(self._runtime(), self._plan(), "初版"))
        self.assertEqual(cmd.update["messages"][0].status, "error")
        self.assertEqual(self.inserted, [])


class TestRetryBehaviour(unittest.TestCase):
    """characterization: chat_loop 重试复用同一个 input_data 时的真实行为"""

    def _graph(self):
        from typing import Annotated, TypedDict
        from langgraph.graph import StateGraph, START, END, add_messages
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.types import Overwrite

        class S(TypedDict):
            messages: Annotated[list, add_messages]
            step_count: int
            tool_counts: Annotated[dict, mw.merge_tool_counts]

        async def node(state):
            return {
                "messages": [AIMessage(content="回复")],
                "step_count": (state.get("step_count") or 0) + 1,
                "tool_counts": {"maps_geo": 1},
            }

        g = StateGraph(S)
        g.add_node("n", node)
        g.add_edge(START, "n")
        g.add_edge("n", END)
        return g.compile(checkpointer=InMemorySaver()), Overwrite

    def test_user_message_not_duplicated(self):
        app, Overwrite = self._graph()
        cfg = {"configurable": {"thread_id": "t1"}}
        inp = {"messages": [HumanMessage(content="用户输入")], "step_count": 0, "tool_counts": Overwrite({})}
        asyncio.run(app.ainvoke(inp, config=cfg))
        asyncio.run(app.ainvoke(inp, config=cfg))
        state = asyncio.run(app.aget_state(cfg)).values
        humans = [m for m in state["messages"] if isinstance(m, HumanMessage)]
        self.assertEqual(len(humans), 1)
        self.assertEqual(len(state["messages"]), 3)

    def test_retry_resets_budget_counters(self):
        app, Overwrite = self._graph()
        cfg = {"configurable": {"thread_id": "t2"}}
        inp = {"messages": [HumanMessage(content="用户输入")], "step_count": 0, "tool_counts": Overwrite({})}
        asyncio.run(app.ainvoke(inp, config=cfg))
        asyncio.run(app.ainvoke(inp, config=cfg))
        state = asyncio.run(app.aget_state(cfg)).values
        self.assertEqual(state["step_count"], 1)
        self.assertEqual(state["tool_counts"], {"maps_geo": 1})


if __name__ == "__main__":
    unittest.main(verbosity=2)

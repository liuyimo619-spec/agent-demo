"""
同账号并发写入同一 thread 的对话历史丢失 —— 最小复现
================================================================

背景：api.py 里 thread_id = user_id，InMemorySaver 按 thread 存 state。
      同一账号两端同时发消息时，两个 ainvoke 并行跑在同一个 thread 上。

运行（手工执行，文件名不匹配 test*.py，unittest discover 不会收集它）：
    uv run python tests/repro_thread_race.py
    uv run python tests/repro_thread_race.py 30    # 指定轮数

判据：一轮对话结束后读取 state，若 A、B 各自的回复没有同时存在，即记为丢失。
"""

import asyncio
import sys

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, MessagesState, StateGraph

# 真实场景这一步是模型调用（几百毫秒到几秒），必然让出控制权。
# 这里用一个可调的 sleep 模拟，让两个请求的超级步真正交错。
DELAY = 0.02


async def echo(state: MessagesState):
    text = state["messages"][-1].content
    await asyncio.sleep(DELAY)
    return {"messages": [AIMessage(content=f"reply-{text}")]}


def build(saver):
    g = StateGraph(MessagesState)
    g.add_node("echo", echo)
    g.add_edge(START, "echo")
    return g.compile(checkpointer=saver)


def show(msgs):
    return " ".join(
        f"{type(m).__name__.replace('Message', '')}:{m.content}" for m in msgs
    )


async def concurrent_trial(i):
    """两个请求同时打到同一个 thread_id。"""
    app = build(InMemorySaver())
    cfg = {"configurable": {"thread_id": f"user-{i}"}}
    try:
        await asyncio.gather(
            app.ainvoke({"messages": [HumanMessage(content="A")]}, cfg),
            app.ainvoke({"messages": [HumanMessage(content="B")]}, cfg),
        )
    except Exception as e:  # 并发写同一 thread 也可能直接抛错
        return f"异常：{type(e).__name__}: {e}", False
    snap = await app.aget_state(cfg)
    msgs = snap.values.get("messages", [])
    got = {m.content for m in msgs}
    return show(msgs), {"reply-A", "reply-B"}.issubset(got)


async def sequential_trial():
    """对照组：同样两个请求，串行执行。"""
    app = build(InMemorySaver())
    cfg = {"configurable": {"thread_id": "user-seq"}}
    await app.ainvoke({"messages": [HumanMessage(content="A")]}, cfg)
    await app.ainvoke({"messages": [HumanMessage(content="B")]}, cfg)
    snap = await app.aget_state(cfg)
    msgs = snap.values.get("messages", [])
    got = {m.content for m in msgs}
    return show(msgs), {"reply-A", "reply-B"}.issubset(got)


async def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 10

    print("=" * 78)
    print(f"对照组（串行，同一 thread 连续两次）  DELAY={DELAY}s")
    text, ok = await sequential_trial()
    print(f"  最终历史: {text}")
    print(f"  两条回复都在: {ok}")
    print("=" * 78)
    print(f"实验组（并发，同一 thread 同时两次）  共 {rounds} 轮")
    lost = 0
    for i in range(rounds):
        text, ok = await concurrent_trial(i)
        lost += 0 if ok else 1
        print(f"  第{i + 1:>2}轮  {'完整' if ok else '缺回复'}  {text}")
    print("-" * 78)
    print(f"丢失轮数: {lost}/{rounds}  ({lost / rounds:.0%})")
    print("=" * 78)


if __name__ == "__main__":
    asyncio.run(main())

"""对话性能监测：记录排队、各 Agent 节点及整条处理链耗时。"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from time import perf_counter
from sqlalchemy import select

from ..database import session_factory
from ..models import ChatRun, WorkItem


STEP_STAGE_NAMES = {
    "step1_understand": "正在理解问题",
    "step2_retrieve": "正在检索资料和查询工具",
    "step3_plan": "正在生成行程草案",
    "step4_validate": "正在校验行程",
    "step5_answer": "正在生成回答",
}


def record_step_duration(run_id: str, step_name: str, duration_ms: float, status: str) -> None:
    """累计节点耗时；行程修订多次经过同一节点时保留总次数和总耗时。"""
    with session_factory() as db:
        run = db.get(ChatRun, run_id)
        if not run:
            return
        result = dict(run.result or {})
        performance = dict(result.get("performance") or {})
        steps = dict(performance.get("steps") or {})
        previous = dict(steps.get(step_name) or {})
        steps[step_name] = {
            "calls": int(previous.get("calls", 0)) + 1,
            "total_ms": round(float(previous.get("total_ms", 0)) + duration_ms, 1),
            "last_ms": round(duration_ms, 1),
            "status": status,
        }
        performance["steps"] = steps
        result["performance"] = performance
        run.result = result
        db.commit()


def set_run_stage(run_id: str, stage: str) -> None:
    """更新 SSE 可见阶段，不改变任务状态和租约。"""
    with session_factory() as db:
        job = db.scalar(
            select(WorkItem).where(WorkItem.kind == "chat", WorkItem.target_id == run_id)
        )
        if job:
            job.stage = stage
            db.commit()


def measure_agent_node(step_name: str, node: Callable[[dict], dict]) -> Callable[[dict], dict]:
    """包装 LangGraph 节点，在成功和异常路径都持久化耗时。"""
    def measured(state: dict) -> dict:
        run_id = state["run_id"]
        set_run_stage(run_id, STEP_STAGE_NAMES[step_name])
        started = perf_counter()
        try:
            output = node(state)
        except Exception:
            record_step_duration(run_id, step_name, (perf_counter() - started) * 1000, "failed")
            raise
        record_step_duration(run_id, step_name, (perf_counter() - started) * 1000, "completed")
        return output

    measured.__name__ = f"measure_{step_name}"
    return measured


def calculate_queue_wait_ms(created_at: datetime, processing_started_at: datetime) -> float:
    """计算任务从创建到工作进程开始处理的等待时间。"""
    return round(max(0.0, (processing_started_at - created_at).total_seconds() * 1000), 1)

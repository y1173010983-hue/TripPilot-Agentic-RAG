"""对话记忆：持久化原文、增量历史摘要、结构化需求和显式长期偏好。"""

from sqlalchemy import select

from ..config import get_settings
from ..models import ChatSession, ChatRun, Message, Preference, TripPlan


def step1_load_conversation_context(
    db,
    session: ChatSession,
    current_message_id: str,
) -> dict:
    """增量压缩旧消息并加载近期窗口；失败回复不会作为可信答案进入模型上下文。"""
    messages = list(
        db.scalars(
            select(Message)
            .where(
                Message.session_id == session.id,
                Message.status == "completed",
                Message.seq > session.summary_through,
            )
            .order_by(Message.seq)
        )
    )
    # 无关问答仅保留在可见聊天记录，不参与窗口选择或历史摘要。
    excluded_ids = set()
    for run in db.scalars(select(ChatRun).where(ChatRun.session_id == session.id)):
        if (run.result or {}).get("intent") == "out_of_scope":
            excluded_ids.update((run.user_message_id, run.assistant_message_id))
    messages = [message for message in messages if message.id not in excluded_ids]
    budget = get_settings().context_char_budget
    chosen: list[dict] = []
    used = 0
    for message in reversed(messages):
        if used + len(message.content) > budget and chosen:
            break
        chosen.append(
            {
                "role": message.role,
                "content": message.content[:budget],
                "seq": message.seq,
            }
        )
        used += len(message.content)
    chosen.reverse()

    boundary = chosen[0]["seq"] if chosen else session.summary_through + 1
    older = [message for message in messages if message.seq < boundary]
    # 摘录式摘要保留来源序号，不让另一个 LLM 把猜测混进用户历史。
    if older:
        additions = "\n".join(
            f"[{message.seq} {message.role}] {message.content[:200]}"
            for message in older
        )
        session.summary = (session.summary + "\n" + additions).strip()[-12000:]
        session.summary_through = older[-1].seq

    preferences = {
        preference.key: preference.value
        for preference in db.scalars(
            select(Preference).where(Preference.user_id == session.user_id)
        )
    }
    plan = db.scalar(
        select(TripPlan)
        .where(TripPlan.session_id == session.id)
        .order_by(TripPlan.version.desc())
        .limit(1)
    )
    db.commit()
    return {
        "history": [
            {"role": message["role"], "content": message["content"]}
            for message in chosen
        ],
        "summary": session.summary,
        "requirements": session.requirements,
        "preferences": preferences,
        "previous_plan": plan.content if plan else None,
        "current_message_id": current_message_id,
    }


def step2_merge_explicit_requirements(previous: dict, updates: dict) -> dict:
    """只合并允许字段；新明确条件覆盖旧值，避免模型写入任意业务字段。"""
    allowed = {
        "destination",
        "departure",
        "start_date",
        "end_date",
        "days",
        "travelers",
        "budget",
        "budget_scope",
        "pace",
        "interests",
        "diet",
        "hotel_area",
        "rooms",
        "locked_activities",
    }
    return {
        **previous,
        **{
            key: value
            for key, value in updates.items()
            if key in allowed and value is not None
        },
    }


def step3_is_destination_switch(previous: dict, updates: dict) -> bool:
    """判断本轮是否把已确认的旅行目的地替换为另一个目的地。"""
    previous_destination = str(previous.get("destination") or "").strip()
    updated_destination = str(updates.get("destination") or "").strip()
    return bool(
        previous_destination
        and updated_destination
        and previous_destination.casefold() != updated_destination.casefold()
    )


def step4_rebuild_requirements_after_destination_switch(previous: dict, updates: dict) -> dict:
    """切换目的地时清理旧地点专属条件，只保留可跨旅行复用的信息和本轮明确更新。"""
    destination_bound_keys = {
        "start_date",
        "end_date",
        "days",
        "hotel_area",
        "locked_activities",
    }
    reusable_requirements = {
        key: value
        for key, value in previous.items()
        if key not in destination_bound_keys
    }
    return step2_merge_explicit_requirements(reusable_requirements, updates)

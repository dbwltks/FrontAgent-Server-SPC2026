from app.tasks.flow_brief_applier import apply_brief_plan_to_template
from app.tasks.flow_brief_planner import TaskFlowBriefPlan, validate_brief_plan
from app.tasks.flow_generator import load_task_flow_template


def test_validate_brief_plan_rejects_unknown_slot():
    plan = TaskFlowBriefPlan(
        template_key="reservation_create",
        name="청소 예약",
        description="청소 예약 접수",
        trigger_description="고객이 청소 예약을 원할 때",
        trigger_examples=["예약해줘", "청소 예약하고 싶어요", "방문 예약"],
        required_slots=["unknown_slot"],
        reasoning="test",
    )
    try:
        validate_brief_plan(plan)
        assert False, "expected ValueError"
    except ValueError as error:
        assert "unknown_slot" in str(error)


def test_apply_brief_plan_patches_flow_and_instructions():
    plan = TaskFlowBriefPlan(
        template_key="reservation_create",
        name="입주청소 예약",
        description="입주/이사 청소 예약 접수",
        trigger_description="고객이 청소 예약을 신청할 때",
        trigger_examples=["입주청소 예약", "청소 예약해줘", "이사청소 잡아줘"],
        required_slots=[
            "service_item",
            "reservation_date",
            "reservation_time",
            "customer_phone",
            "customer_name",
        ],
        assistant_tone_hint="존댓말, 짧게",
        reasoning="청소 예약 생성",
    )
    template = load_task_flow_template("reservation_create")
    patched = apply_brief_plan_to_template(template, plan)

    assert patched["flow"]["name"] == "입주청소 예약"
    assert patched["flow"]["trigger_examples"][0] == "입주청소 예약"

    ask_details = next(n for n in patched["nodes"] if n["node_key"] == "ask_reservation_details")
    assert "[운영자 맞춤 설정]" in ask_details["config"]["instruction"]
    assert "party_size" in ask_details["config"]["instruction"]

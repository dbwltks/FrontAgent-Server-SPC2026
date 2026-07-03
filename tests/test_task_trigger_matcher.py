from app.tasks.service_selection import build_service_selection_message
from app.tasks.trigger_matcher import match_task_trigger


def _flow(**overrides):
    base = {
        "id": "flow-create",
        "name": "예약 생성 플로우",
        "trigger_intent": "reservation_create",
        "trigger_description": "고객이 새 예약을 원할 때",
        "trigger_examples": ["예약하고 싶어요", "방문 예약 잡아주세요"],
        "allowed_channels": ["chat", "voice"],
        "is_enabled": True,
    }
    base.update(overrides)
    return base


def test_match_reservation_create_from_db_example():
    match = match_task_trigger("예약하고 싶어요", [_flow()])
    assert match is not None
    assert match.flow_id == "flow-create"
    assert match.task_type == "reservation_create"


def test_match_reservation_create_from_default_example():
    match = match_task_trigger(
        "입주 청소 예약해줘",
        [_flow(trigger_examples=[])],
    )
    assert match is not None
    assert match.task_type == "reservation_create"


def test_policy_question_does_not_start_task():
    flows = [
        _flow(),
        _flow(
            id="flow-cancel",
            name="예약 취소",
            trigger_intent="reservation_cancel",
            trigger_examples=["예약 취소해줘"],
        ),
    ]
    assert match_task_trigger("예약 변경 가능한가요?", flows) is None


def test_cancel_flow_matches_cancel_example():
    match = match_task_trigger(
        "예약 취소해줘",
        [
            _flow(),
            _flow(
                id="flow-cancel",
                name="예약 취소",
                trigger_intent="reservation_cancel",
                trigger_examples=["예약 취소해줘"],
            ),
        ],
    )
    assert match is not None
    assert match.task_type == "reservation_cancel"


def test_service_catalog_question_matches_create_flow():
    match = match_task_trigger("어떤 서비스 있어요?", [_flow(trigger_examples=[])])
    assert match is not None
    assert match.task_type == "reservation_create"


def test_build_service_selection_message():
    message = build_service_selection_message(
        variables={
            "available_services": {
                "services": [{"name": "화장실 청소"}, {"name": "입주 청소"}]
            }
        },
        current_node_key="ask_service",
        status="waiting_user_input",
    )
    assert message is not None
    assert "화장실 청소" in message
    assert "입주 청소" in message

from app.graph.state import AgentState
from app.repositories.rule_repo import get_active_rules
from app.rules.rule_engine import (
    filter_rules_by_intent,
    get_block_rules,
    get_handoff_rules,
)


def get_rule_response_message(rule: dict) -> str:
    """
    rule에 설정된 응답 메시지를 안전하게 가져온다.

    우선순위:
    1. response_message
    2. instruction
    3. 기본 안내 문구
    """
    return (
        rule.get("response_message")
        or rule.get("instruction")
        or "해당 요청은 현재 운영 규칙상 처리할 수 없습니다."
    )


def rule_node(state: AgentState) -> AgentState:
    all_rules = get_active_rules(state["organization_id"])

    matched_rules = filter_rules_by_intent(
        rules=all_rules,
        intent=state.get("intent"),
        user_message=state["user_message"],
    )

    state["rules"] = matched_rules
    state["applied_rules"] = [
        rule.get("name", "unnamed_rule")
        for rule in matched_rules
    ]

    # 1. block 룰이 있으면 AI 응답 생성으로 넘기지 않고 고정 응답을 만든다.
    block_rules = get_block_rules(matched_rules)

    if block_rules:
        block_rule = block_rules[0]

        state["final_response"] = get_rule_response_message(block_rule)

        return state

    # 2. handoff 룰이 있으면 상담원 연결 안내 응답을 만든다.
    handoff_rules = get_handoff_rules(matched_rules)

    if handoff_rules:
        handoff_rule = handoff_rules[0]

        state["final_response"] = get_rule_response_message(handoff_rule)

        return state

    # 3. warn 룰은 여기서 막지 않는다.
    # applied_rules에 들어간 뒤 response_node의 프롬프트에서 참고하게 둔다.
    return state
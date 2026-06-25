from app.graph.state import AgentState
from app.repositories.rule_repo import get_active_rules


def rule_node(state: AgentState) -> dict:
    """
    현재 조직에 등록된 활성 규칙 목록을 가져온다.

    이번 rules 구조에서는 사용자 메시지와 규칙을 비교하지 않는다.
    즉, 필터/트리거/액션 처리를 하지 않는다.

    checkpointer가 thread_id(=organization_id:session_id) 기준으로 state를
    영속화하므로, 이전 턴에서 이미 채운 rules가 있으면 이번 세션(통화/채팅방)
    동안은 그대로 재사용하고 DB를 다시 조회하지 않는다. 규칙은 세션 시작
    시점 기준으로 고정되며, 세션 도중 관리자가 규칙을 바꿔도 그 세션에는
    반영되지 않는다(다음 세션부터 반영). 매 턴 DB 왕복을 없애 응답 지연을
    줄이기 위한 트레이드오프다.

    역할:
    - 첫 턴: organization_id 기준으로 활성 rules 조회
    - 이후 턴: state["rules"]를 그대로 재사용
    - 관리자 로그용으로 state["applied_rules"]에 규칙 이름 저장

    decision/conversation과 같은 superstep에서 병렬 실행되므로, 자신이 바꾸는
    키(rules/applied_rules)만 dict로 반환한다. 전체 state를 반환하면 다른 병렬
    노드와 같은 키에 동시에 쓰는 것으로 인식돼 InvalidUpdateError가 난다.
    """

    existing_rules = state.get("rules")
    if existing_rules:
        return {
            "rules": existing_rules,
            "applied_rules": state.get("applied_rules", []),
        }

    organization_id = state.get("organization_id")

    if not organization_id:
        return {"rules": [], "applied_rules": []}

    rules = get_active_rules(organization_id)

    return {
        "rules": rules,
        "applied_rules": [rule.get("name", "unnamed_rule") for rule in rules],
    }
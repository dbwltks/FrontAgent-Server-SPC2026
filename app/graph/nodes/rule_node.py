from app.graph.state import AgentState
from app.repositories.rule_repo import get_active_rules
from app.rules.rule_engine import filter_rules_by_intent


def rule_node(state: AgentState) -> AgentState:
    all_rules = get_active_rules(state["organization_id"])

    matched_rules = filter_rules_by_intent(
        rules=all_rules,
        intent=state.get("intent"),
        user_message=state["user_message"],
    )

    state["rules"] = matched_rules
    state["applied_rules"] = [
        rule.get("name", "unnamed_rule") for rule in matched_rules
    ]

    return state
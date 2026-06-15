def filter_rules_by_intent(
    rules: list[dict],
    intent: str | None,
    user_message: str,
) -> list[dict]:
    """
    현재는 간단한 방식:
    - trigger_condition이 all이면 항상 적용
    - rule_type이 intent와 같으면 적용
    - filters 키워드가 user_message에 포함되면 적용

    나중에는 LLM 기반 rule matching으로 고도화 가능.
    """

    matched_rules = []

    for rule in rules:
        trigger_condition = (rule.get("trigger_condition") or "").lower()
        rule_type = (rule.get("rule_type") or "").lower()
        filters = rule.get("filters") or []

        if trigger_condition == "all":
            matched_rules.append(rule)
            continue

        if intent and rule_type == intent:
            matched_rules.append(rule)
            continue

        for keyword in filters:
            if str(keyword) in user_message:
                matched_rules.append(rule)
                break

    return matched_rules
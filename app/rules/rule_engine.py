def normalize_text(text: str | None) -> str:
    """
    비교를 쉽게 하기 위해 문자열을 정리한다.
    지금은 공백 제거 + 소문자 변환만 한다.
    """
    if not text:
        return ""

    return str(text).replace(" ", "").lower()


def normalize_filters(filters) -> list[str]:
    """
    Supabase jsonb에서 가져온 filters 값을 안전하게 list[str]로 변환한다.

    예상 형태:
    ["에어컨", "냉장고"]

    혹시 나중에 이런 형태가 와도 최대한 대응:
    {"keywords": ["에어컨", "냉장고"]}
    """
    if not filters:
        return []

    if isinstance(filters, list):
        return [str(item) for item in filters]

    if isinstance(filters, dict):
        keywords = filters.get("keywords") or filters.get("items") or []
        if isinstance(keywords, list):
            return [str(item) for item in keywords]

    return []


def contains_any_keyword(user_message: str, filters: list[str]) -> bool:
    """
    filters 중 하나라도 user_message에 포함되면 True
    """
    normalized_message = normalize_text(user_message)

    for keyword in filters:
        normalized_keyword = normalize_text(keyword)

        if normalized_keyword and normalized_keyword in normalized_message:
            return True

    return False


def contains_all_keywords(user_message: str, filters: list[str]) -> bool:
    """
    filters에 있는 모든 키워드가 user_message에 포함되면 True
    """
    normalized_message = normalize_text(user_message)

    if not filters:
        return False

    for keyword in filters:
        normalized_keyword = normalize_text(keyword)

        if not normalized_keyword:
            return False

        if normalized_keyword not in normalized_message:
            return False

    return True


def is_intent_matched(rule: dict, intent: str | None) -> bool:
    """
    rule_type과 intent가 같은지 확인한다.
    """
    if not intent:
        return False

    rule_type = normalize_text(rule.get("rule_type"))
    normalized_intent = normalize_text(intent)

    return rule_type == normalized_intent


def is_rule_matched(
    rule: dict,
    intent: str | None,
    user_message: str,
) -> bool:
    """
    rule 하나가 현재 사용자 메시지에 적용되는지 판단한다.
    """
    trigger_condition = normalize_text(rule.get("trigger_condition"))
    filters = normalize_filters(rule.get("filters"))

    # trigger_condition이 비어있으면 기존 방식과 비슷하게 처리
    # 1. intent가 같거나
    # 2. filters 중 하나라도 메시지에 포함되면 적용
    if not trigger_condition:
        return (
            is_intent_matched(rule, intent)
            or contains_any_keyword(user_message, filters)
        )

    # 항상 적용되는 룰
    if trigger_condition in ["all", "always"]:
        return True

    # intent만 보고 적용
    if trigger_condition == "intent":
        return is_intent_matched(rule, intent)

    # filters 중 하나라도 포함되면 적용
    if trigger_condition == "contains_any":
        return contains_any_keyword(user_message, filters)

    # filters가 전부 포함되어야 적용
    # 예: ["에어컨", "냉장고"]
    if trigger_condition == "contains_all":
        return contains_all_keywords(user_message, filters)

    # intent가 같고, filters 중 하나라도 포함되면 적용
    if trigger_condition == "intent_and_contains_any":
        return (
            is_intent_matched(rule, intent)
            and contains_any_keyword(user_message, filters)
        )

    # intent가 같고, filters가 전부 포함되어야 적용
    if trigger_condition == "intent_and_contains_all":
        return (
            is_intent_matched(rule, intent)
            and contains_all_keywords(user_message, filters)
        )

    return False


def filter_rules_by_intent(
    rules: list[dict],
    intent: str | None,
    user_message: str,
) -> list[dict]:
    """
    회사별 rules 중에서 현재 사용자 메시지에 적용되는 rule만 반환한다.

    지원하는 trigger_condition:

    - always 또는 all
      항상 적용

    - intent
      intent가 rule_type과 같으면 적용

    - contains_any
      filters 중 하나라도 사용자 메시지에 포함되면 적용

    - contains_all
      filters 전체가 사용자 메시지에 포함되어야 적용

    - intent_and_contains_any
      intent도 같고, filters 중 하나라도 포함되면 적용

    - intent_and_contains_all
      intent도 같고, filters 전체가 포함되어야 적용
    """
    matched_rules = []

    for rule in rules:
        if not rule.get("is_active", True):
            continue

        if is_rule_matched(
            rule=rule,
            intent=intent,
            user_message=user_message,
        ):
            matched_rules.append(rule)

    matched_rules.sort(
        key=lambda rule: rule.get("priority") or 0,
        reverse=True,
    )

    return matched_rules


def get_block_rules(rules: list[dict]) -> list[dict]:
    """
    매칭된 rule 중 action_type이 block인 rule만 반환한다.
    """
    return [
        rule
        for rule in rules
        if normalize_text(rule.get("action_type")) == "block"
    ]


def get_warn_rules(rules: list[dict]) -> list[dict]:
    """
    매칭된 rule 중 action_type이 warn인 rule만 반환한다.
    """
    return [
        rule
        for rule in rules
        if normalize_text(rule.get("action_type")) == "warn"
    ]


def get_handoff_rules(rules: list[dict]) -> list[dict]:
    """
    매칭된 rule 중 action_type이 handoff인 rule만 반환한다.
    """
    return [
        rule
        for rule in rules
        if normalize_text(rule.get("action_type")) == "handoff"
    ]
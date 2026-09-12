"""Customer-facing acknowledgements of verified state changes, not business execution."""

LABELS = {
    "service.apply": "售后申请诉求",
    "service.progress": "工单查询诉求",
    "product.howto": "操作指导诉求",
    "product.troubleshoot": "故障排查诉求",
    "service.rules": "规则咨询诉求",
    "service.handoff": "人工服务诉求",
}


def control_acknowledgement(intent_state, turn_id, fulfilled):
    delta = intent_state["turns"][turn_id]["delta"]
    updates = delta.get("updates", [])
    if (
        not updates
        or delta.get("ambiguities")
        or delta.get("visual_request", "none") != "none"
        or any(u["operation"] not in {"suspend", "withdraw"} for u in updates)
    ):
        return None
    messages = []
    for update in updates:
        goal = intent_state["goals"][update["goal_id"]]
        expected = "suspended" if update["operation"] == "suspend" else "withdrawn"
        if goal["status"] != expected:
            return None
        label = LABELS.get(goal["intent_id"], "这项诉求")
        verb = "暂停" if expected == "suspended" else "撤回"
        messages.append(f"已{verb}{label}，不会继续按这项诉求办理。")
    if any(u["goal_id"] in fulfilled for u in updates):
        messages.append("此前已提交的工单仍保留，本次没有修改工单状态。")
    return "".join(dict.fromkeys(messages))


def insufficient_information_message(state):
    if not state.get("product_id"):
        return "请先选择要咨询的商品和型号，以便核对适用资料。"
    active = [
        g["intent_id"]
        for g in (state.get("intent_state") or {}).get("goals", {}).values()
        if g["status"] == "active" and g["expression"] == "current"
    ]
    if "service.rules" in active:
        return "现有资料不足以确认这项售后规则，暂时无法给出可靠结论。"
    return "现有资料不足以支持可靠答复，暂时不能给出具体操作建议。"

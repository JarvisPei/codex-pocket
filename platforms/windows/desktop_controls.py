"""Read-only interpretation of sanitized UIA reports, never click authorization.

Indices are valid only within one snapshot. Native actions must separately verify
task identity, drafts, freshness, focus/session state and the live target element.
"""

LABELS = {
    "send": {"Send", "Send message", "Submit", "发送", "发送消息", "提交", "傳送", "傳送訊息", "發送"},
    "stop": {"Stop", "Stop generating", "Stop response", "停止", "停止生成", "停止响应", "停止產生"},
    "resume": {"Resume", "Continue", "继续", "恢复", "繼續"},
    "voice": {"Start voice", "Start voice chat", "Start new voice chat", "开始语音", "开始新的语音聊天"},
    "dictation": {"Dictate", "Dictation", "Stop dictation", "Transcribe and send", "听写", "停止听写", "转录并发送"},
    "queue": {"Queue", "加入队列"},
    "steer": {"Steer", "调整方向"},
}


def button_semantic(control):
    """Exact provider hints only. Contradictory hints fail closed."""
    if not isinstance(control, dict) or any(
            control.get(k) is not None and not isinstance(control[k], str)
            for k in ("label", "nameHint", "helpHint")):
        return None
    hints = {control.get(k) for k in ("label", "nameHint", "helpHint") if control.get(k)}
    roles = {role for role, labels in LABELS.items() if hints & labels}
    if len(roles) != 1 or any(not any(h in values for values in LABELS.values()) for h in hints):
        return None
    return roles.pop()


def inspect_composer(report):
    result = {"readOnly": True, "nativeActionsEnabled": False, "status": "invalid_report"}
    if not isinstance(report, dict) or report.get("schemaVersion") != 4 or report.get("readOnly") is not True:
        return result
    if (report.get("truncated") is not False or report.get("errors") != 0
            or report.get("status") != "candidate_controls_found"):
        return {**result, "status": "incomplete_or_unavailable"}
    controls = report.get("controls")
    if not isinstance(controls, list) or not 0 < len(controls) <= 1200:
        return result
    nodes = {}
    for node in controls:
        if not isinstance(node, dict):
            return result
        if (not isinstance(node.get("patterns", []), list)
                or any(not isinstance(p, str) for p in node.get("patterns", []))):
            return result
        index, parent = node.get("index"), node.get("parent")
        if (type(index) is not int or type(parent) is not int or index < 0
                or index in nodes or parent >= index or (parent != -1 and parent not in nodes)):
            return result
        nodes[index] = node

    def visible(node):
        # Hidden/disabled ancestors also invalidate a candidate.
        while node:
            if node.get("offscreen") is not False or node.get("enabled") is not True:
                return False
            node = nodes.get(node["parent"])
        return True

    edits = [node for node in controls if node.get("type") == "ControlType.Edit"
             and node.get("inDocument") is True and node.get("frameworkHint") == "Chrome"
             and node.get("valueReadOnly") is False and node.get("keyboardFocusable") is True
             and "ValuePatternIdentifiers.Pattern" in node.get("patterns", []) and visible(node)]
    if len(edits) != 1:
        return {**result, "status": "ambiguous_editor" if edits else "no_writable_editor"}
    editor = edits[0]
    container = nodes.get(editor["parent"])
    if not container or container.get("type") != "ControlType.Group" or container.get("inDocument") is not True:
        return {**result, "status": "unverified_container"}

    def near_editor(node):
        # Only the editor's immediate group and three descendant edges. Never
        # widen to the document/window to find an unrelated Send or Stop button.
        for _ in range(3):
            if node["parent"] == container["index"]:
                return True
            node = nodes.get(node["parent"])
            if node is None:
                break
        return False

    buttons = []
    for node in controls:
        if (node.get("type") == "ControlType.Button" and node.get("inDocument") is True
                and node.get("frameworkHint") == "Chrome" and visible(node) and near_editor(node)
                and "InvokePatternIdentifiers.Pattern" in node.get("patterns", [])):
            role = button_semantic(node)
            if role:
                buttons.append({"index": node["index"], "role": role})
    primary = [b for b in buttons if b["role"] in {"send", "stop", "resume", "queue", "steer"}]
    if len(primary) > 1:
        status = "ambiguous_actions"
    elif primary:
        status = "action_candidate_found"
    elif any(b["role"] == "voice" for b in buttons):
        status = "voice_button_not_send"
    else:
        status = "no_action_candidate"
    return {**result, "status": status, "editorIndex": editor["index"],
            "containerIndex": container["index"], "buttons": buttons}

import copy
import unittest

from windows_desktop_controls import button_semantic, inspect_composer


def report(label="发送"):
    base = dict(inDocument=True, frameworkHint="Chrome", enabled=True, offscreen=False)
    return dict(schemaVersion=4, readOnly=True, truncated=False, errors=0,
                status="candidate_controls_found", controls=[
                    dict(base, index=0, parent=-1, type="ControlType.Document"),
                    dict(base, index=1, parent=0, type="ControlType.Group"),
                    dict(base, index=2, parent=1, type="ControlType.Edit", valueReadOnly=False,
                         keyboardFocusable=True, patterns=["ValuePatternIdentifiers.Pattern"]),
                    dict(base, index=3, parent=1, type="ControlType.Group"),
                    dict(base, index=4, parent=3, type="ControlType.Button", nameHint=label,
                         patterns=["InvokePatternIdentifiers.Pattern"]),
                ])


class ComposerInspectionTest(unittest.TestCase):
    def test_exact_localized_send_is_candidate_not_permission(self):
        for label in ("发送", "傳送", "Send", "Send message", "Submit"):
            with self.subTest(label=label):
                result = inspect_composer(report(label))
                self.assertEqual(result["status"], "action_candidate_found")
                self.assertEqual(result["buttons"], [{"index": 4, "role": "send"}])
                self.assertFalse(result["nativeActionsEnabled"])
                self.assertTrue(result["readOnly"])

    def test_voice_is_not_send(self):
        for label in ("开始新的语音聊天", "Start new voice chat"):
            self.assertEqual(inspect_composer(report(label))["status"], "voice_button_not_send")

    def test_dictation_stop_or_submit_is_not_task_action(self):
        for label in ("Stop dictation", "停止听写", "Transcribe and send", "转录并发送"):
            result = inspect_composer(report(label))
            self.assertEqual(result["status"], "no_action_candidate")
            self.assertEqual(result["buttons"][0]["role"], "dictation")

    def test_stop_resume_queue_and_steer_are_distinct(self):
        for label, role in (("停止", "stop"), ("继续", "resume"), ("加入队列", "queue"), ("调整方向", "steer")):
            self.assertEqual(inspect_composer(report(label))["buttons"][0]["role"], role)

    def test_unknown_and_conflicting_labels_fail_closed(self):
        for label in ("Send\n", "Stop all", "send my private task", "", None):
            self.assertIsNone(button_semantic({"nameHint": label}))
        self.assertIsNone(button_semantic({"nameHint": "Start voice", "helpHint": "Send"}))
        self.assertIsNone(button_semantic({"nameHint": []}))
        self.assertIsNone(button_semantic(None))
        self.assertEqual(button_semantic({"nameHint": "发送", "helpHint": "Send"}), "send")

    def test_incomplete_and_old_snapshots_are_not_usable(self):
        for change in ({"truncated": True}, {"errors": 1}, {"schemaVersion": 3}, {"readOnly": False},
                       {"status": "no_accessible_window"}, {"controls": []}):
            result = inspect_composer({**report(), **change})
            self.assertNotIn("buttons", result)
            self.assertFalse(result["nativeActionsEnabled"])

    def test_requires_unique_writable_visible_editor(self):
        for change in ({"valueReadOnly": True}, {"offscreen": True}, {"enabled": False},
                       {"keyboardFocusable": False}, {"patterns": []}, {"frameworkHint": "other"}):
            data = report()
            data["controls"][2].update(change)
            self.assertEqual(inspect_composer(data)["status"], "no_writable_editor")
        data = report()
        data["controls"].append({**data["controls"][2], "index": 5})
        self.assertEqual(inspect_composer(data)["status"], "ambiguous_editor")

    def test_hidden_or_disabled_parent_is_not_usable(self):
        for change in ({"offscreen": True}, {"enabled": False}):
            data = report()
            data["controls"][1].update(change)
            self.assertEqual(inspect_composer(data)["status"], "no_writable_editor")

    def test_does_not_search_outside_composer(self):
        data = report()
        data["controls"][3]["parent"] = 0
        self.assertEqual(inspect_composer(data)["status"], "no_action_candidate")
        data = report()
        data["controls"][1]["type"] = "ControlType.Document"
        self.assertEqual(inspect_composer(data)["status"], "unverified_container")

    def test_disabled_or_non_invokable_button_is_not_candidate(self):
        for change in ({"offscreen": True}, {"enabled": False}, {"patterns": []}, {"inDocument": False}):
            data = report()
            data["controls"][4].update(change)
            self.assertEqual(inspect_composer(data)["status"], "no_action_candidate")

    def test_multiple_primary_buttons_are_ambiguous(self):
        data = report()
        data["controls"].append({**data["controls"][4], "index": 5, "nameHint": "停止"})
        self.assertEqual(inspect_composer(data)["status"], "ambiguous_actions")

    def test_malformed_tree_rejected(self):
        for change in ({"index": 0}, {"parent": 4}, {"parent": 999}, {"parent": -2}, {"index": True}):
            data = report()
            data["controls"][4].update(change)
            self.assertEqual(inspect_composer(data)["status"], "invalid_report")
        for value in (None, [], "private"):
            self.assertEqual(inspect_composer(value)["status"], "invalid_report")
        data = report()
        data["controls"][2]["patterns"] = None
        self.assertEqual(inspect_composer(data)["status"], "invalid_report")

    def test_input_is_not_mutated_or_returned(self):
        data = report()
        data["controls"][2]["privateUnrecognizedField"] = "private draft"
        original = copy.deepcopy(data)
        result = inspect_composer(data)
        self.assertEqual(data, original)
        self.assertNotIn("private draft", str(result))


if __name__ == "__main__":
    unittest.main()

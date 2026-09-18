import re
import unittest
from pathlib import Path


SOURCE = (
    Path(__file__).resolve().parents[1] / "scripts" / "codex-ax.swift"
).read_text(encoding="utf-8")


def string_set(named: str) -> set[str]:
    match = re.search(
        rf"let {re.escape(named)}: Set<String> = \[(.*?)\]",
        SOURCE,
        re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"missing Swift string set: {named}")
    return set(re.findall(r'"([^"]+)"', match.group(1)))


class AccessibilityLocalizationTest(unittest.TestCase):
    def test_composer_actions_cover_bundled_chinese_labels(self):
        self.assertGreaterEqual(
            string_set("composerSendTerms"),
            {"send", "send message", "发送", "发送消息", "傳送", "傳送訊息"},
        )
        self.assertGreaterEqual(
            string_set("composerStopTerms"),
            {"stop", "停止"},
        )

    def test_action_scanners_use_the_localized_exact_allowlists(self):
        self.assertIn(
            "exactSemanticMatch(hit, terms: composerSendTerms)",
            SOURCE,
        )
        self.assertIn(
            "exactSemanticMatch(hit, terms: composerStopTerms)",
            SOURCE,
        )
        self.assertIn("composerStopTerms.contains(", SOURCE)

    def test_resume_is_separate_from_send_in_each_supported_language(self):
        resume = string_set("composerResumeTerms")
        self.assertGreaterEqual(
            resume, {"resume", "continue", "继续", "恢复", "繼續", "恢復"}
        )
        self.assertFalse(resume & string_set("composerSendTerms"))
        self.assertIn("exactSemanticMatch(hit, terms: composerResumeTerms)", SOURCE)

    def test_continue_has_single_guarded_native_invocation_and_own_receipt(self):
        start = SOURCE.index("    if payload.continueOnly {")
        end = SOURCE.index("    if !payload.continueOnly {", start)
        resume = SOURCE[start:end]
        for guard in (
            "titles.count == 1, titles[0] == expectedTitle",
            "candidates.textAreas.count == 1",
            "composerIsEmpty(candidates.textAreas[0])",
            "candidates.stopButtons.isEmpty",
            "candidates.sendButtons.isEmpty",
            "candidates.resumeButtons.count == 1",
            "kAXEnabledAttribute as CFString) as? Bool) == true",
        ):
            self.assertIn(guard, resume)
        self.assertEqual(resume.count("AXUIElementPerformAction("), 1)
        self.assertNotIn("clickElementCenter", resume)
        self.assertNotIn("postUnicodeText", resume)
        self.assertNotIn("kAXValueAttribute", resume)
        self.assertLess(resume.index("AXUIElementPerformAction("), resume.index("for _ in 0..<60"))
        self.assertIn("latest.resumeButtons.isEmpty", resume)
        self.assertIn("latest.stopButtons.count == 1", resume)
        self.assertIn("if running || finished", resume)
        self.assertIn('"mode": "continue"', resume)
        self.assertIn("not retrying.", resume)


if __name__ == "__main__":
    unittest.main()

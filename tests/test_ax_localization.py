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


if __name__ == "__main__":
    unittest.main()

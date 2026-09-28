"""Compile pure Swift geometry fixtures. Never load AX or control Desktop."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SOURCE = (Path(__file__).resolve().parents[1] / "scripts/codex-ax.swift").read_text()
RULES = SOURCE.split("// BEGIN PURE HEADER RULES", 1)[1].split("\n", 1)[1].split(
    "// END PURE HEADER RULES", 1
)[0]


class HeaderSafetySourceTest(unittest.TestCase):
    def test_tree_probe_is_bounded_and_read_only(self):
        probe = SOURCE.split("func taskTitlesFromHeaderTree", 1)[1].split("func currentTaskTitles", 1)[0]
        for guard in ("depth <= 32", "visitedElements.count < 2_500", "band.intersects(frame)",
                      "return truncated ? nil : titles"):
            self.assertIn(guard, probe)
        self.assertNotIn("AXUIElementPerformAction", probe)
        self.assertNotIn("AXUIElementSetAttributeValue", probe)

    def test_tree_requires_semantic_header_but_traverses_web_wrappers(self):
        probe = SOURCE.split("func taskTitlesFromHeaderTree", 1)[1].split("func currentTaskTitles", 1)[0]
        self.assertIn('role != "AXOutline"', probe)
        self.assertNotIn('role != "AXWebArea"', probe)
        self.assertNotIn('role != "AXScrollArea"', probe)
        self.assertIn('"AXHidden" as CFString', probe)
        self.assertIn('isHeader || isTaskHeaderContainer(', probe)
        self.assertIn('let values = isHeader ? taskHeaderTexts(', probe)

    def test_hit_testing_filters_element_bounds_and_ignores_help_identifiers(self):
        probe = SOURCE.split("func currentTaskTitles", 1)[1].split("func visibleSidebarTaskButtons", 1)[0]
        self.assertIn('frame: CGRect(origin: hitPosition, size: hitSize)', probe)
        self.assertNotIn('normalizedFields(hit)', probe)
        self.assertIn('if titles.isEmpty {', probe)

    def test_ambiguous_titles_are_not_silently_selected(self):
        probe = SOURCE.split("func currentTaskTitles", 1)[1].split("func visibleSidebarTaskButtons", 1)[0]
        self.assertIn("guard windows.count == 1 else { return [] }", probe)
        self.assertIn("guard let treeTitles", probe)
        self.assertIn("titles.append(title)", probe)
        self.assertNotIn("titles.first", probe)
        self.assertIn("titles.count == 1, titles[0] == expectedTitle", SOURCE)


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("swiftc"), "requires macOS Swift")
class HeaderGeometryFixtureTest(unittest.TestCase):
    def test_native_and_legacy_header_labels_without_ui_access(self):
        harness = r'''
import Foundation
import CoreGraphics
'''+ RULES + r'''
let window = CGRect(x: 100, y: 100, width: 1415, height: 923)
let label = CGRect(x: 470, y: 118, width: 100, height: 18)
func texts(_ frame: CGRect, role: String = "AXStaticText", value: String? = "继续项目开发",
           title: String? = nil, inWindow: CGRect = window) -> [String] {
    taskHeaderTexts(role: role, frame: frame, window: inWindow, value: value, title: title)
}
precondition(texts(label) == ["继续项目开发"])
precondition(texts(label, value: nil, title: "继续项目开发") == ["继续项目开发"])
precondition(texts(label, title: "继续项目开发") == ["继续项目开发"])
precondition(texts(label, title: "different task").count == 2)
precondition(texts(label, role: "AXGroup").isEmpty)
precondition(texts(label, role: "AXButton").isEmpty)
precondition(texts(label, value: " \n").isEmpty)
precondition(texts(label, value: String(repeating: "a", count: 1001)).isEmpty)
// Sidebar duplicate and message body must never become identity evidence.
precondition(texts(CGRect(x: 180, y: 320, width: 180, height: 20)).isEmpty)
precondition(texts(CGRect(x: 640, y: 200, width: 180, height: 20)).isEmpty)
precondition(texts(CGRect(x: 470, y: 95, width: 100, height: 30)).isEmpty)
precondition(texts(CGRect(x: 470, y: 140, width: 100, height: 30)).isEmpty)
precondition(texts(CGRect(x: 470, y: 118, width: 0, height: 18)).isEmpty)
// Coordinates are window-relative, including a monitor left of the primary.
let shifted = CGRect(x: -1200, y: 0, width: 1415, height: 923)
precondition(texts(CGRect(x: -830, y: 18, width: 100, height: 18), inWindow: shifted)
             == ["继续项目开发"])
// Native and unified web headers both qualify, but never full-page wrappers,
// a sidebar group, or a banner embedded in the conversation.
let topBar = CGRect(x: 100, y: 100, width: 1415, height: 44)
func header(_ role: String, _ subrole: String = "", _ frame: CGRect = topBar) -> Bool {
    isTaskHeaderContainer(role: role, subrole: subrole, frame: frame, window: window)
}
precondition(header("AXToolbar"))
precondition(header("AXGroup", "AXLandmarkBanner"))
precondition(!header("AXGroup"))
precondition(!header("AXWebArea"))
precondition(!header("AXScrollArea"))
precondition(!header("AXGroup", "AXLandmarkBanner", window))
precondition(!header("AXGroup", "AXLandmarkBanner", CGRect(x: 470, y: 200, width: 700, height: 40)))
precondition(!header("AXGroup", "AXLandmarkBanner", CGRect(x: 100, y: 100, width: 1415, height: 0)))
precondition(!header("AXGroup", "AXLandmarkBanner", CGRect(x: 50, y: 100, width: 1415, height: 44)))
precondition(!header("AXGroup", "AXLandmarkNavigation"))
print("24 header fixtures passed; no Accessibility APIs loaded")
'''
        with tempfile.TemporaryDirectory(prefix="pocket-header-fixtures-") as folder:
            source = Path(folder) / "main.swift"
            binary = Path(folder) / "header-fixtures"
            source.write_text(harness)
            command = [shutil.which("swiftc")]
            sdk = Path("/Library/Developer/CommandLineTools/SDKs/MacOSX15.4.sdk")
            if sdk.exists():
                command += ["-sdk", str(sdk)]
            result = subprocess.run(command + [str(source), "-o", str(binary)],
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("24 header fixtures passed", result.stdout)

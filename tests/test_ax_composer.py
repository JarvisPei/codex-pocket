"""Replay synthetic composer trees against the actual Swift scan, without UI access."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SOURCE = (Path(__file__).resolve().parents[1] / "scripts/codex-ax.swift").read_text()
RULES = SOURCE.split("// BEGIN PURE COMPOSER SCAN", 1)[1].split("\n", 1)[1].split(
    "// END PURE COMPOSER SCAN", 1
)[0]
READINESS = SOURCE.split("// BEGIN PURE COMPOSER READINESS", 1)[1].split("\n", 1)[1].split(
    "// END PURE COMPOSER READINESS", 1
)[0]
ATTACHMENTS = SOURCE.split("// BEGIN PURE ATTACHMENT SCAN", 1)[1].split("\n", 1)[1].split(
    "// END PURE ATTACHMENT SCAN", 1
)[0]
EVIDENCE = "struct ComposerAttachmentEvidence" + SOURCE.split("struct ComposerAttachmentEvidence", 1)[1].split(
    "func composerAttachmentEvidence", 1
)[0]
MATCH_EVIDENCE = "func attachmentEvidenceMatches" + SOURCE.split("func attachmentEvidenceMatches", 1)[1].split(
    "func pasteAttachments", 1
)[0]


class ComposerScanSafetyTest(unittest.TestCase):
    def test_attachment_scan_is_bounded_and_incomplete_is_not_empty(self):
        body = SOURCE.split("func composerAttachmentEvidence(", 1)[1].split("func clickPoint", 1)[0]
        self.assertIn("guard let nodes = attachmentNodes(", body)
        self.assertNotIn("AXUIElementCopyElementAtPosition", body)
        self.assertNotIn("x += 12", body)
        self.assertIn("guard existingEvidence.complete else { return false }", body)
        self.assertIn("evidence.complete && !expectedNames.isEmpty", body)
        self.assertIn("if evidence.complete && evidence.names.isEmpty", SOURCE)
        self.assertNotIn("visibleComposerAttachmentNames", SOURCE)

    def test_production_uses_fixture_scanner_and_exact_action_labels(self):
        body = SOURCE.split("func composerCandidates()", 1)[1].split("func composerIsEmpty", 1)[0]
        self.assertIn("let scan = scanComposerTree(", body)
        self.assertNotIn("AXUIElementCopyElementAtPosition", body)
        self.assertNotIn("AXUIElementPerformAction", body)
        for term in ("composerSendTerms", "composerResumeTerms", "composerStopTerms"):
            self.assertIn(f"pressable && exactSemanticMatch(hit, terms: {term})", body)
        self.assertIn('found \\(lastCount).', SOURCE)
        self.assertIn("titles.count == 1, titles[0] == expectedTitle", SOURCE)

    def test_stop_uses_the_same_scanner_as_desktop_status(self):
        stop = SOURCE.split("func scanBottomOfWindows", 1)[1].split("let windows = activeWindows()", 1)[0]
        self.assertIn("composerCandidates().stopButtons", stop)
        self.assertIn("guard stopCandidates.count == 1", stop)
        self.assertIn("kAXPressAction as CFString", stop)

    def test_preflight_waits_without_writing_or_replacing_uuid_navigation(self):
        wait = SOURCE.split("func waitForDesktopComposer", 1)[1].split("func performDesktopSend", 1)[0]
        self.assertIn("timeout: TimeInterval = 3.0", wait)
        self.assertIn("titlesBefore == [expectedTitle] && titlesAfter == [expectedTitle]", wait)
        for forbidden in ("AXUIElementPerformAction", "AXUIElementSetAttributeValue", "postKey(",
                          "postUnicodeText", "NSWorkspace.shared.open", "clickElementCenter"):
            self.assertNotIn(forbidden, wait)
        send = SOURCE.split("func performDesktopSend()", 1)[1].split("func walk(", 1)[0]
        self.assertLess(send.index("NSWorkspace.shared.open(url)"), send.index("waitForDesktopComposer("))
        self.assertLess(send.index("waitForDesktopComposer("), send.index("let existingText = composerText(textArea)"))


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("swiftc"), "requires macOS Swift")
class ComposerTreeFixtureTest(unittest.TestCase):
    def test_attachment_tree_regions_and_scan_budgets(self):
        harness = "import Foundation\nimport CoreGraphics\n" + RULES + ATTACHMENTS + EVIDENCE + MATCH_EVIDENCE + r'''
let window = CGRect(x: 0, y: 0, width: 1600, height: 1000)
let region = CGRect(x: 400, y: 600, width: 700, height: 340)
struct Node {
    var data: ComposerScanInfo
    var children: [Int] = []
}
var reads = 0
func scan(_ nodes: [Node]) -> [Int]? {
    reads = 0
    return attachmentNodes(0, window: window, region: region, identity: { UInt($0) },
        info: { reads += 1; return nodes[$0].data }, childNodes: { nodes[$0].children })
}
var nodes = [
    Node(data: ComposerScanInfo(role: "AXWindow", frame: window), children: [1]),
    Node(data: ComposerScanInfo(role: "AXWebArea", frame: window), children: [2]),
    Node(data: ComposerScanInfo(role: "AXGroup", frame: nil), children: [3, 4, 5, 6]),
    Node(data: ComposerScanInfo(role: "AXImage", frame: CGRect(x: 420, y: 640, width: 200, height: 200))),
    Node(data: ComposerScanInfo(role: "AXButton", frame: CGRect(x: 600, y: 640, width: 20, height: 20))),
    Node(data: ComposerScanInfo(role: "AXTextArea", frame: CGRect(x: 400, y: 900, width: 700, height: 40)), children: [7]),
    Node(data: ComposerScanInfo(role: "AXImage", frame: CGRect(x: 400, y: 450, width: 200, height: 200))),
    Node(data: ComposerScanInfo(role: "AXStaticText", frame: CGRect(x: 410, y: 905, width: 100, height: 20))),
]
precondition(scan(nodes) == [3, 4]) // Partly intersecting history image and editor text excluded.
precondition(reads == 7) // Once per visited node, independent of pixel area.
nodes[2].children += [2, 3, 4]
precondition(scan(nodes) == [3, 4] && reads == 7) // Cycles/shared nodes deduplicated.
nodes[3].data.hidden = true
precondition(scan(nodes) == [4])
nodes[2].data.hidden = true
precondition(scan(nodes) == [])
nodes[2].data.hidden = false
nodes[2].data = ComposerScanInfo(role: "AXOutline", frame: nil)
precondition(scan(nodes) == [])
nodes[2].data = ComposerScanInfo(role: "AXGroup", frame: nil)
nodes[1].data = ComposerScanInfo(role: "AXWebArea", frame: CGRect(x: 0, y: 0, width: 1600, height: 600))
precondition(scan(nodes) == []) // Viewport clips stale/off-screen elements.
nodes[1].data = ComposerScanInfo(role: "AXWebArea", frame: window)
nodes[3].data.hidden = false
var huge = nodes
huge[2].children += Array(8..<1600)
while huge.count < 1600 { huge.append(Node(data: ComposerScanInfo(role: "AXGroup", frame: nil))) }
precondition(scan(huge) == nil && reads <= 1500) // Not partial evidence, nor empty evidence.
var deep = nodes
deep[2].children.append(8)
for i in 8..<55 { deep.append(Node(data: ComposerScanInfo(role: "AXGroup", frame: nil), children: i < 54 ? [i + 1] : [])) }
precondition(scan(deep) == nil)
nodes[2].children.append(8)
nodes.append(Node(data: ComposerScanInfo(role: "AXGroup", frame: CGRect(x: 420, y: 640, width: 210, height: 210)), children: [3, 4]))
precondition(scan(nodes) == [3, 4, 8]) // Group filename retained; children deduplicated.
var evidence = ComposerAttachmentEvidence()
evidence.names = ["photo.jpg"]
precondition(!attachmentEvidenceMatches(evidence, expectedNames: ["photo.jpg"]))
evidence.complete = true
precondition(attachmentEvidenceMatches(evidence, expectedNames: ["photo.jpg"]))
evidence.names = []
precondition(!attachmentEvidenceMatches(evidence, expectedNames: ["photo.jpg"]))
evidence.previewImages = 1
precondition(attachmentEvidenceMatches(evidence, expectedNames: ["photo.jpg"]))
precondition(!attachmentEvidenceMatches(evidence, expectedNames: ["photo.jpg", "second.jpg"]))
print("15 attachment fixtures passed; no Accessibility APIs loaded")
'''
        self.run_fixture(harness, "15 attachment fixtures passed")

    def test_same_thread_remount_and_safe_readiness_transitions(self):
        harness = "import Foundation\n" + READINESS + r'''
var gate = ComposerReadinessGate()
func sample(_ editors: [UInt], matches: Bool = true, changed: Bool = false,
            stops: Int = 0, enabled: Bool = true) -> ComposerReadinessDecision {
    gate.observe(titleMatches: matches, differentTask: changed, editors: editors,
                 stopCount: stops, editorEnabled: enabled)
}
precondition(sample([]) == .waiting) // Existing title does not mean editor is mounted.
precondition(sample([]) == .waiting)
precondition(sample([1]) == .waiting)
precondition(sample([1]) == .ready)
gate = ComposerReadinessGate()
precondition(sample([1]) == .waiting)
precondition(sample([2]) == .waiting) // Remount must settle on the same editor.
precondition(sample([2]) == .ready)
precondition(sample([2], matches: false) == .waiting)
precondition(sample([2]) == .waiting) // Lost identity resets stability.
precondition(sample([2]) == .ready)
precondition(sample([1, 2]) == .ambiguous)
precondition(sample([2], stops: 1) == .busy)
precondition(sample([2], changed: true) == .taskChanged)
precondition(sample([2], enabled: false) == .waiting)
precondition(sample([2]) == .waiting)
precondition(sample([2]) == .ready)
precondition(sample([]) == .waiting)
precondition(sample([2]) == .waiting)
print("18 readiness fixtures passed; no Accessibility APIs loaded")
'''
        self.run_fixture(harness, "18 readiness fixtures passed")

    def test_nested_centered_hidden_ambiguous_and_bounded_trees(self):
        harness = "import Foundation\nimport CoreGraphics\n" + RULES + r'''
let window = CGRect(x: 0, y: 0, width: 2400, height: 1000)
struct Node {
    var data: ComposerScanInfo
    var children: [Int] = []
}
func editor(_ x: CGFloat = 800, _ y: CGFloat = 920) -> Node {
    Node(data: ComposerScanInfo(role: "AXTextArea", frame: CGRect(x: x, y: y, width: 500, height: 40)))
}
func result(_ nodes: [Node]) -> ComposerTreeScan<Int> {
    scanComposerTree(0, window: window, identity: { UInt($0) },
                     info: { nodes[$0].data }, childNodes: { nodes[$0].children })
}
var nodes = [
    Node(data: ComposerScanInfo(role: "AXWindow", frame: window), children: [1]),
    Node(data: ComposerScanInfo(role: "AXScrollArea", frame: window), children: [2]),
    Node(data: ComposerScanInfo(role: "AXWebArea", frame: window), children: [3]),
    Node(data: ComposerScanInfo(role: "AXGroup", frame: nil), children: [4, 6]),
    editor(),
    Node(data: ComposerScanInfo(role: "AXStaticText", frame: CGRect(x: 805, y: 925, width: 100, height: 20))),
    Node(data: ComposerScanInfo(role: "AXButton", frame: CGRect(x: 1320, y: 920, width: 40, height: 40), send: true)),
]
nodes[4].children = [5]
precondition(result(nodes).textAreas == [4]) // Centered editor outside old rightmost 560px.
precondition(result(nodes).sendButtons == [6]) // Button need not be near window edge either.
nodes[4].children = [4, 5] // Editor contents/cycles never create another input.
precondition(result(nodes).textAreas == [4])
nodes.append(editor(1400))
nodes[3].children.append(7)
precondition(result(nodes).textAreas.count == 2) // Never silently select the first editor.
nodes[7].data.hidden = true
precondition(result(nodes).textAreas == [4])
nodes[7].data.hidden = false
nodes[7].data = editor(1400, 300).data
precondition(result(nodes).textAreas == [4]) // An editor in the upper conversation is excluded.
nodes[7].data = editor(1400).data
nodes[3].data.hidden = true
precondition(result(nodes).textAreas.isEmpty) // Hidden parent hides the whole subtree.
nodes[3].data.hidden = false
nodes[7].data = ComposerScanInfo(role: "AXTextField", frame: nodes[7].data.frame)
precondition(result(nodes).textAreas == [4]) // Search/text fields never promoted to composer.
nodes[3].children = [4, 6]
nodes[2].data = ComposerScanInfo(role: "AXWebArea", frame: CGRect(x: 0, y: 0, width: 2400, height: 900))
precondition(result(nodes).textAreas.isEmpty) // Text outside the scroll viewport is excluded.
nodes[2].data = ComposerScanInfo(role: "AXWebArea", frame: window)
nodes[6].data.send = false
nodes[6].data.stop = true
precondition(result(nodes).sendButtons.isEmpty && result(nodes).stopButtons == [6])
nodes[6].data.stop = false
nodes[6].data.resume = true
precondition(result(nodes).resumeButtons == [6] && result(nodes).stopButtons.isEmpty)
nodes[3].children += [3, 4, 6]
precondition(result(nodes).textAreas == [4]) // Cycles/shared nodes are visited once.
nodes[3].data = ComposerScanInfo(role: "AXOutline", frame: nil)
precondition(result(nodes).textAreas.isEmpty) // Sidebar outlines are not composer sources.
nodes[3].data = ComposerScanInfo(role: "AXGroup", frame: nil)
nodes[4].data = ComposerScanInfo(role: "AXTextArea", frame: CGRect(x: 800, y: 920, width: 0, height: 40))
precondition(result(nodes).textAreas.isEmpty) // Invisible input does not count.
nodes[4].data = editor().data
var oversized = nodes
oversized[3].children = [4, 6] + Array(7..<2510)
while oversized.count < 2510 { oversized.append(Node(data: ComposerScanInfo(role: "AXGroup", frame: nil))) }
precondition(result(oversized).textAreas.isEmpty && result(oversized).resumeButtons.isEmpty)
var deep = Array(nodes.prefix(7))
deep[3].children = [4, 6, 7]
for i in 7..<55 { deep.append(Node(data: ComposerScanInfo(role: "AXGroup", frame: nil), children: i < 54 ? [i + 1] : [])) }
precondition(result(deep).textAreas.isEmpty) // Incomplete scan cannot establish uniqueness.
print("16 composer fixtures passed; no Accessibility APIs loaded")
'''
        self.run_fixture(harness, "16 composer fixtures passed")

    def run_fixture(self, harness, expected):
        with tempfile.TemporaryDirectory(prefix="pocket-composer-fixtures-") as folder:
            source = Path(folder) / "main.swift"
            binary = Path(folder) / "composer-fixtures"
            source.write_text(harness)
            command = [shutil.which("swiftc")]
            sdk = Path("/Library/Developer/CommandLineTools/SDKs/MacOSX15.4.sdk")
            if sdk.exists():
                command += ["-sdk", str(sdk)]
            compiled = subprocess.run(command + [str(source), "-o", str(binary)],
                                      capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            executed = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(executed.returncode, 0, executed.stderr)
            self.assertIn(expected, executed.stdout)

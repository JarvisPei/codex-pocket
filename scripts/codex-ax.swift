#!/usr/bin/env swift

import AppKit
import ApplicationServices
import CryptoKit
import Foundation

struct Options {
    let maxDepth: Int
    let showAllInteractive: Bool
    let hitGrid: Bool
    let performStop: Bool
    let checkStop: Bool
    let inspectHeader: Bool
    let currentTask: Bool
    let activate: Bool
    let desktopSend: Bool
    let desktopState: Bool
    let desktopRequestRespond: Bool
    let requestAccessibility: Bool
    let expectedTaskTitle: String?

    init(arguments: [String]) {
        maxDepth = arguments
            .first(where: { $0.hasPrefix("--max-depth=") })
            .flatMap { Int($0.split(separator: "=", maxSplits: 1)[1]) } ?? 18
        showAllInteractive = arguments.contains("--all-interactive")
        hitGrid = arguments.contains("--hit-grid")
        performStop = arguments.contains("--stop")
        checkStop = arguments.contains("--check-stop")
        inspectHeader = arguments.contains("--inspect-header")
        currentTask = arguments.contains("--current-task")
        activate = arguments.contains("--activate")
        desktopSend = arguments.contains("--desktop-send")
        desktopState = arguments.contains("--desktop-state")
        desktopRequestRespond = arguments.contains("--desktop-request-respond")
        requestAccessibility = arguments.contains("--request-accessibility")
        expectedTaskTitle = arguments
            .first(where: { $0.hasPrefix("--expected-task-title=") })
            .map { String($0.dropFirst("--expected-task-title=".count)) }
    }
}

struct DesktopSendPayload: Decodable {
    let threadId: String
    let expectedTaskTitle: String
    let message: String
    let continueOnly: Bool
    let attachmentPaths: [String]?
}

struct DesktopRequestResponsePayload: Decodable {
    let expectedTaskTitle: String
    let fingerprint: String
    let action: String
    let answer: String?
    let optionLabel: String?
}

struct DesktopRequestCandidate {
    let kind: String
    let prompt: String
    let fingerprint: String
    let actions: [(id: String, label: String)]
    let actionElements: [String: AXUIElement]
    let options: [(label: String, element: AXUIElement)]
    let textAreas: [AXUIElement]
}

struct ComposerCandidates {
    var textAreas: [AXUIElement] = []
    var sendButtons: [AXUIElement] = []
    var resumeButtons: [AXUIElement] = []
    var stopButtons: [AXUIElement] = []
}

func attribute(_ element: AXUIElement, _ name: CFString) -> AnyObject? {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, name, &value) == .success else {
        return nil
    }
    return value
}

func stringAttribute(_ element: AXUIElement, _ name: CFString) -> String? {
    attribute(element, name) as? String
}

func actions(_ element: AXUIElement) -> [String] {
    var names: CFArray?
    guard AXUIElementCopyActionNames(element, &names) == .success else {
        return []
    }
    return names as? [String] ?? []
}

func children(_ element: AXUIElement) -> [AXUIElement] {
    attribute(element, kAXChildrenAttribute as CFString) as? [AXUIElement] ?? []
}

func normalizedFields(_ element: AXUIElement) -> [String] {
    [
        stringAttribute(element, kAXRoleAttribute as CFString),
        stringAttribute(element, kAXSubroleAttribute as CFString),
        stringAttribute(element, kAXTitleAttribute as CFString),
        stringAttribute(element, kAXDescriptionAttribute as CFString),
        stringAttribute(element, kAXIdentifierAttribute as CFString),
        stringAttribute(element, kAXHelpAttribute as CFString),
        stringAttribute(element, kAXValueAttribute as CFString),
    ].compactMap { $0 }
}

let options = Options(arguments: Array(CommandLine.arguments.dropFirst()))
if options.requestAccessibility {
    let promptOptions = ["AXTrustedCheckOptionPrompt": true] as CFDictionary
    let trusted = AXIsProcessTrustedWithOptions(promptOptions)
    print(trusted ? "Accessibility permission is enabled." : "Accessibility permission is required.")
    exit(trusted ? 0 : 2)
}
guard AXIsProcessTrusted() else {
    fputs(
        "Accessibility permission is not granted to this process. Enable it in System Settings > Privacy & Security > Accessibility.\n",
        stderr
    )
    exit(2)
}

guard let app = NSWorkspace.shared.runningApplications.first(where: {
    $0.localizedName == "ChatGPT"
}) else {
    fputs("ChatGPT is not running.\n", stderr)
    exit(3)
}

let root = AXUIElementCreateApplication(app.processIdentifier)
// Electron exposes its Chromium web-content accessibility tree lazily. Codex
// Desktop can therefore appear as only an opaque AXScrollArea until an
// assistive client explicitly enables accessibility. New Chromium builds can
// react to either the legacy manual switch or the enhanced-UI switch, so set
// both without weakening any of the semantic checks used for actions below.
@discardableResult
func enableElectronAccessibility() -> Bool {
    let manualResult = AXUIElementSetAttributeValue(
        root,
        "AXManualAccessibility" as CFString,
        kCFBooleanTrue
    )
    let enhancedResult = AXUIElementSetAttributeValue(
        root,
        "AXEnhancedUserInterface" as CFString,
        kCFBooleanTrue
    )
    return manualResult == .success || enhancedResult == .success
}
// Composer actions are intentionally matched against an exact allowlist. Codex
// localizes the button accessibility label with its UI language, so relying on
// only the English labels makes Desktop dispatch fail on Chinese installations.
// Keep this list narrow: these values are allowed to trigger a real click.
let composerSendTerms: Set<String> = [
    "send", "send message",
    "发送", "发送消息",
    "傳送", "傳送訊息",
]
let composerStopTerms: Set<String> = [
    "stop", "停止",
]
let composerResumeTerms: Set<String> = [
    "resume", "continue", "resume task", "continue task",
    "继续", "恢复", "继续任务", "恢复任务",
    "繼續", "恢復", "繼續任務", "恢復任務",
]

// Broader terms are only used by the diagnostic tree walker and never authorize
// a click.
let stopDiagnosticTerms = [
    "stop", "cancel", "interrupt", "abort",
    "停止", "中止", "取消", "打断",
]
var visited = Set<CFHashCode>()
var matches = 0

func pointAttribute(_ element: AXUIElement, _ name: CFString) -> CGPoint? {
    guard let value = attribute(element, name) else { return nil }
    var point = CGPoint.zero
    guard AXValueGetValue(value as! AXValue, .cgPoint, &point) else { return nil }
    return point
}

func sizeAttribute(_ element: AXUIElement, _ name: CFString) -> CGSize? {
    guard let value = attribute(element, name) else { return nil }
    var size = CGSize.zero
    guard AXValueGetValue(value as! AXValue, .cgSize, &size) else { return nil }
    return size
}

func activeWindows() -> [AXUIElement] {
    if let focused = attribute(root, kAXFocusedWindowAttribute as CFString) {
        return [focused as! AXUIElement]
    }
    return attribute(root, kAXWindowsAttribute as CFString) as? [AXUIElement] ?? []
}

func electronAccessibilityTreeIsReady() -> Bool {
    var visitedElements = Set<CFHashCode>()

    func hasPublishedWebDescendant(_ element: AXUIElement, depth: Int) -> Bool {
        guard depth <= 10 else { return false }
        let hash = CFHash(element)
        guard visitedElements.insert(hash).inserted else { return false }

        let role = stringAttribute(element, kAXRoleAttribute as CFString) ?? ""
        if role != kAXWindowRole as String,
           role != kAXScrollAreaRole as String,
           actions(element).contains("AXScrollToVisible")
        {
            return true
        }
        for child in children(element) {
            if hasPublishedWebDescendant(child, depth: depth + 1) {
                return true
            }
        }
        return false
    }

    return activeWindows().contains(where: {
        hasPublishedWebDescendant($0, depth: 0)
    })
}

@discardableResult
func prepareElectronAccessibilityTree(timeout: TimeInterval = 5.0) -> Bool {
    _ = enableElectronAccessibility()
    let deadline = Date().addingTimeInterval(timeout)
    while true {
        if electronAccessibilityTreeIsReady() {
            return true
        }
        guard Date() < deadline else { return false }
        Thread.sleep(forTimeInterval: 0.1)
    }
}

func scanBottomOfWindows(performStop: Bool, checkStop: Bool) {
    if performStop || checkStop {
        // Status, Send and Stop must agree on the same centered composer scan.
        let stopCandidates = composerCandidates().stopButtons
        if checkStop {
            print("{\"stopCandidates\":\(stopCandidates.count)}")
            return
        }
        guard stopCandidates.count == 1 else {
            fputs("Refusing to interrupt: expected exactly one semantic Stop button, found \(stopCandidates.count).\n", stderr)
            exit(4)
        }
        let result = AXUIElementPerformAction(stopCandidates[0], kAXPressAction as CFString)
        guard result == .success else {
            fputs("Failed to press the semantic Stop button: AX error \(result.rawValue).\n", stderr)
            exit(5)
        }
        print("Pressed the semantic Stop button in the active ChatGPT/Codex window.")
        return
    }
    let windows = activeWindows()
    var hitElements = Set<CFHashCode>()

    for (windowIndex, window) in windows.enumerated() {
        guard
            let position = pointAttribute(window, kAXPositionAttribute as CFString),
            let size = sizeAttribute(window, kAXSizeAttribute as CFString)
        else { continue }

        let startX = position.x + max(0, size.width - 520)
        let endX = position.x + size.width
        let startY = position.y + max(0, size.height - 190)
        let endY = position.y + size.height
        var y = startY
        while y <= endY {
            var x = startX
            while x <= endX {
                var hit: AXUIElement?
                if AXUIElementCopyElementAtPosition(root, Float(x), Float(y), &hit) == .success,
                   let hit
                {
                    let hash = CFHash(hit)
                    if hitElements.insert(hash).inserted {
                        let fields = normalizedFields(hit)
                        let actionNames = actions(hit)
                        let role =
                            stringAttribute(hit, kAXRoleAttribute as CFString) ?? "unknown"
                        if role == kAXButtonRole as String ||
                            actionNames.contains(kAXPressAction as String)
                        {
                            if !performStop && !checkStop {
                                print(
                                    """
                                    HIT
                                      window: \(windowIndex)
                                      point: \(Int(x)),\(Int(y))
                                      fields: \(fields)
                                      actions: \(actionNames)
                                    """
                                )
                            }
                        }
                    }
                }
                x += 12
            }
            y += 12
        }
    }

}

func inspectWindowHeaders() {
    let windows =
        attribute(root, kAXWindowsAttribute as CFString) as? [AXUIElement] ?? []
    var hitElements = Set<CFHashCode>()

    for (windowIndex, window) in windows.enumerated() {
        guard
            let position = pointAttribute(window, kAXPositionAttribute as CFString),
            let size = sizeAttribute(window, kAXSizeAttribute as CFString)
        else { continue }

        let startX = position.x + min(220, size.width * 0.15)
        let endX = position.x + min(900, size.width * 0.7)
        let startY = position.y
        let endY = position.y + min(150, size.height * 0.2)
        var y = startY
        while y <= endY {
            var x = startX
            while x <= endX {
                var hit: AXUIElement?
                if AXUIElementCopyElementAtPosition(root, Float(x), Float(y), &hit) == .success,
                   let hit
                {
                    let hash = CFHash(hit)
                    if hitElements.insert(hash).inserted {
                        let fields = normalizedFields(hit)
                        if !fields.isEmpty {
                            print(
                                """
                                HEADER_HIT
                                  window: \(windowIndex)
                                  point: \(Int(x)),\(Int(y))
                                  fields: \(fields)
                                  actions: \(actions(hit))
                                """
                            )
                        }
                    }
                }
                x += 10
            }
            y += 10
        }
    }
}

// BEGIN PURE HEADER RULES -- also compiled by the fixture tests, without AX access.
func taskHeaderBand(window: CGRect) -> CGRect {
    let left = min(250.0, window.width * 0.18)
    let right = min(950.0, window.width * 0.72)
    return CGRect(x: window.minX + left, y: window.minY + 8,
                  width: max(0, right - left), height: min(44, window.height * 0.06))
}

func isTaskHeaderContainer(role: String, subrole: String, frame: CGRect,
                           window: CGRect) -> Bool {
    // The unified shell renders an HTML <header>, exposed as a banner landmark,
    // rather than necessarily creating a native macOS toolbar. Require a narrow
    // top-level header frame; a banner inside a conversation is not sufficient.
    let semanticHeader = role == "AXToolbar" ||
        (role == "AXGroup" && subrole == "AXLandmarkBanner")
    return semanticHeader && frame.minX.isFinite && frame.minY.isFinite &&
        frame.width.isFinite && frame.height.isFinite &&
        frame.width > 0 && frame.height > 0 &&
        frame.minY >= window.minY && frame.maxY <= window.minY + 56 &&
        frame.minX >= window.minX && frame.maxX <= window.maxX
}

func taskHeaderTexts(role: String, frame: CGRect, window: CGRect,
                     value: String?, title: String?) -> [String] {
    // Labels only: never infer identity from a generic group's description,
    // sidebar selection, window name, or a button that happens to have text.
    guard role == "AXStaticText", frame.width > 0, frame.height > 0,
          frame.minY.isFinite, frame.minX.isFinite,
          frame.width.isFinite, frame.height.isFinite,
          taskHeaderBand(window: window).contains(frame) else { return [] }
    var result: [String] = []
    for raw in [value, title].compactMap({ $0 }) {
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if !text.isEmpty && text.count <= 1_000 && !result.contains(text) {
            result.append(text)
        }
    }
    return result
}
// END PURE HEADER RULES

func taskTitlesFromHeaderTree(_ window: AXUIElement) -> [String]? {
    guard let position = pointAttribute(window, kAXPositionAttribute as CFString),
          let size = sizeAttribute(window, kAXSizeAttribute as CFString)
    else { return nil }
    let windowFrame = CGRect(origin: position, size: size)
    let band = taskHeaderBand(window: windowFrame)
    var visitedElements = Set<CFHashCode>()
    var titles: [String] = []
    var truncated = false

    func scanHeader(_ element: AXUIElement, depth: Int, insideHeader: Bool) {
        let hash = CFHash(element)
        guard !visitedElements.contains(hash) else { return }
        guard depth <= 32, visitedElements.count < 2_500 else {
            truncated = true
            return
        }
        visitedElements.insert(hash)
        let role = stringAttribute(element, kAXRoleAttribute as CFString) ?? ""
        guard role != "AXOutline",
              (attribute(element, "AXHidden" as CFString) as? Bool) != true
        else { return }
        // WebArea / ScrollArea can wrap the entire Electron surface, including
        // the top banner. Traverse these containers, but never use their own
        // text as identity evidence or accept arbitrary text elsewhere in them.
        var isHeader = insideHeader
        if let point = pointAttribute(element, kAXPositionAttribute as CFString),
           let size = sizeAttribute(element, kAXSizeAttribute as CFString),
           size.width > 0, size.height > 0 {
            let frame = CGRect(origin: point, size: size)
            // Keep only subtrees that overlap the visible top-title region.
            guard band.intersects(frame) else { return }
            isHeader = isHeader || isTaskHeaderContainer(
                role: role,
                subrole: stringAttribute(element, kAXSubroleAttribute as CFString) ?? "",
                frame: frame, window: windowFrame
            )
            let values = isHeader ? taskHeaderTexts(
                role: role,
                frame: frame, window: windowFrame,
                value: stringAttribute(element, kAXValueAttribute as CFString),
                title: stringAttribute(element, kAXTitleAttribute as CFString)
            ) : []
            for value in values where !titles.contains(value) { titles.append(value) }
        }
        for child in children(element) {
            scanHeader(child, depth: depth + 1, insideHeader: isHeader)
        }
    }
    scanHeader(window, depth: 0, insideHeader: false)
    // An incomplete scan cannot prove that a title is unique.
    return truncated ? nil : titles
}

func currentTaskTitles() -> [String] {
    let windows = activeWindows()
    guard windows.count == 1 else { return [] }
    var hitElements = Set<CFHashCode>()
    var titles: [String] = []

    for window in windows {
        guard
            let position = pointAttribute(window, kAXPositionAttribute as CFString),
            let size = sizeAttribute(window, kAXSizeAttribute as CFString)
        else { continue }

        let startX = position.x + min(250, size.width * 0.18)
        let endX = position.x + min(950, size.width * 0.72)
        let startY = position.y + 14
        let endY = position.y + min(40, size.height * 0.05)
        var y = startY
        while y <= endY {
            var x = startX
            while x <= endX {
                var hit: AXUIElement?
                if AXUIElementCopyElementAtPosition(root, Float(x), Float(y), &hit) == .success,
                   let hit
                {
                    let hash = CFHash(hit)
                    if hitElements.insert(hash).inserted,
                       stringAttribute(hit, kAXRoleAttribute as CFString) ==
                        kAXStaticTextRole as String
                    {
                        if let hitPosition = pointAttribute(hit, kAXPositionAttribute as CFString),
                           let hitSize = sizeAttribute(hit, kAXSizeAttribute as CFString) {
                            let values = taskHeaderTexts(
                                role: "AXStaticText",
                                frame: CGRect(origin: hitPosition, size: hitSize),
                                window: CGRect(origin: position, size: size),
                                value: stringAttribute(hit, kAXValueAttribute as CFString),
                                title: stringAttribute(hit, kAXTitleAttribute as CFString)
                            )
                            for value in values where !titles.contains(value) { titles.append(value) }
                        }
                    }
                }
                x += 8
            }
            y += 8
        }
    }
    // New native headers may return a containing AXGroup from hit testing.
    // Use the tree only when the legacy scan found no text. Never replace an
    // ambiguous legacy result with a convenient single tree match.
    if titles.isEmpty {
        guard let treeTitles = taskTitlesFromHeaderTree(windows[0]) else { return [] }
        for title in treeTitles where !titles.contains(title) { titles.append(title) }
    }
    return titles
}

func visibleSidebarTaskButtons(titled expectedTitle: String) -> [AXUIElement] {
    var visitedElements = Set<CFHashCode>()
    var matches: [AXUIElement] = []

    for window in activeWindows() {
        guard
            let windowPosition = pointAttribute(window, kAXPositionAttribute as CFString),
            let windowSize = sizeAttribute(window, kAXSizeAttribute as CFString)
        else { continue }
        let sidebarRight = windowPosition.x + min(390, windowSize.width * 0.32)
        let windowBottom = windowPosition.y + windowSize.height

        func scan(_ element: AXUIElement, depth: Int) {
            guard depth <= 32 else { return }
            let hash = CFHash(element)
            guard visitedElements.insert(hash).inserted else { return }

            let role = stringAttribute(element, kAXRoleAttribute as CFString) ?? ""
            let title = stringAttribute(element, kAXTitleAttribute as CFString)?
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if role == kAXButtonRole as String,
               title == expectedTitle,
               actions(element).contains(kAXPressAction as String),
               (attribute(element, kAXEnabledAttribute as CFString) as? Bool) != false,
               let position = pointAttribute(element, kAXPositionAttribute as CFString),
               let size = sizeAttribute(element, kAXSizeAttribute as CFString),
               size.width > 0,
               size.height > 0,
               position.x >= windowPosition.x,
               position.x < sidebarRight,
               position.y >= windowPosition.y + 45,
               position.y < windowBottom
            {
                matches.append(element)
            }

            for child in children(element) {
                scan(child, depth: depth + 1)
            }
        }

        scan(window, depth: 0)
    }
    return matches
}

func navigateToVisibleSidebarTask(titled expectedTitle: String) -> Bool {
    let candidates = visibleSidebarTaskButtons(titled: expectedTitle)
    guard candidates.count == 1 else { return false }
    let candidate = candidates[0]
    _ = AXUIElementPerformAction(candidate, "AXScrollToVisible" as CFString)
    return AXUIElementPerformAction(candidate, kAXPressAction as CFString) == .success
}

func exactSemanticMatch(_ element: AXUIElement, terms: Set<String>) -> Bool {
    normalizedFields(element).contains(where: {
        terms.contains(
            $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        )
    })
}

// BEGIN PURE COMPOSER SCAN -- fixture tests inject nodes, never Accessibility APIs.
struct ComposerScanInfo {
    let role: String
    let frame: CGRect?
    var hidden = false
    var send = false
    var resume = false
    var stop = false
}

struct ComposerTreeScan<Node> {
    var textAreas: [Node] = []
    var sendButtons: [Node] = []
    var resumeButtons: [Node] = []
    var stopButtons: [Node] = []
}

func scanComposerTree<Node>(
    _ rootNode: Node, window: CGRect,
    identity: (Node) -> UInt,
    info: (Node) -> ComposerScanInfo,
    childNodes: (Node) -> [Node]
) -> ComposerTreeScan<Node> {
    // Centered/narrow composers need not intersect the old rightmost 560 px.
    let region = CGRect(x: window.minX, y: max(window.minY, window.maxY - 210),
                        width: window.width, height: min(210, window.height))
    var found = ComposerTreeScan<Node>()
    var visitedNodes = Set<UInt>()
    var truncated = false

    func visit(_ node: Node, depth: Int, clip: CGRect) {
        let key = identity(node)
        guard !visitedNodes.contains(key) else { return }
        guard depth <= 40, visitedNodes.count < 2_500 else {
            truncated = true
            return
        }
        visitedNodes.insert(key)
        let item = info(node)
        guard !item.hidden, item.role != "AXOutline",
              depth == 0 || item.role != "AXWindow" else { return }
        var childClip = clip
        if let frame = item.frame, frame.width > 0, frame.height > 0,
           frame.minX.isFinite, frame.minY.isFinite,
           frame.width.isFinite, frame.height.isFinite {
            let visible = frame.intersection(clip)
            guard !visible.isNull, visible.intersects(region) else { return }
            if item.role == "AXScrollArea" || item.role == "AXWebArea" {
                childClip = visible
            }
            if item.role == "AXTextArea" {
                found.textAreas.append(node)
                return // Paragraph/placeholder descendants are not other editors.
            }
            if item.role == "AXButton" {
                if item.send { found.sendButtons.append(node) }
                if item.resume { found.resumeButtons.append(node) }
                if item.stop { found.stopButtons.append(node) }
                return
            }
        }
        for child in childNodes(node) {
            visit(child, depth: depth + 1, clip: childClip)
        }
    }
    visit(rootNode, depth: 0, clip: window)
    // Partial traversal cannot establish uniqueness. Never use its first match.
    return truncated ? ComposerTreeScan<Node>() : found
}
// END PURE COMPOSER SCAN

func composerCandidates() -> ComposerCandidates {
    guard let windowValue = attribute(
        root,
        kAXFocusedWindowAttribute as CFString
    ) else { return ComposerCandidates() }
    let window = windowValue as! AXUIElement
    guard
        let position = pointAttribute(window, kAXPositionAttribute as CFString),
        let size = sizeAttribute(window, kAXSizeAttribute as CFString)
    else { return ComposerCandidates() }

    let scan = scanComposerTree(
        window, window: CGRect(origin: position, size: size),
        identity: { UInt(CFHash($0)) },
        info: { hit in
            let role = stringAttribute(hit, kAXRoleAttribute as CFString) ?? ""
            let point = pointAttribute(hit, kAXPositionAttribute as CFString)
            let size = sizeAttribute(hit, kAXSizeAttribute as CFString)
            let frame: CGRect? = point.flatMap { point in
                size.map { CGRect(origin: point, size: $0) }
            }
            let pressable = role == kAXButtonRole as String &&
                actions(hit).contains(kAXPressAction as String)
            return ComposerScanInfo(
                role: role, frame: frame,
                hidden: (attribute(hit, "AXHidden" as CFString) as? Bool) == true,
                send: pressable && exactSemanticMatch(hit, terms: composerSendTerms),
                resume: pressable && exactSemanticMatch(hit, terms: composerResumeTerms),
                stop: pressable && exactSemanticMatch(hit, terms: composerStopTerms)
            )
        },
        childNodes: children
    )
    return ComposerCandidates(textAreas: scan.textAreas, sendButtons: scan.sendButtons,
                              resumeButtons: scan.resumeButtons, stopButtons: scan.stopButtons)
}

func composerIsEmpty(_ textArea: AXUIElement) -> Bool {
    composerText(textArea).isEmpty
}

func composerText(_ textArea: AXUIElement) -> String {
    let value = (stringAttribute(textArea, kAXValueAttribute as CFString) ?? "")
        .trimmingCharacters(in: .whitespacesAndNewlines)
    let placeholder = (
        stringAttribute(textArea, kAXDescriptionAttribute as CFString) ?? ""
    ).trimmingCharacters(in: .whitespacesAndNewlines)
    return value.isEmpty || (!placeholder.isEmpty && value == placeholder) ? "" : value
}

func postKey(_ keyCode: CGKeyCode, flags: CGEventFlags = []) -> Bool {
    guard
        let down = CGEvent(
            keyboardEventSource: nil,
            virtualKey: keyCode,
            keyDown: true
        ),
        let up = CGEvent(
            keyboardEventSource: nil,
            virtualKey: keyCode,
            keyDown: false
        )
    else { return false }
    down.flags = flags
    up.flags = flags
    down.post(tap: .cghidEventTap)
    up.post(tap: .cghidEventTap)
    return true
}

func executeAppleScript(_ source: String, failureLabel: String) -> Bool {
    guard let script = NSAppleScript(source: source) else { return false }
    var error: NSDictionary?
    _ = script.executeAndReturnError(&error)
    if let error {
        fputs("\(failureLabel): \(error)\n", stderr)
        return false
    }
    return true
}

func appleScriptString(_ value: String) -> String {
    "\"" + value
        .replacingOccurrences(of: "\\", with: "\\\\")
        .replacingOccurrences(of: "\"", with: "\\\"") + "\""
}

func prepareSystemImageClipboard(_ url: URL) -> Bool {
    executeAppleScript(
        "set imageFile to POSIX file \(appleScriptString(url.path))\n"
            + "set the clipboard to (read imageFile as JPEG picture)",
        failureLabel: "System image clipboard failed"
    )
}

func performSystemEventsPaste() -> Bool {
    executeAppleScript(
        "tell application \"ChatGPT\" to activate\n"
            + "delay 0.2\n"
            + "tell application \"System Events\" to keystroke \"v\" using {command down}",
        failureLabel: "System Events paste failed"
    )
}

func postUnicodeText(_ text: String) -> Bool {
    var start = text.startIndex
    while start < text.endIndex {
        let end = text.index(
            start,
            offsetBy: 256,
            limitedBy: text.endIndex
        ) ?? text.endIndex
        let units = Array(text[start..<end].utf16)
        let posted = units.withUnsafeBufferPointer { buffer -> Bool in
            guard
                let down = CGEvent(
                    keyboardEventSource: nil,
                    virtualKey: 0,
                    keyDown: true
                ),
                let up = CGEvent(
                    keyboardEventSource: nil,
                    virtualKey: 0,
                    keyDown: false
                )
            else { return false }
            down.keyboardSetUnicodeString(
                stringLength: buffer.count,
                unicodeString: buffer.baseAddress
            )
            up.keyboardSetUnicodeString(
                stringLength: buffer.count,
                unicodeString: buffer.baseAddress
            )
            down.post(tap: .cghidEventTap)
            up.post(tap: .cghidEventTap)
            return true
        }
        guard posted else { return false }
        start = end
        Thread.sleep(forTimeInterval: 0.01)
    }
    return true
}

func validatedAttachmentURLs(_ paths: [String]) -> [URL]? {
    guard paths.count <= 4, Set(paths).count == paths.count else { return nil }
    let rootURL = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library", isDirectory: true)
        .appendingPathComponent("Application Support", isDirectory: true)
        .appendingPathComponent("MobileCodexBridge", isDirectory: true)
        .appendingPathComponent("uploads", isDirectory: true)
        .resolvingSymlinksInPath()
        .standardizedFileURL
    let rootPrefix = rootURL.path.hasSuffix("/") ? rootURL.path : rootURL.path + "/"
    var urls: [URL] = []
    for path in paths {
        let url = URL(fileURLWithPath: path)
            .resolvingSymlinksInPath()
            .standardizedFileURL
        guard url.path.hasPrefix(rootPrefix) else { return nil }
        guard
            let values = try? url.resourceValues(
                forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey]
            ),
            values.isRegularFile == true,
            values.isSymbolicLink != true,
            let size = values.fileSize,
            size > 0,
            size <= 20 * 1024 * 1024
        else { return nil }
        urls.append(url)
    }
    return urls
}

func clonedPasteboardItems(_ items: [NSPasteboardItem]?) -> [NSPasteboardItem] {
    (items ?? []).map { source in
        let clone = NSPasteboardItem()
        for type in source.types {
            if let data = source.data(forType: type) {
                clone.setData(data, forType: type)
            }
        }
        return clone
    }
}

// BEGIN PURE ATTACHMENT SCAN -- bounded tree traversal, no per-pixel AX queries.
func attachmentNodes<Node>(
    _ rootNode: Node, window: CGRect, region: CGRect,
    identity: (Node) -> UInt, info: (Node) -> ComposerScanInfo,
    childNodes: (Node) -> [Node]
) -> [Node]? {
    guard region.width > 0, region.height > 0 else { return nil }
    var found: [Node] = []
    var visited = Set<UInt>()
    var truncated = false
    func visit(_ node: Node, depth: Int, clip: CGRect) {
        guard !truncated else { return }
        let key = identity(node)
        guard !visited.contains(key) else { return }
        guard depth <= 40, visited.count < 1_500 else {
            truncated = true
            return
        }
        visited.insert(key)
        let item = info(node)
        guard !item.hidden, item.role != "AXOutline",
              depth == 0 || item.role != "AXWindow" else { return }
        var childClip = clip
        if let frame = item.frame, frame.width > 0, frame.height > 0,
           frame.minX.isFinite, frame.minY.isFinite,
           frame.width.isFinite, frame.height.isFinite {
            let visible = frame.intersection(clip)
            guard !visible.isNull, visible.intersects(region) else { return }
            if item.role == "AXScrollArea" || item.role == "AXWebArea" {
                childClip = visible
            }
            if ["AXGroup", "AXButton", "AXImage", "AXStaticText"].contains(item.role) {
                // A partly intersecting history image must not count as a preview.
                if region.contains(frame) { found.append(node) }
                // File chips sometimes expose their filename on the group.
                // Still traverse it for the image/remove control underneath.
                if item.role != "AXGroup" { return }
            }
        }
        // Editor text is not an attachment filename; do not read its paragraphs.
        guard item.role != "AXTextArea" else { return }
        for child in childNodes(node) {
            visit(child, depth: depth + 1, clip: childClip)
            if truncated { return }
        }
    }
    visit(rootNode, depth: 0, clip: window)
    return truncated ? nil : found
}
// END PURE ATTACHMENT SCAN

struct ComposerAttachmentEvidence {
    var complete = false
    var names = Set<String>()
    var removalControls = 0
    var previewImages = 0
    var upperComposerButtons = 0

    var estimatedCount: Int {
        max(max(removalControls, previewImages), upperComposerButtons)
    }
}

func composerAttachmentEvidence(_ expectedNames: Set<String>) -> ComposerAttachmentEvidence {
    guard !expectedNames.isEmpty,
          let windowValue = attribute(root, kAXFocusedWindowAttribute as CFString)
    else { return ComposerAttachmentEvidence() }
    let window = windowValue as! AXUIElement
    guard
        let windowPosition = pointAttribute(window, kAXPositionAttribute as CFString),
        let windowSize = sizeAttribute(window, kAXSizeAttribute as CFString)
    else { return ComposerAttachmentEvidence() }
    let candidates = composerCandidates()
    guard candidates.textAreas.count == 1,
          let textAreaPosition = pointAttribute(
            candidates.textAreas[0],
            kAXPositionAttribute as CFString
          ),
          let textAreaSize = sizeAttribute(
            candidates.textAreas[0],
            kAXSizeAttribute as CFString
          )
    else { return ComposerAttachmentEvidence() }
    let startX = max(windowPosition.x, textAreaPosition.x)
    let endX = min(
        windowPosition.x + windowSize.width,
        min(textAreaPosition.x + textAreaSize.width, startX + 700)
    )
    let startY = max(
        windowPosition.y + max(0, windowSize.height - 650),
        textAreaPosition.y - 320
    )
    let endY = min(
        windowPosition.y + windowSize.height,
        textAreaPosition.y + textAreaSize.height
    )
    let region = CGRect(x: startX, y: startY, width: endX - startX, height: endY - startY)
    guard let nodes = attachmentNodes(
        window, window: CGRect(origin: windowPosition, size: windowSize), region: region,
        identity: { UInt(CFHash($0)) },
        info: { node in
            let position = pointAttribute(node, kAXPositionAttribute as CFString)
            let size = sizeAttribute(node, kAXSizeAttribute as CFString)
            return ComposerScanInfo(
                role: stringAttribute(node, kAXRoleAttribute as CFString) ?? "",
                frame: position.flatMap { p in size.map { CGRect(origin: p, size: $0) } },
                hidden: (attribute(node, "AXHidden" as CFString) as? Bool) == true
            )
        }, childNodes: children
    ) else { return ComposerAttachmentEvidence() }
    var evidence = ComposerAttachmentEvidence(complete: true)
    for hit in nodes {
        let role = stringAttribute(hit, kAXRoleAttribute as CFString) ?? ""
        let position = pointAttribute(hit, kAXPositionAttribute as CFString)
        let size = sizeAttribute(hit, kAXSizeAttribute as CFString)
        for field in normalizedFields(hit) {
            let value = field.trimmingCharacters(in: .whitespacesAndNewlines)
            if expectedNames.contains(value) { evidence.names.insert(value) }
        }
        if role == kAXButtonRole as String,
           actions(hit).contains(kAXPressAction as String) {
            let label = elementSemanticLabel(hit).lowercased()
            let removalTerms = [
                "remove attachment", "remove file", "remove image",
                "delete attachment", "delete file", "delete image",
                "remove", "delete", "close", "移除", "删除",
            ]
            if removalTerms.contains(where: { label == $0 || label.contains($0) }) {
                evidence.removalControls += 1
            } else if label.isEmpty, let position, let size,
                      position.y + size.height / 2 < textAreaPosition.y,
                      size.width <= 80, size.height <= 80 {
                // Only unlabelled small controls qualify: labelled history
                // activity buttons just above the composer are not attachments.
                evidence.upperComposerButtons += 1
            }
        }
        if role == kAXImageRole as String, let size,
           size.width >= 40, size.width <= 320,
           size.height >= 40, size.height <= 320 {
            evidence.previewImages += 1
        }
    }
    return evidence
}

func attachmentEvidenceMatches(
    _ evidence: ComposerAttachmentEvidence,
    expectedNames: Set<String>
) -> Bool {
    evidence.complete && !expectedNames.isEmpty &&
        (evidence.names == expectedNames || evidence.estimatedCount == expectedNames.count)
}

func pasteAttachments(_ urls: [URL], into textArea: AXUIElement) -> Bool {
    guard !urls.isEmpty else { return true }
    let expectedNames = Set(urls.map(\.lastPathComponent))
    let initialTextAreaPosition = pointAttribute(
        textArea,
        kAXPositionAttribute as CFString
    )
    let initialTextAreaSize = sizeAttribute(
        textArea,
        kAXSizeAttribute as CFString
    )
    let existingEvidence = composerAttachmentEvidence(expectedNames)
    // An incomplete scan cannot prove that the composer has no attachments.
    guard existingEvidence.complete else { return false }
    if attachmentEvidenceMatches(existingEvidence, expectedNames: expectedNames) {
        return true
    }
    if !existingEvidence.names.isEmpty || existingEvidence.estimatedCount > 0 {
        return false
    }

    let pasteboard = NSPasteboard.general
    desktopSendStage("attachment-clipboard")
    let backup = clonedPasteboardItems(pasteboard.pasteboardItems)
    pasteboard.clearContents()
    if urls.count == 1, NSImage(contentsOf: urls[0]) != nil {
        guard prepareSystemImageClipboard(urls[0]) else { return false }
    } else {
        guard pasteboard.writeObjects(urls.map { $0 as NSURL }) else { return false }
    }
    let injectedChangeCount = pasteboard.changeCount
    defer {
        if pasteboard.changeCount == injectedChangeCount {
            pasteboard.clearContents()
            if !backup.isEmpty {
                _ = pasteboard.writeObjects(backup)
            }
        }
    }

    desktopSendStage("attachment-paste")
    _ = AXUIElementPerformAction(textArea, kAXPressAction as CFString)
    guard performSystemEventsPaste() else { return false }
    desktopSendStage("attachment-confirmation")
    var missingTextAreaSamples = 0
    var lastEvidence = ComposerAttachmentEvidence()
    for _ in 0..<80 {
        Thread.sleep(forTimeInterval: 0.1)
        lastEvidence = composerAttachmentEvidence(expectedNames)
        if attachmentEvidenceMatches(lastEvidence, expectedNames: expectedNames) {
            return true
        }
        // Recent Codex Desktop builds render image thumbnails as unlabelled
        // Electron nodes, so neither a filename nor an AXImage is exposed.
        // A single inserted preview still moves or resizes the otherwise-empty
        // composer text area. Treat that deterministic layout change as
        // attachment confirmation; multi-file sends still require count/name
        // evidence so that a partial paste cannot be accepted.
        if urls.count == 1 {
            let latest = composerCandidates()
            if latest.textAreas.isEmpty {
                missingTextAreaSamples += 1
                // In current Electron builds an accepted image paste replaces
                // the composer text area before the thumbnail becomes visible
                // to Accessibility. The original text area was present and
                // focused immediately before Cmd-V, so a sustained replacement
                // is a stronger signal than an unlabelled thumbnail hit.
                if missingTextAreaSamples >= 3 {
                    return true
                }
                continue
            }
            missingTextAreaSamples = 0
            if latest.textAreas.count == 1,
               let latestPosition = pointAttribute(
                   latest.textAreas[0],
                   kAXPositionAttribute as CFString
               ),
               let latestSize = sizeAttribute(
                   latest.textAreas[0],
                   kAXSizeAttribute as CFString
               ),
               let initialTextAreaPosition,
               let initialTextAreaSize,
               (
                   abs(latestPosition.y - initialTextAreaPosition.y) >= 8
                   || abs(latestSize.height - initialTextAreaSize.height) >= 8
               )
            {
                return true
            }
        }
    }
    fputs(
        "Attachment evidence unavailable: names=\(lastEvidence.names.count), "
            + "removal=\(lastEvidence.removalControls), "
            + "images=\(lastEvidence.previewImages), "
            + "upperControls=\(lastEvidence.upperComposerButtons), "
            + "missingTextAreaSamples=\(missingTextAreaSamples).\n",
        stderr
    )
    return false
}

func clickPoint(_ point: CGPoint) -> Bool {
    let originalLocation = CGEvent(source: nil)?.location
    guard
        let move = CGEvent(
            mouseEventSource: nil,
            mouseType: .mouseMoved,
            mouseCursorPosition: point,
            mouseButton: .left
        ),
        let down = CGEvent(
            mouseEventSource: nil,
            mouseType: .leftMouseDown,
            mouseCursorPosition: point,
            mouseButton: .left
        ),
        let up = CGEvent(
            mouseEventSource: nil,
            mouseType: .leftMouseUp,
            mouseCursorPosition: point,
            mouseButton: .left
        )
    else { return false }
    move.post(tap: .cghidEventTap)
    Thread.sleep(forTimeInterval: 0.04)
    down.post(tap: .cghidEventTap)
    Thread.sleep(forTimeInterval: 0.04)
    up.post(tap: .cghidEventTap)
    if let originalLocation,
       let restore = CGEvent(
        mouseEventSource: nil,
        mouseType: .mouseMoved,
        mouseCursorPosition: originalLocation,
        mouseButton: .left
       )
    {
        Thread.sleep(forTimeInterval: 0.04)
        restore.post(tap: .cghidEventTap)
    }
    return true
}

func clickElementCenter(_ element: AXUIElement) -> Bool {
    guard
        let position = pointAttribute(element, kAXPositionAttribute as CFString),
        let size = sizeAttribute(element, kAXSizeAttribute as CFString),
        size.width >= 20,
        size.width <= 120,
        size.height >= 20,
        size.height <= 120
    else { return false }
    return clickPoint(CGPoint(
        x: position.x + size.width / 2,
        y: position.y + size.height / 2
    ))
}

func clickComposerTextArea(_ textArea: AXUIElement) -> Bool {
    guard
        let position = pointAttribute(textArea, kAXPositionAttribute as CFString),
        let size = sizeAttribute(textArea, kAXSizeAttribute as CFString),
        size.width >= 100,
        size.height >= 40
    else { return false }
    return clickPoint(CGPoint(
        x: position.x + size.width / 2,
        y: position.y + size.height / 2
    ))
}

func focusedWindowElements(maxDepth: Int = 36) -> [AXUIElement] {
    guard let windowValue = attribute(
        root,
        kAXFocusedWindowAttribute as CFString
    ) else { return [] }
    let window = windowValue as! AXUIElement
    var result: [AXUIElement] = []
    var seen = Set<CFHashCode>()

    func collect(_ element: AXUIElement, depth: Int) {
        guard depth <= maxDepth, result.count < 20_000 else { return }
        let hash = CFHash(element)
        guard seen.insert(hash).inserted else { return }
        result.append(element)
        for child in children(element) {
            collect(child, depth: depth + 1)
        }
    }
    collect(window, depth: 0)
    return result
}

func elementSemanticLabel(_ element: AXUIElement) -> String {
    let candidates = [
        stringAttribute(element, kAXTitleAttribute as CFString),
        stringAttribute(element, kAXDescriptionAttribute as CFString),
        stringAttribute(element, kAXHelpAttribute as CFString),
        stringAttribute(element, kAXValueAttribute as CFString),
    ]
    for candidate in candidates {
        let value = (candidate ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if !value.isEmpty, !value.hasPrefix("AX") {
            return value
        }
    }
    return ""
}

func centerPoint(_ element: AXUIElement) -> CGPoint? {
    guard
        let position = pointAttribute(element, kAXPositionAttribute as CFString),
        let size = sizeAttribute(element, kAXSizeAttribute as CFString)
    else { return nil }
    return CGPoint(x: position.x + size.width / 2, y: position.y + size.height / 2)
}

func sameActionRow(_ left: AXUIElement, _ right: AXUIElement) -> Bool {
    guard let a = centerPoint(left), let b = centerPoint(right) else { return false }
    return abs(a.y - b.y) <= 80 && abs(a.x - b.x) <= 900
}

func nearbyRequestText(
    elements: [AXUIElement],
    actionElements: [AXUIElement],
    excluding excluded: Set<String>
) -> String {
    let actionCenters = actionElements.compactMap(centerPoint)
    guard let actionY = actionCenters.map(\.y).min() else { return "" }
    var rows: [(y: CGFloat, x: CGFloat, value: String)] = []
    for element in elements {
        let role = stringAttribute(element, kAXRoleAttribute as CFString) ?? ""
        guard role == kAXStaticTextRole as String || role == "AXHeading" else { continue }
        guard let center = centerPoint(element) else { continue }
        guard center.y < actionY + 12, center.y >= actionY - 420 else { continue }
        let value = elementSemanticLabel(element)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let lowered = value.lowercased()
        guard
            !value.isEmpty,
            value.count <= 4_000,
            !excluded.contains(lowered),
            !value.hasPrefix("AX")
        else { continue }
        rows.append((center.y, center.x, value))
    }
    rows.sort { left, right in
        if abs(left.y - right.y) > 2 { return left.y < right.y }
        return left.x < right.x
    }
    var unique: [String] = []
    for row in rows.suffix(24) where !unique.contains(row.value) {
        unique.append(row.value)
    }
    return unique.joined(separator: "\n").prefix(8_000).description
}

func requestFingerprint(
    taskTitle: String,
    kind: String,
    prompt: String,
    labels: [String]
) -> String {
    let seed = ([taskTitle, kind, prompt] + labels).joined(separator: "\u{1f}")
    return SHA256.hash(data: Data(seed.utf8)).map { String(format: "%02x", $0) }.joined()
}

func desktopRequestCandidate(taskTitle: String) -> DesktopRequestCandidate? {
    let elements = focusedWindowElements()
    let buttons = elements.filter {
        (stringAttribute($0, kAXRoleAttribute as CFString) ?? "") == kAXButtonRole as String
            && actions($0).contains(kAXPressAction as String)
    }
    let allowTerms: Set<String> = ["allow once", "仅允许一次", "仅本次允许"]
    let denyTerms: Set<String> = ["deny", "拒绝"]
    let allowButtons = buttons.filter { exactSemanticMatch($0, terms: allowTerms) }
    let denyButtons = buttons.filter { exactSemanticMatch($0, terms: denyTerms) }

    if allowButtons.count == 1, denyButtons.count == 1,
       sameActionRow(allowButtons[0], denyButtons[0])
    {
        let allowLabel = elementSemanticLabel(allowButtons[0])
        let denyLabel = elementSemanticLabel(denyButtons[0])
        let excluded = Set(
            ([taskTitle, allowLabel, denyLabel, "Always allow"])
                .map { $0.lowercased() }
        )
        let prompt = nearbyRequestText(
            elements: elements,
            actionElements: [allowButtons[0], denyButtons[0]],
            excluding: excluded
        )
        guard !prompt.isEmpty else { return nil }
        let actionPairs = [
            (id: "approve_once", label: allowLabel.isEmpty ? "Allow once" : allowLabel),
            (id: "deny", label: denyLabel.isEmpty ? "Deny" : denyLabel),
        ]
        return DesktopRequestCandidate(
            kind: "approval",
            prompt: prompt,
            fingerprint: requestFingerprint(
                taskTitle: taskTitle,
                kind: "approval",
                prompt: prompt,
                labels: actionPairs.map(\.label)
            ),
            actions: actionPairs,
            actionElements: [
                "approve_once": allowButtons[0],
                "deny": denyButtons[0],
            ],
            options: [],
            textAreas: []
        )
    }

    let primaryTerms: Set<String> = ["submit", "continue", "提交", "继续"]
    let skipTerms: Set<String> = ["skip", "跳过"]
    let primaryButtons = buttons.filter { exactSemanticMatch($0, terms: primaryTerms) }
    guard primaryButtons.count == 1, let primaryCenter = centerPoint(primaryButtons[0]) else {
        return nil
    }
    let skipButtons = buttons.filter {
        exactSemanticMatch($0, terms: skipTerms) && sameActionRow($0, primaryButtons[0])
    }
    guard skipButtons.count <= 1 else { return nil }
    let radios = elements.filter {
        let role = stringAttribute($0, kAXRoleAttribute as CFString) ?? ""
        guard role == "AXRadioButton", let center = centerPoint($0) else { return false }
        return center.y < primaryCenter.y && center.y >= primaryCenter.y - 420
    }
    let inputAreas = elements.filter {
        let role = stringAttribute($0, kAXRoleAttribute as CFString) ?? ""
        guard role == kAXTextAreaRole as String, let center = centerPoint($0) else { return false }
        return center.y < primaryCenter.y && center.y >= primaryCenter.y - 420
    }
    guard !radios.isEmpty || inputAreas.count == 1 else { return nil }
    let optionPairs = radios.compactMap { element -> (label: String, element: AXUIElement)? in
        let label = elementSemanticLabel(element)
        return label.isEmpty ? nil : (label, element)
    }
    let primaryLabel = elementSemanticLabel(primaryButtons[0])
    let skipLabel = skipButtons.first.map(elementSemanticLabel)
    let actionElements = [primaryButtons[0]] + skipButtons
    let excluded = Set(
        ([taskTitle, primaryLabel] + optionPairs.map(\.label) + [skipLabel ?? ""])
            .map { $0.lowercased() }
    )
    let prompt = nearbyRequestText(
        elements: elements,
        actionElements: actionElements,
        excluding: excluded
    )
    guard !prompt.isEmpty else { return nil }
    var actionsOutput = [(id: "answer", label: primaryLabel.isEmpty ? "Submit" : primaryLabel)]
    var actionsById: [String: AXUIElement] = ["answer": primaryButtons[0]]
    if let skipButton = skipButtons.first {
        actionsOutput.append((id: "skip", label: skipLabel?.isEmpty == false ? skipLabel! : "Skip"))
        actionsById["skip"] = skipButton
    }
    let labels = actionsOutput.map(\.label) + optionPairs.map(\.label)
    return DesktopRequestCandidate(
        kind: "userInput",
        prompt: prompt,
        fingerprint: requestFingerprint(
            taskTitle: taskTitle,
            kind: "userInput",
            prompt: prompt,
            labels: labels
        ),
        actions: actionsOutput,
        actionElements: actionsById,
        options: optionPairs,
        textAreas: inputAreas
    )
}

func requestJSONObject(_ request: DesktopRequestCandidate) -> [String: Any] {
    [
        "kind": request.kind,
        "prompt": request.prompt,
        "fingerprint": request.fingerprint,
        "actions": request.actions.map { ["id": $0.id, "label": $0.label] },
        "options": request.options.map(\.label),
        "allowsFreeform": request.textAreas.count == 1,
    ]
}

func printDesktopState() {
    let titles = currentTaskTitles()
    let stopCount = composerCandidates().stopButtons.count
    let request: Any
    if titles.count == 1, let candidate = desktopRequestCandidate(taskTitle: titles[0]) {
        request = requestJSONObject(candidate)
    } else {
        request = NSNull()
    }
    let output: [String: Any] = [
        "taskTitles": titles,
        "stopCandidates": stopCount,
        "request": request,
    ]
    let data = try! JSONSerialization.data(withJSONObject: output, options: [.sortedKeys])
    print(String(decoding: data, as: UTF8.self))
}

func failDesktopRequest(_ message: String, code: Int32) -> Never {
    fputs("\(message)\n", stderr)
    exit(code)
}

func performDesktopRequestResponse() {
    let input = FileHandle.standardInput.readDataToEndOfFile()
    guard
        input.count <= 16_000,
        let payload = try? JSONDecoder().decode(DesktopRequestResponsePayload.self, from: input)
    else {
        failDesktopRequest("Invalid desktop request response payload.", code: 50)
    }
    let expectedTitle = payload.expectedTaskTitle
        .trimmingCharacters(in: .whitespacesAndNewlines)
    guard !expectedTitle.isEmpty, expectedTitle.count <= 1_000,
          payload.fingerprint.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil
    else {
        failDesktopRequest("Invalid desktop request identity.", code: 50)
    }
    _ = app.activate()
    Thread.sleep(forTimeInterval: 0.15)
    let titles = currentTaskTitles()
    guard titles.count == 1, titles[0] == expectedTitle else {
        failDesktopRequest("Foreground task identity changed.", code: 51)
    }
    guard let candidate = desktopRequestCandidate(taskTitle: expectedTitle) else {
        failDesktopRequest("Desktop request is no longer available.", code: 52)
    }
    guard candidate.fingerprint == payload.fingerprint else {
        failDesktopRequest("Desktop request identity changed.", code: 53)
    }

    if payload.action == "answer" {
        if let optionLabel = payload.optionLabel?.trimmingCharacters(in: .whitespacesAndNewlines),
           !optionLabel.isEmpty
        {
            let optionMatches = candidate.options.filter { $0.label == optionLabel }
            guard optionMatches.count == 1,
                  AXUIElementPerformAction(
                    optionMatches[0].element,
                    kAXPressAction as CFString
                  ) == .success
            else {
                failDesktopRequest("Requested answer option is unavailable.", code: 55)
            }
            Thread.sleep(forTimeInterval: 0.12)
        } else if let answer = payload.answer?.trimmingCharacters(in: .whitespacesAndNewlines),
                  !answer.isEmpty,
                  answer.count <= 8_000,
                  candidate.textAreas.count == 1
        {
            let textArea = candidate.textAreas[0]
            let setResult = AXUIElementSetAttributeValue(
                textArea,
                kAXValueAttribute as CFString,
                answer as CFString
            )
            if setResult != .success {
                _ = AXUIElementSetAttributeValue(
                    textArea,
                    kAXFocusedAttribute as CFString,
                    kCFBooleanTrue
                )
                guard
                    postKey(CGKeyCode(0), flags: .maskCommand),
                    postKey(CGKeyCode(51)),
                    postUnicodeText(answer)
                else {
                    failDesktopRequest("Unable to enter the requested answer.", code: 55)
                }
            }
            Thread.sleep(forTimeInterval: 0.08)
        } else {
            failDesktopRequest("A valid answer is required.", code: 54)
        }

        if let latest = desktopRequestCandidate(taskTitle: expectedTitle) {
            guard latest.fingerprint == payload.fingerprint,
                  let submit = latest.actionElements["answer"]
            else {
                // Some one-choice prompts advance immediately after selection.
                let output: [String: Any] = ["ok": true, "action": "answer"]
                let data = try! JSONSerialization.data(withJSONObject: output, options: [.sortedKeys])
                print(String(decoding: data, as: UTF8.self))
                return
            }
            guard AXUIElementPerformAction(submit, kAXPressAction as CFString) == .success else {
                failDesktopRequest("Unable to submit the requested answer.", code: 56)
            }
        }
    } else {
        guard let actionElement = candidate.actionElements[payload.action] else {
            failDesktopRequest("Requested desktop action is unavailable.", code: 54)
        }
        guard AXUIElementPerformAction(actionElement, kAXPressAction as CFString) == .success else {
            failDesktopRequest("Unable to press the requested desktop action.", code: 56)
        }
    }
    let output: [String: Any] = ["ok": true, "action": payload.action]
    let data = try! JSONSerialization.data(withJSONObject: output, options: [.sortedKeys])
    print(String(decoding: data, as: UTF8.self))
}

func failDesktopSend(_ message: String, code: Int32) -> Never {
    fputs("\(message)\n", stderr)
    exit(code)
}

// BEGIN PURE COMPOSER READINESS -- independent of AX and clocks for replay tests.
enum ComposerReadinessDecision: Equatable {
    case waiting, ready, busy, ambiguous, taskChanged
}

struct ComposerReadinessGate {
    private var previousEditor: UInt?

    mutating func observe(titleMatches: Bool, differentTask: Bool,
                          editors: [UInt], stopCount: Int,
                          editorEnabled: Bool) -> ComposerReadinessDecision {
        if differentTask { previousEditor = nil; return .taskChanged }
        guard titleMatches else { previousEditor = nil; return .waiting }
        if stopCount > 0 { previousEditor = nil; return .busy }
        if editors.count > 1 { previousEditor = nil; return .ambiguous }
        guard editors.count == 1, editorEnabled else {
            previousEditor = nil
            return .waiting
        }
        let editor = editors[0]
        let stable = previousEditor == editor
        previousEditor = editor
        return stable ? .ready : .waiting
    }
}
// END PURE COMPOSER READINESS

func waitForDesktopComposer(expectedTitle: String, timeout: TimeInterval = 3.0) -> ComposerCandidates {
    let deadline = Date().addingTimeInterval(timeout)
    var gate = ComposerReadinessGate()
    var lastCount = 0
    repeat {
        let titlesBefore = currentTaskTitles()
        let candidates = composerCandidates()
        let titlesAfter = currentTaskTitles()
        lastCount = candidates.textAreas.count
        let matches = titlesBefore == [expectedTitle] && titlesAfter == [expectedTitle]
        let changed = [titlesBefore, titlesAfter].contains {
            $0.count == 1 && $0[0] != expectedTitle
        }
        let enabled = candidates.textAreas.count == 1 &&
            (attribute(candidates.textAreas[0], kAXEnabledAttribute as CFString) as? Bool) != false
        switch gate.observe(
            titleMatches: matches, differentTask: changed,
            editors: candidates.textAreas.map { UInt(CFHash($0)) },
            stopCount: candidates.stopButtons.count, editorEnabled: enabled
        ) {
        case .ready:
            return candidates
        case .busy:
            failDesktopSend("The requested Codex task is already running.", code: 27)
        case .ambiguous:
            failDesktopSend("Expected one Codex composer text area, found \(lastCount).", code: 28)
        case .taskChanged:
            failDesktopSend("Codex task identity changed while waiting for its composer.", code: 32)
        case .waiting:
            if Date() < deadline { Thread.sleep(forTimeInterval: 0.1) }
        }
    } while Date() < deadline
    failDesktopSend("Codex composer did not become ready before the deadline; found \(lastCount) text areas. No message was entered.", code: 28)
}

func desktopSendStage(_ stage: String) {
    // Fixed phase names only: never log the message, title, or attachment path.
    fputs("desktop-send stage=\(stage)\n", stderr)
    fflush(stderr)
}

func performDesktopSend() {
    desktopSendStage("preflight")
    let input = FileHandle.standardInput.readDataToEndOfFile()
    guard
        input.count <= 24_000,
        let payload = try? JSONDecoder().decode(DesktopSendPayload.self, from: input)
    else {
        failDesktopSend("Invalid desktop send payload.", code: 20)
    }
    let threadPattern = try! NSRegularExpression(
        pattern: "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    let threadRange = NSRange(payload.threadId.startIndex..., in: payload.threadId)
    guard threadPattern.firstMatch(
        in: payload.threadId,
        range: threadRange
    ) != nil else {
        failDesktopSend("Invalid Codex thread id.", code: 21)
    }
    let expectedTitle = payload.expectedTaskTitle
        .trimmingCharacters(in: .whitespacesAndNewlines)
    let message = payload.message.trimmingCharacters(in: .whitespacesAndNewlines)
    let attachmentPaths = payload.attachmentPaths ?? []
    guard !expectedTitle.isEmpty, expectedTitle.count <= 1_000 else {
        failDesktopSend("Invalid expected task title.", code: 22)
    }
    guard message.count <= 20_000 else {
        failDesktopSend("Desktop message is too long.", code: 23)
    }
    guard payload.continueOnly
        ? (message.isEmpty && attachmentPaths.isEmpty)
        : (!message.isEmpty || !attachmentPaths.isEmpty)
    else {
        failDesktopSend("Desktop message mode does not match its input.", code: 24)
    }
    guard let attachmentURLs = validatedAttachmentURLs(attachmentPaths) else {
        failDesktopSend("Desktop attachment path is outside the Bridge upload store.", code: 41)
    }
    guard let url = URL(string: "codex://threads/\(payload.threadId)"),
          NSWorkspace.shared.open(url)
    else {
        failDesktopSend("Unable to open the requested Codex task.", code: 25)
    }
    _ = app.activate()

    var titleMatched = false
    for _ in 0..<50 {
        Thread.sleep(forTimeInterval: 0.1)
        let titles = currentTaskTitles()
        if titles.count == 1, titles[0] == expectedTitle {
            titleMatched = true
            break
        }
    }
    if !titleMatched, navigateToVisibleSidebarTask(titled: expectedTitle) {
        for _ in 0..<50 {
            Thread.sleep(forTimeInterval: 0.1)
            let titles = currentTaskTitles()
            if titles.count == 1, titles[0] == expectedTitle {
                titleMatched = true
                break
            }
        }
    }
    guard titleMatched else {
        failDesktopSend(
            "Codex task identity did not match after deep-link and unique visible sidebar navigation.",
            code: 26
        )
    }

    // A same-thread deep link can leave the old title visible while the editor
    // is being remounted. Title matching alone is not a composer-ready signal.
    var candidates = waitForDesktopComposer(expectedTitle: expectedTitle)
    var textArea = candidates.textAreas[0]
    let existingText = composerText(textArea)
    let reuseMatchingDraft = !payload.continueOnly && existingText == message
    guard existingText.isEmpty || reuseMatchingDraft else {
        failDesktopSend("The Codex composer already contains a different draft.", code: 29)
    }

    if payload.continueOnly {
        // Resume is a distinct native action, not an empty Send. Refresh all
        // guards immediately before a single semantic invocation; never retry
        // a possibly delivered Resume or treat an already-empty draft as proof.
        candidates = composerCandidates()
        let titles = currentTaskTitles()
        guard titles.count == 1, titles[0] == expectedTitle else {
            failDesktopSend("Codex task identity changed before resuming.", code: 32)
        }
        guard candidates.textAreas.count == 1,
              composerIsEmpty(candidates.textAreas[0]) else {
            failDesktopSend("Codex Resume requires an empty composer.", code: 29)
        }
        guard candidates.stopButtons.isEmpty,
              candidates.sendButtons.isEmpty,
              candidates.resumeButtons.count == 1,
              (attribute(candidates.resumeButtons[0], kAXEnabledAttribute as CFString) as? Bool) == true
        else {
            failDesktopSend("Expected one enabled semantic Resume button.", code: 31)
        }
        let pressResult = AXUIElementPerformAction(
            candidates.resumeButtons[0], kAXPressAction as CFString
        )
        guard pressResult == .success else {
            failDesktopSend("Unable to activate Codex Resume; not retrying.", code: 33)
        }
        for _ in 0..<60 {
            Thread.sleep(forTimeInterval: 0.1)
            let latestTitles = currentTaskTitles()
            guard latestTitles.count == 1, latestTitles[0] == expectedTitle else { continue }
            let latest = composerCandidates()
            let running = latest.stopButtons.count == 1
            let finished = latest.resumeButtons.isEmpty &&
                latest.textAreas.count == 1 && composerIsEmpty(latest.textAreas[0]) &&
                latest.sendButtons.count == 1 &&
                (attribute(latest.sendButtons[0], kAXEnabledAttribute as CFString) as? Bool) == false
            if running || finished {
                let output: [String: Any] = [
                    "ok": true, "taskTitle": expectedTitle,
                    "stopCandidates": latest.stopButtons.count, "mode": "continue",
                ]
                let data = try! JSONSerialization.data(withJSONObject: output, options: [.sortedKeys])
                print(String(decoding: data, as: UTF8.self))
                return
            }
        }
        failDesktopSend("Codex Resume acknowledgement is uncertain; not retrying.", code: 34)
    }

    if !payload.continueOnly {
        _ = AXUIElementPerformAction(textArea, kAXPressAction as CFString)
        let focusResult = AXUIElementSetAttributeValue(
            textArea,
            kAXFocusedAttribute as CFString,
            kCFBooleanTrue
        )
        guard focusResult == .success else {
            failDesktopSend("Unable to focus the Codex composer.", code: 35)
        }
        Thread.sleep(forTimeInterval: 0.1)
        desktopSendStage("attachment-preflight")
        guard pasteAttachments(attachmentURLs, into: textArea) else {
            failDesktopSend("Codex did not confirm the requested attachments.", code: 43)
        }
        desktopSendStage("composer-after-attachment")
        var replacementTextArea: AXUIElement?
        for _ in 0..<150 {
            candidates = composerCandidates()
            if candidates.textAreas.count == 1 {
                replacementTextArea = candidates.textAreas[0]
                break
            }
            Thread.sleep(forTimeInterval: 0.1)
        }
        guard let replacementTextArea else {
            failDesktopSend("Expected one Codex composer text area after attaching files.", code: 28)
        }
        textArea = replacementTextArea
        _ = AXUIElementPerformAction(textArea, kAXPressAction as CFString)
        let refocusResult = AXUIElementSetAttributeValue(
            textArea,
            kAXFocusedAttribute as CFString,
            kCFBooleanTrue
        )
        guard refocusResult == .success else {
            failDesktopSend("Unable to refocus the Codex composer after attaching files.", code: 35)
        }
        Thread.sleep(forTimeInterval: 0.1)
        if reuseMatchingDraft {
            guard
                postKey(CGKeyCode(0), flags: .maskCommand),
                postKey(CGKeyCode(51))
            else {
                failDesktopSend("Unable to replace the matching Codex draft.", code: 36)
            }
            var cleared = false
            for _ in 0..<20 {
                Thread.sleep(forTimeInterval: 0.05)
                if composerIsEmpty(textArea) {
                    cleared = true
                    break
                }
            }
            guard cleared else {
                failDesktopSend("Matching Codex draft did not clear.", code: 36)
            }
        }
        if !message.isEmpty {
            desktopSendStage("text-input")
            var inputConfirmed = false

            func refreshComposerTextArea() -> AXUIElement? {
                let latest = composerCandidates()
                return latest.textAreas.count == 1 ? latest.textAreas[0] : nil
            }

            // Setting AXValue on Electron's rich composer after an attachment
            // can update a stale editor node while React replaces it. The text
            // becomes visible, but reading the old AX node still returns empty;
            // the keyboard fallback would then append a duplicate copy. Use
            // focused keyboard input for attachment turns, and reacquire the
            // current text area while confirming either input path.
            if attachmentURLs.isEmpty {
                let setResult = AXUIElementSetAttributeValue(
                    textArea,
                    kAXValueAttribute as CFString,
                    message as CFString
                )
                if setResult == .success {
                    for _ in 0..<12 {
                        Thread.sleep(forTimeInterval: 0.05)
                        if let latestTextArea = refreshComposerTextArea() {
                            textArea = latestTextArea
                        }
                        if composerText(textArea) == message {
                            inputConfirmed = true
                            break
                        }
                    }
                }
            }

            if !inputConfirmed {
                if let latestTextArea = refreshComposerTextArea() {
                    textArea = latestTextArea
                }
                let textBeforeKeyboardInput = composerText(textArea)
                if textBeforeKeyboardInput == message {
                    inputConfirmed = true
                } else if !textBeforeKeyboardInput.isEmpty {
                    failDesktopSend(
                        "Codex composer contains unexpected text after direct input.",
                        code: 38
                    )
                }
            }
            if !inputConfirmed {
                guard clickComposerTextArea(textArea) else {
                    failDesktopSend("Unable to click the Codex composer text area.", code: 35)
                }
                _ = AXUIElementSetAttributeValue(
                    textArea,
                    kAXFocusedAttribute as CFString,
                    kCFBooleanTrue
                )
                Thread.sleep(forTimeInterval: 0.1)
                guard postUnicodeText(message) else {
                    failDesktopSend("Unable to type into the Codex composer.", code: 37)
                }
                for _ in 0..<30 {
                    Thread.sleep(forTimeInterval: 0.05)
                    if let latestTextArea = refreshComposerTextArea() {
                        textArea = latestTextArea
                    }
                    if composerText(textArea) == message {
                        inputConfirmed = true
                        break
                    }
                }
            }
            guard inputConfirmed else {
                failDesktopSend("Codex did not acknowledge keyboard input.", code: 38)
            }
        }
    }

    desktopSendStage("send-button")
    var sendButton: AXUIElement?
    for _ in 0..<30 {
        candidates = composerCandidates()
        if candidates.sendButtons.count == 1 {
            sendButton = candidates.sendButtons[0]
            break
        }
        Thread.sleep(forTimeInterval: 0.1)
    }
    guard let sendButton else {
        failDesktopSend("Expected one semantic Send button.", code: 31)
    }
    var titleMatchedBeforePress = false
    for _ in 0..<20 {
        let titlesBeforePress = currentTaskTitles()
        if titlesBeforePress.count == 1, titlesBeforePress[0] == expectedTitle {
            titleMatchedBeforePress = true
            break
        }
        // Attaching a preview can briefly rebuild the header subtree along
        // with the composer. Preserve the identity guard, but require a
        // persistent mismatch instead of rejecting a single transient sample.
        Thread.sleep(forTimeInterval: 0.1)
    }
    guard titleMatchedBeforePress else {
        failDesktopSend("Codex task identity changed before sending.", code: 32)
    }
    desktopSendStage("submission")
    if !clickElementCenter(sendButton) {
        let pressResult = AXUIElementPerformAction(sendButton, kAXPressAction as CFString)
        guard pressResult == .success else {
            failDesktopSend(
                "Unable to activate the semantic Send button: AX error \(pressResult.rawValue).",
                code: 33
            )
        }
    }

    func waitForSubmissionAcknowledgement(attempts: Int) -> (Bool, Int) {
        let expectedAttachmentNames = Set(attachmentURLs.map(\.lastPathComponent))
        for _ in 0..<attempts {
            Thread.sleep(forTimeInterval: 0.1)
            let latestTitles = currentTaskTitles()
            if latestTitles.count != 1 || latestTitles[0] != expectedTitle {
                // The Electron header can disappear for a few frames while an
                // attachment submission reflows the task. Keep sampling; the
                // later fallback still requires an exact title match before it
                // can click anything again.
                continue
            }
            let latest = composerCandidates()
            let latestStopCount = latest.stopButtons.count
            if latestStopCount == 1 {
                return (true, latestStopCount)
            }
            if latest.textAreas.count == 1,
               composerIsEmpty(latest.textAreas[0]),
               latest.sendButtons.count == 1,
               (attribute(
                   latest.sendButtons[0],
                   kAXEnabledAttribute as CFString
               ) as? Bool) == false
            {
                // A very short turn can finish before the Stop button is ever
                // sampled. Once the composer is empty and Send is disabled,
                // neither text nor an attachment draft remains. This is also
                // more reliable than scanning the freshly inserted user image,
                // which can briefly sit inside the composer's search region.
                return (true, latestStopCount)
            }
            if !expectedAttachmentNames.isEmpty {
                let evidence = composerAttachmentEvidence(expectedAttachmentNames)
                if evidence.complete && evidence.names.isEmpty && evidence.estimatedCount == 0 {
                    return (true, latestStopCount)
                }
            }
            if expectedAttachmentNames.isEmpty,
               latest.textAreas.count == 1,
               composerIsEmpty(latest.textAreas[0])
            {
                return (true, latestStopCount)
            }
        }
        return (false, 0)
    }

    var (accepted, stopCount) = waitForSubmissionAcknowledgement(attempts: 10)
    if !accepted, !attachmentURLs.isEmpty {
        // Image submission can finish before a Stop sample while the composer
        // and newly inserted user message are still animating. Give the
        // attachment-specific cleared-composer signal time to stabilize before
        // considering a second Send click.
        (accepted, stopCount) = waitForSubmissionAcknowledgement(attempts: 40)
    }
    if !accepted {
        let titlesBeforeClick = currentTaskTitles()
        candidates = composerCandidates()
        let draftStillMatches = payload.continueOnly || (
            candidates.textAreas.count == 1 &&
            composerText(candidates.textAreas[0]) == message &&
            (
                attachmentURLs.isEmpty || attachmentEvidenceMatches(
                    composerAttachmentEvidence(
                        Set(attachmentURLs.map(\.lastPathComponent))
                    ),
                    expectedNames: Set(attachmentURLs.map(\.lastPathComponent))
                )
            )
        )
        guard
            titlesBeforeClick.count == 1,
            titlesBeforeClick[0] == expectedTitle,
            candidates.stopButtons.isEmpty,
            candidates.sendButtons.count == 1,
            draftStillMatches
        else {
            failDesktopSend("Refusing unsafe mouse fallback for Codex Send.", code: 39)
        }
        guard clickElementCenter(candidates.sendButtons[0]) else {
            failDesktopSend("Unable to click the Codex Send button.", code: 40)
        }
        (accepted, stopCount) = waitForSubmissionAcknowledgement(attempts: 40)
    }
    guard accepted else {
        failDesktopSend("Codex did not acknowledge the submitted message.", code: 34)
    }
    let output: [String: Any] = [
        "ok": true,
        "taskTitle": expectedTitle,
        "stopCandidates": stopCount,
        "mode": payload.continueOnly ? "continue" : "message",
    ]
    let data = try! JSONSerialization.data(withJSONObject: output, options: [.sortedKeys])
    print(String(decoding: data, as: UTF8.self))
}

func walk(_ element: AXUIElement, depth: Int, path: String) {
    guard depth <= options.maxDepth else { return }
    let hash = CFHash(element)
    guard visited.insert(hash).inserted else { return }

    let fields = normalizedFields(element)
    let actionNames = actions(element)
    let haystack = (fields + actionNames).joined(separator: " ").lowercased()
    let role = stringAttribute(element, kAXRoleAttribute as CFString) ?? "unknown"
    let isInteractive =
        role == kAXButtonRole as String ||
        role == "AXLink" ||
        actionNames.contains(kAXPressAction as String)
    let isStopCandidate = stopDiagnosticTerms.contains(where: { haystack.contains($0) })

    if isStopCandidate || (options.showAllInteractive && isInteractive) {
        matches += 1
        let enabled = attribute(element, kAXEnabledAttribute as CFString) as? Bool
        print(
            """
            MATCH \(matches)
              path: \(path)
              fields: \(fields)
              actions: \(actionNames)
              enabled: \(enabled.map(String.init) ?? "unknown")
            """
        )
    }

    for (index, child) in children(element).enumerated() {
        walk(child, depth: depth + 1, path: "\(path)/\(role)[\(index)]")
    }
}

_ = prepareElectronAccessibilityTree()

if options.desktopRequestRespond {
    performDesktopRequestResponse()
} else if options.desktopState {
    printDesktopState()
} else if options.desktopSend {
    performDesktopSend()
} else if options.activate {
    _ = app.activate()
    Thread.sleep(forTimeInterval: 0.25)
    print("Activated the ChatGPT/Codex window.")
} else if options.performStop {
    guard let expectedTaskTitle = options.expectedTaskTitle, !expectedTaskTitle.isEmpty else {
        fputs("Refusing to interrupt without --expected-task-title.\n", stderr)
        exit(6)
    }
    _ = app.activate()
    Thread.sleep(forTimeInterval: 0.25)
    let titles = currentTaskTitles()
    guard titles.count == 1, titles[0] == expectedTaskTitle else {
        fputs("Refusing to interrupt because the foreground task identity changed.\n", stderr)
        exit(7)
    }
    scanBottomOfWindows(performStop: true, checkStop: false)
} else if options.currentTask {
    let titles = currentTaskTitles()
    let data = try JSONSerialization.data(
        withJSONObject: ["taskTitles": titles],
        options: [.sortedKeys]
    )
    print(String(decoding: data, as: UTF8.self))
} else if options.inspectHeader {
    inspectWindowHeaders()
} else if options.hitGrid || options.checkStop {
    scanBottomOfWindows(
        performStop: options.performStop,
        checkStop: options.checkStop
    )
} else {
    walk(root, depth: 0, path: "ChatGPT")
    print("Scanned ChatGPT accessibility tree; matches: \(matches)")
}

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
let stopTerms = [
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

func scanBottomOfWindows(performStop: Bool, checkStop: Bool) {
    let windows =
        attribute(root, kAXWindowsAttribute as CFString) as? [AXUIElement] ?? []
    var hitElements = Set<CFHashCode>()
    var stopCandidates: [AXUIElement] = []

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
                            let isExactStop =
                                role == kAXButtonRole as String &&
                                fields.contains(where: {
                                    $0.trimmingCharacters(in: .whitespacesAndNewlines)
                                        .lowercased() == "stop"
                                }) &&
                                actionNames.contains(kAXPressAction as String)
                            if isExactStop {
                                stopCandidates.append(hit)
                            }
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

    if checkStop {
        print("{\"stopCandidates\":\(stopCandidates.count)}")
        return
    }
    guard performStop else { return }
    guard stopCandidates.count == 1 else {
        fputs(
            "Refusing to interrupt: expected exactly one semantic Stop button, found \(stopCandidates.count).\n",
            stderr
        )
        exit(4)
    }
    let result = AXUIElementPerformAction(
        stopCandidates[0],
        kAXPressAction as CFString
    )
    guard result == .success else {
        fputs("Failed to press the semantic Stop button: AX error \(result.rawValue).\n", stderr)
        exit(5)
    }
    print("Pressed the semantic Stop button in the active ChatGPT/Codex window.")
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

func currentTaskTitles() -> [String] {
    let windows =
        attribute(root, kAXWindowsAttribute as CFString) as? [AXUIElement] ?? []
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
                        let values = normalizedFields(hit)
                            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                            .filter { !$0.isEmpty && !$0.hasPrefix("AX") }
                        for value in values where !titles.contains(value) {
                            titles.append(value)
                        }
                    }
                }
                x += 8
            }
            y += 8
        }
    }
    return titles
}

func exactSemanticMatch(_ element: AXUIElement, terms: Set<String>) -> Bool {
    normalizedFields(element).contains(where: {
        terms.contains(
            $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        )
    })
}

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

    var result = ComposerCandidates()
    var hitElements = Set<CFHashCode>()
    let startX = position.x + max(0, size.width - 560)
    let endX = position.x + size.width
    let startY = position.y + max(0, size.height - 210)
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
                    let role = stringAttribute(hit, kAXRoleAttribute as CFString) ?? ""
                    let actionNames = actions(hit)
                    if role == kAXTextAreaRole as String {
                        result.textAreas.append(hit)
                    } else if role == kAXButtonRole as String,
                              actionNames.contains(kAXPressAction as String)
                    {
                        if exactSemanticMatch(hit, terms: ["send"]) {
                            result.sendButtons.append(hit)
                        }
                        if exactSemanticMatch(hit, terms: ["stop"]) {
                            result.stopButtons.append(hit)
                        }
                    }
                }
            }
            x += 10
        }
        y += 10
    }
    return result
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

func clickElementCenter(_ element: AXUIElement) -> Bool {
    guard
        let position = pointAttribute(element, kAXPositionAttribute as CFString),
        let size = sizeAttribute(element, kAXSizeAttribute as CFString),
        size.width >= 20,
        size.width <= 120,
        size.height >= 20,
        size.height <= 120
    else { return false }
    let center = CGPoint(
        x: position.x + size.width / 2,
        y: position.y + size.height / 2
    )
    let originalLocation = CGEvent(source: nil)?.location
    guard
        let move = CGEvent(
            mouseEventSource: nil,
            mouseType: .mouseMoved,
            mouseCursorPosition: center,
            mouseButton: .left
        ),
        let down = CGEvent(
            mouseEventSource: nil,
            mouseType: .leftMouseDown,
            mouseCursorPosition: center,
            mouseButton: .left
        ),
        let up = CGEvent(
            mouseEventSource: nil,
            mouseType: .leftMouseUp,
            mouseCursorPosition: center,
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

func performDesktopSend() {
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
    guard !expectedTitle.isEmpty, expectedTitle.count <= 1_000 else {
        failDesktopSend("Invalid expected task title.", code: 22)
    }
    guard message.count <= 20_000 else {
        failDesktopSend("Desktop message is too long.", code: 23)
    }
    guard payload.continueOnly ? message.isEmpty : !message.isEmpty else {
        failDesktopSend("Desktop message mode does not match its input.", code: 24)
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
    guard titleMatched else {
        failDesktopSend("Codex task identity did not match after navigation.", code: 26)
    }

    var candidates = composerCandidates()
    guard candidates.stopButtons.isEmpty else {
        failDesktopSend("The requested Codex task is already running.", code: 27)
    }
    guard candidates.textAreas.count == 1 else {
        failDesktopSend("Expected one Codex composer text area.", code: 28)
    }
    let textArea = candidates.textAreas[0]
    let existingText = composerText(textArea)
    let reuseMatchingDraft = !payload.continueOnly && existingText == message
    guard existingText.isEmpty || reuseMatchingDraft else {
        failDesktopSend("The Codex composer already contains a different draft.", code: 29)
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
        guard postUnicodeText(message) else {
            failDesktopSend("Unable to type into the Codex composer.", code: 37)
        }
        var inputConfirmed = false
        for _ in 0..<30 {
            Thread.sleep(forTimeInterval: 0.05)
            if composerText(textArea) == message {
                inputConfirmed = true
                break
            }
        }
        guard inputConfirmed else {
            failDesktopSend("Codex did not acknowledge keyboard input.", code: 38)
        }
    }

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
    let titlesBeforePress = currentTaskTitles()
    guard titlesBeforePress.count == 1, titlesBeforePress[0] == expectedTitle else {
        failDesktopSend("Codex task identity changed before sending.", code: 32)
    }
    let pressResult = AXUIElementPerformAction(sendButton, kAXPressAction as CFString)
    guard pressResult == .success else {
        failDesktopSend(
            "Unable to press the semantic Send button: AX error \(pressResult.rawValue).",
            code: 33
        )
    }

    func waitForSubmissionAcknowledgement(attempts: Int) -> (Bool, Int) {
        for _ in 0..<attempts {
            Thread.sleep(forTimeInterval: 0.1)
            let latest = composerCandidates()
            let latestStopCount = latest.stopButtons.count
            if latestStopCount == 1 {
                return (true, latestStopCount)
            }
            if latest.textAreas.count == 1,
               composerIsEmpty(latest.textAreas[0])
            {
                return (true, latestStopCount)
            }
        }
        return (false, 0)
    }

    var (accepted, stopCount) = waitForSubmissionAcknowledgement(attempts: 10)
    if !accepted {
        let titlesBeforeClick = currentTaskTitles()
        candidates = composerCandidates()
        let draftStillMatches = payload.continueOnly || (
            candidates.textAreas.count == 1 &&
            composerText(candidates.textAreas[0]) == message
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
    let isStopCandidate = stopTerms.contains(where: { haystack.contains($0) })

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

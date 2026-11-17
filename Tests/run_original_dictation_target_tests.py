#!/usr/bin/env python3
"""Run real focus-routing/insertion methods with inert platform adapters; no UI events."""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
view = (ROOT / "Sources/Fluid/ContentView.swift").read_text()
typing = (ROOT / "Sources/Fluid/Services/TypingService.swift").read_text()


def method(source, name, prefix="private func"):
    start = source.index("    " + prefix + " " + name + "(")
    opening = source.index("{", start)
    depth = 1
    i = opening + 1
    while depth:
        depth += (source[i] == "{") - (source[i] == "}")
        i += 1
    return source[start:i].replace("private func", "func", 1)


resolver = method(view, "resolveTypingTargetPID")
insertion = method(typing, "insertTextInstantly")
strict = method(typing, "insertTextIntoCapturedTarget") if "private func insertTextIntoCapturedTarget(" in typing else ""
clipboard = method(typing, "insertTextViaClipboardToPid") if strict else ""
swift = r'''
import Foundation
import CoreFoundation
typealias pid_t = Int32
final class App {
    let processIdentifier: Int32
    let bundleIdentifier: String? = "external.app"
    let localizedName: String? = "Test App"
    init(_ pid: Int32) { processIdentifier = pid }
}
final class NSWorkspace {
    static let shared = NSWorkspace()
    var frontmostApplication: App? = App(22)
}
struct NSRunningApplication {
    let bundleIdentifier: String? = "external.app"
    init?(processIdentifier: Int32) {}
}
final class NotchContentState {
    static let shared = NotchContentState()
    var recordingTargetPID: Int32? = 11
}
enum AXError { case success, failure }
let kAXSelectedTextAttribute = "AXSelectedText"
func AXIsProcessTrusted() -> Bool { true }
enum CGEventFlags { case maskCommand }
final class CGEvent {
    static var postedPIDs = [Int32]()
    var flags = CGEventFlags.maskCommand
    init?(keyboardEventSource: Any?, virtualKey: UInt16, keyDown: Bool) {}
    func postToPid(_ pid: Int32) { CGEvent.postedPIDs.append(pid) }
}
func AXUIElementSetAttributeValue(_ element: Int, _ attribute: CFString, _ value: CFTypeRef) -> AXError {
    TypingService.axWrites.append(element)
    if TypingService.driftDuringAX { TypingService.exactFocus = false }
    return TypingService.axSucceeds ? .success : .failure
}
final class TypingService {
    struct CapturedFocusTarget {
        let pid: Int32
        let element: Int
        var isSecureTextField = false
    }
    enum Mode { case standard, reliablePaste }
    var textInsertionMode = Mode.reliablePaste
    static var focusedPID: Int32? = 22
    static var exactFocus = true
    static var axSucceeds = true
    static var driftDuringAX = false
    static var driftDuringClipboard = false
    static var axWrites = [Int]()
    var fallbackCalls = 0
    var targetedPastes: [Int32] { CGEvent.postedPIDs }
    static let pasteVirtualKeyCode: UInt16 = 9
    static func activateApp(pid: Int32) -> Bool { true }
    static func captureSystemFocusedPID() -> Int32? { focusedPID }
    static func isExactFocusTargetActive(_ target: CapturedFocusTarget) -> Bool { exactFocus }
    CAPTURE_GUARD
    static func logFocusState(_ message: String) {}
    func log(_ message: String) {}
    func ghosttyTargetPID(preferredTargetPID: Int32?) -> Int32? { nil }
    func tryReliablePasteInsertion(_ text: String, preferredTargetPID: Int32?) -> Bool { fallbackCalls += 1; return false }
    func insertTextBulkInstant(_ text: String, targetPID: Int32) -> Bool { fallbackCalls += 1; return false }
    func getSystemFocusedElementAndPID() -> (pid: Int32, element: Int)? { (22, 222) }
    func insertTextViaAccessibility(_ text: String) -> Bool { fallbackCalls += 1; return false }
    func insertTextBulkHIDInstant(_ text: String) -> Bool { fallbackCalls += 1; return false }
    func insertTextViaClipboard(_ text: String) -> Bool { fallbackCalls += 1; return false }
    func typeCharacter(_ character: Character) { fallbackCalls += 1 }
    func withTemporaryPasteboardString(_ text: String, restoreDelayMicros: useconds_t, action: () -> Bool) -> Bool {
        if Self.driftDuringClipboard { Self.exactFocus = false }
        return action()
    }
    INSERTION
    STRICT
    CLIPBOARD
}
final class View {
    var recordingFocusTarget: TypingService.CapturedFocusTarget? = .init(pid: 11, element: 111)
    RESOLVER
}
@main struct Tests {
    static var failures = 0
    static var assertions = 0
    static func check(_ value: @autoclosure () -> Bool, _ message: String) {
        assertions += 1
        if !value() { failures += 1; print("FAIL: \(message)") }
    }
    static func main() {
        let view = View()
        let target = DICTATION_RESOLVE_CALL
        check(target.pid == 11 && target.shouldRestoreOriginalFocus,
              "dictation must retain app A after the user switches to app B")
        FIXED_TESTS
        print("\(assertions) assertions, \(failures) failures")
        if failures != 0 { exit(1) }
    }
}
'''
fixed_tests = r'''
        let manual = view.resolveTypingTargetPID(preferRecordingTarget: false)
        check(manual.pid == 22, "manual paste-last must follow the CURRENT cursor")
        NotchContentState.shared.recordingTargetPID = nil
        view.recordingFocusTarget = nil
        check(view.resolveTypingTargetPID(preferRecordingTarget: true).pid == nil,
              "missing original target must never select the new app")
        let typer = TypingService()
        let original = TypingService.CapturedFocusTarget(pid: 11, element: 111)
        check(typer.insertTextInstantly("dictation", preferredTargetPID: 11, requiredFocusTarget: original),
              "captured field accepts direct AX insertion")
        check(TypingService.axWrites == [111] && typer.fallbackCalls == 0,
              "write targets the captured element, not another field or global fallback")
        let secure = TypingService.CapturedFocusTarget(pid: 11, element: 333, isSecureTextField: true)
        check(!typer.insertTextInstantly("dictation", preferredTargetPID: 11, requiredFocusTarget: secure),
              "secure fields must not receive delayed automatic dictation")
        check(!typer.insertTextInstantly("dictation", preferredTargetPID: 22, requiredFocusTarget: original),
              "mismatched PIDs must never redirect the captured field")
        TypingService.exactFocus = false
        check(!typer.insertTextInstantly("dictation", preferredTargetPID: 11, requiredFocusTarget: original),
              "same-app different-field focus must reject automatic insertion")
        check(typer.fallbackCalls == 0 && typer.targetedPastes.isEmpty,
              "failed focus check must not fall through to arbitrary app paste")
        TypingService.exactFocus = true
        TypingService.axSucceeds = false
        TypingService.driftDuringAX = true
        check(!typer.insertTextInstantly("dictation", preferredTargetPID: 11, requiredFocusTarget: original),
              "focus drift after failed AX insertion must suppress keyboard fallback")
        check(typer.targetedPastes.isEmpty && typer.fallbackCalls == 0,
              "focus drift must never dispatch to the current field")
        TypingService.exactFocus = true
        TypingService.driftDuringAX = false
        TypingService.driftDuringClipboard = true
        check(!typer.insertTextInstantly("dictation", preferredTargetPID: 11, requiredFocusTarget: original),
              "focus drift while preparing clipboard must suppress event dispatch")
        check(typer.targetedPastes.isEmpty, "clipboard focus drift must not emit any key events")
        TypingService.exactFocus = true
        TypingService.driftDuringClipboard = false
        check(typer.insertTextInstantly("dictation", preferredTargetPID: 11, requiredFocusTarget: original),
              "AX-unsupported fields retain guarded PID paste compatibility")
        check(typer.targetedPastes == [11, 11] && typer.fallbackCalls == 0,
              "compatibility paste is targeted only to original PID")
'''
swift = (swift.replace("RESOLVER", resolver).replace("INSERTION", insertion)
         .replace("STRICT", strict).replace("FIXED_TESTS", fixed_tests if strict else "")
         .replace("CLIPBOARD", clipboard)
         .replace("DICTATION_RESOLVE_CALL", "view.resolveTypingTargetPID(preferRecordingTarget: true)" if "preferRecordingTarget:" in resolver else "view.resolveTypingTargetPID()")
         .replace("CAPTURE_GUARD", method(typing, "canInsertBeforePostInsertionAction", "nonisolated static func")))
with tempfile.TemporaryDirectory(prefix="fv-original-target-test-") as directory:
    root = Path(directory)
    source = root / "Tests.swift"
    binary = root / "tests"
    source.write_text(swift)
    subprocess.run([os.environ.get("SWIFTC", "swiftc"), "-swift-version", "5", "-parse-as-library", str(source), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)

# Exercise the actual production caller as well as its policy: manual recovery is
# deliberately separate, and live delivery must await the guarded outcome.
assert "preferRecordingTarget: false" in method(view, "pasteLastDictationFromHistory")
delivery = view[view.index("        if shouldTypeExternally {"):view.index("    private func hideOverlayAfterOutput")]
assert "typeOutputPlanToActiveFieldAndWait" in delivery
assert "resolveTypingTargetPID(preferRecordingTarget: true)" in delivery
assert "requiredFocusTarget: focusTarget" in delivery
print("Production caller and recovery wiring verified")

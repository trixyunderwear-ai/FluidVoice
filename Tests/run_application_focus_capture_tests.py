#!/usr/bin/env python3
"""Execute production AX-capture methods with inert, PID-checked AX adapters."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / "Sources/Fluid/Services/TypingService.swift").read_text()


def method(name, prefix="static func"):
    start = source.index("    " + prefix + " " + name + "(")
    opening = source.index("{", start)
    index, depth = opening + 1, 1
    while depth:
        depth += (source[index] == "{") - (source[index] == "}")
        index += 1
    return source[start:index].replace("private static func", "static func", 1)


capture = method("captureSystemFocusTarget")
methods = capture + "\n" + method("isExactFocusTargetActive")
for name in ["focusedAXElement", "capturedFocusTarget"]:
    if "private static func " + name + "(" in source:
        methods += "\n" + method(name, "private static func")
for actual, inert in [
    ("AXUIElementCreateSystemWide", "mockSystemWide"),
    ("AXUIElementCreateApplication", "mockApplication"),
    ("AXUIElementCopyAttributeValue", "mockCopyAttribute"),
    ("AXUIElementGetTypeID", "mockAXTypeID"),
    ("AXUIElementGetPid", "mockGetPID"),
    ("AXIsProcessTrusted", "mockTrusted"),
]:
    methods = methods.replace(actual, inert)
swift = r'''
import Foundation
import CoreFoundation
typealias pid_t = Int32
typealias AXUIElement = NSNumber
enum AXError { case success, failure }
let kAXFocusedUIElementAttribute = "AXFocusedUIElement"
let kAXFocusedWindowAttribute = "AXFocusedWindow"
let kAXMainWindowAttribute = "AXMainWindow"
enum Platform {
    static var trusted = true
    static var systemElement: NSNumber? = nil
    static var appElement: NSNumber? = NSNumber(value: 11001)
    static var secure = false
    static var appQueries = 0
}
func mockTrusted() -> Bool { Platform.trusted }
func mockSystemWide() -> NSNumber { NSNumber(value: 0) }
func mockApplication(_ pid: Int32) -> NSNumber { NSNumber(value: Int(pid) * 1000) }
func mockAXTypeID() -> CFTypeID { CFNumberGetTypeID() }
func mockGetPID(_ element: NSNumber, _ pid: inout Int32) { pid = Int32(element.intValue / 1000) }
func mockCopyAttribute(_ element: NSNumber, _ attribute: CFString, _ value: inout CFTypeRef?) -> AXError {
    if element.intValue == 0 {
        value = Platform.systemElement
    } else {
        Platform.appQueries += 1
        value = Platform.appElement
    }
    return value == nil ? .failure : .success
}
final class App { var processIdentifier: Int32 = 11 }
final class NSWorkspace { static let shared = NSWorkspace(); var frontmostApplication: App? = App() }
struct DebugLogger { static let shared = DebugLogger(); func benchmark(_ label: String, message: String, source: String) {} }
final class TypingService {
    struct CapturedFocusTarget {
        let pid: Int32
        let window: NSNumber?
        let element: NSNumber
        var isSecureTextField: Bool { Platform.secure }
    }
    struct FocusSnapshot { let pid: Int32; let window: NSNumber?; let element: NSNumber }
    static var snapshot: FocusSnapshot?
    static func storeFocusSnapshot(_ value: FocusSnapshot?) { snapshot = value }
    static func copyAXElementAttribute(from element: NSNumber, attribute: CFString) -> NSNumber? { nil }
    static func logFocusState(_ message: String) {}
    METHODS
}
@main struct Tests {
    static func main() {
        var failures = 0
        var assertions = 0
        func check(_ condition: Bool, _ message: String) {
            assertions += 1
            if !condition { failures += 1; print("FAIL: \(message)") }
        }
        let recovered = CAPTURE
        check(recovered?.pid == 11 && recovered?.element == NSNumber(value: 11001), "trusted system-query failure must recover exact focused field from the verified original application")
        if let recovered {
            check(TypingService.isExactFocusTargetActive(recovered), "exact-focus verification must support the same app-scoped AX fallback")
        }
        Platform.appElement = NSNumber(value: 22001)
        check(CAPTURE == nil, "application query returning another PID must be rejected")
        Platform.systemElement = NSNumber(value: 22001)
        check(CAPTURE == nil, "system query returning another PID must not retarget a captured application")
        Platform.systemElement = nil
        Platform.appElement = NSNumber(value: 11001)
        Platform.secure = true
        check(CAPTURE == nil, "secure fields must not become delayed automatic destinations")
        Platform.secure = false
        Platform.trusted = false
        let count = Platform.appQueries
        check(CAPTURE == nil && Platform.appQueries == count, "untrusted app must not attempt AX fallback or manufacture a target")
        print("\(assertions) application-focus assertions, \(failures) failures")
        if failures != 0 { exit(1) }
    }
}
'''
call = "TypingService.captureSystemFocusTarget(expectedPID: 11)" if "expectedPID:" in capture else "TypingService.captureSystemFocusTarget()"
swift = swift.replace("METHODS", methods).replace("CAPTURE", call)
with tempfile.TemporaryDirectory(prefix="fv-app-focus-") as directory:
    path = Path(directory)
    script = path / "Tests.swift"
    binary = path / "tests"
    script.write_text(swift)
    subprocess.run(["swiftc", "-swift-version", "5", "-parse-as-library", str(script), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)

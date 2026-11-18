#!/usr/bin/env python3
"""Execute real recording/reopen methods against inert platform adapters."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
view = (root / "Sources/Fluid/ContentView.swift").read_text()
menu = (root / "Sources/Fluid/Services/MenuBarManager.swift").read_text()


def block(source, start):
    opening = source.index("{", start)
    depth, index = 1, opening + 1
    while depth:
        depth += (source[index] == "{") - (source[index] == "}")
        index += 1
    return source[start:index]


def method(source, name, prefix="private func"):
    return block(source, source.index("    " + prefix + " " + name + "(")).replace(prefix, "func", 1)


disappear = block(view, view.index(".onDisappear {"))
disappear = disappear[disappear.index("{") + 1:-1]
methods = "\n".join(method(view, name) for name in [
    "captureRecordingTargetContext", "captureRecordingFormattingContextIfNeeded", "captureRecordingContext",
    "startRecording", "beginDictationRecording",
])
if "private func handleMainWindowDisappearance(" in view:
    methods += "\n" + method(view, "handleMainWindowDisappearance")
window_methods = "\n".join([
    method(menu, "openMainWindow", "@objc private func"),
    method(menu, "isFluidMainWindow"), method(menu, "bringToFront"),
    method(menu, "retainMainWindowIfPresent", "func"),
])
swift = r'''
import Foundation
typealias pid_t = Int32
struct Target { let pid: Int32; let element: Int }
final class App { let processIdentifier: Int32; init(_ pid: Int32) { processIdentifier = pid } }
final class NSWorkspace { static let shared = NSWorkspace(); var frontmostApplication: App? = App(11) }
enum Focus {
    static var target: Target? = Target(pid: 11, element: 111)
    static func overlay() { target = nil; NSWorkspace.shared.frontmostApplication = App(22) }
    static func reset() { target = Target(pid: 11, element: 111); NSWorkspace.shared.frontmostApplication = App(11) }
}
enum TypingService {
    typealias CapturedFocusTarget = Target
    static func captureSystemFocusTarget(expectedPID: Int32? = nil) -> Target? { Focus.target }
    static func textBeforeCursorInFocusedField() -> String { "" }
}
final class NotchContentState { static let shared = NotchContentState(); var recordingTargetPID: Int32? }
struct DebugLogger {
    static let shared = DebugLogger()
    func debug(_ message: String, source: String) {}
    func info(_ message: String, source: String) {}
    func error(_ message: String, source: String) {}
    func warning(_ message: String, source: String) {}
    func benchmark(_ label: String, message: String, source: String) {}
}
enum ActiveRecordingMode: String { case dictate, promptMode, command, edit }
enum AnalyticsOnboardingTryoutStartMethod { case hotkey }
final class SettingsStore {
    enum DictationShortcutSlot: String { case primary, secondary }
    struct Model { let displayName = "GigaAM"; let supportsStreaming = true }
    static let shared = SettingsStore()
    let selectedSpeechModel = Model()
    var enableTranscriptionSounds = false
    var needsDictationFormattingContext = false
    var onboardingPlaygroundValidated = false
    var onboardingPlaygroundSkipped = false
    var playgroundUsed = false
}
final class AnalyticsService {
    static let shared = AnalyticsService()
    enum Outcome { case error }; enum Stage { case audioStart }
    func recordOnboardingTryoutAttemptStarted(startMethod: AnalyticsOnboardingTryoutStartMethod) {}
    func recordOnboardingTryoutAttemptResult(outcome: Outcome, failureStage: Stage) {}
}
final class TranscriptionSoundPlayer { static let shared = TranscriptionSoundPlayer(); func playStartSound() {} }
final class Rewrite { func clearState() {}; func setPromptAppBundleID(_ value: String) {} }
final class ASR {
    enum Status { case authorized }; enum Outcome { case failed, started }
    var isRunningOrStarting = false
    var micStatus = Status.authorized
    var finalText = ""
    var stopCalls = 0
    var previewStopCalls = 0
    func start(onCaptureStarted: () -> Void) async -> Outcome { onCaptureStarted(); return .started }
    func ensureAsrReady() async throws {}
    func stopWithoutTranscription() async { stopCalls += 1 }
    func stopMicrophonePreview() async { previewStopCalls += 1 }
}
enum WindowLevel { case normal, floating }
struct WindowStyle: OptionSet { let rawValue: Int; static let titled = WindowStyle(rawValue: 1) }
final class NSWindow {
    enum Animation { case none }
    var level = WindowLevel.normal
    var styleMask = WindowStyle.titled
    var canBecomeKey = true
    var isMiniaturized = false
    var isReleasedWhenClosed = false
    var title = "FluidVoice"
    var animationBehavior = Animation.none
    var alphaValue = 1.0
    var shown = 0
    var closed = 0
    func close() { closed += 1 }
    func orderFrontRegardless() { shown += 1 }
    func makeKeyAndOrderFront(_ sender: Any?) { shown += 1 }
    func deminiaturize(_ sender: Any?) { isMiniaturized = false }
}
final class Application {
    var windows = [NSWindow]()
    var isHidden = false
    func unhide(_ sender: Any?) { isHidden = false }
    func activate(ignoringOtherApps: Bool) {}
}
let NSApp = Application()
@MainActor final class MenuBarManager {
    enum Mode { case dictation }
    var hostedWindow: NSWindow?
    var created = 0
    func setOverlayMode(_ mode: Mode) { Focus.overlay() }
    func showRecordingOverlayImmediately() { Focus.overlay() }
    func hideRecordingOverlayImmediately(reason: String) {}
    func ensureUsableMainWindow(_ window: NSWindow) {}
    func createAndShowMainWindow() { created += 1; NSApp.windows.append(NSWindow()) }
    WINDOW_METHODS
}
@MainActor final class Content {
    let asr = ASR()
    let settings = SettingsStore.shared
    let menuBarManager = MenuBarManager()
    let rewriteModeService = Rewrite()
    var recordingFocusTarget: Target?
    var recordingAppInfo: (name: String, bundleId: String, windowTitle: String)?
    var recordingPrecedingText = ""
    var isRecordingForCommand = false
    var isRecordingForRewrite = false
    var isOnboardingVoicePlaygroundStepActive = false
    var playgroundUsed = false
    var prewarmCancellations = 0
    var visualCleanups = 0
    func getCurrentAppInfo() -> (name: String, bundleId: String, windowTitle: String) { ("", "", "") }
    func advanceOverlayLifecycle() { Focus.overlay() }
    func setActiveRecordingMode(_ mode: ActiveRecordingMode) { Focus.overlay() }
    func applyDictationShortcutSelectionContext(for slot: SettingsStore.DictationShortcutSlot) {}
    func prewarmPrivateAIDictationIfNeeded(for slot: SettingsStore.DictationShortcutSlot) {}
    func appBench(_ message: String) {}
    func cancelPrewarmDictationIfNeeded() { prewarmCancellations += 1 }
    func finishAccessibilityPermissionFlow() { visualCleanups += 1 }
    func removeShortcutCaptureMonitor() { visualCleanups += 1 }
    func disappeared() { DISAPPEAR }
    CONTENT_METHODS
}
@main struct Tests {
    @MainActor static func main() async throws {
        var failures = 0
        var assertions = 0
        func check(_ condition: Bool, _ message: String) {
            assertions += 1
            if !condition { failures += 1; print("FAIL: \(message)") }
        }
        Focus.reset()
        let normal = Content()
        normal.beginDictationRecording(for: .primary, mode: .dictate)
        check(normal.recordingFocusTarget?.pid == 11, "dictation captures original field synchronously before mode/overlay changes")
        try await Task.sleep(nanoseconds: 20_000_000)
        check(normal.recordingFocusTarget?.pid == 11, "first-PCM callback must not overwrite original capture after overlay focus changes")
        Focus.reset()
        let resumed = Content()
        resumed.startRecording()
        check(resumed.recordingFocusTarget?.pid == 11, "continuous/startRecording path captures before UI changes")
        try await Task.sleep(nanoseconds: 20_000_000)
        check(resumed.recordingFocusTarget?.pid == 11, "continuous first-PCM callback retains original field")
        Focus.reset()
        let active = Content()
        active.recordingFocusTarget = Target(pid: 33, element: 333)
        active.asr.isRunningOrStarting = true
        active.beginDictationRecording(for: .primary, mode: .dictate)
        check(active.recordingFocusTarget?.pid == 33, "ignored start must not overwrite active recording target")
        let hidden = Content()
        hidden.disappeared()
        try await Task.sleep(nanoseconds: 20_000_000)
        check(hidden.asr.stopCalls == 0 && hidden.prewarmCancellations == 0, "closing/hiding settings must not stop app-owned recording or prewarm")
        check(hidden.visualCleanups == 2, "window disappearance still cleans visual permission/shortcut monitors")
        check(hidden.asr.previewStopCalls == 1, "idle window disappearance releases microphone preview only")
        let background = Content()
        background.asr.isRunningOrStarting = true
        background.disappeared()
        try await Task.sleep(nanoseconds: 20_000_000)
        check(background.asr.previewStopCalls == 0 && background.asr.stopCalls == 0, "active background capture must not be torn down by preview cleanup")
        let window = NSWindow()
        window.isMiniaturized = true
        NSApp.windows = [window]
        let manager = MenuBarManager()
        manager.openMainWindow()
        check(manager.created == 0 && NSApp.windows.count == 1, "reopen must reuse minimized main window rather than create fresh ContentView")
        check(!window.isMiniaturized && window.shown > 0, "reopen must deminiaturize and foreground existing window")
        NSApp.windows = []
        manager.openMainWindow()
        check(manager.created == 0 && window.shown > 2, "closed primary window must retain ContentView identity for reopen")
        let retained = NSWindow()
        retained.isReleasedWhenClosed = true
        NSApp.windows = [retained]
        manager.retainMainWindowIfPresent()
        check(manager.hostedWindow === retained && !retained.isReleasedWhenClosed, "appearing main window retains its view ownership before close")
        NSApp.windows = []
        manager.openMainWindow()
        check(manager.created == 0 && retained.shown > 0, "reopen after close reuses the retained view instead of replacing recording callbacks")

        print("\(assertions) capture/window lifecycle assertions, \(failures) failures")
        if failures != 0 { exit(1) }
    }
}
'''
swift = swift.replace("CONTENT_METHODS", methods).replace("WINDOW_METHODS", window_methods).replace("DISAPPEAR", disappear)
with tempfile.TemporaryDirectory(prefix="fv-focus-capture-lifecycle-") as directory:
    path = Path(directory)
    source = path / "Tests.swift"
    binary = path / "tests"
    source.write_text(swift)
    subprocess.run(["swiftc", "-swift-version", "5", "-parse-as-library", str(source), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)

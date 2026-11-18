import Foundation

private final nonisolated class MockGigaAMTransport: GigaAMStreamingTransport, @unchecked Sendable {
    let lock = NSLock()
    private var messages: [URLSessionWebSocketTask.Message]
    private var writes: [URLSessionWebSocketTask.Message] = []
    private var closed = false
    var truncated = false
    var sendsFinal = true
    var failsBinary = false
    var binaryDelay: UInt64 = 0

    init(ready: Bool = true) {
        self.messages = ready ? [.string("{\"type\":\"ready\"}")] : []
    }

    func send(_ message: URLSessionWebSocketTask.Message) async throws {
        if case .data = message, self.binaryDelay > 0 { try await Task.sleep(nanoseconds: self.binaryDelay) }
        try self.lock.withLock {
            guard !self.closed else { throw GigaAMStreamingError.disconnected }
            self.writes.append(message)
            switch message {
            case .data:
                if self.failsBinary { throw GigaAMStreamingError.disconnected }
                let binaryCount = self.writes.filter { if case .data = $0 { return true }; return false }.count
                let partial = binaryCount == 1 ? "old partial" : "replacement partial"
                self.messages.append(.string("{\"type\":\"partial\",\"text\":\"\(partial)\"}"))
            case let .string(text):
                if text.contains("\"stop\""), self.sendsFinal {
                    self.messages.append(.string("{\"type\":\"final\",\"text\":\"authoritative final\",\"truncated\":\(self.truncated)}"))
                }
            @unknown default: break
            }
        }
    }

    func receive() async throws -> URLSessionWebSocketTask.Message {
        while true {
            if let value = try self.lock.withLock({ () throws -> URLSessionWebSocketTask.Message? in
                if !self.messages.isEmpty { return self.messages.removeFirst() }
                if self.closed { throw GigaAMStreamingError.disconnected }
                return nil
            }) { return value }
            try await Task.sleep(nanoseconds: 1_000_000)
        }
    }

    func close() { self.lock.withLock { self.closed = true } }
    func snapshot() -> (writes: [URLSessionWebSocketTask.Message], closed: Bool) {
        self.lock.withLock { (self.writes, self.closed) }
    }
}

private final nonisolated class MockGigaAMFactory: @unchecked Sendable {
    private let lock = NSLock()
    private var transports: [MockGigaAMTransport]
    init(_ transports: [MockGigaAMTransport]) { self.transports = transports }
    func next() -> any GigaAMStreamingTransport { self.lock.withLock { self.transports.removeFirst() } }
}

@main
struct GigaAMStreamingSessionTests {
    static var assertions = 0
    static let server = URL(string: "http://127.0.0.1:9876")!

    static func check(_ condition: Bool, _ message: String) throws {
        self.assertions += 1
        if !condition { throw NSError(domain: "GigaAMStreamingTests", code: 1, userInfo: [NSLocalizedDescriptionKey: message]) }
    }

    static func rejects(_ message: String, operation: () async throws -> Void) async throws {
        do { try await operation() } catch { return }
        try self.check(false, message)
    }

    static func main() async throws {
        if CommandLine.arguments.count == 2 {
            try await self.liveSmoke(pcmPath: CommandLine.arguments[1])
            return
        }
        try self.check(
            GigaAMStreamingSession.encodePCM16([-2, -1, 0, 0.5, 1, 2, Float.nan]) == Data([1, 128, 1, 128, 0, 0, 255, 63, 255, 127, 255, 127, 0, 0]),
            "PCM must be finite, clamped, mono Int16 little-endian"
        )
        let mock = MockGigaAMTransport()
        let session = GigaAMStreamingSession(makeTransport: { _ in mock })
        await session.begin()
        try self.check(!(await session.hasStreamedAudio()), "short/no-preview captures must remain eligible for HTTP")
        _ = try await session.preview([0.1, 0.2], serverURL: self.server)
        try await Task.sleep(nanoseconds: 20_000_000)
        _ = try await session.preview([0.1, 0.2, 0.3], serverURL: self.server)
        try await Task.sleep(nanoseconds: 20_000_000)
        let partial = try await session.preview([0.1, 0.2, 0.3], serverURL: self.server)
        try self.check(partial == "replacement partial", "partial snapshots replace, not concatenate")
        let result = try await session.finish([0.1, 0.2, 0.3, 0.4], serverURL: self.server)
        try self.check(result == "authoritative final", "only final server text is authoritative")
        let snapshot = mock.snapshot()
        let pcm = snapshot.writes.compactMap { message -> Data? in if case let .data(value) = message { return value }; return nil }
        try self.check(pcm.map(\.count) == [4, 2, 2], "cumulative input must send only new samples, including stop tail")
        try self.check(pcm.reduce(Data(), +) == GigaAMStreamingSession.encodePCM16([0.1, 0.2, 0.3, 0.4]), "PCM must have no duplicate prefix or missing tail")
        let commands = snapshot.writes.compactMap { message -> String? in if case let .string(value) = message { return value }; return nil }
        try self.check(commands.count == 2 && commands[0].contains("on_finalize") && commands[0].contains("manual") && commands[1].contains("stop"), "ready/configure/audio/stop must use manual finalization, without waiting for configure ack")
        let activeAfterFinal = await session.isActive()
        try self.check(snapshot.closed && !activeAfterFinal, "successful final must close the session")

        let truncated = MockGigaAMTransport()
        truncated.truncated = true
        let truncatedSession = GigaAMStreamingSession(makeTransport: { _ in truncated })
        await truncatedSession.begin()
        _ = try await truncatedSession.preview([0.2], serverURL: self.server)
        try await self.rejects("truncated final must fail into full-audio HTTP fallback") {
            _ = try await truncatedSession.finish([0.2], serverURL: self.server)
        }
        try self.check(truncated.snapshot().closed, "truncated stream must close")

        let missingReady = MockGigaAMTransport(ready: false)
        let readySession = GigaAMStreamingSession(readyTimeout: 0.04, makeTransport: { _ in missingReady })
        await readySession.begin()
        try await self.rejects("ready wait must be bounded") { _ = try await readySession.preview([0.1], serverURL: self.server) }
        try self.check(missingReady.snapshot().closed && missingReady.snapshot().writes.isEmpty, "audio must never be sent before ready")

        let missingFinal = MockGigaAMTransport()
        missingFinal.sendsFinal = false
        let finalSession = GigaAMStreamingSession(finalTimeout: 0.04, makeTransport: { _ in missingFinal })
        await finalSession.begin()
        _ = try await finalSession.preview([0.1], serverURL: self.server)
        try await self.rejects("missing final must time out instead of reusing partial") { _ = try await finalSession.finish([0.1], serverURL: self.server) }
        try self.check(missingFinal.snapshot().closed, "timed-out final must close")

        let broken = MockGigaAMTransport()
        broken.failsBinary = true
        let brokenSession = GigaAMStreamingSession(makeTransport: { _ in broken })
        await brokenSession.begin()
        try await self.rejects("binary send failure must not count samples as delivered") { _ = try await brokenSession.preview([0.1], serverURL: self.server) }
        try self.check(!(await brokenSession.hasStreamedAudio()), "failed send must not advance PCM cursor")

        let slow = MockGigaAMTransport()
        slow.binaryDelay = 40_000_000
        let slowSession = GigaAMStreamingSession(makeTransport: { _ in slow })
        await slowSession.begin()
        let preview = Task { try await slowSession.preview([0.1], serverURL: self.server) }
        // Normal stop drains the in-flight preview instead of cancelling it.
        _ = try await preview.value
        try self.check(!slow.snapshot().closed, "normal stop must preserve the healthy preview connection")
        let final = try await slowSession.finish([0.1, 0.2], serverURL: self.server)
        try self.check(final == "authoritative final", "stop during preview must retain WS final path")

        let cancelled = MockGigaAMTransport()
        cancelled.binaryDelay = 50_000_000
        let cancelSession = GigaAMStreamingSession(makeTransport: { _ in cancelled })
        await cancelSession.begin()
        let pending = Task { try await cancelSession.preview([0.1], serverURL: self.server) }
        try await Task.sleep(nanoseconds: 10_000_000)
        await cancelSession.cancel()
        _ = await pending.result
        let activeAfterCancel = await cancelSession.isActive()
        try self.check(cancelled.snapshot().closed && !activeAfterCancel, "cancel must close receiver/send and clear active state")

        let oldTransport = MockGigaAMTransport()
        oldTransport.binaryDelay = 50_000_000
        let newTransport = MockGigaAMTransport()
        let factory = MockGigaAMFactory([oldTransport, newTransport])
        let generationSession = GigaAMStreamingSession(makeTransport: { _ in factory.next() })
        await generationSession.begin()
        let oldPreview = Task { try await generationSession.preview([0.1], serverURL: self.server) }
        try await Task.sleep(nanoseconds: 20_000_000)
        await generationSession.begin()
        _ = try await generationSession.preview([0.2, 0.3, 0.4], serverURL: self.server)
        _ = await oldPreview.result
        let newFinal = try await generationSession.finish([0.2, 0.3, 0.4], serverURL: self.server)
        let newPCM = newTransport.snapshot().writes.compactMap { message -> Data? in if case let .data(value) = message { return value }; return nil }
        try self.check(newFinal == "authoritative final", "old receiver/send completion must not fail the next generation")
        try self.check(newPCM == [GigaAMStreamingSession.encodePCM16([0.2, 0.3, 0.4])], "new recording must resend its full PCM even when it is longer than old recording")
        await session.begin()
        try self.check(!(await session.hasStreamedAudio()), "a new capture resets sample cursor even if next capture is longer")
        await session.cancel()
        print("\(self.assertions) GigaAM streaming assertions passed")
    }

    /// Optional actual URLSession/WebSocket smoke; synthetic fixture only, no mic/UI.
    static func liveSmoke(pcmPath: String) async throws {
        let pcm = try Data(contentsOf: URL(fileURLWithPath: pcmPath))
        let samples: [Float] = stride(from: 0, to: pcm.count - 1, by: 2).map { offset in
            Float(Int16(bitPattern: UInt16(pcm[offset]) | (UInt16(pcm[offset + 1]) << 8))) / 32768
        }
        let session = GigaAMStreamingSession()
        await session.begin()
        let start = ProcessInfo.processInfo.systemUptime
        var firstPartial: Double?
        var end = 0
        while end < samples.count {
            end = min(samples.count, end + 4000)
            let partial = try await session.preview(Array(samples.prefix(end)), serverURL: self.server)
            if !partial.isEmpty, firstPartial == nil { firstPartial = ProcessInfo.processInfo.systemUptime - start }
            try await Task.sleep(nanoseconds: 250_000_000)
        }
        let stop = ProcessInfo.processInfo.systemUptime
        let final = try await session.finish(samples, serverURL: self.server)
        try self.check(!final.isEmpty, "live WebSocket final cannot be empty for speech fixture")
        print("URLSession smoke audioSeconds=\(Double(samples.count) / 16000) firstPartialMs=\(Int((firstPartial ?? -1) * 1000)) finalTailMs=\(Int((ProcessInfo.processInfo.systemUptime - stop) * 1000)) finalWords=\(final.split(whereSeparator: \.isWhitespace).count)")
    }
}

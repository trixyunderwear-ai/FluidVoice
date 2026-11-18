import Foundation

/// A small injectable boundary keeps the WebSocket lifecycle testable without a mic.
nonisolated protocol GigaAMStreamingTransport: Sendable {
    func send(_ message: URLSessionWebSocketTask.Message) async throws
    func receive() async throws -> URLSessionWebSocketTask.Message
    func close()
}

private final nonisolated class GigaAMURLSessionTransport: GigaAMStreamingTransport, @unchecked Sendable {
    private let session: URLSession
    private let socket: URLSessionWebSocketTask

    init(url: URL) {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 5
        self.session = URLSession(configuration: configuration)
        self.socket = self.session.webSocketTask(with: url)
        self.socket.resume()
    }

    func send(_ message: URLSessionWebSocketTask.Message) async throws { try await self.socket.send(message) }
    func receive() async throws -> URLSessionWebSocketTask.Message { try await self.socket.receive() }
    func close() {
        self.socket.cancel(with: .goingAway, reason: nil)
        self.session.invalidateAndCancel()
    }
}

nonisolated enum GigaAMStreamingError: Error {
    case inactive, invalidMessage, rejected, disconnected, timeout, audioPrefixChanged
}

/// One recording owns one connection. Receiver callbacks carry an identity so a
/// cancelled/previous socket cannot overwrite the next recording's partial/final.
actor GigaAMStreamingSession {
    typealias TransportFactory = @Sendable (URL) -> any GigaAMStreamingTransport
    private let makeTransport: TransportFactory
    private let readyTimeout: TimeInterval
    private let finalTimeout: TimeInterval
    private var generation = UUID()
    private var transport: (any GigaAMStreamingTransport)?
    private var receiver: Task<Void, Never>?
    private var active = false
    private var ready = false
    private var configured = false
    private var finishing = false
    private var failed = false
    private var sentSamples = 0
    private var partial = ""
    private var final: String?

    init(
        readyTimeout: TimeInterval = 5,
        finalTimeout: TimeInterval = 10,
        makeTransport: @escaping TransportFactory = { GigaAMURLSessionTransport(url: $0) }
    ) {
        self.readyTimeout = readyTimeout
        self.finalTimeout = finalTimeout
        self.makeTransport = makeTransport
    }

    deinit {
        self.receiver?.cancel()
        self.transport?.close()
    }

    func begin() {
        self.cancel()
        self.active = true
    }

    func cancel() {
        self.generation = UUID()
        self.receiver?.cancel()
        self.receiver = nil
        self.transport?.close()
        self.transport = nil
        self.active = false
        self.ready = false
        self.configured = false
        self.finishing = false
        self.failed = false
        self.sentSamples = 0
        self.partial = ""
        self.final = nil
    }

    func isActive() -> Bool { self.active }
    func hasStreamedAudio() -> Bool { self.active && self.sentSamples > 0 }

    func preview(_ samples: [Float], serverURL: URL) async throws -> String {
        let token = self.generation
        do {
            try await self.append(samples, serverURL: serverURL, token: token)
            return self.partial
        } catch {
            self.fail(token: token)
            throw error
        }
    }

    func finish(_ samples: [Float], serverURL: URL) async throws -> String {
        let token = self.generation
        do {
            try await self.append(samples, serverURL: serverURL, token: token)
            self.finishing = true
            try await self.send(.string("{\"type\":\"stop\"}"), token: token)
            let deadline = ProcessInfo.processInfo.systemUptime + self.finalTimeout
            while self.final == nil {
                try self.check(token)
                guard ProcessInfo.processInfo.systemUptime < deadline else { throw GigaAMStreamingError.timeout }
                try await Task.sleep(nanoseconds: 10_000_000)
            }
            try self.check(token)
            let text = self.final!
            self.cancel()
            return text
        } catch {
            self.fail(token: token)
            throw error
        }
    }

    private func check(_ token: UUID) throws {
        try Task.checkCancellation()
        guard token == self.generation, self.active else { throw CancellationError() }
        guard !self.failed else { throw GigaAMStreamingError.disconnected }
    }

    private func append(_ samples: [Float], serverURL: URL, token: UUID) async throws {
        try self.check(token)
        guard !self.finishing, samples.count >= self.sentSamples else { throw GigaAMStreamingError.audioPrefixChanged }
        if self.transport == nil {
            var components = URLComponents(url: serverURL.appendingPathComponent("v1/ws"), resolvingAgainstBaseURL: false)
            components?.scheme = serverURL.scheme == "https" ? "wss" : "ws"
            guard let url = components?.url else { throw GigaAMStreamingError.invalidMessage }
            let transport = self.makeTransport(url)
            self.transport = transport
            self.receiver = Task { [weak self, transport] in
                do {
                    while !Task.isCancelled {
                        let message = try await transport.receive()
                        guard let self else { return }
                        if try await self.accept(message, token: token) { return }
                    }
                } catch {
                    await self?.fail(token: token)
                }
            }
        }
        if !self.configured {
            let deadline = ProcessInfo.processInfo.systemUptime + self.readyTimeout
            while !self.ready {
                try self.check(token)
                guard ProcessInfo.processInfo.systemUptime < deadline else { throw GigaAMStreamingError.timeout }
                try await Task.sleep(nanoseconds: 10_000_000)
            }
            // gigastt does not send a configure acknowledgement. Audio follows
            // this awaited send in-order on the same connection.
            try await self.send(.string("{\"type\":\"configure\",\"sample_rate\":16000,\"endpoint_mode\":\"manual\",\"commit_policy\":\"on_finalize\"}"), token: token)
            self.configured = true
        }
        let delta = Self.encodePCM16(samples.dropFirst(self.sentSamples))
        if !delta.isEmpty {
            try await self.send(.data(delta), token: token)
            try self.check(token)
            self.sentSamples = samples.count
        }
    }

    private func send(_ message: URLSessionWebSocketTask.Message, token: UUID) async throws {
        try self.check(token)
        guard let transport = self.transport else { throw GigaAMStreamingError.disconnected }
        // Closing on timeout/cancellation also unblocks URLSession's pending
        // send, so a task-group loser cannot hold stop/cancel indefinitely.
        try await withTaskCancellationHandler {
            try await withThrowingTaskGroup(of: Void.self) { group in
                group.addTask { try await transport.send(message) }
                group.addTask {
                    try await Task.sleep(nanoseconds: 5_000_000_000)
                    transport.close()
                    throw GigaAMStreamingError.timeout
                }
                defer { group.cancelAll() }
                _ = try await group.next()
            }
        } onCancel: {
            transport.close()
        }
        try self.check(token)
    }

    /// true ends the receiver after the one authoritative manual final.
    private func accept(_ message: URLSessionWebSocketTask.Message, token: UUID) throws -> Bool {
        guard token == self.generation, self.active else { return true }
        let data: Data
        switch message {
        case let .string(text): data = Data(text.utf8)
        case let .data(value): data = value
        @unknown default: throw GigaAMStreamingError.invalidMessage
        }
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = object["type"] as? String
        else { throw GigaAMStreamingError.invalidMessage }
        switch type {
        case "ready": self.ready = true
        case "partial":
            guard self.configured, let text = object["text"] as? String else { throw GigaAMStreamingError.invalidMessage }
            self.partial = text // Snapshots replace; never concatenate hypotheses.
        case "final":
            guard self.finishing, object["truncated"] as? Bool != true,
                  let text = object["text"] as? String
            else { throw GigaAMStreamingError.invalidMessage }
            self.final = text
            return true
        case "error": throw GigaAMStreamingError.rejected
        default: break // Progress/diagnostic events carry no transcript state.
        }
        return false
    }

    private func fail(token: UUID) {
        guard token == self.generation else { return }
        self.failed = true
        self.receiver?.cancel()
        self.receiver = nil
        self.transport?.close()
        self.transport = nil
    }

    nonisolated static func encodePCM16<S: Sequence>(_ samples: S) -> Data where S.Element == Float {
        var data = Data()
        for sample in samples {
            let finite = sample.isFinite ? sample : 0
            var value = Int16(max(-1, min(1, finite)) * 32767).littleEndian
            withUnsafeBytes(of: &value) { data.append(contentsOf: $0) }
        }
        return data
    }
}

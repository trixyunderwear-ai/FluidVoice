#!/usr/bin/env python3
"""Compile the actual Foundation-only WS session with inert transport tests."""
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="gigaam-stream-tests-") as directory:
    binary = Path(directory) / "tests"
    subprocess.run([
        "swiftc", "-swift-version", "5", "-parse-as-library",
        str(root / "Sources/Fluid/Services/GigaAMStreamingSession.swift"),
        str(root / "Tests/FluidDictationIntegrationTests/GigaAMStreamingSessionTests.swift"),
        "-o", str(binary),
    ], check=True)
    subprocess.run([str(binary), *sys.argv[1:]], check=True)

# Source-wiring checks complement executable transport tests: the complete final
# PCM must remain the HTTP fallback and normal stop must not cancel a preview.
provider = (root / "Sources/Fluid/Services/GigaAMProvider.swift").read_text()
asr = (root / "Sources/Fluid/Services/ASRService.swift").read_text()
assert "return try await self.transcribe(samples)" in provider
assert "self.streamingSession.hasStreamedAudio()" in provider
assert "beginStreamingSession()" in asr and "cancelStreamingSession()" in asr
assert "if !(self.transcriptionProvider is GigaAMProvider) || self.isRunning" in asr
print("Streaming lifecycle and full-audio fallback wiring verified")

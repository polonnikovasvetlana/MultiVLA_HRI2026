"""HTTP service for server-side Silero TTS synthesis."""

from __future__ import annotations

import argparse
import io
import json
import threading
import wave
from array import array
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import torch


MAX_REQUEST_BYTES = 16 * 1024
MAX_TEXT_LENGTH = 500


class SileroTTS:
    def __init__(self, device: str, speaker: str, sample_rate: int) -> None:
        self.device = torch.device(device)
        self.speaker = speaker
        self.sample_rate = sample_rate
        loaded = torch.hub.load(
            repo_or_dir="snakers4/silero-models",
            model="silero_tts",
            language="en",
            speaker="v3_en",
            trust_repo=True,
        )
        self.model = loaded[0] if isinstance(loaded, tuple) else loaded
        self.model.to(self.device)
        self.lock = threading.Lock()

    def synthesize(self, text: str) -> bytes:
        value = " ".join(text.split())
        if not value:
            raise ValueError("text must not be empty")
        if len(value) > MAX_TEXT_LENGTH:
            raise ValueError(f"text must not exceed {MAX_TEXT_LENGTH} characters")

        with self.lock, torch.inference_mode():
            audio = self.model.apply_tts(
                text=value,
                speaker=self.speaker,
                sample_rate=self.sample_rate,
            )

        samples = array(
            "h",
            (
                int(max(-1.0, min(1.0, float(sample))) * 32767.0)
                for sample in audio.detach().cpu().flatten().tolist()
            ),
        )
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.sample_rate)
            wav.writeframes(samples.tobytes())
        return output.getvalue()


class TTSHandler(BaseHTTPRequestHandler):
    engine: SileroTTS

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_json(
                200,
                {
                    "status": "ok",
                    "device": str(self.engine.device),
                    "speaker": self.engine.speaker,
                    "sample_rate": self.engine.sample_rate,
                },
            )
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/synthesize":
            self.send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise ValueError("invalid request size")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
                raise ValueError("JSON field 'text' must be a string")
            audio = self.engine.synthesize(payload["text"])
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json(400, {"error": str(error)})
            return
        except Exception as error:
            self.send_json(500, {"error": f"TTS synthesis failed: {error}"})
            return

        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(audio)))
        self.end_headers()
        self.wfile.write(audio)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"TTS {self.address_string()} - {format % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Silero English TTS HTTP server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8030)
    parser.add_argument(
        "--device",
        choices=("cpu", "cuda"),
        default="cuda" if torch.cuda.is_available() else "cpu",
    )
    parser.add_argument("--speaker", default="en_23")
    parser.add_argument("--sample-rate", type=int, default=48000)
    args = parser.parse_args()

    print(f"Loading Silero TTS on {args.device}...", flush=True)
    TTSHandler.engine = SileroTTS(args.device, args.speaker, args.sample_rate)
    server = ThreadingHTTPServer((args.host, args.port), TTSHandler)
    print(f"TTS API: http://{args.host}:{args.port}", flush=True)
    print("Routes: GET /health, POST /synthesize", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nTTS stopped")
    finally:
        server.server_close()



if __name__ == "__main__":
    main()

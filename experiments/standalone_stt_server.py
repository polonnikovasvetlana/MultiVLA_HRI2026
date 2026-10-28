import argparse
import json
import os
import re
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Pattern

import torch
import whisper


MAX_AUDIO_BYTES = 25 * 1024 * 1024
DEFAULT_WAKE_WORDS = "вэлли,валли,Vallie,Wally,Valley"


def parse_wake_words(value: str) -> list[str]:
    return [word.strip() for word in value.split(",") if word.strip()]


def compile_wake_pattern(wake_words: list[str]) -> Pattern[str]:
    """Build a Unicode-aware pattern for one- or multi-word wake aliases."""
    alternatives: list[str] = []
    for wake_word in sorted(wake_words, key=len, reverse=True):
        words = wake_word.split()
        if words:
            alternatives.append(r"[\W_]+".join(re.escape(word) for word in words))
    if not alternatives:
        raise ValueError("Список wake words пуст")
    return re.compile(
        rf"(?<!\w)(?:{'|'.join(alternatives)})(?!\w)",
        flags=re.IGNORECASE,
    )


def extract_wake_command(text: str, pattern: Pattern[str]) -> tuple[bool, str, str | None]:
    """Return activation state, text without wake word, and matched alias."""
    match = pattern.search(text)
    if match is None:
        return False, "", None

    separators = " \t\r\n,.:;!?—-"
    before = text[:match.start()].rstrip(separators)
    after = text[match.end():].lstrip(separators)
    command = re.sub(r"\s+", " ", f"{before} {after}").strip(separators)
    return True, command, match.group(0)


class TranscriptionHandler(BaseHTTPRequestHandler):
    model: Any = None
    model_lock = threading.Lock()
    use_fp16 = False
    language: str | None = "ru"
    wake_pattern = compile_wake_pattern(parse_wake_words(DEFAULT_WAKE_WORDS))

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_json(200, {"status": "ok"})
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/transcribe":
            self.send_json(404, {"error": "not found"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json(400, {"error": "invalid Content-Length"})
            return

        if content_length <= 0:
            self.send_json(400, {"error": "empty audio body"})
            return
        if content_length > MAX_AUDIO_BYTES:
            self.send_json(413, {"error": "audio is too large"})
            return

        wav_data = self.rfile.read(content_length)
        temp_path = ""

        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as audio_file:
                audio_file.write(wav_data)
                temp_path = audio_file.name

            print(f"🟡 Получено {len(wav_data)} байт. Распознаю...", flush=True)
            with self.model_lock:
                result = self.model.transcribe(
                    temp_path,
                    language=self.language,
                    task="transcribe",
                    fp16=self.use_fp16,
                    condition_on_previous_text=False,
                    verbose=None,
                )

            text = str(result.get("text", "")).strip()
            activated, command, matched_wake_word = extract_wake_command(
                text,
                self.wake_pattern,
            )

            if not activated:
                print("🔇 Wake word не найден. Фраза отклонена.", flush=True)
                self.send_json(200, {"activated": False})
                return

            print(f"✅ Wake word: {matched_wake_word}; команда: {command or '—'}", flush=True)
            self.send_json(
                200,
                {
                    "activated": True,
                    "command": command,
                    "wake_word": matched_wake_word,
                    "language": result.get("language", self.language),
                },
            )
        except Exception as error:
            print(f"❌ Ошибка распознавания: {error}", flush=True)
            self.send_json(500, {"error": str(error)})
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    def log_message(self, format: str, *args: Any) -> None:
        return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Whisper HTTP transcription server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--model", default=os.getenv("WHISPER_MODEL", "small"))
    parser.add_argument(
        "--language",
        choices=("ru", "en", "auto"),
        default=os.getenv("WHISPER_LANGUAGE", "ru"),
        help="Язык речи; auto включает автоопределение Whisper",
    )
    parser.add_argument(
        "--wake-words",
        default=os.getenv("WAKE_WORDS", os.getenv("WAKE_WORD", DEFAULT_WAKE_WORDS)),
        help="Варианты wake word через запятую",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    wake_words = parse_wake_words(args.wake_words)
    TranscriptionHandler.wake_pattern = compile_wake_pattern(wake_words)
    TranscriptionHandler.language = None if args.language == "auto" else args.language

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"⏳ Загружаю Whisper {args.model!r} на {device}...", flush=True)
    TranscriptionHandler.model = whisper.load_model(args.model, device=device)
    TranscriptionHandler.use_fp16 = device == "cuda"

    server = ThreadingHTTPServer((args.host, args.port), TranscriptionHandler)
    print(f"✅ Сервер: http://{args.host}:{args.port}", flush=True)
    print(f"Wake words: {', '.join(wake_words)}", flush=True)
    print(f"Язык: {args.language}", flush=True)
    print("Маршруты: GET /health, POST /transcribe", flush=True)
    print("Остановка: Ctrl+C", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n⛔ Сервер остановлен")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

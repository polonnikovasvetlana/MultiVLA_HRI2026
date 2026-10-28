import argparse
import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import torch
import whisper

from server_command_pipeline import CommandPipeline
from wake_words import compile_wake_pattern, extract_wake_command, parse_wake_words


MAX_AUDIO_BYTES = 25 * 1024 * 1024
DEFAULT_WAKE_WORDS = "вэлли,валли,Vallie,Wally,Valley,Vali,Valya,Walla,Valet,Valera"


class TranscriptionHandler(BaseHTTPRequestHandler):
    model: Any = None
    model_lock = threading.Lock()
    use_fp16 = False
    language: str | None = "ru"
    wake_pattern = compile_wake_pattern(parse_wake_words(DEFAULT_WAKE_WORDS))
    command_pipeline = CommandPipeline()

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
                    "planner_url": self.command_pipeline.planner_url,
                    "executor_url": self.command_pipeline.executor_url,
                    "auto_execute": self.command_pipeline.auto_execute,
                },
            )
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
                self.send_json(
                    200,
                    {
                        "activated": False,
                        "text": text,
                        "language": result.get("language", self.language),
                    },
                )
                return

            print(f"✅ Wake word: {matched_wake_word}; команда: {command or '—'}", flush=True)
            pipeline_result: dict[str, Any] | None = None
            pipeline_error: str | None = None
            if command:
                try:
                    pipeline_result = self.command_pipeline.process(command)
                except Exception as error:
                    pipeline_error = str(error)
                    print(f"❌ Ошибка command pipeline: {error}", flush=True)

            self.send_json(
                200,
                {
                    "activated": True,
                    "text": text,
                    "command": command,
                    "wake_word": matched_wake_word,
                    "language": result.get("language", self.language),
                    "pipeline": pipeline_result,
                    "pipeline_error": pipeline_error,
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
    parser.add_argument("--model", default=os.getenv("WHISPER_MODEL", "medium"))
    parser.add_argument(
        "--language",
        choices=("ru", "en", "auto"),
        default=os.getenv("WHISPER_LANGUAGE", "en"),
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
    print(
        f"Planner: {TranscriptionHandler.command_pipeline.planner_url}; "
        f"executor: {TranscriptionHandler.command_pipeline.executor_url}; "
        f"auto_execute={TranscriptionHandler.command_pipeline.auto_execute}",
        flush=True,
    )
    print("Остановка: Ctrl+C", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n⛔ Сервер остановлен")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

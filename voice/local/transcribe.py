"""Распознавание аудиофайла или короткой записи микрофона через MLX Whisper."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


DEFAULT_MODEL = "mlx-community/whisper-medium-mlx"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path, help="Путь к готовой аудиозаписи")
    source.add_argument("--microphone", action="store_true", help="Записать фразу на Mac")
    source.add_argument("--list-microphones", action="store_true", help="Показать устройства FFmpeg")
    parser.add_argument("--seconds", type=int, default=10, help="Длительность записи, по умолчанию 10 с")
    parser.add_argument("--mic-index", type=int, default=0, help="Индекс аудиоустройства FFmpeg")
    parser.add_argument("--language", choices=("ru", "en", "auto"), default="ru")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="MLX-модель: Hugging Face ID или локальная папка")
    parser.add_argument("--output", type=Path, help="Новый текстовый файл; существующий не перезаписывается")
    args = parser.parse_args()
    if args.seconds <= 0 or args.mic_index < 0:
        parser.error("--seconds должен быть положительным, --mic-index — неотрицательным")
    if args.file is not None:
        args.file = args.file.expanduser().resolve()
        if not args.file.is_file():
            parser.error(f"Аудиофайл не найден: {args.file}")
    return args


def require_ffmpeg() -> str:
    executable = shutil.which("ffmpeg")
    if executable is None:
        raise RuntimeError("FFmpeg не найден. Активируй окружение agenticbot с установленным FFmpeg.")
    return executable


def main() -> int:
    args = parse_args()
    ffmpeg = require_ffmpeg()
    if args.list_microphones:
        # AVFoundation reports device names on stderr and normally exits with
        # a nonzero status because no recording input was supplied.
        subprocess.run([ffmpeg, "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""], check=False)
        return 0

    output = args.output.expanduser().resolve() if args.output else (
        args.file.with_suffix(".txt") if args.file else
        Path(__file__).resolve().parent / f"transcript-{datetime.now():%Y%m%d-%H%M%S-%f}.txt"
    )
    if output.exists():
        raise RuntimeError(f"Файл уже существует: {output}. Укажи другой --output.")
    if not output.parent.is_dir():
        raise RuntimeError(f"Папка для результата не существует: {output.parent}")

    try:
        import mlx.core as mx
        import mlx_whisper
    except ImportError as error:
        raise RuntimeError("Не удалось импортировать MLX Whisper. Используй окружение agenticbot.") from error
    if not mx.metal.is_available():
        raise RuntimeError("Metal недоступен: этот скрипт предназначен для MLX на GPU Apple Silicon.")
    mx.set_default_device(mx.gpu)
    print(f"Устройство: {mx.default_device()}", flush=True)
    print(f"Модель: {args.model}", flush=True)
    print("При отсутствии модели в кеше она будет скачана с Hugging Face.", flush=True)

    with tempfile.TemporaryDirectory(prefix="agenticbot-audio-") as temp_dir:
        audio = args.file
        if args.microphone:
            if sys.platform != "darwin":
                raise RuntimeError("Запись через AVFoundation поддерживается только на macOS.")
            audio = Path(temp_dir) / "microphone.wav"
            print(f"Говори: запись {args.seconds} с. При первом запуске разреши доступ к микрофону.", flush=True)
            subprocess.run([
                ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                "-f", "avfoundation", "-i", f":{args.mic_index}",
                "-t", str(args.seconds), "-ar", "16000", "-ac", "1",
                "-c:a", "pcm_s16le", str(audio),
            ], check=True)
        started = time.perf_counter()
        result = mlx_whisper.transcribe(
            str(audio), path_or_hf_repo=args.model,
            language=None if args.language == "auto" else args.language,
            task="transcribe", condition_on_previous_text=False, verbose=None,
        )
        elapsed = time.perf_counter() - started
        text = str(result.get("text", "")).strip()
        # Exclusive creation protects an existing result even if it appeared
        # while recording or transcribing.
        with output.open("x", encoding="utf-8") as destination:
            destination.write(text + "\n")
    print(f"\nТекст: {text or '[речь не распознана]'}")
    print(f"Распознавание и загрузка модели: {elapsed:.2f} с (без времени записи).")
    print(f"Результат: {output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        raise SystemExit(1)

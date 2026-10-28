from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from typing import Any

import requests
try:
    import speech_recognition as sr
except ImportError:
    sr = None


SERVER_URL = os.getenv("STT_SERVER_URL", "http://127.0.0.1:8000/transcribe")
TTS_SERVER_URL = os.getenv("TTS_SERVER_URL", "http://127.0.0.1:8030/synthesize")
ROBOT_BRIDGE_URL = os.getenv("ROBOT_BRIDGE_URL", "http://127.0.0.1:8020")
PUBLISH_PREVIEW = os.getenv("PUBLISH_PREVIEW", "1").casefold() in {"1", "true", "yes"}
MIC_INDEX = int(os.environ["MIC_INDEX"]) if os.getenv("MIC_INDEX") else None
MAX_PHRASE_SECONDS = float(os.getenv("MAX_PHRASE_SECONDS", "15"))
VOICE_WAIT_TIMEOUT = float(os.getenv("VOICE_WAIT_TIMEOUT", "10"))
REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "120"))
ENERGY_THRESHOLD = int(os.getenv("ENERGY_THRESHOLD", "200"))
PAUSE_THRESHOLD = float(os.getenv("PAUSE_THRESHOLD", "1.9"))
AMBIENT_NOISE_SECONDS = float(os.getenv("AMBIENT_NOISE_SECONDS", "1"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lightweight STT microphone client")
    parser.add_argument(
        "--show-all-text",
        action="store_true",
        help="Печатать распознанный текст, даже если wake word не найден",
    )
    parser.add_argument(
        "--speak-responses",
        action="store_true",
        help="Озвучивать reply_text через серверный Silero TTS",
    )
    return parser.parse_args()


@contextmanager
def suppress_native_stderr():
    """Hide noisy ALSA/JACK diagnostics while PyAudio opens a device."""
    sys.stderr.flush()
    stderr_fd = sys.stderr.fileno()
    saved_stderr_fd = os.dup(stderr_fd)
    try:
        with open(os.devnull, "w") as devnull:
            os.dup2(devnull.fileno(), stderr_fd)
            yield
    finally:
        sys.stderr.flush()
        os.dup2(saved_stderr_fd, stderr_fd)
        os.close(saved_stderr_fd)


def send_audio(audio: sr.AudioData) -> dict[str, Any]:
    response = requests.post(
        SERVER_URL,
        data=audio.get_wav_data(convert_rate=16000, convert_width=2),
        headers={"Content-Type": "audio/wav"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Сервер вернул некорректный JSON")
    return payload


def audio_player(path: str) -> list[str]:
    if sys.platform == "darwin" and shutil.which("afplay"):
        return ["afplay", path]
    if shutil.which("aplay"):
        return ["aplay", "-q", path]
    if shutil.which("ffplay"):
        return [
            "ffplay",
            "-nodisp",
            "-autoexit",
            "-loglevel",
            "error",
            path,
        ]
    raise RuntimeError("No afplay, aplay or ffplay executable was found")


def speak_pipeline_reply(payload: dict[str, Any]) -> None:
    pipeline = payload.get("pipeline")
    if not isinstance(pipeline, dict):
        return
    reply_text = str(pipeline.get("reply_text", "")).strip()
    if not reply_text:
        return

    temp_path = ""
    try:
        print(f"Reply: {reply_text}", flush=True)
        response = requests.post(
            TTS_SERVER_URL,
            json={"text": reply_text},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        if "audio/wav" not in content_type.casefold():
            raise ValueError(f"Unexpected TTS Content-Type: {content_type}")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as audio_file:
            audio_file.write(response.content)
            temp_path = audio_file.name
        subprocess.run(audio_player(temp_path), check=True)
    except (
        requests.RequestException,
        OSError,
        subprocess.SubprocessError,
        RuntimeError,
        ValueError,
    ) as error:
        print(f"TTS playback failed: {error}")
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def publish_preview_task(pipeline: dict[str, Any]) -> dict[str, Any] | None:
    if not PUBLISH_PREVIEW:
        return None
    execution = pipeline.get("execution")
    if not isinstance(execution, dict) or execution.get("state") != "AUTO_EXECUTE_DISABLED":
        return None
    preview = pipeline.get("preview")
    task = preview.get("task") if isinstance(preview, dict) else None
    if not isinstance(task, dict):
        raise ValueError("Сервер не вернул готовую задачу для публикации")
    try:
        response = requests.post(
            f"{ROBOT_BRIDGE_URL.rstrip('/')}/publish",
            json=task,
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        raise RuntimeError(f"Robot bridge недоступен: {error}") from error
    result = response.json()
    if not isinstance(result, dict):
        raise ValueError("Robot bridge вернул некорректный JSON")
    return result


def print_pipeline_result(payload: dict[str, Any]) -> None:
    error = payload.get("pipeline_error")
    if error:
        print(f"❌ Сервер не обработал команду: {error}")
        return
    pipeline = payload.get("pipeline")
    if not isinstance(pipeline, dict):
        print("⚠️ Сервер не вернул результат планирования.")
        return
    status = pipeline.get("pipeline_status")
    if status == "duplicate":
        print("🔁 Сервер отклонил недавний повтор команды.")
        return
    plan = pipeline.get("plan")
    if status == "confirmation_required" and isinstance(plan, dict):
        print(f"❓ Нужно уточнение: {plan.get('clarification_question', 'подтвердите команду')}")
        return
    if status == "rejected" or not isinstance(plan, dict):
        print(f"🚫 Команда отклонена: {pipeline.get('planner_message', 'неизвестная причина')}")
        return
    print(f"📋 План сервера: {plan.get('behavior')} — {plan.get('arguments', {})}")
    print_preview(pipeline.get("preview"))
    execution = pipeline.get("execution")
    if isinstance(execution, dict):
        print(f"🤖 Исполнение: {execution.get('state')} — {execution.get('message')}")
    published = publish_preview_task(pipeline)
    if published is not None:
        print(
            f"📤 ROS: {published.get('state')} — {published.get('message')}"
        )


def print_preview(preview: Any) -> None:
    if not isinstance(preview, dict):
        return
    if preview.get("available") is False:
        print(f"⚠️ Preview недоступен: {preview.get('error', 'неизвестная ошибка')}")
        return

    perception = preview.get("perception")
    if isinstance(perception, dict) and perception:
        print("📍 Координаты CV:")
        for name, value in perception.items():
            if not isinstance(value, dict):
                continue
            print(
                f"   {name}: x={value.get('x_mm')} мм, y={value.get('y_mm')} мм, "
                f"z={value.get('z_mm')} мм, confidence={value.get('score')}"
            )

    task = preview.get("task")
    steps = task.get("steps") if isinstance(task, dict) else None
    if not isinstance(steps, list):
        return
    print("🧭 Последовательность действий:")
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        action = step.get("action", "unknown")
        details = []
        if step.get("frame") is not None:
            details.append(f"frame={step['frame']}")
        for key in ("x", "y", "z", "dx", "dy", "dz", "seconds"):
            if step.get(key) is not None:
                unit = "с" if key == "seconds" else "мм"
                details.append(f"{key}={step[key]} {unit}")
        suffix = f" ({', '.join(details)})" if details else ""
        print(f"   {index}. {action}{suffix}")


def main() -> None:
    if sr is None:
        raise SystemExit(
            "Не установлен SpeechRecognition. Запустите клиент в окружении с микрофонными зависимостями."
        )
    args = parse_args()

    # Recognizer.listen provides a lightweight adaptive energy-based VAD: it
    # waits for speech and stops after a pause. No Whisper model is loaded here.
    recognizer = sr.Recognizer()
    recognizer.energy_threshold = ENERGY_THRESHOLD
    recognizer.dynamic_energy_threshold = True
    recognizer.pause_threshold = PAUSE_THRESHOLD

    try:
        with suppress_native_stderr():
            microphone = sr.Microphone(sample_rate=16000, device_index=MIC_INDEX)
            with microphone as source:
                recognizer.adjust_for_ambient_noise(
                    source,
                    duration=AMBIENT_NOISE_SECONDS,
                )
    except (OSError, AttributeError) as error:
        raise SystemExit(f"❌ Не удалось открыть микрофон: {error}") from error

    print("✅ Готово. VAD на клиенте, wake word проверяет сервер.")
    if args.show_all_text:
        print("📝 Диагностика: показываю весь распознанный текст.")
    print("Скажите wake word и команду одной фразой. Остановка: Ctrl+C")

    while True:
        print("\n🎙 Жду речь...", flush=True)

        try:
            with suppress_native_stderr():
                with microphone as source:
                    audio = recognizer.listen(
                        source,
                        timeout=VOICE_WAIT_TIMEOUT,
                        phrase_time_limit=MAX_PHRASE_SECONDS,
                    )

            print("🟡 Речь обнаружена. Отправляю на сервер...", flush=True)
            payload = send_audio(audio)

            transcript = str(payload.get("text", "")).strip()
            if args.show_all_text:
                print(f"📝 Распознано: {transcript or '—'}")

            if not payload.get("activated", False):
                print("🔇 Wake word не найден — сервер отклонил фразу.")
                continue

            command = str(payload.get("command", "")).strip()
            if not command:
                print("😶 Wake word найден, но команда пуста.")
                continue

            print(f"✅ Команда: {command}")
            print_pipeline_result(payload)
            if args.speak_responses:
                speak_pipeline_reply(payload)

        except sr.WaitTimeoutError:
            print("⏱ Речь не обнаружена за отведённое время.")
        except requests.RequestException as error:
            print(f"❌ Сервер недоступен: {error}")
        except KeyboardInterrupt:
            print("\n⛔ Клиент остановлен")
            return
        except Exception as error:
            print(f"❌ Ошибка: {error}")


if __name__ == "__main__":
    main()

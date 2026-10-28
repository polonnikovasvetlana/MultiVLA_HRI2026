import os
import sys
from contextlib import contextmanager


os.environ["PYNPUT_BACKEND"] = "dummy"

from whisper_mic import WhisperMic


@contextmanager
def suppress_native_stderr():
    """Скрывает служебные сообщения ALSA/JACK на время работы микрофона."""
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


class DebugWhisperMic(WhisperMic):
    def _WhisperMic__record_load(self, callback_arg, audio):
        print("\n🟡 Запись закончена. Распознаю...", flush=True)
        return super()._WhisperMic__record_load(callback_arg, audio)

print("⏳ Загружаю модель и настраиваю микрофон...", flush=True)

with suppress_native_stderr():
    mic = DebugWhisperMic(
        model="small",
        energy=200,
        dynamic_energy=True,
        pause=6.0,
        hallucinate_threshold=100,
    )

print("✅ Микрофон готов")
print("🟢 ГОВОРИТЕ. Для завершения помолчите 1 секунду.", flush=True)

try:
    with suppress_native_stderr():
        result = mic.listen(
            timeout=10,
            phrase_time_limit=15,
            try_again=False,
        )
except KeyboardInterrupt:
    print("\n⛔ Остановлено пользователем")
    raise SystemExit(130)
except Exception as error:
    print(f"\n❌ Ошибка микрофона: {error}")
    raise SystemExit(1)

if result is None:
    print("🔇 Запись слишком тихая. Говорите ближе к микрофону.")
elif result.startswith("Timeout:"):
    print("⏱ Речь не обнаружена за 10 секунд.")
else:
    print(f"✅ Результат: {result.strip()}")

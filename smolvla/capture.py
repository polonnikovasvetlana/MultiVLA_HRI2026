"""Camera ownership and asynchronous, one-episode-per-dataset persistence."""
from collections import deque
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import queue
import threading
import time
import traceback

from hardware import JOINT_IDS

SIDES = {"top": {"name": "Сверху", "color": "#f05252"},
         "left": {"name": "Слева", "color": "#548bff"},
         "right": {"name": "Справа", "color": "#42c893"}}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


class Camera:
    def __init__(self, device="/dev/video0", demo=False):
        self.device, self.demo = device, demo
        self.condition = threading.Condition()
        self.latest = None
        self.error = "Камера запускается"
        self.times = deque(maxlen=60)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="camera", daemon=True)

    def start(self):
        self.thread.start()

    def snapshot(self):
        with self.condition:
            return self.latest

    def status(self):
        with self.condition:
            age = (time.monotonic_ns() - self.latest["received_ns"]) / 1e9 if self.latest else None
            fps = (len(self.times)-1)/(self.times[-1]-self.times[0]) if len(self.times)>1 else 0
            return {"online": age is not None and age < .5, "fps": round(fps, 1),
                    "error": self.error, "age_s": age, "width": 1280, "height": 720}

    def _run(self):
        import cv2
        import numpy as np
        cap, sequence = None, 0
        while not self.stop.is_set():
            try:
                if not self.demo and cap is None:
                    cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
                    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                    cap.set(cv2.CAP_PROP_FPS, 30)
                    if not cap.isOpened():
                        raise RuntimeError("Камера недоступна. Проверьте USB и send_camera.py")
                if self.demo:
                    bgr = np.zeros((720, 1280, 3), dtype=np.uint8)
                    bgr[:] = (40, 32, 25)
                    cv2.circle(bgr, (180 + sequence * 7 % 900, 350), 90, (110, 190, 240), -1)
                    cv2.putText(bgr, "DEMO / NO ROBOT", (60, 80), 0, 1.4, (240, 240, 240), 2)
                    self.stop.wait(1/30)
                else:
                    ok, bgr = cap.read()
                    if not ok:
                        raise RuntimeError("Не удалось получить кадр камеры")
                received_ns, wall_ns = time.monotonic_ns(), time.time_ns()
                if bgr.shape[:2] != (720, 1280):
                    raise RuntimeError(f"Камера вернула {bgr.shape[:2]}, ожидается 720×1280")
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                ok, encoded = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 75])
                if not ok:
                    raise RuntimeError("Не удалось подготовить видео для браузера")
                sequence += 1
                with self.condition:
                    self.latest = {"rgb": rgb, "jpeg": encoded.tobytes(), "sequence": sequence,
                                   "received_ns": received_ns, "wall_ns": wall_ns}
                    self.times.append(time.monotonic())
                    self.error = None
                    self.condition.notify_all()
            except Exception as exc:
                with self.condition:
                    self.error = str(exc)
                if cap is not None:
                    cap.release()
                    cap = None
                self.stop.wait(1)
        if cap is not None:
            cap.release()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=5)


class Episode:
    def __init__(self, root, side, fps, calibrations, demo=False):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self.path = root / f"so101e_{stamp}_{side}"
        self.manifest = root / (self.path.name + ".capture.json")
        self.fps, self.side, self.demo = fps, side, demo
        self.calibrations = calibrations
        self.queue = queue.Queue(maxsize=60)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.done = threading.Event()
        self.frames = 0
        self.first_ns = self.last_ns = None
        self.error = None
        self.state = "starting"
        self.thread = threading.Thread(target=self._run, name="episode-writer", daemon=True)

    def start(self):
        self.thread.start()

    def enqueue(self, item):
        with self.lock:
            if self.stop.is_set() or not self.ready.is_set():
                return
            try:
                self.queue.put_nowait(item)
            except queue.Full:
                self.error = "Диск или кодировщик не успевает. Запись остановлена без скрытого пропуска кадров"
                self.stop.set()

    def finish(self, error=None):
        with self.lock:
            if self.done.is_set():
                return
            if error:
                self.error = error
            self.stop.set()
            self.state = "saving"

    def status(self):
        with self.lock:
            duration = (self.last_ns-self.first_ns)/1e9 + 1/self.fps if self.first_ns is not None else 0
            return {"id": self.path.name, "path": str(self.path), "side": self.side,
                    "state": self.state, "frames": self.frames,
                    "duration_s": duration, "error": self.error,
                    "queue_frames": self.queue.qsize()}

    def _run(self):
        dataset = None
        try:
            import numpy as np
            from lerobot.datasets import LeRobotDataset
            from lerobot.configs.video import RGBEncoderConfig
            from lerobot.utils.feature_utils import hw_to_dataset_features
            features = {
                **hw_to_dataset_features({**{j+".pos": float for j in JOINT_IDS},
                                          "front": (720,1280,3)}, prefix="observation", use_video=True),
                **hw_to_dataset_features({j+".pos": float for j in JOINT_IDS},
                                         prefix="action", use_video=True),
            }
            dataset = LeRobotDataset.create(
                "local/"+self.path.name, fps=self.fps, root=self.path, features=features,
                robot_type="so101e_follower", streaming_encoding=True, encoder_threads=2,
                rgb_encoder=RGBEncoderConfig(vcodec="h264", preset="ultrafast", crf=23))
            metadata = {"schema_version": 2, "id": self.path.name, "created_at_utc": utc_now(),
                        "approach_side": self.side, "side_reference": "fixed_camera_image",
                        "labeling_status": "unlabeled", "demo": self.demo, "fps": self.fps,
                        "camera": {"width":1280,"height":720,"color":"RGB","fourcc":"MJPG"},
                        "joint_order": list(JOINT_IDS), "calibrations": self.calibrations,
                        "versions": {p: importlib.metadata.version(p) for p in ("lerobot","av","torchcodec")},
                        "path": str(self.path), "state": "recording"}
            atomic_json(self.manifest, metadata)
            with self.lock:
                if not self.stop.is_set():
                    self.state = "recording"
                self.ready.set()
            with (self.path / "capture_timing.jsonl").open("w", buffering=1) as timing:
                while not self.stop.is_set() or not self.queue.empty():
                    try:
                        item = self.queue.get(timeout=.1)
                    except queue.Empty:
                        continue
                    # Empty language is a format placeholder; no task label is assigned.
                    dataset.add_frame({"observation.state": np.asarray(item["state"], dtype=np.float32),
                                       "action": np.asarray(item["action"], dtype=np.float32),
                                       "observation.images.front": item["image"], "task": ""})
                    with self.lock:
                        index = self.frames
                        self.frames += 1
                        self.first_ns = self.first_ns if self.first_ns is not None else item["tick_ns"]
                        self.last_ns = item["tick_ns"]
                    timing.write(json.dumps({"frame_index":index, **{k:v for k,v in item.items()
                                             if k not in ("state","action","image")}})+"\n")
            if self.frames:
                dataset.save_episode()
            dataset.finalize()
            dataset = None
            with self.lock:
                self.state = "error" if self.error else ("saved" if self.frames else "empty")
            metadata.update(self.status())
            metadata["approach_side"] = metadata.pop("side")
            metadata["finished_at_utc"] = utc_now()
            atomic_json(self.manifest, metadata)
        except Exception as exc:
            traceback.print_exc()
            with self.lock:
                self.error = str(exc)
                self.state = "error"
            atomic_json(self.manifest, {**self.status(), "approach_side": self.side,
                                        "demo": self.demo, "created_at_utc":utc_now()})
        finally:
            self.stop.set()
            if dataset is not None:
                try:
                    dataset.finalize()
                except Exception:
                    traceback.print_exc()
            self.done.set()

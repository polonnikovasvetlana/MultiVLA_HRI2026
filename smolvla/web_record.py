#!/usr/bin/env python3
"""Local web recorder: python smolvla/web_record.py --host 0.0.0.0 --port 8080."""
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import json
from pathlib import Path
import shutil
import signal
import threading
import time
import traceback
from urllib.parse import urlsplit

from capture import Camera, Episode, SIDES
from hardware import Robot, DemoRobot
from home import HomeStore, HomeMotion, leader_aligned, checked_positions


class Controller:
    def __init__(self, root, camera, robot_factory=Robot, fps=20, demo=False, home_file=None):
        self.root, self.camera = Path(root), camera
        self.root.mkdir(parents=True, exist_ok=True)
        self.robot_factory, self.fps, self.demo = robot_factory, fps, demo
        self.lock = threading.RLock()
        self.mode, self.error, self.side = "idle", None, "top"
        self.episode = None
        self.stop = threading.Event()
        self.thread = None
        self.ticks = deque(maxlen=60)
        self.home = HomeStore(home_file or self.root / "home_pose.json")
        self.positions = self.leader_positions = None
        self.positions_ns = 0
        self.home_motion = self.home_target = None
        self.home_progress = 0
        self.home_cancel = self.resume_requested = False
        self.home_error = None

    def _between_episodes(self):
        if self.episode and not self.episode.done.is_set():
            raise ValueError("Сначала завершите и сохраните текущий эпизод")

    def save_home(self):
        with self.lock:
            self._between_episodes()
            if self.mode not in ("connected", "paused"):
                raise ValueError("Сначала включите управление и поставьте руку в стартовую позу")
            if time.monotonic_ns() - self.positions_ns > 300_000_000:
                raise ValueError("Нет свежего положения руки")
            self.home.save(self.positions, self.robot.calibrations)
            self.home_error = None

    def go_home(self):
        with self.lock:
            self._between_episodes()
            if self.mode not in ("connected", "paused"):
                raise ValueError("Сначала включите управление")
            target = self.home.target(self.robot.calibrations)
            if time.monotonic_ns() - self.positions_ns > 300_000_000:
                raise ValueError("Нет свежего положения руки")
            checked_positions(self.positions)
            self.home_target, self.home_motion, self.home_progress = target, None, 0
            self.home_cancel = self.resume_requested = False
            self.home_error = None
            self.mode = "homing"

    def cancel_home(self):
        with self.lock:
            if self.mode != "homing":
                raise ValueError("Рука сейчас не едет в home")
            self.home_cancel = True

    def resume(self):
        with self.lock:
            if self.mode != "paused":
                raise ValueError("Управление сейчас не на паузе")
            if not leader_aligned(self.positions, self.leader_positions):
                raise ValueError("Сначала совместите позу leader с текущей позой follower")
            self.resume_requested = True

    def select_side(self, side):
        if side not in SIDES:
            raise ValueError("Выберите сторону: top, left или right")
        with self.lock:
            if self.episode and not self.episode.done.is_set():
                raise ValueError("Сторона фиксируется на весь эпизод. Сначала завершите запись")
            self.side = side

    def connect(self):
        with self.lock:
            if self.mode not in ("idle", "error") or (self.thread and self.thread.is_alive()):
                raise ValueError("Управление уже включено или подключается")
            self.mode, self.error = "connecting", None
            self.positions = self.leader_positions = None
            self.positions_ns = 0
            self.home_motion = None
            self.home_cancel = self.resume_requested = False
            self.home_error = None
            self.stop = threading.Event()
            self.ticks.clear()
            self.thread = threading.Thread(target=self._control, name="robot-control", daemon=True)
            self.thread.start()

    def disconnect(self):
        with self.lock:
            self.stop.set()
            if self.episode and not self.episode.done.is_set():
                self.episode.finish()
            if self.mode in ("connected", "connecting", "homing", "paused"):
                self.mode = "stopping"

    def record(self, side):
        with self.lock:
            if self.mode != "connected":
                raise ValueError("Сначала включите управление")
            if not self.camera.status()["online"]:
                raise ValueError("Нет свежего изображения камеры")
            self.select_side(side)
            self.episode = Episode(self.root, self.side, self.fps,
                                   self.robot.calibrations, demo=self.demo)
            self.episode.start()

    def stop_recording(self):
        with self.lock:
            if not self.episode or self.episode.done.is_set():
                raise ValueError("Сейчас нет активного эпизода")
            self.episode.finish()

    def _control(self):
        robot = self.robot_factory()
        try:
            robot.connect()
            with self.lock:
                self.robot = robot
                self.mode = "stopping" if self.stop.is_set() else "connected"
            while not self.stop.is_set():
                tick_ns = time.monotonic_ns()
                frame = self.camera.snapshot()
                with self.lock:
                    mode = self.mode
                if mode == "connected":
                    state, action, state_ns, action_ns = robot.sample()
                    leader = action
                else:
                    state_ns = time.monotonic_ns()
                    state, leader = robot.read_state(), robot.read_leader()
                    action, action_ns = None, time.monotonic_ns()
                    with self.lock:
                        if mode == "homing" and self.mode == "homing":
                            if self.home_cancel:
                                robot.send_target(state)
                                self.mode = "paused"
                                self.home_cancel = False
                            else:
                                now = time.monotonic()
                                if self.home_motion is None:
                                    self.home_motion = HomeMotion(state, self.home_target, now)
                                robot.send_target(self.home_motion.command(now))
                                self.home_progress = self.home_motion.progress(now)
                                if self.home_progress == 1 and self.home_motion.reached(state):
                                    self.mode = "paused"
                                elif now > self.home_motion.started_at + self.home_motion.duration + 5:
                                    robot.send_target(state)
                                    self.mode = "paused"
                                    self.home_error = "Рука не достигла home. Выезд остановлен"
                        elif mode == "paused" and self.resume_requested:
                            # Recheck live values on the serial thread, not only the HTTP snapshot.
                            if leader_aligned(state, leader):
                                self.mode = "connected"
                                self.home_error = None
                            else:
                                self.home_error = "Совместите leader с follower, затем продолжите управление"
                            self.resume_requested = False
                with self.lock:
                    self.ticks.append(time.monotonic())
                    self.positions, self.leader_positions = list(state), list(leader)
                    self.positions_ns = time.monotonic_ns()
                    episode = self.episode
                if mode == "connected" and episode and episode.ready.is_set() and not episode.stop.is_set():
                    if frame is None or (tick_ns-frame["received_ns"]) > 500_000_000:
                        episode.finish("Камера перестала присылать свежие кадры")
                    else:
                        episode.enqueue({"image": frame["rgb"], "state": state, "action": action,
                                         "tick_ns": tick_ns, "state_read_ns": state_ns,
                                         "action_sent_ns": action_ns,
                                         "camera_sequence": frame["sequence"],
                                         "camera_received_ns": frame["received_ns"],
                                         "camera_wall_ns": frame["wall_ns"]})
                self.stop.wait(max(0, 1/self.fps-(time.monotonic_ns()-tick_ns)/1e9))
        except Exception as exc:
            traceback.print_exc()
            with self.lock:
                self.error = str(exc)
                if self.episode and not self.episode.done.is_set():
                    self.episode.finish("Управление остановлено: "+str(exc))
        finally:
            try:
                robot.close()
            except Exception as exc:
                with self.lock:
                    self.error = "Ошибка отключения моторов: "+str(exc)
            with self.lock:
                self.mode = "error" if self.error else "idle"

    def history(self):
        result = []
        for path in sorted(self.root.glob("*.capture.json"), reverse=True)[:100]:
            try:
                item = json.loads(path.read_text())
                if item.get("state") in ("saved", "error"):
                    result.append({k:item.get(k) for k in (
                        "id", "approach_side", "frames", "duration_s", "state", "path", "error")})
            except (OSError, ValueError):
                continue
        return result

    def status(self):
        with self.lock:
            fps = (len(self.ticks)-1)/(self.ticks[-1]-self.ticks[0]) if len(self.ticks)>1 else 0
            home_error = self.home_error or self.home.error
            home_valid = bool(self.home.pose)
            if home_valid and self.mode in ("connected", "paused", "homing"):
                try:
                    self.home.target(self.robot.calibrations)
                except ValueError as exc:
                    home_valid, home_error = False, str(exc)
            return {"control": self.mode, "error": self.error, "side": self.side,
                    "control_fps": round(fps, 1) if self.mode=="connected" else 0,
                    "target_fps": self.fps, "camera": self.camera.status(),
                    "episode": self.episode.status() if self.episode else None,
                    "root": str(self.root), "free_gb": round(shutil.disk_usage(self.root).free/1e9,1),
                    "demo": self.demo,
                    "home": {"saved": bool(self.home.pose), "valid": home_valid,
                             "error": home_error, "progress": self.home_progress,
                             "can_resume": self.mode == "paused" and
                             leader_aligned(self.positions, self.leader_positions),
                             "saved_at_utc": self.home.pose.get("saved_at_utc") if self.home.pose else None}}

    def close(self):
        self.disconnect()
        if self.thread:
            self.thread.join(timeout=10)
        if self.episode:
            self.episode.finish()
            self.episode.thread.join(timeout=45)
        self.camera.close()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, controller):
        self.controller = controller
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, message, *args):
        if self.path not in ("/api/status", "/api/episodes"):
            super().log_message(message, *args)

    def respond(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        controller = self.server.controller
        if path == "/":
            body = Path(__file__).with_name("web.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/status":
            self.respond(controller.status())
        elif path == "/api/episodes":
            self.respond(controller.history())
        elif path == "/video":
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            sequence = -1
            try:
                while not controller.camera.stop.is_set():
                    with controller.camera.condition:
                        controller.camera.condition.wait_for(
                            lambda: controller.camera.latest is not None and
                            controller.camera.latest["sequence"] != sequence, timeout=1)
                        frame = controller.camera.latest
                    if frame is None or frame["sequence"] == sequence:
                        continue
                    sequence = frame["sequence"]
                    jpeg = frame["jpeg"]
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "+
                                     str(len(jpeg)).encode()+b"\r\n\r\n"+jpeg+b"\r\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass
        else:
            self.respond({"error":"Не найдено"}, 404)

    def do_POST(self):
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
            self.respond({"error":"Запрос должен быть отправлен с этой страницы"},403)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0 or size > 4096 or "application/json" not in self.headers.get("Content-Type", ""):
                raise ValueError("Ожидается небольшой JSON-запрос")
            data = json.loads(self.rfile.read(size)) if size else {}
            controller = self.server.controller
            path = urlsplit(self.path).path
            if path == "/api/control/start":
                controller.connect()
            elif path == "/api/control/stop":
                controller.disconnect()
            elif path == "/api/control/resume":
                controller.resume()
            elif path == "/api/home/save":
                controller.save_home()
            elif path == "/api/home/go":
                controller.go_home()
            elif path == "/api/home/cancel":
                controller.cancel_home()
            elif path == "/api/side":
                controller.select_side(data.get("side"))
            elif path == "/api/record/start":
                controller.record(data.get("side", controller.side))
            elif path == "/api/record/stop":
                controller.stop_recording()
            else:
                self.respond({"error":"Не найдено"},404)
                return
            self.respond(controller.status(),202)
        except (ValueError, TypeError) as exc:
            self.respond({"error":str(exc)},409)
        except Exception as exc:
            traceback.print_exc()
            self.respond({"error":str(exc)},500)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--home-file", type=Path, help="Saved, taught home pose")
    parser.add_argument("--demo", action="store_true", help="Synthetic camera and robot; never opens motors")
    args = parser.parse_args()
    root = args.root or Path.home()/"datasets"/("web_demo" if args.demo else "grasp_capture")
    camera = Camera(demo=args.demo)
    home_file = args.home_file or (root/"home_pose.json" if args.demo else
                                  Path.home()/".config/multivla/so101e_home.json")
    controller = Controller(root, camera, DemoRobot if args.demo else Robot, demo=args.demo,
                            home_file=home_file)
    server = Server((args.host,args.port), controller)
    camera.start()
    def shutdown(*_):
        threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,shutdown)
    signal.signal(signal.SIGINT,shutdown)
    print(f"Recorder: http://{args.host}:{args.port} | Data: {root}",flush=True)
    try:
        server.serve_forever()
    finally:
        controller.close()
        server.server_close()


if __name__ == "__main__":
    main()

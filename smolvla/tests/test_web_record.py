"""Control lifecycle and persistence guards; these tests never open motor drivers."""
from pathlib import Path
import json
import queue
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web_record import Controller, Server
from capture import Episode
from home import HomeMotion, HomeStore


class CameraStub:
    def __init__(self):
        self.stop = threading.Event()

    def snapshot(self):
        return {"rgb": None, "received_ns": time.monotonic_ns(),
                "wall_ns": time.time_ns(), "sequence": 1}

    def status(self):
        return {"online": True, "fps": 30, "error": None}

    def close(self):
        self.stop.set()


class RobotStub:
    calibrations = {}

    def __init__(self):
        self.connected = False
        self.closed = False
        self.positions = list(range(7))
        self.leader_positions = list(range(7))
        self.commands = []

    def connect(self):
        self.connected = True

    def sample(self):
        tick = time.monotonic_ns()
        state = self.read_state()
        return state, self.send_target(self.read_leader()), tick, tick

    def read_state(self):
        return self.positions[:]

    def read_leader(self):
        return self.leader_positions[:]

    def send_target(self, positions):
        self.positions = list(positions)
        self.commands.append(self.positions[:])
        return self.positions[:]

    def close(self):
        self.closed = True


class EpisodeStub:
    def __init__(self, root, side, fps, calibrations, demo=False):
        self.side = side
        self.frames = 0
        self.done, self.ready, self.stop = (threading.Event() for _ in range(3))
        self.thread = threading.Thread(target=lambda: None)

    def start(self):
        self.thread.start()
        self.ready.set()

    def enqueue(self, item):
        self.frames += 1

    def finish(self, error=None):
        self.stop.set()
        self.done.set()

    def status(self):
        return {"frames": self.frames, "side": self.side,
                "state": "saved" if self.done.is_set() else "recording"}


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("Timed out")
        time.sleep(.01)


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.robot = RobotStub()
        self.created = 0
        def factory():
            self.created += 1
            return self.robot
        self.controller = Controller(Path(self.directory.name), CameraStub(), factory)
        self.patcher = patch("web_record.Episode", EpisodeStub)
        self.patcher.start()

    def tearDown(self):
        self.controller.close()
        self.patcher.stop()
        self.directory.cleanup()

    def connect(self):
        self.controller.connect()
        wait_for(lambda: self.controller.mode == "connected" and self.controller.positions is not None)

    def test_opening_page_does_not_construct_or_connect_robot(self):
        server = Server(("127.0.0.1", 0), self.controller)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            address = "http://127.0.0.1:" + str(server.server_port)
            with urllib.request.urlopen(address + "/api/status") as response:
                self.assertEqual(json.load(response)["control"], "idle")
            with urllib.request.urlopen(address) as response:
                self.assertIn("Начать эпизод", response.read().decode())
            self.assertEqual(self.created, 0)
            self.assertFalse(self.robot.connected)
            request = urllib.request.Request(address + "/api/control/start", data=b"{}",
                                             headers={"Content-Type":"application/json",
                                                      "Origin":"http://another-site.example"})
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request)
            self.assertEqual(caught.exception.code, 403)
            self.assertEqual(self.created, 0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_record_requires_control_and_freezes_side_until_stop(self):
        with self.assertRaises(ValueError):
            self.controller.record("top")
        self.connect()
        self.controller.record("left")
        wait_for(lambda: self.controller.episode.frames > 0)
        with self.assertRaises(ValueError):
            self.controller.select_side("right")
        with self.assertRaises(ValueError):
            self.controller.record("top")
        self.controller.stop_recording()
        self.controller.select_side("right")
        self.assertEqual(self.controller.episode.side, "left")
        self.assertEqual(self.controller.side, "right")

    def test_disconnect_finishes_episode_and_closes_robot(self):
        self.connect()
        self.controller.record("right")
        self.controller.disconnect()
        wait_for(lambda: self.robot.closed)
        self.assertTrue(self.controller.episode.done.is_set())
        self.assertEqual(self.controller.mode, "idle")

    def test_saved_history_survives_new_controller(self):
        root = Path(self.directory.name)
        (root / "example.capture.json").write_text(json.dumps({
            "id":"example", "state":"saved", "approach_side":"left", "frames":100}))
        restored = Controller(root, CameraStub(), lambda: self.fail("Must not open hardware"))
        try:
            self.assertEqual(restored.history()[0]["approach_side"], "left")
            self.assertEqual(restored.history()[0]["frames"], 100)
        finally:
            restored.close()

    def test_full_writer_queue_stops_with_an_explicit_error(self):
        episode = Episode(Path(self.directory.name), "top", 20, {})
        episode.ready.set()
        episode.queue = queue.Queue(maxsize=1)
        episode.enqueue({"marker":1})
        episode.enqueue({"marker":2})
        self.assertTrue(episode.stop.is_set())
        self.assertIn("не успевает", episode.error)
        self.assertEqual(episode.queue.get_nowait()["marker"], 1)

    def test_home_requires_taught_pose_and_is_forbidden_during_recording(self):
        self.assertFalse(self.controller.status()["home"]["saved"])
        self.connect()
        with self.assertRaises(ValueError):
            self.controller.go_home()
        self.controller.save_home()
        self.controller.record("top")
        with self.assertRaises(ValueError):
            self.controller.go_home()
        with self.assertRaises(ValueError):
            self.controller.save_home()
        self.assertEqual(self.controller.mode, "connected")

    def test_home_stays_paused_and_cannot_jump_back_to_unaligned_leader(self):
        self.connect()
        self.controller.save_home()
        home = self.controller.positions[:]
        self.robot.leader_positions = [v+10 for v in home]
        wait_for(lambda: self.controller.positions == self.robot.leader_positions)
        with patch.object(HomeMotion, "MIN_DURATION", .15), patch.object(HomeMotion, "MAX_SPEED", 1e6):
            self.controller.go_home()
            with self.assertRaises(ValueError):
                self.controller.record("left")
            wait_for(lambda: self.controller.mode == "paused")
        self.assertEqual(self.robot.positions, home)
        command_count = len(self.robot.commands)
        time.sleep(.12)
        self.assertEqual(len(self.robot.commands), command_count)
        with self.assertRaises(ValueError):
            self.controller.resume()
        self.robot.leader_positions = home[:]
        wait_for(lambda: self.controller.status()["home"]["can_resume"])
        self.controller.resume()
        wait_for(lambda: self.controller.mode == "connected")
        self.assertEqual(self.robot.positions, home)

    def test_cancel_home_holds_current_pose_and_does_not_resume_tracking(self):
        self.connect()
        self.controller.save_home()
        self.robot.leader_positions = [value+20 for value in self.robot.positions]
        wait_for(lambda: self.controller.positions == self.robot.leader_positions)
        self.controller.go_home()
        wait_for(lambda: self.controller.home_motion is not None)
        self.controller.cancel_home()
        wait_for(lambda: self.controller.mode == "paused")
        held = self.robot.positions[:]
        self.robot.leader_positions = [v+20 for v in held]
        count = len(self.robot.commands)
        time.sleep(.12)
        self.assertEqual(self.robot.positions, held)
        self.assertEqual(len(self.robot.commands), count)

    def test_taught_home_survives_restart_and_rejects_changed_calibration(self):
        self.connect()
        self.controller.save_home()
        restored = HomeStore(self.controller.home.path)
        self.assertEqual(restored.target({}), self.controller.positions)
        with self.assertRaisesRegex(ValueError, "калибровки"):
            restored.target({"changed":True})


class TrajectoryTests(unittest.TestCase):
    def test_trajectory_stays_in_bounds_and_obeys_speed_limit(self):
        start = [-100, 90, -60, 40, -80, 0, 70]
        end = [100, -90, 70, -50, 90, 100, -70]
        motion = HomeMotion(start, end, 0)
        self.assertEqual(motion.command(0), start)
        self.assertEqual(motion.command(motion.duration), end)
        last = motion.command(0)
        t = .05
        while t <= motion.duration:
            current = motion.command(t)
            for a, b, lo, hi in zip(last,current,start,end):
                self.assertLessEqual(abs(b-a)/.05, HomeMotion.MAX_SPEED+.001)
                self.assertTrue(min(lo,hi)-.001 <= b <= max(lo,hi)+.001)
            last = current
            t += .05

    def test_invalid_pose_never_becomes_a_trajectory(self):
        valid = [0,0,0,0,0,50,0]
        for invalid in ([0]*6, [float("nan")]*7, [101]*7):
            with self.assertRaises(ValueError):
                HomeMotion(valid, invalid, 0)


if __name__ == "__main__":
    unittest.main()

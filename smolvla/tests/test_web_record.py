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

    def connect(self):
        self.connected = True

    def sample(self):
        tick = time.monotonic_ns()
        return list(range(7)), list(range(7)), tick, tick

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
        wait_for(lambda: self.controller.mode == "connected")

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


if __name__ == "__main__":
    unittest.main()

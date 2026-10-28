# SO101E web recorder

Branch: `feature/smolvla`. The browser controls unlimited, manually ended episodes.
No object or task inputs are required. Only the approach side is selected.

On Jetson:

```bash
source ~/venvs/lerobot/bin/activate
cd ~/MultiVLA_HRI2026
python smolvla/web_record.py
```

Open **http://192.168.50.239:8080** from a computer on the same network.

To run in the background with the included user service:

```bash
mkdir -p ~/.config/systemd/user
cp smolvla/smolvla-recorder.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user start smolvla-recorder
```

Use `systemctl --user stop smolvla-recorder` to stop and disconnect,
`systemctl --user restart smolvla-recorder` to restart, and
`journalctl --user -u smolvla-recorder -n 50` to inspect logs. Start the service
or the foreground command, not both. The service is not enabled for automatic
startup by these commands.

1. Check the live camera and select **Сверху / Слева / Справа**.
2. Click **Включить управление**. The follower immediately follows the leader.
3. Click **Начать эпизод** and demonstrate the movement with the leader.
4. Click **Завершить и сохранить** when finished. There is no duration or episode-count limit.
5. Select another side or start another episode. Click **Отключить управление** when done.

Opening the page starts camera preview only. Serial ports are opened only after
clicking the control button. Stop any separate camera sender/teleop before using
this recorder. Disconnecting control also finishes the current episode and
disables motor torque. Closing the browser tab does not stop the server or robot;
use the disconnect button. Stop the server with Ctrl+C to flush pending writes
and disconnect the hardware.

## Data

Default root: **`~/datasets/grasp_capture/`**. Each start/stop creates a new,
single-episode LeRobot dataset, for example:

```text
grasp_capture/
  so101e_20261028_160000_123456_left/
    data/                    # 7D measured state + effective sent action
    videos/                  # raw RGB H.264, 1280x720
    meta/                    # LeRobot metadata
    capture_timing.jsonl     # camera sequence + receive/read/send timestamps per frame
  so101e_20261028_160000_123456_left.capture.json
```

The adjacent capture JSON stores `approach_side`, duration, frame count,
calibration snapshots, and labeling status `unlabeled`. Copy it together with the
dataset directory. Left/right refer to the fixed camera image. The side cannot
change during a starting, active, or saving episode. RGB is not tinted; the
recorded side is available for later SAM3 prompting. No object label is assigned.
LeRobot requires a task field in its format; it is an empty string placeholder,
not a task annotation. Actual object/task annotation is deferred.

Camera receive timestamps are measured after OpenCV returns a frame, not hardware
exposure timestamps. Timing JSONL preserves actual cadence and repeated camera
sequence numbers for later inspection/resampling. Default control/data rate is
20 Hz; camera capture is 30 Hz. The writer runs off the control thread. A full
writer queue stops recording with a visible error rather than silently dropping
samples. Camera loss also stops the episode. Saved/error episodes appear in the
browser history, including after restarting the server. A failed episode needs
inspection before training.

The app uses Python's HTTP server, OpenCV and the installed LeRobot/PyAV stack;
no frontend build, cloud hosting or new web framework dependency is required.
It listens on the local network. POST requests from a different browser origin
are rejected. This is a local lab tool, not an internet-facing service.

Options: `--host`, `--port`, `--root`. `record_raw.py` is an alias for the web entry
point; the previous task/object CLI has been replaced.

## Verification without moving motors

```bash
python smolvla/web_record.py --demo --port 8081 --root /tmp/smolvla-demo-data
python -m unittest discover -s smolvla/tests -v
```

Demo mode uses synthetic frames and joint vectors and never opens motor drivers.
Demo captures are marked `demo: true` and must not be used as demonstrations.

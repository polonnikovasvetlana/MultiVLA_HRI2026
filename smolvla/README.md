# Raw SO101E pilot collection

Working branch: `feature/smolvla`.

This launcher is standalone: it uses the installed SO101E plugins and calibration
files without importing source files from `feature/robot`. Robot port and ID
defaults are declared at the top of `record_raw.py`.

Run on Jetson from an interactive terminal:

```bash
source ~/venvs/lerobot/bin/activate
cd ~/MultiVLA_HRI2026
python smolvla/record_raw.py \
  --target-object cube \
  --approach-side top \
  --task "Pick up the cube and place it in the tray."
```

Replace the object and task with the actual demonstration. Approach side is
top, left, or right; keep the camera fixed and interpret left/right relative
to that camera. The operator must demonstrate the selected approach.

Defaults: 3 episodes, 30 seconds each, 15 seconds reset, dataset/control 20 Hz,
camera MJPG 1280x720 at 30 FPS, CPU H.264 ultrafast CRF 23, two encoder threads.
The follower follows the leader immediately when the recorder connects.
Only this recorder should own the serial ports and camera; stop any separate
teleop or camera sender first.

Controls in the focused interactive terminal:
- n / right arrow: finish the episode or reset interval early.
- r / left arrow: discard and redo an episode.
- q / Escape: finish the session.

Each launch creates a new dataset under ~/datasets. The adjacent .session.json
stores target object, approach side, task, joint order, calibration snapshots
and hashes, software versions, and recorder arguments. Keep this file with the
dataset when copying it. The side/object apply to the entire session.

Use --dry-run to validate configuration and print the command without opening
hardware. Other options: --num-episodes, --episode-time-s, --reset-time-s, --fps,
--root. Existing datasets are never overwritten; resuming is not implemented.

The video is raw RGB. SAM3 and its HTTP server are not required during collection.
Later processing must segment the actual saved frames and use the stored side.
LeRobot supplies standard frame/episode indices and dataset timestamps; this
launcher does not log sensor capture timestamps, online SAM3 IDs, or success
labels per episode. Those additions are needed for experiments on live latency
and for automated quality review. Review the pilot before bulk collection.

Installation and tests completed on 2026-10-28:
- LeRobot v0.6.1 core_scripts and feetech extras in ~/venvs/lerobot.
- PyAV 15.1.0 H.264 encoding and TorchCodec 0.11.1 decoding work.
- Added system libavdevice60, libavfilter9, libpostproc57; no driver update.
- Two synthetic episodes (60 frames) round-trip through PyAV and TorchCodec.
- Real camera + read-only state collection: 100 frames at 19.94 Hz, 0 cycles over
  50 ms. Diagnostic recordings sent no robot commands and are not training data.
- Launcher dry run and LeRobot config parsing pass. Live operator-driven
  demonstration collection remains to be performed.

Environment snapshot, install logs and diagnostic datasets:
~/so101_backups/record_setup_20261028_142837/

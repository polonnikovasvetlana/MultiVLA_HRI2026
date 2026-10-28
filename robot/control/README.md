# SO101E tools

These commands are separate from the stock `so101_follower` and
`so101_leader` setup. They use the custom seven-motor plugin and dedicated
calibration IDs, so the original six-motor calibration files remain intact.

Motor map:

1. `shoulder_pan`
2. `shoulder_lift`
3. `elbow_flex`
4. `wrist_flex`
5. `wrist_roll`
6. `gripper`
7. `elbow_rotate`

Commands:

```bash
python ~/so101e_tools/check.py
python ~/so101e_tools/calibrate.py leader
python ~/so101e_tools/calibrate.py follower
python ~/so101e_tools/teleoperate.py
```

The teleoperation launcher refuses to start unless:

- both buses contain all seven expected motors;
- both dedicated calibration files are valid.

The leader and follower do not need to be aligned by hand. Teleoperation uses
the same startup and control flow as the official `lerobot-teleoperate` command:
it connects both devices and immediately sends the leader's full current pose
to the follower at 60 Hz. There is no slow startup interpolation and no
per-cycle relative-target clipping. Keep the follower's workspace clear before
starting. `Ctrl+C` stops teleoperation and disables follower torque.

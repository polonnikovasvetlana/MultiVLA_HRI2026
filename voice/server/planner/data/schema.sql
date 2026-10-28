CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    behavior_tree TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1))
);

CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    object_type TEXT,
    rule_text TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1))
);

CREATE TABLE IF NOT EXISTS object_aliases (
    alias TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workspace_limits (
    frame_id TEXT PRIMARY KEY,
    min_x REAL NOT NULL,
    max_x REAL NOT NULL,
    min_y REAL NOT NULL,
    max_y REAL NOT NULL,
    min_z REAL NOT NULL,
    max_z REAL NOT NULL
);

INSERT OR IGNORE INTO skills VALUES
    ('pick', 'Pick a known object', 'pick.xml', 1),
    ('place', 'Place the currently held object', 'place.xml', 1),
    ('pick_and_place', 'Pick a known object and place it at a known destination', 'pick_and_place.xml', 1),
    ('move_to', 'Move the end effector to an explicit pose', 'move_to.xml', 1),
    ('home', 'Move the robot to its configured home pose', 'home.xml', 1),
    ('stop', 'Cancel active motion and enter the safe stopped state', 'stop.xml', 1);

INSERT OR IGNORE INTO rules(action, object_type, rule_text, priority) VALUES
    ('pick', '*', 'Approach the object from above.', 10),
    ('pick', 'cup', 'Do not grasp a cup by its handle.', 20),
    ('place', '*', 'Open the gripper only after the place pose is reached.', 10),
    ('move_to', '*', 'Reject poses outside the configured workspace.', 100),
    ('stop', '*', 'Stop bypasses the LLM and cancels active motion immediately.', 100);

INSERT OR IGNORE INTO object_aliases VALUES
    ('кружка', 'cup'),
    ('чашка', 'cup'),
    ('стакан', 'cup'),
    ('красная кружка', 'red_cup'),
    ('красную кружку', 'red_cup'),
    ('red cup', 'red_cup'),
    ('синий поднос', 'blue_tray'),
    ('синего подноса', 'blue_tray'),
    ('синий поднос', 'blue_tray'),
    ('blue tray', 'blue_tray');

INSERT OR IGNORE INTO object_aliases VALUES
    ('мышь', 'mouse'),
    ('мышку', 'mouse'),
    ('синяя мышь', 'blue_mouse'),
    ('синюю мышь', 'blue_mouse'),
    ('blue mouse', 'blue_mouse'),
    ('светофор', 'traffic_light'),
    ('синий светофор', 'blue_traffic_light'),
    ('синего светофора', 'blue_traffic_light'),
    ('blue traffic light', 'blue_traffic_light');

INSERT OR IGNORE INTO workspace_limits VALUES
    ('base_link', -0.5, 0.5, -0.5, 0.5, 0.0, 0.6);

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = Path(os.getenv("PLANNER_DB_PATH", str(PACKAGE_DIR / "data" / "planner.db")))
SCHEMA_PATH = PACKAGE_DIR / "data" / "schema.sql"


def normalize_name(value: str) -> str:
    return " ".join(re.sub(r"[^\w\s-]", " ", value.casefold()).split())


class PlannerRepository:
    def __init__(self, path: str | Path = DEFAULT_DB_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    def enabled_skills(self) -> list[dict[str, str]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, description, behavior_tree FROM skills WHERE enabled = 1 ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    def skill_enabled(self, skill_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM skills WHERE id = ? AND enabled = 1", (skill_id,)
            ).fetchone()
        return row is not None

    def aliases(self) -> dict[str, str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT alias, canonical_name FROM object_aliases"
            ).fetchall()
        return {normalize_name(row["alias"]): row["canonical_name"] for row in rows}

    def canonical_name(self, value: str) -> str:
        normalized = normalize_name(value)
        return self.aliases().get(normalized, normalized.replace(" ", "_"))

    def matching_entities(
        self, text: str, available_objects: list[str] | None = None
    ) -> list[str]:
        normalized = normalize_name(text)
        matches: list[tuple[int, int, int, str]] = []
        aliases = self.aliases()
        for object_name in available_objects or []:
            # Perception uses canonical identifiers such as ``cyan_toy``, while
            # a spoken command naturally contains "cyan toy".  Scene objects
            # are valid temporary aliases even when they are not in SQLite.
            spoken_name = normalize_name(re.sub(r"[_-]+", " ", object_name))
            if spoken_name:
                aliases.setdefault(spoken_name, self.canonical_name(object_name))

        for alias, canonical in aliases.items():
            for match in re.finditer(rf"(?<!\w){re.escape(alias)}(?!\w)", normalized):
                matches.append((match.start(), match.end(), -len(alias), canonical))
        matches.sort(key=lambda item: (item[0], item[2]))
        result: list[str] = []
        occupied: list[tuple[int, int]] = []
        for start, end, _, canonical in matches:
            if any(start < used_end and end > used_start for used_start, used_end in occupied):
                continue
            occupied.append((start, end))
            if canonical not in result:
                result.append(canonical)
        return result

    def rules(self, action: str | None = None, object_type: str | None = None) -> list[str]:
        with self.connect() as connection:
            if action is None:
                rows = connection.execute(
                    """
                    SELECT rule_text FROM rules
                    WHERE enabled = 1
                    ORDER BY priority DESC, id ASC
                    LIMIT 20
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT rule_text FROM rules
                    WHERE enabled = 1
                      AND (action = ? OR action = '*')
                      AND (object_type = ? OR object_type = '*' OR object_type IS NULL)
                    ORDER BY priority DESC, id ASC
                    LIMIT 20
                    """,
                    (action, object_type or "*"),
                ).fetchall()
        return [row["rule_text"] for row in rows]

    def workspace(self, frame_id: str) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM workspace_limits WHERE frame_id = ?", (frame_id,)
            ).fetchone()

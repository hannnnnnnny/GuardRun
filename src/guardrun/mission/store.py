"""Saving missions under ``.guardrun/missions`` so they survive restarts."""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path
from typing import Any

from guardrun.mission.plan import Attempt, Mission, PlanError, Task, TaskStatus, check_graph
from guardrun.safety.fileio import write_text

# Mission ids become file names, so they are restricted to a safe alphabet.
MISSION_ID = re.compile(r"^[a-f0-9]{12}$")
MAX_MISSION_BYTES = 20 * 1024 * 1024


class MissionError(PlanError):
    """Raised for unknown, malformed, or unreadable missions."""


def mission_to_dict(mission: Mission) -> dict[str, Any]:
    data = dataclasses.asdict(mission)
    for task in data["tasks"]:
        task["status"] = task["status"].value
    return data


def _task_from_record(raw: dict[str, Any]) -> Task:
    attempts = [Attempt(**attempt) for attempt in raw.pop("attempts", [])]
    status = TaskStatus(raw.pop("status", "pending"))
    return Task(**raw, status=status, attempts=attempts)


def mission_from_dict(data: dict[str, Any]) -> Mission:
    """Rebuild a saved mission; any structural problem is a :class:`MissionError`."""
    try:
        record = dict(data)
        tasks = [_task_from_record(dict(raw)) for raw in record.pop("tasks")]
        mission = Mission(tasks=tasks, **record)
        check_graph(mission.tasks)
    except (KeyError, TypeError, ValueError, PlanError) as exc:
        raise MissionError(f"mission record is corrupt: {exc}") from exc
    return mission


class MissionStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, mission_id: str) -> Path:
        if not MISSION_ID.match(mission_id):
            raise MissionError(f"invalid mission id {mission_id!r}")
        return self.directory / f"{mission_id}.json"

    def save(self, mission: Mission) -> None:
        mission.touch()
        text = json.dumps(mission_to_dict(mission), ensure_ascii=False, indent=1)
        write_text(self._path(mission.id), text, max_bytes=MAX_MISSION_BYTES)

    def load(self, mission_id: str) -> Mission:
        path = self._path(mission_id)
        if not path.exists():
            raise MissionError(f"no mission named {mission_id}")
        return mission_from_dict(self._read(path))

    def list_missions(self) -> list[Mission]:
        """Every readable mission, most recently updated first."""
        missions = []
        for path in sorted(self.directory.glob("*.json")) if self.directory.is_dir() else []:
            try:
                missions.append(mission_from_dict(self._read(path)))
            except MissionError:
                continue
        return sorted(missions, key=lambda m: m.updated, reverse=True)

    def _read(self, path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise MissionError(f"cannot read {path.name}: {exc}") from exc
        if not isinstance(data, dict):
            raise MissionError(f"{path.name} is not a mission record")
        return data

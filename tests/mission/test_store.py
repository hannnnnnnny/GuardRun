import json
from pathlib import Path

import pytest

from guardrun.mission.plan import Attempt, TaskStatus
from guardrun.mission.store import MissionError, MissionStore, mission_from_dict, mission_to_dict
from tests.mission.conftest import mission, task


def test_round_trip(tmp_path: Path) -> None:
    store = MissionStore(tmp_path / "missions")
    original = mission(task("a"), task("b", depends_on=["a"]))
    original.tasks[0].status = TaskStatus.PASSED
    original.tasks[0].attempts.append(Attempt(1, "completed", "did it", True, "ok", 0.5))
    original.cost_usd = 1.25
    store.save(original)
    loaded = store.load(original.id)
    assert loaded == original
    assert loaded.tasks[0].status is TaskStatus.PASSED


def test_unknown_and_invalid_ids(tmp_path: Path) -> None:
    store = MissionStore(tmp_path)
    with pytest.raises(MissionError, match="no mission"):
        store.load("0123456789ab")
    with pytest.raises(MissionError, match="invalid mission id"):
        store.load("../../etc/passwd")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("tasks"),
        lambda d: d["tasks"][0].update(status="exploded"),
        lambda d: d["tasks"][0].update(surprise=1),
        lambda d: d["tasks"][0].update(depends_on=["ghost"]),
        lambda d: d["tasks"][0]["attempts"].append({"number": 1}),
    ],
)
def test_corrupt_records_are_rejected(mutate: object) -> None:
    data = mission_to_dict(mission())
    mutate(data)  # type: ignore[operator]
    with pytest.raises(MissionError, match=r"corrupt|unknown"):
        mission_from_dict(data)


def test_list_skips_unreadable_files(tmp_path: Path) -> None:
    store = MissionStore(tmp_path)
    assert store.list_missions() == []
    first, second = mission(), mission()
    store.save(first)
    store.save(second)
    (tmp_path / "bad.json").write_text("{not json")
    (tmp_path / "list.json").write_text("[]")
    (tmp_path / "abcdefabcdef.json").write_text(json.dumps({"goal": "x"}))
    listed = store.list_missions()
    assert {m.id for m in listed} == {first.id, second.id}
    assert listed[0].updated >= listed[1].updated

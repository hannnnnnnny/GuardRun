from importlib.metadata import version

import guardrun


def test_version_is_exposed() -> None:
    assert guardrun.__version__ == "0.2.0"


def test_top_level_api() -> None:
    for name in ("Agent", "AgentOptions", "ApprovalMode", "Budget", "RunResult", "build_agent"):
        assert hasattr(guardrun, name)


def test_version_matches_installed_metadata() -> None:
    assert version("guardrun") == guardrun.__version__

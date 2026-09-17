import pytest


@pytest.fixture(autouse=True)
def isolate_transcript_root(tmp_path_factory, monkeypatch):
    """Point transcript ingestion at an empty directory for every test.

    Without this the suite would read the developer's real
    ~/.claude/projects — slow, non-deterministic, and machine-dependent.
    Tests that want transcripts write them into their own tmp_path and pass
    the root explicitly.
    """
    root = tmp_path_factory.mktemp("empty-transcripts")
    monkeypatch.setenv("TOKENDOG_TRANSCRIPT_ROOT", str(root))


@pytest.fixture(autouse=True)
def isolate_tokendog_env(monkeypatch):
    """Clear every TOKENDOG_* switch the developer happens to have exported.

    The hooks read their mode from the environment, so a developer running with
    TOKENDOG_OBSERVE_ONLY=1 or TRUNCATE_MODE=shadow set (which is exactly what
    you do while dogfooding) saw deny and enforce tests pass by doing nothing.
    Tests that need a mode set it themselves.
    """
    for name in ("TOKENDOG_OBSERVE_ONLY", "TOKENDOG_TRUNCATE_MODE", "TOKENDOG_WORKER",
                 "TOKENDOG_MAX_LINES", "TOKENDOG_MAX_BYTES", "TOKENDOG_PYTHON",
                 "TOKENDOG_ADVICE", "TOKENDOG_AUTO_REFRESH", "TOKENDOG_HOME"):
        monkeypatch.delenv(name, raising=False)

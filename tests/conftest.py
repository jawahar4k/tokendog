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

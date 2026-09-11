"""The window flags as the CLI exposes them, and the labels the tables print."""
import pytest

from tokendog.report import WINDOWED_COMMANDS, _label, _rate, _tok, main


# --- labels ------------------------------------------------------------


def test_a_session_id_is_shortened_to_eight_characters():
    assert _label("4d1d8352-766a-40c4-b175-ce56815cd2c1") == "4d1d8352"


def test_a_subagent_label_keeps_eight_characters_after_the_prefix():
    """Eight characters total leaves two hex digits after `agent-`.

    Measured on one machine: sixteen labels covering 376 subagent transcripts,
    18 to 35 rows each, all rendered as the same handful of names.
    """
    a = _label("agent-a930989dbeef1234")
    b = _label("agent-a93aaaaabeef1234")
    assert a != b
    assert a.startswith("agent-") and len(a) == len("agent-") + 8


def test_subagent_labels_from_one_fanout_stay_distinct():
    """Real ids vary from their first character, as these do — the observed
    collisions came from the label being too short, not from the ids sharing a
    prefix."""
    ids = [f"agent-a{i:x}0989dbeef{i:04x}" for i in range(40)]
    assert len({_label(i) for i in ids}) == 40


# --- number formatting -------------------------------------------------


@pytest.mark.parametrize("n,text", [
    (13_190_000, "13.19M"), (610_000, "610K"), (9_999, "9,999"), (0, "0"),
])
def test_token_counts_are_shown_at_reading_size(n, text):
    assert _tok(n) == text


def test_an_absent_count_is_a_dash_not_a_zero():
    """Zero and 'not measured' are different facts."""
    assert _tok(None) == "—"
    assert _rate(None) == "—"


@pytest.mark.parametrize("rate,text", [
    (740_000, "740K/m"), (1_200_000, "1.20M/m"), (500, "500/m"),
])
def test_rates_are_shown_per_minute(rate, text):
    assert _rate(rate) == text


# --- the flags ---------------------------------------------------------


def test_every_turn_measuring_report_takes_a_window():
    assert set(WINDOWED_COMMANDS) == {
        "bands", "resumes", "coldstart", "hygiene", "surface", "session", "outcomes", "errors",
        "pipelines", "discovery", "floor", "savings", "condense"}


def test_cost_is_not_windowed_because_its_flags_already_meant_something_else():
    """`cost --since` is a date handed to the SQL roll-up. Reinterpreting it
    would change the meaning of a flag that already worked."""
    assert "cost" not in WINDOWED_COMMANDS


@pytest.mark.parametrize("cmd", ["bands", "resumes", "coldstart", "hygiene"])
def test_an_unreadable_time_exits_two_with_a_usable_message(cmd, capsys):
    assert main([cmd, "--since", "lunchtime"]) == 2
    out = capsys.readouterr().out
    assert "17:29" in out


def test_a_backwards_window_is_refused(capsys):
    assert main(["hygiene", "--since", "18:00", "--until", "17:00"]) == 2
    assert "not after" in capsys.readouterr().out


def test_a_valid_window_is_accepted(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert main(["hygiene", "--since", "17:29", "--until", "17:45"]) in (0, None)

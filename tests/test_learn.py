"""Session learnings: extract signal, gate it, keep what survives, apply it."""
import json

import pytest

from tokendog.learn import (
    MIN_SCORE,
    extract,
    gate,
    inject_text,
    redact,
    save_lesson,
    score,
    similar,
    local_dir,
)


def _tool_use(tid, name, **inp):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tid, "name": name, "input": inp}]}}


def _tool_result(tid, text, *, error=False):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": error}]}}


def _user(text):
    return {"type": "user", "message": {"content": [{"type": "text", "text": text}]}}


def _assistant(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


# --- extraction --------------------------------------------------------------


def test_an_error_followed_by_a_success_of_the_same_tool_is_a_fix():
    recs = [_tool_use("t1", "Bash", command="npm test"),
            _tool_result("t1", "Error: Cannot find module 'left-pad' from src/app.js", error=True),
            _tool_use("t2", "Bash", command="npm install left-pad && npm test"),
            _tool_result("t2", "all tests passed")]
    sig = extract(recs)
    assert len(sig["fixes"]) == 1
    assert "left-pad" in sig["fixes"][0]["error"]


@pytest.mark.parametrize("noise", [
    "File has not been read yet. Read it first before writing to it.",
    "String to replace not found in file.",
    "Permission to use Bash has been denied.",
    "InputValidationError: missing required parameter",
    "No such file or directory",
    "no matches found",
    "Exit code 1",
    "The user doesn't want to proceed with this tool use.",
    "short err",
])
def test_errors_that_teach_nothing_are_dropped(noise):
    """Harness preconditions and typos are not lessons about the world."""
    recs = [_tool_use("t1", "Edit", file_path="a.py"), _tool_result("t1", noise, error=True),
            _tool_use("t2", "Edit", file_path="a.py"), _tool_result("t2", "ok")]
    assert extract(recs)["fixes"] == []


def test_an_error_with_no_later_success_is_not_a_fix():
    recs = [_tool_use("t1", "Bash", command="make"),
            _tool_result("t1", "error: linker command failed with exit code 1 (use -v)", error=True)]
    assert extract(recs)["fixes"] == []


def test_a_success_of_a_different_tool_does_not_close_the_error():
    recs = [_tool_use("t1", "Bash", command="make"),
            _tool_result("t1", "error: linker command failed with exit code 1 (use -v)", error=True),
            _tool_use("t2", "Read", file_path="Makefile"), _tool_result("t2", "all: …")]
    assert extract(recs)["fixes"] == []


def test_a_user_correction_is_paired_with_what_it_corrected():
    recs = [_assistant("I'll use the v1 endpoint for the upload."),
            _user("no, v1 is deprecated — use /v2/uploads, it takes multipart")]
    sig = extract(recs)
    assert len(sig["corrections"]) == 1
    assert "v1 endpoint" in sig["corrections"][0]["said"]
    assert "/v2/uploads" in sig["corrections"][0]["correction"]


@pytest.mark.parametrize("text", ["No, that's wrong", "Actually use pnpm", "wrong file",
                                  "instead, call the batch API", "you forgot the header",
                                  "it didn't work", "still failing", "why did you delete that"])
def test_correction_openers_are_recognised(text):
    recs = [_assistant("Done."), _user(text)]
    assert len(extract(recs)["corrections"]) == 1


def test_an_ordinary_message_is_not_a_correction():
    recs = [_assistant("Done."), _user("great, now add tests for the parser")]
    assert extract(recs)["corrections"] == []


def test_a_compaction_summary_is_signal():
    recs = [{"type": "user", "isCompactSummary": True,
             "message": {"content": "Summary: migrated auth to OAuth; the staging WAF rejects bodies over 1MB"}}]
    assert len(extract(recs)["summaries"]) == 1


def test_subagent_records_are_not_this_session():
    rec = _user("no, that's wrong"); rec["isSidechain"] = True
    assert extract([_assistant("x"), rec])["corrections"] == []


def test_extraction_survives_malformed_records():
    recs = [{}, {"type": "user"}, {"type": "assistant", "message": None},
            {"type": "user", "message": {"content": [None, 7, {"type": "tool_result"}]}}]
    sig = extract(recs)
    assert sig == {"fixes": [], "corrections": [], "summaries": []}


# --- the prefilter -----------------------------------------------------------


def test_most_sessions_never_reach_a_model():
    """Two fixes and a correction is not enough. No model is called below 6."""
    assert score({"fixes": [1, 2], "corrections": [1], "summaries": []}) == 6
    assert score({"fixes": [1], "corrections": [1], "summaries": []}) < MIN_SCORE
    assert MIN_SCORE == 6


# --- gates -------------------------------------------------------------------


def _cand(**kw):
    base = {"title": "Staging WAF rejects request bodies over 1MB",
            "rule": "Split uploads to staging into chunks under 1MB; the WAF returns a bare 403.",
            "why": "The 403 has no body, so it looks like an auth failure.",
            "evidence": "Error: 403 Forbidden (body 1.4MB)", "paths": [], "tags": ["staging"]}
    return base | kw


def test_a_well_formed_candidate_passes(tmp_path):
    out = gate([_cand()], repo=tmp_path, existing=[])
    assert len(out["kept"]) == 1


@pytest.mark.parametrize("field,value", [("title", "short"), ("rule", "too short"),
                                         ("evidence", "x")])
def test_a_candidate_without_substance_is_rejected(tmp_path, field, value):
    assert gate([_cand(**{field: value})], repo=tmp_path, existing=[])["kept"] == []


def test_a_cited_path_must_exist_but_a_bad_one_does_not_sink_the_lesson(tmp_path):
    (tmp_path / "src").mkdir(); (tmp_path / "src" / "upload.py").write_text("x")
    out = gate([_cand(paths=["src/upload.py", "src/nope.py", "/etc/passwd", "../x"])],
               repo=tmp_path, existing=[])
    assert out["kept"][0]["paths"] == ["src/upload.py"]
    assert out["dropped_paths"] == 3


def test_a_duplicate_of_a_shared_lesson_is_dropped(tmp_path):
    existing = [{"title": "Staging WAF rejects request bodies over 1MB", "scope": "shared"}]
    assert gate([_cand()], repo=tmp_path, existing=existing)["kept"] == []


def test_a_duplicate_of_your_own_lesson_corroborates_it(tmp_path):
    """Seeing it again in a new session is evidence, not noise."""
    existing = [{"title": "Staging WAF rejects request bodies over 1MB", "scope": "local",
                 "file": "staging-waf.md"}]
    out = gate([_cand()], repo=tmp_path, existing=existing)
    assert out["kept"] == [] and out["corroborated"] == ["staging-waf.md"]


def test_two_near_identical_candidates_in_one_run_keep_one(tmp_path):
    out = gate([_cand(), _cand(title="Staging WAF rejects request bodies over 1 MB")],
               repo=tmp_path, existing=[])
    assert len(out["kept"]) == 1


def test_a_forgotten_lesson_is_not_captured_again(tmp_path):
    existing = [{"title": "Staging WAF rejects request bodies over 1MB", "scope": "forgotten"}]
    assert gate([_cand()], repo=tmp_path, existing=existing)["kept"] == []


def test_fields_are_clipped(tmp_path):
    out = gate([_cand(title="T" * 500, rule="R" * 2_000)], repo=tmp_path, existing=[])
    assert len(out["kept"][0]["title"]) <= 90 and len(out["kept"][0]["rule"]) <= 400


def test_similarity_is_word_overlap():
    assert similar("staging waf rejects big bodies", "the staging WAF rejects big bodies") >= 0.6
    assert similar("staging waf rejects big bodies", "use pnpm not npm") < 0.6


# --- redaction ---------------------------------------------------------------


@pytest.mark.parametrize("secret", [
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghijklmnopqrstuv",
    "AKIAABCDEFGHIJKLMNOP",
    "ghp_" + "a" * 36,
    "xoxb-1234-5678-abcdefghijkl",
    "sk-ant-api03-" + "x" * 40,
    "sk-" + "y" * 48,
    "someone@example.com",
])
def test_secrets_are_redacted(secret):
    assert secret not in redact(f"see {secret} here")


def test_key_value_secrets_are_redacted_but_the_key_survives():
    out = redact("export API_TOKEN=abc123def456 and password: hunter22")
    assert "abc123def456" not in out and "hunter22" not in out
    assert "API_TOKEN" in out


def test_a_candidate_is_redacted_in_every_field(tmp_path):
    out = gate([_cand(evidence="token=ghp_" + "a" * 36 + " failed",
                      why="mail ops@example.com")], repo=tmp_path, existing=[])
    blob = json.dumps(out["kept"])
    assert "ghp_" + "a" * 36 not in blob and "ops@example.com" not in blob


# --- saving ------------------------------------------------------------------


def test_a_lesson_is_saved_where_git_can_never_see_it(tmp_path):
    path = save_lesson(tmp_path, _cand(), session_hash="abc123")
    assert path.parent == local_dir(tmp_path)
    assert (local_dir(tmp_path) / ".gitignore").read_text().strip() == "*"
    text = path.read_text()
    assert text.startswith("---") and 'shared: "no"' in text
    assert "Staging WAF" in text and "**Evidence:**" in text


def test_two_lessons_with_the_same_title_do_not_overwrite(tmp_path):
    a = save_lesson(tmp_path, _cand(), session_hash="a")
    b = save_lesson(tmp_path, _cand(), session_hash="b")
    assert a != b and a.exists() and b.exists()


# --- applying ----------------------------------------------------------------


def _shared(repo, name, title, rule):
    d = repo / ".claude" / "learnings"; d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(f'---\ntitle: "{title}"\n---\n\n# {title}\n\n{rule}\n')


def test_nothing_learned_injects_nothing(tmp_path):
    assert inject_text(tmp_path) is None


def test_lessons_are_injected_as_notes_to_check_not_instructions(tmp_path):
    _shared(tmp_path, "waf.md", "Staging WAF rejects bodies over 1MB", "Split uploads under 1MB.")
    text = inject_text(tmp_path)
    assert "not instructions" in text.lower()
    assert "Staging WAF" in text and "Split uploads" in text


def test_shared_lessons_come_before_yours_and_yours_skip_what_is_shared(tmp_path):
    _shared(tmp_path, "waf.md", "Staging WAF rejects request bodies over 1MB", "Split uploads.")
    save_lesson(tmp_path, _cand(), session_hash="a")                       # same lesson
    save_lesson(tmp_path, _cand(title="pnpm workspaces need a root lockfile",
                                rule="Run pnpm install at the root, never in a package dir."),
                session_hash="b")
    text = inject_text(tmp_path)
    assert text.index("Reviewed") < text.index("Yours")
    assert text.count("Staging WAF") == 1
    assert "pnpm workspaces" in text


def test_the_injection_is_capped(tmp_path):
    for i in range(200):
        _shared(tmp_path, f"l{i}.md", f"Lesson number {i} about a distinct pitfall {i}",
                "rule " + "x" * 200)
    text = inject_text(tmp_path)
    assert len(text) <= 6_000
    assert "more in .claude/learnings/" in text


# --- a fix must be a retry of the same thing ---------------------------------


def test_an_unrelated_bash_success_is_not_a_fix():
    """Bash runs constantly. Measured on a real session, 82 of 96 "fixes" were
    an error followed by some unrelated command — the model moving on, not
    fixing anything. For Bash the retry has to run the same program."""
    recs = [_tool_use("t1", "Bash", command="sed -n ',+16p' notes.md"),
            _tool_result("t1", "sed: 1: \",+16p \": invalid command code , in expression", error=True),
            _tool_use("t2", "Bash", command="grep -n entry notes.md"),
            _tool_result("t2", "12: entry")]
    assert extract(recs)["fixes"] == []


def test_a_bash_retry_of_the_same_program_is_a_fix():
    recs = [_tool_use("t1", "Bash", command="npm test"),
            _tool_result("t1", "Error: Cannot find module 'left-pad' from src/app.js", error=True),
            _tool_use("t2", "Bash", command="cd web && npm install left-pad && npm test"),
            _tool_result("t2", "passed")]
    assert len(extract(recs)["fixes"]) == 1


def test_an_unrelated_success_closes_the_error_so_a_later_retry_is_not_credited():
    """Once the model has moved on, a much later success is not the fix."""
    recs = [_tool_use("t1", "Bash", command="make build"),
            _tool_result("t1", "error: linker command failed with exit code 1", error=True),
            _tool_use("t2", "Bash", command="ls -la"), _tool_result("t2", "total 8"),
            _tool_use("t3", "Bash", command="make build"), _tool_result("t3", "ok")]
    assert extract(recs)["fixes"] == []


@pytest.mark.parametrize("noise", [
    "Exit code 143 Command timed out after 2m 0s run=34065141203",
    "<tool_use_error>Blocked: sleep 45 followed by: cat /tmp/x</tool_use_error>",
])
def test_harness_limits_are_not_lessons(noise):
    recs = [_tool_use("t1", "Bash", command="make"), _tool_result("t1", noise, error=True),
            _tool_use("t2", "Bash", command="make"), _tool_result("t2", "ok")]
    assert extract(recs)["fixes"] == []


@pytest.mark.parametrize("output", [
    "Exit code 1  M .glitch/gaps.db ?? .codex/ ?? .glitch/mcp.yaml",     # git status, exit 1
    "Exit code 1\n3a4\n> added line",                                      # diff found a difference
    "Exit code 1",                                                         # grep found nothing
])
def test_a_nonzero_exit_with_ordinary_output_is_not_an_error(output):
    """Bash reports exit 1 for plenty of non-failures. Measured on a real
    session: a `git status` inside a compound command, counted as a lesson."""
    recs = [_tool_use("t1", "Bash", command="git status; git log"),
            _tool_result("t1", output, error=True),
            _tool_use("t2", "Bash", command="git log --oneline"), _tool_result("t2", "abc fix")]
    assert extract(recs)["fixes"] == []


def test_a_nonzero_exit_that_says_what_failed_is_still_an_error():
    recs = [_tool_use("t1", "Bash", command="git push"),
            _tool_result("t1", "Exit code 1\nfatal: refusing to update checked out branch", error=True),
            _tool_use("t2", "Bash", command="git push origin HEAD:other"), _tool_result("t2", "ok")]
    assert len(extract(recs)["fixes"]) == 1

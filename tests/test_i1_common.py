"""I1 regression coverage for the base presentation branch."""
from benchlink_sidecar.server import HTML_SHELL
from benchlink_sidecar.ui.copy import result_card


def test_fallback_never_renders_raw_server_message():
    assert "body:j.message" not in HTML_SHELL.replace(" ", "")
    assert "COPY.errors[j.code]" in HTML_SHELL


def test_every_recovery_action_is_wired_or_hidden():
    for action in ("Retry", "End task", "Edit task", "Send again", "Send to Floyd", "Copy device ID", "Copy diagnostics", "Details", "New task"):
        assert f"'{action}':" in HTML_SHELL
    assert 'button.onclick = ' in HTML_SHELL


def test_form_and_state_accessibility():
    for markup in ('for="goal"', 'aria-describedby="goalHelp"', 'role="status"', 'aria-live="polite"', 'role="alert"', 'id="statusHeading" tabindex="-1"', 'id="errTitle" tabindex="-1"'):
        assert markup in HTML_SHELL
    assert "a.setAttribute('aria-label', l.url)" in HTML_SHELL
    assert 'Companion sidecar' not in HTML_SHELL
    assert "Offline / fail-closed:" not in HTML_SHELL


def test_result_finished_time_and_identifiers():
    card = result_card({'task_id':'task_test', 'session_id':'sess_test', 'created_at':'2026-11-05T18:05:00Z'}, target_name='Floyd')
    assert 'finished 1:05 PM' in card['meta']
    assert card['session_id'] == 'sess_test'
    assert 'id="resultDetails"' in HTML_SHELL
    assert 'Copy session ID' in HTML_SHELL and 'Copy task ID' in HTML_SHELL


def test_artifact_icon_is_hidden_from_assistive_technology():
    assert 'lucide-link-2' in HTML_SHELL
    assert 'aria-hidden="true"' in HTML_SHELL

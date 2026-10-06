"""A failed crash-protection write must be visible, and clear once a retry works."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import editor_frame
from parse_dmp_worksheet import SiteInfo
from session import create_blank_session


def _editor(tmp_path):
    ed = object.__new__(editor_frame.EditorFrame)
    ed.session = create_blank_session(SiteInfo(school_name="HEALTH"))
    ed.session.path = tmp_path / "HEALTH.dmps"
    ed.dirty, ed._recovery_job, ed._recovery_failed = True, None, False
    ed.statuses = []
    ed.on_status_change = lambda text, dirty: ed.statuses.append(text)
    return ed


def test_failed_backup_is_announced_and_retry_clears_it(tmp_path, monkeypatch):
    ed = _editor(tmp_path)
    def full_disk(session):
        raise OSError("No space left on device")
    monkeypatch.setattr(editor_frame, "write_recovery", full_disk)
    ed._write_recovery()
    assert ed.statuses[-1] == "⚠  Unsaved — auto-backup failed, save now"

    monkeypatch.setattr(editor_frame, "write_recovery", lambda session: None)
    ed._write_recovery()
    assert ed.statuses[-1] == "●  Unsaved changes"


def test_healthy_backup_does_not_spam_status(tmp_path, monkeypatch):
    ed = _editor(tmp_path)
    monkeypatch.setattr(editor_frame, "write_recovery", lambda session: None)
    ed._write_recovery()
    assert ed.statuses == []

"""Choosing Save and having it fail must stop the export, not run it anyway."""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import app as app_module
import session as session_module
from parse_dmp_worksheet import SiteInfo
from session import create_blank_session


class Reached(Exception):
    """Raised by a patched boundary to prove the export got past the save prompt."""


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setattr(session_module, "output_dir", lambda: tmp_path)
    a = object.__new__(app_module.App)
    a.state, a._generating = "editing", None
    a.session = create_blank_session(SiteInfo(school_name="GUARD SCHOOL"))
    a.output_dir = tmp_path
    a.pdf_path = a.dmp_path = None
    a.editor = SimpleNamespace(
        dirty=True, save=Mock(return_value=False), edit_epoch=1,
        generation_allowed=lambda: True, flush_design_refresh=lambda: None,
        show_issues_dialog=Mock(), tabs=SimpleNamespace(set=lambda tab: None))
    a._set_generating = Mock()
    a._run_async = Mock()
    return a


@pytest.mark.parametrize("answer, saved, proceeds", [
    (True, False, False),   # chose Save, save failed -> stop
    (True, True, True),     # chose Save, saved -> continue
    (False, None, True),    # chose not to save -> continue from the screen
])
def test_worksheet_respects_failed_save(app, monkeypatch, answer, saved, proceeds):
    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *a, **k: answer)
    app.editor.save.return_value = saved
    app._generate_worksheet()
    assert app.editor.show_issues_dialog.called is proceeds
    assert app.editor.save.called is answer


@pytest.mark.parametrize("saved", [False, True])
def test_riser_respects_failed_save(app, monkeypatch, saved):
    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *a, **k: True)
    app.editor.save.return_value = saved
    app._choose_riser_outputs = lambda: ["11x17"]
    sync = Mock(side_effect=Reached)
    monkeypatch.setattr(app_module, "sync_riser_document", sync)
    if saved:
        with pytest.raises(Reached):
            app._generate_riser()
    else:
        app._generate_riser()
        sync.assert_not_called()
        app._set_generating.assert_not_called()


@pytest.mark.parametrize("saved", [False, True])
def test_remotelink_respects_failed_save(app, monkeypatch, saved):
    monkeypatch.setattr(app_module, "remotelink_readiness_issues", lambda *a: [])
    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *a, **k: True)
    app.editor.save.return_value = saved
    app._show_remotelink_dialog = Mock()
    app._generate_remotelink()
    assert app._show_remotelink_dialog.called is saved

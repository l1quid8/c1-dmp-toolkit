import hashlib
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import app as app_module
import updater


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def test_verify_download_accepts_matching_checksum(tmp_path):
    z = tmp_path / "update.zip"
    z.write_bytes(b"new build")
    updater.verify_download(z, _digest(b"new build"))


@pytest.mark.parametrize("digest", [None, "", "md5:abc", _digest(b"something else")])
def test_verify_download_refuses_unverifiable_or_damaged(tmp_path, digest):
    z = tmp_path / "update.zip"
    z.write_bytes(b"new build")
    with pytest.raises(updater.UpdateError):
        updater.verify_download(z, digest)


def test_fetch_latest_reports_platform_asset_digest(monkeypatch):
    monkeypatch.setattr(updater, "_ASSET_SUFFIX", {sys.platform: "macOS.zip"})
    payload = {"tag_name": "v9.0.0", "assets": [
        {"name": "C1-DMP-Toolkit-Windows.zip", "browser_download_url": "w", "digest": "sha256:w"},
        {"name": "C1-DMP-Toolkit-macOS.zip", "browser_download_url": "m", "digest": "sha256:m"},
    ]}
    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        lambda req, timeout: io.BytesIO(json.dumps(payload).encode()))
    info = updater.fetch_latest()
    assert (info["asset_url"], info["asset_digest"]) == ("m", "sha256:m")


def test_stage_update_is_unavailable_in_dev_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "install_dir", lambda: None)
    assert updater.stage_update(tmp_path / "update.zip") is None


def test_discard_staged_removes_scratch_files(tmp_path):
    work = tmp_path / ".dmp_update_1"
    (work / "extracted").mkdir(parents=True)
    updater.discard_staged(updater.StagedUpdate(["true"], work))
    assert not work.exists()


@pytest.fixture
def app(monkeypatch):
    a = object.__new__(app_module.App)
    a.state, a._generating = "editing", None
    a.editor = SimpleNamespace(maybe_close=Mock(return_value=True))
    a._close_update_dialog = Mock()
    a._shutdown = Mock()
    calls = []
    monkeypatch.setattr(app_module.updater, "launch_staged", lambda s: calls.append(("launch", s)))
    monkeypatch.setattr(app_module.updater, "discard_staged", lambda s: calls.append(("discard", s)))
    return a, calls


def test_cancelled_save_prompt_leaves_no_update_armed(app):
    a, calls = app
    a.editor.maybe_close.return_value = False
    staged = object()
    a._finish_update(staged)
    assert calls == [("discard", staged)]
    a._shutdown.assert_not_called()
    a._close_update_dialog.assert_called_once_with()


def test_running_generation_can_postpone_update(app, monkeypatch):
    a, calls = app
    a._generating = "worksheet"
    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *args, **kw: False)
    staged = object()
    a._finish_update(staged)
    assert calls == [("discard", staged)]
    a._shutdown.assert_not_called()


def test_confirmed_quit_launches_update_then_exits(app):
    a, calls = app
    staged = object()
    a._finish_update(staged)
    assert calls == [("launch", staged)]
    a._shutdown.assert_called_once_with()

"""Slow or unavailable project folders must not freeze the home screen."""

import threading
import time

from test_app_scrollbar_runtime import application, app_module
from session import SessionSummary
import pytest


def test_native_file_menu_never_scans_folders_on_tk_thread(application, monkeypatch, tmp_path):
    application._recent_menu_cache = [SessionSummary(tmp_path/'cached.dmps', 'CACHED SCHOOL', None, '')]
    monkeypatch.setattr(app_module, 'list_recent_sessions',
                        lambda **kwargs: pytest.fail('Native menu synchronously scanned project folders'))
    application._refresh_file_menu()
    assert application._recent_menu.entrycget(0, 'label') == 'CACHED SCHOOL'


def test_native_worksheet_menu_never_scans_output_folder(application, monkeypatch):
    application.state = 'editing'
    application.editor = object()
    monkeypatch.setattr(application, '_latest_worksheet_path',
                        lambda: pytest.fail('Native menu synchronously scanned output folder'))
    application._refresh_worksheet_menu()
    assert application._worksheet_menu.entrycget('Generate Door Chart', 'state') == 'normal'
    application.editor = None


def labels(widget):
    found = []
    for child in widget.winfo_children():
        try:
            found.append(child.cget('text'))
        except Exception:
            pass
        found.extend(labels(child))
    return found


def pump_until(root, condition):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        root.update()
        if condition():
            return
        threading.Event().wait(.01)
    assert condition(), 'background result did not reach the home screen'


def test_slow_recent_scan_keeps_ui_responsive_and_renders_result(application, monkeypatch, tmp_path):
    started, release = threading.Event(), threading.Event()
    calls = []
    def slow_scan(*, limit):
        assert threading.current_thread() is not threading.main_thread(), 'Recent scan blocks the UI thread'
        calls.append(limit)
        started.set()
        release.wait(2)
        return [SessionSummary(tmp_path / 'demo.dmps', 'DEMO SCHOOL', None, 'demo.pdf')]
    monkeypatch.setattr(app_module, 'list_recent_sessions', slow_scan)
    try:
        application._show_drop_zone()
        assert started.wait(1)
        callbacks = []
        application.root.after(0, lambda: callbacks.append('responsive'))
        application.root.update()
        assert callbacks == ['responsive']
        assert 'Loading recent projects…' in labels(application.input_section)
        # Returning home while a filesystem call is blocked must reuse one scan.
        application._show_drop_zone()
        assert calls == [4]
        release.set()
        pump_until(application.root, lambda: 'DEMO SCHOOL' in labels(application.input_section))
    finally:
        release.set()


def test_late_recent_result_does_not_repaint_a_different_screen(application, monkeypatch, tmp_path):
    started, release = threading.Event(), threading.Event()
    def slow_scan(*, limit):
        assert threading.current_thread() is not threading.main_thread(), 'Recent scan blocks the UI thread'
        started.set()
        release.wait(2)
        return [SessionSummary(tmp_path / 'demo.dmps', 'STALE SCHOOL', None, '')]
    monkeypatch.setattr(app_module, 'list_recent_sessions', slow_scan)
    try:
        application._show_drop_zone()
        assert started.wait(1)
        application._clear_input_section()
        release.set()
        application.root.after(150, lambda: None)
        deadline = time.monotonic() + .2
        while time.monotonic() < deadline:
            application.root.update()
            threading.Event().wait(.01)
        assert application.input_section.winfo_children() == []
    finally:
        release.set()


def test_unavailable_recent_folder_leaves_browsing_available(application, monkeypatch):
    def unavailable(*, limit):
        raise OSError('folder unavailable')
    monkeypatch.setattr(app_module, 'list_recent_sessions', unavailable)
    application._show_drop_zone()
    pump_until(application.root, lambda: any('Recent projects unavailable' in str(t)
                                             for t in labels(application.input_section)))
    assert 'browse…' in labels(application.input_section)

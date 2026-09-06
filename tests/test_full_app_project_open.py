"""Exercise real main-loop layout, not just isolated editor construction."""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from session import Session, save_session
from riser_scene import layout_riser
from test_riser_workflows import seven_rsp_design

def test_full_app_opens_seven_rsp_project_and_switches_to_riser(tmp_path):
    design = seven_rsp_design()
    design.riser_document = layout_riser(design)
    project = save_session(Session(design, path=tmp_path / 'demo.dmps'))
    # A nested update_idletasks() loop prevents even Tk's timeout callback
    # from firing. Bound this regression at the process level and dump the
    # actual Python stack instead of hanging the whole suite.
    script = textwrap.dedent('''
        import faulthandler, os, sys
        from pathlib import Path
        sys.path.insert(0, 'scripts')
        import app
        app.output_dir = lambda: Path(sys.argv[1]).parent
        app.load_prefs = lambda: {}
        app.list_recent_sessions = lambda **kw: []
        app.App._check_for_updates = lambda *a, **kw: None
        faulthandler.dump_traceback_later(8, exit=True)
        application = app.App()
        errors = []
        application.root.report_callback_exception = lambda *args: errors.append(args)
        def open_project():
            application._open_session_path(Path(sys.argv[1]))
            application.editor.tabs.set('RISER')
        application.root.after(0, open_project)
        application.root.after(1000, application.root.quit)
        application.run()
        assert not errors, errors
        assert application.editor.tabs.get() == 'RISER'
        assert application.editor.riser_tab.canvas.winfo_ismapped()
        assert not application.editor.tabs.tab('POWER').winfo_ismapped()
        assert not application.editor.tabs.tab('SPLITTERS').winfo_ismapped()
        assert len(application.session.design.rsps) == 7
        print('PROJECT_OPEN_OK', flush=True)
        os._exit(0)
    ''')
    result = subprocess.run([sys.executable, '-c', script, str(project)], cwd=ROOT,
                            capture_output=True, text=True, timeout=15)
    if ('_tkinter.TclError' in result.stderr
            and ('no display name' in result.stderr or "couldn't connect to display" in result.stderr)):
        pytest.skip('Tk display unavailable')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PROJECT_OPEN_OK' in result.stdout

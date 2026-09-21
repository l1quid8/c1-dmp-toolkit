"""Home layout/routing contracts, exercised without a Tcl/Tk interpreter."""

from types import SimpleNamespace
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import app as app_module


class Widget:
    """Record the UI boundary emitted by App; never create native widgets."""

    def __init__(self, master=None, **options):
        self.master, self.options = master, options
        self.children, self.bindings, self.layout = [], {}, {}
        if master is not None:
            master.children.append(self)

    def grid(self, **options):
        self.layout = options

    def pack(self, **options):
        self.layout = options

    def columnconfigure(self, *args, **kwargs):
        pass

    def rowconfigure(self, *args, **kwargs):
        pass

    def configure(self, **options):
        self.options.update(options)

    def winfo_children(self):
        return self.children

    def bind(self, event, callback):
        self.bindings[event] = callback


def nodes(widget):
    yield widget
    for child in widget.children:
        yield from nodes(child)


def test_home_card_centers_creation_without_stealing_import_clicks(monkeypatch):
    # Moving the button outside the card or binding its subtree to Browse
    # breaks the approved layout or sends the user into the wrong start flow.
    for name in ("CTkFrame", "CTkLabel"):
        monkeypatch.setattr(app_module.ctk, name, Widget)
    monkeypatch.setattr(app_module.theme, "ui_font", lambda *a: None)
    monkeypatch.setattr(app_module, "IconTile", lambda parent, text, **kw: Widget(parent, text=text))
    monkeypatch.setattr(app_module, "Chip", lambda parent, text, **kw: Widget(parent, text=text))

    def button(parent, text, command, **options):
        result = Widget(parent, text=text, command=command, **options)
        Widget(result)  # Native buttons also own internal label/canvas children.
        return result

    monkeypatch.setattr(app_module, "primary_button", button)
    events = []
    home = SimpleNamespace(
        input_section=Widget(), _status_lbl=Widget(), _source_lbl=Widget(), _outdir_lbl=Widget(),
        _clear_input_section=lambda: None, _show_flow=lambda: None,
        _set_project_title=lambda *a: None,
        _create_new_project=lambda: events.append("create"),
        _choose_pdf=lambda: events.append("browse"),
        _set_drop_active=lambda active: events.append(("hover", active)),
        _show_recent_projects=lambda: events.append("recents"),
        _show_output_dir_row=lambda: events.append("output"),
    )
    app_module.App._show_drop_zone(home)
    rendered = list(nodes(home.input_section))
    create = next(w for w in rendered if w.options.get("text") == "Create New Project")
    assert create.master is home._drop_zone
    assert create.layout["row"] == 0
    assert create.layout.get("sticky", "") not in ("w", "e")
    assert "No source file needed" not in [w.options.get("text") for w in rendered]
    assert any(w.options.get("text") == "or" for w in rendered)
    assert {"PDF", "XLSX", "DMPS", "browse…"} <= {w.options.get("text") for w in rendered}
    assert events == ["recents", "output"]

    create.options["command"]()
    assert events[-1] == "create"
    assert all("<Button-1>" not in w.bindings for w in nodes(create))
    browse = next(w for w in rendered if w.options.get("text") == "browse…")
    browse.bindings["<Button-1>"](None)
    assert events[-1] == "browse"
    browse.bindings["<Enter>"](None)
    assert events[-1] == ("hover", True)

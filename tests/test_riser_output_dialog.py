"""Riser export choices use the existing folder and persist between exports."""
import customtkinter as ctk
import app as app_module
from test_app_scrollbar_runtime import application


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def choose(application, action):
    errors = []
    def interact():
        dlg = next(w for w in application.root.winfo_children() if isinstance(w, ctk.CTkToplevel))
        try:
            action(list(descendants(dlg)))
        except Exception as exc:
            errors.append(exc)
            dlg.destroy()
    application.root.after(100, interact)
    result = application._choose_riser_outputs()
    if errors:
        raise errors[0]
    return result


def click(widgets, text):
    next(w for w in widgets if isinstance(w, ctk.CTkButton) and w.cget('text') == text).invoke()


def test_default_small_pdf_and_remembered_choices_without_folder_prompt(application, monkeypatch):
    prefs = {'unrelated': 'keep'}
    monkeypatch.setattr(app_module, 'load_prefs', lambda: dict(prefs))
    monkeypatch.setattr(app_module, 'save_prefs', lambda value: prefs.update(value))
    def unexpected(**kwargs):
        raise AssertionError('Export must not prompt for a folder')
    monkeypatch.setattr(app_module.filedialog, 'askdirectory', unexpected)
    original_folder = application.output_dir
    assert choose(application, lambda widgets: click(widgets, 'Generate')) == ['11x17']
    def select_svg(widgets):
        for widget in widgets:
            if isinstance(widget, ctk.CTkCheckBox):
                widget.select() if widget.cget('text') == 'Editable SVG' else widget.deselect()
        click(widgets, 'Generate')
    assert choose(application, select_svg) == ['svg']
    assert choose(application, lambda widgets: click(widgets, 'Generate')) == ['svg']
    assert prefs == {'unrelated': 'keep', 'riser_formats': ['svg']}
    assert application.output_dir == original_folder


def test_cancel_does_not_save_choices(application, monkeypatch):
    saved = []
    monkeypatch.setattr(app_module, 'save_prefs', saved.append)
    assert choose(application, lambda widgets: click(widgets, 'Cancel')) is None
    assert saved == []

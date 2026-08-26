"""Configurable RemoteLink account editor with a live read-back receipt."""

from __future__ import annotations

import dataclasses
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

import theme
from editor_tabs import auto_hide_scrollbar
from paths import resource_path
from rl_injector.rl_config import (
    ARM_MODES,
    RLAdvanced,
    RLUser,
    RLScheduleDay,
    SCHEDULE_DAYS,
    resolve_config,
)
from rl_injector.xml_export import preview_account_summary
from ui_widgets import (
    Card,
    SectionLabel,
    accent_outline_button,
    ghost_button,
    primary_button,
    remove_button,
)


_ARM_LABELS = {
    "Area": "area",
    "All / Perimeter": "all_perimeter",
    "Home / Sleep / Away": "home_sleep_away",
    "Home / Sleep / Away + Guest": "hsa_with_guest",
}
_ARM_LABEL_BY_KEY = {value: label for label, value in _ARM_LABELS.items()}
_DAY_LABELS = {
    "sun": "Sun", "mon": "Mon", "tue": "Tue", "wed": "Wed",
    "thu": "Thu", "fri": "Fri", "sat": "Sat",
}


def _entry(parent, *, width=None, **kw) -> ctk.CTkEntry:
    options = dict(
        height=theme.HEIGHT["input"], fg_color=theme.SURFACE,
        border_color=theme.BORDER_STRONG, border_width=1,
        corner_radius=theme.RADIUS["button"], text_color=theme.TEXT,
        placeholder_text_color=theme.TEXT_TERTIARY,
        font=theme.ui_font(theme.SIZE["body"]),
    )
    if width is not None:
        options["width"] = width
    options.update(kw)
    return ctk.CTkEntry(parent, **options)


def _label(parent, text: str) -> ctk.CTkLabel:
    return ctk.CTkLabel(
        parent, text=text, anchor="w", text_color=theme.TEXT_TERTIARY,
        font=theme.ui_font(theme.SIZE["label"]),
    )


def _checkbox(parent, text, variable, command) -> ctk.CTkCheckBox:
    return ctk.CTkCheckBox(
        parent, text=text, variable=variable, command=command,
        width=18, checkbox_width=18, checkbox_height=18,
        border_width=2, corner_radius=theme.RADIUS["tag"],
        fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
        border_color=theme.BORDER_STRONG, checkmark_color=theme.ON_ACCENT,
        text_color=theme.TEXT, font=theme.ui_font(theme.SIZE["chip"]),
    )


class RemoteLinkTab(ctk.CTkFrame):
    """Account configuration on the left; generated-account receipt on right."""

    def __init__(self, master, session, on_change):
        super().__init__(master, fg_color="transparent")
        self.session = session
        self.on_change = on_change
        self._building = False
        self._danger_confirmed = False
        self._focus_targets: dict[str, ctk.CTkBaseClass] = {}
        self._advanced_open = False

        self.columnconfigure(0, weight=0, minsize=550)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        self.left = ctk.CTkScrollableFrame(self, fg_color="transparent", width=530)
        self.left.grid(row=0, column=0, sticky="nsew", padx=(0, theme.PAD["md"]))
        self.left.columnconfigure(0, weight=1)
        auto_hide_scrollbar(self.left)

        self._build_receipt()
        self._build_form()
        self.refresh_receipt()

    def _build_form(self):
        self._building = True
        try:
            self._build_identity(0)
            self._build_users(1)
            self._build_arming(2)
            self._build_advanced(3)
        finally:
            self._building = False

    def _section_card(self, row: int, title: str) -> Card:
        card = Card(self.left)
        card.grid(row=row, column=0, sticky="ew", pady=(0, theme.PAD["md"]))
        card.columnconfigure(0, weight=1)
        SectionLabel(card, title).grid(
            row=0, column=0, sticky="w", padx=theme.PAD["md"],
            pady=(theme.PAD["md"], theme.PAD["sm"]),
        )
        return card

    def _build_identity(self, row: int):
        card = self._section_card(row, "Account")
        resolved = resolve_config(self.session.remotelink, self.session.design)
        fields = ctk.CTkFrame(card, fg_color="transparent")
        fields.grid(row=1, column=0, sticky="ew", padx=theme.PAD["md"],
                    pady=(0, theme.PAD["md"]))
        fields.columnconfigure(0, weight=1)
        fields.columnconfigure(1, weight=1)
        for column, (label, attr, value) in enumerate((
            ("Account number", "account_num", resolved.account_num),
            ("Receiver number", "receiver_num", resolved.receiver_num),
        )):
            _label(fields, label).grid(row=0, column=column, sticky="w",
                                       padx=(0 if column == 0 else 6, 0))
            var = tk.StringVar(value=value)
            var.trace_add("write", lambda *_a, a=attr, v=var:
                          self._set_top(a, v.get()))
            entry = _entry(fields, textvariable=var)
            entry.grid(row=1, column=column, sticky="ew",
                       padx=(0 if column == 0 else 6, 6 if column == 0 else 0))
            self._focus_targets[f"field:{attr}"] = entry

    def _visible_users(self) -> list[RLUser]:
        return [dataclasses.replace(user) for user in
                resolve_config(self.session.remotelink, self.session.design).users]

    def _customize_users(self):
        config = self.session.remotelink
        if not config.users_customized:
            config.users = self._visible_users()
            config.users_customized = True

    def _build_users(self, row: int):
        old = getattr(self, "_users_card", None)
        if old is not None and old.winfo_exists():
            old.destroy()
        card = self._section_card(row, "Users")
        self._users_card = card
        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.grid(row=0, column=0, sticky="e", padx=theme.PAD["md"],
                     pady=(theme.PAD["sm"], 0))
        ghost_button(actions, "Reset defaults", self._reset_users,
                     width=105).pack(side="left", padx=(0, 6))
        accent_outline_button(actions, "+ Add user", self._add_user,
                              width=100).pack(side="left")

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.grid(row=1, column=0, sticky="ew", padx=theme.PAD["md"],
                  pady=(0, theme.PAD["md"]))
        for column, weight in enumerate((0, 1, 1, 0, 0)):
            body.columnconfigure(column, weight=weight)
        for column, text in enumerate(("#", "Name", "Code", "Profile", "")):
            _label(body, text).grid(row=0, column=column, sticky="w", padx=3)

        users = self._visible_users()
        for index, user in enumerate(users):
            values = (str(user.number), user.name, user.code, user.profile)
            for column, (field, value) in enumerate(zip(
                    ("number", "name", "code", "profile"), values)):
                var = tk.StringVar(value=value)
                var.trace_add("write", lambda *_a, i=index, f=field, v=var:
                              self._set_user(i, f, v.get()))
                entry = _entry(body, textvariable=var,
                               width=54 if column in (0, 3) else None)
                entry.grid(row=index + 1, column=column, sticky="ew", padx=3,
                           pady=3)
                self._focus_targets[f"user:{user.number}"] = entry
            remove_button(body, lambda i=index: self._remove_user(i)).grid(
                row=index + 1, column=4, padx=(5, 0))

    def _set_user(self, index: int, field: str, value: str):
        if self._building:
            return
        self._customize_users()
        if index >= len(self.session.remotelink.users):
            return
        user = self.session.remotelink.users[index]
        setattr(user, field, int(value) if field == "number" and value.isdigit()
                else value)
        self._changed()

    def _add_user(self):
        self._customize_users()
        existing = {user.number for user in self.session.remotelink.users}
        number = next(n for n in range(1, 10000) if n not in existing)
        self.session.remotelink.users.append(RLUser(number, "NEW USER", "", "1"))
        self._rebuild_users()

    def _remove_user(self, index: int):
        self._customize_users()
        if index < len(self.session.remotelink.users):
            self.session.remotelink.users.pop(index)
            self._rebuild_users()

    def _reset_users(self):
        self.session.remotelink.users = []
        self.session.remotelink.users_customized = False
        self._rebuild_users()

    def _rebuild_users(self):
        self._building = True
        try:
            self._build_users(1)
        finally:
            self._building = False
        self._changed()

    def _build_arming(self, row: int):
        card = self._section_card(row, "Arming")
        arming = self.session.remotelink.arming
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.grid(row=1, column=0, sticky="ew", padx=theme.PAD["md"],
                  pady=(0, theme.PAD["md"]))
        for column in range(5):
            body.columnconfigure(column, weight=1)
        delays = (*arming.entry_delays, arming.exit_delay)
        labels = ("Entry 1", "Entry 2", "Entry 3", "Entry 4", "Exit")
        self._delay_vars = []
        for column, (label, value) in enumerate(zip(labels, delays)):
            _label(body, f"{label} (sec)").grid(row=0, column=column,
                                                 sticky="w", padx=3)
            var = tk.StringVar(value=str(value))
            var.trace_add("write", lambda *_a, i=column, v=var:
                          self._set_delay(i, v.get()))
            _entry(body, textvariable=var, width=76).grid(
                row=1, column=column, sticky="ew", padx=3)
            self._delay_vars.append(var)

        _label(body, "Arming type").grid(row=2, column=0, columnspan=5,
                                         sticky="w", padx=3, pady=(10, 2))
        mode_var = tk.StringVar(value=_ARM_LABEL_BY_KEY.get(
            arming.arm_mode, "Area"))
        menu = ctk.CTkOptionMenu(
            body, values=list(_ARM_LABELS), variable=mode_var,
            command=lambda label: self._set_arm_mode(_ARM_LABELS[label]),
            height=theme.HEIGHT["input"], fg_color=theme.SURFACE,
            button_color=theme.SURFACE_CHIP,
            button_hover_color=theme.HOVER_SUBTLE,
            text_color=theme.TEXT, dropdown_fg_color=theme.SURFACE,
            dropdown_text_color=theme.TEXT,
            dropdown_hover_color=theme.HOVER_SUBTLE,
            font=theme.ui_font(theme.SIZE["body"]),
            dropdown_font=theme.ui_font(theme.SIZE["body"]),
        )
        menu.grid(row=3, column=0, columnspan=5, sticky="ew", padx=3)

    def _set_delay(self, index: int, value: str):
        if self._building or not value.isdigit():
            return
        arming = self.session.remotelink.arming
        if index < 4:
            delays = list(arming.entry_delays)
            delays[index] = int(value)
            arming.entry_delays = tuple(delays)
        else:
            arming.exit_delay = int(value)
        self._changed()

    def _set_arm_mode(self, key: str):
        if key not in ARM_MODES:
            return
        self.session.remotelink.arming.arm_mode = key
        self._changed()

    def _build_advanced(self, row: int):
        card = Card(self.left, border_color=theme.BANNER_BORDER)
        card.grid(row=row, column=0, sticky="ew", pady=(0, theme.PAD["md"]))
        card.columnconfigure(0, weight=1)
        self._advanced_card = card
        self._advanced_toggle = ghost_button(
            card, "⚠  Advanced — panel acts on its own  ▸",
            self._toggle_advanced, danger=True, width=300,
        )
        self._advanced_toggle.grid(row=0, column=0, sticky="w",
                                   padx=theme.PAD["sm"], pady=theme.PAD["sm"])
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.grid(row=1, column=0, sticky="ew", padx=theme.PAD["md"],
                  pady=(0, theme.PAD["md"]))
        body.columnconfigure(0, weight=1)
        self._advanced_body = body
        body.grid_remove()

        ctk.CTkLabel(
            body,
            text="These settings can self-arm the panel or send silent "
                 "ambush/duress signals. Enable only when the site programming "
                 "specifically requires them.",
            wraplength=480, justify="left", anchor="w",
            text_color=theme.BANNER_TEXT,
            font=theme.ui_font(theme.SIZE["chip"]),
        ).grid(row=0, column=0, sticky="ew", pady=(0, 8))

        advanced = self.session.remotelink.arming.advanced
        checks = ctk.CTkFrame(body, fg_color="transparent")
        checks.grid(row=1, column=0, sticky="ew")
        self._advanced_vars = {}
        for row_i, (text, attr) in enumerate((
            ("Arming schedule", "schedule_enabled"),
            ("Ambush/duress reports", "ambush_reports"),
            ("Auto-arm", "auto_arm"),
            ("Auto-disarm", "auto_disarm"),
        )):
            var = tk.BooleanVar(value=getattr(advanced, attr))
            _checkbox(checks, text, var,
                      lambda a=attr, v=var: self._set_advanced_bool(a, v)).grid(
                          row=row_i // 2, column=row_i % 2, sticky="w",
                          padx=(0, 18), pady=3)
            self._advanced_vars[attr] = var

        numbers = ctk.CTkFrame(body, fg_color="transparent")
        numbers.grid(row=2, column=0, sticky="ew", pady=(9, 0))
        numbers.columnconfigure(0, weight=1)
        numbers.columnconfigure(1, weight=1)
        for column, (label, attr, value) in enumerate((
            ("Ambush output (0 = off)", "ambush_output", advanced.ambush_output),
            ("Morning ambush minutes", "morn_ambush_min", advanced.morn_ambush_min),
        )):
            _label(numbers, label).grid(row=0, column=column, sticky="w",
                                        padx=(0 if column == 0 else 6, 0))
            var = tk.StringVar(value=value)
            entry = _entry(numbers, textvariable=var)
            entry.grid(row=1, column=column, sticky="ew",
                       padx=(0 if column == 0 else 6, 6 if column == 0 else 0))
            entry.bind("<FocusOut>", lambda _e, a=attr, v=var:
                       self._set_advanced_text(a, v))
            entry.bind("<Return>", lambda _e, a=attr, v=var:
                       self._set_advanced_text(a, v))
            self._focus_targets[f"field:{attr}"] = entry

        self._schedule_host = ctk.CTkFrame(body, fg_color="transparent")
        self._schedule_host.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self._build_schedule_rows()

    def _toggle_advanced(self):
        self._advanced_open = not self._advanced_open
        if self._advanced_open:
            self._advanced_body.grid()
            self._advanced_toggle.configure(
                text="⚠  Advanced — panel acts on its own  ▾")
        else:
            self._advanced_body.grid_remove()
            self._advanced_toggle.configure(
                text="⚠  Advanced — panel acts on its own  ▸")

    def _confirm_danger(self) -> bool:
        if self._danger_confirmed:
            return True
        allowed = messagebox.askyesno(
            "Enable autonomous panel behavior?",
            "These settings can self-arm or auto-disarm the panel and can "
            "enable silent ambush/duress reporting.\n\nContinue only when this "
            "programming is explicitly required for the site.",
            parent=self.winfo_toplevel(),
        )
        if allowed:
            self._danger_confirmed = True
        return allowed

    def _set_advanced_bool(self, attr: str, var: tk.BooleanVar):
        value = bool(var.get())
        advanced = self.session.remotelink.arming.advanced
        if value and not getattr(advanced, attr) and not self._confirm_danger():
            var.set(False)
            return
        setattr(advanced, attr, value)
        if attr == "schedule_enabled":
            self._build_schedule_rows()
        self._changed()

    def _set_advanced_text(self, attr: str, var: tk.StringVar):
        value = var.get().strip() or "0"
        advanced = self.session.remotelink.arming.advanced
        previous = getattr(advanced, attr)
        if value != "0" and previous == "0" and not self._confirm_danger():
            var.set(previous)
            return "break"
        setattr(advanced, attr, value)
        self._changed()
        return "break"

    def _build_schedule_rows(self):
        for child in self._schedule_host.winfo_children():
            child.destroy()
        advanced = self.session.remotelink.arming.advanced
        if not advanced.schedule_enabled:
            return
        SectionLabel(self._schedule_host, "Weekly schedule").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))
        for column, text in enumerate(("Day", "Open", "Close")):
            _label(self._schedule_host, text).grid(
                row=1, column=column, sticky="w", padx=(0, 6))
        for row_i, day in enumerate(SCHEDULE_DAYS, 2):
            item = advanced.schedule.setdefault(day, RLScheduleDay())
            ctk.CTkLabel(
                self._schedule_host, text=_DAY_LABELS[day], anchor="w",
                text_color=theme.TEXT, font=theme.ui_font(theme.SIZE["chip"]),
            ).grid(row=row_i, column=0, sticky="w", pady=2)
            for column, field in ((1, "open_time"), (2, "close_time")):
                var = tk.StringVar(value=getattr(item, field))
                var.trace_add("write", lambda *_a, d=day, f=field, v=var:
                              self._set_schedule(d, f, v.get()))
                _entry(self._schedule_host, textvariable=var, width=92,
                       placeholder_text="HH:MM").grid(
                           row=row_i, column=column, padx=(0, 6), pady=2)

    def _set_schedule(self, day: str, field: str, value: str):
        if self._building:
            return
        advanced = self.session.remotelink.arming.advanced
        item = advanced.schedule.setdefault(day, RLScheduleDay())
        setattr(item, field, value.strip())
        self._changed()

    def _set_top(self, attr: str, value: str):
        if self._building:
            return
        setattr(self.session.remotelink, attr, value.strip())
        self._changed()

    def _changed(self):
        if self._building:
            return
        self.on_change()
        self.refresh_receipt()

    def _build_receipt(self):
        card = Card(self)
        card.grid(row=0, column=1, sticky="nsew")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(1, weight=1)
        header = ctk.CTkFrame(card, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=theme.PAD["md"],
                    pady=(theme.PAD["md"], theme.PAD["sm"]))
        header.columnconfigure(0, weight=1)
        SectionLabel(header, "Live account receipt").grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header, text="Generated from the current project",
            text_color=theme.TEXT_TERTIARY,
            font=theme.ui_font(theme.SIZE["meta"]),
        ).grid(row=0, column=1, sticky="e")
        self.receipt = ctk.CTkTextbox(
            card, wrap="word", fg_color=theme.SURFACE_SUBTLE,
            text_color=theme.TEXT, border_width=1, border_color=theme.BORDER,
            corner_radius=theme.RADIUS["card"],
            font=theme.mono_font(theme.SIZE["chip"]),
        )
        self.receipt.grid(row=1, column=0, sticky="nsew",
                          padx=theme.PAD["md"], pady=(0, theme.PAD["md"]))

    def refresh_receipt(self):
        try:
            text = preview_account_summary(
                self.session.design, self.session.remotelink,
                resource_path("remotelink_account_template.xml"),
            )
        except Exception as exc:
            text = f"Receipt unavailable\n===================\n{exc}"
        self.receipt.configure(state="normal")
        self.receipt.delete("1.0", "end")
        self.receipt.insert("1.0", text)
        self.receipt.configure(state="disabled")

    def refresh(self):
        self.refresh_receipt()

    def focus_issue(self, ref: str | None):
        if not ref:
            return
        if ref.startswith("schedule:") and not self._advanced_open:
            self._toggle_advanced()
        target = self._focus_targets.get(ref)
        if target is not None and target.winfo_exists():
            target.focus_set()

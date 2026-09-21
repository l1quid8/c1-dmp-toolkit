"""Collect blank-project metadata without creating or saving a project."""

from datetime import date

import customtkinter as ctk

import theme
from parse_dmp_worksheet import SiteInfo
from ui_widgets import primary_button, secondary_button


class NewProjectDialog:
    def __init__(self, root, *, prepared_by: str = ""):
        self.result = None
        self.window = ctk.CTkToplevel(root)
        self.window.title("Create New Project")
        self.window.transient(root)
        self.window.configure(fg_color=theme.APP_BG)
        height = min(540, max(300, root.winfo_screenheight() - 100))
        self.window.geometry(f"520x{height}")
        self.window.minsize(420, min(360, height))
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self.window, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=theme.PAD["lg"], pady=(16, 8))
        ctk.CTkLabel(header, text="Create New Project", text_color=theme.TEXT,
                     font=theme.ui_font(theme.SIZE["title"], "bold")).pack(anchor="w")
        ctk.CTkLabel(header, text="Name your site to start a riser. Other details are optional.",
                     text_color=theme.TEXT_SECOND,
                     font=theme.ui_font(theme.SIZE["meta"])).pack(anchor="w")
        form = ctk.CTkScrollableFrame(self.window, fg_color=theme.SURFACE,
                                     border_width=1, border_color=theme.BORDER)
        form.grid(row=1, column=0, sticky="nsew", padx=theme.PAD["lg"])
        form.columnconfigure(1, weight=1)
        fields = [
            ("school_name", "Site / school name", ""),
            ("school_code", "Local code", ""),
            ("address_line1", "Address line 1", ""),
            ("address_line2", "City, state ZIP", ""),
            ("xr550_location", "MSP / XR550 location", "UNSPECIFIED"),
            ("sheet_number", "Drawing / sheet number", "INT-5.0"),
            ("drawn_by", "Prepared by", prepared_by),
            ("issue_date", "Issue date", date.today().isoformat()),
        ]
        self.values = {}
        self.entries = {}
        for row, (name, label, default) in enumerate(fields):
            ctk.CTkLabel(form, text=label, anchor="w", text_color=theme.TEXT,
                         font=theme.ui_font(theme.SIZE["control"])).grid(
                             row=row, column=0, sticky="w", padx=(12, 10), pady=8)
            var = ctk.StringVar(master=self.window, value=default)
            entry = ctk.CTkEntry(form, textvariable=var, fg_color=theme.SURFACE,
                                 border_color=theme.BORDER_STRONG, text_color=theme.TEXT,
                                 font=theme.ui_font(theme.SIZE["control"]))
            entry.grid(row=row, column=1, sticky="ew", padx=(0, 12), pady=8)
            self.values[name] = var
            self.entries[name] = entry
        ctk.CTkLabel(form, text="Review UNSPECIFIED in the editor when the panel location is known.",
                     text_color=theme.TEXT_SECOND, anchor="w", wraplength=420,
                     font=theme.ui_font(theme.SIZE["meta"])).grid(
                         row=len(fields), column=0, columnspan=2, sticky="w", padx=12, pady=(0, 8))

        footer = ctk.CTkFrame(self.window, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=theme.PAD["lg"], pady=(8, 16))
        footer.columnconfigure(0, weight=1)
        self.error = ctk.CTkLabel(footer, text="", anchor="w", text_color=theme.ERROR,
                                  font=theme.ui_font(theme.SIZE["meta"]))
        self.error.grid(row=0, column=0, columnspan=3, sticky="ew")
        secondary_button(footer, "Cancel", self._cancel, width=90).grid(row=1, column=1, padx=(0, 8))
        primary_button(footer, "Create New Project", self._create, width=160).grid(row=1, column=2)
        self.window.protocol("WM_DELETE_WINDOW", self._cancel)
        self.window.bind("<Return>", self._create)
        self.window.bind("<Escape>", self._cancel)
        self.window.grab_set()
        self.entries["school_name"].focus_set()

    def _create(self, _event=None):
        values = {name: var.get().strip() for name, var in self.values.items()}
        if not values["school_name"]:
            self.error.configure(text="Enter a site / school name to create the project.")
            self.entries["school_name"].focus_force()
            return "break"
        site = SiteInfo(**{name: values[name] for name in (
            "school_name", "school_code", "address_line1", "address_line2", "xr550_location")})
        title = {
            "school_name": site.school_name,
            "project_title": site.school_name,
            "local_code": site.school_code,
            "address": "\n".join(line for line in (site.address_line1, site.address_line2) if line),
            **{name: values[name] for name in ("sheet_number", "drawn_by", "issue_date")},
        }
        self.result = site, title
        self.window.grab_release()
        self.window.destroy()
        return "break"

    def _cancel(self, _event=None):
        self.window.grab_release()
        self.window.destroy()
        return "break"

    def show(self):
        self.window.wait_window()
        return self.result


def ask_new_project(root, *, prepared_by: str = ""):
    return NewProjectDialog(root, prepared_by=prepared_by).show()

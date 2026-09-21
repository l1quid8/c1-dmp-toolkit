"""Review source-backed BOM suggestions before creating a project."""

import customtkinter as ctk

import theme
from ui_widgets import primary_button, secondary_button


def ask_bom_review(root, review_text: str) -> bool:
    result = False
    window = ctk.CTkToplevel(root)
    window.title("Review BOM Import")
    window.transient(root)
    window.configure(fg_color=theme.APP_BG)
    window.geometry("760x600")
    window.minsize(580, 420)
    window.columnconfigure(0, weight=1)
    window.rowconfigure(1, weight=1)

    ctk.CTkLabel(window, text="Review BOM Import",
                 font=theme.ui_font(theme.SIZE["title"], "bold"),
                 text_color=theme.TEXT).grid(row=0, column=0, sticky="w", padx=20, pady=(18, 10))
    body = ctk.CTkTextbox(window, wrap="word", fg_color=theme.SURFACE,
                          text_color=theme.TEXT, font=theme.ui_font(theme.SIZE["control"]))
    body.grid(row=1, column=0, sticky="nsew", padx=20)
    body.insert("1.0", review_text)
    body.configure(state="disabled")

    footer = ctk.CTkFrame(window, fg_color="transparent")
    footer.grid(row=2, column=0, sticky="ew", padx=20, pady=18)
    footer.columnconfigure(0, weight=1)

    def close(accept=False):
        nonlocal result
        result = accept
        window.grab_release()
        window.destroy()

    secondary_button(footer, "Cancel", lambda: close(), width=90).grid(row=0, column=1, padx=(0, 8))
    primary_button(footer, "Create Draft Project", lambda: close(True), width=180).grid(row=0, column=2)
    window.protocol("WM_DELETE_WINDOW", close)
    window.bind("<Escape>", lambda _event: close())
    window.grab_set()
    window.wait_window()
    return result

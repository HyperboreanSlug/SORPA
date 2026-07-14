"""Theme colors, fonts, and ttk Treeview dark styling."""
from __future__ import annotations

import customtkinter as ctk
from tkinter import ttk

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# Install CTk resize throttle as early as possible (before widgets are created)
try:
    from gui_app.resize_perf import install_ctk_resize_throttle

    install_ctk_resize_throttle()
except Exception:
    pass

# Dark blue chrome (distinct from mapa’s charcoal greys)
C = {
    "bg": "#0a1020",
    "surface": "#101a2e",
    "panel": "#152238",
    "elevated": "#1c2c48",
    "border": "#2a3d5c",
    "text": "#e8eef8",
    "muted": "#8fa3bf",
    "dim": "#5e7394",
    "accent": "#e8a87c",
    "accent_hover": "#f0bc98",
    "accent_dim": "#3d2e24",
    "success": "#7dcea0",
    "danger": "#e07a7a",
    "info": "#8ab4c9",
    "row_alt": "#0d1628",
    "select": "#243d66",
    "tree_bg": "#0c1526",
    "tree_fg": "#e4ecf8",
    "tree_head": "#15233a",
}

FONT_UI = ("Segoe UI", 12)
FONT_SM = ("Segoe UI", 11)
FONT_BOLD = ("Segoe UI", 12, "bold")
FONT_TITLE = ("Segoe UI", 16, "bold")
FONT_SECTION = ("Segoe UI", 12, "bold")
FONT_MONO = ("Consolas", 11)


def style_treeview(root: ctk.CTk) -> None:
    """Force dark ttk Treeview (Windows otherwise paints blue-on-white)."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass

    style.configure(
        "Dark.Treeview",
        background=C["tree_bg"],
        foreground=C["tree_fg"],
        fieldbackground=C["tree_bg"],
        borderwidth=0,
        relief="flat",
        rowheight=28,
        font=FONT_SM,
    )
    style.configure(
        "Dark.Treeview.Heading",
        background=C["tree_head"],
        foreground=C["muted"],
        relief="flat",
        borderwidth=0,
        font=FONT_BOLD,
        padding=6,
    )
    style.map(
        "Dark.Treeview",
        background=[("selected", C["select"])],
        foreground=[("selected", C["text"])],
    )
    style.map(
        "Dark.Treeview.Heading",
        background=[("active", C["elevated"])],
        foreground=[("active", C["accent"])],
    )
    style.configure(
        "Dark.Vertical.TScrollbar",
        background=C["elevated"],
        troughcolor=C["bg"],
        borderwidth=0,
        arrowsize=12,
    )
    style.configure(
        "Dark.Horizontal.TScrollbar",
        background=C["elevated"],
        troughcolor=C["bg"],
        borderwidth=0,
        arrowsize=12,
    )


_style_treeview = style_treeview

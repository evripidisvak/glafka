from gi.repository import Gdk, Gtk

# Generated covers: dark tints (GNOME palette) so white text stays readable
COVER_COLORS = [
    "#1a5fb4",
    "#0f6b6b",
    "#2e7d4f",
    "#613583",
    "#a51d2d",
    "#63452c",
    "#c64600",
    "#3d3846",
]

CSS = """
.generated-cover { color: white; }
.cover-placeholder { background-color: alpha(currentColor, 0.08); }
.generated-cover .cover-title { font-weight: 800; }
.generated-cover .cover-author { opacity: 0.85; font-size: 0.9em; }
.reader-badge {
  padding: 5px;
  border-radius: 999px;
  background-color: var(--accent-bg-color);
  color: var(--accent-fg-color);
}
""" + "".join(
    f".generated-cover.cover-color-{i} {{ background-color: {color}; }}\n"
    for i, color in enumerate(COVER_COLORS)
)


def load_css() -> None:
    provider = Gtk.CssProvider()
    provider.load_from_string(CSS)
    display = Gdk.Display.get_default()
    if display is not None:
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

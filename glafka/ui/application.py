import logging
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, Gtk

import glafka
from glafka import APP_ID
from glafka.core.config import AppConfig
from glafka.ui.style import load_css
from glafka.ui.thumbnails import remove_legacy_cache
from glafka.ui.window import Window

log = logging.getLogger(__name__)

# GNOME's usual keys; Ctrl+W closes the window through GTK's built-in action
_SHORTCUTS = {
    "win.add-books": ["<Control>o"],
    "win.add-folder": ["<Control><Shift>o"],
    "win.search": ["<Control>f"],
    "win.save-details": ["<Control>s"],
    "win.preferences": ["<Control>comma"],
    "window.close": ["<Control>w"],
    "app.quit": ["<Control>q"],
}


class Application(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        load_css()
        _add_icon_paths()
        Gtk.Window.set_default_icon_name(APP_ID)
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_args: self.quit())
        self.add_action(quit_action)
        for action, accels in _SHORTCUTS.items():
            self.set_accels_for_action(action, accels)
        try:
            AppConfig.migrate_legacy()  # before anything reads the config
        except OSError as e:
            log.warning("Could not move the old config: %s", e)
        remove_legacy_cache()

    def do_activate(self) -> None:
        Window(application=self).present()


def _add_icon_paths() -> None:
    """The app's own icon, also when run from the source tree or a prefix GNOME doesn't search."""
    display = Gdk.Display.get_default()
    if display is None:
        return
    theme = Gtk.IconTheme.get_for_display(display)
    package = Path(glafka.__file__).resolve().parent
    # Installed: <prefix>/share/glafka/glafka -> <prefix>/share/icons. Source tree: data/icons.
    for icons in (package.parent.parent / "icons", package.parent / "data" / "icons"):
        if (icons / "hicolor").is_dir():
            theme.add_search_path(str(icons))

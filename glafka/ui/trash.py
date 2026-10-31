from pathlib import Path

from gi.repository import Gio


def move_to_trash(path: Path) -> None:
    """Move a file to the Trash (restorable from Files). Raises GLib.Error, e.g. no trash there."""
    Gio.File.new_for_path(str(path)).trash(None)

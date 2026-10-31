from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from gi.repository import Gio

from glafka.core.models import Destination


class ConnectedDrive(NamedTuple):
    destination: Destination
    mount: Gio.Mount
    root: Path


class Drive(NamedTuple):
    """A removable drive that could become a destination."""

    name: str  # e.g. "PocketBook (PB710)"
    uuid: str
    root: Path


def mount_uuid(mount: Gio.Mount) -> str | None:
    """The drive ID; on USB readers GIO puts it on the volume, not the mount."""
    volume = mount.get_volume()
    return (volume.get_uuid() if volume else None) or mount.get_uuid()


class DriveMonitor:
    """Which USB destinations are plugged in; calls `on_change` whenever drives come and go."""

    def __init__(self, on_change: Callable[[], None]) -> None:
        self._monitor = Gio.VolumeMonitor.get()  # keep a reference, or signals stop
        self._destinations: list[Destination] = []
        self._on_change = on_change
        for signal in ("mount-added", "mount-removed", "mount-changed"):
            self._monitor.connect(signal, lambda *_args: self._on_change())

    def set_destinations(self, destinations: list[Destination]) -> None:
        self._destinations = destinations
        self._on_change()

    def connected(self) -> list[ConnectedDrive]:
        """USB destinations mounted right now (resolve at use: a mount can vanish any time)."""
        found = []
        for mount in self._monitor.get_mounts():
            uuid, root = mount_uuid(mount), mount.get_root().get_path()
            for destination in self._destinations:
                if destination.kind == "usb" and uuid and destination.volume_uuid == uuid and root:
                    found.append(ConnectedDrive(destination, mount, Path(root)))
        return found

    def find(self, destination_id: str) -> ConnectedDrive | None:
        return next((c for c in self.connected() if c.destination.id == destination_id), None)

    def removable_drives(self) -> list[Drive]:
        """Plugged-in removable drives not yet set up as destinations."""
        known = {r.volume_uuid for r in self._destinations}
        drives = []
        for mount in self._monitor.get_mounts():
            uuid, root, drive = mount_uuid(mount), mount.get_root().get_path(), mount.get_drive()
            if not uuid or not root or uuid in known:
                continue
            if drive is None or not (drive.is_removable() or mount.can_eject()):
                continue
            product = drive.get_name().removeprefix("USB-FS ").strip()
            label = mount.get_name()
            drives.append(Drive(f"{product} ({label})" if product else label, uuid, Path(root)))
        return drives

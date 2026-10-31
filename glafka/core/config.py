import contextlib
import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from glafka.core.models import BookSort, EmailAccount

# The app's name before 2026-10-31; its config, library index and cache are migrated
LEGACY_NAME = "grammata"


def default_library_path() -> Path:
    return Path.home() / "Books" / "Glafka"


@dataclass
class AppConfig:
    library_path: Path
    # After a successful import, originals go to the Trash (never for Calibre libraries)
    move_imported_files: bool = False
    sort: BookSort = BookSort.TITLE_ASC  # grid order chosen in the sort menu
    list_view: bool = False  # rows instead of cover tiles
    email_account: EmailAccount | None = None  # password in the keyring, not in this file

    @classmethod
    def default(cls) -> "AppConfig":
        return cls(default_library_path())

    @staticmethod
    def config_file_path() -> Path:
        base = os.environ.get("XDG_CONFIG_HOME")
        root = Path(base) if base else Path.home() / ".config"
        return root / "glafka" / "config.json"

    @classmethod
    def migrate_legacy(cls) -> None:
        """Move the config from the app's old name, once."""
        new = cls.config_file_path()
        old = new.parent.parent / LEGACY_NAME / "config.json"
        if new.exists() or not old.is_file():
            return
        new.parent.mkdir(parents=True, exist_ok=True)
        os.replace(old, new)
        with contextlib.suppress(OSError):
            old.parent.rmdir()  # only if nothing else is in there

    @classmethod
    def exists(cls) -> bool:
        return cls.config_file_path().exists()

    @classmethod
    def load(cls) -> "AppConfig":
        try:
            data = json.loads(cls.config_file_path().read_text())
            sort_name = data.get("sort", BookSort.TITLE_ASC.name)
            return cls(
                Path(data["library_path"]),
                move_imported_files=bool(data.get("move_imported_files", False)),
                sort=BookSort[sort_name]
                if sort_name in BookSort.__members__
                else BookSort.TITLE_ASC,
                list_view=bool(data.get("list_view", False)),
                email_account=_email_account(data.get("email_account")),
            )
        except (OSError, ValueError, KeyError, TypeError):
            return cls.default()

    def save(self) -> None:
        path = self.config_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "library_path": str(self.library_path),
            "move_imported_files": self.move_imported_files,
            "sort": self.sort.name,
            "list_view": self.list_view,
        }
        if self.email_account is not None:
            data["email_account"] = asdict(self.email_account)
        path.write_text(json.dumps(data, indent=2))


def _email_account(data: object) -> EmailAccount | None:
    """None when missing or unusable: the user sets the account up again."""
    if not isinstance(data, dict):
        return None
    known = {f.name for f in fields(EmailAccount)}
    try:
        account = EmailAccount(**{k: v for k, v in data.items() if k in known})
    except TypeError:
        return None
    if account.security not in ("starttls", "ssl") or not isinstance(account.port, int):
        return None
    return account

"""App passwords in the GNOME keyring (Secret Service). Async: a locked keyring shows a prompt."""

from collections.abc import Callable

from gi.repository import GLib, Secret

from glafka.core.models import EmailAccount

_SCHEMA = Secret.Schema.new(
    "io.github.evripidisvak.Glafka.Smtp",
    Secret.SchemaFlags.NONE,
    {"host": Secret.SchemaAttributeType.STRING, "username": Secret.SchemaAttributeType.STRING},
)


def _attributes(account: EmailAccount) -> dict[str, str]:
    return {"host": account.host, "username": account.username or account.address}


def store_password(
    account: EmailAccount, password: str, on_done: Callable[[str | None], None]
) -> None:
    """Calls on_done with None on success, or the error text."""

    def done(_source: object, result: object) -> None:
        try:
            Secret.password_store_finish(result)
        except GLib.Error as e:
            on_done(e.message)
            return
        on_done(None)

    Secret.password_store(
        _SCHEMA,
        _attributes(account),
        Secret.COLLECTION_DEFAULT,
        f"Glafka: app password for {account.address}",
        password,
        None,
        done,
    )


def lookup_password(
    account: EmailAccount, on_done: Callable[[str | None, str | None], None]
) -> None:
    """Calls on_done(password, error); both None when no password is saved."""

    def done(_source: object, result: object) -> None:
        try:
            on_done(Secret.password_lookup_finish(result), None)
        except GLib.Error as e:
            on_done(None, e.message)

    Secret.password_lookup(_SCHEMA, _attributes(account), None, done)


def clear_password(account: EmailAccount, on_done: Callable[[str | None], None]) -> None:
    """Calls on_done with None on success (also when nothing was saved), or the error text."""

    def done(_source: object, result: object) -> None:
        try:
            Secret.password_clear_finish(result)
        except GLib.Error as e:
            on_done(e.message)
            return
        on_done(None)

    Secret.password_clear(_SCHEMA, _attributes(account), None, done)

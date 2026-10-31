import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from glafka.core.config import AppConfig
from glafka.core.errors import RECOVERABLE_ERRORS
from glafka.core.mail import valid_address
from glafka.core.models import Destination, EmailAccount
from glafka.ui.drives import Drive
from glafka.ui.secrets import clear_password, store_password

# Opens the library at the path; True on success. Saving the setting is its job.
LibraryChooser = Callable[[Path], bool]


@dataclass
class DestinationSettings:
    """What the Send Books To group needs from the window; None while no library is open."""

    get: Callable[[], list[Destination]]
    add: Callable[[Destination], None]
    update: Callable[[Destination], None]
    remove: Callable[[str], None]
    drives: Callable[[], list[Drive]]


def choose_library_folder(parent: Gtk.Window, on_chosen: LibraryChooser) -> None:
    """Folder picker; calls on_chosen with the picked folder unless cancelled."""

    def on_folder_chosen(file_dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            folder = file_dialog.select_folder_finish(result)
        except GLib.Error:
            return  # cancelled
        path = folder.get_path() if folder is not None else None
        if path is not None:
            on_chosen(Path(path))

    Gtk.FileDialog(title="Select Library Folder").select_folder(parent, None, on_folder_chosen)


def show_preferences_dialog(
    parent: Gtk.Window, on_library_changed: LibraryChooser, destinations: DestinationSettings | None
) -> None:
    dialog = Adw.PreferencesDialog(title="Preferences")
    page = Adw.PreferencesPage(title="General", icon_name="preferences-system-symbolic")
    group = Adw.PreferencesGroup(
        title="Library Storage",
        description="Choose where Glafka organizes your book collection and SQLite index.",
    )

    path_row = Adw.ActionRow(
        title="Library Folder",
        subtitle=str(AppConfig.load().library_path),
        subtitle_selectable=True,
        use_markup=False,
    )

    def on_chosen(path: Path) -> bool:
        if not on_library_changed(path):
            return False
        path_row.set_subtitle(str(path))
        return True

    change_btn = Gtk.Button(label="Change…", valign=Gtk.Align.CENTER)
    change_btn.connect("clicked", lambda _btn: choose_library_folder(parent, on_chosen))
    path_row.add_suffix(change_btn)

    group.add(path_row)
    page.add(group)

    importing = Adw.PreferencesGroup(title="Importing")
    move_row = Adw.SwitchRow(
        title="Move Files Into the Library",
        subtitle=(
            "After import, the original files go to the Trash. "
            "Files from a Calibre library are never moved."
        ),
        active=AppConfig.load().move_imported_files,
    )

    def on_move_toggled(row: Adw.SwitchRow, _pspec: object) -> None:
        config = AppConfig.load()
        config.move_imported_files = row.get_active()
        try:
            config.save()
        except OSError as e:
            dialog.add_toast(Adw.Toast(title=f"Could not save the setting: {e}", use_markup=False))

    move_row.connect("notify::active", on_move_toggled)
    importing.add(move_row)
    page.add(importing)
    dialog.add(page)

    sending = Adw.PreferencesPage(title="Sending", icon_name="mail-send-symbolic")
    if destinations is not None:
        sending.add(_destinations_group(dialog, destinations))
    sending.add(_email_group(dialog))
    dialog.add(sending)
    dialog.present(parent)


def _destinations_group(
    dialog: Adw.PreferencesDialog, settings: DestinationSettings
) -> Adw.PreferencesGroup:
    """Ways to send books to e-readers."""
    group = Adw.PreferencesGroup(
        title="Send Books To",
        description=(
            "Each one adds a button to the book details: “Copy to” for a USB drive, "
            "“Email to” for an email address."
        ),
    )
    rows: list[Gtk.Widget] = []

    def edit(destination: Destination) -> None:
        _destination_dialog(dialog, settings, refresh, destination.kind, destination)

    def remove(destination: Destination) -> None:
        try:
            settings.remove(destination.id)
        except RECOVERABLE_ERRORS as e:
            dialog.add_toast(Adw.Toast(title=f"Could not remove it: {e}", use_markup=False))
        refresh()

    def refresh() -> None:
        for row in rows:
            group.remove(row)
        rows.clear()
        for destination in settings.get():
            email = destination.kind == "email"
            formats = ", ".join(f.upper() for f in destination.formats)
            where = (
                f"Email to {destination.email}"
                if email
                else f"USB drive, into “{destination.books_folder}”"
            )
            row = Adw.ActionRow(
                title=destination.name,
                subtitle=f"{where}, {formats}",
                use_markup=False,
                activatable=True,
            )
            row.add_prefix(
                Gtk.Image(icon_name="mail-send-symbolic" if email else "media-removable-symbolic")
            )
            edit_button = Gtk.Button(
                icon_name="document-edit-symbolic",
                valign=Gtk.Align.CENTER,
                tooltip_text="Edit",
                css_classes=["flat"],
            )
            remove_button = Gtk.Button(
                icon_name="user-trash-symbolic",
                valign=Gtk.Align.CENTER,
                tooltip_text="Remove",
                css_classes=["flat"],
            )
            row.connect("activated", lambda _r, r=destination: edit(r))
            edit_button.connect("clicked", lambda _b, r=destination: edit(r))
            remove_button.connect("clicked", lambda _b, r=destination: remove(r))
            row.add_suffix(edit_button)
            row.add_suffix(remove_button)
            rows.append(row)
        add_usb = Adw.ButtonRow(title="Add USB Drive…", start_icon_name="list-add-symbolic")
        add_usb.connect(
            "activated", lambda _r: _destination_dialog(dialog, settings, refresh, "usb")
        )
        add_email = Adw.ButtonRow(title="Add Email Address…", start_icon_name="list-add-symbolic")
        add_email.connect(
            "activated", lambda _r: _destination_dialog(dialog, settings, refresh, "email")
        )
        rows.extend((add_usb, add_email))
        for row in rows:
            group.add(row)

    refresh()
    return group


def _formats(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower()) or ["epub"]


def _destination_dialog(
    dialog: Adw.PreferencesDialog,
    settings: DestinationSettings,
    on_done: Callable[[], None],
    kind: str,
    existing: Destination | None = None,
) -> None:
    """Add a USB drive or email address, or edit one (its drive stays as added)."""
    email = kind == "email"
    drives = settings.drives() if not email and existing is None else []
    if not email and existing is None and not drives:
        alert = Adw.AlertDialog(
            heading="No USB Drive Found",
            body=(
                "Plug in your e-reader and choose its USB or computer-connection mode "
                "(“PC link” on PocketBook), then try again."
            ),
        )
        alert.add_response("close", "Close")
        alert.present(dialog)
        return

    name_row = Adw.EntryRow(title="Name on the button", text=existing.name if existing else "")
    formats_row = Adw.EntryRow(
        title="Formats, preferred first",
        text=", ".join(existing.formats) if existing else "epub, pdf",
    )
    if email:
        target_row = Adw.EntryRow(
            title="Email address",
            text=(existing.email or "") if existing else "",
            input_purpose=Gtk.InputPurpose.EMAIL,
        )
        heading = "Add Email Address"
        body = (
            "Books are emailed as attachments from your email account (below). For PocketBook, "
            "use your Send-to-PocketBook address (name@pbsync.com); it only accepts mail "
            "from addresses approved in your PocketBook account."
        )
    else:
        target_row = Adw.EntryRow(
            title="Books folder on the reader", text=existing.books_folder if existing else ""
        )
        heading = "Add USB Drive"
        body = "Books are copied into this folder, as Author/Title like Calibre does."
    fields = Adw.PreferencesGroup()
    drive_row = None
    if drives:
        drive_row = Adw.ComboRow(title="Drive", model=Gtk.StringList.new([d.name for d in drives]))

        def drive_changed(*_args: object) -> None:
            drive = drives[drive_row.get_selected()]
            name_row.set_text(drive.name.split(" (")[0])
            target_row.set_text("Books" if (drive.root / "Books").is_dir() else "")

        drive_row.connect("notify::selected", drive_changed)
        drive_changed()
        fields.add(drive_row)
    for row in (name_row, target_row, formats_row):
        fields.add(row)

    alert = Adw.AlertDialog(
        heading=f"Edit “{existing.name}”" if existing else heading,
        body=body,
        extra_child=fields,
        default_response="save",
        close_response="cancel",
    )
    alert.add_response("cancel", "Cancel")
    alert.add_response("save", "Save" if existing else "Add")
    alert.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
    if email:

        def check(*_args: object) -> None:
            alert.set_response_enabled("save", valid_address(target_row.get_text().strip()))

        target_row.connect("changed", check)
        check()

    def on_response(_alert: Adw.AlertDialog, response: str) -> None:
        if response != "save":
            return
        name, formats = name_row.get_text().strip(), _formats(formats_row.get_text())
        if email:
            address = target_row.get_text().strip()
            if not valid_address(address):
                return
            values = {"name": name or address, "books_folder": "", "email": address}
        else:
            folder = target_row.get_text().strip().strip("/")
            if ".." in Path(folder).parts:
                folder = "Books"  # always inside the reader
            fallback = existing.name if existing else drives[drive_row.get_selected()].name
            values = {"name": name or fallback, "books_folder": folder or "Books"}
        try:
            if existing is not None:
                settings.update(replace(existing, formats=formats, **values))
            else:
                volume_uuid = None if email else drives[drive_row.get_selected()].uuid
                settings.add(
                    Destination(
                        id=str(uuid.uuid4()),
                        kind=kind,
                        volume_uuid=volume_uuid,
                        formats=formats,
                        **values,
                    )
                )
        except RECOVERABLE_ERRORS as e:
            dialog.add_toast(Adw.Toast(title=f"Could not save it: {e}", use_markup=False))
        on_done()

    alert.connect("response", on_response)
    alert.present(dialog)


def _email_group(dialog: Adw.PreferencesDialog) -> Adw.PreferencesGroup:
    group = Adw.PreferencesGroup(
        title="Email Account",
        description="Books are emailed from this account. Its password is kept in your keyring.",
    )
    row = Adw.ActionRow(title="Sending Account", use_markup=False)
    edit = Gtk.Button(valign=Gtk.Align.CENTER)
    remove = Gtk.Button(
        icon_name="user-trash-symbolic",
        valign=Gtk.Align.CENTER,
        tooltip_text="Remove Account",
        css_classes=["flat"],
    )
    row.add_suffix(remove)
    row.add_suffix(edit)
    group.add(row)

    def show_error(error: str | None) -> None:
        if error is not None:
            dialog.add_toast(Adw.Toast(title=f"Keyring: {error}", use_markup=False))

    def refresh() -> None:
        account = AppConfig.load().email_account
        if account is None:
            row.set_subtitle("Not set up")
        else:
            provider = "Gmail" if account.provider == "gmail" else account.host
            row.set_subtitle(f"{account.address} ({provider})")
        edit.set_label("Set Up…" if account is None else "Change…")
        remove.set_visible(account is not None)

    def on_remove(_button: Gtk.Button) -> None:
        config = AppConfig.load()
        if config.email_account is None:
            return
        clear_password(config.email_account, show_error)
        config.email_account = None
        try:
            config.save()
        except OSError as e:
            dialog.add_toast(Adw.Toast(title=f"Could not save the setting: {e}", use_markup=False))
        refresh()

    edit.connect("clicked", lambda _b: _edit_account(dialog, refresh, show_error))
    remove.connect("clicked", on_remove)
    refresh()
    return group


def _edit_account(
    dialog: Adw.PreferencesDialog,
    on_saved: Callable[[], None],
    on_keyring_error: Callable[[str | None], None],
) -> None:
    old = AppConfig.load().email_account
    is_gmail = old is None or old.provider == "gmail"
    provider_row = Adw.ComboRow(title="Provider", model=Gtk.StringList.new(["Gmail", "Other"]))
    provider_row.set_selected(0 if is_gmail else 1)
    address_row = Adw.EntryRow(
        title="Email address", text=old.address if old else "", input_purpose=Gtk.InputPurpose.EMAIL
    )
    password_row = Adw.PasswordEntryRow(title="App password")
    host_row = Adw.EntryRow(title="Server", text=old.host if old and not is_gmail else "")
    port_row = Adw.SpinRow.new_with_range(1, 65535, 1)
    port_row.set_title("Port")
    port_row.set_value(old.port if old and not is_gmail else 587)
    security_row = Adw.ComboRow(
        title="Encryption", model=Gtk.StringList.new(["STARTTLS", "SSL/TLS"])
    )
    security_row.set_selected(1 if old and old.security == "ssl" else 0)
    user_row = Adw.EntryRow(
        title="User name (if not the address)",
        text=old.username if old and old.username != old.address else "",
    )
    custom_rows = (host_row, port_row, security_row, user_row)
    fields = Adw.PreferencesGroup()
    for r in (provider_row, address_row, password_row, *custom_rows):
        fields.add(r)

    alert = Adw.AlertDialog(
        heading="Sending Account",
        extra_child=fields,
        default_response="save",
        close_response="cancel",
    )
    alert.add_response("cancel", "Cancel")
    alert.add_response("save", "Save")
    alert.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)

    def account() -> EmailAccount | None:
        """The account as entered, or None while incomplete."""
        address = address_row.get_text().strip()
        if not valid_address(address):
            return None
        if provider_row.get_selected() == 0:
            return EmailAccount.gmail(address)
        host = host_row.get_text().strip()
        if not host or any(c.isspace() for c in host):
            return None
        security = "ssl" if security_row.get_selected() == 1 else "starttls"
        user = user_row.get_text().strip() or address
        return EmailAccount(address, host, int(port_row.get_value()), security, user)

    def same_login(a: EmailAccount, b: EmailAccount | None) -> bool:
        """The saved password still applies (it is stored per server and user name)."""
        return b is not None and (a.host, a.username) == (b.host, b.username)

    def update(*_args: object) -> None:
        gmail = provider_row.get_selected() == 0
        for r in custom_rows:
            r.set_visible(not gmail)
        if gmail:
            alert.set_body(
                "Gmail needs an app password, not your account password. Create one in "
                "your Google Account: Security, 2-Step Verification, App passwords."
            )
        else:
            alert.set_body("Use the settings your email provider gives for sending (SMTP).")
        if old is not None:
            password_row.set_title("App password (empty keeps the saved one)")
        entered = account()
        ready = entered is not None and (bool(password_row.get_text()) or same_login(entered, old))
        alert.set_response_enabled("save", ready)

    for r in (address_row, password_row, host_row, user_row):
        r.connect("changed", update)
    for r in (provider_row, security_row):
        r.connect("notify::selected", update)
    update()

    def on_response(_alert: Adw.AlertDialog, response: str) -> None:
        new = account()
        if response != "save" or new is None:
            return
        password = password_row.get_text()
        if not password and not same_login(new, old):
            return
        config = AppConfig.load()
        config.email_account = new
        try:
            config.save()
        except OSError as e:
            dialog.add_toast(Adw.Toast(title=f"Could not save the setting: {e}", use_markup=False))
            return
        if old is not None and not same_login(new, old):
            clear_password(old, on_keyring_error)  # the old login is not used any more
        if password:
            store_password(new, password, on_keyring_error)
        on_saved()

    alert.connect("response", on_response)
    alert.present(dialog)

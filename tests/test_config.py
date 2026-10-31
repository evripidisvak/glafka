import json

from glafka.core.config import AppConfig
from glafka.core.models import BookSort, EmailAccount


def test_move_setting_saved_and_off_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = tmp_path / "glafka" / "config.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"library_path": "/books"}))  # written before the setting existed

    config = AppConfig.load()
    assert config.move_imported_files is False

    config.move_imported_files = True
    config.save()
    assert AppConfig.load().move_imported_files is True
    assert str(AppConfig.load().library_path) == "/books"


def test_sort_choice_saved_and_unknown_values_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config = AppConfig(library_path=tmp_path, sort=BookSort.DATE_ADDED_DESC)
    config.save()
    assert AppConfig.load().sort is BookSort.DATE_ADDED_DESC

    path = tmp_path / "glafka" / "config.json"
    path.write_text(json.dumps({"library_path": "/books", "sort": "NO_SUCH_SORT"}))
    assert AppConfig.load().sort is BookSort.TITLE_ASC


def test_list_view_choice_saved_and_off_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config = AppConfig(library_path=tmp_path)
    assert config.list_view is False
    config.list_view = True
    config.save()
    assert AppConfig.load().list_view is True


def test_config_moves_from_the_old_app_name(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    old = tmp_path / "grammata" / "config.json"
    old.parent.mkdir()
    old.write_text(json.dumps({"library_path": "/home/me/Documents/Books", "sort": "AUTHOR_ASC"}))

    AppConfig.migrate_legacy()

    assert not old.parent.exists()
    config = AppConfig.load()
    assert (str(config.library_path), config.sort) == (
        "/home/me/Documents/Books",
        BookSort.AUTHOR_ASC,
    )


def test_existing_new_config_is_never_overwritten(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    AppConfig(library_path=tmp_path / "new").save()
    old = tmp_path / "grammata" / "config.json"
    old.parent.mkdir()
    old.write_text(json.dumps({"library_path": "/old"}))

    AppConfig.migrate_legacy()

    assert AppConfig.load().library_path == tmp_path / "new"


def test_email_account_saved_without_a_password(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert AppConfig(library_path=tmp_path).email_account is None

    AppConfig(library_path=tmp_path, email_account=EmailAccount.gmail("me@gmail.com")).save()

    account = AppConfig.load().email_account
    assert account == EmailAccount(
        "me@gmail.com", "smtp.gmail.com", 587, "starttls", "me@gmail.com", "gmail"
    )
    saved = (tmp_path / "glafka" / "config.json").read_text()
    assert "password" not in saved


def test_broken_email_account_is_dropped(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = tmp_path / "glafka" / "config.json"
    path.parent.mkdir()
    for broken in (
        {"address": "me@x.org"},
        {"address": "a", "host": "h", "port": 25, "security": "none"},
        "text",
    ):
        path.write_text(json.dumps({"library_path": "/books", "email_account": broken}))
        config = AppConfig.load()
        assert config.email_account is None
        assert str(config.library_path) == "/books"  # the rest still loads

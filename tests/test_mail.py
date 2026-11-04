"""Emailing books. A fake SMTP class stands in for the server: no test touches the network."""

import email
import smtplib
from email import policy

import pytest
from test_importer import make_epub

from glafka.core.errors import GlafkaError
from glafka.core.library import LibraryManager
from glafka.core.models import Destination, EmailAccount, HistoryAction
from glafka.core.send import SendStatus

GMAIL = EmailAccount.gmail("me@gmail.com")
CLOUD = Destination(
    id="e", name="PocketBook (email)", kind="email", books_folder="", email="me@pbsync.com"
)


class FakeSMTP:
    """Records the conversation; class attributes set what the 'server' does."""

    instances: list["FakeSMTP"] = []
    size_limit = "35882577"  # what Gmail advertises
    reject_login = False

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.context = host, port, context
        self.calls: list[str] = []
        self.sent: list[tuple[str, list[str], bytes]] = []
        self.esmtp_features: dict[str, str] = {}
        FakeSMTP.instances.append(self)

    def ehlo(self):
        self.calls.append("ehlo")
        self.esmtp_features = {"size": self.size_limit}

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(f"login {user}")
        if self.reject_login:
            raise smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")

    def sendmail(self, sender, recipients, data):
        self.calls.append("sendmail")
        self.sent.append((sender, recipients, data))

    def close(self):
        self.calls.append("close")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.calls.append("quit")


class FakeSMTPSSL(FakeSMTP):
    pass


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(FakeSMTP, "size_limit", FakeSMTP.size_limit)
    monkeypatch.setattr(FakeSMTP, "reject_login", False)
    monkeypatch.setattr("glafka.core.mail.smtplib.SMTP", FakeSMTP)
    monkeypatch.setattr("glafka.core.mail.smtplib.SMTP_SSL", FakeSMTPSSL)
    return FakeSMTP


@pytest.fixture
def library(tmp_path):
    return LibraryManager.open(tmp_path / "Library")


@pytest.fixture
def book(library, tmp_path):
    return library.import_file(make_epub(tmp_path / "b.epub", "Butter", "Asako Yuzuki")).book


def test_email_carries_the_book_and_is_logged(smtp, library, book):
    result = library.send_by_email(book.id, CLOUD, GMAIL, "app password")

    assert result.status is SendStatus.SENT
    [server] = smtp.instances
    assert (server.host, server.port) == ("smtp.gmail.com", 587)
    assert server.calls == ["ehlo", "starttls", "ehlo", "login me@gmail.com", "sendmail", "quit"]
    [(sender, recipients, data)] = server.sent
    assert (sender, recipients) == ("me@gmail.com", ["me@pbsync.com"])
    assert b"\n" not in data.replace(b"\r\n", b"")  # CRLF only: servers reject bare LF

    msg = email.message_from_bytes(data, policy=policy.default)
    assert (msg["From"], msg["To"], msg["Subject"]) == ("me@gmail.com", "me@pbsync.com", "Butter")
    [attachment] = msg.iter_attachments()
    assert attachment.get_content_type() == "application/epub+zip"
    assert attachment.get_filename() == "Butter - Asako Yuzuki.epub"
    with open(book.files[0].file_path, "rb") as f:
        assert attachment.get_content() == f.read()
    assert library.books_on_readers() == set()  # on the reader only once a scan finds it
    [entry] = library.history(book.id)
    assert (entry.destination_kind, entry.destination_name, entry.action) == (
        "email",
        "PocketBook (email)",
        HistoryAction.SENT,
    )
    assert (entry.book_title, entry.format) == ("Butter", "epub")


def test_ssl_accounts_never_use_starttls(smtp, library, book):
    account = EmailAccount("me@example.org", "mail.example.org", 465, "ssl", "me")

    library.send_by_email(book.id, CLOUD, account, "pw")

    [server] = smtp.instances
    assert isinstance(server, FakeSMTPSSL)
    assert server.context is not None  # certificates checked
    assert "starttls" not in server.calls
    assert "login me" in server.calls


def test_rejected_password_explains_app_passwords_and_logs_nothing(smtp, library, book):
    smtp.reject_login = True

    with pytest.raises(GlafkaError, match="app password"):
        library.send_by_email(book.id, CLOUD, GMAIL, "my normal password")

    [server] = smtp.instances
    assert server.sent == []
    assert library.history(book.id) == []


def test_too_large_for_the_server_stops_before_logging_in(smtp, library, book):
    smtp.size_limit = "1000"

    with pytest.raises(GlafkaError, match="too large"):
        library.send_by_email(book.id, CLOUD, GMAIL, "pw")

    [server] = smtp.instances
    assert not any(call.startswith("login") for call in server.calls)


def test_no_connection_without_a_suitable_format_or_a_valid_address(smtp, library, book):
    pdf_only = Destination(
        id="p", name="PDF", kind="email", books_folder="", email="a@b.org", formats=["pdf"]
    )
    assert library.send_by_email(book.id, pdf_only, GMAIL, "pw").status is (
        SendStatus.NO_SUITABLE_FORMAT
    )

    injected = Destination(id="x", name="X", kind="email", email="a@b.org\r\nBcc: c@d.org")
    with pytest.raises(GlafkaError, match="no valid email address"):
        library.send_by_email(book.id, injected, GMAIL, "pw")

    assert smtp.instances == []


def test_unknown_encryption_never_falls_back_to_plain_text(smtp, library, book):
    account = EmailAccount("me@example.org", "mail.example.org", 25, "none", "me")

    with pytest.raises(GlafkaError, match="encryption"):
        library.send_by_email(book.id, CLOUD, account, "pw")

    assert smtp.instances == []

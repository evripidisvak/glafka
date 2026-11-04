"""Email books (e.g. to Send-to-PocketBook) through the user's own SMTP account.

Named `mail`, not `email`: a module called `email` here would shadow the standard library.
"""

import re
import smtplib
import ssl
from email import policy
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from glafka.core.db import Database
from glafka.core.errors import GlafkaError, NotFoundError
from glafka.core.history import record
from glafka.core.models import Book, BookFile, Destination, EmailAccount, HistoryAction
from glafka.core.send import SendResult, SendStatus, choose_format, file_name

_TIMEOUT = 60  # seconds per network step, not for the whole upload
_ADDRESS = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_CONTENT_TYPES = {"epub": ("application", "epub+zip"), "pdf": ("application", "pdf")}


def valid_address(address: str) -> bool:
    """Plausible address, one line (also keeps header injection out)."""
    return _ADDRESS.fullmatch(address) is not None


def build_message(book: Book, file: BookFile, sender: str, recipient: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = " ".join(book.title.split())  # a line break would end the header
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.rpartition("@")[2])
    msg.set_content(f"{book.title} by {book.author}, sent from Glafka.\n")
    maintype, subtype = _CONTENT_TYPES.get(file.format, ("application", "octet-stream"))
    msg.add_attachment(
        Path(file.file_path).read_bytes(),
        maintype=maintype,
        subtype=subtype,
        filename=file_name(book, file.format),
    )
    return msg


def send_by_email(
    db: Database, book_id: str, destination: Destination, account: EmailAccount, password: str
) -> SendResult:
    """Email the book as an attachment. SENT means the SMTP server accepted it."""
    book = db.get_book_by_id(book_id)
    if book is None:
        raise NotFoundError(f"Book '{book_id}' not found")
    if not destination.email or not valid_address(destination.email):
        raise GlafkaError(f"{destination.name} has no valid email address")
    if not valid_address(account.address):
        raise GlafkaError("The sending account has no valid email address")
    file = choose_format(book, destination)
    if file is None:
        return SendResult(SendStatus.NO_SUITABLE_FORMAT, book)

    # SMTP policy: CRLF line endings; sendmail() sends bytes exactly as given
    data = build_message(book, file, account.address, destination.email).as_bytes(
        policy=policy.SMTP
    )
    try:
        with _connect(account) as smtp:
            limit = _size_limit(smtp)
            if limit and len(data) > limit:
                raise GlafkaError(
                    f"'{book.title}' is too large to email: {_mb(len(data))} MB encoded, "
                    f"{account.host} takes up to {_mb(limit)} MB"
                )
            smtp.login(account.username or account.address, password)
            smtp.sendmail(account.address, [destination.email], data)
    except smtplib.SMTPAuthenticationError as e:
        raise GlafkaError(_login_failed(account)) from e
    except smtplib.SMTPRecipientsRefused as e:
        raise GlafkaError(f"{account.host} refused the address {destination.email}") from e
    except smtplib.SMTPResponseException as e:
        reply = e.smtp_error.decode(errors="replace") if isinstance(e.smtp_error, bytes) else ""
        raise GlafkaError(f"{account.host} refused the email: {e.smtp_code} {reply}") from e
    except (smtplib.SMTPException, OSError) as e:
        raise GlafkaError(f"Could not send through {account.host}: {e}") from e
    record(db, book, destination, HistoryAction.SENT, file=file)
    return SendResult(SendStatus.SENT, book, file.format)


def _connect(account: EmailAccount) -> smtplib.SMTP:
    """Encrypted connection, ready to log in. There is no unencrypted option."""
    context = ssl.create_default_context()
    if account.security == "ssl":
        smtp: smtplib.SMTP = smtplib.SMTP_SSL(
            account.host, account.port, timeout=_TIMEOUT, context=context
        )
    elif account.security == "starttls":
        smtp = smtplib.SMTP(account.host, account.port, timeout=_TIMEOUT)
    else:
        raise GlafkaError(f"Unknown encryption '{account.security}'")
    try:
        smtp.ehlo()
        if account.security == "starttls":
            smtp.starttls(context=context)  # raises if the server does not offer it
            smtp.ehlo()  # features can differ once encrypted
    except BaseException:
        smtp.close()
        raise
    return smtp


def _size_limit(smtp: smtplib.SMTP) -> int:
    """The largest message the server says it takes; 0 if it doesn't say."""
    value = smtp.esmtp_features.get("size", "")
    return int(value) if value.isdigit() else 0


def _login_failed(account: EmailAccount) -> str:
    if account.provider == "gmail":
        return f"Gmail did not accept the app password for {account.address}"
    return f"{account.host} did not accept the user name and password"


def _mb(size: int) -> str:
    return f"{size / 1_000_000:.1f}"

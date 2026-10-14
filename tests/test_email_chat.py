import json
import mailbox
from email.message import EmailMessage
from pathlib import Path

import pytest

from ark_angel.demo import make_photo
from ark_angel.ingest.chat_source import ChatSource, detect_format, parse_whatsapp
from ark_angel.ingest.email_source import EmailSource, html_to_text, strip_quoted


def _email(subject: str, body: str, *, sender="Sam K <sam@example.org>", msg_id="<m1@example.org>",
           date="Mon, 05 Oct 2026 07:42:00 -0600", html: bool = False, photo: Path = None) -> EmailMessage:
    message = EmailMessage()
    message["From"], message["To"], message["Cc"] = sender, "Tip Line <tips@example.gov>", "boss@example.gov"
    message["Date"], message["Subject"], message["Message-ID"] = date, subject, msg_id
    if html:
        message.set_content(body, subtype="html")
    else:
        message.set_content(body)
    if photo is not None:
        message.add_attachment(photo.read_bytes(), maintype="image", subtype="jpeg", filename=photo.name)
    return message


def test_strip_quoted_drops_replies_and_signatures() -> None:
    body = "Call me at 303-555-0190.\n\nOn Sun, Oct 4 Tip Line wrote:\n> call 303-555-0100\n"
    assert strip_quoted(body) == "Call me at 303-555-0190."
    assert strip_quoted("hello\n> quoted\nstill mine\n-- \nSam\n555-0000") == "hello\nstill mine"


def test_html_to_text_skips_scripts_and_styles() -> None:
    assert " ".join(html_to_text("<style>x{}</style><p>Seen <b>near</b> the bridge</p><script>1</script>").split()) \
        == "Seen near the bridge"


def test_eml_becomes_evidence_without_recipients_in_text(tmp_path: Path) -> None:
    path = tmp_path / "tip.eml"
    body = "Saw her near the station. My cell 303-555-0190.\n\nOn Sun Tip Line wrote:\n> 303-555-0100\n"
    path.write_bytes(bytes(_email("Sighting", body)))

    result = EmailSource().load(path)
    item, = result.evidence
    assert item.identifier == "tip:m1"
    assert item.content == ("2026-10-05 07:42 From: Sam K <sam@example.org> | Subject: Sighting | "
                            "Saw her near the station. My cell 303-555-0190.")
    assert "tips@example.gov" not in item.content and "303-555-0100" not in item.content
    assert item.metadata["recipients"] == ["tips@example.gov", "boss@example.gov"]
    assert item.metadata["timestamp"] == "2026-10-05 07:42:00"  # sender's wall clock, not converted


def test_html_only_email_and_geotagged_attachment(tmp_path: Path) -> None:
    photo = make_photo(tmp_path / "bench.jpg", 39.7521, -104.9993, "2026:10:01 02:58:00", "Apple", "iPhone 12")
    path = tmp_path / "tip.eml"
    path.write_bytes(bytes(_email("Photo", "<p>See <b>attached</b></p>", html=True, photo=photo)))

    result = EmailSource(source="hotline").load(path)
    message, image = result.evidence
    assert "See attached" in message.content and "Attachments: bench.jpg" in message.content
    assert image.identifier == "tip:m1/1-bench.jpg"
    assert image.metadata["source"] == "hotline" and image.metadata["attached_to"] == "tip:m1"
    assert abs(image.metadata["latitude"] - 39.7521) < 1e-4
    assert image.metadata["timestamp"] == "2026:10:01 02:58:00"
    assert result.attachments_with_gps == 1


def test_mbox_and_folder_with_duplicate_message_ids(tmp_path: Path) -> None:
    box = mailbox.mbox(str(tmp_path / "inbox.mbox"))
    box.add(_email("one", "first 303-555-0190"))
    box.add(_email("two", "second", msg_id="<m1@other.org>"))  # same local part, different domain
    box.add(_email("three", "third", msg_id=""))
    box.flush()
    ids = [e.identifier for e in EmailSource().load(tmp_path / "inbox.mbox").evidence]
    assert ids == ["inbox:m1", "inbox:m1@2", "inbox:3"]

    folder = tmp_path / "mail"
    folder.mkdir()
    (folder / "a.eml").write_bytes(bytes(_email("a", "x", msg_id="<a@x>")))
    (folder / "b.eml").write_bytes(bytes(_email("b", "y", msg_id="<b@x>")))
    assert [e.identifier for e in EmailSource().load(folder).evidence] == ["mail:a", "mail:b"]


WHATSAPP = """10/02/26, 8:15 PM - Messages and calls are end-to-end encrypted.
10/02/26, 8:15 PM - Jordan: license plate ABC 1234 again,
parked behind the rail yard
10/02/26, 8:20 PM - Jordan: <Media omitted>
10/02/26, 8:21 PM - Jordan added Sam
a stray line after a system message
[10/3/26, 9:02:05 AM] Priya: flyers near Union Station
"""


def test_whatsapp_android_and_ios_lines() -> None:
    messages, skipped = parse_whatsapp(WHATSAPP)
    assert [(m.sender, " ".join(m.text.split())) for m in messages] == [
        ("Jordan", "license plate ABC 1234 again, parked behind the rail yard"),
        ("Priya", "flyers near Union Station"),
    ]
    assert skipped == 3


def test_chat_source_whatsapp_dates_and_dayfirst(tmp_path: Path) -> None:
    path = tmp_path / "group.txt"
    path.write_text(WHATSAPP, encoding="utf-8")
    result = ChatSource().load(path)
    assert result.format == "whatsapp"
    assert [e.identifier for e in result.evidence] == ["group:1", "group:2"]
    assert result.evidence[0].content.startswith("2026-10-02 20:15 Jordan: license plate ABC 1234")
    assert result.evidence[1].metadata == {"source": "group", "sender": "Priya", "timestamp": "2026-10-03 09:02:05"}

    euro = tmp_path / "euro.txt"
    euro.write_text("02.10.26, 20:15 - Ana: hola\n", encoding="utf-8")
    assert ChatSource(dayfirst=True).load(euro).evidence[0].metadata["timestamp"] == "2026-10-02 20:15:00"


def test_chat_source_telegram_json(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"messages": [
        {"type": "message", "date": "2026-10-01T16:45:00", "from": "Ana",
         "text": ["see ", {"type": "mention", "text": "@rt_owl"}]},
        {"type": "service", "action": "join_group_by_link"},
        {"type": "message", "date": "2026-10-01T16:46:00", "from": "Ben", "text": ""},
    ]}), encoding="utf-8")
    result = ChatSource().load(path)
    assert result.format == "telegram" and result.skipped == 2
    assert result.evidence[0].content == "2026-10-01 16:45 Ana: see @rt_owl"


def test_detect_format_refuses_plain_text(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("just some notes\nnothing chat-like\n")
    with pytest.raises(ValueError, match="doesn't look like"):
        detect_format(path)

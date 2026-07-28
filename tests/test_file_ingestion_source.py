from pathlib import Path

from ark_angel.ingest.file_source import FileIngestionSource


def test_fetch_leads_skips_blank_lines(tmp_path: Path) -> None:
    source_file = tmp_path / "notes.txt"
    source_file.write_text("first line\n\n  \nsecond line\n")

    leads = FileIngestionSource().fetch_leads(str(source_file))

    assert [lead.summary for lead in leads] == ["first line", "second line"]
    assert [lead.identifier for lead in leads] == ["notes.txt:1", "notes.txt:4"]

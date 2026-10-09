from pathlib import Path

from click.testing import CliRunner

from ark_angel import __version__
from ark_angel.cli import cli

runner = CliRunner()


def test_version_importable() -> None:
    assert __version__ == "0.1.0"


def test_cli_help() -> None:
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Ark Angel" in result.output


def test_end_to_end_workflow() -> None:
    with runner.isolated_filesystem():
        Path("notes.txt").write_text("Reach out to jane@example.com\nNo contact info\n")

        result = runner.invoke(cli, ["case", "create", "C1", "Missing Person Case"])
        assert result.exit_code == 0, result.output

        result = runner.invoke(cli, ["ingest", "C1", "notes.txt"])
        assert result.exit_code == 0, result.output
        assert "Ingested 2 evidence item(s)" in result.output

        result = runner.invoke(cli, ["enrich", "C1"])
        assert result.exit_code == 0, result.output
        assert "Generated 1 lead(s)" in result.output

        result = runner.invoke(cli, ["report", "C1"])
        assert result.exit_code == 0, result.output
        assert "email present" in result.output


def test_ingest_and_enrich_record_provenance() -> None:
    with runner.isolated_filesystem():
        Path("notes.txt").write_text("Reach out to jane@example.com\n")

        runner.invoke(cli, ["case", "create", "C1", "Case One"])
        runner.invoke(cli, ["ingest", "C1", "notes.txt"])
        runner.invoke(cli, ["enrich", "C1"])

        from ark_angel.cases.store import FileCaseStore

        case = FileCaseStore(".ark_angel/cases").get_case("C1")
        assert case.evidence[0].metadata == {"source": "file"}
        assert case.leads[0].metadata == {"produced_by": "rules"}


def test_plugins_command_lists_registered_tools() -> None:
    result = runner.invoke(cli, ["plugins"])
    assert result.exit_code == 0, result.output
    assert "Ingestion sources: file, image, username" in result.output
    assert "Analyzers: rules" in result.output
    assert "Geo resolvers: exif" in result.output


def test_username_command_records_provenance() -> None:
    from unittest.mock import MagicMock, patch

    from ark_angel.cases.store import FileCaseStore

    response = MagicMock()
    response.status_code = 200
    with runner.isolated_filesystem():
        runner.invoke(cli, ["case", "create", "C1", "Case One"])
        with patch("ark_angel.ingest.username_source.requests.get", return_value=response):
            result = runner.invoke(cli, ["username", "C1", "janedoe"])
        assert result.exit_code == 0, result.output

        case = FileCaseStore(".ark_angel/cases").get_case("C1")
        assert case.evidence and case.leads
        assert all(item.metadata == {"source": "username"} for item in case.evidence)
        assert all(lead.metadata == {"produced_by": "username"} for lead in case.leads)


def test_ingest_image_command_records_provenance() -> None:
    from unittest.mock import patch

    import piexif

    from ark_angel.cases.store import FileCaseStore

    fake_exif = {
        "GPS": {},
        "0th": {piexif.ImageIFD.Make: b"Apple", piexif.ImageIFD.Model: b"iPhone 14"},
        "Exif": {},
    }
    with runner.isolated_filesystem():
        Path("photo.jpg").write_bytes(b"not a real image")
        runner.invoke(cli, ["case", "create", "C1", "Case One"])
        with patch("ark_angel.ingest.image_source._load_exif", return_value=fake_exif):
            result = runner.invoke(cli, ["ingest-image", "C1", "photo.jpg"])
        assert result.exit_code == 0, result.output

        case = FileCaseStore(".ark_angel/cases").get_case("C1")
        assert case.evidence[0].metadata == {"source": "image"}
        assert case.leads[0].metadata == {"produced_by": "image"}


def test_case_list_is_sorted() -> None:
    with runner.isolated_filesystem():
        runner.invoke(cli, ["case", "create", "B", "Case B"])
        runner.invoke(cli, ["case", "create", "A", "Case A"])

        result = runner.invoke(cli, ["case", "list"])
        assert result.output.splitlines() == ["A", "B"]

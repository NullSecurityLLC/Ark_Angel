from pathlib import Path

from click.testing import CliRunner

from ark_angel import __version__
from ark_angel.cli import cli

runner = CliRunner()


def _run(tmp_path: Path, *args: str):
    return runner.invoke(cli, ["--data-dir", str(tmp_path / "cases"), *args])


def test_version_importable() -> None:
    assert __version__ == "0.1.0"


def test_cli_help() -> None:
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Ark Angel" in result.output


def test_end_to_end_workflow(tmp_path: Path) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("Reach out to jane@example.com\nNo contact info\n")

    result = _run(tmp_path, "case", "create", "C1", "Missing Person Case")
    assert result.exit_code == 0, result.output

    result = _run(tmp_path, "ingest", "C1", str(notes))
    assert result.exit_code == 0, result.output
    assert "Ingested 2 evidence item(s)" in result.output

    result = _run(tmp_path, "enrich", "C1")
    assert result.exit_code == 0, result.output
    assert "Generated 1 lead(s)" in result.output

    result = _run(tmp_path, "report", "C1")
    assert result.exit_code == 0, result.output
    assert "email present" in result.output
    assert "jane@example.com" in result.output


def test_enrich_twice_does_not_duplicate_leads(tmp_path: Path) -> None:
    notes = tmp_path / "tips.txt"
    notes.write_text("call 303-555-0147\nsame number (303) 555-0147\n")
    _run(tmp_path, "case", "create", "C1", "t")
    _run(tmp_path, "ingest", "C1", str(notes))

    _run(tmp_path, "enrich", "C1")
    first = _run(tmp_path, "case", "show", "C1").output
    _run(tmp_path, "enrich", "C1")
    assert _run(tmp_path, "case", "show", "C1").output == first

    report = _run(tmp_path, "report", "C1").output
    assert "Phone 3035550147 appears in 2 items" in report


def test_case_list_is_sorted(tmp_path: Path) -> None:
    _run(tmp_path, "case", "create", "B", "Case B")
    _run(tmp_path, "case", "create", "A", "Case A")

    result = _run(tmp_path, "case", "list")
    assert result.output.splitlines() == ["A", "B"]


def test_errors_are_clean_messages_not_tracebacks(tmp_path: Path) -> None:
    missing = _run(tmp_path, "case", "show", "NOPE")
    assert missing.exit_code == 1
    assert "no such case: 'NOPE'" in missing.output

    bad = _run(tmp_path, "case", "create", "../escape", "x")
    assert bad.exit_code == 1
    assert "invalid case id" in bad.output

    _run(tmp_path, "case", "create", "C1", "x")
    dup = _run(tmp_path, "case", "create", "C1", "x")
    assert dup.exit_code == 1
    assert "already exists" in dup.output


def test_report_formats_and_out_file(tmp_path: Path) -> None:
    _run(tmp_path, "case", "create", "C1", "t")
    for fmt in ("md", "json", "html"):
        out = tmp_path / f"r.{fmt}"
        result = _run(tmp_path, "report", "C1", "--format", fmt, "--out", str(out))
        assert result.exit_code == 0, result.output
        assert out.read_text()


def test_pivot_without_online_only_lists_handles(tmp_path: Path) -> None:
    notes = tmp_path / "n.txt"
    notes.write_text("friend uses @rt_owl\n")
    _run(tmp_path, "case", "create", "C1", "t")
    _run(tmp_path, "ingest", "C1", str(notes))

    result = _run(tmp_path, "pivot", "C1")
    assert result.exit_code == 0, result.output
    assert "@rt_owl" in result.output
    assert "--online" in result.output


def test_place_commands_round_trip_and_feed_enrich(tmp_path: Path) -> None:
    tips = tmp_path / "tips.txt"
    tips.write_text("Caller saw her near Union Station\nNeighbor says the station again\n")
    gazetteer = tmp_path / "places.csv"
    gazetteer.write_text("name,latitude,longitude,aliases,radius_m\nBoulder,40.015,-105.2705,,6000\n")
    _run(tmp_path, "case", "create", "C1", "t")
    _run(tmp_path, "ingest", "C1", str(tips))

    assert "Union Station" in _run(tmp_path, "place", "suggest", "C1").output

    result = _run(tmp_path, "place", "add", "C1", "Union Station", "39.7527", "-104.9997", "--alias", "the station")
    assert result.exit_code == 0, result.output
    assert _run(tmp_path, "place", "import", "C1", str(gazetteer)).exit_code == 0
    listing = _run(tmp_path, "place", "list", "C1").output
    assert "Union Station" in listing and "aka the station" in listing and "Boulder" in listing

    _run(tmp_path, "enrich", "C1")
    assert "Location cluster around Union Station: 2 items" in _run(tmp_path, "report", "C1").output

    assert _run(tmp_path, "place", "remove", "C1", "boulder").exit_code == 0
    assert "Boulder" not in _run(tmp_path, "place", "list", "C1").output


def test_place_errors_are_clean(tmp_path: Path) -> None:
    _run(tmp_path, "case", "create", "C1", "t")
    assert "out of range" in _run(tmp_path, "place", "add", "C1", "X", "99", "0").output
    assert "no place named" in _run(tmp_path, "place", "remove", "C1", "nope").output
    offline = _run(tmp_path, "place", "lookup", "C1", "Union Station")
    assert offline.exit_code == 1 and "--online" in offline.output

    bad = tmp_path / "bad.csv"
    bad.write_text("name,latitude,longitude\nX,not-a-number,0\n")
    assert "bad gazetteer row" in _run(tmp_path, "place", "import", "C1", str(bad)).output

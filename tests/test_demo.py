from pathlib import Path

from click.testing import CliRunner

from ark_angel.cli import cli


def test_demo_runs_offline_and_surfaces_planted_patterns(tmp_path: Path) -> None:
    out = tmp_path / "demo.html"
    result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path / "cases"), "demo", "--out", str(out)])

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    open_section = lines[lines.index("Top open leads") + 1:]
    top = [line for line in open_section if line.strip()[:2] in {"1.", "2.", "3.", "4.", "5."}]
    assert "Location cluster around Union Station: 10 items" in top[0]
    assert "Handle rt_demo_owl appears in 7 items" in top[1]
    assert "Plate ABC1234 appears in 4 items" in top[2]
    assert "Email sam.k.demo@example.org appears in 3 items" in top[3]
    assert "Place Boulder appears in 3 items" in top[4]
    assert "unresolved place(s): Union Station, Boulder, Cherry Creek" in result.output
    assert "imported 10 tips from the tip-line export tips.csv" in result.output
    assert "imported  4 messages from the volunteers' WhatsApp export" in result.output
    assert "imported 1 email with 1 geotagged photo attachment" in result.output
    assert "resolved Boulder from the built-in US gazetteer: Boulder, CO (city" in result.output
    verified = lines[lines.index("Verified") + 1]
    assert "Phone 3035550147" in verified
    assert "RESURFACED: dismissed by Det. Demo (simulated)" in result.output
    assert "1 new item(s) since: late_tips.txt:1" in result.output
    page = out.read_text()
    assert "Synthetic demo" in page and "rt_demo_owl" in page


def test_demo_is_rerunnable(tmp_path: Path) -> None:
    args = ["--data-dir", str(tmp_path / "cases"), "demo", "--out", str(tmp_path / "d.html")]
    CliRunner().invoke(cli, args)
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output


def test_demo_links_vague_boulder_tip_to_old_photo(tmp_path: Path) -> None:
    data_dir = str(tmp_path / "cases")
    CliRunner().invoke(cli, ["--data-dir", data_dir, "demo", "--out", str(tmp_path / "d.html")])
    report = CliRunner().invoke(cli, ["--data-dir", data_dir, "report", "DEMO"]).output
    assert "Place Boulder appears in 3 items across 3 sources" in report
    assert "image:trailhead_old.jpg" in report

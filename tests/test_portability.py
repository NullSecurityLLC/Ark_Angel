"""Guards against platform-dependent text handling (Windows defaults to cp1252)."""
import re
from pathlib import Path

from ark_angel.ingest.file_source import FileIngestionSource

SRC = Path(__file__).resolve().parent.parent / "src" / "ark_angel"


def test_every_text_read_and_write_names_an_encoding() -> None:
    offenders = []
    for path in SRC.rglob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if re.search(r"\.(read_text|write_text)\(", line) and "encoding=" not in line:
                offenders.append(f"{path.relative_to(SRC)}:{number}: {line.strip()}")
            if re.search(r"(?<![.\w])open\([^)]*\)", line) and "encoding=" not in line and '"rb"' not in line:
                offenders.append(f"{path.relative_to(SRC)}:{number}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


def test_plain_text_evidence_reads_utf8_bom_and_cp1252(tmp_path: Path) -> None:
    utf8 = tmp_path / "a.txt"
    utf8.write_bytes("﻿café 🙏\n".encode("utf-8"))
    legacy = tmp_path / "b.txt"
    legacy.write_bytes("café – corner\n".encode("cp1252"))
    assert FileIngestionSource().fetch_leads(str(utf8))[0].summary == "café 🙏"
    assert FileIngestionSource().fetch_leads(str(legacy))[0].summary == "café – corner"

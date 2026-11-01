"""Prevent the Windows console conversion that damaged production accents."""

from pathlib import Path
import re


def test_application_sql_and_instructions_preserve_utf8():
    root = Path(__file__).resolve().parents[1]
    suspicious = re.compile(r"\u00c3[\u0080-\u00bf]|\u00c2[\u0080-\u00bf]|\u00e2\u20ac|\ufffd|\b(?:Evaluaci|Operaci|Informaci|Planificaci|Producci|Distribuci|Revisi)\?n\b", re.IGNORECASE)
    paths = [path for directory in ("app", "alembic", "scripts", "docs") for path in (root / directory).rglob("*") if path.suffix in {".py", ".sql", ".md"}]
    failures = []
    for path in paths:
        try:
            content = path.read_text(encoding="utf-8-sig", errors="strict")
            if suspicious.search(content): failures.append(str(path.relative_to(root)))
        except UnicodeError:
            failures.append(str(path.relative_to(root)))
    assert not failures, f"Invalid UTF-8 or damaged accents: {failures}"

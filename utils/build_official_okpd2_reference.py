"""Build the bundled official OKPD2 name reference from the Rosstat archive DOCX files."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from docx import Document


CODE_RE = re.compile(r"^\d{2}(?:\.\d{1,3}){0,4}$")


def extract_names(source_dir: Path) -> dict[str, str]:
    names: dict[str, str] = {}
    for path in sorted(source_dir.glob("*.docx")):
        tables = Document(path).tables
        for row in tables[-1].rows:
            code = row.cells[0].text.strip()
            value = row.cells[1].text.replace("\xa0", " ").strip()
            if CODE_RE.fullmatch(code) and value:
                names[code] = value.splitlines()[0].strip()
    return names


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    names = extract_names(args.source_dir)
    payload = {
        "source": "Росстат, ОК 034-2014 (КПЕС 2008)",
        "source_url": "https://rosstat.gov.ru/classification",
        "source_updated": "2026-09-02",
        "codes": names,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(names)} official OKPD2 names to {args.output}")


if __name__ == "__main__":
    main()

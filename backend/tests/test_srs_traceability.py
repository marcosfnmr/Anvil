"""Keep the SRS traceability matrix complete as requirements evolve."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / "docs" / "SRS_TRACEABILITY.md"
SOURCE_SRS = ROOT / "docs" / "SRS_REQUIREMENTS_v1.md"


def _expand_requirement_cell(cell: str) -> set[str]:
    """Expand identifiers and compact ranges from a matrix requirement cell."""
    covered: set[str] = set()
    for prefix, first, last in re.findall(
        r"\b(RF|RNF|RT|IF|DT|CA|SU)-(\d{2})(?:\.\.(\d{2}))?\b", cell
    ):
        start = int(first)
        end = int(last or first)
        if end < start:
            raise AssertionError(f"Invalid descending requirement range: {prefix}-{first}..{last}")
        covered.update(f"{prefix}-{number:02d}" for number in range(start, end + 1))
    return covered


class SrsTraceabilityTests(unittest.TestCase):
    def test_requirement_matrix_covers_every_identifier_in_the_source_srs(self) -> None:
        source_srs = SOURCE_SRS.read_text(encoding="utf-8")
        expected = set(re.findall(r"\*\*((?:RF|RNF|RT|IF|DT|CA|SU)-\d{2})\*\*", source_srs))
        self.assertEqual(len(expected), 84, "Update the source version/count note when the SRS changes")
        source = MATRIX.read_text(encoding="utf-8")

        # Count rows that pair requirement identifiers with an explicit status;
        # priority and evidence tables do not have a status in the second cell.
        mapped: set[str] = set()
        for line in source.splitlines():
            if not line.startswith("|"):
                continue
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) >= 2 and cells[1].startswith(("Hecho", "Parcial", "Futuro", "Procedimiento requerido")):
                mapped.update(_expand_requirement_cell(cells[0]))

        self.assertEqual(expected - mapped, set(), "SRS identifiers without a primary matrix row")
        self.assertEqual(mapped - expected, set(), "Unexpected identifiers in primary SRS matrix")

        declared = re.search(r"matriz incluya los (\d+):", source)
        self.assertIsNotNone(declared, "Document the source SRS identifier count")
        self.assertEqual(int(declared.group(1)), len(expected))


if __name__ == "__main__":
    unittest.main()

"""Garante que o codigo do agente NUNCA referencia o gabarito.

O agente so pode enxergar agent-input/cases.json e os schemas de ferramenta.
Estes tokens/arquivos sao gabarito e nao podem aparecer em agent/:
  - eval/expected-paths.json, eval/expected-decisions.json
  - eval/test-scenarios.md, docs/test-scenarios.md
  - data/cases.parquet e as colunas root_question / mode / expected_path
"""
from __future__ import annotations

import pathlib

import pytest

_REPO = pathlib.Path(__file__).resolve().parent.parent
AGENT_DIR = _REPO / "agent"

FORBIDDEN = [
    "expected-paths", "expected_paths",
    "expected-decisions", "expected_decisions",
    "test-scenarios", "test_scenarios",
    "root_question", "expected_path",
    "cases.parquet",
    "/eval/", "eval.oracle", "eval.analyze",
]


@pytest.mark.parametrize("pyfile", sorted(AGENT_DIR.glob("*.py")), ids=lambda p: p.name)
def test_agent_file_has_no_gabarito_reference(pyfile: pathlib.Path) -> None:
    text = pyfile.read_text().lower()
    hits = [tok for tok in FORBIDDEN if tok.lower() in text]
    assert not hits, f"{pyfile.name} referencia gabarito: {hits}"


def test_agent_only_reads_agent_input() -> None:
    """dataset.py deve apontar so para agent-input/cases.json."""
    ds = (AGENT_DIR / "dataset.py").read_text()
    assert "agent-input" in ds and "cases.json" in ds
    assert "parquet" not in ds.lower()
    assert "expected" not in ds.lower()


def test_expected_decisions_is_quarantined_location() -> None:
    """O gabarito curado fica em eval/, nunca em agent-input/."""
    assert (_REPO / "inteli-tractian-project" / "eval" / "expected-decisions.json").exists()
    assert not (_REPO / "inteli-tractian-project" / "agent-input" / "expected-decisions.json").exists()

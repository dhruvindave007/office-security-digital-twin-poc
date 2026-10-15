"""The eval's uncertainty detector (it once contained literal backspace characters and never matched)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from evals.investigation.run_eval import admits_uncertainty  # noqa: E402


def test_detects_hedged_answers():
    for t in ["the evidence does not verify which person either track represents",
              "there is no record of a badge for this track",
              "we cannot determine who entered",
              "it is not possible to tell from the footage"]:
        assert admits_uncertainty(t), t


def test_plain_assertions_are_not_hedges():
    for t in ["track sim_e2_a entered the server room at 11:00:02",
              "the occupancy was 3 people, above the maximum of 2",
              "the knowledge base shows nothing"]:      # 'no' inside 'knowledge' must not count
        assert not admits_uncertainty(t), t

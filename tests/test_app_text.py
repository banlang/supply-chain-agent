"""
Pins the user-facing wording of app.py (headless Streamlit AppTest).

The decisions log is pointed at an empty file in tmp_path, so every AI decision
is pending and the planner cards render; nothing is written to logs/.
"""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).parent.parent
pytestmark = pytest.mark.skipif(not (ROOT / "demo_output.json").exists(),
                                reason="demo_output.json not generated (python demo_output.py)")


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.chdir(ROOT)
    mp.setattr("src.review.DECISIONS_LOG", tmp_path_factory.mktemp("logs") / "decisions_log.jsonl")
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    yield at
    mp.undo()


def _markdown(at):
    return [m.value for m in at.markdown]


def test_renders_without_errors(app):
    assert len(app.exception) == 0


def test_executive_headline(app):
    assert ("### 85 of 100 SKUs flagged at risk under a 30-day demand assumption; "
            "Supplier 3 carries 233 of 352 units (66%) in orders needing action now.") in _markdown(app)


def test_executive_decisions_tile(app):
    tile = next(m for m in app.metric if m.label == "Decisions approved / overridden / pending")
    assert (tile.value, tile.delta) == ("0 approved · 0 overridden", "5 pending")


def test_planner_card_wording(app):
    assert any("recorded revenue $" in m and "(dataset field, not lost-sales forecast)" in m
               for m in _markdown(app))
    assert not any("revenue at risk" in m for m in _markdown(app))
    labels = [m.label for m in app.metric]
    assert "Recommended supplier lead time" in labels and "Supplier lead time" not in labels


@pytest.mark.skipif(not (ROOT / "logs" / "run_log.jsonl").exists(), reason="no pipeline run logged")
def test_ai_ops_cost_wording(app):
    assert "LLM token cost per decision (est.)" in [m.label for m in app.metric]
    assert any(m.startswith("### Estimated LLM token cost $") for m in _markdown(app))

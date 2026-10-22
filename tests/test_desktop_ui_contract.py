"""The graph editor's metric table stays equal to the evaluator's metric schema (Task 10.5, D7).

`desktop/src/graph-model.ts` keeps the text between ``export const EVALUATOR_METRICS = `` and `` as const;`` valid JSON
(quoted keys, no trailing comma, no comment inside), so this test reads it without a TypeScript toolchain.
"""

import json
from pathlib import Path

from tuning_factory.evaluation import metric_schema

GRAPH_MODEL = Path(__file__).resolve().parents[1] / "desktop" / "src" / "graph-model.ts"
START, END = "export const EVALUATOR_METRICS = ", " as const;"
EVALUATOR_KINDS = ("text", "classification", "extraction", "json", "kbf_extraction", "pipeline")


def test_ui_metric_table_matches_metric_schema():
    source = GRAPH_MODEL.read_text(encoding="utf-8")
    assert source.count(START) == 1
    start = source.index(START) + len(START)
    table = json.loads(source[start:source.index(END, start)])
    assert table == {kind: [definition.name for definition in metric_schema(kind)] for kind in EVALUATOR_KINDS}

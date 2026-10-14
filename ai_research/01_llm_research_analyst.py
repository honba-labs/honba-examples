"""ai_research/01_llm_research_analyst: translate a natural-language query into a validated screener filter.

Uses the offline `ScriptedFakeLlm` (the LLM port's deterministic test double) and the
local `KnowledgePack` (metric catalog, grammar, presets) to run the ask pipeline:
question → system prompt → LLM → filter text → parse/validate (repair loop) → `AskResult`.

No network, no API keys, no real LLM — the `ScriptedFakeLlm` feeds a scripted response
that the test suite also uses. The live CLI (`honba screener ask`) defaults to the same
fake LLM; swapping in a real provider (`LlmPort` impl) is the only change for production.

Run::

    python ai_research/01_llm_research_analyst.py --query "large cap stocks with volume > 1m"
    python ai_research/01_llm_research_analyst.py --out analyst.json

Pitfalls::

    - The fake LLM proves the plumbing, not a model: ScriptedFakeLlm returns scripted text
      whatever the prompt says. The default echoes your query as the "filter", which fails
      validation, and the repair call then gets "" which parses as an empty AND group (filter_text
      "", no items), which applies no condition at all. A real model needs its own evaluation.
    - Validation checks form, not meaning: parse_filters and the metric catalog reject unknown
      metrics and bad syntax (the defence against hallucinated fields), but a flipped comparison
      or a wrong threshold parses fine. Read filter_text back against your question.
    - Untrusted text both ways: the query is pasted into the prompt, and the model output is only
      ever parsed by the filter grammar, never executed. Keep it that way; do not widen the
      parser to accept free text from a model.
    - Non-determinism: temperature is 0.0 and seed is None, which does not guarantee identical
      output across runs, versions or providers. The result records raw_responses, the
      knowledge content_hash and the full llm_call_history so a run can be audited and replayed.
    - Translation only: the filter is never run here, so no as-of date is applied. A real model
      may recall what happened after a historic date, so keep it to translating the query and
      never ask it which stocks to pick.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # plain checkout: no editable install

from honba.ai.ask import AskResult, build_system_prompt, translate_query
from honba.ai.knowledge import KnowledgePack, build_knowledge_pack
from honba.ai.llm.provider import LlmPort, ScriptedFakeLlm
from honba.screener.catalog import MetricCatalog, load_catalog

from honba_examples.cli import add_out_arg, emit_json, make_parser

__all__ = ["main", "run"]


def run(
    query: str = "large cap stocks with volume > 1m",
    *,
    fake_response: str | None = None,
    catalog: MetricCatalog | None = None,
    max_repairs: int = 2,
) -> dict[str, Any]:
    """Run the analyst pipeline and return a JSON-serializable result."""
    # Build the knowledge pack from the local catalog (offline, content-hashed)
    knowledge: KnowledgePack = build_knowledge_pack(version="1.0.0")

    # Use the fake LLM with a deterministic response (or a provided one for tests)
    if fake_response is None:
        # Default: the shipped CLI's fallback — just echo the query as the filter
        fake_response = query
    llm: LlmPort = ScriptedFakeLlm(responses=[fake_response])

    # Execute the ask pipeline (repair loop is handled inside)
    result: AskResult = translate_query(
        user_query=query,
        llm=llm,
        catalog=catalog or load_catalog(),
        max_repairs=max_repairs,
    )

    # The system prompt the LLM saw — useful for debugging what knowledge was injected
    sys_prompt = build_system_prompt(knowledge)

    return {
        "query": query,
        "knowledge": {
            "version": knowledge.version,
            "content_hash": knowledge.content_hash,
            "metrics": len(knowledge.metrics),
            "presets": knowledge.presets,
            "units": knowledge.units,
            "enums": {k: len(v) for k, v in knowledge.enums.items()},
        },
        "system_prompt_chars": len(sys_prompt),
        "result": {
            "filter_text": result.filter_text,
            "filter_group": result.filter_group.model_dump() if result.filter_group else None,
            "repair_count": result.repair_count,
            "raw_responses": result.raw_responses,
        },
        "llm_call_history": llm.call_history,
    }


def main(argv: list[str] | None = None) -> int:
    parser = make_parser(__doc__)
    parser.add_argument(
        "--query", default="large cap stocks with volume > 1m", help="natural-language question"
    )
    parser.add_argument(
        "--fake-response", default=None, help="override the scripted LLM response (for testing)"
    )
    add_out_arg(parser)
    args = parser.parse_args(argv)

    result = run(args.query, fake_response=args.fake_response)

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

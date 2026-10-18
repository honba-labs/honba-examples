"""ai_research/01_llm_research_analyst: translate a natural-language query into a validated screener filter.

Uses the offline `ScriptedFakeLlm` (the LLM port's deterministic test double) and the
local `KnowledgePack` (metric catalog, grammar, presets) to run the ask pipeline:
question → system prompt → LLM → filter text → parse/validate (repair loop) → `AskResult`.

No network, no API keys, no real LLM — the `ScriptedFakeLlm` feeds a scripted response
that the test suite also uses. The live CLI (`honba screener ask`) defaults to the same
fake LLM; swapping in a real provider (`LlmPort` impl) is the only change for production.

Run::

    python ai_research/01_llm_research_analyst.py --query "large cap stocks with volume > 1m"
    python ai_research/01_llm_research_analyst.py --query "volume > 5m" --fake-response "volume > 5000000"
    python ai_research/01_llm_research_analyst.py --out analyst.json

Pitfalls::

    - The fake LLM proves the plumbing, not a model: ScriptedFakeLlm returns scripted text
      whatever the prompt says. For the default query it first echoes the query (which fails
      validation) and then returns a fixed valid filter on the repair call, so the default run
      shows one repair and a real filter. Any other query needs --fake-response, otherwise the
      empty repair is rejected with EmptyFilterError. A real model needs its own evaluation.
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
from typing import Any

from honba.ai.ask import AskResult, build_system_prompt, translate_query
from honba.ai.knowledge import KnowledgePack, build_knowledge_pack
from honba.ai.llm.provider import LlmPort, ScriptedFakeLlm
from honba.screener.catalog import MetricCatalog, load_catalog

from honba_examples.cli import add_out_arg, emit_json, make_parser

__all__ = ["DEFAULT_FILTER", "DEFAULT_QUERY", "EmptyFilterError", "main", "run"]

DEFAULT_QUERY = "large cap stocks with volume > 1m"
# What the offline fake "repairs" DEFAULT_QUERY to: the metric catalog's market cap and volume.
DEFAULT_FILTER = "market_cap_basic > 100000000000 and volume > 1000000"


class EmptyFilterError(ValueError):
    """The model's reply parsed, but to a filter with no condition (it would match everything)."""


def run(
    query: str = DEFAULT_QUERY,
    *,
    fake_response: str | None = None,
    catalog: MetricCatalog | None = None,
    max_repairs: int = 2,
) -> dict[str, Any]:
    """Run the analyst pipeline and return a JSON-serializable result."""
    # Build the knowledge pack from the local catalog (offline, content-hashed)
    knowledge: KnowledgePack = build_knowledge_pack(version="1.0.0")

    # Use the fake LLM with a deterministic script (or a provided single response for tests).
    # Default: first echo the query as the "filter" (fails validation, like a bad model reply),
    # then, for the default query only, answer the repair prompt with DEFAULT_FILTER. Any other
    # query gets an empty repair, which is rejected below: the fake does not know it.
    responses: list[str]
    if fake_response is not None:
        responses = [fake_response]
    elif query == DEFAULT_QUERY:
        responses = [query, DEFAULT_FILTER]
    else:
        responses = [query]
    llm: LlmPort = ScriptedFakeLlm(responses=responses)

    # Execute the ask pipeline (repair loop is handled inside)
    result: AskResult = translate_query(
        user_query=query,
        llm=llm,
        catalog=catalog or load_catalog(),
        max_repairs=max_repairs,
    )

    # An empty filter parses (as an empty AND group) but selects every stock: never accept it.
    if not result.filter_text.strip() or not result.filter_group.items:
        raise EmptyFilterError(
            f"the model returned an empty filter for {query!r} after {result.repair_count} "
            "repair(s); refusing to treat 'no condition' as a valid screen. The offline fake "
            f"only knows {DEFAULT_QUERY!r}; pass --fake-response for other queries."
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
    parser.add_argument("--query", default=DEFAULT_QUERY, help="natural-language question")
    parser.add_argument(
        "--fake-response", default=None, help="override the scripted LLM response (for testing)"
    )
    add_out_arg(parser)
    args = parser.parse_args(argv)

    try:
        result = run(args.query, fake_response=args.fake_response)
    except EmptyFilterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    emit_json(result, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

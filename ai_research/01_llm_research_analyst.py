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
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    import honba_examples  # noqa: F401
except ModuleNotFoundError:  # plain checkout without `pip install -e .`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honba.ai.ask import AskResult, build_system_prompt, translate_query
from honba.ai.knowledge import KnowledgePack, build_knowledge_pack
from honba.ai.llm.provider import LlmPort, ScriptedFakeLlm
from honba.screener.catalog import MetricCatalog, load_catalog

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
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--query", default="large cap stocks with volume > 1m", help="natural-language question")
    parser.add_argument("--fake-response", default=None, help="override the scripted LLM response (for testing)")
    parser.add_argument("--out", type=Path, default=None, help="also write the JSON here")
    args = parser.parse_args(argv)

    result = run(args.query, fake_response=args.fake_response)

    text = json.dumps(result, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
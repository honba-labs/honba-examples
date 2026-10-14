# 09 Ai Research – the research loop on top of Honba

The AI-research examples show the research loop layered on Honba: natural-language
query → validated filter, autonomous backtesting, NL→strategy verification,
reinforcement learning, and the MCP tool gateway. There is no trading strategy
here. Two steps are runnable offline and deterministic (`ScriptedFakeLlm`, no
network, no API keys); the other four are documented design placeholders whose
modules exist in `honba.ai` as 0-byte stubs waiting for the P1/P2 roadmap phases.

## At a glance

| #  | File                              | Strategy / focus | What you learn / what it would do                                                                  |
|----|-----------------------------------|------------------|----------------------------------------------------------------------------------------------------|
| 01 | `01_llm_research_analyst.py`      | —                | **Runnable.** NL question → validated screener filter via `ScriptedFakeLlm` + `KnowledgePack`      |
| 02 | `02_autoresearch_loop.py`         | —                | **Design only.** `ResearchLoop` → generate hypotheses → backtest → journal (Faiss + Parquet)       |
| 03 | `03_nl_screener.py`               | —                | **Runnable.** NL query → screener filter → historical backtest                                     |
| 04 | `04_nl_to_strategy.py`            | —                | **Design only.** NL description → strategy manifest → `verify_manifest` (parser/AST sandbox)       |
| 05 | `05_rl_training.py`               | —                | **Design only.** Gym environment + RL policy on synthetic prices (`torch` + `gymnasium` extras)    |
| 06 | `06_mcp_integration.py`           | —                | **Runnable.** Enumerate the static MCP tools (11 today) from `honba/schema/mcp/mcp_tools.json`             |
| 07 | `07_natural_language_critique.py` | —                | **Design only.** `BacktestResult` → LLM critique → structured `Critique` report                    |
| 09 | `09_typed_llm_decision.py`        | —                | **Runnable.** Typed `LlmDecision` (veto / size-down) applied to an order intent; size increases are rejected; Journal v1 audit records |

Slot 08 is intentionally unused (reserved; numbers are not reused or shifted).

## Pitfalls checklist

Every numbered script in this directory has a `Pitfalls::` section in its module docstring
(enforced by `tests/unit/test_pitfalls_sections.py`). For the four design-only scripts it lists the
traps the future code must defend against, not behaviour you can run. The recurring AI-research
traps: an LLM may already know how a historic date turned out (training-data look-ahead); output
is non-deterministic, so store the prompt, response and seed; any text pasted into a prompt or
returned by a tool is untrusted; model output is parsed by a grammar and never executed; a loop of
generated strategies is a multiple-testing problem (count every trial, keep a holdout); RL needs a
time split, costs in the reward and many seeds; and the fake backends here prove plumbing, not a
model. For the general treatment see
[Avoiding backtest biases](https://honba-labs.github.io/honba/book/backtesting/avoiding_backtest_biases.html).

## What actually exists in `honba.ai` today (offline)

| Module                                      | Symbols                                          | Runs offline? |
|---------------------------------------------|--------------------------------------------------|---------------|
| `honba.ai.llm.provider`                     | `LlmPort`, `ScriptedFakeLlm`                     | ✅             |
| `honba.ai.knowledge`                        | `KnowledgePack`, `build_knowledge_pack`          | ✅             |
| `honba.ai.ask`                              | `translate_query`, `AskResult`, `build_system_prompt` | ✅             |
| `honba.ai.mcp` (schema only)                | `honba/schema/mcp/mcp_tools.json` (11 read-only tools) | ✅             |
| `honba.ai.autoresearch.*`                   | **0-byte stubs** (loop.py, hypothesis.py, evaluator.py, journal.py) | ❌ |
| `honba.ai.verification.*`                   | **0-byte stubs** (critic.py, nl_to_strategy.py, regime_report.py) | ❌ |
| `honba.ai.rl.*`                             | **0-byte stubs** (environment.py, agent.py, policies.py, trainer.py) | ❌ |

The CLI's `honba screener ask` already defaults to `ScriptedFakeLlm` — swap a real
provider (`LlmPort` impl) only when you have `openai`/`anthropic`/`litellm`
installed and an API key set.

## Running them

```bash
python ai_research/01_llm_research_analyst.py --query "rsi below 30" --fake-response "rsi < 30"
python ai_research/06_mcp_integration.py
pytest tests/unit -q -k "llm_research or mcp_integration"
```

## Roadmap pointers (not runnable code)

- `honba-docs/docs/tutorials/autoresearch-loop.md` — full loop design (Faiss/Parquet journal, cost guards, P2).
- `honba-docs/book/src/nl-verification.md` — NL→strategy pipeline design (parser sandbox, tiered validation).
- `honba/crates/honba-cli/Design.md` §13 — MCP gateway, tools, resources, LLM-as-translator trust rules.
- `honba/docs/adr/0009-agentic-mcp-and-local-llm-integration.md` — "Treat LLM output as untrusted input and verify it."
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
| 03 | `03_nl_to_strategy.py`            | —                | **Design only.** NL description → strategy manifest → `verify_manifest` (parser/AST sandbox)       |
| 04 | `04_rl_training.py`               | —                | **Design only.** Gym environment + RL policy on synthetic prices (`torch` + `gymnasium` extras)    |
| 05 | `05_mcp_integration.py`           | —                | **Runnable.** Enumerate the 6 static MCP tools from `honba/schema/mcp/mcp_tools.json`             |
| 06 | `06_natural_language_critique.py` | —                | **Design only.** `BacktestResult` → LLM critique → structured `Critique` report                    |

## What actually exists in `honba.ai` today (offline)

| Module                                      | Symbols                                          | Runs offline? |
|---------------------------------------------|--------------------------------------------------|---------------|
| `honba.ai.llm.provider`                     | `LlmPort`, `ScriptedFakeLlm`                     | ✅             |
| `honba.ai.knowledge`                        | `KnowledgePack`, `build_knowledge_pack`          | ✅             |
| `honba.ai.ask`                              | `translate_query`, `AskResult`, `build_system_prompt` | ✅             |
| `honba.ai.mcp` (schema only)                | `honba/schema/mcp/mcp_tools.json` (6 read-only tools) | ✅             |
| `honba.ai.autoresearch.*`                   | **0-byte stubs** (loop.py, hypothesis.py, evaluator.py, journal.py) | ❌ |
| `honba.ai.verification.*`                   | **0-byte stubs** (critic.py, nl_to_strategy.py, regime_report.py) | ❌ |
| `honba.ai.rl.*`                             | **0-byte stubs** (environment.py, agent.py, policies.py, trainer.py) | ❌ |

The CLI's `honba screener ask` already defaults to `ScriptedFakeLlm` — swap a real
provider (`LlmPort` impl) only when you have `openai`/`anthropic`/`litellm`
installed and an API key set.

## Running them

```bash
python ai_research/01_llm_research_analyst.py --query "mid cap with rsi below 30"
python ai_research/05_mcp_integration.py
pytest tests/unit -q -k "llm_research or mcp_integration"
```

## Roadmap pointers (not runnable code)

- `honba-docs/docs/tutorials/autoresearch-loop.md` — full loop design (Faiss/Parquet journal, cost guards, P2).
- `honba-docs/book/src/nl-verification.md` — NL→strategy pipeline design (parser sandbox, tiered validation).
- `honba/crates/honba-cli/Design.md` §13 — MCP gateway, tools, resources, LLM-as-translator trust rules.
- `honba/docs/adr/0009-ai-trust-envelope.md` — "Treat LLM output as untrusted input and verify it."
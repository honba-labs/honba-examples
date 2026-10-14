"""ai_research/07_natural_language_critique: design placeholder for an LLM backtest critique.

See README for details. ``honba.ai.verification.critic`` is a 0-byte stub today, so this file runs
nothing. The Pitfalls below are the traps a critique step must defend against, not behaviour you
can run.

Pitfalls::

    - A critique is not verification: a model can miss real bugs and flag non-issues. Every number
      it quotes must match a field of the structured BacktestResult, so require it to cite fields
      and check them in code.
    - Hindsight narrative: told the strategy and date range, a model can recall what the market
      did and "explain" the returns afterwards. Such explanations cannot be falsified, so use them
      to suggest tests to run, not as evidence.
    - Untrusted text in the prompt: strategy source, comments, symbol names and rationale strings
      are pasted into the critique prompt, so a comment saying "rate this excellent" is data, not an
      instruction. Validate the output against a structured Critique schema and never act on it
      automatically.
    - Critique-driven tweaking is a search: each edit made to satisfy the critic is another trial
      on the same data. Give the critic the out-of-sample and walk-forward results too, and keep a
      holdout it never sees.
    - Non-determinism: two runs give different critiques, and re-running until it approves is
      selection. Store the prompt, the response and a hash of the result it saw.
"""

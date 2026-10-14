"""ai_research/04_nl_to_strategy: design placeholder for natural language to strategy.

See README for details. ``honba.ai.verification.nl_to_strategy`` is a 0-byte stub today, so this
file runs nothing (the static, sandbox and lookahead verification modules next to it do exist).
The Pitfalls below are the traps the pipeline must defend against, not behaviour you can run.

Pitfalls::

    - Generated code is untrusted: run it only after the parser and AST checks and inside the
      sandbox in honba.ai.verification, never with exec in the host process, and allow no
      imports, file or network access.
    - Hallucinated names: a model invents indicators, fields and tickers that do not exist.
      Resolve every identifier against the catalog and the instrument list and fail on a miss
      rather than substituting a default.
    - Vague language becomes parameters: "strong momentum" turns into a lookback the model picked.
      Regenerating until the backtest passes is a parameter search, so count each generation as a
      trial and test the final one on data it never saw.
    - Look-ahead written by the model: same-bar close fills, shifted indicators and hard-coded
      winners. The model may know which stocks did well in the stated window, so flag literal
      symbol lists and check the lookahead report, not the author's intent.
    - Non-determinism: the same description yields different code each time. Hash the generated
      source and store the prompt so a result can be tied to the exact code that ran.
"""

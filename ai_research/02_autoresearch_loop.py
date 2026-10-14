"""ai_research/02_autoresearch_loop: design placeholder for the autonomous research loop.

See README for details. ``honba.ai.autoresearch.*`` are 0-byte stubs today, so this file runs
nothing. The Pitfalls below are the traps the loop must be built to defend against (design in
honba-docs/docs/tutorials/autoresearch-loop.md), not behaviour you can run.

Pitfalls::

    - Multiple testing: every generated hypothesis is a trial, and the best of 100 looks good by
      chance. The design tracks num_trials_budget and a deflated Sharpe threshold; count every
      attempt, including ones the model or you discard, and report the trial count with the winner.
    - Data snooping through the journal: feeding earlier results back into the prompt (retrieved
      similar hypotheses, failures) lets the loop learn the test period. Keep a final holdout that
      never enters a prompt or the journal and touch it once.
    - LLM look-ahead: a model trained on data covering the backtest window can "propose" ideas it
      already knows worked. Prefer theory-first hypotheses, test windows after the model's
      training cutoff, and record the cutoff.
    - Non-determinism and cost: model output varies run to run. Store the prompt, the raw
      response and a seed so a result can be replayed, and cap iterations and spend.
    - Untrusted text: hypothesis text, journal entries and fetched documents are pasted into later
      prompts, so any of them can carry instructions. Generated strategy code must go through the
      verification sandbox, never be executed directly.
"""

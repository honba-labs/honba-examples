"""ai_research/05_rl_training: design placeholder for reinforcement-learning training.

See README for details. ``honba.ai.rl.*`` are 0-byte stubs and torch and gymnasium are optional
extras, so this file runs nothing. The Pitfalls below are the traps an RL trainer must defend
against, not behaviour you can run.

Pitfalls::

    - Train/test leakage: split by time, never shuffle episodes across the split, fit feature
      scaling on the training period only, and score on a later period that was never used to pick
      hyperparameters or a checkpoint.
    - Reward hacking: an agent maximises the reward as the simulator computes it. With same-bar
      fills, no impact or free leverage it will learn those artifacts. Put costs and risk
      in the reward and inspect the learned actions, not just the final equity.
    - Overfitting one path: a policy trained on a single price path, synthetic or real, can
      memorise it. Train on many paths, and report the spread across several seeds and regimes,
      not the best run.
    - Synthetic data says nothing about markets: a policy that beats a random-walk environment has
      learned the generator, and one that wins on it proves no edge.
    - Non-stationarity: a policy fit to one regime or to today's survivors can fail when the
      universe or volatility changes. Walk forward and re-test on a point-in-time universe.
"""

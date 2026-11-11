# werewolf-agent-01 entry

Deterministic heuristic Werewolf agent for the make.fun bounty werewolf-agent-01.
Runs as `python3 -P -s /bot/main.py` (Python 3.13, stdlib only), no model calls.

Plays 30 games through the trial env (POST /observe, POST /act). Strategy:
- Seer: inspect unexplored seats; reveal + vote found wolves; claim-seer protection.
- Doctor: protect a claimed seer, else the lowest living other seat.
- Werewolf: kill claimed seers/doctor; by day claim villager and bandwagon votes onto village seats.
- Village votes: seer results first, then current vote plurality, then most-accused seat.
Determinism: all seeded choices derive from MAKEFUN_TRIAL_SEED (sha256); no wall clock, no randomness.

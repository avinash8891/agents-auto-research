"""Research-doctrine constants surfaced via conductor tools.

Teaching content the agent can fetch on demand. Lives in code rather than the
system prompt so it doesn't bloat every API call. Recovers legacy §11
(MECHANISM RESEARCH DIMENSIONS) and §12 (PARAMETER TUNING DETECTOR).
"""

from __future__ import annotations

DIMENSION_EXAMPLES = """\
MECHANISM RESEARCH DIMENSIONS — examples and questions to ask.

These are common STRUCTURAL DIMENSIONS of trading-strategy research. They are
shared vocabulary for comparing prior work, not a closed list of allowed ideas.

1. ENTRY TIMING — When does the market microstructure create the opportunity?
   Questions: What time window has the strongest edge? Why does it exist?
   Example: "opening auction imbalance dissipates within 15 min"

2. EXIT MECHANISM — How does the trade capture the move it entered for?
   Questions: Fixed target vs trailing? Time-based vs price-structure-based?
   Example: "winners trend for 20+ minutes, trailing captures 2x more"

3. SIGNAL QUALITY — What distinguishes strong signals from noise?
   Questions: What market context predicts which signals work?
   Example: "signals on days with >1.5x relative volume have 2x PF"

4. REGIME CONDITIONING — When does the strategy's edge appear/disappear?
   Questions: VIX level? Trend vs mean-reversion? Day-of-week?
   Example: "edge exists only when VIX is 15-30; disappears in low-vol"

5. PORTFOLIO CONSTRUCTION — How should multiple signals be combined?
   Questions: Signal prioritization? Diversification? Correlation risk?
   Example: "ranking signals by overnight gap size improves PF 40%"

6. RISK STRUCTURE — Is the stop/target framework matched to the move?
   Questions: Favorable excursion distribution? Risk:reward vs win rate?
   Example: "optimal stop is 1.5 ATR not fixed %, reduces noise exits"

7. MARKET MICROSTRUCTURE — What creates the edge at the execution level?
   Questions: Order flow? Bid-ask spread? Adverse selection?
   Example: "entries coincide with market-maker inventory rebalancing"

8. EMERGENT — A new reusable mechanism family that doesn't fit the seven
   core dimensions. Use this when forcing the idea into a core dimension
   would hide the real causal mechanism. You MUST define:
     - new_dimension_name
     - why_existing_dimensions_do_not_fit
     - mechanism_family_definition
     - expected_reuse_across_future_theses
   If a prior emergent dimension already exists, reuse that exact name.
"""


PARAMETER_TUNING_EXAMPLES = """\
PARAMETER TUNING DETECTOR — what is rejected vs accepted.

REJECTED — same mechanism, different number:
  ✗ Previous: gap_exclude_pct from 0.005 to 0.01.
    Proposed: gap_exclude_pct from 0.01 to 0.002.
    SAME MECHANISM (gap filtering). Rejected by neighboring-threshold or
    config-key-overlap rule.

  ✗ Previous: max_trades_per_day from 3 to 5.
    Proposed: max_trades_per_day from 5 to 7.
    SAME MECHANISM (position sizing). Rejected.

ACCEPTED — different mechanism dimension:
  ✓ Previous tested entry_timing.
    Proposed tests exit_mechanism (trailing vs fixed target).
    DIFFERENT DIMENSION.

  ✓ Previous used a generic opening cutoff.
    Proposed tests "avoid the first three minutes because auction/spread noise
    creates adverse selection" with diagnostics proving that exact boundary.
    STRUCTURAL THRESHOLD — boundary is causally justified, not nudged.

A threshold or parameter is acceptable only when it represents a claimed
market-structure / risk / liquidity / execution boundary that diagnostics
can falsify. It is NOT acceptable when the only rationale is "a different
number may improve PF".

Examples of nudges that will be rejected:
  - shifting an EMA cutoff from 09:45 to 09:43
  - nudging a stop-distance threshold from 0.58% to 0.62%
  - changing a gap filter by a few basis points without a new diagnostic boundary
"""

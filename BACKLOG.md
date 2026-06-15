# EarlyBird - backlog

Ideas captured for later. Nothing here is committed work or scheduled. Each item notes the
intent and the open questions, so it can be picked up cold.

---

## Buy-the-dip alert on high-conviction holdings

**Intent:** when a name we already hold with high conviction pulls back, surface it as a
"consider adding" moment. The engine is good at finding *new* ideas; it does not yet help us
*add to winners we already believe in* when the price dips. COHR (Coherent) is a likely
example - a mapped chokepoint supplier we rate highly, where a dip is an opportunity rather
than a worry.

**Open questions (to settle before building):**
- *What counts as "high conviction"?* Candidate signals already in the system: held on the
  watchlist with high priority, a chokepoint in the supply map, strong pool fit, cross-fund
  convergence. Probably a small explicit set rather than an inferred score - the user decides
  what earns "high conviction" (same autonomy guardrail as theme promotion).
- *What counts as a "dip"?* % off the recent peak is the reference (we store wk52_high /
  range_pct; running peak once snapshot history exists). Loose by design - we will never call
  the bottom, so the rule is: if we still rate it AND it is down z% off its peak, that is good
  enough to flag. No need to perfectly separate healthy pullback from broken thesis; the
  "still rate it" check carries that.

  **Grounded in COHR's actual 2025-26 pattern** (1y history, 251 days): a strong uptrend that
  dips **10-15% routinely** (12 distinct dips, clustered -11% to -14%) and 20-27% in the hard
  wobbles, recovering each time. Day-count by depth: >=10% off peak on 29% of days, >=12% on
  21%, >=15% on 16%, >=20% on only 6%. 8% (35% of days) is too noisy to use.
  - **z = 10% -> "on a dip, worth a look"** (catches routine pullbacks)
  - **z = 15%+ -> "meaningful dip"** (better add zones, ~16% of days)
  - Caveat: these fit a high-beta runner like COHR. A steadier holding may want tighter z
    (~7/10%). Build choice: one global pair, or scale z per-name to its own typical dip depth
    (the COHR measurement above is exactly that per-name calc).

  **Counter-example - OUST (Ouster, lidar), proves per-name z is needed.** Far wilder than
  COHR: worst drawdown -55%, with dips of 22/28/44/47/51/53/55% through the year. It was
  >=10% below peak on **79%** of days, >=25% on **51%**, >=30% on **42%**. For OUST a 10-15%
  dip is noise (it's there almost always); a real "worth a look" is ~25%, "meaningful" ~35-40%.
  A single global z (10/15%) would fire on OUST constantly and be useless. **Conclusion: scale
  z to each name's own dip distribution** (e.g. trigger at the name's ~70th-percentile drawdown
  for "look" and ~85th for "meaningful"), not one global pair. Same calc, run per holding.
- *Delivery:* same-day buying-moment alert (like watch_alert.py) vs a line in the weekly
  brief. A dip is time-sensitive, so an alert is the likely fit.
- *Guardrail:* this is a prompt to look, never an instruction to buy. The user makes the call.

**Touches:** watchlist (conviction flag), fundamentals (range_pct / wk52_high), possibly the
snapshot store, and a new alert script or a brief section. Pairs with the existing chokepoint
signal - a hot chokepoint we hold, on a dip, is the sharpest version of this.

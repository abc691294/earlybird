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
- *What counts as a "dip"?* Needs a reference. Options: % off recent high (we store wk52_high
  / range_pct), or off a short snapshot moving average once enough snapshot history exists.
  Must distinguish a healthy pullback from a broken thesis - a dip on bad news is not a buy.
- *Delivery:* same-day buying-moment alert (like watch_alert.py) vs a line in the weekly
  brief. A dip is time-sensitive, so an alert is the likely fit.
- *Guardrail:* this is a prompt to look, never an instruction to buy. The user makes the call.

**Touches:** watchlist (conviction flag), fundamentals (range_pct / wk52_high), possibly the
snapshot store, and a new alert script or a brief section. Pairs with the existing chokepoint
signal - a hot chokepoint we hold, on a dip, is the sharpest version of this.

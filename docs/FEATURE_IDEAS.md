# WashWise — parked feature ideas

Notes from the 10-agent feature review (2026-10-26). Shortlist items being built
are tracked in CHANGELOG `[Unreleased]`; this file holds ideas deliberately
deferred for more design.

## Deferred: "best day / best window" (review ranks #2 + #6)

**Why parked:** the naive form ("name the single best day", argmax over
`day_score` in `forecast_summary`) is low-signal and dies on single-day
presets:

- `windows_house` → `days: 1`, `garden_irrigation` → `days: 1`,
  `solar_panels` → `days: 0`. With a 1-day horizon argmax is trivial — the
  sensor always says "today" or nothing. No value.
- Even multi-day, a bare label ("Thursday") ignores *how much* better it is
  and whether today is already fine.

`#2` (backend sensor) and `#6` (card forecast-strip highlight) are twins — the
card reads the same `day_score` data the sensor would expose, so they should
be designed as one feature, not two.

**Candidate framings (undecided):**

1. **Ranked strip, not one pick.** Tint/sort the card forecast strip by
   per-day `day_score` (today 40, Wed 90, Thu 85…) so the user sees the
   gradient. Makes the separate sensor nearly redundant; single-day presets
   degrade gracefully (strip just shows what it has). Card-first.
2. **Wash window = contiguous good run.** Find the next stretch of N
   consecutive non-blocked days ("Clear Wed–Fri" not "best: Thu"). Tells you
   when *and* how long. Best for boat/rv (5-day). More logic — M effort.
3. **Worth-waiting delta.** "Wash now (42) or wait to Thu (90), +48." Caption
   tells you if waiting pays off. Reuses the same `day_score` data.

**Current lean:** merge #2+#6 into one card-first feature — strip tinted +
sorted by `day_score`, best day ringed, caption with an optional
worth-waiting delta. Kills the thin standalone sensor and the single-day dead
case. Decide framing before building.

# Scope-fix 2×2 (2026-11-15)
- Run with `uv run python -m kg.scope_eval`; re-score with `--score-only`.
- Gold labels: `data/answer_key/scope_gold.yaml`, 20 prose cause-share sentences.
- Per-item detail: `item_scores.csv`.
- Model `deepseek-flash`, effort high, temperature 0. Pass 1 was reused from run 1, so only pass 2 was re-run
  (12 chunks × 4 cells).

**I (Claude) wrote the gold labels, not the user.** I wrote them before seeing any results. The user then had the
grading principle changed, see "Gold revision" below. The labels are still unchecked by the user.

**Cells**
- **A, scope card:** A0 = document card only. A1 = document card plus section cards, one per PDF-outline section.
- **B, local context:** B0 = off. B1 = each number sentence shown with the sentence before it (taken from the whole
  document, so it crosses chunk boundaries), plus the rule that a nearby date overrides the default window.
- **Date rule:** a code backstop. If the claim's sentence names exactly one February date and the window is a default
  or incomplete, the window becomes that date. Every cell is scored with it on and off.

## Result: number of the 20 sentences with each field right (revised gold)
| Cell | Metric | Denominator | Region | Window | **All four** | First gold version |
|---|---|---|---|---|---|---|
| Run 1 (v0 prompt) | 17 | 19 | 18 | 16 | **12** | 9 |
| Run 1 + date rule | 17 | 19 | 18 | 18 | **13** | 10 |
| A0B0 (v0.2 prompt, no fixes) | 18 | 19 | 18 | 17 | **12** | 11 |
| A1B0 section cards | 18 | 20 | 18 | 20 | **16** | 15 |
| A0B1 local context (**now the default**) | 19 | 20 | 17 | 20 | **16** | 15 |
| A1B1 both | 20 | 20 | 18 | 20 | **18** | 15 |

The date rule changes nothing in any v0.2 cell, because the model already fills the window. It only helps run 1's output.

Cost: 69 calls (including a smoke test and the halves of 2 truncated chunks). That's 1.0M output and 0.32M input
tokens, about **$0.60** at run 1's rate. This is an estimate, not a billed figure. Re-scoring is free.

## Gold revision (2026-11-15, user's request)
The first version required Texas/Oklahoma/Louisiana for all of FERC's gas cause shares. That came from FERC
Appendix I (the producer sample), which the model never sees, so it tested knowledge of the appendix rather than
extraction. Each gas item is now graded against what its own input supports:
- **c025** (88.4%, 18.1% on Feb 14): the chunk only says "Texas and South Central U.S." (its section heading).
  Unchanged.
- **c040** (58%; 18.1% vs 21.6%): the chunk says "total U.S. Lower 48 natural gas production fell…" and "largest…
  impacts… in Texas, Oklahoma, and Louisiana". Both are now accepted. The electric footprint is still wrong, because
  the chunk scopes production otherwise.
- **c041** (21% gas day, 61% processing): the chunk names no region for production or processing, so the document
  default (ERCOT+SPP+MISO) is accepted too. These two now test only metric, denominator and window.

## What it shows
1. **Both fixes together: 18/20. Either alone: 16. Neither: 12.**
   - Each fix alone gains the same 4 sentences: "13 percent other fuel issues" (denominator from the previous
     sentence), "52 to 60 percent … during that period" (window), and FERC 88.4% and 18.1% (region from the section
     heading).
   - Context alone also fixed the heading-region items, by reading the heading in the chunk. Section cards alone also
     fixed the previous-sentence denominator.
   - Both together add the metric on processing and gas-day production (`share_of_volume`, not events) and the region
     on 58%.
   - The gap between "both" and "either" is 2 sentences, which is within noise for n = 20 with one run.
2. **The fixes cause both remaining misses (g13, g14: 18.1% on Feb 14 and 21.6% on Feb 17 in c040).**
   - Without fixes, the model wrote "U.S. Lower 48", which is now right.
   - With section cards it copies IV.C's card, "ERCOT, MISO South and all of SPP". The outline is too coarse there:
     IV.C opens with generator fuel issues, and its gas subsections ("Weather/Freeze-Related Effects on Production.",
     "Effects on Natural Gas Processing Facilities.") are inline bold headings, not bookmarks.
   - With context only, it falls back to the document default.
   - **Fix: build sections from inline headings.** That should fix both.
3. **Window: every fixed cell gets 20/20**, including the multi-date sentence that caused run 1's one conflict.
4. **Revising the gold mostly raised the cells without fixes** (run 1 9 → 12). Run 1 had said "U.S. Lower 48", which
   the chunk supports.

## Caveats
- n = 20, one sample per cell. Run 1 and A0B0 already differ on 2 items, so treat ±2 as noise.
- The gold labels are mine and not checked by the user.
- c041's two items can't test region under the revised principle.
- This set is prose only. The 20% on test 3 in run 1 was a random sample that included table fragments, so the two
  numbers can't be compared.

## Recommendation
- **Keep local context as the default** (done 2026-11-15). It costs input tokens only.
- **Section cards now score better together with context (18 vs 16), but the gap is within noise,** and the IV.C card is
  wrong. Build cards from inline headings, then re-test before making them the default.
- **Keep the date rule** as a cheap backstop for output from older prompts.

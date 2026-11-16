# Step 0: quote and value check, label normalization (2026-11-15)
Post-processing of run `01_extraction` with v0.2 code, written to `post_graph.json` here
(`uv run python -m kg.post 01_extraction --out results/03_graph`). Run 1's own `graph.json` is unchanged.

| | v0.2 before step 0 | after step 0 |
|---|---|---|
| Quote failures (claims / events / entities / relations) | 49 / 20 / 9 / 5 | 49 / 20 / 9 / 5 (no change) |
| Claims dropped by the value check (X13) | — | **22** of 1,990 |
| Generic roles dropped | 22 | 22 |
| Links `different_scope` / `comparable` / `conflict` | 695 / 5 / 1 | **664 / 5 / 1** |
| Cause-label merges | — | 15 (no link changed) |

The 22 dropped claims are all values the quote doesn't write: durations ("4m 23s" → 4.383 minutes,
"7 hours, 24 minutes" → 7.4), fractions ("a third", "half", "a tenth", "three-quarters"), "twice", "a week", "has no wind" → 0.
None is a misread digit such as "31.4" for "314". The full list is in `post_graph.json` → `stats.value_fail_examples`.

The label merges are case variants ("fuel issues" / "Fuel issues"), "Icing on blades" / "blade icing" and "Freezing" /
"freezing-related". None of them changed a link. The case variants were already equal under the old rule, and the
other pairs differ on window or region anyway. The one `conflict` is still FERC 21.5% (whole event) vs 18.1% (Feb 14),
a scope failure noted in README.

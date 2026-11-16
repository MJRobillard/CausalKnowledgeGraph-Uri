# Feasibility scorecard: text side

Slice: one time window, about 21:00 Feb 14 to 12:00 Feb 16 (Central).
Inputs: ERCOT board review and 4/27 causes report (Tier 1), FERC/NERC final report sections
on that window (Tier 2), Enverus (Tier 2, funded), ~5 news articles (Tier 3).
Held out: the UT Austin timeline (answer key).

| # | Test | How to measure | Pass | If it fails |
|---|------|----------------|------|-------------|
| 1 | Ontology coverage | `len(other) / (events + relations + quant_claims + other)`; cluster the `other` items by `suggested_type` | < 15% | Add the types the clusters reveal. That's v0.2, and your answer to "how does the ontology grow?" |
| 2 | Timeline recall | For each of the ~21 timed events in the UT timeline, is there an extracted event of the same type within ±1 hour? | > 60% | Look at misses. Usually relative times ("Monday night"): add a time-resolution step using the publication date |
| 3 | Scope completeness | Of quant_claims that are shares, the % with denominator, region and window filled and correct (check 20 by hand). Also report how many came from `document_default` | > 70% correct | Strengthen pass 2, or add a third pass that only fills scope |
| 4 | The 12% vs 31.4% test | Find the ERCOT fuel share and the FERC/NERC fuel share. Does the comparability rule label them `different_scope` (not `conflict`)? Check which fields differ | Both extracted; rule says `different_scope` because metric, region and window differ | This is the core of the tier system. If extraction can't capture scope here, fix it before building anything downstream |
| + | Precision | Hand-check 40 random items: is it stated in the quote, is the type right, are the fields right? | Record the number | This is the figure to quote in the interview |
| + | Quote check | Share of items whose quote isn't found in the chunk | < 2% | Lower temperature; shorten chunks |

Record results in a small table in the README, with the date and model used.
A failing number with a clear fix is a better interview story than an untested pipeline.

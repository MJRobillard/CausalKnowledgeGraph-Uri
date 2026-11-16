# Findings: extraction experiment v0 (Winter Storm Uri)

Date: 2026-11-14 · Run: `results/01_extraction/` · Code: `kg/` · Spec: `config/`

## 1. What we were testing

**The core idea:** a language model extracts facts from documents about the Feb 2021 Texas blackout, and
deterministic code decides how much to trust each fact and whether two facts can be compared. The model never sees
trust information (tier, flags). Trust comes only from a hand-written document registry.

The idea succeeds or fails on one case. ERCOT says fuel problems caused **12%** of outages; FERC/NERC says
**31.4%**. A naive system calls that a contradiction. The idea is that once each number keeps its *scope*
(what it's a share of, which region, which dates), code can show they measure different things and are not in conflict.

For that to work, three parts have to hold:
1. **Trust is assigned in code**, correctly and without leaking into extraction.
2. **The comparability rule** labels disagreements correctly (`different_scope` vs `conflict`) when given the scope.
3. **Extraction captures the scope** (metric, denominator, region, window) accurately enough for the rule to use.

## 2. What we ran

| Item | Setting |
|---|---|
| Time slice | Feb 14 21:00 – Feb 16 12:00 (Central), the start of load shedding to the peak of generation outages |
| Inputs | ERCOT board review 2/24 and ERCOT 4/27 cause report (Tier 1); FERC/NERC final report, the 47 of 82 chunks covering Feb 14–16 or the key cause shares (Tier 2); Enverus gas analysis, paid for by TXOGA (Tier 2, flagged); 5 news articles (Tier 3) |
| Held out | UT Austin timeline, used only as the answer key for recall |
| Size | 65 chunks, about 72k words |
| Model | DeepSeek `deepseek-flash` (V4.1), effort high, temperature 0, JSON output checked against the schema and retried on failure |
| Method | Two passes per chunk: pass 1 extracts entities, events and relations; pass 2 extracts numbers with their scope. Code then attaches trust, drops items whose quote isn't in the source, merges entities and labels every pair of cause shares |
| Hand checks | 20 scope rows and 40 precision rows, marked by the user; 15 rows marked "check" were settled from the source text (reasons in the CSVs) |
| Cost | About $1.29, including the smoke test |

## 3. What happened

4,199 items extracted: 512 entities, 968 events, 482 relations, 1,990 numbers, 247 items that fit no type.
1 of 70 chunk pieces failed: one quarter of a FERC chunk (`ferc_final_2021_11:c031bb`) kept hitting the output limit.

| # | Test | Result | Bar | Pass? |
|---|---|---|---|---|
| 1 | Ontology coverage: share of items that fit no type | 6.9% | < 15% | ✅ |
| 2 | Timeline recall vs UT Austin, same type within ±1h | 8 of 11 (73%) in the slice | > 60% | ✅ |
| 3 | Scope completeness: shares with correct denominator, region and window | 4 of 20 (20%) hand-checked; fields *filled* for 95% | > 70% correct | ❌ |
| 4 | ERCOT 12% vs FERC 31.4% | Both found; labelled `different_scope` (metric, denominator, region and cause scheme differ) | label = `different_scope` | ✅ |
| + | Precision (40 random items) | Stated in quote 78%, type right 60%, fields right 60%, all three right 48% | record only | — |
| + | Quote check (quote not found in source) | 1.98% | < 2% | ✅ |

### 3.1 How the results read
- **Recall (test 2) measures what's in the input, not extraction quality.** All 3 missed events are absent from the slice text: the
  South Texas Project nuclear unit tripping at 05:27, its site reaching 0 °C, and the "noon Feb 15" outage levels.
  On events the input contains, recall is 8 of 8. The answer key hasn't been hand-checked yet.
- **Scope (test 3) is the real failure, and it has two causes:**

  | Cause | Scope rows | Correct |
  |---|---|---|
  | **Table fragments**: pdftotext flattens FERC's tables into bare numbers that can't be tied to a row or column | 9 of 20 | 0 of 9 |
  | **Prose** | 11 of 20 | 4 of 11 |

  The table problem is a **parsing** problem; Textract should fix it. The prose failures come from the
  **extraction design**, and better parsing won't fix them:
  - One scope card per document is too coarse. FERC's gas chapters cover producing basins and the Lower 48, not ERCOT+SPP+MISO,
    and gas shares are share of volume, not share of events.
  - The denominator is often in the previous sentence ("13 percent other fuel issues" follows "87 percent natural gas").
  - A nearby label gets taken as the window: a "(Winter 2023-2024)" tag, which is the preceding recommendation's
    deadline, became a reserve margin's window.
- **Comparability links:** 695 `different_scope`, 5 `comparable` (true duplicates such as 31.4% stated twice), 1 `conflict`.
  The one conflict is a scope error: FERC's 21.5% (whole event) vs 18.1% (Feb 14 only) both kept the report's default
  window, although the sentence names the day.

### 3.2 Bugs found and fixed during the run (no re-extraction needed)
- A range ("52 to 60 percent") became two numbers that "conflicted" with each other. Now skipped; the schema needs low and high values.
- Fuzzy entity matching merged different things ("oil-fired" into "gas-fired" units; a 411 MW unit with a 511 MW unit). Now uses
  word-order-insensitive matching and never merges names whose numbers differ. All 10 remaining fuzzy merges are correct.
- The answer key's blank `verified` cells weren't counted. Fixed.

## 4. What this says about the core idea

| Part | Verdict | Evidence |
|---|---|---|
| 1. Trust assigned in code | **Works** | Tier and flags right for every document; the model never saw them |
| 2. Comparability rule | **Works, given correct scope** | 12% vs 31.4% labelled `different_scope` for the right reasons; the false conflicts came from bad scope or my bugs, not the rule |
| 3. Extraction captures scope | **Not shown** | 4 of 11 correct on prose; 0 of 9 on tables |

**Bottom line:** the trust and comparability logic does what it's meant to, but the experiment couldn't show that
extraction provides the scope it needs. Testing the full pipeline was premature: parsing problems (tables) and
extraction-design problems (scope cards, sentence context) hid the question that matters, which is
**can scope be extracted reliably from clean text?**

It also turned up some real disagreements between sources, which is the kind of output the idea is meant to produce:
- When firm load shed began: **FERC 1:15 a.m., UT Austin 1:20, Texas Tribune 1:25**.
- FERC gives two different "Event overall" shares of the gas production drop caused by power loss: **23.5% and 21.5%**.

## 5. Gaps in the ontology (from the 247 unmatched items and the hand checks)
- **Types:** Region outside ERCOT (MISO South, SPP); water service disruption; demand (record demand appears in every source); recommendation; mechanism.
- **Relation:** `associated_with` (correlation, e.g. "as temperatures fell, outages increased").
- **Cause category:** `power_supply_loss`, the evidence for the `requires_power_from` edge.
- **Metrics:** share of survey responses, share of energy, wind speed.
- **Schema:** low and high values for ranges, plus an `approximate` flag ("about" and "nearly" are approximate, not hedged).
- **Rule:** skip generic roles ("Balancing Authorities") as entities.

## 6. Next step: test only the open question
Set tables aside (Textract later) and test scope extraction on clean prose:
1. Take 15–20 cause-share sentences from prose: FERC, ERCOT, Enverus, news.
2. Rerun pass 2 with **the previous sentence included** and **a scope card per section**.
3. Hand-check scope and run the comparability rule on the results.

If prose scope reaches 70% or more, the core idea is supported and the pipeline is worth building out. If not, the
scope model itself needs rethinking, for example a pass that only fills scope, or human-set scope cards per section.
Expected cost: under $0.20 and about 30 minutes of hand checking.

## Files
- Scores: `results/01_extraction/scores.json`
- Hand checks: `review_scope.csv`, `review_precision.csv` (user's original marks in `*.answered.csv`)
- Graph (items, links, entity merges): `results/01_extraction/graph.json`
- Answer key: `data/answer_key/ut_timeline_events.csv`

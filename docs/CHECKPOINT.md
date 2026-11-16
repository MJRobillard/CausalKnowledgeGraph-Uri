# Checkpoint: 2026-11-15
Where the Uri causal KG stands: what's done, the state of each dataset, and what changed since the first survey
(2026-11-14). Assumptions are in `ASSUMPTIONS.md`. One commit per step.

## Done
| Area | What | Where |
|---|---|---|
| Survey | 7 datasets and 12 reports assessed for feasibility, problems and role in the graph | `dataset_survey.md` |
| Weather / gas | NOAA hourly by zone (clean, UTC), CDO stations by county, EIA gas production and Henry Hub | `data/external/noaa/`, `data/external/eia_gas/` |
| Analysis | Cold → outage lag (Spearman −0.42 at no lag, −0.76 at 24 h). Load shed → gas outage event study: inconclusive | `dataset_survey.md`, `analysis/` |
| Pipeline v0 | Two-pass extraction (DeepSeek), trust assigned in code, quote check, entity merging, comparability links, scorecard | `kg/`, `config/` |
| Run 1 | `01_extraction`: 65 chunks, 4,199 items, about $1.29. Hand checks done | `results/`, `FINDINGS_extraction_v0.md`, `README.md` |
| TAMU cleaning | 2016 dates, Central time everywhere, 13 county names, percent-out cap, 135 blank rows. 39 cleaned CSVs | `scripts/clean_tamu.py`, `data/external/tamu/`, `docs/tamu_cleaning.md` |
| Ontology v0.2 | Region, Demand, WaterServiceDisruption, `associated_with`, `power_supply_loss`, 3 metrics, ranges, `approximate`, generic roles dropped. 11 tests pass | `config/`, `kg/post.py`, `tests/` |
| Table trial | pdfplumber on FERC: 129 tables found on 108 pages. It rebuilds ruled tables with labels, but can't read charts | trial only, nothing wired in |
| Docs | Assumptions list and this checkpoint | `ASSUMPTIONS.md`, `CHECKPOINT.md` |
| Step 0 fixes | Quote check keeps number punctuation and checks the value is in the quote (22 claims dropped); cause labels normalized within a scheme (no link changed). Links 664 / 5 / 1 | `kg/common.py`, `kg/post.py` |
| Tiered graph | Documents, entities, events, claims, Tier 0 series and observations; trust from the registry; bitemporal; 3/4 → 3/12 unit-list revisions classified | `kg/graph_build.py`, `kg/unit_revisions.py`, `results/03_graph/` |
| Tier 0 grounding | 29 of 2,867 items anchored (24 agree, 5 disagree); load shed began 01:20; source reliability | `kg/anchor.py`, `config/anchor_rules.yaml`, `anchor_report.md` |
| Invariants | Cause before effect, nothing causes weather, Tier 0 never overridden, no cycles, gas–power loop unrolled, grades G0–G3 | `kg/invariants.py`, `invariants_report.md` |
| Cold models | Instantaneous, cumulative and fail-restart models per fuel with placebo; fail-restart wins for gas, coal, wind | `analysis/cold_models.py`, `cold_models.md` |
| Tests | 44 pass (step 0, graph, anchor, invariants, models, hypotheses, design check) | `tests/` |

## Run 1 scorecard (v0 ontology; not re-run on v0.2)
| Test | Result | Pass? |
|---|---|---|
| 1 Ontology coverage (`other` share) | 6.9% | ✅ |
| 2 Timeline recall ±1 h | 8 of 11 = 73% (all 3 misses are absent from the input) | ✅ |
| 3 Scope completeness | 4 of 20 correct (20%). 9 of the 20 were table fragments | ❌ |
| 4 12% vs 31.4% | Both found; labelled `different_scope` | ✅ |
| Precision (40 items) | 48% right on all three checks | recorded |
| Quote check | 1.98% dropped | ✅ |

## Datasets: status and what changed
| Dataset | Status 2026-11-14 | Now | What changed |
|---|---|---|---|
| TAMU 2021TXPowerOutage | Surveyed; problems listed | **Cleaned** → `data/external/tamu/` | All five listed fixes done. New findings: (1) the demand profile's true start is 1/30 23:00 CST, so TAMU's model ran demand 25 h early and wind 6 h late; (2) the "UTC" headers on the PG/PF outputs and the unit-outage matrices were wrong (a placeholder index, and Central time); (3) solar and hydro are likely 2016 weather stand-ins; (4) the EIA API file is local Central, not UTC; (5) 2 of the 13 county codes don't follow the alphabetical index |
| ERCOT high-resolution data | Surveyed; Central | Unchanged; used as ground truth | Used to verify the clocks of EIA, TAMU's load-shed file and poweroutage.us |
| Unit outage list 3/4 and 3/12 | 1,661 real rows + 135 blank | Both are Tier 0b sources with a `revised_by` link | Redone 2026-11-15: 803 rows expansion, 1,658 unchanged, **135 disclosure** of the redacted rows (61 unique, 74 in 32 groups), 3 corrections (wind unit renames), nothing unexplained |
| Event reports (12) | Surveyed | Run 1 used 4 reports + 5 news articles | Tables: pdfplumber works on FERC's ruled tables. Charts: skipped for now |
| NOAA NCEI hourly | Downloaded, clean (UTC) | Extended to 2/26; read in Central by `kg/series.py` | Cleaning rule reconstructed and checked (`scripts/extend_zone_temps.py`). GHCN-Daily TMIN/TMAX registered as 0a for daily extremes (hourly readings miss them) |
| NOAA CDO stations | 95 of 113 counties | Unchanged | Neighbour stations for the 18 gap counties not done |
| EIA natural gas | Monthly + Henry Hub daily | Unchanged | |

## What changed since 2026-11-14
1. **TAMU data cleaned and logged** (`docs/tamu_cleaning.md`). The script leaves the TAMU folder untouched, and every number
   in the log is in `cleaning_stats.json`.
2. **Ontology, schema and prompt moved to v0.2**, from run 1's hand checks. Comparability now uses range overlap.
   Approximate values are **still flagged as conflicts** (the link carries `approximate: true`). Re-running post-processing
   on run 1 drops 22 generic-role entities and 4 relations, and leaves the links unchanged (695 / 5 / 1).
3. **Table extraction:** a pdfplumber trial looks good on ruled FERC tables. Small defects: split digits ("6 5,622"),
   and section headers that need carrying down to their rows. The text-gap strategy is unusable.
4. **Decisions:** charts are skipped for now. Contradictions stay flagged rather than absorbed by tolerance. The core idea
   still comes before parsing.

## Scope fixes (2026-11-15)
Implemented: section cards (`kg/scope.py`, from the PDF outline), the previous sentence as context (crosses chunk
boundaries), and a nearby-date rule (in the prompt, plus a code backstop in `kg.post --date-rule`). Tested in a 2×2 on
20 prose cause-share sentences (`results/02_scope_eval/RESULTS.md`, about $0.60). All four fields right
(gold revised to what the chunk text supports): 12/20 → 16 with either fix → **18 with both**. The 2 remaining misses
are FERC IV.C regions that the section card or document default overrides. The gold labels are mine and not yet
checked by you. **Local context is now the default** in `kg.extract`; section cards stay opt-in.

## Graph and grounding (2026-11-15, `results/03_graph/`)
- **Anchor coverage 1.0%** (29 of 2,867), as expected: most claims are about other regions or quantities. 5 mismatches:
  2 are a board-deck layout error (day headings follow their bullets in pdftotext order), 2 FERC city lows match
  neither DFW nor IAH, 1 is a small real disagreement (Tribune's 30,000 MW). Reliability scores count extraction
  errors against the source (A15).
- **Load-shed start: 01:20**, from ERCOT's minute data. FERC's one 1:15 and the Tribune's 1:25 are each 5 minutes off.
- **Cold models:** fail-restart wins for gas, coal and wind, passing the placebo → `WeatherCondition → GenerationOutage[fuel]`
  graded G3. Solar is best fit by the instantaneous model. The expected "wind and solar fail fastest" did not hold.
- Nothing contradicted an assumption marked Verified. D9 (redacted rows) is now consistent with every row.

## Hypotheses (2026-11-16, `results/04_hypotheses/`)
- Weather-free baselines: cold → gas and wind outages drop from G3 to G1; coal keeps G3. G3 needs independent support (H4).
- 61 hypothesis families (57 documents, 3 both, 1 skeleton). 14 tested, 47 untestable. G3: frequency low → coal
  outages, the same for all fuels, generation outages → load shed (accounting plus timing).
- Pre-registered measurement table (`config/proxies.yaml`, commit `07ab729`); one post-run amendment (`fb89615`).
- Review fixes: Fig. 35 read off the chart. Grouping fixes (PowerLoss, contested, supply chain, warming).
  Timing-only checks capped at G1; accounting status. Now G3 2, G1 50, G0 3, accounting 2 (57 families; one single-relation "warming weather" family merged into the cold-weather family on the final re-run). Lead figure `hypothesis_lead.png`.

## Causal ML (2026-11-15, `results/05_causal_ml/`)
- EIA-860 fleet matched to the 3/12 list; spatial layer with GeoJSONs and per-plant features.
- DML: 0.70 pp per °F (CI just touches zero); only wind is clear by fuel (1.14). Warmer +5 °F: about 2.5 GW on Feb 16.
  Gas supply vs plant cold not identified. Per-plant effects in `cate_plants.geojson`.

## Grid replay (2026-11-15, `results/06_grid/`)
- Python DC OPF on TAMU's synthetic grid reproduces ERCOT's load shed (r = 0.93, 92% of the energy).
- Shedding was capacity-limited, not transmission-limited.
- The 01:55 frequency trips cost about 94 GWh (coal 16, gas 78). The coal hazard test is underpowered (11 events).

## Next (not started)
- **Supply chain on the grid:** map gas processing plants and compressor stations to grid buses and rank shed blocks
  by the gas infrastructure they cut (the power loss → gas supply edge). Simulate pipeline-level curtailments via
  EIA-860's pipeline names.
- **Feed the causal-ML counterfactuals into the replay** (warmer storm, winterization, relocation) to turn plant MW
  into load shed.
- **Spatially robust inference** (cluster by county or weather zone, or spatial HAC) and a sensitivity analysis
  (e.g. omitted-variable bounds) before the effects are quoted.
- **Gas supply at plant level:** EIA-860 lists each gas plant's supplying pipelines; pipeline curtailment notices
  (FERC appendix) could give a plant-specific supply shock.
- **Geographic interpretation figure** from `cate_plants.geojson` and `data/processed/spatial/geojson/`.
- **A valid daily cold → production test** (restart rate from the hourly fits, or training through the first
  recovery days), and a lagged Permian driver for gas supply → gas outages.
- **Re-extract on v0.2** for `associated_with` (correlational families) and `power_supply_loss`.
- **Re-extract with `--context` on ontology v0.2**, then rebuild the graph. Two anchoring problems would shrink:
  dates from layout (board deck) and cause labels without `power_supply_loss`.
- **Board-deck layout:** pass slide order or day headings to the chunker, so bullets get the right day.
- **Anchoring by hand:** you haven't checked the 29 anchored items or my reading of the 5 mismatches.
- **Fail-restart with a temperature-dependent restart rate and a baseline term** (wind's 2/19 recovery is too fast for a constant ρ).
- **Per-unit cold model** on the 3/12 list with county temperatures (the causal forest / EIA-860 step).
- **Re-run extraction on v0.2** (about $1.30, new run folder). Then: does the `other` share fall, do ranges stay as one
  claim, and do generic roles disappear?
- **Decide the table path**, pdfplumber or Textract, and send number-dense chunks there (v0.2 fix 1 in `README.md`).
- ~~Scope fixes for test 3~~ **done 2026-11-15**, see below. Next: section cards from inline headings (FERC IV.C);
  re-run the whole slice (context is now the default).
- **Add the 3/12 unit outage list as a Tier 0b source**, and redo the 3/4 vs 3/12 comparison with the redacted rows in mind.
- **Hand-check the UT Austin answer key.**
- **Later:** charts (Claude, per figure), `LMP.pkl`, CDO neighbour stations, NOAA in Central time.

> Archived README (2026-11-14 to 2026-11-16): the full run-by-run log. The current overview is the top-level [README](../README.md). Paths below are relative to the repo root.
>
> For the public release the run folders were renamed by pipeline stage and moved from `runs/` to `results/`
> (`2026-11-14_deepseek-flash` → `01_extraction`, `2026-11-15_scope2x2` → `02_scope_eval`, `2026-11-15_graph` → `03_graph`,
> `2026-11-16_hypotheses` → `04_hypotheses`, and the causal-ML, grid and maps runs → `05_causal_ml`, `06_grid`, `07_maps`).
> Source documents moved from `uri_reports/` to `data/sources/`, and third-party datasets to `data/external/`. The text
> below uses the new names.

# causal-kg: Winter Storm Uri

Causal knowledge graph of the February 2021 Texas power and gas failures.
This folder holds the source data, the dataset survey, and the **text extraction pipeline v0** with its
feasibility scorecard.

- `dataset_survey.md`: what each dataset and report is, how usable it is, and findings so far
- `config/`: ontology, document registry (trust metadata), extraction schema and prompt, scorecard, aliases
- `kg/`: pipeline code
- `data/sources/`: source PDFs, ERCOT data, `news/` (Tier 3)
- `data/answer_key/ut_timeline_events.csv`: timed events from the UT Austin report (held out; the extractor never sees it)
- `results/<run>/`: one folder per run (cache, extractions, graph, scores, review sheets)
- `results/03_graph/`: the tiered graph, Tier 0 anchoring, invariants and cold models (no new extraction)
- `results/04_hypotheses/`: hypothesis families from the documents, tested against Tier 0
- `results/05_causal_ml/`: causal ML on the plant × fuel × day panel (DML, causal forest, counterfactuals)
- `results/06_grid/`: grid replay, bottlenecks, the coal contradiction and the frequency-trip counterfactual
- `data/processed/fleet/`, `data/processed/spatial/`: EIA-860 fleet matched to ERCOT outages; GeoJSONs and per-plant spatial features
- `analysis/`: analyses on measured data (`cold_models.py`, `cold_baselines.py`, `hypothesis_tests.py`, the earlier `event_study.py`)

## Pipeline
The LLM extracts; code assigns trust. Tier, flags and claim status come from `config/documents.yaml`
and `config/ontology.yaml`, and the model never sees them.

```bash
uv sync
uv run pytest -q                                   # unit checks, no API calls
uv run python -m kg.extract --run <run>            # scorecard slice; --full for whole docs
uv run python -m kg.post <run>                     # trust, quote check, ids, entity merge, comparability
uv run python -m kg.score <run>                    # writes scores.json and the two review CSVs
# fill in results/<run>/review_scope.csv and review_precision.csv (y/n columns), then:
uv run python -m kg.score <run> --reviewed

# graph, grounding, invariants, causal check (writes results/03_graph/)
uv run python -m kg.post 01_extraction --out results/03_graph   # post_graph.json
uv run python -m kg.graph_build      # nodes/edges.parquet, graph.json, unit_revisions.parquet
uv run python -m kg.anchor           # anchors.parquet, anchor_report.md, reliability.csv
uv run python -m kg.invariants       # invariants_report.md, loop_entity.json, loop_hourly.json
uv run python -m analysis.cold_models   # cold_models.md, cold_<fuel>.png; re-grades via kg.invariants

# hypotheses (writes results/04_hypotheses/; copy nodes/edges.parquet from 03_graph first)
uv run python -m analysis.cold_baselines     # weather-free baselines, re-grades the cold edges
uv run python -m kg.hypotheses               # families.json
uv run python -m analysis.hypothesis_tests   # hypothesis_results.json, test charts (rules: config/proxies.yaml)
uv run python -m analysis.hypothesis_outputs # hypotheses.json, hypothesis_graph.json, overview PNG, report

# fleet, spatial layer, causal ML (downloads public data into data/raw/, gitignored)
uv run python scripts/fetch_spatial.py          # EIA-860, Census counties, GHCN-Daily, ArcGIS layers
uv run python -m kg.fleet                    # EIA-860 ERCOT fleet matched to the 3/12 outage list
uv run python -m kg.spatial                  # GeoJSONs, per-plant distances/bearings, daily cold at plants
uv run python -m analysis.causal_ml          # DML, causal forest, placebo, counterfactuals (~3 min)

# grid replay on TAMU's synthetic Texas grid (Python DC OPF; no MATLAB or Gurobi)
uv run python -m analysis.grid_replay        # 120 hourly OPFs, validation against ERCOT load shed (~2 min)
uv run python -m analysis.coal_test          # coal hazard test, frequency-trip counterfactual (~6 min)
uv run python -m analysis.grid_report        # report, grid_lines.geojson, grid_buses.geojson
```

Modules added 2026-11-15: `kg/series.py` (Tier 0 loaders, all Central time), `kg/unit_revisions.py`
(3/4 vs 3/12 unit list), `kg/graph_build.py`, `kg/anchor.py` with `config/anchor_rules.yaml`, `kg/invariants.py`.
Tier 0 datasets are registered in `config/documents.yaml` (`doc_type: dataset, measured: true`; weather is 0a).

Extractor: DeepSeek API (`deepseek-flash` by default). The key is read from `DEEPSEEK_API_KEY` in the environment or
`.env` (see `.env.example`). Responses are cached per run, so reruns are free.

Changes from the spec:
- Entity resolution uses fuzzy string matching, not embeddings, because DeepSeek has no embeddings API.
- The comparability rule has two additions. Within one cause scheme, a different `cause_label_raw` is a
  different (sub)category, so FERC's 31.4% "fuel issues" vs 27.3% "gas fuel supply" is `different_scope`.
  Prelim vs final versions get `superseded`, not `conflict`.

## Results

### Run `01_extraction` (2026-11-14, `deepseek-flash` = DeepSeek V4.1 Flash, effort high, temperature 0)
Slice inputs:
- ERCOT board review 2/24 and ERCOT 4/27 cause report (Tier 1)
- FERC/NERC final report: 47 of 82 chunks selected (Tier 2)
- Enverus (Tier 2, funded by an interested party)
- 5 news articles (Tier 3)

That is 65 chunks and about 72k words. 4,199 items were extracted. Cost about $1.29.

| # | Test | Result | Pass? |
|---|------|--------|-------|
| 1 | Ontology coverage (`other` share) | **6.9%** (247 of 3,598) | ✅ (< 15%) |
| 2 | Timeline recall vs UT Austin, ±1h | **8 of 11 = 73%** in the slice; 11 of 16 = 69% overall | ✅ (> 60%), answer key not yet hand-checked |
| 3 | Scope completeness (shares) | Filled for 95% of 295 shares, but **correct for only 4 of 20 (20%)** when hand-checked. Excluding table fragments: 4 of 11 | ❌ (> 70%) |
| 4 | 12% vs 31.4% | Both found; rule says **`different_scope`** (metric, denominator, region, cause scheme differ) | ✅ |
| + | Precision (40 items) | Stated in quote 78%, type right 60%, fields right 60%; **all three right 48% (19 of 40)**. Excluding table fragments: fields right 24 of 33 | recorded |
| + | Quote check | **1.98%** of items dropped | ✅ just under 2% |

Other numbers: 1 of 70 (sub)chunks failed after two splits (`ferc_final_2021_11:c031bb`, output truncated);
15 relations dropped because an endpoint failed its quote check; 270 canonical entities after 164 merges;
comparability links: 695 `different_scope`, 5 `comparable` (true duplicates), 1 `conflict`.

Hand checks done 2026-11-14. Your marks are kept in `review_*.answered.csv`. I settled the 15 rows you marked
"check" by reading the source text, and each one has its reason in the `resolution` column. Treating every
"check" as wrong instead gives scope 10% and all-three precision 28%.

**Why test 3 failed, and the v0.2 fixes:**
1. **Table and chart fragments: 9 of 20 scope rows and 7 of 40 precision rows, all wrong.** pdftotext flattens
   FERC's tables into rows of bare numbers, so a value can't be tied to its row and column. Fix: find number-dense
   paragraphs in `chunk.py` and either skip them or send them to a separate table extractor. Hold the tables to
   Tier 0b standards, not prose standards.
2. **One scope card per document is too coarse.** FERC's gas chapters are about producing basins and the Lower 48,
   not "ERCOT+SPP+MISO", and gas shares are `share_of_volume`. Fix: a scope card per section (by heading), or a
   third pass that only fills scope.
3. **A denominator in the previous sentence gets lost** ("13 percent other fuel issues" follows "87 percent natural gas").
   Fix: give pass 2 the sentence before each number.
4. **A nearby label taken as the window:** a "(Winter 2023-2024)" tag became a reserve-margin window. That tag is the
   deadline of the preceding recommendation. (The NERC copy of the PDF is the Nov 2021 report, last modified Mar 2023.
   The 2023-24 dates in it are implementation deadlines, not a later edition.)

### Scope-fix 2×2 (2026-11-15, `results/02_scope_eval/RESULTS.md`)
20 prose cause-share sentences, pass 2 re-run in 4 cells (section cards × local context), about $0.60.
**I (Claude) wrote the gold labels myself** (`data/answer_key/scope_gold.yaml`), before seeing results. They are
graded against what the chunk text supports, not against FERC Appendix I. The labels haven't been checked by you yet.
All four scope fields right: run 1 **12/20**, v0.2 prompt alone 12, section cards 16, local context 16,
**both 18** (a 2-item gap, within noise). The 2 remaining misses are caused by the fixes: the FERC IV.C section card,
or the document default, replaces the chunk's "U.S. Lower 48". Inline-heading sections should fix that. Windows are
20/20 with either fix. The code date rule adds nothing on v0.2 output. **Local context is now the default** in `kg.extract` (`--no-context` to turn it off). Section cards stay
optional (`--section-cards`), and so does the code date rule (`kg.post --date-rule`).

**Gaps in the ontology from the hand checks** (all added in ontology/schema/prompt v0.2, 2026-11-15; not yet re-run):
- types: `Region` entity (MISO South, SPP, Texas, basins; LoadZone covers ERCOT only), `WaterServiceDisruption` and `Demand` events
- relation: `associated_with` (correlation, no causal claim)
- cause category: `power_supply_loss` (the evidence for `requires_power_from`)
- metrics: `share_of_responses`, `share_of_energy`, `wind_speed`
- schema: `value_low`/`value_high` for ranges (`value` = midpoint), plus `approximate` ("about" and "nearly" are
  approximate, not hedged). Comparability checks range overlap. Approximate values get no extra tolerance: a difference
  is still flagged `conflict`, and the link carries `approximate: true` for review.
- generic roles ("Balancing Authorities", "TOPs", "QSEs") are excluded in the prompt, and `post.py` drops any that
  still come through (`generic_role_names` in the ontology). On run 1's extractions that drops 22 entities and
  4 relations, and leaves the comparability links unchanged.

**Source disagreements the hand checks turned up:**
- When EEA 3 and load shed began: FERC says 1:15 a.m., UT Austin 1:20, the Texas Tribune 1:25.
- FERC gives two "Event overall" shares of the gas production drop caused by power loss: 23.5% and 21.5%.

What the misses and errors show:
- **All 3 recall misses are missing from the input, not missed by the extractor.** STP reaching 0°C, the STP
  trip at 05:27, and the "noon Feb 15" capacity levels don't appear in any slice chunk (checked by text search).
  So recall on events the input actually contains is 8 of 8. The 73% measures source coverage. Adding the ERCOT unit
  outage list (Tier 0b) and the FERC chapters on individual units would close these gaps.
- **The one conflict is a scope failure (test 3):** FERC's 21.5% (whole event) vs 18.1% (Feb 14) share of gas
  production losses from power outages. Both claims kept the document-default window although the sentence
  names the day. Fix: in pass 2, tell the model that a date next to a number overrides the default window.
- **Ranges:** "52 to 60 percent" became two claims. The schema has no range value; add `value_low`/`value_high` in v0.2.
- **Top `other` clusters** (candidates for v0.2 types): recommendation (11), mechanism (8), definition and
  cause-category definition (8), methodology note (3), demand peak and peak load record (4), pipeline flow change (2).
  Demand isn't an event type in v0, yet "record demand" appears in every source.
- **Cost:** the model spends about 85% of output tokens on reasoning. `--effort low` is worth testing.

### Graph, Tier 0 grounding, invariants, cold models (2026-11-15, `results/03_graph/`)
Built from run 1's extractions (no re-extraction), after two post-processing fixes:
- **Quote and value check:** numbers keep their punctuation ("31.4" ≠ "314"), and a claim's value must be written in
  its quote. This drops **22 of 1,990** claims, all derived values ("4m 23s" → 4.383, "a third", "half"); none was a
  misread digit. Links 695 / 5 / 1 → **664 / 5 / 1**.
- **Cause-label normalization** before the different-label rule: 15 merges (case variants, "Icing on blades" =
  "blade icing"), **no link changed**. X3's token_set was not used: it scores subsets as 100.

**Graph:** 3,706 nodes (1,919 claims, 948 events, 481 entity mentions → 261 entities, 26 documents including the
Figure 35 chart read, 29 series,
29 observations, 13 event classes) and 7,155 edges, with
tier, flags and claim status copied from each item's document. Bitemporal: valid time from the text, recorded time from
publication or dataset version. **Unit list 3/4 → 3/12:** 803 rows expansion (before 2/14), **135 disclosure** of the
redacted 3/4 rows (61 unique, 74 in 32 groups where the set is known but the row-to-unit pairing isn't), 3 corrections
(wind unit renames). Nothing is left unexplained; the survey's "138 added" is 135 disclosures + 3 renames.

**Tier 0 anchoring:** **29 of 2,867 items (1.0%)** anchored, **24 agree, 5 disagree**. Most claims are about other
regions, other quantities or have no usable time. Of the 5 mismatches, 2 are a board-deck layout error caught by the data
(pdftotext puts each day heading after its bullets, so the 2/16 peak was dated 2/17), 2 are FERC city lows that match
neither DFW nor IAH (probably other stations), and 1 is a small real disagreement (the Tribune's "30,000 MW" Monday vs
≥ 32,494 in the data). **Load shed began 01:20** (first nonzero minute in ERCOT's data), supporting FERC's four 1:20
statements, Enverus and UT Austin over FERC's one 1:15 and the Tribune's 1:25 (each 5 minutes off).

**Invariants:** 3 cause-after-effect violations (175 of 186 causal edges can't be ordered at their precision),
0 causal edges into weather, 0 Tier 0 overrides, 0 instance cycles. The gas–power loop has a cycle at the entity level
and none once unrolled hourly (480 nodes).

**Cold models** (hourly MW out by fuel, train Feb 14–17, test Feb 18–19): fail-restart wins out of sample for
**gas, coal and wind** and passes the ±5-day placebo; **solar** is best fit by the instantaneous model. Gas is explained
worst by instantaneous temperature but best by fail-restart. Wind and solar did not have the highest failure rate α
(coal and gas did). `WeatherCondition → GenerationOutage[gas | coal | wind]` are graded **G3** (test + asserted text).
Limits: one event, system-level data by fuel, a dose-response fit, not a randomized comparison.

### Hypotheses from the documents (2026-11-16, `results/04_hypotheses/`)
The documents propose causal claims; Tier 0 tests them. Grouping, measurement, tests and grades are hand-written rules
and deterministic code. How each concept is measured (`config/proxies.yaml`) was committed alone before any test ran
(`07ab729`). One amendment followed the first run (`fb89615`, Permian temperature may stand for gas supply only as a
cause; the 3 affected families had failed and stay G1).

- **Step 1, weather-free baselines:** the fail-restart model refitted with a fitted tent and with hours since Feb 14 in
  place of temperature. Requiring temperature to beat both by 1.25× out of sample, **gas and wind fall from G3 to G1**
  (tent margins 1.11 and 1.17), all fuels together fail (1.14), and **coal keeps G3** (5.88). G3 now needs independent
  support: Tier 1–2 text from a document that didn't publish the test's data, or a second test on other data.
- **61 families** from 152 in-scope relations: 57 from documents only, 3 both, 1 skeleton only; 35 headline.
  **14 tested** (11 direct, 3 via a proxy), **47 untestable**. Grades: G3 3, G2 5, G1 49, G0 4.
- **G3:** grid emergency (frequency low) → coal outages (+933 MW four minutes after the 01:55 low, coal flat before),
  the same for all fuels (weakened by a pretrend the design check flags), and generation outages → load shed
  (accounting plus timing: outages rose 10 h before the first shed).
- **Failed:** cold → all-fuel and wind outages (H2 margin), gas supply → gas outages (Permian-driven model far worse
  than plant temperature), load shed → gas outages and restoration → load shed (timing).
- **Untestable**, most often for lack of: multi-region series (16), facility shutdown records (12), FERC's daily gas
  production (Fig. 35, a chart, 12), a frequency or EEA series covering the whole storm (7), metered power at gas
  facilities (the `power_supply_loss` loop).

Files: `hypotheses.json` and `hypothesis_graph.json` for the frontend, `hypothesis_overview.png` (tiers, families,
measurements and the inference path), `hypotheses_report.md`, `cold_baselines.md`, and a chart per tested family.

**Review fixes (2026-11-16).**
- **Figure 35, FERC's daily gas production chart,** is now read off the image by code (`scripts/digitize_fig35.py`). It
  matches FERC's stated totals within 1 Bcf/d, and Texas falls from 21.3 Bcf/d to 7.2 on Feb 17.
- **Grouping:** loss of power at gas facilities is one concept, so power loss → gas supply has 20 supporting relations
  and stays untestable. Denials count as contrary evidence: gas supply → gas outages is **contested**, asserted by FERC
  and denied by Enverus.
- **Grading:** timing-only checks no longer pass ("order consistent", max G1), and outages → load shed is an
  `accounting` relation with no causal grade.
- **New tests:** 2(a) (production added to the gas model) does not improve the fit, and cold → production is
  inconclusive by design.
- **Grades are now:** G3 2 (frequency → coal, frequency → all outages), G1 50, G0 3, plus 2 accounting relations (57 families after the final re-run; see below).

Lead visual: `hypothesis_lead.png` shows three edges: confirmed (frequency → coal), contested (gas supply → gas
outages) and untestable (power loss → gas supply).

### Causal ML with spatial confounders (2026-11-15, `results/05_causal_ml/`)
EIA-860's ERCOT fleet is matched to ERCOT's outage list (249 of 285 stations, 88% of outage energy), so plants that
never failed are in the data. The panel is 329 plant × fuel units over Feb 10–19. Day fixed effects identify the effect
of cold from plants that saw different cold on the same day.

- **Average effect:** 0.70 percentage points of capacity out per °F below freezing (95% CI −0.00 to 1.41,
  plant-clustered). The causal forest agrees (0.67).
- **By fuel:** only wind clears zero: 1.14 pp per °F, about 300 MW per °F for the panel's wind fleet. Gas is positive
  (0.72) but not distinguishable from zero.
- **Warmer storm:** +5 °F would have avoided about 2.5 GW on Feb 16 (CI −4.1 to 9.2 GW).
- **Gas supply vs plant cold:** not identified (basin cold is just geography once days are fixed).
- **Map:** the most cold-sensitive plants per °F are Panhandle wind and gas near the Houston coast.
- **Files for the geographic interpretation:** `cate_plants.geojson` (per-plant effects with every spatial feature) and
  `data/processed/spatial/geojson/` (counties, plants, stations, plays, basins, processing plants, pipelines, transmission,
  ERCOT zones).

### Grid replay and the coal contradiction (2026-11-15, `results/06_grid/`)
TAMU's synthetic Texas grid is replayed hour by hour (Feb 14–18) with a DC optimal power flow in Python. The inputs are
measured: ERCOT's demand and MW out by fuel, placed by the outage list's geography, and EIA's actual wind and solar.

- **Validation:** 919 GWh of shed simulated vs 1,000 GWh actual, hourly correlation 0.93 (TAMU's own simulation:
  0.87), first shed in the same hour.
- **Bottlenecks:** during shedding the grid was short of capacity everywhere, not split by transmission. In 64% of
  shed hours every bus had the same shortage price.
- **The frequency trips:** the outage list independently reproduces FERC's figures (coal exactly 1,769 MW, gas 2,153
  vs 2,190 MW). Without them the replay sheds 94 GWh less. The coal trips were brief (both units back by noon, about
  16 GWh); the gas units tripped in the same minutes stayed out for days (about 78 GWh).
- **Coal over the whole storm:** an hourly hazard test can't separate local cold from grid stress, because there are
  only 11 coal outage starts. The coal MW that stayed out for days still lacks a measured cause (frozen coal handling
  per FERC; no Tier 0 series).
- **Files:** `grid_lines.geojson` and `grid_buses.geojson` hold line loading, nodal prices and simulated shed for the
  map.

### Public-release re-run (2026-11-16)
Every stage after extraction was re-run from the committed code (`make all`) so the published outputs match it. Some
committed outputs had been written by earlier code. What changed:
- `01_extraction`: graph and scores now include the step-0 quote check (1,941 → 1,919 quant claims, 462 → 458
  relations); the hand-check results are unchanged.
- `03_graph`: 26 documents (the Figure 35 chart read is now a node).
- `04_hypotheses`: **57 families** (was 58). One multi-region relation that had been grouped as "warming weather →
  generation outages" (untestable, G0) is now in the cold-weather family (10 → 11 relations). Grades: G3 2, G1 50,
  G0 3, accounting 2. No test or tested grade changed.
- `06_grid/coal_test.json` now holds the no-coal-outages counterfactual quoted in the README (919 → 650 GWh, −29%).
- `05_causal_ml` and the cold models differ only by floating-point noise (relative differences below 1e-9).

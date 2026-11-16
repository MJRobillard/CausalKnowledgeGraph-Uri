# Assumptions
Updated 2026-11-15. Everything the data work and the pipeline take for granted, in one place.

Each item is marked:
- **Verified**: checked against data. The evidence is given.
- **Assumed**: believed but not checked, or checked only partly.
- **Choice**: a convention or design decision, not a fact.

Details for the TAMU items are in `docs/tamu_cleaning.md`.

## 1. Time
| # | Assumption | Status | Evidence / note |
|---|---|---|---|
| T1 | Central time for 2/1–2/21/2021 is CST (UTC−6) throughout | Verified | Daylight saving time started 3/14/2021 |
| T2 | Hourly values are labelled by the **start** of the hour; all cleaned files use `time_central` (ISO with offset) | Choice | Matches `dataset_survey.md`. EIA "hour ending" labels are moved back 1 h |
| T3 | ERCOT high-resolution data is in Central time | Verified | Frequency low 59.302 Hz at 01:54:58, and reports say "about 1:55 a.m." on 2/15 |
| T4 | NOAA NCEI hourly data is in UTC | Verified | Houston's coldest hour is 11:00 UTC, which is 5 a.m. local |
| T5 | EIA grid-monitor labels ("1/31/2021 12 a.m. CST") are hour ending, CST, and correct | Verified | On 2/15, EIA demand equals ERCOT's estimated load minus load shed: 65,255 vs 65,268 and 63,395 vs 63,218 MWh, at lag 0 |
| T6 | The EIA API file is hour ending, local prevailing Central (CDT in summer) | Verified for Feb 2021; Assumed for summer | It matches the CSV export exactly at a 1 h shift, and its only duplicate labels are the DST fall-back hours. Summer offsets weren't checked against another source |
| T7 | poweroutage.us `RecordedDateTime` is UTC | Verified | Statewide customers out first passes 500k at 08:20 by the raw label. ERCOT's 9.5 GW shed jump was at 01:59 CST (07:59 UTC) |
| T8 | BES wind profiles are UTC, as labelled | Assumed | Fit to EIA wind is nearly the same at −6 h (r = 0.789) and −7 h (0.795). I kept the label |
| T9 | Times in ERCOT's unit outage lists (START/END) are Central | Assumed | ERCOT document. Consistent with TAMU's derived matrices (first NG outage hour = earliest NG START) |
| T10 | TAMU's simulation clock: row 0 = 2/1/2021 00:00 CST | Verified | `process_event.m` puts 2/14 00:00 CST at row 313 |
| T11 | `ercot_capacity.csv` is hourly from 2/12 00:00 (only row 1 has a time) | Assumed | From `process_event.m`, which puts its 168 rows at simulation rows 265–432 |
| T12 | The model resolves times in text to Central using the publication date | Choice | Prompt rule 4. Ambiguous times are left null, never guessed |

## 2. TAMU data (2021TXPowerOutage)
| # | Assumption | Status | Evidence / note |
|---|---|---|---|
| D1 | The BES demand profile is EIA ERCOT subregion data, and its true start is the hour beginning 1/30/2021 23:00 CST | Verified | All 432 rows match EIA exactly, with one constant offset |
| D2 | TAMU's model therefore ran demand 25 h early, and wind 6 h late, relative to its outage inputs | Verified for demand; follows from T8 for wind | Finding about TAMU's model. Not fixed; both clocks are kept in `pg.csv`/`pf.csv` |
| D3 | The BES solar and hydro "Feb2016" profiles are real 2016 weather-year stand-ins, not mislabelled 2021 data | Assumed | Weak day-to-day fit to 2021 (solar r = 0.47, hydro ≤ 0.17), and the file names say 2016. Cleaned files give the hour each row was *used* |
| D4 | PG/PF row *i* = simulation hour *i* | Verified | Simulated generation sums to the demand profile in every row (ratio 1.000000) |
| D5 | 12 of the 13 numeric county codes are the counties the same utility reports by name with the same customer count | Verified | Customers-tracked counts match within 1% |
| D6 | Code 222 is Terrell County | Assumed | Alphabetical-index lookup only. The utility never names Terrell, but it borders that utility's service area |
| D7 | Percent-out above 100% is a reporting error, so it's capped at 100% | Choice | 46 cells, none caused by a zero denominator. Raw rows with out > tracked are flagged, not changed |
| D8 | The first 48 hours of `cty_out_pct` and `cty_out_dist` are missing, not zero | Verified | TAMU's loop starts at hour 48 |
| D9 | The 135 "blank" rows in the 3/4 unit outage list are redacted entries | Assumed (consistent, 2026-11-15) | Every START/END pair appears as a named unit in the 3/12 list, but 32 pairs match more than one unit. Rechecked in `kg/unit_revisions.py`: all 135 match 3/12 rows that are absent from 3/4; 61 match one unit, 74 fall in 32 groups. In every group the number of 3/12 candidates equals the number of blank rows (31 of 32 groups are one station), so which units were disclosed is determined; only the row-to-unit pairing is ambiguous |
| D10 | Numeric-name columns that duplicate a named county column hold the same customers, so they're dropped | Verified | Out values are all 0, and the tracked counts match the named column |

## 3. Other datasets
| # | Assumption | Status | Note |
|---|---|---|---|
| O1 | One NOAA station per ERCOT weather zone stands in for that zone's temperature | Assumed | Rough. CDO county stations cover 83.7% of MW lost. Two FERC city lows (Dallas 8 °F, Houston 10 °F) don't match DFW or IAH on any day, probably another station |
| O2 | Henry Hub is a lower bound for Texas gas prices | Assumed | Texas hub daily prices are behind paywalls |
| O3 | The 3/12 unit outage list supersedes the 3/4 version | Assumed | Wider window (from 2/10) and more units. TAMU used 3/4. Classified 2026-11-15: 803 rows expansion (before 2/14), 1,658 unchanged, 135 disclosure (61 unique + 74 ambiguous), 3 corrections (all wind unit renames, one also 241 → 224 MW). The survey's "138 added, 3 dropped" is 135 disclosures plus 3 renames |
| O4 | ERCOT's 12% (MW at the peak minute, ERCOT only) and FERC's 31.4% (count of events, 3 areas, 2/8–20) measure different things | Verified | Both documents state their own scope |

## 4. Trust model
| # | Assumption | Status | Note |
|---|---|---|---|
| R1 | Tier comes only from `documents.yaml` plus the ontology's `tier_rules`. The model never sees tier or flags | Choice | The core design |
| R2 | Flags (`interested_party`, `funded_by_interested_party`, `preliminary`, `self_reported`) lower trust but never change the tier | Choice | |
| R3 | The flags assigned to each document are right (e.g. Enverus is funded by TXOGA; ERCOT reports are interested party) | Assumed | Hand-assigned from each document's front matter |
| R4 | The UT Austin timeline is a fair answer key | Assumed | It draws heavily on ERCOT and PUCT figures, so it isn't independent of them. It's held out from extraction |

## 5. Extraction and post-processing
| # | Assumption | Status | Note |
|---|---|---|---|
| X1 | A quote counts as found if it matches after keeping lowercase letters, digits and `%`, plus `.` and `,` between digits and `-` before a digit (updated 2026-11-15) | Choice | Robust to hyphenation, ligatures and sentence punctuation, but "31.4" no longer matches "314". On run 1 the change dropped no extra items (quote failures still 49 claims, 20 events, 9 entities, 5 relations) |
| X2 | Entities merge on exact name, then the alias list, then fuzzy match (token sort ≥ 90, same digits) within a type | Choice | No embeddings: DeepSeek has no embeddings API |
| X3 | Two denominators are the same if their fuzzy token-set similarity is ≥ 80 | Choice | |
| X4 | Values agree within 0.5 points (percents) or 1% (everything else) | Choice | Rounding only |
| X5 | Approximate values get **no extra tolerance**: a difference is still a `conflict`, and the link carries `approximate: true` | Choice (2026-11-15) | Flagging contradictions is the point. Revisit after review |
| X6 | A range is one claim with `value` = midpoint. Two values agree if their ranges overlap | Choice | A range that contains the other value isn't a contradiction |
| X7 | Within one cause scheme, a different cause label means a different (sub)category, so the link is `different_scope`, not `conflict`. Labels are normalized first (`label_key`: lowercase, split on - and /, singular, drop filler words such as "related" and "of"); equal word sets, or one typo (one differing word, same first 3 letters, ratio ≥ 85), are the same label | Choice (normalization added 2026-11-15, with the user) | FERC 31.4% fuel vs 27.3% gas fuel supply. X3's token_set was rejected: it scores subsets as 100 and would merge "fuel issues" with "natural gas fuel supply issues" and "other fuel issues". Plain token_sort ≥ 90 was rejected: it merged "Planned" with "Unplanned Generation Outages". On run 1: 15 label merges, **no link changed** (they were case variants, or pairs that differ on other fields) |
| X8 | A preliminary report vs its final version is `superseded`, not `conflict` | Choice | |
| X9 | Generic roles ("Balancing Authorities", "TOPs", "QSEs") aren't entities | Choice | Prompt rule plus a hand-written name list in the ontology that `post.py` uses as a backstop |
| X10 | When a sentence doesn't state scope, the document card's default applies | Choice | A known weak point. In the hand checks, 9 of 20 scope rows were table fragments. Other misses: one card per document is too coarse, a denominator in the previous sentence was lost, and a date next to a number was ignored |
| X11 | Gold scope labels are graded against what the model's input supports (chunk text plus cards), not outside knowledge such as FERC Appendix I | Choice (user, 2026-11-15) | So "U.S. Lower 48" is right for c040's gas shares, and the document default is accepted where a chunk names no region (c041). Claude wrote the labels; not yet checked by the user |
| X13 | A quant claim's value must be written in its quote: as digits (31.4, 31.4%, 20,000, "2, 300"), as its absolute value, times a scale word ("4.5 million"), or as a number word or ordinal up to the twenties ("six", "twenty-five", "third"). Otherwise the claim is dropped | Choice (2026-11-15) | On run 1 it drops **22 of 1,990** claims, all derived values: durations ("4m 23s" → 4.383, "7 hours, 24 minutes" → 7.4), fractions ("a third", "half", "a tenth"), "twice", "a week", "has no wind" → 0. Links: 695 / 5 / 1 → **664 / 5 / 1**; all 31 fewer `different_scope` links come from the dropped claims |
| X12 | The code date rule fires only when the sentence names exactly one February date or range and the window was a default or incomplete | Choice | "Feb 15 and 17" counts as two dates; "between Feb 14 and 17" and "Feb 14–19" are ranges |

## 5b. Graph (step 1, 2026-11-15)
| # | Assumption | Status | Note |
|---|---|---|---|
| G1 | Tier 0 datasets are registered in `documents.yaml` as `doc_type: dataset, measured: true`; weather also has `exogenous: true`, which the first tier rule maps to 0a | Choice | Tier still comes only from the registry and `tier_rules` (R1). Measured datasets are refused by `extractable()` |
| G2 | A canonical Entity is not an item: provenance stays on its EntityMention nodes (one `stated_in` each), linked by `mention_of`. The Entity node's tier is the best tier among its mentions | Choice | Relations point at the canonical entity and keep the mention gids in `attrs` |
| G3 | Relations are edges, so their provenance is the edge's own `doc_id`, tier, flags and claim_status rather than a `stated_in` edge | Choice | `graph_build.check()` asserts every relation has a document |
| G4 | Recorded time = document `published_at`; for datasets, the version date (unit lists 3/4 and 3/12; ERCOT board data 2/24; NOAA the download date) | Choice | |
| G5 | QuantClaim valid time = `as_of` when it is an ISO time, else the window | Choice | Claims have no time of day of their own; step 2 also uses the `about` event's time |
| G-R1 | Document revision kinds: ERCOT 4/6 → 4/27 cause report is a `correction` (categories redrawn, peak 52,277 → 52,037); FERC/NERC slides → final report is an `expansion` | Choice | Hand judgment, marked `revision_kind_source: hand` on the edge |
| G6 | The 247 `other` items from extraction are not graph nodes | Choice | They have no type to anchor or connect |
| G7 | Unit-list rows are matched in this order: same (station, unit, START); blank rows by (START, END); renamed units by (START, END, MW), or by (START, END) when that pair is unique; moved START within 24 h. Anything left over is `added_unexplained` / `dropped_unexplained` | Choice | Nothing was left over |

## 5c. Tier 0 anchoring (step 2, 2026-11-15; rules in `config/anchor_rules.yaml`)
| # | Assumption | Status | Note |
|---|---|---|---|
| A1 | ERCOT minute labels (MW out by fuel, load shed) are minute-**ending** | Assumed | The files run from 00:01 on 2/14 to 00:00 on 2/20: 8,640 rows = 6 whole days. Under this reading load shed starts in the minute 01:20–01:21, matching FERC and UT Austin. Minute-start labels would move every time one minute later |
| A1b | ERCOT's hourly "estimated load without shed" is hour-ending | Verified | First label 2/15 01:00; its value 65,268 is EIA's 2/15 00:00 hour plus shed (T5) |
| A2 | Claims scoped to "Texas" anchor to ERCOT grid series, flagged `region_approx` | Choice (user, 2026-11-15) | ERCOT serves about 90% of Texas load. Multi-area, Lower-48, SPP and MISO scopes are never anchored to ERCOT |
| A3 | Value tolerance = max(half a unit of the last written digit, 1%); frequency uses the half unit only | Choice (user, 2026-11-15) | 1% of 59.302 Hz would be ±0.59 Hz |
| A4 | Time window from precision: minute ±5 min, hour = that hour ±1 h, day, range. Peak/low claims use the series max/min in the window | Choice | From the task brief |
| A5 | `approximate` = the extracted flag (v0.2) or, for v0 extractions without it, an approximate word within 25 characters before the number. Approximate claims get 5% | Choice | Without the "just before" limit, "52,277 MW (approximately 48.6%)" made the MW figure approximate |
| A6 | A claim takes its time from its `about` event only if the two quotes overlap (one contains the other) | Choice | The model linked FERC's "as low as six degrees in Austin" to an event dated Feb 8 from another sentence |
| A7 | What an MW number measures is read from keywords within 60 characters of it, then from the whole quote: load shed, demand (EIA hourly, or ERCOT's estimate if "without load shed"), or MW out (one fuel word → that fuel, none → total, several → not anchorable). Exclusions (reserves, criteria, forecasts, unit sizes, changes such as "additional" or "reduction", counterfactuals) are checked only near the number. MW with a cause category is MW by cause, which no series measures | Choice | Keyword routing is the weakest part of anchoring; every anchored claim was read by hand on 2026-11-15 |
| A8 | "More than / over / at least" make a claim a lower bound; "up to / less than / under" an upper bound; "just above / just short of" are approximate | Choice | Differs from the ontology, which lists "more than" and "over" as approximate |
| A9 | Daily temperature extremes ("as low as", "fell to" with a day or range) are checked against GHCN-Daily TMIN/TMAX for the zone's airport station; anything else uses the hourly METAR file | Choice, evidence | Hourly readings miss the true extreme: DFW 2/16 hourly low −0.9 °F, TMIN −2 °F |
| A10 | "By 7 p.m." means at or before, within the hour before | Choice | |
| A11 | A non-extreme claim stated to the minute is not compared with an hourly series (`no_series`) | Choice | E.g. "load was at 55,391 MW" at 01:11 vs an hourly average |
| A12 | The claim's whole window must lie inside the series coverage | Choice | Strict. The board deck's "Min Frequency 59.302 Hz" matches the data exactly but is not anchored: its window is 2/14–2/19 and the frequency series covers 42 minutes |
| A13 | A non-extreme claim agrees if any value in its window is within tolerance | Choice | The text's time is imprecise by the window |
| A14 | Named-unit events are matched to unit-list stations by token_sort ≥ 90 after dropping generic words | Choice | Run 1 has no named ERCOT unit with a usable time (FERC anonymizes units), so nothing anchored this way |
| A15 | Reliability scores count a mismatch against the source even when the extraction caused it | Choice (limitation) | 2 of 5 mismatches are a board-deck layout error (day headings follow their bullets in pdftotext order) |

## 5d. Invariants and grades (step 3, 2026-11-15)
| # | Assumption | Status | Note |
|---|---|---|---|
| I1 | An event's start is an interval set by its precision (minute, hour, day; a range's start is a day unless it has a clock time). Cause-before-effect is `ok` only if the cause's interval ends before the effect's begins, `violation` only if the cause starts after the effect has started; otherwise `time_unverifiable` | Choice | Same-day events at day precision can't be ordered. 175 of 186 causal edges are unverifiable |
| I2 | The class layer groups event instances by type; GenerationOutage is split by fuel when its summary or quote names exactly one fuel | Choice | Deterministic keywords. Events naming no fuel or several stay in the unsplit class |
| I3 | Gas–power loop lags: Cold(t)→GasProduction(t+1), GasProduction(t)→GasGeneration(t+1), GasGeneration(t)→LoadShed(t), LoadShed(t)→GasProduction(t+1) | Choice | From the task brief; system knowledge, not fitted |
| I4 | Quant claims whose cause label names loss of power count as evidence for LoadShed → GasProduction | Choice | Run 1 used the v0 vocabulary, which had no `power_supply_loss` category |
| I5 | G3 = a passed test (G2) plus an asserted text edge for the same class pair, or a second passed test. Text is treated as independent of the measured test | Choice | |
| I6 | A G1 grade needs one asserted instance; a class edge with only hedged instances stays G0 with the flag `hedged_only` | Choice | From the task brief |

## 5e. Cold → outage models (step 4, 2026-11-15)
| # | Assumption | Status | Note |
|---|---|---|---|
| C-M1 | Temperature for each fuel = the 9 zone stations weighted by the fuel's HSL capacity per zone. Each county is assigned to its nearest station using the mean coordinates of its CDO stations; station coordinates are rounded airport coordinates | Choice | 95 of 113 counties have coordinates. Capacity share with a station: gas 96%, solar 95%, coal 82%, wind 78% |
| C-M2 | C (a fuel's available capacity) = the HSL sum of that fuel's distinct units in the 3/12 outage list | Choice | A lower bound on the fleet (only units with an outage). Observed MW out stays below C for every modelled fuel |
| C-M3 | Placebo passes if both ±5-day shifted fits have test RMSE ≥ 1.25× the real fit's | Choice | The +5-day shift is the weak test (warm days); the −5-day shift (Feb 9–14) is the strict one |
| C-M4 | Hourly MW out = the mean of the minute values in the hour (minute-ending labels h:01–h+1:00 → hour h) | Choice | Follows A1 |
| C-M5 | NOAA hourly data was extended to Feb 26 with the same cleaning rule, reconstructed from the raw file (100% of cells within 0.05 °F) | Verified | `scripts/extend_zone_temps.py` checks the reconstruction before writing |
| C-M6 | Model 3 is free-run from the observed value at Feb 14 00:00, with no baseline term | Choice | From the brief. Pre-storm outages must decay as if cold-caused, which penalizes model 3 |
| C-M7 | One passed test per fuel is written back (the best of M2 and M3 that beats M1 and passes the placebo); M2 and M3 on the same data don't count as two independent tests | Choice | |

## 5f. Hypotheses (2026-11-16, `results/04_hypotheses/`)
| # | Assumption | Status | Note |
|---|---|---|---|
| H1 | Weather-free baselines for the fail-restart model: a **tent** (rises from Feb 14 00:00 to a fitted peak hour, falls to zero at a fitted end hour; peak grid 24–96 h in 4 h steps, end from peak+12 h in 8 h steps) and **hours** since Feb 14 00:00, both scaled to the cold driver's maximum | Choice (user chose the tent, 2026-11-16) | Only training data and no weather enter the tent. A rising-only ramp can't fall during the test window, so it would be a weak baseline |
| H2 | A weather → outage edge stays above G1 only if the temperature model passes its placebo **and** both weather-free versions have test RMSE ≥ 1.25× the temperature version | Choice | Same ratio as C-M3. With 48 autocorrelated test hours a smaller gap isn't a clear win. Result: gas (1.11), wind (1.17) and total (1.14) fail on the tent; coal passes (5.88); solar fails its placebo |
| H3 | The all-fuels target (`total`) uses temperature weighted by all units' HSL per zone | Choice | For the fuel-less WeatherCondition → GenerationOutage family |
| H4 | **Independence for G3:** a second passed test on a *different data source*, or an asserted Tier 1–2 text claim from a document that *wasn't used to build the test's inputs*. A document is an input if it published a dataset the test uses (`source_document` in the registry: the ERCOT board deck for MW out and frequency). Tier 3 never counts | Choice (user definition, 2026-11-16) | The existing grade test was updated to this rule |
| H5 | Family key = (cause class, effect class, fuel, region class), classes from `event_class` (I2). Region class = the effect event's region, else the cause's, else the document's default scope; classes by hand-written patterns in `config/hypotheses.yaml` (multi-region first, then gas basins, ERCOT, Texas cities read as ERCOT, SPP, MISO, other) | Choice | 61 families from 152 in-scope relations (57 documents, 3 both, 1 system knowledge). 32 relations are out of scope (SPP 20, MISO 11, other 1) |
| H6 | In scope: ERCOT, Texas gas basins, multi-region. A family is `headline` if it has ≥ 2 supporting relations or comes from the skeleton | Choice (user, 2026-11-16) | 35 headline |
| H7 | Same-type pairs (LoadShed → LoadShed) are kept as families, flagged `within_type` | Choice | 8 families |
| H8 | Skeleton families: Cold → gas supply is a gas-basin edge; the other three skeleton edges are ERCOT | Choice | The skeleton edge gas generation → load shed has no text support (origin `system_knowledge`) |
| H9 | Two status rules fixed after the proxies commit but before any test ran: a same-type family (both ends the same series) is untestable; a family without a dedicated pre-registered test is untestable if either series doesn't cover Feb 14–19 (the frequency series covers 42 minutes) | Choice | Disclosed as not part of the pre-registration commit |
| H10 | **Amendment after the first test run:** the Permian-temperature proxy may stand for gas supply only as a cause, never as an effect (a temperature series can't respond to the cause) | Choice (post-hoc, separate commit `fb89615`) | The 3 affected families had all failed; they become untestable, grade unchanged (G1). Pre-amendment results kept in `hypothesis_results_pre_amendment.json` |
| H11 | Generic timing test: onset = first hour the series is ≥ 10% of its storm range from its Feb 14 00:00 value; passes if the cause's onset precedes the effect's; capped at G2 | Choice (pre-registered) | Restoration = running peak of MW out minus MW out (the pre-registered "decrease in MW out"); the first run used −MW out by mistake and was corrected |
| H12 | Outage → shed onset = first minute MW out is ≥ 2,000 MW above its Feb 14 00:00–11:00 mean; frequency → coal compares the 15-minute change after the low with all 15-minute changes, 2/14–19 | Choice (pre-registered) | |
| H13 | FERC Figure 35 (daily dry gas production by state, from IHS) is read off the raster chart by code (`scripts/digitize_fig35.py`): axes fitted to the 10 Bcf/d gridlines and the 15 date labels, band heights per day's pixel column. The values are **Tier 2 chart reads**, not measurements | Verified against FERC's text, within 1 Bcf/d | Read 90.07 vs FERC's 90.8 Bcf/d on 2/8, and 64.92 vs 65.4 on 2/17 (both about 0.6 low). Resolution 0.26 Bcf/d. Texas Feb/Jan change −16.4% vs EIA monthly marketed −15.4%. Total and sum of bands agree within 2.1 Bcf/d |
| H14 | **Amendment 2 grouping rules** (`config/hypotheses.yaml` → `reclassify`, keyword rules on event text): loss of power at gas facilities is one concept, `PowerLoss`, whatever type the extractor gave it; load shed → gas supply is read as PowerLoss → gas supply (`via: LoadShed` kept); a gas-facility shutdown is gas supply disruption (PowerLoss if its text says power was lost); warming weather is its own class (`freeze-thaw` is not warming); gas → gas families split by stage (production, processing, pipeline, delivery); relations with an event dated before 2021 are excluded | Choice (after the hypotheses review, 2026-11-16) | PowerLoss → gas supply now has 20 supporting relations (FERC 11, Enverus 9), the best-documented edge, and is untestable. 2 historical relations excluded |
| H15 | Denied relations are contrary evidence, not support. A family with an asserted relation and a Tier 1–2 denial is `contested` | Choice (review) | Gas supply → gas-fired outages and → all outages (ERCOT): FERC asserts, Enverus (funded by TXOGA) denies |
| H16 | Timing-only checks never count as passes: they are reported as "order consistent / inconsistent" and the family's grade comes from text alone (max G1). Accounting identities (outages → shed) get status `accounting` and no causal grade. Failed tests read "not supported by this test" | Choice (review; pre-registered in amendment 2) | In a storm almost everything starts in order; warming → restoration passed when it was stored as cold → restoration |
| H17 | Test 2(a): Texas production loss from Fig. 35 as a second failure driver in the gas fail-restart model; passes only if test RMSE improves by 1.25× | Choice (pre-registered, amendment 2) | Result: test RMSE 1,725 MW vs 1,164 cold only. Does not improve |
| H18 | The daily cold → Texas production test is labelled **inconclusive (design)** when its training days contain no recovery, because the restart rate can't be fitted | Choice (label added after the run; grade unchanged, G1) | Training Feb 1–16 ends at the production trough. A valid design (restart rate fixed from the hourly fits, or training through the first recovery days) has not been run |

## 5g. Fleet, spatial layer and causal ML (2026-11-15)
| # | Assumption | Status | Note |
|---|---|---|---|
| S1 | The fleet is EIA-860 2020 operable generators in the ERCOT balancing authority (ERCO), aggregated to plant × fuel | Choice | 458 plant-fuel rows. Plants with no row in ERCOT's 3/12 list are treated as having no outage, Feb 10–19 |
| S2 | ERCOT stations are matched to EIA plants by rules, in order: station code = prefix of EIA's RTO/ISO LMP node name; same county and fuel with similar names (token_set ≥ 85, initials joined); the only plant of that fuel in the county within 30% capacity; a unique statewide name match ≥ 92; the unique unclaimed plant in the county with the same fuel and capacity within 3% | Choice | 249 of 285 stations, 88% of outage MWh. Unmatched: about 4.6 GW gas, 3.8 GW wind, 0.6 GW solar |
| S3 | A unit's MW out at any moment is the largest of its overlapping rows (a derate and an outage of the same unit are not summed) | Choice | Daily MWh on a 15-minute grid |
| S4 | Daily cold at a plant = degrees below 32 °F at the daily low, inverse-distance weighted (power 2) from the 4 nearest GHCN-Daily stations that reported that day within 75 km, else the nearest anywhere | Choice, checked | 758 stations; median nearest 15 km; 0.04% of plant-days used the fallback. Matches the DFW and IAH airport lows within about 1 °F |
| S5 | Gas basins are EIA's tight-oil/shale-gas plays grouped by the layer's Basin field (Permian, Eagle Ford, Haynesville-Bossier, Barnett, Anadarko); a plant's supply basin is the nearest one | Choice | Nearest is a geographic proxy, not the plant's actual gas supply |
| S6 | Reference geometry set by hand: five load centres (city halls) and a 10-point Gulf coastline (Brownsville to Sabine Pass) | Choice | Accurate to about 10 km |
| S7 | ERCOT weather and load zone polygons are third-party ArcGIS layers (Esri renewables staff; an ICF user); gas processing plants are a public user copy of EIA's layer; pipelines, transmission (≥ 230 kV) and power plants are Esri Federal User Community mirrors of EIA/HIFLD | Assumed | Provenance in `data/raw/manifest.json`. 24 plants fall outside the weather-zone polygons, 4 outside the load zones |
| M1 | Panel: plant × fuel × day, Feb 10–19; outcome = MWh out ÷ (capacity × 24), clipped to [0, 1]; treatment = S4 cold | Choice (user: unit × day panel) | 329 units, 3,290 rows |
| M2 | Identification: day fixed effects plus cold accumulated before the day plus plant features as controls, so the effect comes from plants with different cold on the same day | Choice | Removes statewide shocks (load shed, statewide gas supply, frequency events) but not local ones |
| M3 | Plants that might hold an unmatched station's outages (same county and fuel as an unmatched station, no matched station of their own) are excluded | Choice | 57 units, 10.3 GW. A first version excluded every plant in such counties (101 units, 25.7 GW); narrowed before any result was read |
| M4 | Inference: LinearDML with 5-fold cross-fitting grouped by plant; the reported average effect re-estimates the final stage with plant-clustered SEs. The within-day permutation placebo is a sanity check, not the inference | Choice | Spatial correlation across nearby plants is not modelled, so intervals are optimistic |
| M5 | Counterfactuals are linear in each plant's causal-forest effect: warmer = cold reduced by Δ (not below zero); winterization = the top quarter of each fuel set to the fuel median | Choice | Not a grid simulation; ignores load shed, gas supply and restarts |
| M6 | The gas-supply split uses cold at the nearest basin as a second treatment | Choice | Result not identified (wrong-signed); with day fixed effects it reduces to geography |

## 5h. Grid replay (2026-11-15, `results/06_grid/`)
| # | Assumption | Status | Note |
|---|---|---|---|
| GR1 | TAMU's synthetic grid (`bte2k/case_Mar3_5pm.mat`, from Breakthrough Energy Sciences) stands in for ERCOT's network | Assumed | 2,000 buses, 3,206 lines, 606 generators; capacity by fuel matches EIA-860 (coal 14,475 MW in both). Substation coordinates checked by name (Houston buses in Harris, Dallas in Dallas). Bottlenecks are properties of this model, not of ERCOT's real lines |
| GR2 | DC optimal power flow per hour; linear costs at half output; load shed is a slack at $9,000/MWh; no unit commitment, ramping, losses, frequency dynamics, HVDC or DC ties | Choice | Solved with SciPy HiGHS, about 0.8 s an hour |
| GR3 | Bus weather zone = bus area (301–308); county = point in Census county polygon (TAMU's county pickles are not loaded) | Choice | |
| GR4 | Demand per hour = ERCOT's estimated load without shed (EIA demand on 2/14), split by EIA subregion shares (2/18 reuses 2/17's), then over the zone's buses by base-case load | Choice | Subregion shares are of served demand, slightly distorted while shedding |
| GR5 | Unavailable capacity per fuel = ERCOT's measured MW out (hourly mean), placed on the grid's generators by the 3/12 list's county pattern that hour (capped at each generator, remainder statewide); wind and solar after outages are scaled to EIA's actual output | Choice | The 3/12 list alone misses about 30% of measured gas MW out |
| GR6 | 1,000 MW of thermal headroom is held back as reserve | Choice | Without it the model sheds too little; ERCOT kept reserves throughout EEA 3 |
| GR7 | Where the model sheds is arbitrary when no line binds (one shortage price everywhere) | Limitation | 64% of shed hours have one price statewide |
| GR8 | The frequency-trip counterfactual restores the 3/12 rows (coal and gas) starting 01:50–02:30 on 2/15, at their county's generators, for as long as each stayed out | Choice | They total 1,769 MW coal (FERC: 1,769) and 2,153 MW gas (FERC: 2,190) |
| GR9 | Coal hazard test: a unit-hour is at risk if the unit was not out in the previous hour; an event is a 3/12 row starting in the hour; cold = the nearest zone station's hourly temperature; reserve = ERCOT available generation (15 s, hourly mean) minus EIA demand served, lagged one hour; logit with unit-clustered SEs | Choice | Reserve is endogenous to simultaneous trips (common shocks); coal has only 11 events |

## 6. Evaluation
| # | Assumption | Status | Note |
|---|---|---|---|
| E1 | A timeline event counts as recalled if an event of the same type was extracted within ±1 h | Choice | |
| E2 | The UT Austin answer key is correct | Assumed | Not yet hand-checked |
| E3 | Hand-check rows marked "check" were settled by reading the source | Choice | Treating them all as wrong instead gives scope 10% and all-three precision 28% |

## 7. Scope decisions (what we're deliberately not doing yet)
- **Charts:** skipped for now (2026-11-15). Possible approach later: read charts with Claude, one skill run per figure.
- **Tables:** pdfplumber works on FERC's ruled tables, but nothing is wired in. Textract was the earlier plan.
- **Core idea first:** test trust, scope and comparability on clean inputs before tuning parsing.
- **`LMP.pkl`:** not loaded (it's a pickle).

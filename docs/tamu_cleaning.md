# Cleaning log: TAMU 2021TXPowerOutage
Updated 2026-11-15. Script: `scripts/clean_tamu.py` (`uv run python scripts/clean_tamu.py`, ~10 s).
The TAMU folder (`2021TXPowerOutage/`) is not modified. Cleaned copies are in `data/external/tamu/`, and every
number below is in `data/external/tamu/cleaning_stats.json`.

**Conventions in every cleaned file**
- `time_central`: ISO 8601 with offset, America/Chicago. All of Feb 2021 is CST (`-06:00`); DST began 3/14.
- Hourly rows are labelled by the **start** of the hour, matching `dataset_survey.md`. EIA's "hour ending" labels
  are moved back 1 h.
- `timestamp_raw`: the source's own label, kept for tracing.

## Fix 1: the 2016 dates
| File | Raw label | Cleaned | Evidence |
|---|---|---|---|
| `BES_demand_Feb2021_v20210224.csv` → `bes_profiles/bes_demand_feb2021.csv` | `2/1/2016 0:00` … `2/18/2016 23:00` | `2021-01-30T23:00-06:00` … `2021-02-17T22:00` | All 432 rows exactly match an EIA ERCOT subregion row (same 8 zone values, reordered), all with the same offset: label = true − 1826 d + 1 h |
| `PG.csv`, `PF.csv` (BES model outputs) → `bes_outputs/pg.csv`, `pf.csv` | `2016-01-01 00:00` … `2016-01-18 23:00`, column headed "UTC" | `time_central` = TAMU simulation clock, `2021-02-01T00:00-06:00` + row; plus `demand_hour_central` | The label is a placeholder row index, not UTC. Simulation row *i* uses profile row *i* (PG sums to the demand profile in every row, ratio 1.000000). `process_event.m` puts 2/14 00:00 CST at row 313, so row 0 is 2/1 00:00 CST |
| `BES_solar_Feb2016_v20210224.csv`, `BES_hydro_Feb2016_v20210224.csv` → `bes_profiles/bes_{solar,hydro}_feb2016_profile.csv` | `2/1/2016 0:00` … | year set to 2021 (`2021-02-01T00:00-06:00` …), i.e. the simulation clock | See caveat below |

Caveats:
- **TAMU's model ran with demand 25 h early.** Its clock starts at 2/1 00:00 CST, but the demand profile's first
  row is the EIA hour starting 1/30 23:00 CST. TAMU appear to have read EIA's "1/31/2021 12 a.m." (hour ending
  midnight at the *start* of 1/31) as 2/1 00:00. So simulated dispatch at "2/15 19:00" used actual demand from
  2/14 18:00. Changing the year alone would have kept that 25 h error in the demand file. `pg.csv`/`pf.csv` keep
  both clocks: `time_central` is when TAMU meant each row to be, and `demand_hour_central` is the demand it actually used.
- **Solar and hydro look like real 2016 weather-year stand-ins, not mislabelled 2021 data.** The file names say
  Feb2016 (demand and wind say Feb2021), and they track EIA's 2021 ERCOT data poorly from day to day (solar daily
  totals r = 0.47; hydro r = −0.3 to 0.17). Solar's hour of day does fit CST: hourly r = 0.84 at a +1 h offset from the
  demand clock, which is 0 h from its own label. The cleaned files give the hour each row was **used** in the
  simulation. They aren't a record of Feb 2021 weather.
- `code/PG.csv` and `code/PF.csv` are byte-identical to the BES model-output copies, so I didn't clean them separately.
- `LMP.pkl` was skipped. It exists only as a pickle (loading one runs code) and has no CSV copy. It's on the same
  432-row clock as PG/PF.

## Fix 2: every timestamp to Central time
How I checked each source's clock:
- **EIA (hour ending CST) is correct as labelled.** EIA ERCOT demand vs ERCOT's own "estimated load without
  shed" minus minute load shed (Central), first two hours of 2/15: 65,255 vs 65,268 MWh and 63,395 vs 63,218 MWh at lag 0.
- **The EIA API file (`All_available_load_from_EIA_API.csv`) is hour-ending local Central, not UTC.** Its values match the
  CSV export exactly (433 of 433) after a 1 h shift. Its only duplicate labels are 1 a.m. on 11/3/2019 and 11/1/2020,
  the DST fall-back hours. I converted it with America/Chicago, so rows from summer 2019 and 2020 are CDT (`-05:00`).
- **poweroutage.us `RecordedDateTime` is UTC**, so the "UTC" header on the `cty_*` files is right. Statewide customers out
  first passes 500k at 08:20 by the raw label. ERCOT's 9.5 GW shed jump was at 01:59 CST (07:59 UTC).
- **The unit-outage hourly matrices are headed "UTC" but are already Central.** `process_table.py` fills them from
  ERCOT's START/END (Central) with no conversion. The first nonzero NG hour is labelled 2/14 00:00, the same as the
  earliest NG START (2/14 00:00 CST). Relabelled with no shift.
- **`load_shed_processed.csv` and `capacity_out_processed.csv` are Central.** Error against ERCOT's minute data
  (load shed) and board-deck data (NG MW out) is lowest at 0 or +10 min. It's about 400 MW at ±60 min.
- **The BES wind profiles are headed UTC, and I converted them as UTC.** Fit to EIA ERCOT wind is nearly the same at
  −6 h (r = 0.789) and −7 h (0.795). It's modelled counterfactual wind, so the fit can't tell those apart, and I trusted
  the label. Note: TAMU used wind row *i* at simulation hour *i* (2/1 00:00 CST + *i*), but row *i* is
  2/1 00:00 **UTC** + *i*, so wind in their model is 6 h late.

| Source | Clock found | Change | Output |
|---|---|---|---|
| 10 EIA CSV/JSON exports (ERCOT, MISO, SWPP) | hour ending CST | −1 h → hour start | `eia/*.csv` |
| EIA API file | hour ending, local Central | −1 h, DST-aware | `eia/ercot_demand_by_subregion_api_2019_2021.csv` |
| BES wind, 4 versions | UTC | −6 h | `bes_profiles/bes_wind_feb2021_v*.csv` |
| `txraw.csv`, `raw.csv` (all states) | UTC | −6 h | `load_outage/txraw.csv`, `raw_all_states.csv` |
| `cty_outage`, `cty_total`, `cty_out_pct`, `cty_out_dist`, `cty_outage_all`, `cty_total_all` | UTC | −6 h (now start 2/11 18:00) | `load_outage/*.csv` |
| `coal_outage`, `ng_outage`, `pv_outage`, `ng_derate_fac` | Central (header wrong) | relabel only | `unit_outage/*.csv` |
| `unit_outage.xlsx` START/END | Central | parse `2/14/21 12:00 AM` format | `unit_outage/unit_outage_20210304.csv` (`start_central`, `end_central`) |
| `load_shed_processed`, `capacity_out_processed` (10-min) | Central | relabel | `ercot_derived/*.csv` |
| `ercot_capacity.csv` | Central; **only row 1 has a time** | rebuilt hourly from 2/12 00:00 (167 rows filled in). `process_event.m` puts its 168 rows at simulation rows 265–432, which is 2/12 00:00–2/18 23:00 | `ercot_derived/ercot_capacity.csv` |
| `ercot_load_forecast.csv` | DeliveryDate + HourEnding 1:00–24:00, DSTFlag all N | date + (HE − 1) h | `ercot_derived/ercot_load_forecast.csv` |
| `load_shed_simulated.xlsx` | EIA-style hour ending CST | −1 h | `ercot_derived/load_shed_simulated.csv` |

Not converted:
- `capacity_out_copied.xlsx` has one timestamp (`2/12 00：00`, with a full-width colon) for 48 rows. The step is
  unknown and no TAMU code reads the file.
- `2021TXPowerOutage/data/BES_wind_20210224.csv` is byte-identical to `BES_wind_Feb2021_v20210224.csv`.
- The `.mat` files are MATLAB inputs.
- The two empty EIA files (MISO demand, SWPP interchange) have no data.

## Fix 3: the 13 numeric county names
All 13 come from one batch: **Southwest Texas Electric Cooperative**, 2021-02-22 12:29 UTC, `CustomersOut` = 0,
no `CountyFIPS`. I mapped each one by finding the county that the same utility reports *by name* with the same
customers-tracked count (within 1%). Where that wasn't possible, I used Texas's alphabetical county index
(FIPS = 48000 + 2 × code − 1).

| Code | County | FIPS | Tracked (numeric row / named row) | How |
|---|---|---|---|---|
| 48 | Concho | 48095 | 4 / 4 | count match |
| 53 | Crockett | 48105 | 2,665 / 2,665 | count match |
| 70 | **Edwards** | 48137 | 7 / 7 | count match (alphabetical index says Ellis) |
| 119 | **Irion** | 48235 | 431 / 431 | count match (alphabetical index says Jack) |
| 164 | Menard | 48327 | 725 / 726 | count match |
| 186 | Pecos | 48371 | 586 / 586 | count match |
| 192 | Reagan | 48383 | 1,263 / 1,267 | count match |
| 207 | Schleicher | 48413 | 2,235 / 2,236 | count match |
| 218 | Sutton | 48435 | 1,356 / 1,356 | count match |
| 222 | Terrell | 48443 | 9 / – | **alphabetical index only.** This utility never names Terrell, but Terrell borders its Val Verde, Crockett and Pecos service area |
| 226 | Tom Green | 48451 | 210 / 210 | count match |
| 231 | Upton | 48461 | 88 / 88 | count match |
| 233 | Val Verde | 48465 | 271 / 271 | count match |

The codes follow the alphabetical index for 11 of 13. Edwards and Irion are each one off, so an index-only mapping
would have gotten them wrong. That's why the count match comes first.

Where the fix applies:
- `txraw.csv` and `raw_all_states.csv`: `CountyName` and `CountyFIPS` are filled in for the 13 rows.
- `cty_outage_all`, `cty_total_all`, `avg_out_and_ttl`: each of these has a column for the numeric name *and* the
  real name. I dropped the 12 numeric columns that are the same customers as the named column. Their out values were
  all 0, and their tracked totals are already counted in the named column. I **added** 222 into `Terrell`, since
  those 9 customers belong to a different utility than the existing Terrell column.
- Not fixed: `raw.csv` has 114 rows in other states with numeric county names. Those are outside Texas.

## Fix 4: cap percent-out at 100%
`load_outage/cty_out_pct.csv`: **46 cells** were above 1.0 (the values are fractions, so 1.0 = 100%). The largest was
57.07. I clipped them to 1.0. None of the 46 come from dividing by zero: the county's tracked count was nonzero in every
case. They come from the utility reporting more customers out than it tracks.
By county: Runnels 13, Brazoria 8, Mitchell 6, Kinney 5, Waller 5, Austin 2, Matagorda 2, and one each for Bandera,
Colorado, Galveston, Liberty and Wharton.

Related, flagged but not changed:
- `txraw.csv` has **317 rows** where `CustomersOut > CustomersTracked` (403 in the all-states file). These now carry
  `out_exceeds_tracked = True`, and the counts are left as reported.
- The first 48 rows of `cty_out_pct` and `cty_out_dist` (2/11 18:00 to 2/13 17:00 Central) are zero because TAMU's
  loop starts at hour 48. They're **missing, not zero**.

## Fix 5: drop the 135 blank outage rows
`unit_outage.xlsx` (TAMU's copy of ERCOT's **3/4** unit outage list) → `unit_outage/unit_outage_20210304.csv`:
1,796 rows in, **135 dropped**, 1,661 out. I also dropped the empty `Unnamed: 4` column.
- The 135 rows aren't fully blank. Each one has START and END, and every other column is empty: no unit, MW, fuel
  or county. ERCOT's original `Unit_Outage_Data_20210304__Public_.xlsx` has the same 135 rows.
- They aren't continuation rows: the sheet has no merged cells, rows are sorted by START, and no blank row's times
  duplicate a named row.
- **They look like redacted entries.** Every one of their START/END pairs appears as a named unit in the **3/12**
  version. The match isn't unique for 32 pairs, but the candidates are mostly wind and solar (for example MIRASOLE,
  MIAM1, OVEJA, MARIAH) and some NG. TAMU's `process_table.py` also dropped them, using `dropna()`.
- **This changes how the 3/4 and 3/12 versions compare.** Some of what looks like rows "added" in 3/12 are these
  redacted rows getting identified.

## Not touched (from the survey, outside these five fixes)
- Source file-name typos ("2021-02-01 to **2020**-02-19"). The data inside covers 2021. The original names are in the
  `source` fields of `cleaning_stats.json`.
- SWPP's fuel totals don't add up.
- 1,951 `txraw` rows have no `CountyFIPS`.
- TAMU used the 3/4 outage list, not the 3/12 one.

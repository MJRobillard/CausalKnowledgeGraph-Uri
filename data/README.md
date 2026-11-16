# Data

The MIT License in `LICENSE` covers this repository's code. Third-party material here is not covered by it unless
its own terms allow; the cleaned TAMU data in `external/tamu/` are redistributed under TAMU's MIT License (Copyright (c)
Wu, Zheng, Xu, Olsen, Xia, Singh and Xie) with attribution.

Every input to the pipeline, where it came from, and whether it is in git. Copyright in each source stays with its
publisher. Public reports and datasets are included unmodified so the results can be reproduced. Copyrighted
third-party documents (news articles, the Enverus and UT Austin reports) are **not** in git; their citations are below
and `scripts/fetch_sources.py` downloads them and checks each one against the sha256 in `config/documents.yaml`.

| Folder | What | Built by | In git |
|---|---|---|---|
| `sources/` | Documents in the registry (`config/documents.yaml`; `file:` paths are relative to this folder): reports, ERCOT data releases, news | downloaded | yes, except `news/` and `other/` |
| `external/tamu/` | Cleaned copy of the TAMU 2021TXPowerOutage data | `scripts/clean_tamu.py` | yes (except one 56 MB file) |
| `external/noaa/` | NOAA temperatures: hourly by ERCOT weather zone, daily, station list by county | downloaded; `scripts/extend_zone_temps.py` | yes |
| `external/eia_gas/` | EIA marketed gas production (Texas, U.S., monthly) and Henry Hub spot price (daily); survey only | downloaded | yes |
| `raw/` | Public downloads for the fleet and spatial layer; `manifest.json` records URL, time, size and sha256 | `scripts/fetch_spatial.py` | manifest only |
| `processed/fleet/` | EIA-860 plants matched to ERCOT's unit outage list | `kg/fleet.py` | yes |
| `processed/spatial/` | GeoJSON layers, per-plant spatial features, daily cold at each plant | `kg/spatial.py` | yes |
| `processed/chart_reads/` | FERC Figure 35 (daily gas production) read off the chart | `scripts/digitize_fig35.py` | yes |
| `answer_key/` | Hand labels: timed events from the UT Austin report (held out from extraction), gold scope labels | by hand | yes |

## Get the data that is not in git

```bash
uv run python scripts/fetch_sources.py     # news articles, Enverus and UT Austin reports (checked by sha256)
uv run python scripts/fetch_spatial.py     # EIA-860, Census counties, GHCN-Daily, ArcGIS layers (checked by sha256)
git clone https://github.com/tamu-engineering-research/2021TXPowerOutage   # grid model; run in the repo root
```

The results in `results/` were built from TAMU commit `71775902f3f851242d221b1c2ea825d8e29cab4c` (2022-03-24). On
2026-11-16 all seven third-party documents downloaded from the URLs below matched the published copies byte for byte.

## Sources

### Reports (`sources/federal/`, `sources/ercot/`, `sources/other/`)

| id | Citation | In git |
|---|---|---|
| `ferc_final_2021_11` | FERC, NERC and Regional Entity Staff (2021-11-16). *The February 2021 Cold Weather Outages in Texas and the South Central United States.* [PDF](https://www.nerc.com/pa/rrm/ea/Documents/February_2021_Cold_Weather_Report.pdf) | yes |
| `ferc_prelim_2021_09` | FERC, NERC and Regional Entity Staff (2021-09-23). *February 2021 Cold Weather Grid Operations: Preliminary Findings and Recommendations* (presentation). [PDF](https://www.ferc.gov/sites/default/files/2021-09/February%202021%20Cold%20Weather%20Grid%20Operations_Preliminary%20Findings%20and%20Recommendations-09-23-21_Full%20Presentation.pdf) | yes |
| `ferc_rec_status_2022_12` | FERC (2022-12). *The February 2021 Cold Weather Outages in Texas and the South Central United States: Tracking of Responses to Recommendations.* [PDF](https://www.ferc.gov/sites/default/files/2022-12/A-3%20-%20The%20February%202021%20Cold%20Weather%20Outages%20in%20Texas%20and%20the%20South%20Central%20United%20States.pdf) | yes |
| `ercot_board_2021_02_24` | Magness, B., ERCOT (2021-02-24). *Review of February 2021 Extreme Cold Weather Event: ERCOT Presentation* (Urgent Board of Directors Meeting, revised). [PDF](https://www.ercot.com/files/docs/2021/02/24/2.2_REVISED_ERCOT_Presentation.pdf) | yes |
| `ercot_causes_2021_04_06` | ERCOT (2021-04-06). *Letter re: Preliminary Report on Outage Causes* (PUCT project 51878). [PDF](https://www.ercot.com/files/docs/2021/04/06/51878_ERCOT_Letter_re_Preliminary_Report_on_Outage_Causes.pdf) | yes |
| `ercot_causes_2021_04_27` | ERCOT (2021-04-27). *Update to April 6, 2021 Preliminary Report on Causes of Generator Outages and Derates.* [PDF](https://www.ercot.com/files/docs/2021/04/28/ERCOT_Winter_Storm_Generator_Outages_By_Cause_Updated_Report_4.27.21.pdf) | yes |
| `ercot_qa` | ERCOT (2021-04-27). *ERCOT Responses to Questions and Comments Concerning ERCOT's April 6, 2021 Preliminary Report on Causes of Outages and Derates.* [PDF](https://www.ercot.com/files/docs/2021/04/28/ERCOT_Responses_to_Preliminary_Outage_Report_Questions.pdf) | yes |
| `puct_51878_26` | PUCT project 51878, item 26 (scanned copy of the 4/27 report; not extracted). [PDF](https://interchange.puc.texas.gov/Documents/51878_26_1125764.PDF) | yes |
| `ercot_gen_list_2021_02_14` | ERCOT (2021-02-15). *Resource list, 2/14/21 21:00.* [PDF](https://www.ercot.com/files/docs/2021/02/15/Resource_list_021421_2100.pdf) | yes |
| `ercot_eea_levels` | ERCOT (2020-09-04). *Energy Emergency Alert one-pager.* [PDF](https://www.ercot.com/files/docs/2021/03/03/EEA_OnePager_updated_9-4-20.pdf) | yes |
| `ut_austin_timeline_2021_07` | A committee of faculty and staff at The University of Texas at Austin (2021-07). *The Timeline and Events of the February 2021 Texas Electric Grid Blackouts.* [PDF](https://energy.utexas.edu/sites/default/files/UTAustin%20(2021)%20EventsFebruary2021TexasBlackout%2020210714.pdf). Held out as an answer key | no |
| `enverus_txoga_2021_04` | Enverus, for the Texas Oil and Gas Association (2021-04-22). *Winter Storm Uri: Natural Gas Analysis.* [PDF](https://www.txoga.org/wp-content/uploads/2021/12/2644-4-22-21-enverus_txoga_winter-storm-uri-natural-gas-analysis.pdf) | no |

`sources/ercot/` also holds two EEA one-pagers and ERCOT's CP MMARS true-up workbook, used only in the dataset survey
(`docs/dataset_survey.md`).

### News (`sources/news/`, Tier 3; not in git)

| id | Citation |
|---|---|
| `news_ap_2021_02_15` | Associated Press (2021-02-15). "Power cut across Texas as snow, ice blanket southern Plains." KRGV. <https://www.krgv.com/news/power-cut-across-texas-as-snow-ice-blanket-southern-plains/> |
| `news_txtrib_ksat_2021_02_15` | The Texas Tribune (2021-02-15). "Rolling power blackouts in effect across Texas as massive winter storm drives demand for electricity." Republished by KSAT. <https://www.ksat.com/news/texas/2021/02/15/rolling-power-blackouts-in-effect-across-texas-as-massive-winter-storm-drives-demand-for-electricity/> |
| `news_abc13_2021_02_15` | ABC13 Houston / KTRK (2021-02-15). "Massive power failure leaves Houston-area residents frustrated." <https://abc13.com/post/power-failure-electricity-widespread-outage-ercot/10341469/> |
| `news_aljazeera_2021_02_15` | Al Jazeera (2021-02-15). "Millions in Texas lose power as winter storm drops temps to -22C." <https://www.aljazeera.com/news/2021/2/15/millions-in-texas-lose-power-as-winter-storm-drops-temps-to-23c> |
| `news_ap_pbs_2021_02_16` | Weber, P. J., Associated Press (2021-02-16). "Anger grows over Texas outages a full day after historic snowfall." PBS NewsHour. <https://www.pbs.org/newshour/nation/anger-grows-over-texas-outages-a-full-day-after-historic-snowfall> |

The extractions in `results/` keep only the short quotes they cite. In `results/01_extraction/extractions.jsonl` the
chunk text of these documents is replaced by its sha256 and rebuilt from the local copy when needed
(`kg/chunk.py: load_extractions`).

### Measured data (`sources/ercot/data/`, `external/`)

| Data | Citation | Terms |
|---|---|---|
| ERCOT MW out by fuel and frequency | ERCOT (2021). *Data for graphs in board deck: generation capacity out by fuel type, and frequency.* [XLSX](https://www.ercot.com/files/docs/2021/03/24/Data_for_Graphs_in_Board_Deck_-_Generation_Capacity_Out_by_Fuel_Type_Chart_and_Frequency_Chart.xlsx) | ERCOT public release |
| ERCOT available generation and load shed | ERCOT (2021-03). *Available Generation and Estimated Load without Load Shed.* [XLSX](https://www.ercot.com/files/docs/2021/03/18/Available_Generation_and_Estimated_Load_without_Load_Shed_Data.xlsx) | ERCOT public release |
| ERCOT unit outage lists | ERCOT (2021-03-04, 2021-03-12). *Unit Outage Data.* [3/4 XLSX](https://www.ercot.com/files/docs/2021/03/04/Unit_Outage_Data_20210304__Public_.xlsx), [3/12 XLSX](https://www.ercot.com/files/docs/2021/03/12/Unit_Outage_Data_20210312.xlsx) | ERCOT public release |
| TAMU data and synthetic grid (`external/tamu/`) | Wu, D., Zheng, X., Xu, Y., Olsen, D., Xia, B., Singh, C., & Xie, L. (2021). An open-source extendable model and corrective measure assessment of the 2021 Texas power outage. *Advances in Applied Energy*, 4, 100056. <https://doi.org/10.1016/j.adapen.2021.100056>. Data: <https://github.com/tamu-engineering-research/2021TXPowerOutage> | MIT (as stated in the TAMU README); cleaning log in `docs/tamu_cleaning.md` |
| EIA hourly demand and generation (via TAMU) | U.S. Energy Information Administration, Hourly Electric Grid Monitor | U.S. government work |
| NOAA hourly temperatures | NOAA NCEI, Global Hourly (Integrated Surface Database), one airport station per ERCOT weather zone, Feb 1–26, 2021 | U.S. government work |
| NOAA daily temperatures | NOAA NCEI, GHCN-Daily (Menne et al., 2012, doi:10.7289/V5D21VHZ) | U.S. government work |
| NOAA stations by county | NOAA NCEI, Climate Data Online web services v2, GHCND stations | U.S. government work |
| EIA gas production and prices | EIA series N9050TX2, N9050US2 (marketed production, MMcf, monthly) and RNGWHHD (Henry Hub spot, $/MMBtu, daily) | U.S. government work |

### Downloads for the fleet and spatial layer (`raw/`, see `raw/manifest.json`)

| Data | Source |
|---|---|
| EIA-860 2020 (generators, plant coordinates) | <https://www.eia.gov/electricity/data/eia860/> |
| Census cartographic boundary counties 2020, 1:5m | <https://www2.census.gov/geo/tiger/GENZ2020/shp/> |
| GHCN-Daily station list and Feb 2021 TMIN/TMAX | NOAA NCEI |
| Sedimentary basins, shale plays, gas processing plants, gas pipelines, power plants, transmission lines ≥ 230 kV | EIA and HIFLD layers on ArcGIS Online (URLs in the manifest) |
| ERCOT weather zones and load zones | Third-party ArcGIS user layers (weaker provenance; see `docs/ASSUMPTIONS.md`) |

Live layers change. `scripts/fetch_spatial.py` warns when a download differs from the checksum in the manifest.

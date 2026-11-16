# Step 1: weather-free baselines for the cold → outage models

The fail-restart model (M3) can fit almost any single rise and fall, and the +5-day placebo used warm days, so it was bound to fail. Here M3 is refitted with the cold driver max(0, 32 − T) replaced by two weather-free drivers of the same scale: a **tent** (rises from Feb 14 00:00 to a fitted peak hour, falls to zero at a fitted end hour) and **hours** since Feb 14 00:00. Train Feb 14 00:00 – 17 23:00, test Feb 18 00:00 – 19 23:00 (ERCOT data ends 2/20 00:00). `total` is all fuels, for the fuel-less WeatherCondition → GenerationOutage family.

**Rule (H2):** the weather → outage edge stays above G1 only if M3 with temperature passes its placebo **and** each weather-free M3 has test RMSE ≥ **1.25×** the temperature version (temperature at least 20% better). 1.25 is the ratio the placebo rule already uses (C-M3); with 48 autocorrelated test hours a smaller gap is not a clear win.

## Out-of-sample RMSE (MW)

| Target | M1 instantaneous | M2 cumulative | M3 temperature | M3 tent | M3 hours | Margin tent / hours | Placebo −5 / +5 | H2 |
|---|---|---|---|---|---|---|---|---|
| gas | 2,038 | 9,411 | 1,164 | 1,287 | 13,454 | 1.11 / 11.55 | 3.90 / 17.44 | ❌ fail |
| coal | 646 | 2,377 | 308 | 1,811 | 3,435 | 5.88 / 11.16 | 4.98 / 19.39 | ✅ pass |
| wind | 3,397 | 5,267 | 2,367 | 2,779 | 7,778 | 1.17 / 3.29 | 1.77 / 3.56 | ❌ fail |
| solar | 190 | 459 | 211 | 482 | 912 | 2.29 / 4.32 | 1.17 / 4.50 | ❌ fail |
| total | 6,610 | 19,651 | 4,106 | 4,663 | 29,929 | 1.14 / 7.29 | 2.68 / 9.48 | ❌ fail |

Fitted tents (training data only, no weather input):

- gas: peak 02-15 12:00, end 02-20 16:00; training RMSE 0.78× the temperature model's
- coal: peak 02-16 04:00, end 02-18 16:00; training RMSE 1.11× the temperature model's
- wind: peak 02-15 04:00, end 02-15 16:00; training RMSE 0.64× the temperature model's
- solar: peak 02-15 04:00, end 02-18 16:00; training RMSE 0.84× the temperature model's
- total: peak 02-15 08:00, end 02-19 20:00; training RMSE 0.72× the temperature model's

## Grades before and after

| Class edge | 2026-11-15 | 2026-11-16 |
|---|---|---|
| WeatherCondition → GenerationOutage:gas | G3 | **G1** |
| WeatherCondition → GenerationOutage:solar | — | **G0** |
| WeatherCondition → GenerationOutage:wind | G3 | **G1** |

Independence for G3 (H4): a second passed test on a different data source, or an asserted Tier 1–2 text claim from a document that wasn't used to build the test's inputs. The ERCOT board deck published the MW-out and frequency data, so it doesn't count (test input documents: ercot_board_2021_02_24).

Charts: `cold_baselines_gas.png`, `cold_baselines_coal.png`, `cold_baselines_wind.png`, `cold_baselines_solar.png`, `cold_baselines_total.png`

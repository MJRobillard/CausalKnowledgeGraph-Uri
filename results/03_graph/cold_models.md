# Cold → generation outages: three models per fuel

Run `03_graph`, step 4 (`analysis/cold_models.py`). Hourly ERCOT MW out by fuel (minute data, hour-start means), Feb 14 00:00 – Feb 19 23:00 Central. **Train** Feb 14 00:00 – Feb 17 23:00 (96 h), **test** Feb 18 00:00 – Feb 19 23:00 (48 h, the thaw; ERCOT's data ends 2/20 00:00, so not through Feb 20).

Temperature for each fuel is the 9 zone stations weighted by that fuel's capacity per zone: each county in the 3/12 unit list goes to its nearest station (from its CDO station coordinates), weighted by the HSL of the fuel's units there. FDH(t) = Σ max(0, 32 − T) from Feb 10 00:00, in °F·h. C = HSL of the fuel's distinct units in the 3/12 list. Model 3 is free-run from the observed value at Feb 14 00:00; no observed value is used after that.

## Results

| Fuel | Model | RMSE train (MW) | R² train | RMSE test (MW) | R² test | Placebo −5 d / +5 d (test RMSE ÷ real) |
|---|---|---|---|---|---|---|
| gas | M1 instantaneous | 5,543 | 0.04 | 2,038 | -0.19 | 3.87 / 4.58 |
| gas | M2 cumulative | 3,498 | 0.62 | 9,411 | -24.38 | 0.59 / 1.88 ❌ |
| gas | **M3 fail-restart** | 2,021 | 0.87 | 1,164 | 0.61 | 3.90 / 17.44 ✅ |
| coal | M1 instantaneous | 1,218 | 0.13 | 646 | -8.39 | 3.21 / 3.86 |
| coal | M2 cumulative | 864 | 0.56 | 2,377 | -125.96 | 0.73 / 1.12 ❌ |
| coal | **M3 fail-restart** | 380 | 0.92 | 308 | -1.13 | 4.98 / 19.39 ✅ |
| wind | M1 instantaneous | 1,243 | 0.11 | 3,397 | -1.62 | 1.38 / 1.49 |
| wind | M2 cumulative | 1,103 | 0.30 | 5,267 | -5.31 | 0.80 / 0.79 ❌ |
| wind | **M3 fail-restart** | 612 | 0.78 | 2,367 | -0.27 | 1.77 / 3.56 ✅ |
| solar | **M1 instantaneous** | 258 | 0.19 | 190 | -0.31 | 2.05 / 2.32 |
| solar | M2 cumulative | 275 | 0.07 | 459 | -6.63 | 0.86 / 0.42 ❌ |
| solar | M3 fail-restart | 132 | 0.79 | 211 | -0.62 | 1.17 / 4.50 ❌ |

Bold = lowest test RMSE for the fuel. Placebo ✅ = both shifted fits have test RMSE ≥ 1.25× the real one (checked for models 2 and 3 only). R² test is against the test-period mean, so it can be negative.

## Fitted fail-restart parameters

| Fuel | α (per °F below 32 per h) | ρ (per h) | Restart half-life (h) | C (MW) | Capacity share with a station |
|---|---|---|---|---|---|
| gas | 1.70e-03 | 0.0125 | 55 | 41,333 | 96% |
| coal | 2.01e-03 | 0.0191 | 36 | 9,201 | 82% |
| wind | 1.03e-03 | 0.0100 | 69 | 28,778 | 78% |
| solar | 1.07e-03 | 0.0424 | 16 | 3,101 | 95% |

α is the hourly fraction of still-available capacity that fails per °F below freezing; ρ the hourly fraction of out capacity that returns.

## Placebo fits (test RMSE, MW)

| Fuel | Model | Real | −5 days | +5 days |
|---|---|---|---|---|
| gas | M1 instantaneous | 2,038 | 7,893 | 9,338 |
| gas | M2 cumulative | 9,411 | 5,550 | 17,697 |
| gas | M3 fail-restart | 1,164 | 4,547 | 20,311 |
| coal | M1 instantaneous | 646 | 2,078 | 2,497 |
| coal | M2 cumulative | 2,377 | 1,730 | 2,665 |
| coal | M3 fail-restart | 308 | 1,532 | 5,972 |
| wind | M1 instantaneous | 3,397 | 4,678 | 5,059 |
| wind | M2 cumulative | 5,267 | 4,232 | 4,180 |
| wind | M3 fail-restart | 2,367 | 4,196 | 8,419 |
| solar | M1 instantaneous | 190 | 390 | 442 |
| solar | M2 cumulative | 459 | 394 | 191 |
| solar | M3 fail-restart | 211 | 248 | 950 |

## Written back to the graph

- `WeatherCondition → GenerationOutage:gas`: M3 fail-restart beats M1 out of sample (1,164 vs 2,038 MW) and passes the placebo (ratios 3.90 / 17.44). Test recorded on the class edge; grade set by `kg.invariants`.
- `WeatherCondition → GenerationOutage:coal`: M3 fail-restart beats M1 out of sample (308 vs 646 MW) and passes the placebo (ratios 4.98 / 19.39). Test recorded on the class edge; grade set by `kg.invariants`.
- `WeatherCondition → GenerationOutage:wind`: M3 fail-restart beats M1 out of sample (2,367 vs 3,397 MW) and passes the placebo (ratios 1.77 / 3.56). Test recorded on the class edge; grade set by `kg.invariants`.

Charts: `cold_gas.png`, `cold_coal.png`, `cold_wind.png`, `cold_solar.png`

Nuclear (footnote, n = 1): one unit (South Texas Project, 1,353 MW) was out from the hour 02/15 05:00 to 02/17 21:00 (65 h). The nuclear-weighted temperature at the trip hour was 18.0 °F. One event can't support a dose-response fit, so no model is fitted.

## Expected vs what happened

- **Model 3 wins out of sample** for gas, coal, wind; for solar the best is M1 instantaneous. Only gas has a positive test R² (0.61); for the other fuels every model does worse than the test-period mean, because the level falls during Feb 18–19 and none of the models falls fast enough.
- **Wind and solar do not have the highest α.** Ranked by α: coal 2.0e-03, gas 1.7e-03, solar 1.1e-03, wind 1.0e-03. Coal and gas fail faster per degree of cold than wind in this fit.
- **Gas is explained worst by instantaneous local temperature** (M1 train R²: gas 0.04, coal 0.13, wind 0.11, solar 0.19), as expected, **but best by the fail-restart model**. So local cold plus slow restarts accounts for most of the gas trajectory at system level. That does not rule out a fuel-supply cause: the gas fields froze in the same cold, so supply losses and plant freezing share one driver and this test can't separate them.
- **The cumulative model (M2) fails every placebo** and extrapolates badly: FDH only rises, so M2 can't express restarts, and any cold spell (Feb 9–14 for the −5-day shift) builds a similar ramp.
- **The +5-day placebo is the weaker test**: shifting by +5 days brings in the warm days of Feb 19–24, so the cold term is mostly zero and model 3 can only decay. The −5-day shift (Feb 9–14, also cold) is the stricter one; model 3's ratios there are gas 3.90, coal 4.98, wind 1.77, solar 1.17.
- **Wind's sharp return on Feb 19** (13.6 GW out at 09:00, 8.7 GW at 23:00, after the wind-weighted temperature crossed 32 °F around 10:00) is faster than any constant ρ allows; a restart rate that depends on temperature would be the next model to try.

## Limits

- **One event.** Four days of training and two of testing; the fits describe this storm, not a general law.
- **System-level data, by fuel, not by unit.** Each fuel is one series; the temperature is a capacity-weighted mix of 9 airport stations, and 18 of 113 counties (about 16% of MW lost) have no station coordinates and drop out of the weights.
- **MW out mixes causes.** Gas outages include fuel-supply losses far from the plant, pre-existing and planned outages; model 3 has no baseline term, so it must decay pre-storm outages as if they were cold-caused.
- **Dose-response fit, not a randomized comparison.** A good fit plus a failed placebo for a shifted series is evidence of a real temperature–outage relationship in this event, not proof of mechanism.
- Placebo +5 d uses NOAA hours Feb 22–26, added 2026-11-15 with the same cleaning rule (`scripts/extend_zone_temps.py`).

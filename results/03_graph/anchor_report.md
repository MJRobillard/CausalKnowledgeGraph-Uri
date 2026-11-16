# Tier 0 anchoring report

Run `03_graph`. 2,867 items checked (1,919 quant claims, 948 events). **29 anchored (1.0%)**: 24 agree, **5 disagree**. Everything else has no Tier 0 counterpart, which is expected: most claims are about other regions, other quantities (causes, counts, prices, gas volumes) or have no usable time.

Rules: `config/anchor_rules.yaml`, assumptions A1–A10 in `ASSUMPTIONS.md`.

## Coverage by tier

| Tier | Items | Anchored | Agree | Disagree | Coverage |
|---|---|---|---|---|---|
| 1 | 267 | 3 | 1 | 2 | 1.1% |
| 2 | 2,457 | 22 | 20 | 2 | 0.9% |
| 3 | 143 | 4 | 3 | 1 | 2.8% |

## Why items were not anchorable

| Reason | Quant claims | Events |
|---|---|---|
| metric_mismatch | 320 | 0 |
| no_series | 1,046 | 880 |
| region | 435 | 31 |
| time_unresolved | 96 | 30 |

Most common details:

- 1,015 × no series for metric '…'
- 393 × region '…' is not ERCOT
- 210 × no series for event type GenerationOutage without a named unit
- 207 × no series for event type InstitutionalAction
- 133 × no series for event type GridEmergency
- 105 × no series for event type WeatherCondition
- 101 × MW quantity not identified as shed, demand or outage
- 89 × temperature in '…'
- 85 × no series for event type GasSupplyDisruption
- 65 × time source none
- 64 × region '…'
- 58 × not a measured level ('…')

## Anchored claims by series

| Series | Agree | Disagree |
|---|---|---|
| `eia_ercot_hourly:demand` | 6 | 0 |
| `ercot_frequency:frequency` | 1 | 0 |
| `ercot_gen_load_shed:load_shed` | 12 | 1 |
| `ercot_mw_out_by_fuel:total` | 2 | 2 |
| `noaa_ghcnd_daily:TMIN` | 3 | 2 |

## Every mismatch (5)

Residual = claim − observed, in series units (MW, °F, Hz; minutes for event times). `~` = approximate (5% tolerance); `TX` = scoped to Texas, read as ERCOT; `dflt` = document-default window (extremes only).

| Doc | Tier | Claim | Series | Mode | Observed | Residual | Tol. | Flags | Quote |
|---|---|---|---|---|---|---|---|---|---|
| ercot_board_2021_02_24 | 1 | 52,277 MW | `total` | max | 50,641.000 | 1,636.000 | 522.770 |  | loss of generation was 52,277 MW (approximately 48.6%) at the highest point. |
| ercot_board_2021_02_24 | 1 | 20,000 MW | `load_shed` | max | 15,500.000 | 4,500.000 | 200.000 |  | 20,000 MW peak load shed. |
| ferc_final_2021_11 | 2 | 8 degF | `TMIN` | min | -2.000 | 10.000 | 0.500 | dflt | Temperatures dropped as low as six degrees in Austin, eight degrees in Dallas and ten degrees in Houston. |
| ferc_final_2021_11 | 2 | 10 degF | `TMIN` | min | 13.000 | -3.000 | 0.500 | dflt | Temperatures dropped as low as six degrees in Austin, eight degrees in Dallas and ten degrees in Houston. |
| news_txtrib_ksat_2021_02_15 | 3 | 30,000 MW | `total` | value_in_window | 32,494.000 | -2,494.000 | 300.000 |  | ERCOT said Monday morning that 30,000 megawatts of power generation had been forced off the system. |

## Reading the mismatches (Claude's hand review, not checked by the user)

- **ercot_board_2021_02_24, 52,277 MW**: Extraction (layout) error, not ERCOT's. In the board deck's timeline the day heading follows its bullets in pdftotext order, so the bullet was dated 'Wednesday, February 17'. It belongs to Tuesday 2/16, and the 2/16 maximum is exactly 52,277 MW (07:36).
- **ercot_board_2021_02_24, 20,000 MW**: Same layout error. The 20,000 MW peak shed was on 2/15 (18:54 onward); on 2/17 the series peaks at 15,500.
- **ferc_final_2021_11, 8 degF**: Station choice or day (O1). FERC gives no date or station; DFW's daily minimum fell to 4 °F on 2/15 and -2 °F on 2/16, never 8 as a low for the event. FERC may mean another station (e.g. Dallas Love Field) or one day.
- **ferc_final_2021_11, 10 degF**: Station choice or day (O1). IAH's lowest daily minimum was 13 °F (2/16); 10 °F may be Hobby or another station. Not a clear source error.
- **news_txtrib_ksat_2021_02_15, 30,000 MW**: A real but small disagreement: ERCOT's Monday-morning figure as reported was 30,000 MW, while MW out was at least 32,494 all day Monday (8% higher). Likely a rounded or partial (thermal-only) figure in the statement.

So 2 of 5 mismatches are extraction errors that Tier 0 caught, not source errors, and the reliability scores below count them against the source anyway: the score measures *the claims as extracted*. Example of a strict non-anchor: the board deck's 'Min Frequency 59.302 Hz' matches the 1-second series minimum exactly, but its window is the document default (2/14–2/19) and the series covers only 42 minutes on 2/15, so it is `no_series` (coverage).

## When did load shed start? (Tier 0b settles a disagreement)

ERCOT's minute load-shed series is zero until the label **01:21** on 2/15, when it steps to 1,000 MW. The labels are minute-ending (A1), so shedding began in the minute **01:20–01:21**. If the labels were minute-start instead, the answer would move one minute later.

| Source | Tier | Stated | Residual (min) | Quote |
|---|---|---|---|---|
| ferc_final_2021_11 | 2 | 01:15 | -5 | after beginning EEA 3 at 1:15 a.m. with 1,000 MW firm load shed |
| enverus_txoga_2021_04 | 2 | 01:20 | +0 | February 15: ERCOT enters Emergency Operations Level 3 at 01:20 AM |
| ferc_final_2021_11 | 2 | 01:20 | +0 | needed to order its first 1,000 MW of load shed at 1:20 a.m. |
| ferc_final_2021_11 | 2 | 01:20 | +0 | 2/15: EEA 1, 2 and 3. Firm load shed implemented at 1:20 a.m. |
| ferc_final_2021_11 | 2 | 01:20 | +0 | At 1:20 a.m., ERCOT declared EEA 3 and instructed TOPs to shed 1,000 MW of firm load. |
| ut_austin_timeline_2021_07 (held out) | 2 | 01:20 | +0 | declared an EEA 3 event and began “firm load shed” or “blackouts.” |
| news_txtrib_ksat_2021_02_15 | 3 | 01:25 | +5 | The blackouts began at 1:25 a.m. Central time. |

**Verdict:** the data supports **1:20 a.m.** (to within the one-minute label ambiguity). Sources that say so: enverus_txoga_2021_04, ferc_final_2021_11, ut_austin_timeline_2021_07 (held out). Off by more than a minute: ferc_final_2021_11 (01:15, -5 min); news_txtrib_ksat_2021_02_15 (01:25, +5 min). The 1:15 and 1:25 statements sit at the edge of the ±5-minute matching window, so the general anchoring rule would not separate them; the minute series does.

## Source reliability

r = (α + agreed) / (α + β + anchored), with the tier prior worth 10 pseudo-observations (means 0.9 / 0.7 / 0.3 for Tiers 1 / 2 / 3). Documents with nothing anchored keep their prior.

| Document | Tier | Flags | Items | Anchored | Agree | Disagree | Prior | r |
|---|---|---|---|---|---|---|---|---|
| ercot_board_2021_02_24 | 1 | interested_party,preliminary | 189 | 2 | 0 | 2 | 0.90 | 0.750 |
| ercot_causes_2021_04_27 | 1 | interested_party,self_reported | 78 | 1 | 1 | 0 | 0.90 | 0.909 |
| enverus_txoga_2021_04 | 2 | funded_by_interested_party | 400 | 2 | 2 | 0 | 0.70 | 0.750 |
| ferc_final_2021_11 | 2 |  | 2057 | 20 | 18 | 2 | 0.70 | 0.833 |
| news_abc13_2021_02_15 | 3 |  | 15 | 2 | 2 | 0 | 0.30 | 0.417 |
| news_aljazeera_2021_02_15 | 3 |  | 49 | 0 | 0 | 0 | 0.30 | 0.300 |
| news_ap_2021_02_15 | 3 |  | 39 | 0 | 0 | 0 | 0.30 | 0.300 |
| news_ap_pbs_2021_02_16 | 3 |  | 29 | 0 | 0 | 0 | 0.30 | 0.300 |
| news_txtrib_ksat_2021_02_15 | 3 |  | 11 | 2 | 1 | 1 | 0.30 | 0.333 |

# Invariants report

Run `03_graph`. 186 extracted causal edges (`caused`, `contributed_to`). Violations are flagged on the edge (`invariant_flags`), never deleted.

| # | Rule | Result |
|---|---|---|
| 1 | Cause before effect | 6 ok, **3 violations**, 175 time_unverifiable |
| 2 | Nothing causes the weather | **0 violations** |
| 3 | Tier 0 never overridden | 0 violations |
| 4 | No causal cycles (event instances) | 0 cycles |
| 5 | Gas–power loop | entity level has a cycle: **True**; hourly unrolled (480 nodes, 477 edges) is acyclic: **True** |
| 6 | Grades | instances {'G1': 168, 'G0': 18}; classes {'G1': 46, 'G0': 3, 'G3': 1} |

## 1. Cause before effect

An event's start is an interval set by its precision (minute, hour, day; a range's start is a day unless a clock time is given). `ok` = the cause's interval ends before the effect's begins; `violation` = the cause starts after the effect has started; anything overlapping or unknown is `time_unverifiable`.

By cause → effect type (all statuses):

| Cause → effect | ok | violation | unverifiable |
|---|---|---|---|
| WeatherCondition → GenerationOutage | 1 | 0 | 41 |
| MarketAction → GridEmergency | 1 | 1 | 2 |
| GasSupplyDisruption → FacilityShutdown | 1 | 0 | 2 |
| GridEmergency → GridEmergency | 1 | 0 | 1 |
| GridEmergency → MarketAction | 1 | 0 | 0 |
| GridEmergency → Restoration | 1 | 0 | 0 |
| GasSupplyDisruption → GenerationOutage | 0 | 0 | 17 |
| GenerationOutage → GridEmergency | 0 | 0 | 17 |
| GasSupplyDisruption → GasSupplyDisruption | 0 | 1 | 7 |
| FacilityShutdown → GasSupplyDisruption | 0 | 0 | 7 |
| GridEmergency → GenerationOutage | 0 | 0 | 7 |
| WeatherCondition → GasSupplyDisruption | 0 | 0 | 6 |
| GridEmergency → GasSupplyDisruption | 0 | 0 | 5 |
| GridEmergency → LoadShed | 0 | 0 | 5 |
| LoadShed → LoadShed | 0 | 0 | 5 |
| WeatherCondition → GridEmergency | 0 | 0 | 5 |
| GenerationOutage → GasSupplyDisruption | 0 | 0 | 4 |
| LoadShed → GasSupplyDisruption | 0 | 0 | 4 |
| WeatherCondition → FacilityShutdown | 0 | 0 | 4 |
| GenerationOutage → GenerationOutage | 0 | 0 | 3 |
| InstitutionalAction → GasSupplyDisruption | 0 | 0 | 3 |
| InstitutionalAction → InstitutionalAction | 0 | 0 | 3 |
| LoadShed → FacilityShutdown | 0 | 0 | 3 |
| WeatherCondition → LoadShed | 0 | 0 | 3 |
| FacilityShutdown → GenerationOutage | 0 | 0 | 2 |
| GasSupplyDisruption → GridEmergency | 0 | 0 | 2 |
| GenerationOutage → LoadShed | 0 | 0 | 2 |
| WeatherCondition → Restoration | 0 | 0 | 2 |
| GridEmergency → FacilityShutdown | 0 | 1 | 1 |
| FacilityShutdown → InstitutionalAction | 0 | 0 | 1 |
| GasSupplyDisruption → InstitutionalAction | 0 | 0 | 1 |
| GasSupplyDisruption → LoadShed | 0 | 0 | 1 |
| GasSupplyDisruption → MarketAction | 0 | 0 | 1 |
| GenerationOutage → FacilityShutdown | 0 | 0 | 1 |
| LoadShed → GenerationOutage | 0 | 0 | 1 |
| LoadShed → GridEmergency | 0 | 0 | 1 |
| LoadShed → InstitutionalAction | 0 | 0 | 1 |
| LoadShed → Restoration | 0 | 0 | 1 |
| MarketAction → FacilityShutdown | 0 | 0 | 1 |
| Restoration → LoadShed | 0 | 0 | 1 |
| WeatherCondition → InstitutionalAction | 0 | 0 | 1 |

Every violation (3):

| Doc | Edge | Cause start | Effect start | Quote |
|---|---|---|---|---|
| ferc_final_2021_11 | GasSupplyDisruption → GasSupplyDisruption (caused) | 2021-02-08 00:00:00 | 2021-02-07 00:00:00 | resulted in a decline of natural gas production |
| ferc_final_2021_11 | MarketAction → GridEmergency (contributed_to) | 2021-02-16 06:45:00 | 2021-02-16 06:15:00 | Still short of generation, and unable to import what it needed to compensate for the generation shortfalls, SP |
| news_ap_pbs_2021_02_16 | GridEmergency → FacilityShutdown (caused) | 2021-02-16 00:00:00 | 2021-02-15 00:00:00 | Houston was forced to close two on Monday because of a loss in power |

## 2. Nothing causes the weather

Every violation (0):

| Doc | Edge | Cause | Weather event | Quote |
|---|---|---|---|---|

## 3. Tier 0 is never overridden

Checked: no comparability link (`different_scope`, `comparable`, `conflict`, `superseded`) touches a Tier 0 node, and `revised_by` into a series comes only from an earlier version of the same dataset (the unit list 3/4 → 3/12). No proposals exist yet; the rule is in place for them.


## 4. No causal cycles

Event instances: 0 cycles. At the class level (event types, the layer that aggregates instances) there are 150 cycles, which is expected: classes are not time-indexed, which is why the loop is unrolled in 5.


## 5. The gas–power loop

Skeleton from system knowledge, with the extracted text edges that support each link (`loop_entity.json`, `loop_hourly.json` for the frontend).

| Edge | Lag (h) | Mechanism | Text instances | Asserted | Grade | Sources | Related class edges |
|---|---|---|---|---|---|---|---|
| Cold -> GasProduction | 1 | freeze-offs and wellhead freezing | 6 | 6 | G1 | enverus_txoga_2021_04, ferc_final_2021_11 |  |
| GasProduction -> GasGeneration | 1 | fuel supply to gas-fired units | 10 | 8 | G1 | enverus_txoga_2021_04, ercot_board_2021_02_24, ferc_final_2021_11 | GasSupplyDisruption → GenerationOutage (7, G1) |
| GasGeneration -> LoadShed | 0 | generation loss forces shedding | 0 | 0 | none |  | GenerationOutage → LoadShed (2, G1); GenerationOutage → GridEmergency (15, G1) |
| LoadShed -> GasProduction | 1 | power_supply_loss: shed circuits feed gas facilities | 4 | 2 | G1 | enverus_txoga_2021_04, ferc_final_2021_11; plus 16 quant claims whose cause is loss of power (enverus_txoga_2021_04, ferc_final_2021_11; matched by label, since v0 had no `power_supply_loss`) |  |

Entity level: cycles [['GasGeneration', 'LoadShed', 'GasProduction']]. Unrolled hourly over Feb 14–18 with the lags above (Cold(t)→GasProduction(t+1), GasProduction(t)→GasGeneration(t+1), GasGeneration(t)→LoadShed(t), LoadShed(t)→GasProduction(t+1)): every edge goes forward in time or stays within an hour without closing a loop, so the graph is a DAG (True). The feedback is real, but each turn of it happens at a later hour.

## 6. Grades

G0 time order only; G1 asserted (not hedged) in text by at least one source; G2 supported by a statistical test in step 4 with a passed placebo; G3 G2 plus a second independent source or test. Instance edges get G0/G1 from their own modality; class edges aggregate instances (GenerationOutage is split by fuel when the event names one fuel).

Instance edges: {'G1': 168, 'G0': 18}. 15 are hedged (flag `hedged`).

Class edges: {'G1': 46, 'G0': 3, 'G3': 1}.

| Class edge | Grade | Instances | Flags |
|---|---|---|---|
| WeatherCondition → GenerationOutage:coal | G3 | 1 |  |
| WeatherCondition → GenerationOutage | G1 | 33 |  |
| GenerationOutage → GridEmergency | G1 | 15 |  |
| GasSupplyDisruption → GenerationOutage:gas | G1 | 10 |  |
| GasSupplyDisruption → GasSupplyDisruption | G1 | 8 |  |
| FacilityShutdown → GasSupplyDisruption | G1 | 7 |  |
| GasSupplyDisruption → GenerationOutage | G1 | 7 |  |
| WeatherCondition → GenerationOutage:wind | G1 | 7 |  |
| WeatherCondition → GasSupplyDisruption | G1 | 6 |  |
| GridEmergency → GasSupplyDisruption | G1 | 5 |  |
| GridEmergency → GenerationOutage | G1 | 5 |  |
| GridEmergency → LoadShed | G1 | 5 |  |
| LoadShed → LoadShed | G1 | 5 |  |
| WeatherCondition → GridEmergency | G1 | 5 |  |
| LoadShed → GasSupplyDisruption | G1 | 4 |  |
| MarketAction → GridEmergency | G1 | 4 |  |
| WeatherCondition → FacilityShutdown | G1 | 4 |  |
| GasSupplyDisruption → FacilityShutdown | G1 | 3 |  |
| GenerationOutage:gas → GasSupplyDisruption | G1 | 3 |  |
| InstitutionalAction → GasSupplyDisruption | G1 | 3 |  |
| InstitutionalAction → InstitutionalAction | G1 | 3 |  |
| LoadShed → FacilityShutdown | G1 | 3 |  |
| WeatherCondition → LoadShed | G1 | 3 |  |
| FacilityShutdown → GenerationOutage:gas | G1 | 2 |  |
| GasSupplyDisruption → GridEmergency | G1 | 2 |  |
| GenerationOutage → GenerationOutage | G1 | 2 |  |
| GenerationOutage → LoadShed | G1 | 2 |  |
| GenerationOutage:wind → GridEmergency | G1 | 2 |  |
| GridEmergency → FacilityShutdown | G1 | 2 |  |
| GridEmergency → GenerationOutage:coal | G1 | 2 |  |
| GridEmergency → GridEmergency | G1 | 2 |  |
| WeatherCondition → Restoration | G1 | 2 |  |
| FacilityShutdown → InstitutionalAction | G1 | 1 |  |
| GasSupplyDisruption → InstitutionalAction | G1 | 1 |  |
| GasSupplyDisruption → LoadShed | G1 | 1 |  |
| GasSupplyDisruption → MarketAction | G1 | 1 |  |
| GenerationOutage → GasSupplyDisruption | G1 | 1 |  |
| GenerationOutage:gas → FacilityShutdown | G1 | 1 |  |
| GridEmergency → MarketAction | G1 | 1 |  |
| GridEmergency → Restoration | G1 | 1 |  |
| LoadShed → GenerationOutage:gas | G1 | 1 |  |
| LoadShed → GridEmergency | G1 | 1 |  |
| LoadShed → InstitutionalAction | G1 | 1 |  |
| LoadShed → Restoration | G1 | 1 |  |
| Restoration → LoadShed | G1 | 1 |  |
| WeatherCondition → GenerationOutage:gas | G1 | 1 |  |
| WeatherCondition → InstitutionalAction | G1 | 1 |  |
| GenerationOutage → GenerationOutage:gas | G0 | 1 | hedged_only |
| MarketAction → FacilityShutdown | G0 | 1 | hedged_only |
| WeatherCondition → GenerationOutage:solar | G0 | 0 |  |

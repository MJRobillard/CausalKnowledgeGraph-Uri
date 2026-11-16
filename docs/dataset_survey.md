# Dataset survey: causal KG (2021 Texas outage / Winter Storm Uri)
Updated 2026-11-14; corrections from cleaning added 2026-11-15 (see `CHECKPOINT.md`, `docs/tamu_cleaning.md`). Folders are relative to `causal-kg/`.

## Summary
| # | Dataset | Folder | Feasibility | Role in the graph |
|---|---|---|---|---|
| 1 | TAMU 2021TXPowerOutage (Wu et al.) | `2021TXPowerOutage/` | High | Grid model, demand and outages by zone, county customer outages |
| 2 | ERCOT high-resolution data | `data/sources/ercot/data/` | High | Record of what actually happened (minute, 15-second and 1-second data) |
| 3 | Unit outage list, 3/4 and 3/12 versions | `data/sources/ercot/data/` | High | Unit-level events; example of a source being revised |
| 4 | Event reports (12, listed separately in §4) | `data/sources/` | Low–high, by report | Causes, timeline, answer key for event extraction, who paid for each source |
| 5 | NOAA NCEI hourly temperatures | `data/external/noaa/` | High | The starting cause (cold weather) |
| 6 | NOAA CDO v2 (token in `.env`) | `data/external/noaa/cdo_ghcnd_stations_by_outage_county.csv` | Medium–high | Matching weather stations to outage counties |
| 7 | EIA natural gas (monthly production, daily Henry Hub price) | `data/external/eia_gas/` | Low–medium | Gas supply, but only monthly; daily data is proprietary |

## 1. TAMU 2021TXPowerOutage
Good
- Main numbers match what was reported: peak load shed about 20 GW, generation out about 52 GW, about 4M customers without power
- ERCOT data adds up: fuel totals match net generation (within 5 MWh), and demand plus interchange balances

Problems
- Dates are wrong in some files: the simulation outputs and the demand profile are dated 2016 but hold Feb 2021 data
- Timezones are mixed (EIA in Central time, simulation outputs in UTC)
- Bad values in the outage records: 13 county names are numbers, 46 percent-out values are over 100%, 317 rows report more customers out than tracked
- Two EIA files are empty, and SWPP's fuel totals don't add up
- Duplicate files; 4 versions of the wind data
- Built on the **3/4** unit outage list, the earlier version

Update 2026-11-15 (cleaned into `data/external/tamu/`):
- The simulation outputs aren't UTC. "2016-01-01 00:00 UTC" is a placeholder row index. The demand profile really starts at the hour beginning 1/30/2021 23:00 CST (exact match to EIA), so TAMU's model ran demand 25 h early and wind 6 h late
- The solar and hydro "Feb2016" profiles look like real 2016 weather stand-ins
- The unit-outage matrices headed "UTC" are actually Central. The EIA API load file is local Central, not UTC
- 2 of the 13 numeric county codes don't follow the alphabetical index (70 = Edwards, 119 = Irion)

## 2. ERCOT high-resolution data
Good
- MW out by fuel and load shed every minute; available generation every 15 seconds; grid frequency every second (2/15 from 01:23)
- Peaks line up with the TAMU data: load shed peaks at 20,000 MW on 2/15 at 19:00 Central, and MW out peaks at 52,009 on 2/16 at 07:00 Central

Problems
- "Demand if nothing had been shed" is **hourly only** (120 points, from 2/15)
- Data covers the whole system only, and only 2/14–2/20
- Times are Central (assumed CST, UTC−6, which is consistent with the TAMU peaks)

## 3. Unit outage list: two versions
- 3/4 version: 1,661 real rows (the "1,796" count includes 135 blank rows), 311 units, starting 2/14
- The 135 blank rows are probably **redacted** entries (START/END only), not empty space. Every one of their time pairs appears as a named unit in 3/12, so some of the "138 added" below are these rows being identified
- 3/12 version: 2,599 rows, 374 units, starting 2/10. 803 of these rows come only from the wider window.
- From 2/14 on: 1,658 rows shared, 138 added, 3 dropped. MW lost rose for wind (+9%), solar (+45%) and gas (+3%).
- Each unit has a county, so outages can be split by zone directly
- Causes are not given per unit (ERCOT keeps them confidential)

## 4. Event reports (`data/sources/`)
All PDFs have a text layer (none are image-only). In most, the charts are images, so the numbers in them have to be read from the slide text or redrawn.

**Where the two cause reports disagree:** ERCOT puts fuel limitations at **12%** (MW out at the peak minute, ERCOT only). FERC/NERC puts fuel issues at **31.4%** (count of outage events, across ERCOT, SPP and MISO, 2/8–20). The difference comes from what's counted and over what area and time, which is exactly what the source tiers should explain.

| # | Report | Who wrote it | Date | Pages | Feasibility |
|---|---|---|---|---|---|
| 4a | Final report | FERC/NERC | Nov 2021 | 316 | High |
| 4b | Preliminary findings slides | FERC/NERC | 9/23/21 | 31 | High |
| 4c | Recommendation tracking | FERC | Dec 2022 | 9 | Medium |
| 4d | Board review of the event | ERCOT | 2/24/21 | 23 | Medium |
| 4e | Preliminary report on outage causes | ERCOT | 4/6/21 | 12 | Medium |
| 4f | Updated report on outage causes | ERCOT | 4/27/21 | 25 | High |
| 4g | ERCOT answers to questions on 4e | ERCOT | 4/27/21 | 3 | Low |
| 4h | PUCT filing 51878 item 26 | ERCOT (to PUCT) | 5/4/21 | 28 | Low |
| 4i | Resource list for DOE emergency order | ERCOT | 2/14/21 21:00 | 3 | Medium |
| 4j | Emergency alert (EEA) levels and tools | ERCOT | 9/2020 and 2/13/21 | 1–3 each | Medium |
| 4k | Event timeline | UT Austin | July 2021 | 101 | High |
| 4l | Natural gas analysis | Enverus, paid by TXOGA | Apr 2021 | 41 | Medium |

**4a. FERC/NERC final report** (`federal/FERC_NERC_Feb2021_Cold_Weather_Final_Report_2021-11.pdf`)
- Good: the most authoritative source. Causes: freezing 44.2%, fuel 31.4% (gas supply 27.3% is *part of* the fuel share), mechanical/electrical 21%. 1,045 units, 4,124 outage events, 28 recommendations. Chapters on gas production and pipelines contain the only open daily gas figures. Clean text (about 670K characters).
- Problems: covers ERCOT, SPP and MISO together and counts events, not MW, so it can't be compared directly with ERCOT's report. Long; extracting from it means splitting it into pieces.

**4b. FERC/NERC preliminary slides** (`federal/FERC_NERC_Preliminary_Findings_Slides_2021-09-23.pdf`)
- Good: lets you compare the preliminary findings with the final report, another example of a source being revised. Text-heavy slides.
- Problems: marked "CUI" on page 1 even though it's published; mostly overlaps with 4a.

**4c. FERC recommendation tracking, Dec 2022** (`federal/FERC_Recommendation_Status_2022-12.pdf`)
- Good: shows what happened afterwards (which recommendations were acted on). Useful if the graph covers responses, not just the event.
- Problems: short, mostly graphics, no data about the event itself.

**4d. ERCOT board review, 2/24/21** (`ercot/ERCOT_Board_Review_2021-02-24.pdf`)
- Good: ERCOT's earliest account, written days after the event. The source for the frequency and fuel charts, whose data is in `ercot/data/`.
- Problems: labelled "preliminary"; 384 images and little text; written by the organization under investigation.

**4e. ERCOT preliminary outage causes, 4/6/21** (`ercot/51878_ERCOT_Letter_re_Preliminary_Report_on_Outage_Causes.pdf`)
- Good: first look at causes; compare with 4f to see what changed.
- Problems: the industry pushed back on its broad categories; replaced by 4f.

**4f. ERCOT updated outage causes, 4/27/21** (`ercot/ERCOT_Outages_By_Cause_Updated_2021-04-27.pdf`)
- Good: cause breakdown at the peak (2/16 08:00, **52,037 MW**): weather 53%, existing outages 15%, equipment 14%, fuel 12%, transmission loss 3%, frequency-related 2%, miscellaneous 1%. Shows causes over time and by fuel. The peak was revised from the previously reported 52,277 MW, another revision example.
- Problems: ERCOT only, 2/14–19; based on what generators self-reported; causes not given per unit.

**4g. ERCOT answers to stakeholder questions** (`ercot/ERCOT_Responses_to_Preliminary_Outage_Report_Questions.pdf`)
- Good: explains how 4e was put together and what it leaves out (for example, ERCOT declined to widen the dates to 2/9–20).
- Problems: no data.

**4h. PUCT filing 51878-26** (`ercot/PUCT_51878_26_ERCOT_filing.pdf`)
- Good: the official record of 4f as filed with the state utility commission.
- Problems: **a scanned copy of 4f** with poor text recognition ("Ausbn", "Centei D, ive"). Use 4f instead; keep this only to show where the record came from.

**4i. DOE 202(c) emergency order resource list, 2/14 21:00** (`ercot/Resource_list_021421_2100.pdf`)
- Good: lists units with fuel, county and winter max/min ratings, at a single point in time. Matches the unit outage list by unit code.
- Problems: one snapshot; covers only the units in the order.

**4j. ERCOT emergency alert levels** (`ercot/EEA_OnePager_updated_9-4-20.pdf`, `EEA_Tools_One_Pager_Winter_2021_2-13-2021.pdf`, `EEA_Levels_SPANISH_9-14-20.pdf`)
- Good: definitions of the alert levels and the emergency tools for each. Can define the graph's "grid state" categories.
- Problems: describe procedures, not what happened; the file named "EEA_Levels" is the **Spanish** version (renamed), and the English one-pagers were added.

**4k. UT Austin timeline, July 2021** (`other/UTAustin_Timeline_Feb2021_Blackouts_2021-07.pdf`)
- Good: written by a university committee (not a party to the investigation); a timeline of events from 2/10 to 2/20 covering both gas and power; clean text (about 205K characters). The answer key for event extraction.
- Problems: mostly prose with only about 21 clock times; you'll need to turn it into a list of events by hand before scoring. Draws heavily on ERCOT's and the PUCT's own figures, so it isn't independent of 4d and 4f. Don't extract events from it if you're also using it as the answer key.

**4l. Enverus/TXOGA natural gas analysis, Apr 2021** (`other/Enverus_TXOGA_Uri_NatGas_Analysis_2021-04.pdf`)
- Good: gas production and demand charts at a level of detail public sources don't have (from Enverus's own data).
- Problems: **paid for by the industry** ("Prepared for TXOGA"), and the underlying data is proprietary. 179 images, so the numbers are mostly in charts. Use it as the "who paid for it" example against 4a's chapter on gas.

**Gap:** no ERCOT control-room logs are public. The closest are 4k, 4j and the frequency data.

## 5. NOAA NCEI hourly temperatures (downloaded)
- One station per ERCOT weather zone: IAH, TYR, MAF, SPS, DFW, AUS, CRP, ABI, plus SAT. 2/1–21, no token needed.
- Clean file: `data/external/noaa/ercot_zone_temp_hourly_utc_F.csv` (hourly, UTC, °F, gaps of up to 3 hours filled)
- Quality: 3% of readings are missing or bad; 1 missing hour each at East and Far West
- One station per zone is a rough stand-in; use CDO v2 to find stations by county for finer detail

## 6. NOAA CDO v2: stations by county (token works, 2026-11-14)
- Looked up stations that reported daily minimum temperature for 2/10–20 in each of the 113 counties in the 3/12 unit outage list. 0 request errors.
- **95 of 113 counties have at least one station, covering 83.7% of the MW lost.** 223 stations in total.
- 52 counties have an airport station (ID starts USW). Those also have hourly data in NCEI's `global-hourly`, so hourly temperature by county is possible there.
- Largest gaps (MW lost): Carson 9.2 GW, Limestone 4.7, Glasscock 4.4, Grimes 3.2, Sterling 2.9. Use the nearest station in a neighbouring county; needs county centroids.
- Limits: daily data only through this API; 1,000 rows per request, 5 requests per second, 10,000 per day (this run used 113).

## 7. EIA natural gas (downloaded)
- Texas marketed production, Feb vs Jan 2021 per day: **−15.4%** (US: −7.4%). The monthly average hides the roughly 70% drop on the worst days that FERC reports.
- Henry Hub price: $3.76 on 2/10, then $11.32 on 2/16, **$23.86 on 2/17**, and back to $2.94 by 2/23
- **Limitation:** Henry Hub is in Louisiana. Texas hub prices (Houston Ship Channel, Katy, Waha) were probably much higher, but their daily prices are likely paid-only (S&P Platts, NGI). Don't chase them; use Henry Hub as a lower bound for Texas prices.
- Daily wellhead and pipeline data comes from paid sources (S&P, Enverus). The open fallback is the daily figures quoted in FERC/NERC's chapter on gas.

## Findings from the downloaded data
1. **The cold came first, then the outages.** The coldest hour across ERCOT (zone average 6.1 °F) was 2/16 06:00 Central. Generation out of service peaked one hour later (52.0 GW). Load shed had already peaked the evening before (2/15 19:00 Central).
2. **Outages lag the temperature.** Correlation between MW out and temperature (Spearman) is −0.42 with no lag and grows to −0.76 with a 24-hour lag. Plants kept failing and stayed down after it started warming: at 2/19 11:00 Central, when the zone average first rose above freezing, 32.9 GW was still out.
3. **Which fuels track temperature most:** solar (−0.71) and wind (−0.61) track it most closely; gas (−0.30) and coal (−0.28) much less. That fits gas outages being driven by fuel supply, not just temperature. While it was getting colder, gas was −0.44; once it started warming, −0.35.
4. **Most of the state was below freezing for days.** Six of the eight zones spent 136–141 hours at or below 32 °F between 2/14 and 2/19. Coast and Southern spent 63–69 hours. The lowest reading was −6 °F at Wichita Falls (North zone).
5. **The gas shortfall shows up in price before production.** Henry Hub rose more than sixfold, while monthly production fell only 15%. Monthly data is too coarse to show the gas–power feedback loop.

**Test: does shedding load raise gas outages a few hours later?** (`analysis/event_study.py`, chart in `analysis/`)
- Shock time t₀: 2/15 01:59 Central. Load shed jumped 9.5 GW in 15 minutes.
- **Result: inconclusive (the "about zero, or noisy" branch).** Gas excess over controls at +6h depends on how far back the pre-shock trend is measured: **+6.1 GW** (12h), +2.8 (6h), **−2.4** (3h). Measured from t₀+1h instead: −1.8 GW.
- Why: gas outages started climbing about 3h *before* t₀ (about +5.5 GW). The gas outages drove the shedding, so the shock isn't independent of gas conditions. After t₀, gas outages keep rising but no faster than just before it.
- The nuclear control is contaminated: South Texas Project's 1.35 GW trip at +3.5h was caused by cold, not gas. Coal is the cleaner control.
- FERC/NERC backs this up: loss of power caused 18.1% of gas production declines on 2/14 (before any shedding) and 21.6% on 2/17, so only a small rise after shedding began. FERC gives **no MW figure** for how much electricity gas facilities draw ("exact extent… unknown"; few were registered as critical load).

**Time alignment check:** NOAA times are UTC (checked: Houston's coldest hour, 11:00 UTC, is 5 a.m. local, which fits a dawn low). ERCOT times are Central (checked: the frequency low of 59.302 Hz falls at 01:54:58, which matches the reported "about 1:55 a.m. CST" on 2/15). Both were put in UTC (ERCOT + 6 hours) before any correlation, and hourly averages are labelled by the start of the hour in both. CST is right for all of 2/1–21, since daylight saving time didn't start until 3/14.

## Open questions
- Lining up times across sources (UTC vs Central, and 1-second to monthly steps) is the main preparation job
- No open daily source for gas supply
- Converting the FERC and ERCOT cause figures to the same scope

# Extraction prompt v0.2

Run per chunk (~1,200–1,500 words, split on headings or paragraphs, never mid-sentence).
Force structured output with `extraction_schema.json`. Temperature 0.

Two choices that matter:

- **The model sees the document's scope, not its tier.** Scope (region, window, cause scheme) is
  needed to fill in what a sentence leaves implicit. Tier and flags are withheld so that trust
  can't leak into what gets extracted. They're attached afterwards from `documents.yaml`.
- **Two passes.** Pass 1 extracts entities, events and relations. Pass 2 re-reads the same chunk
  only for numbers and their scope. Single-pass prompts tend to drop denominators.

---

## System prompt (both passes)

```
You extract structured facts from documents about the February 2021 Texas winter
storm (Winter Storm Uri) and the power and natural gas systems.

Rules:
1. Extract only what the text states. Do not add knowledge from outside the chunk,
   except the DOCUMENT CARD provided.
2. Every item needs a `quote`: exact words copied from the chunk, at most 40 words.
   If you can't quote it, don't extract it.
3. Modality describes the wording, not your belief:
   - asserted: stated as fact ("X caused Y")
   - hedged: "may have", "likely", "contributed to", "appears", "estimated"
   - denied: "did not", "was not a factor"
   "About", "nearly", "roughly" and "more than" make a number approximate, not hedged.
4. Times: copy the time as written into `time_text`. Resolve it to ISO 8601 in US
   Central time using the DOCUMENT CARD's publication date and window when the text
   makes it unambiguous ("Monday, Feb. 15 at 1:55 a.m."). If it is ambiguous
   ("early in the week"), set `time_resolved` to null and precision to "unknown".
   Never guess.
5. Use `caused` only when the text asserts direct causation. Use `contributed_to` for
   partial causes, `associated_with` when the text reports a correlation or
   co-occurrence without claiming cause, and `preceded` when the text only gives an
   order of events.
6. If a statement matters but fits no type, put it in `other` with a suggested type.
   Do not force a bad fit.
7. Entities are named, specific things ("ERCOT", "W.A. Parish", "Permian Basin").
   Do not extract generic roles or classes ("Balancing Authorities", "generators",
   "TOPs", "natural gas producers") as entities.
8. Return valid JSON matching the schema. Empty arrays are fine.
```

## Pass 1 — user message

```
DOCUMENT CARD
title: {title}
publisher: {publisher}
published: {published_at}
default region: {scope.region}
default window: {scope.window}

TASK
Extract entities, events and relations from the chunk below.
Leave `quant_claims` empty in this pass.
- Use Region for named areas other than ERCOT's eight load zones: SPP, MISO South,
  Texas, the Permian Basin, "the Event Area".
- Use Demand for load levels, peaks, records and forecasts.
- Use WaterServiceDisruption for water systems losing pressure or service, or
  issuing boil-water notices.

CHUNK
{chunk_text}
```

## Pass 2 — user message

```
DOCUMENT CARD
title: {title}
publisher: {publisher}
published: {published_at}
default region: {scope.region}
default window: {scope.window}
default as_of: {scope.as_of}
cause scheme: {scope.cause_scheme}
default metric for cause shares: {scope.cause_metric_default}
{section_cards_block}
EVENTS ALREADY FOUND IN THIS CHUNK (use their local_ids in `about`)
{pass1_events_json}

TASK
Extract every number in the chunk as a quant_claim. Leave the other arrays empty.

For each number:
- metric: what kind of quantity it is. A percentage of outage events is
  share_of_events; a percentage of megawatts out is share_of_mw. If the sentence
  doesn't say which, use the card's default metric and mark it document_default.
- denominator: for any share, state what it is a share of, in the document's words.
- region and window: take them from the sentence if stated; otherwise from the card.
- Record where each scope field came from in `scope_source`:
  "sentence", "previous_sentence", "section_default", "document_default", or "missing".
- Ranges: for "30 to 40 percent", make ONE claim with value_low 30, value_high 40 and
  value 35 (the midpoint). Otherwise value_low and value_high are null.
- approximate: true when the number is introduced by "about", "nearly", "roughly",
  "approximately", "more than", "over" or "almost". This is not hedging; keep
  modality as the sentence's wording says.
- Metrics added in v0.2: share_of_energy (% of MWh), share_of_responses (% of survey
  or questionnaire responses), wind_speed (mph).
- cause_label_raw: the cause category exactly as the document names it.
  cause_category: your mapping to the common list. If the document's category is
  broader than any option (e.g. "weather"), use weather_general rather than
  picking a narrower one. Use power_supply_loss when a facility (gas well, plant,
  compressor, water system) lost its electricity supply.
- Skip page numbers, footnote markers, section numbers and dates used only as dates.
{context_block}
CHUNK
{chunk_text}
```

## Scope fixes (v0.2). Local context is on by default (`--no-context` turns it off); section cards are optional (`--section-cards`)

**Section card**: one extra call per report section (sections come from the PDF outline). Its result is
shown to pass 2 next to the document card. System and user messages:

```
You describe the default scope of the numbers in one section of a report about the
February 2021 Texas winter storm (Winter Storm Uri). Use only the DOCUMENT CARD, the
section's heading path and the opening text shown. Return a JSON object with keys
region, window_start, window_end, as_of, metric_default, population, quote.
```

```
DOCUMENT CARD
title: {title}
publisher: {publisher}
published: {published_at}
default region: {scope.region}
default window: {scope.window}

SECTION
heading path: {section_path}
opening text:
{section_text}

Fill in:
- region: the area this section's numbers describe (a state or group of states, basins,
  an ISO footprint). Null if the section doesn't narrow the document's region.
- window_start / window_end (YYYY-MM-DD): the period the section covers, if the heading
  or opening text states one. Else null.
- as_of: a single point in time the section's numbers refer to, if any. Else null.
- metric_default: the usual metric of shares in this section, one of share_of_events,
  share_of_mw, share_of_units, share_of_volume, share_of_energy, share_of_responses;
  null if unclear.
- population: what the section's numbers count or measure, in the document's words
  (e.g. "natural gas production volumes", "generating unit outages, derates and
  failures to start").
- quote: exact words (at most 40) from the opening text that support region or
  population.
```

**Pass 2 addendum, section cards** (fills `{section_cards_block}`):

```

SECTION CARDS (more specific than the document card; use the card of the section the
number appears in; headings appear in the chunk text)
{section_cards_json}
Where a section card gives region, window, metric or population, it overrides the
document card for numbers in that section: mark the field "section_default" in
scope_source. A scope stated in the number's own sentence still wins over both cards.
```

**Pass 2 addendum, local context** (fills `{context_block}`):

```

NUMBER SENTENCES, EACH WITH THE SENTENCE BEFORE IT
{number_context}

- A number's scope is often in the sentence before it. Example: "Wind made up 70
  percent of the units that tripped. The remaining 30 percent were solar." 30 percent
  is a share of the units that tripped. When the number's own sentence leaves out the
  denominator, region or period, take it from the previous sentence and mark that
  field "previous_sentence" in scope_source.
- A date or period next to a number, in its sentence or the sentence before, overrides
  the default window. "On February 12, 40 percent of ..." has window 2021-02-12 to
  2021-02-12. If a sentence names several dates, use the one tied to that number:
  "30 percent overall, 25 percent on February 12 and 35 percent on February 18" gives
  25 the window February 12, 35 the window February 18, and 30 the default window.
```

---

## After extraction (in code, not in the model)

1. Attach `doc_id`, `tier`, `flags`, `claim_status` and `published_at` from `documents.yaml`.
2. Check every `quote` actually appears in the chunk (normalized whitespace). Drop items that
   fail and count them: that's your hallucination rate.
3. Assign global ids; local ids are only unique within a chunk.
4. Resolve entities across chunks: exact name match, then alias list, then embedding
   similarity above a threshold. Log every merge.
5. Apply `comparability_rule` from `ontology.yaml` to pairs of quant_claims about the same cause:
   link them `different_scope` or `comparable`, and flag `conflict` only when comparable values
   differ beyond rounding.

# Reproduce the results in results/ from the committed inputs. One target per pipeline stage, in order.
# `make all` replays every stage after extraction (about 15 minutes; no API calls). See README.md "Run it".

PY := uv run python

.PHONY: all test data extraction graph hypotheses spatial causal_ml grid maps event_study site

all: graph hypotheses spatial causal_ml grid maps

test:
	uv run pytest -q

# Inputs not in git: third-party documents, public downloads, the TAMU repo (grid model)
data:
	$(PY) scripts/fetch_sources.py
	$(PY) scripts/fetch_spatial.py
	test -d 2021TXPowerOutage || git clone https://github.com/tamu-engineering-research/2021TXPowerOutage

# 01: post-processing and scorecard for the extraction run. New extractions need DEEPSEEK_API_KEY:
#     $(PY) -m kg.extract --run <run>      (cached responses replay for free)
extraction:
	$(PY) -m kg.post 01_extraction
	$(PY) -m kg.score 01_extraction --reviewed

# 03: tiered graph, grounding against Tier 0, invariants, cold models
graph:
	$(PY) -m kg.post 01_extraction --out results/03_graph
	$(PY) -m kg.graph_build
	$(PY) -m kg.anchor
	$(PY) -m kg.invariants
	$(PY) -m analysis.cold_models

# 04: hypothesis families and their tests (starts from the graph stage's nodes and edges)
hypotheses:
	$(PY) scripts/digitize_fig35.py
	$(PY) -m analysis.cold_baselines
	$(PY) -m kg.hypotheses
	$(PY) -m analysis.hypothesis_tests
	$(PY) -m analysis.hypothesis_outputs

# fleet match and spatial layer (data/processed/); needs data/raw/ from `make data`
spatial:
	$(PY) -m kg.fleet
	$(PY) -m kg.spatial

# 05: DML and causal forest (about 3 minutes)
causal_ml:
	$(PY) -m analysis.causal_ml

# 06: grid replay and coal tests (about 10 minutes); needs the TAMU repo
grid:
	$(PY) -m analysis.grid_replay
	$(PY) -m analysis.coal_test
	$(PY) -m analysis.grid_report

# 07: maps
maps:
	$(PY) -m analysis.maps

event_study:
	$(PY) -m analysis.event_study

# static walkthrough page (site/, served as-is; on Vercel set Root Directory to `site`): refresh its figures
SITE_FIGS := results/07_maps/map_cold_vs_outages_daily.png results/07_maps/map_frequency_trips.png \
	results/07_maps/map_gas_supply_chain.png results/07_maps/map_grid_congestion.png \
	results/07_maps/map_cold_sensitivity.png results/04_hypotheses/hypothesis_lead.png \
	results/06_grid/replay.png results/05_causal_ml/fuel_effects.png
site:
	mkdir -p site/assets && cp $(SITE_FIGS) site/assets/
	$(PY) scripts/build_site_data.py

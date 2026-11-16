"""Read FERC/NERC Figure 35 (Daily Dry Natural Gas Production, Jan-Feb 2021, Bcf/d, source IHS) off the chart.

The figure is a raster stacked-area chart (page 84 of the final report). Deterministic digitizing, no model:
  - y axis: rows of the 10 Bcf/d gridlines (darker shades of each fill) fitted by least squares -> Bcf/d per pixel
  - x axis: centres of the 15 date labels (Jan 01 ... Feb 26, every 4 days) fitted by least squares -> pixel per day
  - per day, in that day's pixel column: height of each band (Louisiana grey, Oklahoma orange, Texas yellow,
    rest of Lower 48 blue) and the top of the stack (the red dashed total line's centre where it covers the top)
Checked against the two totals FERC states in its text: 90.8 Bcf/d on Feb 8 and 65.4 Bcf/d on Feb 17.

    uv run python scripts/digitize_fig35.py
Writes data/processed/chart_reads/ferc_fig35_daily_production.csv, ..._calibration.json and the extracted image.
These are Tier 2 values read from a chart (FERC's document), not measurements; see ASSUMPTIONS H13.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / "data" / "sources" / "federal" / "FERC_NERC_Feb2021_Cold_Weather_Final_Report_2021-11.pdf"
OUT = ROOT / "data" / "processed" / "chart_reads"
PAGE = 84
FIRST_DAY = pd.Timestamp("2021-01-01")
LABEL_DAYS = list(range(0, 57, 4))                  # Jan 01, Jan 05, ..., Feb 26
FERC_TOTALS = {"2021-02-08": 90.8, "2021-02-17": 65.4}
CHECK_TOL = 1.0                                     # Bcf/d

BANDS = {  # flat fill colour and its gridline shade
    "louisiana": [(192, 192, 192), (191, 191, 191)],
    "oklahoma": [(242, 164, 110), (218, 139, 85)],
    "texas": [(255, 211, 76), (231, 186, 51)],
    "rest_of_lower48": [(124, 156, 214), (100, 131, 189)],
}
GRID_SHADES = [(100, 131, 189), (231, 186, 51), (218, 139, 85)]


def extract_image() -> np.ndarray:
    page = PdfReader(PDF).pages[PAGE - 1]
    assert "Figure 35" in (page.extract_text() or ""), "Figure 35 not on the expected page"
    (img,) = page.images
    im = Image.open(io.BytesIO(img.data)).convert("RGB")
    OUT.mkdir(parents=True, exist_ok=True)
    im.save(OUT / "ferc_fig35.png")
    return np.asarray(im).astype(int)


def is_colour(px: np.ndarray, colours, tol: int = 6) -> np.ndarray:
    return np.any([np.all(np.abs(px - np.array(c)) <= tol, axis=-1) for c in colours], axis=0)


def calibrate_y(a: np.ndarray) -> tuple[float, float, list]:
    """Rows where a gridline shade spans most of the plot -> value = 10 * k; returns (row_of_zero, px_per_bcfd)."""
    plot = a[:, 40:700]
    frac = np.mean(is_colour(plot, GRID_SHADES), axis=1)
    rows = [y for y in range(len(frac)) if frac[y] > 0.05 and (y == 0 or frac[y - 1] <= 0.05)]
    axis_row = max(y for y in range(len(frac)) if np.mean(is_colour(plot[y], BANDS["louisiana"])) > 0.9)
    pts = [(axis_row + 1, 0.0)]                 # bottom edge of the axis row = 0 Bcf/d
    for y in rows:                               # gridlines above the axis are 10, 20, ... from the bottom
        pts.append((y + 0.5, None))
    grid = sorted(rows, reverse=True)
    vals = [(axis_row + 1, 0.0)] + [(y + 0.5, 10.0 * (i + 1)) for i, y in enumerate(grid)]
    ys, vs = np.array([p[0] for p in vals]), np.array([p[1] for p in vals])
    slope, intercept = np.polyfit(vs, ys, 1)    # row = intercept + slope * value
    return float(intercept), float(-slope), vals


def calibrate_x(a: np.ndarray) -> tuple[float, float, list]:
    """Centres of the date-label text clusters under the axis -> x = x0 + day * px_per_day."""
    band = a[458:474, :]
    dark = np.any(np.all(band < 120, axis=-1), axis=0)
    cols = np.where(dark)[0]
    clusters, start = [], cols[0]
    for p, c in zip(cols, cols[1:]):
        if c - p > 8:
            clusters.append((start, p)); start = c
    clusters.append((start, cols[-1]))
    centres = [(s + e) / 2 for s, e in clusters]
    assert len(centres) == len(LABEL_DAYS), f"expected {len(LABEL_DAYS)} date labels, found {len(centres)}"
    slope, x0 = np.polyfit(LABEL_DAYS, centres, 1)
    return float(x0), float(slope), centres


PLOT_TOP = 60                                       # above the 100 Bcf/d gridline (row 64)


def read_column(a: np.ndarray, x: int, row0: float, ppv: float) -> dict:
    """Band heights inside the plot only (rows PLOT_TOP .. the axis), so label text below the axis isn't counted."""
    a = a.copy()
    a[: PLOT_TOP] = 255
    a[int(np.ceil(row0)):] = 255
    col = a[:, x]                                    # one column: a wider window overcounts on steep slopes
    out = {}
    for name, cols in BANDS.items():
        out[name] = is_colour(col, cols).sum() / ppv
    red = np.where(np.all((a[:, x] >= [200, 0, 0]) & (a[:, x] <= [255, 80, 80]), axis=-1))[0]
    blue = np.where(is_colour(a[:, x], BANDS["rest_of_lower48"]))[0]
    top = blue.min() if len(blue) else None
    if len(red) and top is not None and red.max() >= top - 3:
        top_row = (red.min() + red.max() + 1) / 2          # centre of the red dashed stroke
    else:
        top_row = top
    out["total_lower48"] = (row0 - top_row) / ppv if top_row is not None else np.nan
    return out


def main() -> dict:
    a = extract_image()
    row0, ppv, yvals = calibrate_y(a)
    x0, ppd, centres = calibrate_x(a)
    rows = []
    for day in range(0, 59):
        x = int(round(x0 + day * ppd))
        x = min(max(x, int(np.ceil(x0)) + 2), int(x0 + 58 * ppd) - 2)   # edge days: step off the axis and plot border
        if x + 1 >= a.shape[1]:
            break
        v = read_column(a, x, row0, ppv)
        rows.append({"date": (FIRST_DAY + pd.Timedelta(days=day)).date().isoformat(), "pixel_x": x,
                     **{k: round(float(val), 2) for k, val in v.items()},
                     "sum_of_bands": round(sum(v[k] for k in BANDS), 2)})
    df = pd.DataFrame(rows)
    checks = {d: {"ferc_text": v, "chart_read": float(df.set_index("date").total_lower48[d]),
                  "ok": abs(float(df.set_index("date").total_lower48[d]) - v) <= CHECK_TOL}
              for d, v in FERC_TOTALS.items()}
    stack_gap = float((df.total_lower48 - df.sum_of_bands).abs().max())
    cal = {"source": "FERC/NERC final report Nov 2021, Figure 35 (page 84), data from IHS", "image": "ferc_fig35.png",
           "y": {"row_of_zero": row0, "px_per_bcfd": ppv, "points": yvals},
           "x": {"x_of_jan01": x0, "px_per_day": ppd, "label_centres": centres},
           "resolution_bcfd": 1 / ppv, "checks_against_ferc_text": checks,
           "max_gap_total_vs_sum_of_bands_bcfd": stack_gap, "unit": "Bcf/d, dry gas"}
    df.to_csv(OUT / "ferc_fig35_daily_production.csv", index=False)
    (OUT / "ferc_fig35_daily_production_calibration.json").write_text(json.dumps(cal, indent=1, default=float))
    if not all(c["ok"] for c in checks.values()):
        raise SystemExit(f"STOP: chart read disagrees with FERC's stated totals: {checks}")
    return cal


if __name__ == "__main__":
    c = main()
    print(json.dumps({k: c[k] for k in ("resolution_bcfd", "checks_against_ferc_text", "max_gap_total_vs_sum_of_bands_bcfd")}, indent=1))
    print(pd.read_csv(OUT / "ferc_fig35_daily_production.csv").iloc[35:52].to_string(index=False))

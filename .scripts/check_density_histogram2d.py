"""Render the restored Plotly Histogram2d density chart (draw_hunting_density_chart,
_hunting_intelligence.py) against real parnaiguacu data, convert to PNG, and
open it — to visually confirm it still looks right (colorbar + on-chart
hotspot labels, matching the reference report's Figura 7) before wiring it
back into __init__.py / spec.yaml.

Imports draw_hunting_density_chart directly from its module (not via the
package __init__.py, which hasn't been switched back yet).

Run with:
  cd /path/to/ICMBio-patrol_analysis/ecoscope-workflows-patrol-analysis-workflow
  PYTHONPATH=/path/to/wd-partner-tasks/src/ecoscope-workflows-ext-icmbio \
    pixi run -e default python /path/to/ICMBio-Territorial_Intelligence/.scripts/check_density_histogram2d.py
"""
import datetime
import subprocess
from pathlib import Path

from ecoscope.platform.connections import EarthRangerConnection
from ecoscope.platform.tasks.filter._filter import TimeRange, TimezoneInfo
from ecoscope.platform.tasks.io._earthranger import get_events, process_events_details
from ecoscope.platform.tasks.transformation._conversion import convert_values_to_timezone
from ecoscope_workflows_ext_custom.tasks.io._html_to_png import ScreenshotConfig
from ecoscope_workflows_ext_custom.tasks.io import html_to_png

from ecoscope_workflows_ext_icmbio.tasks import (
    add_lat_lon_to_gdf,
    cluster_hunting_hotspots,
    extract_hunting_fields,
    summarize_hotspots,
)
from ecoscope_workflows_ext_icmbio.tasks._hunting_intelligence import draw_hunting_density_chart

SCRIPT_DIR = Path(__file__).parent
OUT_HTML = SCRIPT_DIR / "density_histogram2d_check.html"

er = EarthRangerConnection.from_named_connection("parnaiguacu").get_client()
print(f"Connected to {er.server}")

tz = TimezoneInfo(label="UTC", tzCode="UTC", name="UTC", utc="+00:00")
time_range = TimeRange(
    since=datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc),
    until=datetime.datetime(2026, 7, 1, tzinfo=datetime.timezone.utc),
    timezone=tz,
)

print("Fetching caceria events...")
events_raw = get_events(
    client=er,
    time_range=time_range,
    event_types=["caceria"],
    event_columns=["id", "time", "event_type", "event_category", "serial_number", "event_details", "geometry", "reported_by"],
    include_details=True,
    include_display_values=True,
    include_null_geometry=False,
    include_updates=False,
    include_related_events=False,
    force_point_geometry=True,
    raise_on_empty=False,
)
print(f"  {len(events_raw)} events fetched")

events_tz = convert_values_to_timezone(df=events_raw, timezone="UTC", columns=["time"])
events_resolved = process_events_details(df=events_tz, client=er, map_to_titles=True, ordered=True)
events_with_lat_lon = add_lat_lon_to_gdf(event_gdf=events_resolved)
events_fields = extract_hunting_fields(df=events_with_lat_lon)

print("Clustering hotspots (DBSCAN, radius_km=4.0, min_events=3)...")
clustered = cluster_hunting_hotspots(events_fields, radius_km=4.0, min_events=3)
hotspots = summarize_hotspots(clustered)
print(hotspots[["Agrupamento", "Eventos", "Locais ativos", "Referência descritiva predominante"]].to_string(index=False))

print("Drawing Histogram2d density chart...")
html = draw_hunting_density_chart(events_df=clustered, hotspots_df=hotspots)
OUT_HTML.write_text(html)
print(f"Written: {OUT_HTML}")

png_path = html_to_png(
    html_path=str(OUT_HTML),
    output_dir=str(SCRIPT_DIR),
    config=ScreenshotConfig(width=1280, height=720, full_page=True, device_scale_factor=2, wait_for_timeout=60000, timeout=0, max_concurrent_pages=1, serve_local_files=False),
)
print(f"PNG: {png_path}")
subprocess.run(["open", png_path])

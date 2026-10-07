"""Re-check the create_hexagon_layer density map against real parnaiguacu
data, after re-adding build_hotspot_label_points (removed when this
approach was first rejected in favor of matplotlib hexbin — see
draw_hunting_density_chart's docstring in _hunting_intelligence.py for why).

Fixes the earlier "big circles" bug from the first attempt: the hotspot
marker layer used get_radius=300 with ScatterplotLayerStyle's default
radius_units="pixels" (300px radius = 600px-wide circles). This version
uses radius_units="meters" with a real-world-scale radius instead.

Known, UNCHANGED limitation (the actual reason matplotlib was chosen):
HexagonLayerStyle exposes no color_range/domain control, so deck.gl/
d3-hexbin computes the hexagon color-to-count mapping entirely client-side
in the browser — there is no way to build a server-side legend for it that
is guaranteed accurate to what's rendered. Nothing in the library has
changed since the first attempt; re-confirm this by eye when reviewing the
output before deciding whether to adopt it.

Run with (from ICMBio-patrol_analysis/ecoscope-workflows-patrol-analysis-workflow,
using its already-installed ecoscope env; PYTHONPATH points at the
wd-partner-tasks-icmbio-hunting-intelligence worktree source so the tasks
are picked up without needing a package rebuild):

  cd /path/to/ICMBio-patrol_analysis/ecoscope-workflows-patrol-analysis-workflow
  PYTHONPATH=/path/to/wd-partner-tasks-icmbio-hunting-intelligence/src/ecoscope-workflows-ext-icmbio \
    pixi run -e default python /path/to/ICMBio-Territorial_Intelligence/.scripts/check_hotspot_hexagon_map_v2.py
"""
import datetime
import subprocess
import sys
from pathlib import Path

from ecoscope.platform.connections import EarthRangerConnection
from ecoscope.platform.tasks.filter._filter import TimeRange, TimezoneInfo
from ecoscope.platform.tasks.io._earthranger import get_events, process_events_details
from ecoscope.platform.tasks.results._map_utils import set_base_maps
from ecoscope.platform.tasks.results._pydeck import (
    HexagonLayerStyle,
    LegendFromDataframe,
    LegendStyle,
    ScatterplotLayerStyle,
    create_hexagon_layer,
    create_scatterplot_layer,
    draw_map,
)
from ecoscope.platform.tasks.transformation._conversion import convert_values_to_timezone

from ecoscope_workflows_ext_icmbio.tasks import (
    add_lat_lon_to_gdf,
    apply_qualitative_color_map,
    build_hotspot_label_points,
    cluster_hunting_hotspots,
    compute_view_from_geodataframes,
    extract_hunting_fields,
    summarize_hotspots,
)
from ecoscope_workflows_ext_icmbio.tasks._colormap import QualitativeColormap
from ecoscope_workflows_ext_icmbio.tasks._map_utils import ViewportSettings

# Hexagon bin radius (km) — now a CLI arg so different bin sizes can be
# compared side by side: `python check_hotspot_hexagon_map_v2.py 2.5`
HEX_RADIUS_KM = float(sys.argv[1]) if len(sys.argv) > 1 else 4.0
OUT_PATH = Path(__file__).parent / f"hotspot_hexagon_check_v2_{HEX_RADIUS_KM}km.html"

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

label_points = build_hotspot_label_points(hotspots)

print("Building point + hexagon + hotspot-marker layers...")
event_colormap = apply_qualitative_color_map(df=clustered, input_column_name="event_type_display", colormap=QualitativeColormap.Set2)
# Set1 (not Dark2): Dark2's olive/teal tones blended into the dark-green
# satellite basemap in the first render — Set1's more saturated
# red/blue/orange/pink palette stays legible against dark terrain.
hotspot_colormap = apply_qualitative_color_map(df=label_points, input_column_name="Agrupamento", colormap=QualitativeColormap.Set1)

point_layer = create_scatterplot_layer(
    geodataframe=event_colormap,
    layer_style=ScatterplotLayerStyle(get_fill_color="column_color", get_line_color="column_color"),
)
hexagon_layer = create_hexagon_layer(
    geodataframe=clustered,
    layer_style=HexagonLayerStyle(radius=HEX_RADIUS_KM * 1000, opacity=0.35, extruded=False),
)
# Fixed from the first attempt: radius_units="meters" (was the default
# "pixels", so get_radius=300 rendered as 600px-wide circles). 250m is a
# real-world-scale marker, clearly visible without swallowing the map.
# get_line_color is now a fixed white stroke (not "column_color") so every
# marker keeps a visible edge regardless of its fill color or what's
# underneath (satellite imagery, other hexagons, etc).
hotspot_marker_layer = create_scatterplot_layer(
    geodataframe=hotspot_colormap,
    layer_style=ScatterplotLayerStyle(
        get_fill_color="column_color",
        get_line_color=[255, 255, 255, 255],
        get_line_width=2,
        line_width_units="pixels",
        line_width_min_pixels=2,
        get_radius=250,
        radius_units="meters",
        radius_min_pixels=7,
        radius_max_pixels=22,
        stroked=True,
        filled=True,
    ),
    legend=LegendFromDataframe(title="Agrupamentos", label_column="label", color_column="column_color"),
)

view_state = compute_view_from_geodataframes(geodataframe=clustered, viewport=ViewportSettings())
base_maps = set_base_maps(base_maps=None)

html = draw_map(
    geo_layers=[point_layer, hexagon_layer, hotspot_marker_layer],
    tile_layers=base_maps,
    view_state=view_state,
    static=False,
    title=None,
    max_zoom=17,
    legend_style=LegendStyle(placement="bottom-right"),
    output_type="html",
)

OUT_PATH.write_text(html)
print(f"\nWritten: {OUT_PATH}")
subprocess.run(["open", str(OUT_PATH)])

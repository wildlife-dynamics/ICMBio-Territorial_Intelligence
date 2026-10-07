"""Render the 'Distribuição territorial e hotspots' density map (the
create_hexagon_layer combo that replaced the Plotly Histogram2d density
chart) against real parnaiguacu data, save it, and open it in a browser.

Confirms end-to-end, outside the full compiled workflow:
  - real events fetch -> field extraction -> DBSCAN hotspot clustering
  - raw event points (create_scatterplot_layer), rendered BELOW the
    hexagon layer so the hexagon aggregation draws on top of them
  - real hexagon binning (deck.gl HexagonLayer, via create_hexagon_layer)
  - one small colored marker per hotspot centroid (fed by
    build_hotspot_label_points), carrying a LegendFromDataframe legend
    ("Hotspot 1 (n=12)" etc.) instead of on-map text labels
  - all layers composited on a real basemap via draw_map

Run with (from ICMBio-patrol_analysis/ecoscope-workflows-patrol-analysis-workflow,
using its already-installed ecoscope env; PYTHONPATH points at the
wd-partner-tasks source so the new hunting-intelligence tasks are picked up
without needing a package rebuild):

  cd /path/to/ICMBio-patrol_analysis/ecoscope-workflows-patrol-analysis-workflow
  PYTHONPATH=/path/to/wd-partner-tasks/src/ecoscope-workflows-ext-icmbio \
    pixi run -e default python /path/to/ICMBio-Territorial_Intelligence/.scripts/check_hotspot_hexagon_map.py
"""
import datetime
import subprocess
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

OUT_PATH = Path(__file__).parent.parent / ".scripts" / "hotspot_hexagon_check.html"

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
# Same colormap step spec.yaml's general events map uses (single category
# here: "Caça") — reused so the raw points look consistent with the other
# map, colored/labeled the same way.
event_colormap = apply_qualitative_color_map(df=clustered, input_column_name="event_type_display", colormap=QualitativeColormap.Set2)

# One color per hotspot (by "Agrupamento", e.g. "Hotspot 1") — the "label"
# column (with the "(n=X)" count, from build_hotspot_label_points) is kept
# separate and used only for the legend's display text.
hotspot_colormap = apply_qualitative_color_map(df=label_points, input_column_name="Agrupamento", colormap=QualitativeColormap.Dark2)

point_layer = create_scatterplot_layer(
    geodataframe=event_colormap,
    layer_style=ScatterplotLayerStyle(get_fill_color="column_color", get_line_color="column_color"),
)
hexagon_layer = create_hexagon_layer(
    geodataframe=clustered,
    layer_style=HexagonLayerStyle(radius=4000, opacity=0.35, extruded=False),
)
hotspot_marker_layer = create_scatterplot_layer(
    geodataframe=hotspot_colormap,
    layer_style=ScatterplotLayerStyle(
        get_fill_color="column_color",
        get_line_color="column_color",
        get_radius=300,
        stroked=True,
        filled=True,
    ),
    legend=LegendFromDataframe(title="Agrupamentos", label_column="label", color_column="column_color"),
)

view_state = compute_view_from_geodataframes(geodataframe=clustered, viewport=ViewportSettings())
base_maps = set_base_maps(base_maps=None)

html = draw_map(
    # Order matters: layers listed first are drawn first (i.e. underneath
    # later ones) — points below the hexagon aggregation, hotspot markers
    # (small, legend-carrying) on top of everything.
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

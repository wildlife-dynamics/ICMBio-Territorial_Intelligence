"""'Outside the box' rethink of the interactive hotspot map: instead of a
deck.gl HexagonLayer density field (ambiguous, client-side-only color
legend — see draw_hunting_density_chart's docstring) or a plain colored
bubble map alone, this draws the DBSCAN clustering itself as a "spider
diagram":

  - a PathLayer line from every clustered event to its hotspot's centroid
    (visually showing cluster MEMBERSHIP directly, not an inferred density
    gradient)
  - raw event points (ScatterplotLayer), colored to match their hotspot
  - one centroid "bubble" per hotspot (ScatterplotLayer), radius scaled by
    event count (build_hotspot_label_points' radius_m), same color as its
    member events/lines
  - hover tooltips on both points and bubbles (tooltip_columns) showing
    real per-feature detail — genuinely interactive, not just pannable
  - noise events (not part of any hotspot) shown in neutral gray, no line

Colors are assigned ONCE (apply_qualitative_color_map on the full
clustered events' hotspot_label column) and then reused for the hotspot
bubbles via a label->color dict, rather than calling the colormap task
twice — guarantees the bubble color always matches its member events'
and connecting lines' color exactly.

Run with (from ICMBio-patrol_analysis/ecoscope-workflows-patrol-analysis-workflow,
using its already-installed ecoscope env; PYTHONPATH points at the
wd-partner-tasks-icmbio-hunting-intelligence worktree source):

  cd /path/to/ICMBio-patrol_analysis/ecoscope-workflows-patrol-analysis-workflow
  PYTHONPATH=/path/to/wd-partner-tasks-icmbio-hunting-intelligence/src/ecoscope-workflows-ext-icmbio \
    pixi run -e default python /path/to/ICMBio-Territorial_Intelligence/.scripts/check_hotspot_spider_map.py
"""
import datetime
import subprocess
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString

from ecoscope.platform.connections import EarthRangerConnection
from ecoscope.platform.tasks.filter._filter import TimeRange, TimezoneInfo
from ecoscope.platform.tasks.io._earthranger import get_events, process_events_details
from ecoscope.platform.tasks.results._map_utils import set_base_maps
from ecoscope.platform.tasks.results._pydeck import (
    LegendFromDataframe,
    LegendStyle,
    PathLayerStyle,
    ScatterplotLayerStyle,
    create_path_layer,
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

OUT_PATH = Path(__file__).parent / "hotspot_spider_map_check.html"

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

print("Assigning one consistent color per hotspot (events, lines, and bubbles share it)...")
event_colormap = apply_qualitative_color_map(df=clustered, input_column_name="hotspot_label", colormap=QualitativeColormap.Set1)
label_to_color = dict(zip(event_colormap["column_label"], event_colormap["column_color"]))
label_points["column_color"] = label_points["label"].map(label_to_color)

print("Building spider lines (event -> hotspot centroid)...")
hotspots_by_number = hotspots.set_index("hotspot_number")
member_events = event_colormap[event_colormap["hotspot_number"].notna()].copy()
lines = []
for _, row in member_events.iterrows():
    centroid = hotspots_by_number.loc[int(row["hotspot_number"])]
    lines.append(
        {
            "hotspot_label": row["hotspot_label"],
            "column_color": row["column_color"],
            "geometry": LineString([(row.geometry.x, row.geometry.y), (centroid["central_lon"], centroid["central_lat"])]),
        }
    )
spider_lines = gpd.GeoDataFrame(lines, crs="EPSG:4326")
print(f"  {len(spider_lines)} member-event lines (noise events excluded)")

line_layer = create_path_layer(
    geodataframe=spider_lines,
    layer_style=PathLayerStyle(get_color="column_color", get_width=1.5, width_min_pixels=1),
)

event_layer = create_scatterplot_layer(
    geodataframe=event_colormap,
    layer_style=ScatterplotLayerStyle(get_fill_color="column_color", get_line_color="column_color", get_radius=5),
    tooltip_columns=["time", "hotspot_label", "situacao_local", "tempo_estimado_display", "acao_tomada", "descricao_local"],
)

hotspot_marker_layer = create_scatterplot_layer(
    geodataframe=label_points,
    layer_style=ScatterplotLayerStyle(
        get_fill_color="column_color",
        get_line_color=[255, 255, 255, 255],
        get_line_width=2,
        line_width_units="pixels",
        line_width_min_pixels=2,
        get_radius="radius_m",
        radius_units="meters",
        radius_min_pixels=8,
        radius_max_pixels=40,
        stroked=True,
        filled=True,
    ),
    legend=LegendFromDataframe(title="Agrupamentos", label_column="label", color_column="column_color"),
    tooltip_columns=["Agrupamento", "Eventos", "Locais ativos", "Coordenada central", "Referência descritiva predominante"],
)

view_state = compute_view_from_geodataframes(geodataframe=clustered, viewport=ViewportSettings())
base_maps = set_base_maps(base_maps=None)

html = draw_map(
    # Bottom to top: lines first (so points/bubbles draw over them), then
    # raw events, then hotspot bubbles on top of everything.
    geo_layers=[line_layer, event_layer, hotspot_marker_layer],
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

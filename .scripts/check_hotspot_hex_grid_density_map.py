"""Seventh alternative, and the cleanest synthesis of everything tried so
far: our OWN hexagon binning, computed entirely in Python (standard axial
hex-grid math, pointy-top orientation — see Red Blob Games' reference for
the algorithm), rendered as a PolygonLayer — not deck.gl's HexagonLayer.

This sidesteps the original HexagonLayer problem at the root instead of
patching it: we compute exact event counts per hex cell ourselves, so the
color-to-count mapping (and therefore the legend) is exactly accurate —
nothing computed client-side, nothing guessed. Real hexagon shapes (not
squares, unlike the grid-cell attempt), a real basemap, hover tooltips
with exact counts, and DBSCAN hotspot markers on top for cluster
identification — the full feature set, with an honest legend.

Run with (from ICMBio-patrol_analysis/ecoscope-workflows-patrol-analysis-workflow,
using its already-installed ecoscope env; PYTHONPATH points at the
wd-partner-tasks-icmbio-hunting-intelligence worktree source):

  cd /path/to/ICMBio-patrol_analysis/ecoscope-workflows-patrol-analysis-workflow
  PYTHONPATH=/path/to/wd-partner-tasks-icmbio-hunting-intelligence/src/ecoscope-workflows-ext-icmbio \
    pixi run -e default python /path/to/ICMBio-Territorial_Intelligence/.scripts/check_hotspot_hex_grid_density_map.py [hex_size_km]
"""
import datetime
import math
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib.colors
from shapely.geometry import Polygon

from ecoscope.platform.connections import EarthRangerConnection
from ecoscope.platform.tasks.filter._filter import TimeRange, TimezoneInfo
from ecoscope.platform.tasks.io._earthranger import get_events, process_events_details
from ecoscope.platform.tasks.results._map_utils import set_base_maps
from ecoscope.platform.tasks.results._pydeck import (
    LegendFromDataframe,
    LegendSegment,
    LegendStyle,
    LegendValue,
    PolygonLayerStyle,
    ScatterplotLayerStyle,
    create_polygon_layer_pydeck,
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

HEX_SIZE_KM = float(sys.argv[1]) if len(sys.argv) > 1 else 2.0
OUT_PATH = Path(__file__).parent / f"hotspot_hex_grid_check_{HEX_SIZE_KM}km.html"

SQRT3 = math.sqrt(3)


def pixel_to_axial(x: float, y: float, size: float) -> tuple[int, int]:
    """Standard pointy-top pixel->axial hex coordinate conversion."""
    q = (SQRT3 / 3 * x - 1 / 3 * y) / size
    r = (2 / 3 * y) / size
    return _axial_round(q, r)


def _axial_round(q: float, r: float) -> tuple[int, int]:
    s = -q - r
    rq, rr, rs = round(q), round(r), round(s)
    q_diff, r_diff, s_diff = abs(rq - q), abs(rr - r), abs(rs - s)
    if q_diff > r_diff and q_diff > s_diff:
        rq = -rr - rs
    elif r_diff > s_diff:
        rr = -rq - rs
    return int(rq), int(rr)


def axial_to_pixel(q: int, r: int, size: float) -> tuple[float, float]:
    x = size * (SQRT3 * q + SQRT3 / 2 * r)
    y = size * (3 / 2 * r)
    return x, y


def hex_polygon(cx: float, cy: float, size: float) -> Polygon:
    points = []
    for i in range(6):
        angle = math.radians(60 * i - 30)
        points.append((cx + size * math.cos(angle), cy + size * math.sin(angle)))
    return Polygon(points)


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
hotspot_colormap = apply_qualitative_color_map(df=label_points, input_column_name="Agrupamento", colormap=QualitativeColormap.Set1)

print(f"Building our own {HEX_SIZE_KM}km hexagon grid, counting events per hex, computing colors server-side...")
size_deg = HEX_SIZE_KM / 111.0
coords = clustered.get_coordinates(ignore_index=True)
axial = coords.apply(lambda row: pixel_to_axial(row["x"], row["y"], size_deg), axis=1)
coords["q"] = axial.apply(lambda t: t[0])
coords["r"] = axial.apply(lambda t: t[1])
counts = coords.groupby(["q", "r"]).size().reset_index(name="count")

max_count = int(counts["count"].max())
cmap = matplotlib.colormaps["YlOrRd"]
norm = matplotlib.colors.Normalize(vmin=0, vmax=max_count)


def _color(count: int) -> list[int]:
    r, g, b, a = cmap(norm(count))
    return [int(r * 255), int(g * 255), int(b * 255), 190]


def _center_and_hex(row) -> Polygon:
    cx, cy = axial_to_pixel(int(row["q"]), int(row["r"]), size_deg)
    return hex_polygon(cx, cy, size_deg)


counts["fill_color"] = counts["count"].apply(_color)
counts["geometry"] = counts.apply(_center_and_hex, axis=1)
hex_gdf = gpd.GeoDataFrame(counts, crs="EPSG:4326")
print(f"  {len(hex_gdf)} non-empty hex cells, max count = {max_count}")

hex_layer = create_polygon_layer_pydeck(
    geodataframe=hex_gdf,
    layer_style=PolygonLayerStyle(get_fill_color="fill_color", stroked=False, filled=True),
    legend=LegendSegment(
        title="Eventos por hexágono",
        values=[
            LegendValue(label="1", color=matplotlib.colors.to_hex(cmap(norm(1)))),
            LegendValue(label=str(max(1, max_count // 2)), color=matplotlib.colors.to_hex(cmap(norm(max(1, max_count // 2))))),
            LegendValue(label=f"{max_count} (máximo)", color=matplotlib.colors.to_hex(cmap(norm(max_count)))),
        ],
    ),
    tooltip_columns=["count"],
)

event_layer = create_scatterplot_layer(
    geodataframe=clustered,
    layer_style=ScatterplotLayerStyle(get_fill_color=[44, 62, 80, 200], get_line_color=[255, 255, 255, 200], get_radius=4),
)

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
    tooltip_columns=["Agrupamento", "Eventos", "Locais ativos", "Coordenada central", "Referência descritiva predominante"],
)

view_state = compute_view_from_geodataframes(geodataframe=clustered, viewport=ViewportSettings())
base_maps = set_base_maps(base_maps=None)

html = draw_map(
    geo_layers=[hex_layer, event_layer, hotspot_marker_layer],
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

"""Fifth alternative: a manually-computed grid density layer, not deck.gl's
HexagonLayer. Matches the PRD's own literal wording for this table/map
more closely than any prior attempt — the PRD text says "(count of events
in defined grid system)", not a radius-based clustering method.

Unlike HexagonLayer (color computed client-side by d3-hexbin, no exact
legend possible), here WE compute the grid cell counts and colors in
Python before rendering, using matplotlib's YlOrRd colormap (same
palette as the original matplotlib hexbin chart) sampled against the
REAL max count in this dataset. That means the legend can show actual,
exact count ranges — fully accurate, nothing left to the browser to
compute.

Hotspot bubbles (DBSCAN clusters, build_hotspot_label_points) are layered
on top for the "agrupamentos" identification — the grid shows raw
density, the bubbles identify which concentrations qualify as an
analytical hotspot (>= min_events within radius_km), matching both the
PRD's grid wording AND the reference PDF's DBSCAN hotspot table/method.

Run with (from ICMBio-patrol_analysis/ecoscope-workflows-patrol-analysis-workflow,
using its already-installed ecoscope env; PYTHONPATH points at the
wd-partner-tasks-icmbio-hunting-intelligence worktree source):

  cd /path/to/ICMBio-patrol_analysis/ecoscope-workflows-patrol-analysis-workflow
  PYTHONPATH=/path/to/wd-partner-tasks-icmbio-hunting-intelligence/src/ecoscope-workflows-ext-icmbio \
    pixi run -e default python /path/to/ICMBio-Territorial_Intelligence/.scripts/check_hotspot_grid_density_map.py [cell_size_km]
"""
import datetime
import math
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib.cm
import matplotlib.colors
from shapely.geometry import box

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

CELL_SIZE_KM = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
OUT_PATH = Path(__file__).parent / f"hotspot_grid_density_check_{CELL_SIZE_KM}km.html"

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

print(f"Building {CELL_SIZE_KM}km grid, counting events per cell, computing colors server-side...")
# ~1 deg latitude = 111km everywhere; longitude degrees shrink with
# latitude, but for a single small park extent a fixed deg size derived
# from latitude-only is an acceptable approximation for this diagnostic.
cell_size_deg = CELL_SIZE_KM / 111.0
coords = clustered.get_coordinates(ignore_index=True)
cell_x = (coords["x"] / cell_size_deg).apply(math.floor)
cell_y = (coords["y"] / cell_size_deg).apply(math.floor)
counts = coords.assign(cell_x=cell_x, cell_y=cell_y).groupby(["cell_x", "cell_y"]).size().reset_index(name="count")

max_count = int(counts["count"].max())
cmap = matplotlib.colormaps["YlOrRd"]
norm = matplotlib.colors.Normalize(vmin=0, vmax=max_count)


def _cell_color(count: int) -> list[int]:
    r, g, b, a = cmap(norm(count))
    return [int(r * 255), int(g * 255), int(b * 255), 180]


counts["fill_color"] = counts["count"].apply(_cell_color)
counts["geometry"] = counts.apply(
    lambda row: box(
        row["cell_x"] * cell_size_deg,
        row["cell_y"] * cell_size_deg,
        (row["cell_x"] + 1) * cell_size_deg,
        (row["cell_y"] + 1) * cell_size_deg,
    ),
    axis=1,
)
grid_gdf = gpd.GeoDataFrame(counts, crs="EPSG:4326")
print(f"  {len(grid_gdf)} non-empty grid cells, max count = {max_count}")

grid_layer = create_polygon_layer_pydeck(
    geodataframe=grid_gdf,
    layer_style=PolygonLayerStyle(get_fill_color="fill_color", stroked=False, filled=True),
    legend=LegendSegment(
        title="Eventos por célula de grade",
        # Real, exact breaks over the actual count range in this data —
        # not an invented/approximate scale.
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
    geo_layers=[grid_layer, event_layer, hotspot_marker_layer],
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

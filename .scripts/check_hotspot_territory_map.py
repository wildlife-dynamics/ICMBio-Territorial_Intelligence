"""Third alternative for the interactive hotspot map: instead of a
HexagonLayer density field (ambiguous color legend) or spider lines
(rejected — too cluttered), draw each hotspot's actual spatial extent as a
shaded "territory" polygon (convex hull of its member events, buffered for
padding), colored per hotspot, with raw event points and a small centroid
marker on top — all pickable with hover tooltips.

No computed color-to-count mapping anywhere: each hotspot gets one fixed
categorical color (used consistently for its polygon fill/outline, member
event points, and centroid marker), so the legend is a simple, exact
color->name mapping — nothing ambiguous to caption.

Run with (from ICMBio-patrol_analysis/ecoscope-workflows-patrol-analysis-workflow,
using its already-installed ecoscope env; PYTHONPATH points at the
wd-partner-tasks-icmbio-hunting-intelligence worktree source):

  cd /path/to/ICMBio-patrol_analysis/ecoscope-workflows-patrol-analysis-workflow
  PYTHONPATH=/path/to/wd-partner-tasks-icmbio-hunting-intelligence/src/ecoscope-workflows-ext-icmbio \
    pixi run -e default python /path/to/ICMBio-Territorial_Intelligence/.scripts/check_hotspot_territory_map.py
"""
import datetime
import subprocess
from pathlib import Path

import geopandas as gpd

from ecoscope.platform.connections import EarthRangerConnection
from ecoscope.platform.tasks.filter._filter import TimeRange, TimezoneInfo
from ecoscope.platform.tasks.io._earthranger import get_events, process_events_details
from ecoscope.platform.tasks.results._map_utils import set_base_maps
from ecoscope.platform.tasks.results._pydeck import (
    LegendFromDataframe,
    LegendStyle,
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

OUT_PATH = Path(__file__).parent / "hotspot_territory_map_check.html"

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

print("Assigning one consistent color per hotspot...")
event_colormap = apply_qualitative_color_map(df=clustered, input_column_name="hotspot_label", colormap=QualitativeColormap.Set1)
label_to_color = dict(zip(event_colormap["column_label"], event_colormap["column_color"]))
label_points["column_color"] = label_points["label"].map(label_to_color)

print("Building per-hotspot territory polygons (convex hull of member events, buffered)...")
hotspots_by_number = hotspots.set_index("hotspot_number")
territories = []
for hotspot_number, group in event_colormap[event_colormap["hotspot_number"].notna()].groupby("hotspot_number", observed=True):
    hull = group.geometry.union_all().convex_hull.buffer(0.01)  # ~1km padding in degrees
    row = hotspots_by_number.loc[int(hotspot_number)]
    color = label_to_color[row["Agrupamento"] + f" (n={int(row['Eventos'])})"]
    territories.append(
        {
            "Agrupamento": row["Agrupamento"],
            "Eventos": int(row["Eventos"]),
            "Locais ativos": int(row["Locais ativos"]),
            "Coordenada central": row["Coordenada central"],
            "Referência descritiva predominante": row["Referência descritiva predominante"],
            "column_color": color,
            "fill_color": (color[0], color[1], color[2], 70),
            "geometry": hull,
        }
    )
territory_gdf = gpd.GeoDataFrame(territories, crs="EPSG:4326")

territory_layer = create_polygon_layer_pydeck(
    geodataframe=territory_gdf,
    layer_style=PolygonLayerStyle(
        get_fill_color="fill_color",
        get_line_color="column_color",
        get_line_width=2,
        line_width_units="pixels",
        line_width_min_pixels=2,
        stroked=True,
        filled=True,
    ),
    legend=LegendFromDataframe(title="Agrupamentos", label_column="Agrupamento", color_column="column_color"),
    tooltip_columns=["Agrupamento", "Eventos", "Locais ativos", "Coordenada central", "Referência descritiva predominante"],
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
        get_radius=150,
        radius_units="meters",
        radius_min_pixels=6,
        radius_max_pixels=14,
        stroked=True,
        filled=True,
    ),
    tooltip_columns=["Agrupamento", "Eventos", "Locais ativos", "Coordenada central", "Referência descritiva predominante"],
)

view_state = compute_view_from_geodataframes(geodataframe=clustered, viewport=ViewportSettings())
base_maps = set_base_maps(base_maps=None)

html = draw_map(
    geo_layers=[territory_layer, event_layer, hotspot_marker_layer],
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

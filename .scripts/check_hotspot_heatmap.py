"""Fourth alternative for the density/hotspot map: deck.gl's HeatmapLayer
instead of HexagonLayer. Not wrapped as a task anywhere in ecoscope's
pydeck module (no create_heatmap_layer exists) — this builds a
PydeckLayerDefinition by hand with a local HeatmapLayerStyle model, which
works because PydeckLayerDefinition is a plain dataclass (no runtime
Union-type validation) and draw_map's rendering loop only calls
model_dump() on whatever BaseModel is given as layer_style.

Why HeatmapLayer, specifically: unlike HexagonLayerStyle, deck.gl's
HeatmapLayer accepts an explicit `colorRange` (color_range here) — the
exact sequence of colors used for its gradient, low to high. That doesn't
give us an exact count-to-color domain (the actual intensity value at
each pixel is still computed GPU-side and we can't read it back), but it
means the legend doesn't have to claim one either: captioned as "Baixa ->
Alta densidade relativa" (low -> high relative density) using the *same*
color_range we explicitly set, the legend is now provably accurate to
what's rendered, with no invented count thresholds — which is all the PRD
itself asks for ("Densidade relativa ... exploratórios", explicitly NOT a
precise analysis, see the PRD methodology text).

Run with (from ICMBio-patrol_analysis/ecoscope-workflows-patrol-analysis-workflow,
using its already-installed ecoscope env; PYTHONPATH points at the
wd-partner-tasks-icmbio-hunting-intelligence worktree source):

  cd /path/to/ICMBio-patrol_analysis/ecoscope-workflows-patrol-analysis-workflow
  PYTHONPATH=/path/to/wd-partner-tasks-icmbio-hunting-intelligence/src/ecoscope-workflows-ext-icmbio \
    pixi run -e default python /path/to/ICMBio-Territorial_Intelligence/.scripts/check_hotspot_heatmap.py
"""
import datetime
import subprocess
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel
from pydantic.json_schema import SkipJsonSchema

from ecoscope.platform.connections import EarthRangerConnection
from ecoscope.platform.tasks.filter._filter import TimeRange, TimezoneInfo
from ecoscope.platform.tasks.io._earthranger import get_events, process_events_details
from ecoscope.platform.tasks.results._map_utils import set_base_maps
from ecoscope.platform.tasks.results._pydeck import (
    LayerStyleBase,
    LegendFromDataframe,
    LegendSegment,
    LegendStyle,
    LegendValue,
    PydeckLayerDefinition,
    ScatterplotLayerStyle,
    create_scatterplot_layer,
    draw_map,
)
from ecoscope.platform.annotations import AdvancedField
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


class HeatmapLayerStyle(LayerStyleBase):
    """Not in ecoscope's _pydeck module — hand-rolled here, matching its
    existing LayerStyleBase pattern. Field names/defaults follow deck.gl's
    own HeatmapLayer API (https://deck.gl/docs/api-reference/aggregation-layers/heatmap-layer)."""

    get_position: Annotated[str, AdvancedField(default="geometry.coordinates")] = "geometry.coordinates"
    get_weight: Annotated[str | float | SkipJsonSchema[None], AdvancedField(default=1)] = 1
    radius_pixels: Annotated[float, AdvancedField(default=40)] = 40
    intensity: Annotated[float, AdvancedField(default=1)] = 1
    threshold: Annotated[float, AdvancedField(default=0.03)] = 0.03
    aggregation: Annotated[str, AdvancedField(default="SUM")] = "SUM"
    # Yellow -> orange -> red, matching the matplotlib hexbin chart's own
    # "YlOrRd" colormap for visual consistency across the two approaches.
    color_range: Annotated[list[list[int]], AdvancedField(default=None)] = [
        [255, 255, 204],
        [255, 237, 160],
        [254, 217, 118],
        [254, 178, 76],
        [253, 141, 60],
        [240, 59, 32],
        [189, 0, 38],
    ]


OUT_PATH = Path(__file__).parent / "hotspot_heatmap_check.html"

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

print("Building point + heatmap + hotspot-marker layers...")
event_colormap = apply_qualitative_color_map(df=clustered, input_column_name="event_type_display", colormap=QualitativeColormap.Set2)
hotspot_colormap = apply_qualitative_color_map(df=label_points, input_column_name="Agrupamento", colormap=QualitativeColormap.Set1)

point_layer = create_scatterplot_layer(
    geodataframe=event_colormap,
    layer_style=ScatterplotLayerStyle(get_fill_color="column_color", get_line_color="column_color", get_radius=5),
)

heatmap_layer = PydeckLayerDefinition(
    layer_type="HeatmapLayer",
    layer_style=HeatmapLayerStyle(),
    legend=LegendSegment(
        title="Densidade relativa de registros",
        values=[
            LegendValue(label="Baixa", color="#ffffcc"),
            LegendValue(label="Média", color="#fd8d3c"),
            LegendValue(label="Alta", color="#bd0026"),
        ],
    ),
    geodataframe=clustered,
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
    geo_layers=[heatmap_layer, point_layer, hotspot_marker_layer],
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

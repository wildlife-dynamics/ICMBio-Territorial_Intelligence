"""Sixth alternative: deck.gl HexagonLayer, but with an explicit
color_domain/color_range — fields that exist in real deck.gl
(https://deck.gl/docs/api-reference/aggregation-layers/hexagon-layer)
but were never exposed by ecoscope's HexagonLayerStyle wrapper model.

HexagonLayerStyle is just a pydantic BaseModel and PydeckLayerDefinition
is a plain dataclass with no runtime Union validation, so a local subclass
adding color_range/color_domain flows straight through create_hexagon_layer
and draw_map's rendering (which only calls .model_dump() on whatever
BaseModel instance it's given).

Why this solves the original problem: HexagonLayer's default color
aggregation is a plain SUM of weight (1 per point) = point COUNT per bin,
a deterministic integer, not a continuous kernel like HeatmapLayer. With
an explicit color_domain=[0, max_count], deck.gl's color-to-count mapping
becomes exactly what we specify, and we already have a principled,
non-invented value for max_count: the size of our largest identified
hotspot (summarize_hotspots' own "Eventos" column) — so the legend's
labeled range is tied directly to numbers already shown in the hotspots
table elsewhere in the report, not a guess.

Run with (from ICMBio-patrol_analysis/ecoscope-workflows-patrol-analysis-workflow,
using its already-installed ecoscope env; PYTHONPATH points at the
wd-partner-tasks-icmbio-hunting-intelligence worktree source):

  cd /path/to/ICMBio-patrol_analysis/ecoscope-workflows-patrol-analysis-workflow
  PYTHONPATH=/path/to/wd-partner-tasks-icmbio-hunting-intelligence/src/ecoscope-workflows-ext-icmbio \
    pixi run -e default python /path/to/ICMBio-Territorial_Intelligence/.scripts/check_hotspot_hexagon_colordomain.py [radius_km]
"""
import datetime
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import matplotlib.colors
from pydantic.json_schema import SkipJsonSchema

from ecoscope.platform.connections import EarthRangerConnection
from ecoscope.platform.annotations import AdvancedField
from ecoscope.platform.tasks.filter._filter import TimeRange, TimezoneInfo
from ecoscope.platform.tasks.io._earthranger import get_events, process_events_details
from ecoscope.platform.tasks.results._map_utils import set_base_maps
from ecoscope.platform.tasks.results._pydeck import (
    HexagonLayerStyle,
    LegendFromDataframe,
    LegendSegment,
    LegendStyle,
    LegendValue,
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


class HexagonLayerStyleWithColorDomain(HexagonLayerStyle):
    """HexagonLayerStyle plus deck.gl's real colorRange/colorDomain
    fields (not in ecoscope's model) — see module docstring."""

    color_range: Annotated[list[list[int]], AdvancedField(default=None)] = [
        [255, 255, 204],
        [255, 237, 160],
        [254, 217, 118],
        [254, 178, 76],
        [253, 141, 60],
        [240, 59, 32],
        [189, 0, 38],
    ]
    color_domain: Annotated[list[float] | SkipJsonSchema[None], AdvancedField(default=None)] = None


HEX_RADIUS_KM = float(sys.argv[1]) if len(sys.argv) > 1 else 4.0
OUT_PATH = Path(__file__).parent / f"hotspot_hexagon_colordomain_check_{HEX_RADIUS_KM}km.html"

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

print(f"Clustering hotspots (DBSCAN, radius_km={HEX_RADIUS_KM}, min_events=3)...")
clustered = cluster_hunting_hotspots(events_fields, radius_km=HEX_RADIUS_KM, min_events=3)
hotspots = summarize_hotspots(clustered)
print(hotspots[["Agrupamento", "Eventos", "Locais ativos", "Referência descritiva predominante"]].to_string(index=False))

max_count = int(hotspots["Eventos"].max()) if len(hotspots) else 1
print(f"Using color_domain=[0, {max_count}] (size of the largest identified hotspot)")

label_points = build_hotspot_label_points(hotspots)

print("Building point + hexagon (explicit color_domain) + hotspot-marker layers...")
event_colormap = apply_qualitative_color_map(df=clustered, input_column_name="event_type_display", colormap=QualitativeColormap.Set2)
hotspot_colormap = apply_qualitative_color_map(df=label_points, input_column_name="Agrupamento", colormap=QualitativeColormap.Set1)

point_layer = create_scatterplot_layer(
    geodataframe=event_colormap,
    layer_style=ScatterplotLayerStyle(get_fill_color="column_color", get_line_color="column_color", get_radius=5),
)

hexagon_layer = create_hexagon_layer(
    geodataframe=clustered,
    layer_style=HexagonLayerStyleWithColorDomain(
        radius=HEX_RADIUS_KM * 1000,
        opacity=0.6,
        extruded=False,
        color_domain=[0, max_count],
    ),
    legend=LegendSegment(
        title="Eventos por hexágono",
        values=[
            LegendValue(label="1", color=matplotlib.colors.to_hex([c / 255 for c in [255, 255, 204]])),
            LegendValue(label=str(max(1, max_count // 2)), color=matplotlib.colors.to_hex([c / 255 for c in [253, 141, 60]])),
            LegendValue(label=f"{max_count} (maior agrupamento)", color=matplotlib.colors.to_hex([c / 255 for c in [189, 0, 38]])),
        ],
    ),
    tooltip_columns=["event_type_display"],
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

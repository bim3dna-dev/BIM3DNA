# -*- coding: utf-8 -*-
__title__ = "Smart Vertical\nPipe Tags"
__author__ = "BIM3DNA"
__version__ = "1.0"
__doc__ = "Scan vertical pipes in the active view and suggest correct vertical pipe tag types by level span."

# =========================
# Config
# =========================
XY_VERTICAL_TOL_MM = 25.0
VERTICAL_MIN_DEGREES = 75.0
MIN_VERTICAL_HEIGHT_MM = 100.0
LEVEL_TOL_MM = 50.0
MIN_STORY_HEIGHT_MM = 500.0
DEFAULT_TOP_BAND_MM = 4000.0
DEFAULT_BOTTOM_BAND_MM = 500.0
TAG_LEVEL_ZONE_BELOW_MM = 750.0
TAG_LEVEL_ZONE_ABOVE_MM = 750.0
EXISTING_TAG_PROXIMITY_TOL_MM = 100.0
TAG_OVERLAP_XY_TOL_MM = 100.0
DEFAULT_DIRECTION = "DOWN"
TARGET_TAG_FAMILY_NAME = "M_Vertical_Pipe_Tag_MEPcontent"
CONFIG_FILE_NAME = "smart_vertical_pipe_tags_config.json"
INCLUDE_DEPENDENT_VIEWS = False
MAX_VIEWS_FOR_BATCH_SCAN_WARNING = 50

STATUS_VERTICAL_CANDIDATE = "VERTICAL_CANDIDATE"
STATUS_SKIPPED_NO_LOCATION = "SKIPPED_NO_LOCATION"
STATUS_SKIPPED_NOT_VERTICAL = "SKIPPED_NOT_VERTICAL"
STATUS_SKIPPED_TOO_SHORT = "SKIPPED_TOO_SHORT"
STATUS_OUTSIDE_ACTIVE_LEVEL_BAND = "OUTSIDE_ACTIVE_LEVEL_BAND"
STATUS_SKIPPED_ALREADY_TAGGED = "SKIPPED_ALREADY_TAGGED"
STATUS_OUTSIDE_ACTIVE_TAG_ZONE = "OUTSIDE_ACTIVE_TAG_ZONE"
STATUS_EXISTING_TAG_OUTSIDE_ACTIVE_TAG_ZONE = "EXISTING_TAG_OUTSIDE_ACTIVE_TAG_ZONE"
STATUS_TAG_CREATED = "TAG_CREATED"
STATUS_TAG_FAILED = "TAG_FAILED"

INTENT_GOING_DOWN = "GOING_DOWN"
INTENT_COMING_DOWN = "COMING_DOWN"
INTENT_COMING_TOGETHER = "COMING_TOGETHER"
INTENT_COMING_UP = "COMING_UP"
INTENT_CONTINUOUS_DOWN = "CONTINUOUS_DOWN"
INTENT_GOING_UP = "GOING_UP"
INTENT_CONTINUOUS_UP = "CONTINUOUS_UP"
INTENT_GOING_OUT = "GOING_OUT"
INTENT_THROUGH_LEVEL = "THROUGH_LEVEL"
INTENT_STARTS_HERE_GOES_DOWN = "STARTS_HERE_GOES_DOWN"
INTENT_STARTS_HERE_GOES_UP = "STARTS_HERE_GOES_UP"
INTENT_OUTSIDE_ACTIVE_LEVEL_BAND = "OUTSIDE_ACTIVE_LEVEL_BAND"
INTENT_UNKNOWN = "UNKNOWN"

TAG_TYPE_CANDIDATES = {
    INTENT_COMING_DOWN: ["Coming Down"],
    INTENT_COMING_TOGETHER: ["Coming Together"],
    INTENT_COMING_UP: ["Coming Up"],
    INTENT_CONTINUOUS_DOWN: ["Continuous Down"],
    INTENT_CONTINUOUS_UP: ["Continuous Up"],
    INTENT_GOING_DOWN: ["Going Down"],
    INTENT_GOING_OUT: ["Going Out"],
    INTENT_GOING_UP: ["Going Up"],
    INTENT_STARTS_HERE_GOES_DOWN: ["Going Down"],
    INTENT_STARTS_HERE_GOES_UP: ["Going Up"],
    INTENT_THROUGH_LEVEL: ["Continuous Down"],
}

CONFIG_TYPE_INTENTS = [
    INTENT_COMING_DOWN,
    INTENT_COMING_TOGETHER,
    INTENT_COMING_UP,
    INTENT_CONTINUOUS_DOWN,
    INTENT_CONTINUOUS_UP,
    INTENT_GOING_DOWN,
    INTENT_GOING_OUT,
    INTENT_GOING_UP,
]

REQUIRED_COMPATIBLE_TYPE_NAMES = [
    "Coming Down",
    "Coming Up",
    "Continuous Down",
    "Continuous Up",
    "Going Down",
    "Going Up",
]

EXPECTED_VERTICAL_TYPE_NAMES = [
    "Coming Down",
    "Coming Together",
    "Coming Up",
    "Continuous Down",
    "Continuous Up",
    "Going Down",
    "Going Out",
    "Going Up",
]

DEFAULT_TYPE_MAPPING = {
    INTENT_COMING_DOWN: "Coming Down",
    INTENT_COMING_TOGETHER: "Coming Together",
    INTENT_COMING_UP: "Coming Up",
    INTENT_CONTINUOUS_DOWN: "Continuous Down",
    INTENT_CONTINUOUS_UP: "Continuous Up",
    INTENT_GOING_DOWN: "Going Down",
    INTENT_GOING_OUT: "Going Out",
    INTENT_GOING_UP: "Going Up",
}

DEFAULT_CONFIG = {
    "target_family_name": TARGET_TAG_FAMILY_NAME,
    "target_type_mapping": DEFAULT_TYPE_MAPPING,
    "tag_level_zone_below_mm": TAG_LEVEL_ZONE_BELOW_MM,
    "tag_level_zone_above_mm": TAG_LEVEL_ZONE_ABOVE_MM,
}


# =========================
# Revit context
# =========================
from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    Element,
    ElementId,
    FamilySymbol,
    IFamilyLoadOptions,
    FilteredElementCollector,
    IndependentTag,
    Level,
    LocationCurve,
    Reference,
    TagMode,
    TagOrientation,
    Transaction,
    ViewPlan,
    XYZ,
)
from Autodesk.Revit.UI import (
    TaskDialog,
    TaskDialogCommonButtons,
    TaskDialogResult,
)
import clr

clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")

from System.Collections.Generic import List
from System.Windows.Forms import (
    Form,
    Button,
    Label,
    ListBox,
    OpenFileDialog,
    DialogResult,
    FormBorderStyle,
    FormStartPosition,
    SelectionMode,
)
from System.Drawing import Point, Size
import math
import os
import json

try:
    from pyrevit import script
except Exception:
    script = None

try:
    from pyrevit import forms
except Exception:
    forms = None

uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document
CONFIG = None


# =========================
# Helpers
# =========================
def mm_to_ft(mm_value):
    return float(mm_value) / 304.8


def ft_to_mm(ft_value):
    if ft_value is None:
        return None
    return float(ft_value) * 304.8


def fmt_mm(ft_value):
    if ft_value is None:
        return "-"
    return str(round(ft_to_mm(ft_value), 1))


def fmt_bool(value):
    return str(bool(value)).lower()


def safe_element_name(elem):
    if elem is None:
        return ""
    try:
        value = Element.Name.GetValue(elem)
        if value:
            return value
    except Exception:
        pass
    try:
        if elem.Name:
            return elem.Name
    except Exception:
        pass
    return ""


def get_type_name(elem):
    try:
        type_elem = doc.GetElement(elem.GetTypeId())
    except Exception:
        type_elem = None
    name_value = safe_element_name(type_elem)
    if name_value:
        return name_value
    return "<Unknown Type>"


def get_pipe_system_name(pipe):
    try:
        system = pipe.MEPSystem
        if system is not None and system.Name:
            return system.Name
    except Exception:
        pass
    try:
        param = pipe.get_Parameter(BuiltInParameter.RBS_SYSTEM_NAME_PARAM)
        if param is not None and param.AsString():
            return param.AsString()
    except Exception:
        pass
    return ""


def output_emit(output, text):
    if output is not None:
        try:
            output.print_md(text)
            return
        except Exception:
            pass
    print(text)


def linkify(output, element_id):
    try:
        return output.linkify(ElementId(int(element_id)))
    except Exception:
        return str(element_id)


def format_element_ids(values, output=None):
    if not values:
        return "UNKNOWN"
    text_values = []
    for value in values:
        if value is None:
            continue
        if output is not None:
            text_values.append(linkify(output, value))
        else:
            text_values.append(str(value))
    if not text_values:
        return "UNKNOWN"
    return ", ".join(text_values)


def format_existing_tag_ids(row, output=None):
    tags = row.get("existing_tags") or []
    if not tags and row.get("existing_tag") is not None:
        tags = [row.get("existing_tag")]
    return format_element_ids([tag.get("id") for tag in tags], output)


def format_existing_tag_types(row):
    tags = row.get("existing_tags") or []
    if not tags and row.get("existing_tag") is not None:
        tags = [row.get("existing_tag")]
    values = []
    for tag in tags:
        values.append(
            "{} / {}".format(
                tag.get("family") or "UNKNOWN", tag.get("type") or "UNKNOWN"
            )
        )
    if values:
        return "; ".join(values)
    return "UNKNOWN"


def format_point_mm(point):
    if point is None:
        return ["-", "-", "-"]
    return [fmt_mm(point.X), fmt_mm(point.Y), fmt_mm(point.Z)]


def format_xy_mm(point):
    if point is None:
        return "-/-"
    return "{}/{}".format(fmt_mm(point.X), fmt_mm(point.Y))


def unique_join(values):
    result = []
    for value in values:
        if value is None:
            continue
        text = str(value)
        if text not in result:
            result.append(text)
    if not result:
        return "-"
    return ", ".join(result)


def emit_table(output, headers, rows):
    output_emit(output, "| {} |".format(" | ".join(headers)))
    output_emit(output, "| {} |".format(" | ".join(["---" for _h in headers])))
    if rows:
        for row in rows:
            output_emit(output, "| {} |".format(" | ".join([str(x) for x in row])))
    else:
        output_emit(output, "| {} |".format(" | ".join(["-" for _h in headers])))


def show_dialog(message):
    TaskDialog.Show("Smart Vertical Pipe Tags", message)


def get_bundle_dir():
    try:
        return os.path.dirname(__file__)
    except Exception:
        return os.getcwd()


def get_config_path():
    return os.path.join(get_bundle_dir(), CONFIG_FILE_NAME)


def clone_default_config():
    return {
        "target_family_name": TARGET_TAG_FAMILY_NAME,
        "target_type_mapping": dict(DEFAULT_TYPE_MAPPING),
        "tag_level_zone_below_mm": TAG_LEVEL_ZONE_BELOW_MM,
        "tag_level_zone_above_mm": TAG_LEVEL_ZONE_ABOVE_MM,
        "last_loaded_family_path": "",
        "last_selected_family_compatibility": "",
        "last_selected_family_missing_types": [],
    }


def normalize_config(config):
    merged = clone_default_config()
    if isinstance(config, dict):
        for key in [
            "target_family_name",
            "tag_level_zone_below_mm",
            "tag_level_zone_above_mm",
            "last_loaded_family_path",
            "last_selected_family_compatibility",
        ]:
            if key in config and config.get(key) is not None:
                merged[key] = config.get(key)
        if isinstance(config.get("last_selected_family_missing_types"), list):
            merged["last_selected_family_missing_types"] = config.get(
                "last_selected_family_missing_types"
            )
        mapping = dict(DEFAULT_TYPE_MAPPING)
        if isinstance(config.get("target_type_mapping"), dict):
            for intent, value in config.get("target_type_mapping").items():
                if value:
                    mapping[str(intent)] = str(value)
        merged["target_type_mapping"] = mapping
    return merged


def load_config():
    path = get_config_path()
    try:
        if os.path.exists(path):
            with open(path, "r") as config_file:
                return normalize_config(json.load(config_file))
    except Exception as ex:
        print("Smart Vertical Pipe Tags config read failed: {}".format(ex))
    return clone_default_config()


def save_config(config):
    path = get_config_path()
    try:
        with open(path, "w") as config_file:
            json.dump(normalize_config(config), config_file, indent=2, sort_keys=True)
        return True
    except Exception as ex:
        print("Smart Vertical Pipe Tags config write failed: {}".format(ex))
        return False


def get_config():
    global CONFIG
    if CONFIG is None:
        CONFIG = load_config()
    return CONFIG


def get_target_family_name():
    return get_config().get("target_family_name") or TARGET_TAG_FAMILY_NAME


def get_configured_type_name(intent):
    mapping = get_config().get("target_type_mapping") or {}
    return mapping.get(intent) or DEFAULT_TYPE_MAPPING.get(intent)


def get_tag_zone_below_mm():
    try:
        return float(get_config().get("tag_level_zone_below_mm"))
    except Exception:
        return TAG_LEVEL_ZONE_BELOW_MM


def get_tag_zone_above_mm():
    try:
        return float(get_config().get("tag_level_zone_above_mm"))
    except Exception:
        return TAG_LEVEL_ZONE_ABOVE_MM


ACTION_OPTIONS = [
    ("Scan active view vertical pipes", {"mode": "scan", "scope": "active_view"}),
    ("Scan selected vertical pipes", {"mode": "scan", "scope": "selected"}),
    (
        "Place missing tags for selected vertical pipes",
        {"mode": "place_missing", "scope": "selected"},
    ),
    (
        "Place missing tags for active view vertical pipes",
        {"mode": "place_missing", "scope": "active_view"},
    ),
    (
        "Choose loaded vertical pipe tag family",
        {"mode": "choose_family", "scope": None},
    ),
    ("Load vertical pipe tag family from .rfa", {"mode": "load_family", "scope": None}),
    (
        "Scan all floor plan views for vertical pipe tag candidates",
        {"mode": "scan_all_views", "scope": "all_floor_plans"},
    ),
    ("Exit", {"mode": "exit", "scope": None}),
]


def choose_from_list(title, prompt, labels):
    if forms is not None:
        try:
            selected = forms.SelectFromList.show(
                labels,
                title=title,
                button_name="Select",
                multiselect=False,
            )
            if selected:
                return selected
            return None
        except Exception:
            pass

    form = Form()
    form.Text = title
    form.Width = 520
    form.Height = 360
    form.FormBorderStyle = FormBorderStyle.FixedDialog
    form.StartPosition = FormStartPosition.CenterScreen
    label = Label()
    label.Text = prompt
    label.Location = Point(12, 12)
    label.Size = Size(480, 32)
    listbox = ListBox()
    listbox.Location = Point(12, 50)
    listbox.Size = Size(480, 220)
    listbox.SelectionMode = SelectionMode.One
    for item in labels:
        listbox.Items.Add(item)
    if listbox.Items.Count > 0:
        listbox.SelectedIndex = 0
    ok_button = Button()
    ok_button.Text = "OK"
    ok_button.Location = Point(330, 285)
    ok_button.DialogResult = DialogResult.OK
    cancel_button = Button()
    cancel_button.Text = "Cancel"
    cancel_button.Location = Point(415, 285)
    cancel_button.DialogResult = DialogResult.Cancel
    form.Controls.Add(label)
    form.Controls.Add(listbox)
    form.Controls.Add(ok_button)
    form.Controls.Add(cancel_button)
    form.AcceptButton = ok_button
    form.CancelButton = cancel_button
    result = form.ShowDialog()
    if result == DialogResult.OK and listbox.SelectedItem is not None:
        return str(listbox.SelectedItem)
    return None


def choose_action():
    labels = [item[0] for item in ACTION_OPTIONS]
    selected = choose_from_list(
        "Smart Vertical Pipe Tags",
        "Choose action",
        labels,
    )
    if selected is None:
        return None
    for label, action in ACTION_OPTIONS:
        if label == selected:
            return dict(action)
    return None


def confirm_active_view_placement():
    dialog = TaskDialog("Smart Vertical Pipe Tags")
    dialog.MainInstruction = "Place missing vertical pipe tags"
    dialog.MainContent = (
        "Place missing vertical pipe tags for all valid candidates in the active view? "
        "Existing vertical pipe tags will not be modified."
    )
    dialog.CommonButtons = TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No
    return dialog.Show() == TaskDialogResult.Yes


def confirm_yes_no(title, instruction, content):
    dialog = TaskDialog(title)
    dialog.MainInstruction = instruction
    dialog.MainContent = content
    dialog.CommonButtons = TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No
    return dialog.Show() == TaskDialogResult.Yes


def normalize_name(name):
    value = (name or "").lower()
    for old in ["-", "_", " "]:
        value = value.replace(old, "")
    return value


def is_target_vertical_pipe_tag_family(family_name):
    normalized = normalize_name(family_name)
    target = normalize_name(get_target_family_name())
    if target in normalized:
        return True
    if normalize_name(TARGET_TAG_FAMILY_NAME) in normalized:
        return True
    return (
        "vertical" in normalized
        and "pipe" in normalized
        and "tag" in normalized
        and "mepcontent" in normalized
    )


# =========================
# View / Level Context
# =========================
def get_active_view_level(view):
    try:
        if view.GenLevel is not None:
            return view.GenLevel
    except Exception:
        pass
    try:
        param = view.get_Parameter(BuiltInParameter.PLAN_VIEW_LEVEL)
        if param is not None:
            level_id = param.AsElementId()
            if level_id and level_id.IntegerValue > 0:
                return doc.GetElement(level_id)
    except Exception:
        pass
    return None


def collect_levels():
    levels = list(FilteredElementCollector(doc).OfClass(Level).ToElements())
    levels.sort(key=lambda lvl: lvl.Elevation)
    return levels


def build_level_info(current_level):
    levels = collect_levels()
    previous_level = None
    next_level = None
    ignored_levels = []
    current_elev = current_level.Elevation
    min_story_height = mm_to_ft(MIN_STORY_HEIGHT_MM)
    for level in levels:
        if level.Id.IntegerValue == current_level.Id.IntegerValue:
            continue
        delta = level.Elevation - current_elev
        if delta > 0 and delta <= min_story_height:
            ignored_levels.append(
                {
                    "level": level,
                    "reason": "Ignored as near-duplicate level above current level.",
                }
            )
            continue
        if delta < 0 and abs(delta) <= min_story_height:
            ignored_levels.append(
                {
                    "level": level,
                    "reason": "Ignored as near-duplicate level below current level.",
                }
            )
            continue
        if level.Elevation < current_elev:
            previous_level = level
        elif level.Elevation > current_elev and next_level is None:
            next_level = level
    tol = mm_to_ft(LEVEL_TOL_MM)
    if next_level is not None:
        band_top = next_level.Elevation
    else:
        band_top = current_elev + mm_to_ft(DEFAULT_TOP_BAND_MM)
    if previous_level is not None:
        previous_story_elevation = previous_level.Elevation
    else:
        previous_story_elevation = current_elev - mm_to_ft(DEFAULT_BOTTOM_BAND_MM)
    return {
        "current_level": current_level,
        "current_elevation": current_elev,
        "levels": levels,
        "previous_level": previous_level,
        "next_level": next_level,
        "tolerance": tol,
        "band_bottom": current_elev - mm_to_ft(DEFAULT_BOTTOM_BAND_MM),
        "band_top": band_top,
        "tag_zone_bottom": current_elev - mm_to_ft(get_tag_zone_below_mm()),
        "tag_zone_top": current_elev + mm_to_ft(get_tag_zone_above_mm()),
        "previous_story_elevation": previous_story_elevation,
        "ignored_levels": ignored_levels,
    }


def get_recommended_tag_level(pipe_min_z, pipe_max_z, levels):
    if pipe_min_z is None or pipe_max_z is None or not levels:
        return {
            "level": None,
            "name": "-",
            "elevation_mm": "-",
            "reason": "No level recommendation available.",
        }
    center_z = (pipe_min_z + pipe_max_z) / 2.0
    below = mm_to_ft(get_tag_zone_below_mm())
    above = mm_to_ft(get_tag_zone_above_mm())
    matches = []
    nearest_level = None
    nearest_distance = None
    for level in levels:
        distance = abs(level.Elevation - center_z)
        if nearest_distance is None or distance < nearest_distance:
            nearest_level = level
            nearest_distance = distance
        zone_bottom = level.Elevation - below
        zone_top = level.Elevation + above
        if pipe_min_z <= zone_top and pipe_max_z >= zone_bottom:
            matches.append({"level": level, "distance": distance})
    if matches:
        matches.sort(key=lambda item: item.get("distance"))
        level = matches[0].get("level")
        return {
            "level": level,
            "name": safe_element_name(level),
            "elevation_mm": fmt_mm(level.Elevation),
            "reason": "Pipe intersects this level's vertical pipe tag zone.",
        }
    if nearest_level is not None:
        return {
            "level": nearest_level,
            "name": safe_element_name(nearest_level),
            "elevation_mm": fmt_mm(nearest_level.Elevation),
            "reason": "No level tag zone intersects this pipe; nearest level by elevation.",
        }
    return {
        "level": None,
        "name": "-",
        "elevation_mm": "-",
        "reason": "No level recommendation available.",
    }


# =========================
# Pipe Geometry
# =========================
def get_curve_points(pipe):
    try:
        loc = pipe.Location
        if not isinstance(loc, LocationCurve):
            return None, None
        curve = loc.Curve
        if curve is None:
            return None, None
        return curve.GetEndPoint(0), curve.GetEndPoint(1)
    except Exception:
        return None, None


def get_pipe_geometry_info(pipe):
    p0, p1 = get_curve_points(pipe)
    if p0 is None or p1 is None:
        return None
    dx = p1.X - p0.X
    dy = p1.Y - p0.Y
    dz = p1.Z - p0.Z
    horizontal_run = (dx * dx + dy * dy) ** 0.5
    length = (dx * dx + dy * dy + dz * dz) ** 0.5
    if horizontal_run <= 1e-9:
        angle = 90.0
    else:
        angle = math.degrees(math.atan2(abs(dz), horizontal_run))
    is_vertical_candidate = (
        horizontal_run <= mm_to_ft(XY_VERTICAL_TOL_MM) or angle >= VERTICAL_MIN_DEGREES
    )
    return {
        "start": p0,
        "end": p1,
        "min_z": min(p0.Z, p1.Z),
        "max_z": max(p0.Z, p1.Z),
        "dx": dx,
        "dy": dy,
        "dz": dz,
        "horizontal_run": horizontal_run,
        "length": length,
        "angle_from_horizontal_degrees": angle,
        "is_vertical_candidate": is_vertical_candidate,
    }


def build_pipe_span(geometry, level_info):
    tol = level_info["tolerance"]
    level_z = level_info["current_elevation"]
    min_z = geometry["min_z"]
    max_z = geometry["max_z"]
    start_z = geometry["start"].Z
    end_z = geometry["end"].Z
    return {
        "pipe_min_z": min_z,
        "pipe_max_z": max_z,
        "height": max_z - min_z,
        "crosses_current_level": min_z <= level_z + tol and max_z >= level_z - tol,
        "crosses_floor_band": min_z < level_info["band_top"]
        and max_z > level_info["band_bottom"],
        "crosses_active_tag_zone": min_z <= level_info["tag_zone_top"]
        and max_z >= level_info["tag_zone_bottom"],
        "starts_near_current_level": abs(start_z - level_z) <= tol
        or abs(end_z - level_z) <= tol,
        "ends_near_current_level": abs(start_z - level_z) <= tol
        or abs(end_z - level_z) <= tol,
        "starts_above_current_level": start_z > level_z + tol or end_z > level_z + tol,
        "continues_below_current_level": min_z < level_z - tol,
        "continues_above_current_level": max_z > level_z + tol,
        "continues_above_floor_band": max_z > level_info["band_top"] + tol,
        "upper_end_near_current_level": abs(max_z - level_z) <= tol,
        "lower_end_near_current_level": abs(min_z - level_z) <= tol,
    }


# =========================
# Intent / Tags
# =========================
def classify_vertical_pipe_tag_intent(pipe_span, level_info):
    if not pipe_span.get("crosses_floor_band"):
        return (
            INTENT_OUTSIDE_ACTIVE_LEVEL_BAND,
            "Pipe does not intersect active floor band.",
        )
    if not pipe_span.get("crosses_active_tag_zone"):
        return (
            STATUS_OUTSIDE_ACTIVE_TAG_ZONE,
            "Pipe is within the broader story band but not near the active view level. Tag it on the corresponding level view.",
        )

    below = pipe_span.get("continues_below_current_level")
    above = pipe_span.get("continues_above_current_level")
    upper_near = pipe_span.get("upper_end_near_current_level")
    lower_near = pipe_span.get("lower_end_near_current_level")

    if DEFAULT_DIRECTION == "DOWN":
        if below and above:
            return (
                INTENT_CONTINUOUS_DOWN,
                "Pipe crosses the active tag zone and extends above and below current level.",
            )
        if upper_near and below:
            return (
                INTENT_GOING_DOWN,
                "Pipe upper end is near active level and extends downward.",
            )
        if lower_near and above:
            return (
                INTENT_COMING_DOWN,
                "Pipe lower end is near active level and extends upward; default drainage direction is down.",
            )
        if below:
            return INTENT_GOING_DOWN, "Pipe continues below the active level."
        if above and not below:
            return (
                INTENT_COMING_DOWN,
                "Pipe is inside active tag zone and continues upward; default drainage direction is down.",
            )
    else:
        if below and above:
            return (
                INTENT_CONTINUOUS_UP,
                "Pipe spans through active level; default direction is up.",
            )
        if lower_near and above:
            return (
                INTENT_GOING_UP,
                "Pipe starts near active level and continues upward.",
            )
        if below:
            return INTENT_GOING_DOWN, "Pipe continues below active level."

    return INTENT_UNKNOWN, "Ambiguous vertical span relative to active level."


def collect_pipe_tag_symbols():
    symbols = []
    try:
        collector = (
            FilteredElementCollector(doc)
            .OfClass(FamilySymbol)
            .OfCategory(BuiltInCategory.OST_PipeTags)
            .ToElements()
        )
    except Exception:
        try:
            collector = FilteredElementCollector(doc).OfClass(FamilySymbol).ToElements()
        except Exception:
            collector = []
    for symbol in collector:
        try:
            family_name = symbol.Family.Name
        except Exception:
            family_name = ""
        try:
            category_name = symbol.Category.Name
        except Exception:
            category_name = ""
        if (
            "pipe" not in (category_name or "").lower()
            and "tag" not in (category_name or "").lower()
        ):
            continue
        type_name = safe_element_name(symbol)
        symbols.append(
            {
                "symbol": symbol,
                "family": family_name,
                "type": type_name,
                "id": symbol.Id.IntegerValue,
                "category": category_name,
            }
        )
    return symbols


def resolve_symbols_by_intent(symbols):
    resolved = {}
    target_symbols = [
        s for s in symbols if is_target_vertical_pipe_tag_family(s.get("family"))
    ]
    for intent in CONFIG_TYPE_INTENTS + [
        INTENT_STARTS_HERE_GOES_DOWN,
        INTENT_STARTS_HERE_GOES_UP,
        INTENT_THROUGH_LEVEL,
    ]:
        resolved[intent] = None
        configured_name = get_configured_type_name(intent)
        candidates = []
        if configured_name:
            candidates.append(configured_name)
        candidates.extend(TAG_TYPE_CANDIDATES.get(intent, []))
        for candidate in candidates:
            candidate_lower = normalize_name(candidate)
            for symbol_info in target_symbols:
                if candidate_lower == normalize_name(symbol_info.get("type")):
                    resolved[intent] = symbol_info
                    break
            if resolved[intent] is not None:
                break
    return resolved


def intent_matches_for_symbol(symbol_info):
    matches = []
    type_lower = normalize_name(symbol_info.get("type"))
    for intent in CONFIG_TYPE_INTENTS:
        configured_name = get_configured_type_name(intent)
        candidates = []
        if configured_name:
            candidates.append(configured_name)
        candidates.extend(TAG_TYPE_CANDIDATES.get(intent, []))
        for candidate in candidates:
            if type_lower == normalize_name(candidate):
                matches.append(intent)
                break
    return ", ".join(matches)


def suggested_tag_type_text(intent, resolved):
    symbol_info = resolved.get(intent)
    if symbol_info is None:
        if intent in [INTENT_OUTSIDE_ACTIVE_LEVEL_BAND, INTENT_UNKNOWN]:
            return "-"
        return "MISSING_TAG_TYPE"
    return "{} / {} ({})".format(
        symbol_info.get("family"),
        symbol_info.get("type"),
        symbol_info.get("id"),
    )


def intended_revit_tag_type_for_intent(intent):
    if intent == INTENT_THROUGH_LEVEL:
        if DEFAULT_DIRECTION == "UP":
            intent = INTENT_CONTINUOUS_UP
        else:
            intent = INTENT_CONTINUOUS_DOWN
    configured_name = get_configured_type_name(intent)
    if configured_name:
        return configured_name
    candidates = TAG_TYPE_CANDIDATES.get(intent, [])
    if candidates:
        return candidates[0]
    return "-"


def family_names_from_symbols(symbols):
    names = []
    for symbol in symbols:
        family = symbol.get("family")
        if family and family not in names:
            names.append(family)
    names.sort()
    return names


def symbols_for_family(symbols, family_name):
    target = normalize_name(family_name)
    return [s for s in symbols if normalize_name(s.get("family")) == target]


def auto_map_types_for_family(symbols, family_name, existing_mapping=None):
    mapping = dict(DEFAULT_TYPE_MAPPING)
    if isinstance(existing_mapping, dict):
        for intent, value in existing_mapping.items():
            if value:
                mapping[str(intent)] = str(value)
    family_symbols = symbols_for_family(symbols, family_name)
    type_names = [s.get("type") for s in family_symbols if s.get("type")]
    for intent in CONFIG_TYPE_INTENTS:
        expected = DEFAULT_TYPE_MAPPING.get(intent)
        if expected is None:
            continue
        for type_name in type_names:
            if normalize_name(type_name) == normalize_name(expected):
                mapping[intent] = type_name
                break
    return mapping, type_names


def analyze_pipe_tag_family_compatibility(family_name, symbols, config=None):
    family_symbols = symbols_for_family(symbols, family_name)
    type_names = [s.get("type") for s in family_symbols if s.get("type")]
    normalized_types = [normalize_name(name) for name in type_names]
    missing_required = []
    matched_required = 0
    for required in REQUIRED_COMPATIBLE_TYPE_NAMES:
        if normalize_name(required) in normalized_types:
            matched_required += 1
        else:
            missing_required.append(required)
    matched_expected = 0
    for expected in EXPECTED_VERTICAL_TYPE_NAMES:
        if normalize_name(expected) in normalized_types:
            matched_expected += 1
    return {
        "family_name": family_name,
        "type_names": type_names,
        "matched_required_type_count": matched_required,
        "matched_expected_type_count": matched_expected,
        "missing_required_type_names": missing_required,
        "is_compatible": len(missing_required) == 0,
    }


def compatibility_display_text(analysis):
    family_name = analysis.get("family_name")
    matched = analysis.get("matched_expected_type_count") or 0
    total = len(EXPECTED_VERTICAL_TYPE_NAMES)
    if analysis.get("is_compatible"):
        return "{}  [compatible: {}/{}]".format(family_name, matched, total)
    return "{}  [incompatible: {}/{}]".format(family_name, matched, total)


def build_family_selection_options(symbols):
    analyses = []
    for family_name in family_names_from_symbols(symbols):
        analyses.append(
            analyze_pipe_tag_family_compatibility(family_name, symbols, get_config())
        )
    analyses.sort(
        key=lambda item: (
            not bool(item.get("is_compatible")),
            -int(item.get("matched_expected_type_count") or 0),
            item.get("family_name") or "",
        )
    )
    display_to_analysis = {}
    labels = []
    for analysis in analyses:
        label = compatibility_display_text(analysis)
        labels.append(label)
        display_to_analysis[label] = analysis
    return labels, display_to_analysis


def print_family_selection_report(analysis, config_path, load_report=None):
    family_name = analysis.get("family_name")
    type_names = analysis.get("type_names") or []
    output = None
    if script is not None:
        try:
            output = script.get_output()
        except Exception:
            output = None
    output_emit(output, "## Smart Vertical Pipe Tags - Family Selection")
    output_emit(output, "")
    if load_report:
        output_emit(output, "### Family Load")
        emit_table(
            output,
            ["Field", "Value"],
            [
                ["Selected file path", load_report.get("path") or "-"],
                ["Load success", fmt_bool(load_report.get("success"))],
                ["Load message", load_report.get("message") or ""],
            ],
        )
        output_emit(output, "")
    output_emit(output, "### Selected Family")
    emit_table(
        output,
        ["Field", "Value"],
        [
            ["Selected family", family_name],
            [
                "Compatibility",
                "compatible" if analysis.get("is_compatible") else "incompatible",
            ],
            [
                "Matched required types count",
                "{} / {}".format(
                    analysis.get("matched_required_type_count"),
                    len(REQUIRED_COMPATIBLE_TYPE_NAMES),
                ),
            ],
            [
                "Missing required types",
                unique_join(analysis.get("missing_required_type_names") or []),
            ],
            ["Config path", config_path],
        ],
    )
    output_emit(output, "")
    output_emit(output, "### Available type names")
    for type_name in type_names:
        output_emit(output, "- {}".format(type_name))


def store_selected_family(symbols, analysis, load_path=None, load_report=None):
    selected = analysis.get("family_name")
    if not selected:
        return
    if not analysis.get("is_compatible"):
        if not confirm_yes_no(
            "Smart Vertical Pipe Tags",
            "Incompatible tag family",
            "Selected family does not contain the expected vertical pipe tag types. "
            "Placement may fail or produce incorrect tags. Continue?",
        ):
            return
    config = get_config()
    mapping, type_names = auto_map_types_for_family(
        symbols,
        selected,
        config.get("target_type_mapping"),
    )
    config["target_family_name"] = selected
    config["target_type_mapping"] = mapping
    config["last_selected_family_compatibility"] = (
        "compatible" if analysis.get("is_compatible") else "incompatible"
    )
    config["last_selected_family_missing_types"] = (
        analysis.get("missing_required_type_names") or []
    )
    if load_path:
        config["last_loaded_family_path"] = load_path
    save_config(config)
    print_family_selection_report(analysis, get_config_path(), load_report)


def choose_vertical_pipe_tag_family():
    symbols = collect_pipe_tag_symbols()
    labels, display_to_analysis = build_family_selection_options(symbols)
    if not labels:
        show_dialog("No loaded pipe tag families were found in this document.")
        return
    selected = choose_from_list(
        "Choose Loaded Vertical Pipe Tag Family",
        "Select the loaded pipe tag family to use for Smart Vertical Pipe Tags.",
        labels,
    )
    if not selected:
        return
    analysis = display_to_analysis.get(selected)
    if analysis is not None:
        store_selected_family(symbols, analysis)


class SmartVerticalPipeTagFamilyLoadOptions(IFamilyLoadOptions):
    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        try:
            overwriteParameterValues.Value = True
        except Exception:
            pass
        return True

    def OnSharedFamilyFound(
        self, sharedFamily, familyInUse, source, overwriteParameterValues
    ):
        try:
            overwriteParameterValues.Value = True
        except Exception:
            pass
        return True


def pick_rfa_file():
    if forms is not None:
        try:
            path = forms.pick_file(file_ext="rfa", title="Select Revit tag family")
            if path:
                return path
        except Exception:
            pass
    dialog = OpenFileDialog()
    dialog.Title = "Select Revit tag family"
    dialog.Filter = "Revit Family (*.rfa)|*.rfa"
    dialog.Multiselect = False
    result = dialog.ShowDialog()
    if result == DialogResult.OK:
        return dialog.FileName
    return None


def try_load_family(path):
    tx = Transaction(doc, "Smart Vertical Pipe Tags - Load Family")
    started = False
    first_error = None
    try:
        tx.Start()
        started = True
        success = False
        try:
            success = bool(
                doc.LoadFamily(path, SmartVerticalPipeTagFamilyLoadOptions())
            )
        except Exception:
            try:
                success = bool(doc.LoadFamily(path))
            except Exception as ex:
                first_error = ex
                if started:
                    tx.RollBack()
                    started = False
                # Some Revit versions/load paths require LoadFamily outside an open transaction.
                try:
                    success = bool(
                        doc.LoadFamily(path, SmartVerticalPipeTagFamilyLoadOptions())
                    )
                    return success, (
                        "Family loaded." if success else "LoadFamily returned false."
                    )
                except Exception:
                    try:
                        success = bool(doc.LoadFamily(path))
                        return success, (
                            "Family loaded."
                            if success
                            else "LoadFamily returned false."
                        )
                    except Exception as retry_ex:
                        return False, "LoadFamily failed: {}; retry failed: {}".format(
                            first_error, retry_ex
                        )
        tx.Commit()
        return success, "Family loaded." if success else "LoadFamily returned false."
    except Exception as ex:
        try:
            if started:
                tx.RollBack()
        except Exception:
            pass
        return False, "Family load transaction failed: {}".format(ex)


def load_vertical_pipe_tag_family_from_rfa():
    path = pick_rfa_file()
    if not path:
        return
    if not path.lower().endswith(".rfa"):
        show_dialog("Select a Revit family file with .rfa extension.")
        return
    before_symbols = collect_pipe_tag_symbols()
    before_families = family_names_from_symbols(before_symbols)
    success, message = try_load_family(path)
    after_symbols = collect_pipe_tag_symbols()
    after_families = family_names_from_symbols(after_symbols)
    load_report = {"path": path, "success": success, "message": message}
    if not success:
        print_family_load_failure_report(load_report)
        return

    new_families = [name for name in after_families if name not in before_families]
    selected_analysis = None
    if new_families:
        selected_analysis = analyze_pipe_tag_family_compatibility(
            new_families[0], after_symbols, get_config()
        )
    else:
        best = None
        for family_name in after_families:
            analysis = analyze_pipe_tag_family_compatibility(
                family_name, after_symbols, get_config()
            )
            if best is None:
                best = analysis
            elif int(analysis.get("matched_expected_type_count") or 0) > int(
                best.get("matched_expected_type_count") or 0
            ):
                best = analysis
        selected_analysis = best

    if selected_analysis is not None and selected_analysis.get(
        "matched_expected_type_count"
    ):
        store_selected_family(after_symbols, selected_analysis, path, load_report)
    else:
        print_family_load_failure_report(load_report)
        choose_vertical_pipe_tag_family()


def print_family_load_failure_report(load_report):
    output = None
    if script is not None:
        try:
            output = script.get_output()
        except Exception:
            output = None
    output_emit(output, "## Smart Vertical Pipe Tags - Family Load")
    emit_table(
        output,
        ["Field", "Value"],
        [
            ["Selected file path", load_report.get("path") or "-"],
            ["Load success", fmt_bool(load_report.get("success"))],
            ["Load message", load_report.get("message") or ""],
        ],
    )


# =========================
# Existing Tags
# =========================
def get_tagged_element_ids(tag):
    ids = []
    try:
        tagged_ids = tag.GetTaggedLocalElementIds()
        for element_id in tagged_ids:
            ids.append(element_id.IntegerValue)
        return ids
    except Exception:
        pass
    try:
        tagged_ids = tag.GetTaggedElementIds()
        for tagged_id in tagged_ids:
            try:
                host_id = tagged_id.HostElementId
                if host_id and host_id.IntegerValue > 0:
                    ids.append(host_id.IntegerValue)
                    continue
            except Exception:
                pass
            try:
                linked_id = tagged_id.LinkedElementId
                if linked_id and linked_id.IntegerValue > 0:
                    ids.append(linked_id.IntegerValue)
            except Exception:
                pass
        if ids:
            return ids
    except Exception:
        pass
    try:
        tagged_id = tag.TaggedLocalElementId
        if tagged_id and tagged_id.IntegerValue > 0:
            ids.append(tagged_id.IntegerValue)
    except Exception:
        pass
    try:
        tagged_id = tag.TaggedElementId
        try:
            host_id = tagged_id.HostElementId
            if host_id and host_id.IntegerValue > 0:
                ids.append(host_id.IntegerValue)
        except Exception:
            pass
        try:
            linked_id = tagged_id.LinkedElementId
            if linked_id and linked_id.IntegerValue > 0:
                ids.append(linked_id.IntegerValue)
        except Exception:
            pass
        try:
            if tagged_id.IntegerValue > 0:
                ids.append(tagged_id.IntegerValue)
        except Exception:
            pass
    except Exception:
        pass
    unique_ids = []
    for element_id in ids:
        if element_id not in unique_ids:
            unique_ids.append(element_id)
    return unique_ids


def get_symbol_family_name(symbol):
    if symbol is None:
        return "UNKNOWN"
    try:
        if symbol.Family is not None and symbol.Family.Name:
            return symbol.Family.Name
    except Exception:
        pass
    try:
        if symbol.FamilyName:
            return symbol.FamilyName
    except Exception:
        pass
    return "UNKNOWN"


def resolve_tag_type_info(tag):
    try:
        tag_type = doc.GetElement(tag.GetTypeId())
    except Exception:
        tag_type = None
    family_name = get_symbol_family_name(tag_type)
    type_name = safe_element_name(tag_type)
    if not family_name:
        family_name = "UNKNOWN"
    if not type_name:
        type_name = "UNKNOWN"
    return tag_type, family_name, type_name


def collect_tag_head_position(tag):
    try:
        return tag.TagHeadPosition
    except Exception:
        pass
    try:
        loc = tag.Location
        if loc is not None and hasattr(loc, "Point"):
            return loc.Point
    except Exception:
        pass
    return None


def is_pipe_curve_element(element_id):
    try:
        elem = doc.GetElement(ElementId(int(element_id)))
        if elem is None or elem.Category is None:
            return False
        pipe_category_id = ElementId(BuiltInCategory.OST_PipeCurves).IntegerValue
        return elem.Category.Id.IntegerValue == pipe_category_id
    except Exception:
        return False


def pipe_center_xy_from_row(row):
    geometry = row.get("geometry")
    if geometry is None:
        return None
    start = geometry.get("start")
    end = geometry.get("end")
    if start is None or end is None:
        return None
    return XYZ(
        (start.X + end.X) / 2.0, (start.Y + end.Y) / 2.0, (start.Z + end.Z) / 2.0
    )


def xy_distance(point_a, point_b):
    if point_a is None or point_b is None:
        return None
    dx = point_a.X - point_b.X
    dy = point_a.Y - point_b.Y
    return (dx * dx + dy * dy) ** 0.5


def collect_existing_vertical_tags(active_view):
    rows = []
    by_id = {}
    try:
        class_tags = (
            FilteredElementCollector(doc, active_view.Id)
            .OfClass(IndependentTag)
            .ToElements()
        )
    except Exception:
        class_tags = []
    try:
        category_tags = (
            FilteredElementCollector(doc, active_view.Id)
            .OfCategory(BuiltInCategory.OST_PipeTags)
            .WhereElementIsNotElementType()
            .ToElements()
        )
    except Exception:
        category_tags = []
    for tag in list(class_tags) + list(category_tags):
        try:
            tag_id = tag.Id.IntegerValue
        except Exception:
            continue
        if tag_id not in by_id:
            by_id[tag_id] = tag

    for tag_id in sorted(by_id.keys()):
        tag = by_id[tag_id]
        _tag_type, family_name, type_name = resolve_tag_type_info(tag)
        family_matches = is_target_vertical_pipe_tag_family(family_name)
        if not family_matches and family_name != "UNKNOWN":
            continue
        tagged_ids = get_tagged_element_ids(tag)
        tag_head = collect_tag_head_position(tag)
        detection_method = "TAGGED_ELEMENT_ID" if tagged_ids else "UNKNOWN_TAGGED_ID"
        if family_name == "UNKNOWN" or type_name == "UNKNOWN":
            detection_method = "UNRESOLVED_TYPE"
        rows.append(
            {
                "id": tag_id,
                "family": family_name,
                "type": type_name,
                "tagged_ids": tagged_ids,
                "tag_head": tag_head,
                "detection_method": detection_method,
                "family_matches": family_matches,
                "element": tag,
            }
        )
    return rows


def add_existing_tag_to_pipe_map(by_pipe, pipe_id, tag):
    if pipe_id not in by_pipe:
        by_pipe[pipe_id] = []
    existing_ids = [item.get("id") for item in by_pipe[pipe_id]]
    if tag.get("id") not in existing_ids:
        by_pipe[pipe_id].append(tag)


def build_existing_vertical_tag_by_pipe_id(existing_tags, scan_rows=None):
    by_pipe = {}
    for tag in existing_tags:
        if not tag.get("family_matches"):
            continue
        for pipe_id in tag.get("tagged_ids", []):
            if is_pipe_curve_element(pipe_id):
                add_existing_tag_to_pipe_map(by_pipe, pipe_id, tag)
    if scan_rows:
        tol = mm_to_ft(EXISTING_TAG_PROXIMITY_TOL_MM)
        for tag in existing_tags:
            if not tag.get("family_matches"):
                continue
            if tag.get("tagged_ids"):
                continue
            tag_head = tag.get("tag_head")
            if tag_head is None:
                continue
            for row in scan_rows:
                if row.get("status") not in [
                    STATUS_VERTICAL_CANDIDATE,
                    STATUS_OUTSIDE_ACTIVE_TAG_ZONE,
                ]:
                    continue
                center = pipe_center_xy_from_row(row)
                distance = xy_distance(tag_head, center)
                if distance is not None and distance <= tol:
                    tag["detection_method"] = "DUPLICATE_BY_PROXIMITY"
                    add_existing_tag_to_pipe_map(by_pipe, row.get("id"), tag)
    return by_pipe


# =========================
# Scan
# =========================
def scan_pipe(pipe, level_info, resolved_symbols):
    geometry = get_pipe_geometry_info(pipe)
    row = {
        "pipe": pipe,
        "id": pipe.Id.IntegerValue,
        "system": get_pipe_system_name(pipe),
        "type": get_type_name(pipe),
        "geometry": geometry,
        "span": None,
        "intent": INTENT_UNKNOWN,
        "intended_tag_type": "-",
        "tag_symbol": None,
        "tag_text": "-",
        "existing_tag": None,
        "existing_tags": [],
        "recommended_level": {
            "level": None,
            "name": "-",
            "elevation_mm": "-",
            "reason": "No level recommendation available.",
        },
        "recommended_action": "-",
        "created_tag_id": None,
        "created_tag_verification": None,
        "placement_point": None,
        "placement_status": "",
        "placement_reason": "",
        "status": STATUS_SKIPPED_NO_LOCATION,
        "reason": "",
    }

    if geometry is None:
        row["reason"] = "No LocationCurve."
        row["recommended_action"] = "Not a vertical pipe tagging candidate."
        return row

    if not geometry["is_vertical_candidate"]:
        row["status"] = STATUS_SKIPPED_NOT_VERTICAL
        row["reason"] = "Not vertical by XY tolerance / angle threshold."
        row["recommended_action"] = "Not a vertical pipe tagging candidate."
        return row

    if geometry["max_z"] - geometry["min_z"] < mm_to_ft(MIN_VERTICAL_HEIGHT_MM):
        row["status"] = STATUS_SKIPPED_TOO_SHORT
        row["reason"] = "Vertical height below minimum."
        row["recommended_action"] = "Not a vertical pipe tagging candidate."
        return row

    row["recommended_level"] = get_recommended_tag_level(
        geometry["min_z"],
        geometry["max_z"],
        level_info.get("levels", []),
    )
    span = build_pipe_span(geometry, level_info)
    row["span"] = span
    intent, reason = classify_vertical_pipe_tag_intent(span, level_info)
    row["intent"] = intent
    row["reason"] = reason

    if intent == INTENT_OUTSIDE_ACTIVE_LEVEL_BAND:
        row["status"] = STATUS_OUTSIDE_ACTIVE_LEVEL_BAND
        row["recommended_action"] = "Open the recommended level view and tag there."
        return row
    if intent == STATUS_OUTSIDE_ACTIVE_TAG_ZONE:
        row["status"] = STATUS_OUTSIDE_ACTIVE_TAG_ZONE
        row["recommended_action"] = "Open the recommended level view and tag there."
        return row
    if intent == INTENT_UNKNOWN:
        row["status"] = STATUS_VERTICAL_CANDIDATE
        row["tag_text"] = "UNKNOWN"
        row["recommended_action"] = "Review classification before tagging."
        return row

    row["status"] = STATUS_VERTICAL_CANDIDATE
    row["recommended_action"] = "Taggable in active view."
    row["intended_tag_type"] = intended_revit_tag_type_for_intent(intent)
    symbol_info = resolved_symbols.get(intent)
    row["tag_symbol"] = symbol_info
    row["tag_text"] = suggested_tag_type_text(intent, resolved_symbols)
    if symbol_info is None:
        row["reason"] = reason + " Matching tag type was not found."
    return row


def collect_active_view_pipes(active_view):
    try:
        return list(
            FilteredElementCollector(doc, active_view.Id)
            .OfCategory(BuiltInCategory.OST_PipeCurves)
            .WhereElementIsNotElementType()
            .ToElements()
        )
    except Exception:
        return []


def collect_selected_pipes():
    pipes = []
    try:
        selected_ids = list(uidoc.Selection.GetElementIds())
    except Exception:
        selected_ids = []
    for element_id in selected_ids:
        try:
            pipe_category_id = ElementId(BuiltInCategory.OST_PipeCurves).IntegerValue
            elem = doc.GetElement(element_id)
            if (
                elem is not None
                and elem.Category is not None
                and elem.Category.Id.IntegerValue == pipe_category_id
            ):
                pipes.append(elem)
        except Exception:
            pass
    return pipes


def select_valid_candidates(scan_rows, scope=None):
    element_ids = List[ElementId]()
    outside_zone_ids = List[ElementId]()
    missing_count = 0
    for row in scan_rows:
        if row.get("status") in [
            STATUS_OUTSIDE_ACTIVE_TAG_ZONE,
            STATUS_EXISTING_TAG_OUTSIDE_ACTIVE_TAG_ZONE,
        ]:
            try:
                outside_zone_ids.Add(ElementId(int(row.get("id"))))
            except Exception:
                pass
            continue
        if row.get("status") != STATUS_VERTICAL_CANDIDATE:
            continue
        if row.get("intent") in [INTENT_UNKNOWN, INTENT_OUTSIDE_ACTIVE_LEVEL_BAND]:
            continue
        if row.get("tag_symbol") is None:
            missing_count += 1
        try:
            element_ids.Add(ElementId(int(row.get("id"))))
        except Exception:
            pass
    if element_ids.Count == 0:
        if scope == "selected" and outside_zone_ids.Count > 0:
            try:
                uidoc.Selection.SetElementIds(outside_zone_ids)
                print(
                    "No active-view taggable candidates found. Selected {} outside-zone pipes for review.".format(
                        outside_zone_ids.Count
                    )
                )
                return outside_zone_ids.Count
            except Exception as ex:
                print("Outside-zone pipe selection failed: {}".format(ex))
        print("No valid vertical pipe tag candidates found.")
        return 0
    try:
        uidoc.Selection.SetElementIds(element_ids)
        print(
            "Selected {} vertical pipe candidates for review.".format(element_ids.Count)
        )
        print("Selected {} candidates with missing tag type.".format(missing_count))
        return element_ids.Count
    except Exception as ex:
        print("Candidate selection failed: {}".format(ex))
        return 0


def get_tag_point(row, level_info):
    geometry = row.get("geometry")
    if geometry is None:
        return None
    p0 = geometry.get("start")
    p1 = geometry.get("end")
    if p0 is None or p1 is None:
        return None
    return XYZ(
        (p0.X + p1.X) / 2.0,
        (p0.Y + p1.Y) / 2.0,
        level_info["current_elevation"],
    )


def is_placeable_candidate(row):
    if row.get("status") != STATUS_VERTICAL_CANDIDATE:
        return False
    if row.get("intent") in [INTENT_UNKNOWN, INTENT_OUTSIDE_ACTIVE_LEVEL_BAND]:
        return False
    if row.get("tag_symbol") is None:
        return False
    if row.get("existing_tag") is not None:
        return False
    return True


def is_pipe_tags_category_hidden(active_view):
    try:
        category_id = ElementId(BuiltInCategory.OST_PipeTags)
        return active_view.GetCategoryHidden(category_id)
    except Exception:
        pass
    return None


def verify_created_tag(active_view, row):
    tag_id = row.get("created_tag_id")
    verification = {
        "tag_id": tag_id,
        "host_pipe_id": row.get("id"),
        "owner_view_id": "-",
        "active_view_id": active_view.Id.IntegerValue,
        "type": "UNKNOWN",
        "tagged_ids": [],
        "tag_head": None,
        "hidden": "unknown",
        "pipe_tags_category_hidden": "unknown",
        "status": "CREATED_BUT_NOT_FOUND_AFTER_COMMIT",
    }
    if tag_id is None:
        row["created_tag_verification"] = verification
        return verification
    try:
        tag = doc.GetElement(ElementId(int(tag_id)))
    except Exception:
        tag = None
    if tag is None:
        row["created_tag_verification"] = verification
        return verification

    try:
        verification["owner_view_id"] = tag.OwnerViewId.IntegerValue
    except Exception:
        pass
    _tag_type, family_name, type_name = resolve_tag_type_info(tag)
    verification["type"] = "{} / {}".format(family_name, type_name)
    verification["tagged_ids"] = get_tagged_element_ids(tag)
    verification["tag_head"] = collect_tag_head_position(tag)
    try:
        verification["hidden"] = fmt_bool(tag.IsHidden(active_view))
    except Exception:
        verification["hidden"] = "unknown"
    category_hidden = is_pipe_tags_category_hidden(active_view)
    if category_hidden is not None:
        verification["pipe_tags_category_hidden"] = fmt_bool(category_hidden)

    owner_matches = str(verification.get("owner_view_id")) == str(
        active_view.Id.IntegerValue
    )
    hidden_value = verification.get("hidden")
    category_value = verification.get("pipe_tags_category_hidden")
    if not owner_matches:
        verification["status"] = "CREATED_IN_DIFFERENT_VIEW"
    elif hidden_value == "true" or category_value == "true":
        verification["status"] = "CREATED_BUT_HIDDEN_OR_NOT_VISIBLE"
    elif not verification.get("tagged_ids"):
        verification["status"] = "CREATED_BUT_TAGGED_ID_UNREADABLE"
    else:
        verification["status"] = "VERIFIED_IN_ACTIVE_VIEW"
    row["created_tag_verification"] = verification
    return verification


def add_row_to_overlap_groups(groups, row, point, tolerance):
    for group in groups:
        if xy_distance(group.get("point"), point) <= tolerance:
            group["rows"].append(row)
            return
    groups.append({"point": point, "rows": [row]})


def finalize_overlap_groups(groups):
    result = []
    group_id = 1
    for group in groups:
        rows = group.get("rows", [])
        if len(rows) <= 1:
            continue
        result.append(
            {
                "id": group_id,
                "point": group.get("point"),
                "rows": rows,
                "count": len(rows),
            }
        )
        group_id += 1
    return result


def build_candidate_overlap_groups(scan_rows, level_info):
    groups = []
    tolerance = mm_to_ft(TAG_OVERLAP_XY_TOL_MM)
    for row in scan_rows:
        if row.get("status") != STATUS_VERTICAL_CANDIDATE:
            continue
        point = get_tag_point(row, level_info)
        if point is None:
            continue
        add_row_to_overlap_groups(groups, row, point, tolerance)
    return finalize_overlap_groups(groups)


def build_created_tag_overlap_groups(created_rows):
    groups = []
    tolerance = mm_to_ft(TAG_OVERLAP_XY_TOL_MM)
    for row in created_rows:
        verification = row.get("created_tag_verification") or {}
        point = verification.get("tag_head") or row.get("placement_point")
        if point is None:
            continue
        add_row_to_overlap_groups(groups, row, point, tolerance)
    return finalize_overlap_groups(groups)


def update_existing_tag_status(scan_rows, existing_tag_by_pipe_id):
    for row in scan_rows:
        existing_tags = existing_tag_by_pipe_id.get(row.get("id"))
        if not existing_tags:
            continue
        existing_tag = existing_tags[0]
        row["existing_tags"] = existing_tags
        if row.get("status") == STATUS_VERTICAL_CANDIDATE:
            row["existing_tag"] = existing_tag
            row["status"] = STATUS_SKIPPED_ALREADY_TAGGED
            row["recommended_action"] = "Already tagged in active view."
            if existing_tag.get("detection_method") == "DUPLICATE_BY_PROXIMITY":
                row["reason"] = (
                    "Pipe already has a potential matching vertical tag in the active view "
                    "detected by proximity."
                )
            else:
                row["reason"] = (
                    "Pipe already has a vertical pipe tag in the active view."
                )
        elif row.get("status") == STATUS_OUTSIDE_ACTIVE_TAG_ZONE:
            row["existing_tag"] = existing_tag
            row["status"] = STATUS_EXISTING_TAG_OUTSIDE_ACTIVE_TAG_ZONE
            row["recommended_action"] = (
                "Open the recommended level view and review this existing tag."
            )
            row["reason"] = (
                "Existing vertical tag is on a pipe outside the active tag zone."
            )


def create_tag_for_row(active_view, row, level_info):
    symbol_info = row.get("tag_symbol")
    symbol = symbol_info.get("symbol") if symbol_info is not None else None
    if symbol is None:
        row["placement_status"] = STATUS_TAG_FAILED
        row["placement_reason"] = "Missing resolved tag type."
        return None

    tag_point = get_tag_point(row, level_info)
    if tag_point is None:
        row["placement_status"] = STATUS_TAG_FAILED
        row["placement_reason"] = "Could not compute tag point."
        return None

    tx = Transaction(doc, "Smart Vertical Pipe Tag - {}".format(row.get("id")))
    try:
        tx.Start()
        try:
            if not symbol.IsActive:
                symbol.Activate()
                doc.Regenerate()
        except Exception:
            pass

        tag = IndependentTag.Create(
            doc,
            active_view.Id,
            Reference(row.get("pipe")),
            False,
            TagMode.TM_ADDBY_CATEGORY,
            TagOrientation.Horizontal,
            tag_point,
        )
        try:
            if tag.GetTypeId().IntegerValue != symbol.Id.IntegerValue:
                tag.ChangeTypeId(symbol.Id)
        except Exception as ex:
            tx.RollBack()
            row["placement_status"] = STATUS_TAG_FAILED
            row["placement_reason"] = "ChangeTypeId failed: {}".format(ex)
            return None
        try:
            tag.HasLeader = False
        except Exception:
            pass
        tx.Commit()
        row["created_tag_id"] = tag.Id.IntegerValue
        row["placement_point"] = tag_point
        row["placement_status"] = STATUS_TAG_CREATED
        row["placement_reason"] = "Created"
        verify_created_tag(active_view, row)
        return tag
    except Exception as ex:
        try:
            tx.RollBack()
        except Exception:
            pass
        row["placement_status"] = STATUS_TAG_FAILED
        row["placement_reason"] = "Tag creation failed: {}".format(ex)
        return None


def place_missing_tags(active_view, scan_rows, level_info):
    created = []
    failures = []
    skipped_existing_count = 0
    for row in scan_rows:
        if row.get("status") == STATUS_SKIPPED_ALREADY_TAGGED:
            skipped_existing_count += 1
            continue
        if (
            row.get("status") == STATUS_VERTICAL_CANDIDATE
            and row.get("tag_symbol") is None
        ):
            row["placement_status"] = STATUS_TAG_FAILED
            row["placement_reason"] = "MISSING_TAG_TYPE"
            failures.append(row)
            continue
        if not is_placeable_candidate(row):
            continue
        tag = create_tag_for_row(active_view, row, level_info)
        if tag is None:
            failures.append(row)
        else:
            created.append(row)
    if skipped_existing_count:
        print(
            "Skipped {} pipes because matching vertical tags already exist in this view.".format(
                skipped_existing_count
            )
        )
    return created, failures


def select_after_placement(created_rows, failure_rows, scan_rows=None):
    element_ids = List[ElementId]()
    if created_rows:
        for row in created_rows:
            try:
                element_ids.Add(ElementId(int(row.get("created_tag_id"))))
            except Exception:
                pass
        try:
            uidoc.Selection.SetElementIds(element_ids)
            print("Selected {} newly created tags.".format(element_ids.Count))
        except Exception as ex:
            print("Created tag selection failed: {}".format(ex))
        try:
            uidoc.ShowElements(element_ids)
            print("ShowElements called for {} created tags.".format(element_ids.Count))
        except Exception as ex:
            try:
                if element_ids.Count > 0:
                    uidoc.ShowElements(element_ids[0])
                    print("ShowElements called for 1 created tag.")
                else:
                    print("ShowElements failed: {}".format(ex))
            except Exception as nested_ex:
                print("ShowElements failed: {}".format(nested_ex))
        print(
            "Tags were created and selected. If they are not visible, use Select by ID or Zoom to Selection. Check that Pipe Tags annotation category is visible in the active view."
        )
        return

    already_tagged_rows = []
    outside_zone_rows = []
    if scan_rows:
        already_tagged_rows = [
            row
            for row in scan_rows
            if row.get("status") == STATUS_SKIPPED_ALREADY_TAGGED
        ]
        outside_zone_rows = [
            row
            for row in scan_rows
            if row.get("status")
            in [
                STATUS_OUTSIDE_ACTIVE_TAG_ZONE,
                STATUS_EXISTING_TAG_OUTSIDE_ACTIVE_TAG_ZONE,
            ]
        ]
    if not failure_rows and not already_tagged_rows and outside_zone_rows:
        for row in outside_zone_rows:
            try:
                element_ids.Add(ElementId(int(row.get("id"))))
            except Exception:
                pass
        try:
            uidoc.Selection.SetElementIds(element_ids)
            print(
                "Selected {} outside-zone pipes for review. Open the recommended level view and run the tool there.".format(
                    element_ids.Count
                )
            )
        except Exception as ex:
            print("Outside-zone pipe selection failed: {}".format(ex))
        return

    for row in failure_rows:
        try:
            element_ids.Add(ElementId(int(row.get("id"))))
        except Exception:
            pass
    if element_ids.Count == 0:
        print("No tags created. No unresolved candidate pipes to select.")
        return
    try:
        uidoc.Selection.SetElementIds(element_ids)
        print(
            "No tags created. Selected {} unresolved candidate pipes for review.".format(
                element_ids.Count
            )
        )
    except Exception as ex:
        print("Unresolved candidate selection failed: {}".format(ex))


# =========================
# Report
# =========================
def level_name(level):
    if level is None:
        return "-"
    return safe_element_name(level)


def level_elevation(level):
    if level is None:
        return "-"
    return fmt_mm(level.Elevation)


def pipe_row_to_report(row, output):
    geometry = row.get("geometry")
    span = row.get("span")
    element_value = (
        linkify(output, row.get("id")) if output is not None else str(row.get("id"))
    )
    if geometry is None:
        min_z = max_z = height = "-"
    else:
        min_z = fmt_mm(geometry.get("min_z"))
        max_z = fmt_mm(geometry.get("max_z"))
        height = fmt_mm(geometry.get("max_z") - geometry.get("min_z"))
    if span is None:
        crosses_current = "-"
        crosses_band = "-"
        crosses_tag_zone = "-"
    else:
        crosses_current = fmt_bool(span.get("crosses_current_level"))
        crosses_band = fmt_bool(span.get("crosses_floor_band"))
        crosses_tag_zone = fmt_bool(span.get("crosses_active_tag_zone"))
    taggable = row.get("status") == STATUS_VERTICAL_CANDIDATE and row.get(
        "intent"
    ) not in [INTENT_UNKNOWN, INTENT_OUTSIDE_ACTIVE_LEVEL_BAND]
    recommended = row.get("recommended_level") or {}
    return [
        element_value,
        row.get("system") or "",
        row.get("type") or "",
        min_z,
        max_z,
        height,
        crosses_current,
        crosses_band,
        crosses_tag_zone,
        fmt_bool(taggable),
        row.get("intent") or "",
        row.get("intended_tag_type") or "-",
        "-" if row.get("tag_symbol") is None else row.get("tag_symbol").get("id"),
        recommended.get("name") or "-",
        recommended.get("elevation_mm") or "-",
        row.get("recommended_action") or "-",
        row.get("status") or "",
        row.get("reason") or "",
    ]


def build_counts(scan_rows, total_checked):
    counts = {
        "total": total_checked,
        "vertical_candidates": 0,
        "skipped_not_vertical": 0,
        "skipped_too_short": 0,
        "skipped_no_location": 0,
        "outside_active_level_band": 0,
        "outside_active_tag_zone": 0,
        "unknown_intent": 0,
        "missing_tag_type": 0,
        "created_tags": 0,
        "skipped_already_tagged": 0,
        "failed_tag_creation": 0,
    }
    for row in scan_rows:
        status = row.get("status")
        if status == STATUS_VERTICAL_CANDIDATE:
            counts["vertical_candidates"] += 1
        elif status == STATUS_SKIPPED_NOT_VERTICAL:
            counts["skipped_not_vertical"] += 1
        elif status == STATUS_SKIPPED_TOO_SHORT:
            counts["skipped_too_short"] += 1
        elif status == STATUS_SKIPPED_NO_LOCATION:
            counts["skipped_no_location"] += 1
        elif status == STATUS_OUTSIDE_ACTIVE_LEVEL_BAND:
            counts["outside_active_level_band"] += 1
        elif status == STATUS_OUTSIDE_ACTIVE_TAG_ZONE:
            counts["outside_active_tag_zone"] += 1
        elif status == STATUS_EXISTING_TAG_OUTSIDE_ACTIVE_TAG_ZONE:
            counts["outside_active_tag_zone"] += 1
        elif status == STATUS_SKIPPED_ALREADY_TAGGED:
            counts["skipped_already_tagged"] += 1
        if row.get("intent") == INTENT_UNKNOWN:
            counts["unknown_intent"] += 1
        if row.get("tag_text") == "MISSING_TAG_TYPE":
            counts["missing_tag_type"] += 1
        if row.get("placement_status") == STATUS_TAG_CREATED:
            counts["created_tags"] += 1
        elif row.get("placement_status") == STATUS_TAG_FAILED:
            counts["failed_tag_creation"] += 1
    return counts


def print_report(
    active_view,
    level_info,
    tag_symbols,
    resolved_symbols,
    scan_rows,
    existing_tags,
    total_checked,
    scope,
):
    output = None
    if script is not None:
        try:
            output = script.get_output()
        except Exception:
            output = None

    counts = build_counts(scan_rows, total_checked)
    current_level = level_info["current_level"]
    next_level = level_info["next_level"]
    previous_level = level_info["previous_level"]
    created_rows = [
        row for row in scan_rows if row.get("placement_status") == STATUS_TAG_CREATED
    ]
    candidate_overlap_groups = build_candidate_overlap_groups(scan_rows, level_info)
    created_overlap_groups = build_created_tag_overlap_groups(created_rows)

    output_emit(output, "## Smart Vertical Pipe Tags - Scan Report")
    output_emit(output, "")
    output_emit(output, "### Active View / Level Context")
    emit_table(
        output,
        ["Field", "Value"],
        [
            ["Scope", scope],
            ["View name", safe_element_name(active_view)],
            ["View Id", active_view.Id.IntegerValue],
            [
                "Current level",
                "{} / {} mm".format(
                    level_name(current_level), level_elevation(current_level)
                ),
            ],
            [
                "Valid next story level",
                "{} / {} mm".format(
                    level_name(next_level), level_elevation(next_level)
                ),
            ],
            [
                "Valid previous story level",
                "{} / {} mm".format(
                    level_name(previous_level), level_elevation(previous_level)
                ),
            ],
            [
                "Ignored near-duplicate level count",
                len(level_info.get("ignored_levels", [])),
            ],
            ["Band bottom", "{} mm".format(fmt_mm(level_info["band_bottom"]))],
            ["Band top", "{} mm".format(fmt_mm(level_info["band_top"]))],
            [
                "Band height",
                "{} mm".format(
                    fmt_mm(level_info["band_top"] - level_info["band_bottom"])
                ),
            ],
            ["Tag zone bottom", "{} mm".format(fmt_mm(level_info["tag_zone_bottom"]))],
            ["Tag zone top", "{} mm".format(fmt_mm(level_info["tag_zone_top"]))],
            [
                "Tag zone height",
                "{} mm".format(
                    fmt_mm(level_info["tag_zone_top"] - level_info["tag_zone_bottom"])
                ),
            ],
        ],
    )

    output_emit(output, "")
    output_emit(output, "### Ignored near-duplicate levels")
    emit_table(
        output,
        ["Level name", "Elevation mm", "Reason"],
        [
            [
                level_name(item.get("level")),
                level_elevation(item.get("level")),
                item.get("reason") or "",
            ]
            for item in level_info.get("ignored_levels", [])
        ],
    )

    output_emit(output, "")
    output_emit(output, "### Loaded Vertical Pipe Tag Types")
    emit_table(
        output,
        ["Family", "Type", "ElementId", "Intent match if any"],
        [
            [
                symbol.get("family") or "",
                symbol.get("type") or "",
                symbol.get("id"),
                intent_matches_for_symbol(symbol),
            ]
            for symbol in tag_symbols
            if is_target_vertical_pipe_tag_family(symbol.get("family"))
        ],
    )

    output_emit(output, "")
    output_emit(output, "### Vertical Pipe Candidates")
    emit_table(
        output,
        [
            "Pipe ElementId",
            "System",
            "Type",
            "Min Z mm",
            "Max Z mm",
            "Height mm",
            "Crosses current level",
            "Crosses floor band",
            "Crosses active tag zone",
            "Taggable on active view",
            "Suggested intent",
            "Intended Revit tag type",
            "Resolved tag type ElementId",
            "Recommended tag level",
            "Recommended level elevation mm",
            "Recommended action",
            "Status",
            "Reason",
        ],
        [pipe_row_to_report(row, output) for row in scan_rows],
    )

    output_emit(output, "")
    output_emit(output, "### Pipes outside active tag zone")
    outside_zone_rows = [
        row
        for row in scan_rows
        if row.get("status")
        in [STATUS_OUTSIDE_ACTIVE_TAG_ZONE, STATUS_EXISTING_TAG_OUTSIDE_ACTIVE_TAG_ZONE]
    ]
    emit_table(
        output,
        [
            "Pipe ElementId",
            "Min Z mm",
            "Max Z mm",
            "Current active level",
            "Recommended tag level",
            "Recommended action",
        ],
        [
            [
                linkify(output, row.get("id")) if output is not None else row.get("id"),
                (
                    fmt_mm(row.get("geometry").get("min_z"))
                    if row.get("geometry")
                    else "-"
                ),
                (
                    fmt_mm(row.get("geometry").get("max_z"))
                    if row.get("geometry")
                    else "-"
                ),
                level_name(current_level),
                (row.get("recommended_level") or {}).get("name") or "-",
                row.get("recommended_action") or "",
            ]
            for row in outside_zone_rows
        ],
    )

    output_emit(output, "")
    output_emit(output, "### Potential Overlapping Vertical Tag Candidates")
    emit_table(
        output,
        [
            "Group",
            "Candidate pipe ids",
            "Intended tag types",
            "Placement XY mm",
            "Count",
            "Note",
        ],
        [
            [
                group.get("id"),
                format_element_ids(
                    [row.get("id") for row in group.get("rows", [])], output
                ),
                unique_join(
                    [row.get("intended_tag_type") for row in group.get("rows", [])]
                ),
                format_xy_mm(group.get("point")),
                group.get("count"),
                "Multiple vertical candidates share nearly the same tag location. Review for possible overlapping arrows.",
            ]
            for group in candidate_overlap_groups
        ],
    )

    output_emit(output, "")
    output_emit(output, "### Skipped Pipes Summary")
    emit_table(
        output,
        ["Metric", "Count"],
        [
            ["total PipeCurves checked", counts["total"]],
            ["vertical candidates", counts["vertical_candidates"]],
            ["skipped not vertical", counts["skipped_not_vertical"]],
            ["skipped too short", counts["skipped_too_short"]],
            ["skipped no location", counts["skipped_no_location"]],
            ["outside active level band", counts["outside_active_level_band"]],
            ["outside active tag zone", counts["outside_active_tag_zone"]],
            ["unknown intent", counts["unknown_intent"]],
            ["missing tag type", counts["missing_tag_type"]],
            ["created tags", counts["created_tags"]],
            ["skipped already tagged", counts["skipped_already_tagged"]],
            ["failed tag creation", counts["failed_tag_creation"]],
            ["overlapping candidate groups", len(candidate_overlap_groups)],
            ["overlapping created tag groups", len(created_overlap_groups)],
        ],
    )

    output_emit(output, "")
    output_emit(output, "### Created Tags")
    emit_table(
        output,
        [
            "Pipe ElementId",
            "Tag ElementId",
            "Intent",
            "Tag type",
            "Placement X/Y/Z mm",
            "Leader",
            "Status",
        ],
        [
            [
                linkify(output, row.get("id")) if output is not None else row.get("id"),
                (
                    linkify(output, row.get("created_tag_id"))
                    if output is not None
                    else row.get("created_tag_id")
                ),
                row.get("intent") or "",
                row.get("tag_text") or "",
                "{}/{}/{}".format(
                    (
                        fmt_mm(row.get("placement_point").X)
                        if row.get("placement_point") is not None
                        else "-"
                    ),
                    (
                        fmt_mm(row.get("placement_point").Y)
                        if row.get("placement_point") is not None
                        else "-"
                    ),
                    (
                        fmt_mm(row.get("placement_point").Z)
                        if row.get("placement_point") is not None
                        else "-"
                    ),
                ),
                "false",
                row.get("placement_status") or "",
            ]
            for row in created_rows
        ],
    )

    output_emit(output, "")
    output_emit(output, "### Created Tag Verification")
    emit_table(
        output,
        [
            "Tag ElementId",
            "Host Pipe ElementId",
            "Owner View Id",
            "Active View Id",
            "Type",
            "Tagged element ids",
            "Tag head X/Y/Z mm",
            "Hidden in active view",
            "Verification status",
        ],
        [
            [
                (
                    linkify(
                        output,
                        (row.get("created_tag_verification") or {}).get("tag_id"),
                    )
                    if output is not None
                    else (row.get("created_tag_verification") or {}).get("tag_id")
                ),
                (
                    linkify(
                        output,
                        (row.get("created_tag_verification") or {}).get("host_pipe_id"),
                    )
                    if output is not None
                    else (row.get("created_tag_verification") or {}).get("host_pipe_id")
                ),
                (row.get("created_tag_verification") or {}).get("owner_view_id") or "-",
                (row.get("created_tag_verification") or {}).get("active_view_id")
                or "-",
                (row.get("created_tag_verification") or {}).get("type") or "UNKNOWN",
                format_element_ids(
                    (row.get("created_tag_verification") or {}).get("tagged_ids", []),
                    output,
                ),
                "{}/{}/{}".format(
                    *format_point_mm(
                        (row.get("created_tag_verification") or {}).get("tag_head")
                    )
                ),
                (row.get("created_tag_verification") or {}).get("hidden") or "unknown",
                (row.get("created_tag_verification") or {}).get("status") or "-",
            ]
            for row in created_rows
        ],
    )

    output_emit(output, "")
    output_emit(output, "### Potential Overlapping Created Tags")
    emit_table(
        output,
        [
            "Group",
            "Created tag ids",
            "Host pipe ids",
            "Tag types",
            "Tag head XY mm",
            "Count",
            "Note",
        ],
        [
            [
                group.get("id"),
                format_element_ids(
                    [
                        (row.get("created_tag_verification") or {}).get("tag_id")
                        for row in group.get("rows", [])
                    ],
                    output,
                ),
                format_element_ids(
                    [
                        (row.get("created_tag_verification") or {}).get("host_pipe_id")
                        for row in group.get("rows", [])
                    ],
                    output,
                ),
                unique_join(
                    [
                        (row.get("created_tag_verification") or {}).get("type")
                        for row in group.get("rows", [])
                    ]
                ),
                format_xy_mm(group.get("point")),
                group.get("count"),
                "Multiple created tags share nearly the same tag head location. Review for possible overlapping arrows.",
            ]
            for group in created_overlap_groups
        ],
    )

    output_emit(output, "")
    output_emit(output, "### Skipped Existing Tags")
    skipped_existing = [
        row
        for row in scan_rows
        if row.get("status")
        in [STATUS_SKIPPED_ALREADY_TAGGED, STATUS_EXISTING_TAG_OUTSIDE_ACTIVE_TAG_ZONE]
    ]
    emit_table(
        output,
        ["Pipe ElementId", "Existing Tag ElementId(s)", "Existing tag type", "Reason"],
        [
            [
                linkify(output, row.get("id")) if output is not None else row.get("id"),
                format_existing_tag_ids(row, output),
                format_existing_tag_types(row),
                row.get("reason") or "",
            ]
            for row in skipped_existing
        ],
    )

    output_emit(output, "")
    output_emit(output, "### Tag Creation Failures")
    failed_rows = [
        row for row in scan_rows if row.get("placement_status") == STATUS_TAG_FAILED
    ]
    emit_table(
        output,
        ["Pipe ElementId", "Intent", "Intended tag type", "Reason"],
        [
            [
                linkify(output, row.get("id")) if output is not None else row.get("id"),
                row.get("intent") or "",
                row.get("intended_tag_type") or "-",
                row.get("placement_reason") or row.get("reason") or "",
            ]
            for row in failed_rows
        ],
    )

    output_emit(output, "")
    output_emit(output, "### Existing Vertical Tags in View")
    emit_table(
        output,
        [
            "Tag ElementId",
            "Family",
            "Type",
            "Tagged element ids",
            "Tag head X/Y/Z mm",
            "Detection method",
        ],
        [
            [
                linkify(output, tag.get("id")) if output is not None else tag.get("id"),
                tag.get("family") or "UNKNOWN",
                tag.get("type") or "UNKNOWN",
                format_element_ids(tag.get("tagged_ids", []), output),
                "{}/{}/{}".format(*format_point_mm(tag.get("tag_head"))),
                tag.get("detection_method") or "",
            ]
            for tag in existing_tags
        ],
    )


# =========================
# Multi-View Scan
# =========================
def is_eligible_batch_view(view):
    try:
        if view.IsTemplate:
            return False, "View template."
    except Exception:
        pass
    if not isinstance(view, ViewPlan):
        return False, "Not a plan view."
    try:
        view_type = str(view.ViewType)
        if view_type not in ["FloorPlan", "EngineeringPlan"]:
            return False, "Not a floor plan or engineering plan."
    except Exception:
        pass
    if not INCLUDE_DEPENDENT_VIEWS:
        try:
            primary_id = view.GetPrimaryViewId()
            if primary_id and primary_id.IntegerValue > 0:
                return False, "Dependent view excluded."
        except Exception:
            pass
    if get_active_view_level(view) is None:
        return False, "No usable level."
    return True, ""


def collect_batch_floor_plan_views():
    views = []
    skipped = []
    try:
        all_views = FilteredElementCollector(doc).OfClass(ViewPlan).ToElements()
    except Exception:
        all_views = []
    for view in all_views:
        eligible, reason = is_eligible_batch_view(view)
        if eligible:
            views.append(view)
        else:
            skipped.append({"view": view, "reason": reason})
    views.sort(key=lambda view: safe_element_name(view))
    return views, skipped


def confirm_batch_scan(view_count):
    if view_count <= MAX_VIEWS_FOR_BATCH_SCAN_WARNING:
        return True
    dialog = TaskDialog("Smart Vertical Pipe Tags")
    dialog.MainInstruction = "Scan all floor plan views"
    dialog.MainContent = (
        "This will scan {} floor plan views and may take time. "
        "No tags will be created. Continue?".format(view_count)
    )
    dialog.CommonButtons = TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No
    return dialog.Show() == TaskDialogResult.Yes


def scan_view_for_vertical_tags(view, tag_symbols, resolved_symbols):
    current_level = get_active_view_level(view)
    level_info = build_level_info(current_level)
    pipes = collect_active_view_pipes(view)
    scan_rows = [scan_pipe(pipe, level_info, resolved_symbols) for pipe in pipes]
    existing_tags = collect_existing_vertical_tags(view)
    existing_tag_by_pipe_id = build_existing_vertical_tag_by_pipe_id(
        existing_tags, scan_rows
    )
    update_existing_tag_status(scan_rows, existing_tag_by_pipe_id)
    missing_rows = [row for row in scan_rows if is_placeable_candidate(row)]
    return {
        "view": view,
        "level_info": level_info,
        "pipes": pipes,
        "scan_rows": scan_rows,
        "existing_tags": existing_tags,
        "missing_rows": missing_rows,
        "counts": build_counts(scan_rows, len(pipes)),
    }


def print_multi_view_scan_report(results, skipped_views):
    output = None
    if script is not None:
        try:
            output = script.get_output()
        except Exception:
            output = None
    output_emit(output, "## Smart Vertical Pipe Tags - All Floor Plan Views Scan")
    output_emit(output, "")
    output_emit(
        output, "No tags were created. Batch placement is intentionally deferred."
    )
    output_emit(output, "")
    output_emit(output, "### All Floor Plan Views Scan Summary")
    emit_table(
        output,
        [
            "View name",
            "View Id",
            "Level name",
            "PipeCurves checked",
            "Vertical candidates",
            "Missing tags",
            "Already tagged",
            "Missing tag type",
            "Outside active tag zone",
            "Skipped not vertical",
            "Skipped too short",
        ],
        [
            [
                safe_element_name(result.get("view")),
                (
                    linkify(output, result.get("view").Id.IntegerValue)
                    if output is not None
                    else result.get("view").Id.IntegerValue
                ),
                level_name(result.get("level_info").get("current_level")),
                result.get("counts").get("total"),
                result.get("counts").get("vertical_candidates"),
                len(result.get("missing_rows", [])),
                result.get("counts").get("skipped_already_tagged"),
                result.get("counts").get("missing_tag_type"),
                result.get("counts").get("outside_active_tag_zone"),
                result.get("counts").get("skipped_not_vertical"),
                result.get("counts").get("skipped_too_short"),
            ]
            for result in results
        ],
    )

    output_emit(output, "")
    output_emit(output, "### Missing Vertical Tags by View")
    missing_rows = []
    for result in results:
        view = result.get("view")
        level = level_name(result.get("level_info").get("current_level"))
        for row in result.get("missing_rows", []):
            geometry = row.get("geometry")
            missing_rows.append(
                [
                    safe_element_name(view),
                    level,
                    (
                        linkify(output, row.get("id"))
                        if output is not None
                        else row.get("id")
                    ),
                    row.get("intent") or "",
                    row.get("intended_tag_type") or "",
                    fmt_mm(geometry.get("min_z")) if geometry else "-",
                    fmt_mm(geometry.get("max_z")) if geometry else "-",
                    "Open this view and run active-view placement to create the tag.",
                ]
            )
    emit_table(
        output,
        [
            "View name",
            "Level",
            "Pipe ElementId",
            "Suggested intent",
            "Intended tag type",
            "Pipe min Z mm",
            "Pipe max Z mm",
            "Recommended action",
        ],
        missing_rows,
    )

    output_emit(output, "")
    output_emit(output, "### Views skipped")
    emit_table(
        output,
        ["View name", "View Id", "Reason"],
        [
            [
                safe_element_name(item.get("view")),
                (
                    item.get("view").Id.IntegerValue
                    if item.get("view") is not None
                    else "-"
                ),
                item.get("reason") or "",
            ]
            for item in skipped_views
        ],
    )


def select_active_view_missing_after_batch(results, active_view):
    for result in results:
        try:
            if result.get("view").Id.IntegerValue != active_view.Id.IntegerValue:
                continue
        except Exception:
            continue
        rows = result.get("missing_rows", [])
        if not rows:
            break
        element_ids = List[ElementId]()
        for row in rows:
            try:
                element_ids.Add(ElementId(int(row.get("id"))))
            except Exception:
                pass
        if element_ids.Count > 0:
            try:
                uidoc.Selection.SetElementIds(element_ids)
                print(
                    "Selected {} active-view missing vertical tag candidates.".format(
                        element_ids.Count
                    )
                )
            except Exception as ex:
                print("Active-view missing candidate selection failed: {}".format(ex))
        return
    print(
        "Multi-view scan completed. Open a listed view and run active-view placement to create tags."
    )


def run_multi_view_scan(active_view, tag_symbols, resolved_symbols):
    views, skipped_views = collect_batch_floor_plan_views()
    if not views:
        show_dialog("No eligible floor plan views were found for batch scan.")
        return
    if not confirm_batch_scan(len(views)):
        return
    results = []
    for view in views:
        try:
            results.append(
                scan_view_for_vertical_tags(view, tag_symbols, resolved_symbols)
            )
        except Exception as ex:
            skipped_views.append({"view": view, "reason": "Scan failed: {}".format(ex)})
    print_multi_view_scan_report(results, skipped_views)
    select_active_view_missing_after_batch(results, active_view)


# =========================
# Main
# =========================
def execute_action(action):
    active_view = uidoc.ActiveView
    scope = action.get("scope")
    mode = action.get("mode")

    if mode == "exit":
        return False

    if mode == "choose_family":
        choose_vertical_pipe_tag_family()
        return True

    if mode == "load_family":
        load_vertical_pipe_tag_family_from_rfa()
        return True

    tag_symbols = collect_pipe_tag_symbols()
    resolved_symbols = resolve_symbols_by_intent(tag_symbols)

    if mode == "scan_all_views":
        run_multi_view_scan(active_view, tag_symbols, resolved_symbols)
        return True

    if not isinstance(active_view, ViewPlan):
        show_dialog(
            "Smart Vertical Pipe Tags currently works only in floor plan views."
        )
        return True

    current_level = get_active_view_level(active_view)
    if current_level is None:
        show_dialog("Could not determine the active plan level.")
        return True

    level_info = build_level_info(current_level)
    if scope == "selected":
        pipes = collect_selected_pipes()
        if not pipes:
            show_dialog(
                "No selected PipeCurves found. Select one or more pipes and run again."
            )
            return True
    else:
        if mode == "place_missing" and not confirm_active_view_placement():
            return True
        pipes = collect_active_view_pipes(active_view)
    scan_rows = [scan_pipe(pipe, level_info, resolved_symbols) for pipe in pipes]
    existing_tags = collect_existing_vertical_tags(active_view)
    existing_tag_by_pipe_id = build_existing_vertical_tag_by_pipe_id(
        existing_tags, scan_rows
    )
    update_existing_tag_status(scan_rows, existing_tag_by_pipe_id)

    created_rows = []
    failure_rows = []
    if mode == "place_missing":
        created_rows, failure_rows = place_missing_tags(
            active_view, scan_rows, level_info
        )

    print_report(
        active_view,
        level_info,
        tag_symbols,
        resolved_symbols,
        scan_rows,
        existing_tags,
        len(pipes),
        scope,
    )
    if mode == "place_missing":
        if not created_rows:
            outside_count = len(
                [
                    row
                    for row in scan_rows
                    if row.get("status")
                    in [
                        STATUS_OUTSIDE_ACTIVE_TAG_ZONE,
                        STATUS_EXISTING_TAG_OUTSIDE_ACTIVE_TAG_ZONE,
                    ]
                ]
            )
            active_count = len(
                [
                    row
                    for row in scan_rows
                    if row.get("status")
                    in [STATUS_VERTICAL_CANDIDATE, STATUS_SKIPPED_ALREADY_TAGGED]
                ]
            )
            if outside_count and not active_count:
                print(
                    "Selected vertical pipes are outside the active view tag zone. Open the recommended level view and run the tool there."
                )
        select_after_placement(created_rows, failure_rows, scan_rows)
    else:
        select_valid_candidates(scan_rows, scope)
    return True


def main():
    get_config()
    save_config(get_config())
    while True:
        action = choose_action()
        if action is None:
            return
        keep_running = execute_action(action)
        if not keep_running:
            return


try:
    main()
except Exception as ex:
    show_dialog("Smart Vertical Pipe Tags failed.\n\n{}".format(ex))
    raise

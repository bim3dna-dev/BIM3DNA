# -*- coding: utf-8 -*-
__title__ = "HVAC\nTagging"
__author__ = "BIM3DNA"
__version__ = "1.0"
__doc__ = (
    "Scan active-view vertical ducts and manage vertical duct tag family configuration."
)

import os
import json
import math
import clr

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    Element,
    ElementId,
    Family,
    FamilySymbol,
    FilteredElementCollector,
    IFamilyLoadOptions,
    Level,
    LocationCurve,
    Transaction,
    ViewPlan,
)
from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons, TaskDialogResult

clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")

from System.Windows.Forms import (
    Form,
    ListBox,
    Button,
    Label,
    OpenFileDialog,
    DialogResult,
    FormBorderStyle,
    FormStartPosition,
    SelectionMode,
)
from System.Drawing import Point, Size
from System.Collections.Generic import List

try:
    from pyrevit import script
except Exception:
    script = None

try:
    from pyrevit import forms
except Exception:
    forms = None

try:
    from Autodesk.Revit.DB.Mechanical import Duct
except Exception:
    Duct = None


uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document


# =========================
# Config
# =========================
CONFIG_FILE_NAME = "hvac_vertical_duct_tagging_config.json"
DEFAULT_TAG_FAMILY_NAME = ""
XY_TOLERANCE_FT = 0.10
MIN_VERTICAL_HEIGHT_FT = 1.0
VERTICAL_ANGLE_DEGREES = 80.0
MIN_STORY_HEIGHT_FT = 1.64

INTENT_GOING_UP = "GOING_UP"
INTENT_GOING_DOWN = "GOING_DOWN"
INTENT_CONTINUOUS_UP = "CONTINUOUS_UP"
INTENT_CONTINUOUS_DOWN = "CONTINUOUS_DOWN"
INTENT_OUTSIDE_ACTIVE_LEVEL_BAND = "OUTSIDE_ACTIVE_LEVEL_BAND"
INTENT_UNKNOWN_VERTICAL_DUCT = "UNKNOWN_VERTICAL_DUCT"

STATUS_VERTICAL_CANDIDATE = "VERTICAL_DUCT_CANDIDATE"
STATUS_SKIPPED_NO_LOCATION = "SKIPPED_NO_LOCATION_CURVE"
STATUS_SKIPPED_NON_LINEAR = "SKIPPED_NON_LINEAR"
STATUS_SKIPPED_NOT_VERTICAL = "SKIPPED_NOT_VERTICAL"
STATUS_SKIPPED_TOO_SHORT = "SKIPPED_TOO_SHORT"
STATUS_OUTSIDE_ACTIVE_LEVEL_BAND = "SKIPPED_OUTSIDE_ACTIVE_LEVEL_BAND"
STATUS_SKIPPED_OTHER = "SKIPPED_OTHER"

VERTICAL_INTENT_KEYWORDS = [
    "vertical",
    "riser",
    "up",
    "down",
    "goingup",
    "goingdown",
    "comingup",
    "comingdown",
    "continuous",
]

DEFAULT_CONFIG = {
    "selected_duct_tag_family_name": DEFAULT_TAG_FAMILY_NAME,
    "selected_duct_tag_type_name": "",
    "selected_duct_tag_symbol_id": "",
    "last_loaded_rfa_path": "",
    "selected_family_compatibility_summary": "",
    "selected_family_compatibility_details": {},
    "xy_tolerance_ft": XY_TOLERANCE_FT,
    "min_vertical_height_ft": MIN_VERTICAL_HEIGHT_FT,
    "vertical_angle_degrees": VERTICAL_ANGLE_DEGREES,
    "show_skipped_diagnostics": False,
    "max_skipped_diagnostics_rows": 30,
    "selected_family_api_compatible": False,
    "selected_family_vertical_intent_match": False,
    "selected_family_semantic_warning": "",
    "last_used_action": "",
}

CONFIG = None


# =========================
# General helpers
# =========================
def get_script_dir():
    try:
        return os.path.dirname(__file__)
    except Exception:
        return os.getcwd()


def get_config_path():
    return os.path.join(get_script_dir(), CONFIG_FILE_NAME)


def read_config():
    cfg = dict(DEFAULT_CONFIG)
    path = get_config_path()
    if os.path.exists(path):
        try:
            with open(path, "r") as fp:
                loaded = json.load(fp)
            if isinstance(loaded, dict):
                cfg.update(loaded)
            print("PROFILE -> loaded HVAC tagging config from {}".format(path))
        except Exception as ex:
            print("PROFILE -> failed to load config: {}".format(ex))
    return cfg


def write_config(cfg):
    path = get_config_path()
    try:
        with open(path, "w") as fp:
            json.dump(cfg, fp, indent=2, sort_keys=True)
        print("PROFILE -> saved HVAC tagging config to {}".format(path))
        return True
    except Exception as ex:
        print("PROFILE -> failed to save config: {}".format(ex))
        return False


def get_config():
    global CONFIG
    if CONFIG is None:
        CONFIG = read_config()
    return CONFIG


def mm_from_ft(value):
    return float(value) * 304.8


def ft_from_mm(value):
    return float(value) / 304.8


def fmt_mm(value):
    if value is None:
        return "-"
    return str(round(mm_from_ft(value), 1))


def fmt_bool(value):
    return str(bool(value)).lower()


def safe_name(elem):
    if elem is None:
        return ""
    try:
        value = Element.Name.GetValue(elem)
        if value:
            return value
    except Exception:
        pass
    try:
        return elem.Name or ""
    except Exception:
        return ""


def normalize_name(value):
    if value is None:
        return ""
    return str(value).lower().replace(" ", "").replace("_", "").replace("-", "")


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


def is_duct_curve(elem):
    if elem is None:
        return False
    try:
        if Duct is not None and isinstance(elem, Duct):
            return True
    except Exception:
        pass
    try:
        return elem.Category and elem.Category.Id.IntegerValue == int(
            BuiltInCategory.OST_DuctCurves
        )
    except Exception:
        return False


def is_duct_tag_symbol(symbol):
    try:
        return (
            isinstance(symbol, FamilySymbol)
            and symbol.Category is not None
            and symbol.Category.Id.IntegerValue == int(BuiltInCategory.OST_DuctTags)
        )
    except Exception:
        return False


def get_type_name(elem):
    try:
        type_elem = doc.GetElement(elem.GetTypeId())
        return safe_name(type_elem) or "<Unknown Type>"
    except Exception:
        return "<Unknown Type>"


def get_type_family_name(elem):
    try:
        type_elem = doc.GetElement(elem.GetTypeId())
        fam_name = getattr(type_elem, "FamilyName", "")
        if fam_name:
            return fam_name
    except Exception:
        pass
    try:
        type_elem = doc.GetElement(elem.GetTypeId())
        return safe_name(type_elem.Family)
    except Exception:
        pass
    return ""


def get_family_name_from_symbol(symbol):
    try:
        if symbol.Family is not None:
            return safe_name(symbol.Family)
    except Exception:
        pass
    try:
        return symbol.FamilyName or ""
    except Exception:
        pass
    return ""


def get_symbol_type_name(symbol):
    return safe_name(symbol) or "<Unnamed Type>"


def has_vertical_intent_keywords(value):
    norm = normalize_name(value)
    for keyword in VERTICAL_INTENT_KEYWORDS:
        if normalize_name(keyword) in norm:
            return True
    return False


def family_has_vertical_intent_match(family_name, symbols):
    if has_vertical_intent_keywords(family_name):
        return True
    for symbol in symbols:
        if has_vertical_intent_keywords(get_symbol_type_name(symbol)):
            return True
        if has_vertical_intent_keywords(get_family_name_from_symbol(symbol)):
            return True
    return False


def winforms_select_one(title, message, options):
    if not options:
        return None
    form = Form()
    form.Text = title
    form.Size = Size(560, 420)
    form.StartPosition = FormStartPosition.CenterScreen
    form.FormBorderStyle = FormBorderStyle.FixedDialog
    label = Label()
    label.Text = message
    label.Location = Point(12, 12)
    label.Size = Size(520, 30)
    listbox = ListBox()
    listbox.Location = Point(12, 48)
    listbox.Size = Size(520, 270)
    listbox.SelectionMode = SelectionMode.One
    for option in options:
        listbox.Items.Add(option)
    listbox.SelectedIndex = 0
    ok = Button()
    ok.Text = "OK"
    ok.Location = Point(356, 334)
    ok.DialogResult = DialogResult.OK
    cancel = Button()
    cancel.Text = "Cancel"
    cancel.Location = Point(444, 334)
    cancel.DialogResult = DialogResult.Cancel
    form.Controls.Add(label)
    form.Controls.Add(listbox)
    form.Controls.Add(ok)
    form.Controls.Add(cancel)
    form.AcceptButton = ok
    form.CancelButton = cancel
    result = form.ShowDialog()
    if result == DialogResult.OK and listbox.SelectedItem is not None:
        return str(listbox.SelectedItem)
    return None


def select_one(title, message, options):
    if forms is not None:
        try:
            return forms.SelectFromList.show(options, title=title, multiselect=False)
        except Exception:
            pass
    return winforms_select_one(title, message, options)


def ask_yes_no(title, message):
    result = TaskDialog.Show(
        title, message, TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No
    )
    return result == TaskDialogResult.Yes


# =========================
# Duct tag family/type configuration
# =========================
def collect_loaded_duct_tag_symbols():
    symbols = []
    try:
        collector = FilteredElementCollector(doc).OfClass(FamilySymbol)
        for symbol in collector:
            if is_duct_tag_symbol(symbol):
                symbols.append(symbol)
    except Exception as ex:
        print("CONFIG UI -> failed collecting duct tag symbols: {}".format(ex))
    return symbols


def group_symbols_by_family(symbols):
    grouped = {}
    for symbol in symbols:
        family_name = get_family_name_from_symbol(symbol)
        if not family_name:
            family_name = "<Unknown Family>"
        grouped.setdefault(family_name, []).append(symbol)
    return grouped


def analyze_duct_tag_family_compatibility(family_name, symbols):
    type_names = []
    active_or_activatable = 0
    for symbol in symbols:
        type_name = get_symbol_type_name(symbol)
        type_names.append(type_name)
        try:
            if symbol.IsActive:
                active_or_activatable += 1
            else:
                active_or_activatable += 1
        except Exception:
            pass
    is_compatible = len(symbols) > 0 and active_or_activatable > 0
    vertical_intent_match = family_has_vertical_intent_match(family_name, symbols)
    semantic_warning = ""
    if is_compatible and not vertical_intent_match:
        semantic_warning = (
            "The selected duct tag is API-compatible, but no vertical/riser/up/down "
            "intent keywords were found. It may be a generic duct size tag."
        )
    details = {
        "family_name": family_name,
        "type_names": sorted(type_names),
        "matched_required_type_count": active_or_activatable,
        "total_type_count": len(symbols),
        "missing_required_type_names": [],
        "is_compatible": bool(is_compatible),
        "api_compatible": bool(is_compatible),
        "vertical_intent_match": bool(vertical_intent_match),
        "semantic_warning": semantic_warning,
    }
    print(
        "FAMILY COMPATIBILITY -> {} api_compatible={} vertical_intent_match={} matched={}/{}".format(
            family_name,
            is_compatible,
            vertical_intent_match,
            active_or_activatable,
            max(1, len(symbols)),
        )
    )
    return details


def family_picker_labels(grouped):
    compatible = []
    incompatible = []
    label_to_family = {}
    for family_name, symbols in grouped.items():
        details = analyze_duct_tag_family_compatibility(family_name, symbols)
        total = max(1, details["total_type_count"])
        if details["is_compatible"]:
            label = "{}  [compatible: {}/{}]".format(
                family_name, details["matched_required_type_count"], total
            )
            compatible.append(label)
        else:
            label = "{}  [incompatible: {}/{}]".format(
                family_name, details["matched_required_type_count"], total
            )
            incompatible.append(label)
        label_to_family[label] = family_name
    return sorted(compatible) + sorted(incompatible), label_to_family


def choose_loaded_vertical_duct_tag_family():
    cfg = get_config()
    symbols = collect_loaded_duct_tag_symbols()
    grouped = group_symbols_by_family(symbols)
    print("CONFIG UI -> available duct tag families: {}".format(len(grouped)))
    if not grouped:
        TaskDialog.Show(
            "Choose loaded vertical duct tag family",
            "No loaded duct tag families were found in this project.",
        )
        return

    labels, label_to_family = family_picker_labels(grouped)
    picked_label = select_one(
        "Choose loaded vertical duct tag family",
        "Select a loaded duct tag family.",
        labels,
    )
    if not picked_label:
        return
    family_name = label_to_family.get(picked_label)
    symbols_for_family = grouped.get(family_name, [])
    details = analyze_duct_tag_family_compatibility(family_name, symbols_for_family)
    if not details["is_compatible"]:
        if not ask_yes_no(
            "Choose loaded vertical duct tag family",
            "Selected family does not appear to be a compatible duct tag family. Continue anyway?",
        ):
            return
    elif not details.get("vertical_intent_match"):
        if not ask_yes_no(
            "Choose loaded vertical duct tag family",
            details.get("semantic_warning")
            or "The selected duct tag is API-compatible, but no vertical/riser/up/down intent keywords were found. Continue anyway?",
        ):
            return

    type_names = sorted([get_symbol_type_name(s) for s in symbols_for_family])
    selected_type_name = type_names[0] if type_names else ""
    selected_symbol_id = ""
    if symbols_for_family:
        selected_symbol_id = str(symbols_for_family[0].Id.IntegerValue)

    cfg["selected_duct_tag_family_name"] = family_name
    cfg["selected_duct_tag_type_name"] = selected_type_name
    cfg["selected_duct_tag_symbol_id"] = selected_symbol_id
    cfg["selected_family_compatibility_summary"] = (
        "compatible" if details["is_compatible"] else "incompatible"
    )
    cfg["selected_family_compatibility_details"] = details
    cfg["selected_family_api_compatible"] = bool(details.get("api_compatible"))
    cfg["selected_family_vertical_intent_match"] = bool(
        details.get("vertical_intent_match")
    )
    cfg["selected_family_semantic_warning"] = details.get("semantic_warning") or ""
    cfg["last_used_action"] = "Choose loaded vertical duct tag family"
    write_config(cfg)

    output = script.get_output() if script is not None else None
    output_emit(output, "# Smart HVAC Vertical Duct Tags - Family Selection")
    output_emit(output, "- Selected family: `{}`".format(family_name))
    output_emit(
        output,
        "- Compatibility: `{}`".format(cfg["selected_family_compatibility_summary"]),
    )
    output_emit(
        output,
        "- API compatible: `{}`".format(cfg["selected_family_api_compatible"]),
    )
    output_emit(
        output,
        "- Vertical intent match: `{}`".format(
            cfg["selected_family_vertical_intent_match"]
        ),
    )
    if cfg["selected_family_semantic_warning"]:
        output_emit(
            output,
            "- Semantic warning: `{}`".format(cfg["selected_family_semantic_warning"]),
        )
        output_emit(
            output,
            "- Status: Selected tag is valid for duct tagging, but not optimized for vertical/riser intent tagging.",
        )
    output_emit(
        output,
        "- Matched types: `{}/{}`".format(
            details["matched_required_type_count"], details["total_type_count"]
        ),
    )
    output_emit(output, "- Available type names: `{}`".format(", ".join(type_names)))
    output_emit(output, "- Config path: `{}`".format(get_config_path()))


class DuctTagFamilyLoadOptions(IFamilyLoadOptions):
    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        overwriteParameterValues.Value = True
        return True

    def OnSharedFamilyFound(
        self, sharedFamily, familyInUse, source, overwriteParameterValues
    ):
        overwriteParameterValues.Value = True
        return True


def pick_rfa_file():
    if forms is not None:
        try:
            return forms.pick_file(
                file_ext="rfa", title="Load vertical duct tag family from .rfa"
            )
        except Exception:
            pass
    dialog = OpenFileDialog()
    dialog.Title = "Load vertical duct tag family from .rfa"
    dialog.Filter = "Revit Families (*.rfa)|*.rfa"
    if dialog.ShowDialog() == DialogResult.OK:
        return dialog.FileName
    return None


def load_vertical_duct_tag_family_from_rfa():
    cfg = get_config()
    path = pick_rfa_file()
    if not path:
        return
    if not path.lower().endswith(".rfa"):
        TaskDialog.Show(
            "Load vertical duct tag family from .rfa",
            "Selected file is not an .rfa file.",
        )
        return

    before_families = set(
        group_symbols_by_family(collect_loaded_duct_tag_symbols()).keys()
    )
    loaded = False
    reason = ""
    t = Transaction(doc, "Smart HVAC Vertical Duct Tags - Load Family")
    try:
        t.Start()
        try:
            loaded = bool(doc.LoadFamily(path, DuctTagFamilyLoadOptions()))
        except Exception:
            loaded = bool(doc.LoadFamily(path))
        t.Commit()
    except Exception as ex:
        reason = str(ex)
        try:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
        except Exception:
            pass
    print("FAMILY LOAD -> path: {}".format(path))
    print("FAMILY LOAD -> success: {}; reason: {}".format(loaded, reason))

    after_grouped = group_symbols_by_family(collect_loaded_duct_tag_symbols())
    after_families = set(after_grouped.keys())
    new_families = sorted(list(after_families.difference(before_families)))
    selected_family = new_families[0] if new_families else ""
    if not selected_family and after_grouped:
        selected_family = select_one(
            "Choose loaded vertical duct tag family",
            "Family loaded or already existed. Select the duct tag family to use.",
            sorted(after_grouped.keys()),
        )

    if selected_family and selected_family in after_grouped:
        details = analyze_duct_tag_family_compatibility(
            selected_family, after_grouped[selected_family]
        )
        symbols_for_family = after_grouped[selected_family]
        cfg["selected_duct_tag_family_name"] = selected_family
        cfg["selected_duct_tag_type_name"] = (
            get_symbol_type_name(symbols_for_family[0]) if symbols_for_family else ""
        )
        cfg["selected_duct_tag_symbol_id"] = (
            str(symbols_for_family[0].Id.IntegerValue) if symbols_for_family else ""
        )
        cfg["selected_family_compatibility_summary"] = (
            "compatible" if details["is_compatible"] else "incompatible"
        )
        cfg["selected_family_compatibility_details"] = details
        cfg["selected_family_api_compatible"] = bool(details.get("api_compatible"))
        cfg["selected_family_vertical_intent_match"] = bool(
            details.get("vertical_intent_match")
        )
        cfg["selected_family_semantic_warning"] = details.get("semantic_warning") or ""
    cfg["last_loaded_rfa_path"] = path
    cfg["last_used_action"] = "Load vertical duct tag family from .rfa"
    write_config(cfg)

    output = script.get_output() if script is not None else None
    output_emit(output, "# Smart HVAC Vertical Duct Tags - Family Load")
    output_emit(output, "- Selected file: `{}`".format(path))
    output_emit(output, "- Load success: `{}`".format(loaded))
    output_emit(output, "- Loaded/selected family: `{}`".format(selected_family or "-"))
    if selected_family in after_grouped:
        output_emit(
            output,
            "- Available type names: `{}`".format(
                ", ".join(
                    [get_symbol_type_name(s) for s in after_grouped[selected_family]]
                )
            ),
        )
    if reason:
        output_emit(output, "- Reason: `{}`".format(reason))


def resolve_configured_symbol():
    cfg = get_config()
    family_name = cfg.get("selected_duct_tag_family_name") or ""
    type_name = cfg.get("selected_duct_tag_type_name") or ""
    symbols = collect_loaded_duct_tag_symbols()
    family_norm = normalize_name(family_name)
    type_norm = normalize_name(type_name)
    fallback = None
    for symbol in symbols:
        if fallback is None:
            fallback = symbol
        if (
            family_norm
            and normalize_name(get_family_name_from_symbol(symbol)) != family_norm
        ):
            continue
        if type_norm and normalize_name(get_symbol_type_name(symbol)) != type_norm:
            continue
        print(
            "CONFIG RESOLVE -> duct tag resolved: {} / {}".format(
                get_family_name_from_symbol(symbol), get_symbol_type_name(symbol)
            )
        )
        return symbol
    if fallback is not None:
        print(
            "CONFIG RESOLVE -> configured duct tag unresolved; fallback available: {} / {}".format(
                get_family_name_from_symbol(fallback), get_symbol_type_name(fallback)
            )
        )
    else:
        print("CONFIG RESOLVE -> no duct tag symbols loaded.")
    return None


def activate_symbol_if_needed(symbol):
    if symbol is None:
        return False
    try:
        if symbol.IsActive:
            return True
    except Exception:
        return True
    t = Transaction(doc, "Smart HVAC Vertical Duct Tags - Activate Symbol")
    try:
        t.Start()
        symbol.Activate()
        t.Commit()
        return True
    except Exception:
        try:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
        except Exception:
            pass
    return False


# =========================
# Active level / scan logic
# =========================
def get_active_level(view):
    try:
        if isinstance(view, ViewPlan) and view.GenLevel is not None:
            return view.GenLevel
    except Exception:
        pass
    return None


def collect_project_levels():
    levels = []
    try:
        for level in FilteredElementCollector(doc).OfClass(Level):
            levels.append(level)
    except Exception:
        pass
    levels.sort(key=lambda x: x.Elevation)
    return levels


def get_active_level_band(active_level, levels):
    elev = active_level.Elevation
    lower = elev - ft_from_mm(500.0)
    upper = elev + ft_from_mm(4000.0)
    for idx, level in enumerate(levels):
        if level.Id.IntegerValue != active_level.Id.IntegerValue:
            continue
        prev_level = levels[idx - 1] if idx > 0 else None
        next_level = levels[idx + 1] if idx < len(levels) - 1 else None
        if prev_level and abs(elev - prev_level.Elevation) > MIN_STORY_HEIGHT_FT:
            lower = (prev_level.Elevation + elev) / 2.0
        if next_level and abs(next_level.Elevation - elev) > MIN_STORY_HEIGHT_FT:
            upper = (next_level.Elevation + elev) / 2.0
        break
    return lower, upper


def get_duct_line_endpoints(duct):
    try:
        loc = duct.Location
        if not isinstance(loc, LocationCurve):
            return None, None, STATUS_SKIPPED_NO_LOCATION
        curve = loc.Curve
        if curve is None:
            return None, None, STATUS_SKIPPED_NO_LOCATION
        try:
            p0 = curve.GetEndPoint(0)
            p1 = curve.GetEndPoint(1)
            return p0, p1, ""
        except Exception:
            return None, None, STATUS_SKIPPED_NON_LINEAR
    except Exception:
        return None, None, STATUS_SKIPPED_NO_LOCATION


def classify_vertical_duct(duct, active_level, band_bottom, band_top, cfg):
    p0, p1, endpoint_status = get_duct_line_endpoints(duct)
    row = {
        "duct_id": duct.Id.IntegerValue,
        "type_name": get_type_name(duct),
        "type_family_name": get_type_family_name(duct),
        "start_z": None,
        "end_z": None,
        "min_z": None,
        "max_z": None,
        "height": None,
        "xy_offset": None,
        "vertical_angle": None,
        "status": endpoint_status or STATUS_VERTICAL_CANDIDATE,
        "intent": INTENT_UNKNOWN_VERTICAL_DUCT,
        "inside_active_level_band": False,
        "suggested_tag_type": "",
        "reason": "",
    }
    if p0 is None or p1 is None:
        row["reason"] = endpoint_status
        return row

    dz = abs(p1.Z - p0.Z)
    dx = p1.X - p0.X
    dy = p1.Y - p0.Y
    xy = math.sqrt(dx * dx + dy * dy)
    angle = math.degrees(math.atan2(dz, xy)) if xy > 1e-9 else 90.0
    min_z = min(p0.Z, p1.Z)
    max_z = max(p0.Z, p1.Z)
    row["start_z"] = p0.Z
    row["end_z"] = p1.Z
    row["min_z"] = min_z
    row["max_z"] = max_z
    row["height"] = dz
    row["xy_offset"] = xy
    row["vertical_angle"] = angle

    if dz < float(cfg.get("min_vertical_height_ft", MIN_VERTICAL_HEIGHT_FT)):
        row["status"] = STATUS_SKIPPED_TOO_SHORT
        row["reason"] = "Vertical height below configured minimum."
        return row
    if xy > float(cfg.get("xy_tolerance_ft", XY_TOLERANCE_FT)) and angle < float(
        cfg.get("vertical_angle_degrees", VERTICAL_ANGLE_DEGREES)
    ):
        row["status"] = STATUS_SKIPPED_NOT_VERTICAL
        row["reason"] = "XY movement/angle does not meet vertical duct threshold."
        return row

    row["inside_active_level_band"] = min_z <= band_top and max_z >= band_bottom
    if not row["inside_active_level_band"]:
        row["status"] = STATUS_OUTSIDE_ACTIVE_LEVEL_BAND
        row["intent"] = INTENT_OUTSIDE_ACTIVE_LEVEL_BAND
        row["reason"] = "Vertical duct does not intersect the active level band."
        return row

    level_z = active_level.Elevation
    if min_z < level_z and max_z > level_z:
        row["intent"] = INTENT_CONTINUOUS_UP if p1.Z > p0.Z else INTENT_CONTINUOUS_DOWN
    elif p1.Z > p0.Z:
        row["intent"] = INTENT_GOING_UP
    elif p1.Z < p0.Z:
        row["intent"] = INTENT_GOING_DOWN
    else:
        row["intent"] = INTENT_UNKNOWN_VERTICAL_DUCT
    row["status"] = STATUS_VERTICAL_CANDIDATE
    row["reason"] = "Vertical duct candidate in active level band."
    return row


def loose_match_type_for_intent(intent, symbols):
    intent_keywords = {
        INTENT_GOING_UP: ["goingup", "up", "comingup", "riser", "vertical"],
        INTENT_GOING_DOWN: ["goingdown", "down", "comingdown", "riser", "vertical"],
        INTENT_CONTINUOUS_UP: ["continuousup", "continuous", "up", "riser", "vertical"],
        INTENT_CONTINUOUS_DOWN: [
            "continuousdown",
            "continuous",
            "down",
            "riser",
            "vertical",
        ],
    }
    keywords = intent_keywords.get(intent, [])
    for keyword in keywords:
        for symbol in symbols:
            type_name = get_symbol_type_name(symbol)
            family_name = get_family_name_from_symbol(symbol)
            if not has_vertical_intent_keywords(
                type_name
            ) and not has_vertical_intent_keywords(family_name):
                continue
            if keyword in normalize_name(type_name):
                return get_symbol_type_name(symbol)
    return ""


def scan_active_view_vertical_ducts():
    cfg = get_config()
    cfg["last_used_action"] = "Scan active view vertical ducts"
    write_config(cfg)

    view = doc.ActiveView
    active_level = get_active_level(view)
    if active_level is None:
        TaskDialog.Show(
            "Smart HVAC Vertical Duct Tags",
            "HVAC vertical duct tagging currently works only in floor plan views with a valid level.",
        )
        return

    output = script.get_output() if script is not None else None
    levels = collect_project_levels()
    band_bottom, band_top = get_active_level_band(active_level, levels)
    symbols = collect_loaded_duct_tag_symbols()
    selected_family = cfg.get("selected_duct_tag_family_name") or ""
    family_symbols = []
    for symbol in symbols:
        if not selected_family or normalize_name(
            get_family_name_from_symbol(symbol)
        ) == normalize_name(selected_family):
            family_symbols.append(symbol)

    rows = []
    checked = 0
    try:
        collector = (
            FilteredElementCollector(doc, view.Id)
            .OfCategory(BuiltInCategory.OST_DuctCurves)
            .WhereElementIsNotElementType()
        )
        for duct in collector:
            if not is_duct_curve(duct):
                continue
            checked += 1
            row = classify_vertical_duct(duct, active_level, band_bottom, band_top, cfg)
            row["suggested_tag_type"] = loose_match_type_for_intent(
                row["intent"], family_symbols
            )
            if (
                row["status"] == STATUS_VERTICAL_CANDIDATE
                and not row["suggested_tag_type"]
                and family_symbols
            ):
                row["reason"] = (
                    row["reason"]
                    + " No selected duct tag type matched vertical intent keywords."
                )
            rows.append(row)
    except Exception as ex:
        TaskDialog.Show("Smart HVAC Vertical Duct Tags", "Scan failed: {}".format(ex))
        return

    candidate_count = len([r for r in rows if r["status"] == STATUS_VERTICAL_CANDIDATE])
    candidate_rows = [r for r in rows if r["status"] == STATUS_VERTICAL_CANDIDATE]
    skipped_rows = [r for r in rows if r["status"] != STATUS_VERTICAL_CANDIDATE]
    outside_count = len(
        [r for r in rows if r["status"] == STATUS_OUTSIDE_ACTIVE_LEVEL_BAND]
    )
    not_vertical_count = len(
        [r for r in rows if r["status"] == STATUS_SKIPPED_NOT_VERTICAL]
    )
    too_short_count = len([r for r in rows if r["status"] == STATUS_SKIPPED_TOO_SHORT])
    no_location_count = len(
        [r for r in rows if r["status"] == STATUS_SKIPPED_NO_LOCATION]
    )
    non_linear_count = len(
        [r for r in rows if r["status"] == STATUS_SKIPPED_NON_LINEAR]
    )
    known_skipped = (
        outside_count
        + not_vertical_count
        + too_short_count
        + no_location_count
        + non_linear_count
    )
    other_skipped_count = max(0, len(skipped_rows) - known_skipped)

    print("SCAN HVAC VERTICAL DUCTS -> checked: {}".format(checked))
    print("SCAN HVAC VERTICAL DUCTS -> candidates: {}".format(candidate_count))
    print(
        "SCAN HVAC VERTICAL DUCTS -> outside active level band: {}".format(
            outside_count
        )
    )
    candidate_ids = [ElementId(int(r["duct_id"])) for r in candidate_rows]
    if candidate_ids:
        try:
            id_list = List[ElementId]()
            for eid in candidate_ids:
                id_list.Add(eid)
            uidoc.Selection.SetElementIds(id_list)
            print(
                "SCAN HVAC VERTICAL DUCTS -> selected candidate ids: {}".format(
                    [r["duct_id"] for r in candidate_rows]
                )
            )
        except Exception as ex:
            print(
                "SCAN HVAC VERTICAL DUCTS -> candidate selection failed: {}".format(ex)
            )

    output_emit(output, "# Smart HVAC Vertical Duct Tags - Active View Scan")
    output_emit(output, "")
    output_emit(output, "## Active View / Level Context")
    output_emit(output, "- Active view: `{}`".format(safe_name(view)))
    output_emit(output, "- Active level: `{}`".format(safe_name(active_level)))
    output_emit(
        output, "- Level elevation mm: `{}`".format(fmt_mm(active_level.Elevation))
    )
    output_emit(output, "- Level band bottom mm: `{}`".format(fmt_mm(band_bottom)))
    output_emit(output, "- Level band top mm: `{}`".format(fmt_mm(band_top)))
    output_emit(output, "")
    output_emit(output, "## Summary")
    output_emit(output, "- DuctCurves checked: `{}`".format(checked))
    output_emit(output, "- Vertical duct candidates: `{}`".format(candidate_count))
    output_emit(output, "- Outside active level band: `{}`".format(outside_count))
    output_emit(output, "- Skipped not vertical: `{}`".format(not_vertical_count))
    output_emit(output, "- Skipped too short: `{}`".format(too_short_count))
    output_emit(output, "- Skipped no LocationCurve: `{}`".format(no_location_count))
    output_emit(output, "- Skipped non-linear curve: `{}`".format(non_linear_count))
    output_emit(output, "- Skipped other: `{}`".format(other_skipped_count))
    output_emit(
        output, "- Selected duct tag family: `{}`".format(selected_family or "-")
    )
    unresolved_intent = (
        selected_family
        and bool(cfg.get("selected_family_api_compatible", False))
        and len(family_symbols) > 0
        and not any([r.get("suggested_tag_type") for r in candidate_rows])
    )
    if unresolved_intent:
        output_emit(
            output,
            "- Tag intent status: Selected duct tag family is API-compatible, but no vertical/riser intent type was resolved.",
        )
    output_emit(output, "")
    output_emit(output, "## Vertical Duct Candidates")
    headers = [
        "Duct ElementId",
        "Type family",
        "Type",
        "Start Z mm",
        "End Z mm",
        "Min Z mm",
        "Max Z mm",
        "Height mm",
        "XY offset mm",
        "Angle deg",
        "Intent",
        "Suggested tag type",
        "Reason",
    ]
    output_emit(output, "|" + "|".join(headers) + "|")
    output_emit(output, "|" + "|".join(["---"] * len(headers)) + "|")
    for row in candidate_rows:
        values = [
            linkify(output, row["duct_id"]),
            row["type_family_name"] or "-",
            row["type_name"],
            fmt_mm(row["start_z"]),
            fmt_mm(row["end_z"]),
            fmt_mm(row["min_z"]),
            fmt_mm(row["max_z"]),
            fmt_mm(row["height"]),
            fmt_mm(row["xy_offset"]),
            (
                str(round(row["vertical_angle"], 1))
                if row["vertical_angle"] is not None
                else "-"
            ),
            row["intent"],
            row["suggested_tag_type"] or "-",
            row["reason"],
        ]
        output_emit(
            output, "|" + "|".join([str(v).replace("\n", " ") for v in values]) + "|"
        )

    if not candidate_rows:
        output_emit(output, "|-|-|-|-|-|-|-|-|-|-|-|-|-|")

    output_emit(output, "")
    output_emit(output, "## Skipped Duct Summary")
    output_emit(output, "- Skipped too short: `{}`".format(too_short_count))
    output_emit(output, "- Skipped not vertical: `{}`".format(not_vertical_count))
    output_emit(output, "- Skipped no LocationCurve: `{}`".format(no_location_count))
    output_emit(
        output, "- Skipped outside active level band: `{}`".format(outside_count)
    )
    output_emit(output, "- Skipped non-linear curve: `{}`".format(non_linear_count))
    output_emit(output, "- Skipped other: `{}`".format(other_skipped_count))

    output_emit(output, "")
    output_emit(output, "## Skipped Duct Diagnostics")
    show_skipped = bool(cfg.get("show_skipped_diagnostics", False))
    max_skipped = int(cfg.get("max_skipped_diagnostics_rows", 30) or 30)
    if not show_skipped:
        output_emit(
            output,
            "Skipped diagnostics are disabled. Set `show_skipped_diagnostics` to `true` in config to show details.",
        )
    else:
        output_emit(
            output,
            "Skipped diagnostics limited to first {} rows. Full debug output can be enabled in config.".format(
                max_skipped
            ),
        )
        skipped_headers = [
            "Duct ElementId",
            "Type family",
            "Type",
            "Height mm",
            "XY offset mm",
            "Angle deg",
            "Classification",
            "Reason",
        ]
        output_emit(output, "|" + "|".join(skipped_headers) + "|")
        output_emit(output, "|" + "|".join(["---"] * len(skipped_headers)) + "|")
        for row in skipped_rows[:max_skipped]:
            values = [
                linkify(output, row["duct_id"]),
                row["type_family_name"] or "-",
                row["type_name"],
                fmt_mm(row["height"]),
                fmt_mm(row["xy_offset"]),
                (
                    str(round(row["vertical_angle"], 1))
                    if row["vertical_angle"] is not None
                    else "-"
                ),
                row["status"],
                row["reason"],
            ]
            output_emit(
                output,
                "|" + "|".join([str(v).replace("\n", " ") for v in values]) + "|",
            )


def show_current_hvac_tagging_configuration():
    cfg = get_config()
    output = script.get_output() if script is not None else None
    output_emit(output, "# Smart HVAC Vertical Duct Tags - Current Configuration")
    output_emit(output, "- Config path: `{}`".format(get_config_path()))
    for key in sorted(cfg.keys()):
        output_emit(output, "- `{}`: `{}`".format(key, cfg.get(key)))
    print("CONFIG UI -> current HVAC tagging configuration shown.")


# =========================
# Main menu
# =========================
def main_menu():
    actions = [
        "Scan active view vertical ducts",
        "Choose loaded vertical duct tag family",
        "Load vertical duct tag family from .rfa",
        "Show current HVAC tagging configuration",
        "Exit",
    ]
    while True:
        choice = select_one("Smart HVAC Vertical Duct Tags", "Choose action", actions)
        if not choice or choice == "Exit":
            return
        if choice == "Scan active view vertical ducts":
            scan_active_view_vertical_ducts()
        elif choice == "Choose loaded vertical duct tag family":
            choose_loaded_vertical_duct_tag_family()
        elif choice == "Load vertical duct tag family from .rfa":
            load_vertical_duct_tag_family_from_rfa()
        elif choice == "Show current HVAC tagging configuration":
            show_current_hvac_tagging_configuration()


if __name__ == "__main__":
    main_menu()

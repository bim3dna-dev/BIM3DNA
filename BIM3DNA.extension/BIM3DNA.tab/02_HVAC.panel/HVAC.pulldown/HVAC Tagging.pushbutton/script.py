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
    IndependentTag,
    Level,
    LocationCurve,
    Reference,
    TagMode,
    TagOrientation,
    Transaction,
    TransactionGroup,
    ViewPlan,
    XYZ,
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
INTENT_COMING_UP = "COMING_UP"
INTENT_COMING_DOWN = "COMING_DOWN"
INTENT_CONTINUOUS_UP = "CONTINUOUS_UP"
INTENT_CONTINUOUS_DOWN = "CONTINUOUS_DOWN"
INTENT_OUTSIDE_ACTIVE_LEVEL_BAND = "OUTSIDE_ACTIVE_LEVEL_BAND"
INTENT_UNKNOWN_VERTICAL_DUCT = "UNKNOWN_VERTICAL_DUCT"
INTENT_GEOMETRY_FALLBACK = "GEOMETRY_FALLBACK"

STATUS_VERTICAL_CANDIDATE = "VERTICAL_DUCT_CANDIDATE"
STATUS_SKIPPED_NO_LOCATION = "SKIPPED_NO_LOCATION_CURVE"
STATUS_SKIPPED_NON_LINEAR = "SKIPPED_NON_LINEAR"
STATUS_SKIPPED_NOT_VERTICAL = "SKIPPED_NOT_VERTICAL"
STATUS_SKIPPED_TOO_SHORT = "SKIPPED_TOO_SHORT"
STATUS_OUTSIDE_ACTIVE_LEVEL_BAND = "SKIPPED_OUTSIDE_ACTIVE_LEVEL_BAND"
STATUS_SKIPPED_OTHER = "SKIPPED_OTHER"
STATUS_SKIPPED_UNCONFIGURED_SYSTEM_INTENT = "SKIPPED_UNCONFIGURED_SYSTEM_INTENT"
STATUS_SKIPPED_ALREADY_TAGGED_IN_VIEW = "SKIPPED_ALREADY_TAGGED_IN_VIEW"

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
    "system_intent_overrides": {},
    "tag_offset_x_ft": 0.0,
    "tag_offset_y_ft": 0.0,
    "tag_offset_x_mm": 0.0,
    "tag_offset_y_mm": 0.0,
    "tag_add_leader": False,
    "batch_include_dependent_views": False,
    "batch_view_name_include_filter": "",
    "batch_view_name_exclude_filter": "",
    "last_used_action": "",
}

ALLOWED_SYSTEM_INTENT_OVERRIDES = [
    INTENT_GEOMETRY_FALLBACK,
    INTENT_CONTINUOUS_UP,
    INTENT_CONTINUOUS_DOWN,
    INTENT_GOING_UP,
    INTENT_GOING_DOWN,
    INTENT_COMING_UP,
    INTENT_COMING_DOWN,
]

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


def output_table(output, headers, rows):
    output_emit(output, "|" + "|".join(headers) + "|")
    output_emit(output, "|" + "|".join(["---"] * len(headers)) + "|")
    if not rows:
        output_emit(output, "|" + "|".join(["-"] * len(headers)) + "|")
        return
    for values in rows:
        output_emit(
            output,
            "|" + "|".join([str(value).replace("\n", " ") for value in values]) + "|",
        )


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


def safe_param_to_text(param):
    if param is None:
        return "-"
    try:
        value = param.AsValueString()
        if value:
            return str(value)
    except Exception:
        pass
    try:
        value = param.AsString()
        if value:
            return str(value)
    except Exception:
        pass
    try:
        elem_id = param.AsElementId()
        if elem_id is not None and elem_id.IntegerValue > 0:
            elem = doc.GetElement(elem_id)
            name = safe_name(elem)
            if name:
                return name
            return str(elem_id.IntegerValue)
    except Exception:
        pass
    try:
        value = param.AsInteger()
        return str(value)
    except Exception:
        pass
    try:
        value = param.AsDouble()
        return str(value)
    except Exception:
        pass
    return "-"


def get_builtin_param_value(elem, bip_name):
    try:
        bip = getattr(BuiltInParameter, bip_name)
    except Exception:
        return "-"
    try:
        return safe_param_to_text(elem.get_Parameter(bip))
    except Exception:
        return "-"


def get_lookup_param_value(elem, param_name):
    try:
        return safe_param_to_text(elem.LookupParameter(param_name))
    except Exception:
        return "-"


def first_non_empty(values):
    for value in values:
        if value is None:
            continue
        text = str(value)
        if text and text != "-":
            return text
    return "-"


def get_mep_system_metadata(duct):
    mep_system_name = "-"
    mep_system_type = "-"
    try:
        mep_system = duct.MEPSystem
        if mep_system is not None:
            mep_system_name = safe_name(mep_system) or "-"
            try:
                type_elem = doc.GetElement(mep_system.GetTypeId())
                type_name = safe_name(type_elem)
                if type_name:
                    mep_system_type = type_name
            except Exception:
                pass
            if mep_system_type == "-":
                try:
                    mep_system_type = mep_system.GetType().Name or "-"
                except Exception:
                    pass
    except Exception:
        pass

    system_name = first_non_empty(
        [
            get_builtin_param_value(duct, "RBS_SYSTEM_NAME_PARAM"),
            get_lookup_param_value(duct, "System Name"),
        ]
    )
    system_type = first_non_empty(
        [
            get_builtin_param_value(duct, "RBS_DUCT_SYSTEM_TYPE_PARAM"),
            get_lookup_param_value(duct, "System Type"),
        ]
    )
    system_classification = first_non_empty(
        [
            get_builtin_param_value(duct, "RBS_SYSTEM_CLASSIFICATION_PARAM"),
            get_lookup_param_value(duct, "System Classification"),
        ]
    )
    return {
        "system_name": system_name,
        "system_type": system_type,
        "system_classification": system_classification,
        "mep_system_name": mep_system_name,
        "mep_system_type": mep_system_type,
    }


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


def prompt_string(title, default_value):
    if forms is not None:
        try:
            return forms.ask_for_string(default=str(default_value), prompt=title)
        except Exception:
            pass
    options = [str(default_value), ""]
    return select_one(title, "Enter a value, or choose blank to keep current.", options)


def get_tag_offset_x_ft(cfg):
    if "tag_offset_x_mm" in cfg:
        try:
            return ft_from_mm(float(cfg.get("tag_offset_x_mm") or 0.0))
        except Exception:
            pass
    return float(cfg.get("tag_offset_x_ft", 0.0) or 0.0)


def get_tag_offset_y_ft(cfg):
    if "tag_offset_y_mm" in cfg:
        try:
            return ft_from_mm(float(cfg.get("tag_offset_y_mm") or 0.0))
        except Exception:
            pass
    return float(cfg.get("tag_offset_y_ft", 0.0) or 0.0)


def sync_tag_offset_feet_from_mm(cfg):
    try:
        cfg["tag_offset_x_ft"] = ft_from_mm(float(cfg.get("tag_offset_x_mm") or 0.0))
    except Exception:
        cfg["tag_offset_x_ft"] = float(cfg.get("tag_offset_x_ft", 0.0) or 0.0)
    try:
        cfg["tag_offset_y_ft"] = ft_from_mm(float(cfg.get("tag_offset_y_mm") or 0.0))
    except Exception:
        cfg["tag_offset_y_ft"] = float(cfg.get("tag_offset_y_ft", 0.0) or 0.0)
    return cfg


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
    system_metadata = get_mep_system_metadata(duct)
    row = {
        "duct_id": duct.Id.IntegerValue,
        "type_name": get_type_name(duct),
        "type_family_name": get_type_family_name(duct),
        "system_name": system_metadata.get("system_name", "-"),
        "system_type": system_metadata.get("system_type", "-"),
        "system_classification": system_metadata.get("system_classification", "-"),
        "mep_system_name": system_metadata.get("mep_system_name", "-"),
        "mep_system_type": system_metadata.get("mep_system_type", "-"),
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


def get_system_summary_key(row):
    return first_non_empty(
        [
            row.get("system_type"),
            row.get("system_name"),
            row.get("mep_system_name"),
            row.get("system_classification"),
            "-",
        ]
    )


def get_system_intent_overrides(cfg):
    overrides = cfg.get("system_intent_overrides", {})
    if isinstance(overrides, dict):
        return dict(overrides)
    return {}


def apply_system_intent_override(row, cfg):
    geometry_intent = row.get("intent") or INTENT_UNKNOWN_VERTICAL_DUCT
    system_key = get_system_summary_key(row)
    overrides = get_system_intent_overrides(cfg)
    override_intent = overrides.get(system_key, INTENT_GEOMETRY_FALLBACK)
    if override_intent not in ALLOWED_SYSTEM_INTENT_OVERRIDES:
        print(
            "CONFIG RESOLVE -> system intent override invalid for {}: {}".format(
                system_key, override_intent
            )
        )
        override_intent = INTENT_GEOMETRY_FALLBACK

    final_intent = geometry_intent
    if override_intent != INTENT_GEOMETRY_FALLBACK:
        final_intent = override_intent

    row["system_key"] = system_key
    row["geometry_intent"] = geometry_intent
    row["override_intent"] = override_intent
    row["final_intent"] = final_intent
    row["system_rule_matched"] = override_intent != INTENT_GEOMETRY_FALLBACK
    row["system_rule_changed_intent"] = final_intent != geometry_intent
    row["intent"] = final_intent
    if final_intent != geometry_intent:
        row["reason"] = row.get(
            "reason", ""
        ) + " System override changed intent from {} to {}.".format(
            geometry_intent, final_intent
        )
        print(
            "CONFIG RESOLVE -> system intent override {}: {} -> {}".format(
                system_key, geometry_intent, final_intent
            )
        )
    elif override_intent != INTENT_GEOMETRY_FALLBACK:
        row["reason"] = row.get(
            "reason", ""
        ) + " System rule confirmed intent from {}.".format(system_key)
        print(
            "CONFIG RESOLVE -> system intent override {} confirmed {}".format(
                system_key, final_intent
            )
        )
    return row


def build_system_summary(candidate_rows):
    grouped = {}
    for row in candidate_rows:
        key = row.get("system_key") or get_system_summary_key(row)
        if key not in grouped:
            grouped[key] = {
                "system_key": key,
                "count": 0,
                "candidate_ids": [],
                "geometry_intents": [],
                "override_intents": [],
                "final_intents": [],
                "suggested_tag_types": [],
            }
        item = grouped[key]
        item["count"] += 1
        item["candidate_ids"].append(row.get("duct_id"))
        geometry_intent = row.get("geometry_intent") or row.get("intent") or "-"
        if geometry_intent not in item["geometry_intents"]:
            item["geometry_intents"].append(geometry_intent)
        override_intent = row.get("override_intent") or INTENT_GEOMETRY_FALLBACK
        if override_intent not in item["override_intents"]:
            item["override_intents"].append(override_intent)
        final_intent = row.get("final_intent") or row.get("intent") or "-"
        if final_intent not in item["final_intents"]:
            item["final_intents"].append(final_intent)
        suggested = row.get("suggested_tag_type") or "-"
        if suggested not in item["suggested_tag_types"]:
            item["suggested_tag_types"].append(suggested)
    return [grouped[key] for key in sorted(grouped.keys())]


def loose_match_type_for_intent(intent, symbols):
    intent_keywords = {
        INTENT_GOING_UP: ["goingup", "up", "comingup", "riser", "vertical"],
        INTENT_GOING_DOWN: ["goingdown", "down", "comingdown", "riser", "vertical"],
        INTENT_COMING_UP: ["comingup", "up", "goingup", "riser", "vertical"],
        INTENT_COMING_DOWN: ["comingdown", "down", "goingdown", "riser", "vertical"],
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


def get_selected_family_symbols(cfg):
    selected_family = cfg.get("selected_duct_tag_family_name") or ""
    selected_norm = normalize_name(selected_family)
    all_symbols = collect_loaded_duct_tag_symbols()
    if not selected_norm:
        return []

    exact = [
        symbol
        for symbol in all_symbols
        if normalize_name(get_family_name_from_symbol(symbol)) == selected_norm
    ]
    if exact:
        return exact

    configured_symbol_id = str(cfg.get("selected_duct_tag_symbol_id") or "").strip()
    if configured_symbol_id:
        try:
            configured_symbol = doc.GetElement(ElementId(int(configured_symbol_id)))
            if is_duct_tag_symbol(configured_symbol):
                configured_family_norm = normalize_name(
                    get_family_name_from_symbol(configured_symbol)
                )
                by_symbol_family = [
                    symbol
                    for symbol in all_symbols
                    if normalize_name(get_family_name_from_symbol(symbol))
                    == configured_family_norm
                ]
                if by_symbol_family:
                    return by_symbol_family
        except Exception:
            pass

    # Tolerate a legacy/configured display label only when it identifies one family.
    grouped = group_symbols_by_family(all_symbols)
    possible_names = [
        family_name
        for family_name in grouped.keys()
        if selected_norm in normalize_name(family_name)
        or normalize_name(family_name) in selected_norm
    ]
    if len(possible_names) == 1:
        return grouped[possible_names[0]]
    return []


def resolve_symbol_for_final_intent(intent, symbols):
    preferred_type_names = {
        INTENT_CONTINUOUS_UP: ["continuousup"],
        INTENT_CONTINUOUS_DOWN: ["continuousdown"],
        INTENT_GOING_UP: ["goingup"],
        INTENT_GOING_DOWN: ["goingdown"],
        INTENT_COMING_UP: ["comingup"],
        INTENT_COMING_DOWN: ["comingdown"],
    }
    for preferred in preferred_type_names.get(intent, []):
        for symbol in symbols:
            if preferred in normalize_name(get_symbol_type_name(symbol)):
                return symbol
    matched_type_name = loose_match_type_for_intent(intent, symbols)
    if matched_type_name:
        matched_norm = normalize_name(matched_type_name)
        for symbol in symbols:
            if normalize_name(get_symbol_type_name(symbol)) == matched_norm:
                return symbol
    return None


def build_vertical_intent_symbol_map(symbols):
    intent_map = {}
    intents = [
        INTENT_CONTINUOUS_DOWN,
        INTENT_CONTINUOUS_UP,
        INTENT_GOING_UP,
        INTENT_GOING_DOWN,
        INTENT_COMING_UP,
        INTENT_COMING_DOWN,
    ]
    for intent in intents:
        symbol = resolve_symbol_for_final_intent(intent, symbols)
        if symbol is not None:
            intent_map[intent] = symbol
    return intent_map


def try_load_configured_duct_tag_family(cfg):
    path = str(cfg.get("last_loaded_rfa_path") or "").strip()
    if not path or not os.path.isfile(path):
        return False, "Configured family file path is unavailable."
    transaction = Transaction(
        doc, "Smart HVAC Vertical Duct Tags - Reload Configured Family"
    )
    try:
        transaction.Start()
        try:
            doc.LoadFamily(path, DuctTagFamilyLoadOptions())
        except Exception:
            doc.LoadFamily(path)
        transaction.Commit()
        print("FAMILY LOAD -> guarded configured family load: {}".format(path))
        return True, ""
    except Exception as ex:
        try:
            if transaction.HasStarted() and not transaction.HasEnded():
                transaction.RollBack()
        except Exception:
            pass
        return False, str(ex)


def resolve_selected_family_intent_symbols(cfg, allow_guarded_load):
    selected_family = cfg.get("selected_duct_tag_family_name") or ""
    symbols = get_selected_family_symbols(cfg)
    if not symbols and allow_guarded_load:
        loaded, reason = try_load_configured_duct_tag_family(cfg)
        print(
            "BATCH HVAC TAG RESOLVE -> guarded family load success={} reason={}".format(
                loaded, reason or "-"
            )
        )
        symbols = get_selected_family_symbols(cfg)

    available_type_names = sorted([get_symbol_type_name(symbol) for symbol in symbols])
    intent_map = build_vertical_intent_symbol_map(symbols)
    print(
        "BATCH HVAC TAG RESOLVE -> selected family: {}".format(selected_family or "-")
    )
    print(
        "BATCH HVAC TAG RESOLVE -> available type names: {}".format(
            available_type_names
        )
    )
    print(
        "BATCH HVAC TAG RESOLVE -> intent map: {}".format(
            dict(
                [
                    (intent, get_symbol_type_name(symbol))
                    for intent, symbol in intent_map.items()
                ]
            )
        )
    )
    for intent in [
        INTENT_CONTINUOUS_DOWN,
        INTENT_CONTINUOUS_UP,
        INTENT_GOING_UP,
        INTENT_GOING_DOWN,
        INTENT_COMING_UP,
        INTENT_COMING_DOWN,
    ]:
        symbol = intent_map.get(intent)
        if symbol is not None:
            print(
                "BATCH HVAC TAG RESOLVE -> {} resolved to {}".format(
                    intent, get_symbol_type_name(symbol)
                )
            )
        else:
            print(
                "BATCH HVAC TAG RESOLVE -> failed intent {}; available types: {}".format(
                    intent, available_type_names
                )
            )

    error = ""
    if not selected_family:
        error = "No selected duct tag family is configured."
    elif not symbols:
        error = "Selected duct tag family is not loaded."
    return symbols, intent_map, error


def get_existing_tagged_element_ids_in_view(view):
    tagged_ids = set()
    try:
        tags = (
            FilteredElementCollector(doc, view.Id)
            .OfClass(IndependentTag)
            .WhereElementIsNotElementType()
        )
        for tag in tags:
            try:
                ids = tag.GetTaggedLocalElementIds()
                if ids:
                    for elem_id in ids:
                        tagged_ids.add(elem_id.IntegerValue)
                    continue
            except Exception:
                pass
            try:
                elem_id = tag.TaggedLocalElementId
                if elem_id is not None and elem_id.IntegerValue > 0:
                    tagged_ids.add(elem_id.IntegerValue)
            except Exception:
                pass
    except Exception as ex:
        print("PLACE HVAC VERTICAL DUCT TAGS -> duplicate scan failed: {}".format(ex))
    return tagged_ids


def get_vertical_duct_tag_point(row, cfg, view=None):
    start_z = row.get("start_z")
    end_z = row.get("end_z")
    p0, p1, status = get_duct_line_endpoints(
        doc.GetElement(ElementId(int(row["duct_id"])))
    )
    if p0 is None or p1 is None:
        return None
    x = (p0.X + p1.X) / 2.0 + get_tag_offset_x_ft(cfg)
    y = (p0.Y + p1.Y) / 2.0 + get_tag_offset_y_ft(cfg)
    z = start_z if start_z is not None else p0.Z
    try:
        target_view = view if view is not None else doc.ActiveView
        active_level = get_active_level(target_view)
        if active_level is not None:
            z = active_level.Elevation
    except Exception:
        if end_z is not None and start_z is not None:
            z = (start_z + end_z) / 2.0
    return XYZ(x, y, z)


def fmt_xyz_mm(point):
    if point is None:
        return "-"
    return "X {}, Y {}, Z {}".format(fmt_mm(point.X), fmt_mm(point.Y), fmt_mm(point.Z))


def activate_symbols_in_transaction(symbols):
    activated = False
    for symbol in symbols:
        if symbol is None:
            continue
        try:
            if not symbol.IsActive:
                symbol.Activate()
                activated = True
        except Exception as ex:
            print(
                "PLACE HVAC VERTICAL DUCT TAGS -> symbol activation failed {}: {}".format(
                    get_symbol_type_name(symbol), ex
                )
            )
            raise
    if activated:
        try:
            doc.Regenerate()
        except Exception:
            pass


def create_duct_tag(view, duct, symbol, tag_point, add_leader):
    reference = Reference(duct)
    tag = IndependentTag.Create(
        doc,
        view.Id,
        reference,
        bool(add_leader),
        TagMode.TM_ADDBY_CATEGORY,
        TagOrientation.Horizontal,
        tag_point,
    )
    if symbol is not None:
        try:
            if tag.GetTypeId().IntegerValue != symbol.Id.IntegerValue:
                tag.ChangeTypeId(symbol.Id)
        except Exception:
            try:
                tag.ChangeTypeId(symbol.Id)
            except Exception as ex:
                raise Exception("Tag created but type change failed: {}".format(ex))
    return tag


def prepare_selected_vertical_duct_tag_candidates():
    cfg = get_config()
    view = doc.ActiveView
    active_level = get_active_level(view)
    if active_level is None:
        return (
            [],
            [],
            "HVAC vertical duct tagging works only in floor plan views with a valid level.",
        )
    levels = collect_project_levels()
    band_bottom, band_top = get_active_level_band(active_level, levels)
    selected_ids = list(uidoc.Selection.GetElementIds())
    print(
        "PLACE HVAC VERTICAL DUCT TAGS -> selected ids: {}".format(
            [eid.IntegerValue for eid in selected_ids]
        )
    )

    if not bool(cfg.get("selected_family_api_compatible", False)) or not bool(
        cfg.get("selected_family_vertical_intent_match", False)
    ):
        return (
            [],
            [],
            (
                "No selected vertical-intent-compatible duct tag family is configured. "
                "Choose loaded vertical duct tag family first."
            ),
        )

    family_symbols = get_selected_family_symbols(cfg)
    if not family_symbols:
        return [], [], "Selected duct tag family was not found in the current document."
    intent_symbol_map = build_vertical_intent_symbol_map(family_symbols)

    already_tagged = get_existing_tagged_element_ids_in_view(view)
    candidates = []
    skipped = []
    for elem_id in selected_ids:
        elem = doc.GetElement(elem_id)
        if not is_duct_curve(elem):
            skipped.append(
                {
                    "duct_id": elem_id.IntegerValue,
                    "reason": "SKIPPED_INVALID_NOT_DUCT_CURVE",
                }
            )
            continue
        row = classify_vertical_duct(elem, active_level, band_bottom, band_top, cfg)
        if row["status"] != STATUS_VERTICAL_CANDIDATE:
            skipped.append({"duct_id": elem_id.IntegerValue, "reason": row["status"]})
            continue
        row = apply_system_intent_override(row, cfg)
        symbol = intent_symbol_map.get(row.get("final_intent"))
        if symbol is None:
            skipped.append(
                {
                    "duct_id": elem_id.IntegerValue,
                    "reason": "SKIPPED_NO_MATCHING_TAG_TYPE_FOR_{}".format(
                        row.get("final_intent") or "-"
                    ),
                }
            )
            continue
        if elem_id.IntegerValue in already_tagged:
            skipped.append(
                {
                    "duct_id": elem_id.IntegerValue,
                    "reason": "SKIPPED_ALREADY_TAGGED",
                }
            )
            continue
        tag_point = get_vertical_duct_tag_point(row, cfg)
        if tag_point is None:
            skipped.append(
                {
                    "duct_id": elem_id.IntegerValue,
                    "reason": "SKIPPED_TAG_POINT_UNRESOLVED",
                }
            )
            continue
        row["tag_symbol"] = symbol
        row["tag_type"] = get_symbol_type_name(symbol)
        row["tag_point"] = tag_point
        candidates.append(row)
    return candidates, skipped, ""


def confirm_vertical_duct_tag_placement(candidates):
    cfg = get_config()
    output = script.get_output() if script is not None else None
    output_emit(output, "# Smart HVAC Vertical Duct Tags - Placement Preview")
    output_emit(output, "")
    output_emit(output, "## Placement Settings")
    output_emit(
        output, "- Tag offset X mm: `{}`".format(cfg.get("tag_offset_x_mm", 0.0))
    )
    output_emit(
        output, "- Tag offset Y mm: `{}`".format(cfg.get("tag_offset_y_mm", 0.0))
    )
    output_emit(
        output, "- Add leader: `{}`".format(fmt_bool(cfg.get("tag_add_leader", False)))
    )
    output_emit(output, "")
    headers = [
        "Duct ElementId",
        "System Type",
        "System key",
        "Geometry intent",
        "Override intent",
        "Final intent",
        "Tag type to place",
    ]
    output_emit(output, "|" + "|".join(headers) + "|")
    output_emit(output, "|" + "|".join(["---"] * len(headers)) + "|")
    for row in candidates:
        values = [
            linkify(output, row["duct_id"]),
            row.get("system_type") or "-",
            row.get("system_key") or "-",
            row.get("geometry_intent") or "-",
            row.get("override_intent") or "-",
            row.get("final_intent") or "-",
            row.get("tag_type") or "-",
        ]
        output_emit(
            output, "|" + "|".join([str(v).replace("\n", " ") for v in values]) + "|"
        )
    return ask_yes_no(
        "Place vertical duct tags",
        "Place {} vertical duct tag(s) in the active view?".format(len(candidates)),
    )


def is_valid_but_not_taggable_skip(reason):
    text = str(reason or "")
    return (
        text == "SKIPPED_ALREADY_TAGGED"
        or text.startswith("SKIPPED_NO_MATCHING_TAG_TYPE_FOR_")
        or text == "SKIPPED_TAG_POINT_UNRESOLVED"
    )


def get_placement_summary_counts(candidates, skipped, failed):
    skipped_already = [
        r for r in skipped if r.get("reason") == "SKIPPED_ALREADY_TAGGED"
    ]
    valid_skipped = [
        r for r in skipped if is_valid_but_not_taggable_skip(r.get("reason"))
    ]
    invalid_skipped = [
        r for r in skipped if not is_valid_but_not_taggable_skip(r.get("reason"))
    ]
    return {
        "valid_vertical_candidates": len(candidates) + len(valid_skipped),
        "taggable_candidates": len(candidates),
        "skipped_already_tagged": len(skipped_already),
        "skipped_invalid": len(invalid_skipped),
        "failed": len(failed),
        "skipped_already_rows": skipped_already,
    }


def place_vertical_duct_tags_for_selected_candidates():
    cfg = get_config()
    cfg["last_used_action"] = "Place vertical duct tags for selected candidates"
    write_config(cfg)
    candidates, skipped, error = prepare_selected_vertical_duct_tag_candidates()
    if error:
        TaskDialog.Show("Place vertical duct tags for selected candidates", error)
        return
    if not candidates:
        counts = get_placement_summary_counts(candidates, skipped, [])
        output = script.get_output() if script is not None else None
        output_emit(output, "# Smart HVAC Vertical Duct Tags - Placement Report")
        output_emit(output, "")
        output_emit(output, "## Placement Summary")
        output_emit(
            output,
            "- Selected elements checked: `{}`".format(
                len(list(uidoc.Selection.GetElementIds()))
            ),
        )
        output_emit(
            output,
            "- Valid vertical candidates: `{}`".format(
                counts["valid_vertical_candidates"]
            ),
        )
        output_emit(
            output,
            "- Taggable candidates: `{}`".format(counts["taggable_candidates"]),
        )
        output_emit(output, "- Tags created: `0`")
        output_emit(
            output,
            "- Skipped already tagged: `{}`".format(counts["skipped_already_tagged"]),
        )
        output_emit(output, "- Skipped invalid: `{}`".format(counts["skipped_invalid"]))
        output_emit(output, "- Failed: `0`")
        output_emit(output, "")
        output_emit(output, "## Skipped / Failed")
        output_emit(output, "|Duct id|Reason|")
        output_emit(output, "|---|---|")
        for row in skipped:
            output_emit(
                output,
                "|{}|{}|".format(
                    linkify(output, row.get("duct_id")), str(row.get("reason", "-"))
                ),
            )
        if not skipped:
            output_emit(output, "|-|-|")
        TaskDialog.Show(
            "Place vertical duct tags for selected candidates",
            "No selected vertical duct candidates can be tagged. See output report for skipped reasons.",
        )
        print(
            "PLACE HVAC VERTICAL DUCT TAGS -> valid candidates: {}".format(
                counts["valid_vertical_candidates"]
            )
        )
        return
    print(
        "PLACE HVAC VERTICAL DUCT TAGS -> valid candidates: {}".format(
            [r["duct_id"] for r in candidates]
        )
    )
    if not confirm_vertical_duct_tag_placement(candidates):
        print("PLACE HVAC VERTICAL DUCT TAGS -> user cancelled before transaction")
        return

    view = doc.ActiveView
    add_leader = bool(cfg.get("tag_add_leader", False))
    created = []
    failed = []
    symbols_to_activate = []
    for row in candidates:
        symbol = row.get("tag_symbol")
        if symbol is not None and symbol not in symbols_to_activate:
            symbols_to_activate.append(symbol)

    t = Transaction(doc, "Place Smart HVAC Vertical Duct Tags")
    try:
        t.Start()
        activate_symbols_in_transaction(symbols_to_activate)
        for row in candidates:
            try:
                duct = doc.GetElement(ElementId(int(row["duct_id"])))
                tag = create_duct_tag(
                    view,
                    duct,
                    row.get("tag_symbol"),
                    row.get("tag_point"),
                    add_leader,
                )
                created.append(
                    {
                        "duct_id": row["duct_id"],
                        "tag_id": tag.Id.IntegerValue,
                        "system_type": row.get("system_type") or "-",
                        "final_intent": row.get("final_intent") or "-",
                        "tag_type": row.get("tag_type") or "-",
                        "tag_point": row.get("tag_point"),
                    }
                )
            except Exception as ex:
                failed.append({"duct_id": row["duct_id"], "reason": str(ex)})
        t.Commit()
    except Exception as ex:
        try:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
        except Exception:
            pass
        TaskDialog.Show(
            "Place vertical duct tags for selected candidates",
            "Placement transaction failed: {}".format(ex),
        )
        return

    counts = get_placement_summary_counts(candidates, skipped, failed)
    skipped_already = counts["skipped_already_rows"]
    print(
        "PLACE HVAC VERTICAL DUCT TAGS -> created tags: {}".format(
            [r["tag_id"] for r in created]
        )
    )
    print(
        "PLACE HVAC VERTICAL DUCT TAGS -> skipped already tagged: {}".format(
            [r["duct_id"] for r in skipped_already]
        )
    )
    print("PLACE HVAC VERTICAL DUCT TAGS -> failed: {}".format(failed))

    output = script.get_output() if script is not None else None
    output_emit(output, "# Smart HVAC Vertical Duct Tags - Placement Report")
    output_emit(output, "")
    output_emit(output, "## Placement Summary")
    output_emit(
        output, "- Tag offset X mm: `{}`".format(cfg.get("tag_offset_x_mm", 0.0))
    )
    output_emit(
        output, "- Tag offset Y mm: `{}`".format(cfg.get("tag_offset_y_mm", 0.0))
    )
    output_emit(output, "- Add leader: `{}`".format(fmt_bool(add_leader)))
    output_emit(
        output,
        "- Selected elements checked: `{}`".format(
            len(list(uidoc.Selection.GetElementIds()))
        ),
    )
    output_emit(
        output,
        "- Valid vertical candidates: `{}`".format(counts["valid_vertical_candidates"]),
    )
    output_emit(
        output,
        "- Taggable candidates: `{}`".format(counts["taggable_candidates"]),
    )
    output_emit(output, "- Tags created: `{}`".format(len(created)))
    output_emit(
        output,
        "- Skipped already tagged: `{}`".format(counts["skipped_already_tagged"]),
    )
    output_emit(output, "- Skipped invalid: `{}`".format(counts["skipped_invalid"]))
    output_emit(output, "- Failed: `{}`".format(counts["failed"]))

    output_emit(output, "")
    output_emit(output, "## Created Tags")
    created_headers = [
        "Duct id",
        "Tag id",
        "System type",
        "Final intent",
        "Tag type",
        "Placement point",
    ]
    output_emit(output, "|" + "|".join(created_headers) + "|")
    output_emit(output, "|" + "|".join(["---"] * len(created_headers)) + "|")
    for row in created:
        values = [
            linkify(output, row["duct_id"]),
            linkify(output, row["tag_id"]),
            row["system_type"],
            row["final_intent"],
            row["tag_type"],
            fmt_xyz_mm(row["tag_point"]),
        ]
        output_emit(
            output, "|" + "|".join([str(v).replace("\n", " ") for v in values]) + "|"
        )
    if not created:
        output_emit(output, "|" + "|".join(["-"] * len(created_headers)) + "|")

    output_emit(output, "")
    output_emit(output, "## Skipped / Failed")
    skipped_headers = ["Duct id", "Reason"]
    output_emit(output, "|" + "|".join(skipped_headers) + "|")
    output_emit(output, "|" + "|".join(["---"] * len(skipped_headers)) + "|")
    for row in skipped + failed:
        output_emit(
            output,
            "|{}|{}|".format(
                linkify(output, row.get("duct_id")), str(row.get("reason", "-"))
            ),
        )
    if not skipped and not failed:
        output_emit(output, "|-|-|")

    if created:
        try:
            id_list = List[ElementId]()
            for row in created:
                id_list.Add(ElementId(int(row["tag_id"])))
            uidoc.Selection.SetElementIds(id_list)
        except Exception as ex:
            print(
                "PLACE HVAC VERTICAL DUCT TAGS -> tag selection failed: {}".format(ex)
            )
    else:
        try:
            id_list = List[ElementId]()
            for row in candidates:
                id_list.Add(ElementId(int(row["duct_id"])))
            uidoc.Selection.SetElementIds(id_list)
        except Exception:
            pass


# =========================
# All floor plan batch workflow
# =========================
def is_dependent_plan_view(view):
    try:
        primary_id = view.GetPrimaryViewId()
        return primary_id is not None and primary_id.IntegerValue > 0
    except Exception:
        return False


def split_filter_terms(value):
    text = str(value or "").strip()
    if not text:
        return []
    text = text.replace(";", ",")
    return [item.strip().lower() for item in text.split(",") if item.strip()]


def view_name_matches_batch_filters(view, cfg):
    name = safe_name(view).lower()
    if "template" in name:
        return False
    include_terms = split_filter_terms(cfg.get("batch_view_name_include_filter", ""))
    exclude_terms = split_filter_terms(cfg.get("batch_view_name_exclude_filter", ""))
    if include_terms and not any([term in name for term in include_terms]):
        return False
    if exclude_terms and any([term in name for term in exclude_terms]):
        return False
    return True


def collect_batch_floor_plan_views(cfg):
    views = []
    include_dependents = bool(cfg.get("batch_include_dependent_views", False))
    try:
        collector = FilteredElementCollector(doc).OfClass(ViewPlan)
        for view in collector:
            try:
                if view.IsTemplate or view.GenLevel is None:
                    continue
                view_type_name = str(view.ViewType)
                if view_type_name not in ("FloorPlan", "EngineeringPlan"):
                    continue
                if not include_dependents and is_dependent_plan_view(view):
                    continue
                if not view_name_matches_batch_filters(view, cfg):
                    continue
                views.append(view)
            except Exception:
                continue
    except Exception as ex:
        print("BATCH HVAC VERTICAL DUCTS -> view discovery failed: {}".format(ex))
    views.sort(
        key=lambda view: (
            view.GenLevel.Elevation if view.GenLevel is not None else 0.0,
            safe_name(view).lower(),
            view.Id.IntegerValue,
        )
    )
    print(
        "BATCH HVAC VERTICAL DUCTS -> eligible views: {}".format(
            [
                {
                    "view_id": view.Id.IntegerValue,
                    "view_name": safe_name(view),
                    "level": safe_name(view.GenLevel),
                }
                for view in views
            ]
        )
    )
    return views


def get_explicit_batch_system_override(row, cfg):
    system_key = get_system_summary_key(row)
    overrides = get_system_intent_overrides(cfg)
    configured = system_key in overrides
    override_intent = overrides.get(system_key)
    if override_intent not in ALLOWED_SYSTEM_INTENT_OVERRIDES:
        configured = False
        override_intent = None
    return system_key, override_intent, configured


def scan_batch_view(view, cfg, levels, intent_symbol_map):
    active_level = get_active_level(view)
    result = {
        "view": view,
        "view_id": view.Id.IntegerValue,
        "view_name": safe_name(view),
        "level_name": safe_name(active_level),
        "ducts_checked": 0,
        "vertical_candidates": [],
        "taggable_candidates": [],
        "unconfigured_candidates": [],
        "already_tagged_candidates": [],
        "skipped_rows": [],
        "failed_rows": [],
        "created_rows": [],
    }
    if active_level is None:
        result["failed_rows"].append(
            {"duct_id": "-", "reason": "View has no valid GenLevel."}
        )
        return result

    band_bottom, band_top = get_active_level_band(active_level, levels)
    result["band_bottom"] = band_bottom
    result["band_top"] = band_top
    already_tagged = get_existing_tagged_element_ids_in_view(view)
    try:
        collector = (
            FilteredElementCollector(doc, view.Id)
            .OfCategory(BuiltInCategory.OST_DuctCurves)
            .WhereElementIsNotElementType()
        )
        for duct in collector:
            if not is_duct_curve(duct):
                continue
            result["ducts_checked"] += 1
            row = classify_vertical_duct(duct, active_level, band_bottom, band_top, cfg)
            row["view"] = view
            row["view_id"] = view.Id.IntegerValue
            row["view_name"] = safe_name(view)
            row["level_name"] = safe_name(active_level)
            if row["status"] != STATUS_VERTICAL_CANDIDATE:
                result["skipped_rows"].append(row)
                continue

            system_key, override_intent, configured = (
                get_explicit_batch_system_override(row, cfg)
            )
            row["system_key"] = system_key
            row["geometry_intent"] = row.get("intent") or INTENT_UNKNOWN_VERTICAL_DUCT
            row["batch_system_configured"] = configured
            result["vertical_candidates"].append(row)

            if not configured:
                row["override_intent"] = "-"
                row["final_intent"] = "-"
                row["batch_skip_reason"] = STATUS_SKIPPED_UNCONFIGURED_SYSTEM_INTENT
                result["unconfigured_candidates"].append(row)
                continue

            row = apply_system_intent_override(row, cfg)
            symbol = intent_symbol_map.get(row.get("final_intent"))
            row["tag_symbol"] = symbol
            row["tag_type"] = (
                get_symbol_type_name(symbol) if symbol is not None else "-"
            )
            row["suggested_tag_type"] = row["tag_type"]
            row["already_tagged"] = row["duct_id"] in already_tagged
            if row["already_tagged"]:
                row["batch_skip_reason"] = STATUS_SKIPPED_ALREADY_TAGGED_IN_VIEW
                result["already_tagged_candidates"].append(row)
                continue
            if symbol is None:
                row["batch_skip_reason"] = "SKIPPED_NO_MATCHING_TAG_TYPE_FOR_{}".format(
                    row.get("final_intent") or "-"
                )
                result["skipped_rows"].append(row)
                continue
            tag_point = get_vertical_duct_tag_point(row, cfg, view)
            if tag_point is None:
                row["batch_skip_reason"] = "SKIPPED_TAG_POINT_UNRESOLVED"
                result["skipped_rows"].append(row)
                continue
            row["tag_point"] = tag_point
            result["taggable_candidates"].append(row)
    except Exception as ex:
        result["failed_rows"].append({"duct_id": "-", "reason": str(ex)})
    print(
        "BATCH HVAC VERTICAL DUCTS -> checked view: {} | level={} | ducts={} | "
        "candidates={} | taggable={} | unconfigured={} | already_tagged={}".format(
            result["view_name"],
            result["level_name"],
            result["ducts_checked"],
            len(result["vertical_candidates"]),
            len(result["taggable_candidates"]),
            len(result["unconfigured_candidates"]),
            len(result["already_tagged_candidates"]),
        )
    )
    return result


def run_all_floor_plan_scan(cfg):
    views = collect_batch_floor_plan_views(cfg)
    levels = collect_project_levels()
    family_symbols, intent_symbol_map, family_error = (
        resolve_selected_family_intent_symbols(cfg, True)
    )
    if family_error:
        print("BATCH HVAC TAG RESOLVE -> error: {}".format(family_error))
        return views, [], family_error
    results = []
    for view in views:
        results.append(scan_batch_view(view, cfg, levels, intent_symbol_map))
    total_candidates = sum([len(result["vertical_candidates"]) for result in results])
    unconfigured_keys = sorted(
        set(
            [
                row.get("system_key") or "-"
                for result in results
                for row in result["unconfigured_candidates"]
            ]
        )
    )
    print("BATCH HVAC VERTICAL DUCTS -> total candidates: {}".format(total_candidates))
    print(
        "BATCH HVAC VERTICAL DUCTS -> unconfigured system keys: {}".format(
            unconfigured_keys
        )
    )
    return views, results, ""


def batch_scan_totals(views, results):
    return {
        "eligible_views": len(views),
        "views_checked": len(results),
        "ducts_checked": sum([r["ducts_checked"] for r in results]),
        "vertical_candidates": sum([len(r["vertical_candidates"]) for r in results]),
        "configured_candidates": sum(
            [
                len(
                    [
                        row
                        for row in r["vertical_candidates"]
                        if row.get("batch_system_configured")
                    ]
                )
                for r in results
            ]
        ),
        "taggable_candidates": sum([len(r["taggable_candidates"]) for r in results]),
        "unconfigured": sum([len(r["unconfigured_candidates"]) for r in results]),
        "already_tagged": sum([len(r["already_tagged_candidates"]) for r in results]),
        "failed": sum([len(r["failed_rows"]) for r in results]),
    }


def emit_batch_scope(output, cfg, totals):
    output_emit(output, "## Batch View Scope")
    output_emit(
        output,
        "- Eligible floor plan views: `{}`".format(totals["eligible_views"]),
    )
    output_emit(
        output,
        "- Include dependent views: `{}`".format(
            fmt_bool(cfg.get("batch_include_dependent_views", False))
        ),
    )
    output_emit(
        output,
        "- View name include filter: `{}`".format(
            cfg.get("batch_view_name_include_filter", "") or "-"
        ),
    )
    output_emit(
        output,
        "- View name exclude filter: `{}`".format(
            cfg.get("batch_view_name_exclude_filter", "") or "-"
        ),
    )


def emit_batch_per_view_summary(output, results, include_created):
    headers = [
        "View name",
        "Level",
        "Ducts checked",
        "Vertical candidates",
        "Taggable candidates",
        "Tags created",
        "Skipped already tagged",
        "Skipped unconfigured system",
        "Failed",
    ]
    rows = []
    for result in results:
        rows.append(
            [
                result["view_name"],
                result["level_name"],
                result["ducts_checked"],
                len(result["vertical_candidates"]),
                len(result["taggable_candidates"]),
                len(result["created_rows"]) if include_created else 0,
                len(result["already_tagged_candidates"]),
                len(result["unconfigured_candidates"]),
                len(result["failed_rows"]),
            ]
        )
    output_table(output, headers, rows)


def scan_all_floor_plan_views_for_vertical_ducts():
    cfg = get_config()
    cfg["last_used_action"] = "Scan all floor plan views for vertical ducts"
    write_config(cfg)
    views, results, error = run_all_floor_plan_scan(cfg)
    if error:
        TaskDialog.Show("Scan all floor plan views for vertical ducts", error)
        return
    totals = batch_scan_totals(views, results)
    output = script.get_output() if script is not None else None

    output_emit(output, "# Smart HVAC Vertical Duct Tags - All Floor Plan Scan")
    output_emit(output, "")
    emit_batch_scope(output, cfg, totals)
    output_emit(output, "")
    output_emit(output, "## Batch Summary")
    output_emit(output, "- Views checked: `{}`".format(totals["views_checked"]))
    output_emit(output, "- Total ducts checked: `{}`".format(totals["ducts_checked"]))
    output_emit(
        output,
        "- Total vertical candidates: `{}`".format(totals["vertical_candidates"]),
    )
    output_emit(
        output,
        "- Candidates with configured system rule: `{}`".format(
            totals["configured_candidates"]
        ),
    )
    output_emit(
        output,
        "- Candidates skipped unconfigured system: `{}`".format(totals["unconfigured"]),
    )
    output_emit(
        output,
        "- Candidates already tagged in view: `{}`".format(totals["already_tagged"]),
    )
    output_emit(
        output,
        "- Taggable candidates: `{}`".format(totals["taggable_candidates"]),
    )

    output_emit(output, "")
    output_emit(output, "## Per View Summary")
    emit_batch_per_view_summary(output, results, False)

    output_emit(output, "")
    output_emit(output, "## Valid Candidates")
    candidate_rows = []
    for result in results:
        for row in result["vertical_candidates"]:
            candidate_rows.append(
                [
                    result["view_name"],
                    result["level_name"],
                    linkify(output, row["duct_id"]),
                    row.get("system_type") or "-",
                    row.get("system_key") or "-",
                    row.get("geometry_intent") or row.get("intent") or "-",
                    row.get("override_intent") or "-",
                    row.get("final_intent") or "-",
                    row.get("tag_type") or "-",
                    row.get("batch_skip_reason") or "TAGGABLE",
                ]
            )
    output_table(
        output,
        [
            "View name",
            "Level",
            "Duct id",
            "System Type",
            "System key",
            "Geometry intent",
            "Override intent",
            "Final intent",
            "Tag type",
            "Status",
        ],
        candidate_rows,
    )

    output_emit(output, "")
    output_emit(output, "## Unconfigured System Keys")
    grouped = {}
    for result in results:
        for row in result["unconfigured_candidates"]:
            key = row.get("system_key") or "-"
            grouped.setdefault(key, []).append(
                "{}:{}".format(result["view_name"], row["duct_id"])
            )
    output_table(
        output,
        ["System key", "Candidate count", "View / duct ids"],
        [
            [key, len(grouped[key]), ", ".join(grouped[key])]
            for key in sorted(grouped.keys())
        ],
    )

    output_emit(output, "")
    output_emit(output, "## Skipped / Already Tagged Summary")
    output_emit(
        output,
        "- Already tagged in same view: `{}`".format(totals["already_tagged"]),
    )
    output_emit(
        output,
        "- Unconfigured system intent: `{}`".format(totals["unconfigured"]),
    )
    output_emit(output, "- Scan failures: `{}`".format(totals["failed"]))
    TaskDialog.Show(
        "Scan all floor plan views for vertical ducts",
        (
            "Batch scan completed.\n\n"
            "Views checked: {}\n"
            "Vertical candidates: {}\n"
            "Taggable candidates: {}\n"
            "Unconfigured system candidates: {}\n"
            "Already tagged in view: {}\n\n"
            "See the pyRevit output report for details."
        ).format(
            totals["views_checked"],
            totals["vertical_candidates"],
            totals["taggable_candidates"],
            totals["unconfigured"],
            totals["already_tagged"],
        ),
    )


def confirm_all_floor_plan_tag_placement(results):
    output = script.get_output() if script is not None else None
    output_emit(output, "# Smart HVAC Vertical Duct Tags - Batch Placement Preview")
    rows = []
    for result in results:
        for row in result["taggable_candidates"]:
            rows.append(
                [
                    result["view_name"],
                    result["level_name"],
                    linkify(output, row["duct_id"]),
                    row.get("system_type") or "-",
                    row.get("system_key") or "-",
                    row.get("geometry_intent") or "-",
                    row.get("override_intent") or "-",
                    row.get("final_intent") or "-",
                    row.get("tag_type") or "-",
                    fmt_bool(row.get("already_tagged", False)),
                ]
            )
    output_table(
        output,
        [
            "View name",
            "Level",
            "Duct id",
            "System Type",
            "System key",
            "Geometry intent",
            "Override intent",
            "Final intent",
            "Tag type",
            "Already tagged",
        ],
        rows,
    )
    return ask_yes_no(
        "Place vertical duct tags in all floor plan views",
        "Place {} vertical duct tag(s) across {} eligible floor plan view(s)?".format(
            len(rows), len(results)
        ),
    )


def place_vertical_duct_tags_in_all_floor_plan_views():
    cfg = get_config()
    cfg["last_used_action"] = "Place vertical duct tags in all floor plan views"
    write_config(cfg)
    if not bool(cfg.get("selected_family_api_compatible", False)) or not bool(
        cfg.get("selected_family_vertical_intent_match", False)
    ):
        TaskDialog.Show(
            "Place vertical duct tags in all floor plan views",
            (
                "No selected vertical-intent-compatible duct tag family is configured. "
                "Choose loaded vertical duct tag family first."
            ),
        )
        return

    views, results, error = run_all_floor_plan_scan(cfg)
    if error:
        TaskDialog.Show("Place vertical duct tags in all floor plan views", error)
        return
    totals = batch_scan_totals(views, results)
    if totals["taggable_candidates"] == 0:
        output = script.get_output() if script is not None else None
        output_emit(
            output,
            "# Smart HVAC Vertical Duct Tags - All Floor Plan Placement Report",
        )
        output_emit(output, "")
        output_emit(output, "## Batch Summary")
        output_emit(output, "- Views checked: `{}`".format(totals["views_checked"]))
        output_emit(
            output,
            "- Valid vertical candidates: `{}`".format(totals["vertical_candidates"]),
        )
        output_emit(output, "- Taggable candidates: `0`")
        output_emit(output, "- Tags created: `0`")
        output_emit(
            output,
            "- Skipped already tagged in view: `{}`".format(totals["already_tagged"]),
        )
        output_emit(
            output,
            "- Skipped unconfigured system: `{}`".format(totals["unconfigured"]),
        )
        output_emit(output, "")
        output_emit(output, "## Per View Placement Summary")
        emit_batch_per_view_summary(output, results, True)
        TaskDialog.Show(
            "Place vertical duct tags in all floor plan views",
            (
                "No batch candidates can be tagged.\n\n"
                "Check unconfigured system keys, duplicate tags, and selected tag types "
                "in the batch scan report."
            ),
        )
        return
    if not confirm_all_floor_plan_tag_placement(results):
        print("BATCH HVAC VERTICAL DUCT TAGS -> user cancelled before transaction")
        return

    add_leader = bool(cfg.get("tag_add_leader", False))
    created = []
    failed = []
    group = TransactionGroup(doc, "Smart HVAC Vertical Duct Tags - Batch Placement")
    group_started = False
    try:
        group.Start()
        group_started = True
        for result in results:
            candidates = result["taggable_candidates"]
            if not candidates:
                continue
            transaction = Transaction(
                doc, "Place tags - {}".format(result["view_name"][:80])
            )
            view_created = []
            try:
                transaction.Start()
                symbols = []
                for row in candidates:
                    symbol = row.get("tag_symbol")
                    if symbol is not None and symbol not in symbols:
                        symbols.append(symbol)
                activate_symbols_in_transaction(symbols)
                for row in candidates:
                    try:
                        duct = doc.GetElement(ElementId(int(row["duct_id"])))
                        tag = create_duct_tag(
                            result["view"],
                            duct,
                            row.get("tag_symbol"),
                            row.get("tag_point"),
                            add_leader,
                        )
                        view_created.append(
                            {
                                "view_name": result["view_name"],
                                "duct_id": row["duct_id"],
                                "tag_id": tag.Id.IntegerValue,
                                "system_key": row.get("system_key") or "-",
                                "final_intent": row.get("final_intent") or "-",
                                "tag_type": row.get("tag_type") or "-",
                                "tag_point": row.get("tag_point"),
                            }
                        )
                    except Exception as ex:
                        failure = {
                            "view_name": result["view_name"],
                            "duct_id": row["duct_id"],
                            "reason": str(ex),
                        }
                        failed.append(failure)
                        result["failed_rows"].append(failure)
                transaction.Commit()
                result["created_rows"].extend(view_created)
                created.extend(view_created)
            except Exception as ex:
                try:
                    if transaction.HasStarted() and not transaction.HasEnded():
                        transaction.RollBack()
                except Exception:
                    pass
                for row in candidates:
                    failure = {
                        "view_name": result["view_name"],
                        "duct_id": row["duct_id"],
                        "reason": "View transaction rolled back: {}".format(ex),
                    }
                    failed.append(failure)
                    result["failed_rows"].append(failure)
        group.Assimilate()
        group_started = False
    except Exception as ex:
        if group_started:
            try:
                group.RollBack()
            except Exception:
                pass
        TaskDialog.Show(
            "Place vertical duct tags in all floor plan views",
            "Batch transaction group failed and was rolled back: {}".format(ex),
        )
        return

    print(
        "BATCH HVAC VERTICAL DUCT TAGS -> created tags: {}".format(
            [row["tag_id"] for row in created]
        )
    )
    print(
        "BATCH HVAC VERTICAL DUCT TAGS -> skipped already tagged: {}".format(
            [
                {
                    "view": result["view_name"],
                    "duct_id": row["duct_id"],
                }
                for result in results
                for row in result["already_tagged_candidates"]
            ]
        )
    )
    print("BATCH HVAC VERTICAL DUCT TAGS -> failed: {}".format(failed))

    totals = batch_scan_totals(views, results)
    output = script.get_output() if script is not None else None
    output_emit(
        output,
        "# Smart HVAC Vertical Duct Tags - All Floor Plan Placement Report",
    )
    output_emit(output, "")
    output_emit(output, "## Placement Settings")
    output_emit(
        output, "- Tag offset X mm: `{}`".format(cfg.get("tag_offset_x_mm", 0.0))
    )
    output_emit(
        output, "- Tag offset Y mm: `{}`".format(cfg.get("tag_offset_y_mm", 0.0))
    )
    output_emit(output, "- Add leader: `{}`".format(fmt_bool(add_leader)))
    output_emit(output, "")
    output_emit(output, "## Batch Summary")
    output_emit(output, "- Views checked: `{}`".format(totals["views_checked"]))
    output_emit(
        output,
        "- Valid vertical candidates: `{}`".format(totals["vertical_candidates"]),
    )
    output_emit(
        output,
        "- Taggable candidates: `{}`".format(totals["taggable_candidates"]),
    )
    output_emit(output, "- Tags created: `{}`".format(len(created)))
    output_emit(
        output,
        "- Skipped already tagged in view: `{}`".format(totals["already_tagged"]),
    )
    output_emit(
        output,
        "- Skipped unconfigured system: `{}`".format(totals["unconfigured"]),
    )
    output_emit(output, "- Failed: `{}`".format(len(failed)))

    output_emit(output, "")
    output_emit(output, "## Per View Placement Summary")
    emit_batch_per_view_summary(output, results, True)

    output_emit(output, "")
    output_emit(output, "## Created Tags")
    output_table(
        output,
        [
            "View name",
            "Duct id",
            "Tag id",
            "System key",
            "Final intent",
            "Tag type",
            "Placement point",
        ],
        [
            [
                row["view_name"],
                linkify(output, row["duct_id"]),
                linkify(output, row["tag_id"]),
                row["system_key"],
                row["final_intent"],
                row["tag_type"],
                fmt_xyz_mm(row["tag_point"]),
            ]
            for row in created
        ],
    )

    output_emit(output, "")
    output_emit(output, "## Skipped / Failed")
    skipped_rows = []
    for result in results:
        for row in result["already_tagged_candidates"]:
            skipped_rows.append(
                [
                    result["view_name"],
                    linkify(output, row["duct_id"]),
                    STATUS_SKIPPED_ALREADY_TAGGED_IN_VIEW,
                ]
            )
        for row in result["unconfigured_candidates"]:
            skipped_rows.append(
                [
                    result["view_name"],
                    linkify(output, row["duct_id"]),
                    STATUS_SKIPPED_UNCONFIGURED_SYSTEM_INTENT,
                ]
            )
    for row in failed:
        skipped_rows.append(
            [
                row.get("view_name") or "-",
                linkify(output, row.get("duct_id")),
                row.get("reason") or "-",
            ]
        )
    output_table(output, ["View name", "Duct id", "Reason"], skipped_rows)

    TaskDialog.Show(
        "Place vertical duct tags in all floor plan views",
        (
            "Batch placement completed.\n\n"
            "Tags created: {}\n"
            "Skipped already tagged: {}\n"
            "Skipped unconfigured system: {}\n"
            "Failed: {}\n\n"
            "See the pyRevit output report for details."
        ).format(
            len(created),
            totals["already_tagged"],
            totals["unconfigured"],
            len(failed),
        ),
    )


def parse_float_or_keep(value, current_value, label, warnings):
    if value is None:
        return current_value
    text = str(value).strip()
    if text == "":
        return current_value
    try:
        return float(text.replace(",", "."))
    except Exception:
        warnings.append(
            "{} invalid input `{}`; kept current value `{}`.".format(
                label, text, current_value
            )
        )
        return current_value


def configure_vertical_duct_tag_placement_settings():
    cfg = get_config()
    warnings = []
    current_x = float(
        cfg.get(
            "tag_offset_x_mm", mm_from_ft(float(cfg.get("tag_offset_x_ft", 0.0) or 0.0))
        )
        or 0.0
    )
    current_y = float(
        cfg.get(
            "tag_offset_y_mm", mm_from_ft(float(cfg.get("tag_offset_y_ft", 0.0) or 0.0))
        )
        or 0.0
    )
    current_leader = bool(cfg.get("tag_add_leader", False))

    value_x = prompt_string(
        "Smart HVAC Vertical Duct Tags - X offset in mm",
        current_x,
    )
    new_x = parse_float_or_keep(value_x, current_x, "X offset mm", warnings)

    value_y = prompt_string(
        "Smart HVAC Vertical Duct Tags - Y offset in mm",
        current_y,
    )
    new_y = parse_float_or_keep(value_y, current_y, "Y offset mm", warnings)

    leader_choice = select_one(
        "Smart HVAC Vertical Duct Tags - Add leader",
        "Choose whether new vertical duct tags should be created with a leader.",
        [
            "false - No leader",
            "true - Add leader",
            "Skip - keep current ({})".format(fmt_bool(current_leader)),
        ],
    )
    new_leader = current_leader
    if leader_choice:
        if leader_choice.startswith("true"):
            new_leader = True
        elif leader_choice.startswith("false"):
            new_leader = False

    cfg["tag_offset_x_mm"] = new_x
    cfg["tag_offset_y_mm"] = new_y
    cfg["tag_add_leader"] = bool(new_leader)
    cfg["last_used_action"] = "Configure vertical duct tag placement settings"
    sync_tag_offset_feet_from_mm(cfg)
    write_config(cfg)

    lines = [
        "Vertical duct tag placement settings saved:",
        "",
        "X offset mm: {}".format(new_x),
        "Y offset mm: {}".format(new_y),
        "Add leader: {}".format(fmt_bool(new_leader)),
    ]
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        for warning in warnings:
            lines.append(warning)
            print("CONFIG UI -> placement settings warning: {}".format(warning))
    print(
        "CONFIG UI -> placement settings x_mm={} y_mm={} add_leader={}".format(
            new_x, new_y, new_leader
        )
    )
    TaskDialog.Show(
        "Configure vertical duct tag placement settings",
        "\n".join(lines),
    )


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
    family_symbols = get_selected_family_symbols(cfg) if selected_family else symbols
    intent_symbol_map = build_vertical_intent_symbol_map(family_symbols)

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
            if row["status"] == STATUS_VERTICAL_CANDIDATE:
                row = apply_system_intent_override(row, cfg)
            resolved_symbol = intent_symbol_map.get(
                row.get("final_intent") or row["intent"]
            )
            row["suggested_tag_type"] = (
                get_symbol_type_name(resolved_symbol)
                if resolved_symbol is not None
                else ""
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
    system_summary = build_system_summary(candidate_rows)
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
        "SCAN HVAC VERTICAL DUCTS -> system summary: {}".format(
            [
                {
                    "system_key": item["system_key"],
                    "count": item["count"],
                    "candidate_ids": item["candidate_ids"],
                    "geometry_intents": item["geometry_intents"],
                    "override_intents": item["override_intents"],
                    "final_intents": item["final_intents"],
                    "suggested_tag_types": item["suggested_tag_types"],
                }
                for item in system_summary
            ]
        )
    )
    matched_system_rules = [
        {
            "duct_id": r["duct_id"],
            "system_key": r.get("system_key"),
            "geometry_intent": r.get("geometry_intent"),
            "override_intent": r.get("override_intent"),
            "final_intent": r.get("final_intent"),
        }
        for r in candidate_rows
        if r.get("system_rule_matched")
    ]
    system_intent_changes = [
        {
            "duct_id": r["duct_id"],
            "system_key": r.get("system_key"),
            "geometry_intent": r.get("geometry_intent"),
            "override_intent": r.get("override_intent"),
            "final_intent": r.get("final_intent"),
        }
        for r in candidate_rows
        if r.get("system_rule_changed_intent")
    ]
    print(
        "SCAN HVAC VERTICAL DUCTS -> system rules matched: {}".format(
            len(matched_system_rules)
        )
    )
    print(
        "SCAN HVAC VERTICAL DUCTS -> system rules changed intent: {}".format(
            len(system_intent_changes)
        )
    )
    print(
        "SCAN HVAC VERTICAL DUCTS -> system intent changes: {}".format(
            system_intent_changes
        )
    )
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
    output_emit(
        output, "- System rules matched: `{}`".format(len(matched_system_rules))
    )
    output_emit(
        output,
        "- System rules changed intent: `{}`".format(len(system_intent_changes)),
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
    if system_intent_changes:
        output_emit(
            output,
            "- System override changed intent: `{}`".format(len(system_intent_changes)),
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
        "System Name",
        "System Type",
        "System Classification",
        "MEPSystem Name",
        "System key",
        "Geometry intent",
        "Override intent",
        "Final intent",
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
            row.get("system_name") or "-",
            row.get("system_type") or "-",
            row.get("system_classification") or "-",
            row.get("mep_system_name") or "-",
            row.get("system_key") or "-",
            row.get("geometry_intent") or row["intent"],
            row.get("override_intent") or INTENT_GEOMETRY_FALLBACK,
            row.get("final_intent") or row["intent"],
            row["suggested_tag_type"] or "-",
            row["reason"],
        ]
        output_emit(
            output, "|" + "|".join([str(v).replace("\n", " ") for v in values]) + "|"
        )

    if not candidate_rows:
        output_emit(output, "|" + "|".join(["-"] * len(headers)) + "|")

    output_emit(output, "")
    output_emit(output, "## System Type Summary")
    summary_headers = [
        "System key",
        "Count",
        "Candidate element ids",
        "Geometry intents",
        "Override intent",
        "Final intents",
        "Suggested tag types",
    ]
    output_emit(output, "|" + "|".join(summary_headers) + "|")
    output_emit(output, "|" + "|".join(["---"] * len(summary_headers)) + "|")
    for item in system_summary:
        values = [
            item["system_key"],
            item["count"],
            ", ".join([str(v) for v in item["candidate_ids"]]),
            ", ".join(item["geometry_intents"]),
            ", ".join(item["override_intents"]),
            ", ".join(item["final_intents"]),
            ", ".join(item["suggested_tag_types"]),
        ]
        output_emit(
            output, "|" + "|".join([str(v).replace("\n", " ") for v in values]) + "|"
        )
    if not system_summary:
        output_emit(output, "|" + "|".join(["-"] * len(summary_headers)) + "|")

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


def collect_active_view_vertical_duct_system_keys(cfg):
    view = doc.ActiveView
    active_level = get_active_level(view)
    if active_level is None:
        return None, "Active view is not a floor plan view with a valid level."
    levels = collect_project_levels()
    band_bottom, band_top = get_active_level_band(active_level, levels)
    system_keys = []
    checked = 0
    candidates = 0
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
            if row["status"] != STATUS_VERTICAL_CANDIDATE:
                continue
            candidates += 1
            system_key = get_system_summary_key(row)
            if system_key not in system_keys:
                system_keys.append(system_key)
    except Exception as ex:
        return None, "Failed to scan active view system keys: {}".format(ex)
    print(
        "CONFIG UI -> active view system key scan checked={} candidates={} keys={}".format(
            checked, candidates, system_keys
        )
    )
    return sorted(system_keys), ""


def collect_active_view_vertical_duct_system_context(cfg):
    view = doc.ActiveView
    active_level = get_active_level(view)
    if active_level is None:
        return None, "Active view is not a floor plan view with a valid level."
    levels = collect_project_levels()
    band_bottom, band_top = get_active_level_band(active_level, levels)
    context = {}
    checked = 0
    candidates = 0
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
            if row["status"] != STATUS_VERTICAL_CANDIDATE:
                continue
            candidates += 1
            system_key = get_system_summary_key(row)
            if system_key not in context:
                context[system_key] = {
                    "candidate_ids": [],
                    "geometry_intents": [],
                }
            context[system_key]["candidate_ids"].append(row.get("duct_id"))
            intent = row.get("intent") or INTENT_UNKNOWN_VERTICAL_DUCT
            if intent not in context[system_key]["geometry_intents"]:
                context[system_key]["geometry_intents"].append(intent)
    except Exception as ex:
        return None, "Failed to scan active view system keys: {}".format(ex)
    print(
        "CONFIG UI -> active view system context scan checked={} candidates={} keys={}".format(
            checked, candidates, sorted(context.keys())
        )
    )
    return context, ""


SYSTEM_OVERRIDE_OPTION_LABELS = [
    (INTENT_GEOMETRY_FALLBACK, "GEOMETRY_FALLBACK - Use duct geometry direction"),
    (INTENT_CONTINUOUS_UP, "CONTINUOUS_UP - Force Continuous Up"),
    (INTENT_CONTINUOUS_DOWN, "CONTINUOUS_DOWN - Force Continuous Down"),
    (INTENT_GOING_UP, "GOING_UP - Force Going Up"),
    (INTENT_GOING_DOWN, "GOING_DOWN - Force Going Down"),
    (INTENT_COMING_UP, "COMING_UP - Force Coming Up"),
    (INTENT_COMING_DOWN, "COMING_DOWN - Force Coming Down"),
    ("SKIP", "SKIP - Keep current value"),
]


def configure_system_type_intent_overrides():
    cfg = get_config()
    print("CONFIG UI -> configuring system intent overrides")
    system_context, error = collect_active_view_vertical_duct_system_context(cfg)
    if error:
        TaskDialog.Show("Configure system type intent overrides", error)
        return
    if not system_context:
        TaskDialog.Show(
            "Configure system type intent overrides",
            "No valid vertical duct candidates with system keys were found in the active view.",
        )
        return

    existing = get_system_intent_overrides(cfg)
    updated = dict(existing)
    options = [label for value, label in SYSTEM_OVERRIDE_OPTION_LABELS]
    label_to_value = {}
    for value, label in SYSTEM_OVERRIDE_OPTION_LABELS:
        label_to_value[label] = value
    system_keys = sorted(system_context.keys())
    for system_key in system_keys:
        current = existing.get(system_key, INTENT_GEOMETRY_FALLBACK)
        item = system_context.get(system_key, {})
        geometry_intents = ", ".join(sorted(item.get("geometry_intents", []))) or "-"
        candidate_ids = (
            ", ".join([str(v) for v in item.get("candidate_ids", [])]) or "-"
        )
        picked = select_one(
            "Configure vertical intent for system type",
            (
                "System key: {}\n"
                "Current override: {}\n"
                "Detected geometry intents in active view: {}\n"
                "Candidate ids: {}\n\n"
                "Office convention must be confirmed by HVAC.\n"
                "If Supply Air / blue goes up, choose CONTINUOUS_UP.\n"
                "If Return Air / green goes down, choose CONTINUOUS_DOWN.\n\n"
                "Choose final vertical tag intent for this system type."
            ).format(
                system_key,
                current,
                geometry_intents,
                candidate_ids,
            ),
            options,
        )
        if not picked:
            print("CONFIG UI -> system intent override configuration cancelled")
            return
        picked_value = label_to_value.get(picked, "")
        if picked_value == "SKIP":
            continue
        updated[system_key] = picked_value
        print(
            "CONFIG RESOLVE -> system intent override {} = {}".format(
                system_key, picked_value
            )
        )

    cfg["system_intent_overrides"] = updated
    cfg["last_used_action"] = "Configure system type intent overrides"
    write_config(cfg)
    lines = ["System intent overrides saved:"]
    if updated:
        for key in sorted(updated.keys()):
            lines.append("{} -> {}".format(key, updated[key]))
    else:
        lines.append("none")
    lines.append("")
    lines.append(
        "Next test:\nRun Scan active view vertical ducts and verify Geometry intent vs Final intent."
    )
    TaskDialog.Show("Configure system type intent overrides", "\n".join(lines))


def show_current_hvac_tagging_configuration():
    cfg = get_config()
    output = script.get_output() if script is not None else None
    output_emit(output, "# Smart HVAC Vertical Duct Tags - Current Configuration")
    output_emit(output, "- Config path: `{}`".format(get_config_path()))
    for key in sorted(cfg.keys()):
        output_emit(output, "- `{}`: `{}`".format(key, cfg.get(key)))
    overrides = get_system_intent_overrides(cfg)
    output_emit(output, "")
    output_emit(output, "## System intent overrides")
    if overrides:
        for key in sorted(overrides.keys()):
            output_emit(output, "- `{}` -> `{}`".format(key, overrides[key]))
    else:
        output_emit(output, "System intent overrides: none")
    print("CONFIG UI -> current HVAC tagging configuration shown.")


# =========================
# Main menu
# =========================
def main_menu():
    actions = [
        "Scan active view vertical ducts",
        "Place vertical duct tags for selected candidates",
        "Scan all floor plan views for vertical ducts",
        "Place vertical duct tags in all floor plan views",
        "Configure vertical duct tag placement settings",
        "Choose loaded vertical duct tag family",
        "Load vertical duct tag family from .rfa",
        "Configure system type intent overrides",
        "Show current HVAC tagging configuration",
        "Exit",
    ]
    while True:
        choice = select_one("Smart HVAC Vertical Duct Tags", "Choose action", actions)
        if not choice or choice == "Exit":
            return
        if choice == "Scan active view vertical ducts":
            scan_active_view_vertical_ducts()
        elif choice == "Place vertical duct tags for selected candidates":
            place_vertical_duct_tags_for_selected_candidates()
        elif choice == "Scan all floor plan views for vertical ducts":
            scan_all_floor_plan_views_for_vertical_ducts()
        elif choice == "Place vertical duct tags in all floor plan views":
            place_vertical_duct_tags_in_all_floor_plan_views()
        elif choice == "Configure vertical duct tag placement settings":
            configure_vertical_duct_tag_placement_settings()
        elif choice == "Choose loaded vertical duct tag family":
            choose_loaded_vertical_duct_tag_family()
        elif choice == "Load vertical duct tag family from .rfa":
            load_vertical_duct_tag_family_from_rfa()
        elif choice == "Configure system type intent overrides":
            configure_system_type_intent_overrides()
        elif choice == "Show current HVAC tagging configuration":
            show_current_hvac_tagging_configuration()


if __name__ == "__main__":
    main_menu()

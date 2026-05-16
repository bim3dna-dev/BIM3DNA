# -*- coding: utf-8 -*-
__title__ = "Pipe Slope\nGuard"
__author__ = "BIM3DNA"
__doc__ = "Audit and optionally apply 5.0 / 1000 slope to horizontal pipes."
__version__ = "2.0.1"

# =========================
# Config
# =========================
DEFAULT_TARGET_SLOPE_RATIO = 0.005
MIN_TARGET_SLOPE_RATIO = 0.0001
MAX_TARGET_SLOPE_RATIO = 0.05
SLOPE_TOL = 1e-4
MIN_HORIZONTAL_RUN_FT = 0.10
NEAR_VERTICAL_XY_TOL_FT = 0.01
NEAR_VERTICAL_DIR_Z = 0.95
MAX_BULK_PROCESSABLE_SLOPE_RATIO = 0.05
MAX_ABS_DIRECTION_Z_FOR_HORIZONTAL = 0.20
ANGLED_TRANSITION_MIN_DEGREES = 20.0
ANGLED_TRANSITION_MAX_DEGREES = 70.0
VERTICAL_MIN_DEGREES = 75.0
FORCE_CONNECTED_OWNER_MOVE_WARN_MM = 1.0
FORCE_CONNECTED_OWNER_MOVE_HARD_LIMIT_MM = 500.0
FORCE_ALLOW_STEEP_PIPE = False
FORCE_SELECTION_SOFT_WARNING_COUNT = 30
FORCE_LOCAL_MAX_PASSES = 2

STATUS_OK = "OK"
STATUS_WRONG_SLOPE = "WRONG_SLOPE"
STATUS_SKIPPED_VERTICAL = "SKIPPED_VERTICAL"
STATUS_SKIPPED_PINNED = "SKIPPED_PINNED"
STATUS_SKIPPED_TOO_SHORT = "SKIPPED_TOO_SHORT"
STATUS_SKIPPED_NO_LOCATION = "SKIPPED_NO_LOCATION"
STATUS_SKIPPED_CONNECTED_NETWORK = "SKIPPED_CONNECTED_NETWORK"
STATUS_SKIPPED_STEEP_PIPE = "SKIPPED_STEEP_PIPE"
STATUS_SKIPPED_ANGLED_TRANSITION = "SKIPPED_ANGLED_TRANSITION"
STATUS_FAILED = "FAILED"
ROLLBACK_HARD_CONSTRAINED_BY_CONNECTED_FITTINGS = "HARD_CONSTRAINED_BY_CONNECTED_FITTINGS"
ROLLBACK_CONSTRAINED_BY_ANGLED_TRANSITION = "CONSTRAINED_BY_ANGLED_TRANSITION"
ROLLBACK_CONNECTED_FITTING_ACTION = (
    "Inspect/rebuild local fitting assembly; automatic fitting movement is intentionally disabled."
)


# =========================
# Revit context
# =========================
from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    Element,
    ElementId,
    FailureProcessingResult,
    FailureSeverity,
    FilteredElementCollector,
    IFailuresPreprocessor,
    Line,
    LocationCurve,
    LocationPoint,
    StorageType,
    Transaction,
    TransactionGroup,
    XYZ,
)
from Autodesk.Revit.UI import (
    TaskDialog,
    TaskDialogCommandLinkId,
    TaskDialogCommonButtons,
    TaskDialogResult,
)
import json
import math
import os
import clr
clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")
from System.Windows.Forms import (
    Button,
    DialogResult,
    Form,
    FormBorderStyle,
    FormStartPosition,
    Label,
    ListBox,
    SelectionMode,
    TextBox,
)
from System.Drawing import Point, Size
from System.Collections.Generic import List

uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document
try:
    from pyrevit import script
except Exception:
    script = None


# =========================
# Helpers
# =========================
class GuardFailuresPreprocessor(IFailuresPreprocessor):
    def __init__(self):
        self.messages = []

    def PreprocessFailures(self, failures_accessor):
        has_error = False
        try:
            failure_messages = failures_accessor.GetFailureMessages()
        except Exception:
            failure_messages = []

        for failure_message in failure_messages:
            try:
                description = failure_message.GetDescriptionText() or ""
            except Exception:
                description = ""

            try:
                severity = failure_message.GetSeverity()
            except Exception:
                severity = None

            if severity == FailureSeverity.Warning:
                try:
                    failures_accessor.DeleteWarning(failure_message)
                except Exception:
                    pass
                self.messages.append("Warning: {}".format(description))
            else:
                has_error = True
                self.messages.append("Error: {}".format(description))

        if has_error:
            return FailureProcessingResult.ProceedWithRollBack
        return FailureProcessingResult.Continue


def ft_to_display_per_1000(slope_ratio):
    if slope_ratio is None:
        return "-"
    return round(float(slope_ratio) * 1000.0, 3)


def get_config_path():
    try:
        return os.path.join(os.path.dirname(__file__), "pipe_slope_guard_config.json")
    except Exception:
        return "pipe_slope_guard_config.json"


def parse_target_slope_input(text_value):
    value = (text_value or "").strip().lower().replace(",", ".")
    if not value:
        return None
    try:
        if "/1000" in value:
            number_text = value.split("/1000")[0].strip()
            numeric_value = float(number_text)
            ratio = numeric_value / 1000.0
        else:
            numeric_value = float(value)
            if numeric_value >= 0.1:
                ratio = numeric_value / 1000.0
            else:
                ratio = numeric_value
    except Exception:
        return None

    if ratio < MIN_TARGET_SLOPE_RATIO or ratio > MAX_TARGET_SLOPE_RATIO:
        return None
    return ratio


def load_target_slope_ratio():
    path = get_config_path()
    try:
        if not os.path.exists(path):
            return DEFAULT_TARGET_SLOPE_RATIO
        config_file = open(path, "r")
        try:
            data = json.load(config_file)
        finally:
            config_file.close()
        ratio = float(data.get("target_slope_ratio", DEFAULT_TARGET_SLOPE_RATIO))
        if ratio < MIN_TARGET_SLOPE_RATIO or ratio > MAX_TARGET_SLOPE_RATIO:
            raise Exception("target slope outside allowed range")
        return ratio
    except Exception as ex:
        print("PIPE SLOPE GUARD -> config read warning: {}".format(ex))
        return DEFAULT_TARGET_SLOPE_RATIO


def save_target_slope_ratio(target_slope_ratio):
    path = get_config_path()
    try:
        data = {
            "target_slope_ratio": float(target_slope_ratio),
            "target_slope_per_1000": float(target_slope_ratio) * 1000.0,
        }
        config_file = open(path, "w")
        try:
            json.dump(data, config_file, indent=2)
        finally:
            config_file.close()
    except Exception as ex:
        print("PIPE SLOPE GUARD -> config write warning: {}".format(ex))


def format_target_per_1000(target_slope_ratio):
    return round(float(target_slope_ratio) * 1000.0, 3)


def safe_name(value, fallback=""):
    try:
        return value if value else fallback
    except Exception:
        return fallback


def get_pipe_type_name(pipe):
    try:
        pipe_type = doc.GetElement(pipe.GetTypeId())
        if pipe_type is not None:
            try:
                name_value = Element.Name.GetValue(pipe_type)
                if name_value:
                    return name_value
            except Exception:
                pass
            try:
                if pipe_type.Name:
                    return pipe_type.Name
            except Exception:
                pass
            try:
                param = pipe_type.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
                if param:
                    name_value = param.AsString() or param.AsValueString()
                    if name_value:
                        return name_value
            except Exception:
                pass
    except Exception:
        pass
    return "<Unknown Type>"


def get_pipe_system_name(pipe):
    try:
        mep_system = pipe.MEPSystem
        if mep_system is not None:
            return safe_name(mep_system.Name, "")
    except Exception:
        pass

    for param_name in ["System Name", "System Type"]:
        try:
            param = pipe.LookupParameter(param_name)
            if param:
                value = param.AsString() if param.StorageType == StorageType.String else param.AsValueString()
                if value:
                    return value
        except Exception:
            pass
    return ""


def get_param_double_by_builtin(elem, builtin_name):
    try:
        bip = getattr(BuiltInParameter, builtin_name)
    except Exception:
        return None
    try:
        param = elem.get_Parameter(bip)
        if param and param.StorageType == StorageType.Double:
            return param
    except Exception:
        return None
    return None


def get_param_by_lookup_names(elem, names):
    for name in names:
        try:
            param = elem.LookupParameter(name)
            if param:
                return param
        except Exception:
            pass
    return None


def get_pipe_connectors(pipe):
    connectors = []
    try:
        connector_set = pipe.ConnectorManager.Connectors
    except Exception:
        connector_set = None
    if connector_set is None:
        try:
            connector_set = pipe.MEPModel.ConnectorManager.Connectors
        except Exception:
            connector_set = None
    if connector_set is None:
        return connectors
    try:
        for connector in connector_set:
            connectors.append(connector)
    except Exception:
        pass
    return connectors


def get_end_connectors(pipe):
    end_connectors = []
    for connector in get_pipe_connectors(pipe):
        try:
            connector_type = str(connector.ConnectorType)
        except Exception:
            connector_type = ""
        if connector_type == "End":
            end_connectors.append(connector)
    if end_connectors:
        return end_connectors
    return get_pipe_connectors(pipe)


def get_connected_owner_ids(pipe):
    owner_ids = set()
    pipe_id = pipe.Id.IntegerValue
    for connector in get_end_connectors(pipe):
        try:
            refs = connector.AllRefs
        except Exception:
            refs = []
        try:
            for ref_connector in refs:
                try:
                    owner = ref_connector.Owner
                    if owner is None:
                        continue
                    owner_id = owner.Id.IntegerValue
                    if owner_id != pipe_id:
                        owner_ids.add(owner_id)
                except Exception:
                    pass
        except Exception:
            pass
    return sorted(list(owner_ids))


def get_connected_owner_infos(pipe):
    owner_infos = {}
    pipe_id = pipe.Id.IntegerValue
    for connector in get_end_connectors(pipe):
        try:
            refs = connector.AllRefs
        except Exception:
            refs = []
        try:
            for ref_connector in refs:
                try:
                    owner = ref_connector.Owner
                    if owner is None:
                        continue
                    owner_id = owner.Id.IntegerValue
                    if owner_id == pipe_id:
                        continue
                    category_name = ""
                    category_id = None
                    try:
                        if owner.Category is not None:
                            category_name = owner.Category.Name or ""
                            category_id = owner.Category.Id.IntegerValue
                    except Exception:
                        pass
                    owner_infos[owner_id] = {
                        "id": owner_id,
                        "category_name": category_name,
                        "category_id": category_id,
                        "name": get_element_display_name(owner),
                    }
                except Exception:
                    pass
        except Exception:
            pass
    return [owner_infos[x] for x in sorted(owner_infos.keys())]


def get_connected_fitting_ids(pipe):
    fitting_ids = []
    try:
        fitting_category_id = int(BuiltInCategory.OST_PipeFitting)
    except Exception:
        fitting_category_id = None
    for info in get_connected_owner_infos(pipe):
        if fitting_category_id is not None and info.get("category_id") == fitting_category_id:
            fitting_ids.append(info.get("id"))
    return sorted(fitting_ids)


def is_connected_to_pipe_fitting(pipe):
    return len(get_connected_fitting_ids(pipe)) > 0


def get_connected_angled_transition_pipe_ids(pipe, max_depth=1):
    angled_ids = set()
    pipe_id = pipe.Id.IntegerValue

    for connector in get_end_connectors(pipe):
        try:
            refs = connector.AllRefs
        except Exception:
            refs = []
        for ref_connector in refs:
            try:
                owner = ref_connector.Owner
            except Exception:
                owner = None
            if owner is None:
                continue
            try:
                owner_id = owner.Id.IntegerValue
            except Exception:
                continue
            if owner_id == pipe_id:
                continue

            if is_pipe_category(owner):
                if is_angled_transition_pipe(owner):
                    angled_ids.add(owner_id)
                continue

            if max_depth < 1:
                continue

            for owner_connector in get_pipe_connectors(owner):
                try:
                    owner_refs = owner_connector.AllRefs
                except Exception:
                    owner_refs = []
                for next_ref in owner_refs:
                    try:
                        next_owner = next_ref.Owner
                    except Exception:
                        next_owner = None
                    if next_owner is None:
                        continue
                    try:
                        next_id = next_owner.Id.IntegerValue
                    except Exception:
                        continue
                    if next_id == pipe_id or next_id == owner_id:
                        continue
                    if is_pipe_category(next_owner) and is_angled_transition_pipe(next_owner):
                        angled_ids.add(next_id)

    return sorted(list(angled_ids))


def is_pipe_connected(pipe):
    return len(get_connected_owner_ids(pipe)) > 0


def xyz_to_tuple(point):
    if point is None:
        return None
    return (float(point.X), float(point.Y), float(point.Z))


def distance_mm(a, b):
    if a is None or b is None:
        return None
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    dz = a[2] - b[2]
    return ((dx * dx + dy * dy + dz * dz) ** 0.5) * 304.8


def point_list_movement_mm(before_points, after_points):
    if before_points is None or after_points is None:
        return None
    if len(before_points) != len(after_points):
        return None
    max_move = 0.0
    for idx in range(len(before_points)):
        move = distance_mm(before_points[idx], after_points[idx])
        if move is None:
            return None
        if move > max_move:
            max_move = move
    return max_move


def get_element_display_name(elem):
    if elem is None:
        return ""
    try:
        name_value = Element.Name.GetValue(elem)
        if name_value:
            return name_value
    except Exception:
        pass
    try:
        if elem.Name:
            return elem.Name
    except Exception:
        pass
    try:
        type_elem = doc.GetElement(elem.GetTypeId())
        if type_elem is not None:
            name_value = Element.Name.GetValue(type_elem)
            if name_value:
                return name_value
    except Exception:
        pass
    return ""


def get_element_location_summary(elem):
    summary = {"kind": "none", "points": None}
    try:
        loc = elem.Location
    except Exception:
        loc = None

    try:
        if isinstance(loc, LocationPoint):
            summary["kind"] = "point"
            summary["points"] = [xyz_to_tuple(loc.Point)]
            return summary
    except Exception:
        pass

    try:
        if isinstance(loc, LocationCurve) and loc.Curve is not None:
            summary["kind"] = "curve"
            summary["points"] = [
                xyz_to_tuple(loc.Curve.GetEndPoint(0)),
                xyz_to_tuple(loc.Curve.GetEndPoint(1)),
            ]
            return summary
    except Exception:
        pass

    try:
        bb = elem.get_BoundingBox(None)
        if bb:
            center = XYZ(
                (bb.Min.X + bb.Max.X) * 0.5,
                (bb.Min.Y + bb.Max.Y) * 0.5,
                (bb.Min.Z + bb.Max.Z) * 0.5,
            )
            summary["kind"] = "bbox_center"
            summary["points"] = [xyz_to_tuple(center)]
    except Exception:
        pass
    return summary


def count_connected_connectors(pipe):
    connected_count = 0
    for connector in get_pipe_connectors(pipe):
        connected = False
        try:
            refs = connector.AllRefs
        except Exception:
            refs = []
        try:
            for ref_connector in refs:
                try:
                    owner = ref_connector.Owner
                    if owner is not None and owner.Id.IntegerValue != pipe.Id.IntegerValue:
                        connected = True
                        break
                except Exception:
                    pass
        except Exception:
            pass
        if connected:
            connected_count += 1
    return connected_count


def snapshot_connected_owner(owner_id):
    elem = doc.GetElement(ElementId(int(owner_id)))
    snap = {
        "id": int(owner_id),
        "category_name": "",
        "name": "",
        "location": {"kind": "missing", "points": None},
        "is_valid": False,
    }
    if elem is None:
        return snap
    try:
        snap["is_valid"] = bool(elem.IsValidObject)
    except Exception:
        snap["is_valid"] = True
    try:
        if elem.Category is not None:
            snap["category_name"] = elem.Category.Name or ""
    except Exception:
        pass
    snap["name"] = get_element_display_name(elem)
    snap["location"] = get_element_location_summary(elem)
    return snap


def snapshot_pipe_connectivity(pipe):
    connector_count = len(get_pipe_connectors(pipe))
    connected_ids = get_connected_owner_ids(pipe)
    p0, p1 = curve_points(pipe)
    owner_snaps = {}
    for owner_id in connected_ids:
        owner_snaps[int(owner_id)] = snapshot_connected_owner(owner_id)
    return {
        "pipe_id": pipe.Id.IntegerValue,
        "connected_owner_ids": connected_ids,
        "connected_owner_count": len(connected_ids),
        "connector_count": connector_count,
        "connected_connector_count": count_connected_connectors(pipe),
        "pipe_endpoint_points": [
            xyz_to_tuple(p0),
            xyz_to_tuple(p1),
        ],
        "pipe_slope_ratio": achieved_slope_ratio(pipe),
        "connected_owner_snapshots": owner_snaps,
    }


def get_slope_parameter(pipe):
    for builtin_name in ["RBS_PIPE_SLOPE"]:
        param = get_param_double_by_builtin(pipe, builtin_name)
        if param and not param.IsReadOnly:
            return param

    for name in ["Slope"]:
        param = get_param_by_lookup_names(pipe, [name])
        if param and not param.IsReadOnly and param.StorageType == StorageType.Double:
            return param
    return None


def get_offset_parameters(pipe):
    start_param = None
    end_param = None

    for builtin_name in ["RBS_START_OFFSET_PARAM", "RBS_PIPE_START_OFFSET_PARAM"]:
        start_param = get_param_double_by_builtin(pipe, builtin_name)
        if start_param:
            break

    for builtin_name in ["RBS_END_OFFSET_PARAM", "RBS_PIPE_END_OFFSET_PARAM"]:
        end_param = get_param_double_by_builtin(pipe, builtin_name)
        if end_param:
            break

    if start_param is None:
        start_param = get_param_by_lookup_names(
            pipe, ["Start Middle Elevation", "Start Offset", "Start Center Elevation"]
        )
    if end_param is None:
        end_param = get_param_by_lookup_names(
            pipe, ["End Middle Elevation", "End Offset", "End Center Elevation"]
        )

    if (
        start_param
        and end_param
        and (not start_param.IsReadOnly)
        and (not end_param.IsReadOnly)
        and start_param.StorageType == StorageType.Double
        and end_param.StorageType == StorageType.Double
    ):
        return start_param, end_param

    return None, None


def set_failure_options(transaction, preprocessor):
    try:
        options = transaction.GetFailureHandlingOptions()
        options = options.SetFailuresPreprocessor(preprocessor)
        options = options.SetClearAfterRollback(True)
        try:
            options = options.SetForcedModalHandling(False)
        except Exception:
            pass
        transaction.SetFailureHandlingOptions(options)
    except Exception:
        pass


def show_info_dialog(title, message):
    dialog = TaskDialog(title)
    dialog.MainInstruction = title
    dialog.MainContent = message
    dialog.Show()


# =========================
# Collection
# =========================
def choose_mode():
    last_target_slope = load_target_slope_ratio()
    options = [
        ("Scan selected pipes", {"mode": "audit", "scope": "selected", "force": False}),
        ("Scan active view pipes", {"mode": "audit", "scope": "active_view", "force": False}),
        ("Scan entire model pipes", {"mode": "audit", "scope": "entire_model", "force": False}),
        ("Safe apply selected pipes only", {"mode": "safe_apply", "scope": "selected", "force": False}),
        ("Safe apply active view pipes", {"mode": "safe_apply", "scope": "active_view", "force": False}),
        ("Safe apply entire model pipes", {"mode": "safe_apply", "scope": "entire_model", "force": False}),
        ("Select wrong-slope pipes in active view", {"mode": "select_wrong", "scope": "active_view", "force": False}),
        ("Select wrong-slope pipes in entire model", {"mode": "select_wrong", "scope": "entire_model", "force": False}),
        ("Force apply selected pipes only", {"mode": "force_apply", "scope": "selected", "force": True}),
    ]

    try:
        form = Form()
        form.Text = "Pipe Slope Guard"
        form.ClientSize = Size(400, 345)
        form.FormBorderStyle = FormBorderStyle.FixedDialog
        form.StartPosition = FormStartPosition.CenterScreen
        form.MaximizeBox = False
        form.MinimizeBox = False

        slope_label = Label()
        slope_label.Text = "Target slope (/1000):"
        slope_label.Location = Point(10, 12)
        slope_label.Size = Size(145, 20)
        form.Controls.Add(slope_label)

        slope_text = TextBox()
        slope_text.Location = Point(155, 10)
        slope_text.Size = Size(90, 22)
        slope_text.Text = str(format_target_per_1000(last_target_slope))
        form.Controls.Add(slope_text)

        label = Label()
        label.Text = "Choose scope and action:"
        label.Location = Point(10, 45)
        label.Size = Size(360, 20)
        form.Controls.Add(label)

        list_box = ListBox()
        list_box.Location = Point(10, 70)
        list_box.Size = Size(380, 210)
        list_box.SelectionMode = SelectionMode.One
        for option_label, _mode_info in options:
            list_box.Items.Add(option_label)
        list_box.SelectedIndex = 0
        form.Controls.Add(list_box)

        ok_button = Button()
        ok_button.Text = "OK"
        ok_button.Location = Point(220, 300)
        ok_button.DialogResult = DialogResult.OK
        form.Controls.Add(ok_button)

        cancel_button = Button()
        cancel_button.Text = "Cancel"
        cancel_button.Location = Point(305, 300)
        cancel_button.DialogResult = DialogResult.Cancel
        form.Controls.Add(cancel_button)

        form.AcceptButton = ok_button
        form.CancelButton = cancel_button

        if form.ShowDialog() != DialogResult.OK:
            return None
        selected_index = list_box.SelectedIndex
        if selected_index < 0:
            return None
        target_slope_ratio = parse_target_slope_input(slope_text.Text)
        if target_slope_ratio is None:
            show_info_dialog(
                "Pipe Slope Guard",
                "Invalid target slope. Use values like 5, 5.0/1000, or 0.005.",
            )
            return None
        save_target_slope_ratio(target_slope_ratio)
        mode_info = dict(options[selected_index][1])
        mode_info["target_slope_ratio"] = target_slope_ratio
        return mode_info
    except Exception as ex:
        show_info_dialog(
            "Pipe Slope Guard",
            "Could not open the mode selection dialog.\n\n{}".format(ex),
        )
        return None


def confirm_force_apply(preflight_counts=None):
    dialog = TaskDialog("Pipe Slope Guard")
    dialog.MainInstruction = "Force apply selected pipes only"
    warning = (
        "Force apply may rewrite pipe geometry on connected MEP networks and can "
        "move fittings or pipe endpoints. Use only on a selected local area. "
        "Topology will be validated before committing."
    )
    if preflight_counts:
        dialog.MainContent = (
            "Force Apply Preflight:\n"
            "Selected PipeCurves: {selected}\n"
            "Force-correctable wrong horizontal pipes: {force_correctable}\n"
            "Already correct: {already_correct}\n"
            "Skipped vertical: {skipped_vertical}\n"
            "Skipped angled transition: {skipped_angled_transition}\n"
            "Skipped steep/transition: {skipped_steep_pipe}\n"
            "Skipped too short/no-location/pinned: {skipped_other}\n\n"
            "{warning}"
        ).format(warning=warning, **preflight_counts)
    else:
        dialog.MainContent = warning
    dialog.CommonButtons = TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No
    result = dialog.Show()
    return result == TaskDialogResult.Yes


def show_no_force_correctable_warning():
    show_info_dialog(
        "Pipe Slope Guard",
        "No force-correctable wrong horizontal pipes found in the current selection.",
    )


def confirm_large_force_selection(force_count):
    dialog = TaskDialog("Pipe Slope Guard")
    dialog.MainInstruction = "Large Force Apply Selection"
    dialog.MainContent = (
        "You selected {} force-correctable wrong horizontal pipes.\n\n"
        "Force apply may rewrite pipe geometry on connected MEP networks and can move "
        "fittings or pipe endpoints. This is selected-only, but the selected set is larger "
        "than the recommended local batch size of {}.\n\n"
        "The tool will validate topology per pipe, roll back failed pipes, and keep "
        "failed/rolled-back pipes selected after completion.\n\n"
        "Continue?"
    ).format(force_count, FORCE_SELECTION_SOFT_WARNING_COUNT)
    dialog.CommonButtons = TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No
    return dialog.Show() == TaskDialogResult.Yes


def show_broad_entire_model_selection_warning(selected_count):
    show_info_dialog(
        "Pipe Slope Guard",
        (
            "Selected {} wrong-slope pipes across the entire model. The elements are "
            "selected, but Force Apply will require explicit confirmation above {} force-correctable pipes. Use this selection for "
            "review, then isolate/select a smaller local area before Force Apply."
        ).format(selected_count, FORCE_SELECTION_SOFT_WARNING_COUNT),
    )


def collect_selected_pipes():
    pipes = []
    selected_ids = list(uidoc.Selection.GetElementIds())
    for element_id in selected_ids:
        try:
            elem = doc.GetElement(element_id)
            if elem and elem.Category and elem.Category.Id.IntegerValue == int(BuiltInCategory.OST_PipeCurves):
                pipes.append(elem)
        except Exception:
            pass
    return pipes


def collect_active_view_pipes():
    view = uidoc.ActiveView
    return list(
        FilteredElementCollector(doc, view.Id)
        .OfCategory(BuiltInCategory.OST_PipeCurves)
        .WhereElementIsNotElementType()
        .ToElements()
    )


def collect_entire_model_pipes():
    return list(
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_PipeCurves)
        .WhereElementIsNotElementType()
        .ToElements()
    )


def count_entire_model_pipes():
    try:
        return (
            FilteredElementCollector(doc)
            .OfCategory(BuiltInCategory.OST_PipeCurves)
            .WhereElementIsNotElementType()
            .GetElementCount()
        )
    except Exception:
        return len(collect_entire_model_pipes())


def confirm_entire_model_scan(pipe_count):
    dialog = TaskDialog("Pipe Slope Guard")
    dialog.MainInstruction = "Scan entire model pipes"
    if pipe_count > 500:
        dialog.MainContent = (
            "Scanning the entire model may take time. No elements will be modified.\n\n"
            "PipeCurves found: {}".format(pipe_count)
        )
    else:
        dialog.MainContent = "Scanning the entire model may take time. No elements will be modified."
    dialog.CommonButtons = TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No
    return dialog.Show() == TaskDialogResult.Yes


def confirm_entire_model_safe_apply():
    dialog = TaskDialog("Pipe Slope Guard")
    dialog.MainInstruction = "Safe apply entire model pipes"
    dialog.MainContent = (
        "Safe apply entire model pipes may modify writable/unconnected pipes across the whole model. "
        "Connected networks requiring geometry rewrite will be skipped. Continue?"
    )
    dialog.CommonButtons = TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No
    return dialog.Show() == TaskDialogResult.Yes


# =========================
# Slope math
# =========================
def curve_points(pipe):
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


def horizontal_run_xy(pipe):
    p0, p1 = curve_points(pipe)
    if p0 is None or p1 is None:
        return None
    dx = p1.X - p0.X
    dy = p1.Y - p0.Y
    return (dx * dx + dy * dy) ** 0.5


def achieved_slope_ratio(pipe):
    p0, p1 = curve_points(pipe)
    if p0 is None or p1 is None:
        return None
    xy_run = horizontal_run_xy(pipe)
    if xy_run is None or xy_run <= 1e-9:
        return None
    return abs(p1.Z - p0.Z) / xy_run


def get_pipe_direction_data(pipe):
    p0, p1 = curve_points(pipe)
    if p0 is None or p1 is None:
        return None
    dx = p1.X - p0.X
    dy = p1.Y - p0.Y
    dz = p1.Z - p0.Z
    horizontal_run = (dx * dx + dy * dy) ** 0.5
    length = (dx * dx + dy * dy + dz * dz) ** 0.5
    if length <= 1e-9:
        abs_direction = None
    else:
        abs_direction = abs(dz) / length
    if horizontal_run <= 1e-9:
        slope_ratio = None
    else:
        slope_ratio = abs(dz) / horizontal_run
    angle = math.degrees(math.atan2(abs(dz), horizontal_run))
    return {
        "dx": dx,
        "dy": dy,
        "dz": dz,
        "horizontal_run": horizontal_run,
        "length": length,
        "abs_direction_z": abs_direction,
        "slope_ratio": slope_ratio,
        "angle_from_horizontal_degrees": angle,
    }


def is_pipe_category(elem):
    try:
        return (
            elem is not None
            and elem.Category is not None
            and elem.Category.Id.IntegerValue == int(BuiltInCategory.OST_PipeCurves)
        )
    except Exception:
        return False


def is_angled_transition_pipe(pipe):
    data = get_pipe_direction_data(pipe)
    if data is None:
        return False
    angle = data.get("angle_from_horizontal_degrees")
    if angle is None:
        return False
    return (
        angle >= ANGLED_TRANSITION_MIN_DEGREES
        and angle <= ANGLED_TRANSITION_MAX_DEGREES
    )


def is_vertical_or_near_vertical(pipe):
    data = get_pipe_direction_data(pipe)
    if data is None:
        return False

    total = data.get("length")
    xy_run = data.get("horizontal_run")

    if total <= 1e-9 or xy_run <= NEAR_VERTICAL_XY_TOL_FT:
        return True

    angle = data.get("angle_from_horizontal_degrees")
    if angle is not None and angle >= VERTICAL_MIN_DEGREES:
        return True

    dir_z = data.get("abs_direction_z")
    return dir_z is not None and dir_z >= NEAR_VERTICAL_DIR_Z


def abs_direction_z(pipe):
    p0, p1 = curve_points(pipe)
    if p0 is None or p1 is None:
        return None

    dx = p1.X - p0.X
    dy = p1.Y - p0.Y
    dz = p1.Z - p0.Z
    total = (dx * dx + dy * dy + dz * dz) ** 0.5
    if total <= 1e-9:
        return None
    return abs(dz) / total


def compute_target_endpoints(pipe, target_ratio):
    p0, p1 = curve_points(pipe)
    if p0 is None or p1 is None:
        return None, None, None

    xy_run = horizontal_run_xy(pipe)
    if xy_run is None or xy_run <= 1e-9:
        return None, None, None

    delta_z = target_ratio * xy_run
    if p0.Z >= p1.Z:
        high = p0
        low = p1
        new_low = XYZ(low.X, low.Y, high.Z - delta_z)
        return high, new_low, 0
    else:
        high = p1
        low = p0
        new_low = XYZ(low.X, low.Y, high.Z - delta_z)
        return new_low, high, 1


def classify_pipe(pipe, target_slope_ratio, allow_steep=False):
    connected_owner_ids = get_connected_owner_ids(pipe)
    connected_fitting_ids = get_connected_fitting_ids(pipe)
    direction_data = get_pipe_direction_data(pipe)
    record = {
        "pipe": pipe,
        "element_id": pipe.Id.IntegerValue,
        "type_name": get_pipe_type_name(pipe),
        "system_name": get_pipe_system_name(pipe),
        "connected": len(connected_owner_ids) > 0,
        "connected_owner_ids": connected_owner_ids,
        "connected_owner_count": len(connected_owner_ids),
        "connected_with_fitting": len(connected_fitting_ids) > 0,
        "connected_fitting_ids": connected_fitting_ids,
        "connected_angled_transition_ids": [],
        "slope_ratio": None,
        "before_slope_ratio": None,
        "after_slope_ratio": None,
        "attempted_after_slope_ratio": None,
        "angle_from_horizontal_degrees": None,
        "target_slope_ratio": target_slope_ratio,
        "classification": STATUS_FAILED,
        "reason": "",
    }
    if direction_data is not None:
        record["angle_from_horizontal_degrees"] = direction_data.get(
            "angle_from_horizontal_degrees"
        )
        record["abs_direction_z"] = direction_data.get("abs_direction_z")

    try:
        if pipe.Pinned:
            record["classification"] = STATUS_SKIPPED_PINNED
            record["reason"] = "Pinned"
            return record
    except Exception:
        pass

    p0, p1 = curve_points(pipe)
    if p0 is None or p1 is None:
        record["classification"] = STATUS_SKIPPED_NO_LOCATION
        record["reason"] = "No LocationCurve"
        return record

    xy_run = horizontal_run_xy(pipe)
    if xy_run is None:
        record["classification"] = STATUS_SKIPPED_NO_LOCATION
        record["reason"] = "No measurable run"
        return record

    angle = record.get("angle_from_horizontal_degrees")
    if is_vertical_or_near_vertical(pipe):
        record["classification"] = STATUS_SKIPPED_VERTICAL
        record["reason"] = "Vertical or near-vertical"
        return record

    if xy_run < MIN_HORIZONTAL_RUN_FT:
        record["classification"] = STATUS_SKIPPED_TOO_SHORT
        record["reason"] = "Too short"
        return record

    if (
        angle is not None
        and angle >= ANGLED_TRANSITION_MIN_DEGREES
        and angle <= ANGLED_TRANSITION_MAX_DEGREES
    ):
        record["classification"] = STATUS_SKIPPED_ANGLED_TRANSITION
        record["reason"] = (
            "Angled transition / offset pipe; intentionally excluded from horizontal slope correction."
        )
        record["slope_ratio"] = achieved_slope_ratio(pipe)
        record["before_slope_ratio"] = record["slope_ratio"]
        return record

    slope_ratio = achieved_slope_ratio(pipe)
    record["slope_ratio"] = slope_ratio
    record["before_slope_ratio"] = slope_ratio

    if slope_ratio is None:
        record["classification"] = STATUS_FAILED
        record["reason"] = "Slope not measurable"
        return record

    direction_z = abs_direction_z(pipe)
    record["abs_direction_z"] = direction_z
    if (
        not allow_steep
        and (
            slope_ratio > MAX_BULK_PROCESSABLE_SLOPE_RATIO
            or (
                direction_z is not None
                and direction_z > MAX_ABS_DIRECTION_Z_FOR_HORIZONTAL
            )
        )
    ):
        record["classification"] = STATUS_SKIPPED_STEEP_PIPE
        record["reason"] = (
            "Steep/transition pipe; skipped to avoid converting non-horizontal geometry."
        )
        return record

    record["connected_angled_transition_ids"] = get_connected_angled_transition_pipe_ids(
        pipe, max_depth=1
    )

    if abs(slope_ratio - target_slope_ratio) <= SLOPE_TOL:
        record["classification"] = STATUS_OK
        record["measured_horizontal"] = True
        record["wrong_detected"] = False
        record["reason"] = "Within tolerance"
    else:
        record["classification"] = STATUS_WRONG_SLOPE
        record["measured_horizontal"] = True
        record["wrong_detected"] = True
        record["reason"] = "Outside tolerance"

    return record


def log_pipe_record(record, target_slope_ratio):
    slope_ratio = record.get("slope_ratio")
    print(
        "PIPE {} | Type={} | System={} | connected={} | connected_ids={} | connected_with_fitting={} | fitting_ids={} | connected_angled_transition_ids={} | angle_deg={} | slope_ratio={} | slope_per_1000={} | target={} | classification={}".format(
            record.get("element_id"),
            record.get("type_name") or "",
            record.get("system_name") or "",
            str(bool(record.get("connected"))).lower(),
            ",".join([str(x) for x in record.get("connected_owner_ids", [])]),
            str(bool(record.get("connected_with_fitting"))).lower(),
            ",".join([str(x) for x in record.get("connected_fitting_ids", [])]),
            ",".join([str(x) for x in record.get("connected_angled_transition_ids", [])]),
            "-" if record.get("angle_from_horizontal_degrees") is None else round(float(record.get("angle_from_horizontal_degrees")), 2),
            "-" if slope_ratio is None else round(float(slope_ratio), 6),
            ft_to_display_per_1000(slope_ratio),
            target_slope_ratio,
            record.get("classification"),
        )
    )


# =========================
# Apply methods
# =========================
def verify_target(pipe, target_slope_ratio):
    slope_ratio = achieved_slope_ratio(pipe)
    if slope_ratio is None:
        return False, None
    return abs(slope_ratio - target_slope_ratio) <= SLOPE_TOL, slope_ratio


def validate_force_apply(before_snapshot, after_snapshot, target_slope_ratio):
    pipe_id = before_snapshot.get("pipe_id")
    warnings = []

    before_ids = set(before_snapshot.get("connected_owner_ids", []))
    after_ids = set(after_snapshot.get("connected_owner_ids", []))
    if before_ids != after_ids:
        return False, "connected_owner_ids changed", warnings

    if before_snapshot.get("connected_owner_count") != after_snapshot.get("connected_owner_count"):
        return False, "connected_owner_count changed", warnings

    if before_snapshot.get("connector_count") != after_snapshot.get("connector_count"):
        return False, "connector_count changed", warnings

    before_connected = before_snapshot.get("connected_connector_count") or 0
    after_connected = after_snapshot.get("connected_connector_count") or 0
    if after_connected < before_connected:
        return False, "connected_connector_count decreased", warnings

    after_owner_snaps = after_snapshot.get("connected_owner_snapshots", {})
    before_owner_snaps = before_snapshot.get("connected_owner_snapshots", {})
    for owner_id in before_ids:
        after_owner = after_owner_snaps.get(int(owner_id))
        if after_owner is None:
            return False, "connected owner {} missing after apply".format(owner_id), warnings
        if not after_owner.get("is_valid"):
            return False, "connected owner {} invalid after apply".format(owner_id), warnings

        before_owner = before_owner_snaps.get(int(owner_id))
        if before_owner is None:
            continue
        before_location = before_owner.get("location", {})
        after_location = after_owner.get("location", {})
        if before_location.get("kind") != after_location.get("kind"):
            continue
        movement = point_list_movement_mm(
            before_location.get("points"), after_location.get("points")
        )
        if movement is None:
            continue
        if movement > FORCE_CONNECTED_OWNER_MOVE_WARN_MM:
            warnings.append(
                "connected owner {} moved {} mm".format(owner_id, round(movement, 2))
            )
        if movement > FORCE_CONNECTED_OWNER_MOVE_HARD_LIMIT_MM:
            return (
                False,
                "connected owner {} moved {} mm".format(owner_id, round(movement, 2)),
                warnings,
            )

    final_slope = after_snapshot.get("pipe_slope_ratio")
    if final_slope is None or abs(final_slope - target_slope_ratio) > SLOPE_TOL:
        return False, "final pipe slope not within tolerance ({})".format(final_slope), warnings

    return True, "topology preserved", warnings


def try_apply_via_slope_parameter(pipe, target_slope_ratio):
    param = get_slope_parameter(pipe)
    if param is None:
        return False, "Slope parameter not writable"
    try:
        param.Set(target_slope_ratio)
        doc.Regenerate()
        ok, measured = verify_target(pipe, target_slope_ratio)
        if ok:
            return True, "Slope parameter"
        return False, "Slope parameter verify failed ({})".format(measured)
    except Exception as ex:
        return False, "Slope parameter failed: {}".format(ex)


def try_apply_via_offsets(pipe, target_slope_ratio):
    start_param, end_param = get_offset_parameters(pipe)
    if start_param is None or end_param is None:
        return False, "Offset parameters not writable"

    p0, p1 = curve_points(pipe)
    if p0 is None or p1 is None:
        return False, "No endpoints"

    new_p0, new_p1, _ = compute_target_endpoints(pipe, target_slope_ratio)
    if new_p0 is None or new_p1 is None:
        return False, "Target endpoints not computable"

    try:
        start_param.Set(start_param.AsDouble() + (new_p0.Z - p0.Z))
        end_param.Set(end_param.AsDouble() + (new_p1.Z - p1.Z))
        doc.Regenerate()
        ok, measured = verify_target(pipe, target_slope_ratio)
        if ok:
            return True, "Offsets"
        return False, "Offset verify failed ({})".format(measured)
    except Exception as ex:
        return False, "Offset apply failed: {}".format(ex)


def try_apply_via_curve_rewrite(pipe, target_slope_ratio):
    loc = pipe.Location
    if not isinstance(loc, LocationCurve):
        return False, "No LocationCurve"

    new_p0, new_p1, _ = compute_target_endpoints(pipe, target_slope_ratio)
    if new_p0 is None or new_p1 is None:
        return False, "Target endpoints not computable"

    try:
        new_line = Line.CreateBound(new_p0, new_p1)
        loc.Curve = new_line
        doc.Regenerate()
        ok, measured = verify_target(pipe, target_slope_ratio)
        if ok:
            return True, "LocationCurve"
        return False, "Curve rewrite verify failed ({})".format(measured)
    except Exception as ex:
        return False, "Curve rewrite failed: {}".format(ex)


def run_apply_method(pipe, method, target_slope_ratio):
        tx = Transaction(doc, "Pipe Slope Guard - Pipe {}".format(pipe.Id.IntegerValue))
        preprocessor = GuardFailuresPreprocessor()
        try:
            tx.Start()
            set_failure_options(tx, preprocessor)
            success, message = method(pipe, target_slope_ratio)
            if success:
                tx.Commit()
                return True, "{}".format(message)
            tx.RollBack()
            if preprocessor.messages:
                message = "{} | {}".format(message, " ; ".join(preprocessor.messages))
            print(
                "PIPE {} APPLY METHOD FAILED -> {}".format(
                    pipe.Id.IntegerValue, message
                )
            )
        except Exception as ex:
            try:
                tx.RollBack()
            except Exception:
                pass
            print(
                "PIPE {} APPLY EXCEPTION -> {}".format(pipe.Id.IntegerValue, ex)
            )
            return False, "Exception: {}".format(ex)

        return False, message


def set_force_rollback_details(record, reason, target_slope_ratio):
    before_slope = record_before_slope(record)
    after_slope = record.get("attempted_after_slope_ratio")
    rollback_reason = reason
    record["rollback_connected_owner_ids"] = list(record.get("connected_owner_ids", []))
    record["rollback_fitting_ids"] = list(record.get("connected_fitting_ids", []))
    record["fitting_ids"] = list(record.get("connected_fitting_ids", []))
    record["rollback_connected_with_fitting"] = bool(record.get("connected_with_fitting"))
    record["rollback_connected_angled_transition_ids"] = list(
        record.get("connected_angled_transition_ids", [])
    )
    record["shared_rollback_owner_ids"] = []
    if (
        before_slope is not None
        and after_slope is not None
        and abs(float(before_slope) - float(after_slope)) <= SLOPE_TOL
    ):
        record["hard_constrained_no_retry"] = True
        if record.get("connected_angled_transition_ids"):
            record["rollback_constraint_subtype"] = ROLLBACK_CONSTRAINED_BY_ANGLED_TRANSITION
            rollback_reason = (
                rollback_reason
                + " Pipe is connected to or near an angled transition/offset assembly. "
                "Revit restored the slope after regeneration. Inspect/rebuild the local offset assembly."
            )
            print(
                "PIPE {} FORCE CONSTRAINT -> {}; pipe remained at {} / 1000 after regeneration.".format(
                    record.get("element_id"),
                    ROLLBACK_CONSTRAINED_BY_ANGLED_TRANSITION,
                    slope_per_1000_text(after_slope),
                )
            )
        elif bool(record.get("connected_with_fitting")) and int(record.get("connected_owner_count", 0) or 0) >= 2:
            record["rollback_constraint_subtype"] = ROLLBACK_HARD_CONSTRAINED_BY_CONNECTED_FITTINGS
            rollback_reason = (
                rollback_reason
                + " Pipe appears constrained by connected fittings/connector geometry. "
                "Revit restored the original slope after regeneration. Inspect or rebuild the local fitting assembly."
            )
            print(
                "PIPE {} FORCE CONSTRAINT -> {}; pipe remained at {} / 1000 after regeneration.".format(
                    record.get("element_id"),
                    ROLLBACK_HARD_CONSTRAINED_BY_CONNECTED_FITTINGS,
                    slope_per_1000_text(after_slope),
                )
            )

    record["force_rollback_reason"] = rollback_reason
    record["rollback_reason"] = rollback_reason

    print(
        "PIPE {} FORCE VALIDATION ROLLBACK -> before={} after={} target={} connected_ids={} fitting_ids={} connected_angled_transition_ids={} reason={}".format(
            record.get("element_id"),
            slope_per_1000_text(before_slope),
            slope_per_1000_text(after_slope),
            format_target_per_1000(target_slope_ratio),
            record_connected_owner_ids_text(record),
            record_fitting_ids_text(record),
            ",".join([str(x) for x in record.get("connected_angled_transition_ids", [])]),
            rollback_reason,
        )
    )


def run_force_curve_rewrite_validated(pipe, record, target_slope_ratio):
    pipe_id = pipe.Id.IntegerValue
    group = TransactionGroup(doc, "Pipe Slope Guard Force Validate - Pipe {}".format(pipe_id))
    try:
        group.Start()
        before_snapshot = snapshot_pipe_connectivity(pipe)
        print(
            "PIPE {} FORCE SNAPSHOT BEFORE -> connected_ids={}".format(
                pipe_id,
                ",".join([str(x) for x in before_snapshot.get("connected_owner_ids", [])]),
            )
        )

        success, message = run_apply_method(pipe, try_apply_via_curve_rewrite, target_slope_ratio)
        if not success:
            try:
                group.RollBack()
            except Exception:
                pass
            record["force_rolled_back"] = True
            set_force_rollback_details(record, message, target_slope_ratio)
            return False, message

        try:
            doc.Regenerate()
        except Exception:
            pass
        after_snapshot = snapshot_pipe_connectivity(pipe)
        record["attempted_after_slope_ratio"] = after_snapshot.get("pipe_slope_ratio")
        print(
            "PIPE {} FORCE SNAPSHOT AFTER -> connected_ids={}".format(
                pipe_id,
                ",".join([str(x) for x in after_snapshot.get("connected_owner_ids", [])]),
            )
        )
        valid, reason, warnings = validate_force_apply(
            before_snapshot, after_snapshot, target_slope_ratio
        )
        for warning in warnings:
            print(
                "PIPE {} FORCE VALIDATION WARNING -> {}".format(pipe_id, warning)
            )
        record["force_validation_warnings"] = len(warnings)

        if valid:
            group.Assimilate()
            record["force_validated_commit"] = True
            print("PIPE {} FORCE VALIDATION OK -> topology preserved".format(pipe_id))
            return True, "LocationCurve force validated"

        group.RollBack()
        record["force_rolled_back"] = True
        set_force_rollback_details(record, reason, target_slope_ratio)
        return False, reason
    except Exception as ex:
        try:
            group.RollBack()
        except Exception:
            pass
        record["force_rolled_back"] = True
        reason = "Force validation exception: {}".format(ex)
        set_force_rollback_details(record, reason, target_slope_ratio)
        return False, reason


def apply_pipe_target_slope(pipe, record, target_slope_ratio, force=False, force_pass=None):
    success, message = run_apply_method(pipe, try_apply_via_slope_parameter, target_slope_ratio)
    if success:
        return True, message

    success, message = run_apply_method(pipe, try_apply_via_offsets, target_slope_ratio)
    if success:
        return True, message

    connected = bool(record.get("connected"))
    if connected and not force:
        record["classification"] = STATUS_SKIPPED_CONNECTED_NETWORK
        record["skip_detail"] = (
            "Connected network and only curve rewrite was available; skipped in safe mode."
        )
        return False, record["skip_detail"]

    if force and connected:
        print(
            "PIPE {} FORCE PASS {} -> geometry rewrite allowed by explicit selected-only force mode.".format(
                pipe.Id.IntegerValue,
                force_pass or 1,
            )
        )

    if force:
        success, message = run_force_curve_rewrite_validated(
            pipe, record, target_slope_ratio
        )
    else:
        success, message = run_apply_method(
            pipe, try_apply_via_curve_rewrite, target_slope_ratio
        )
    if success:
        return True, message

    return False, "All apply methods failed"


def get_wrong_slope_records(records):
    return [r for r in records if r.get("classification") == STATUS_WRONG_SLOPE]


def select_wrong_slope_records(records):
    wrong_records = get_wrong_slope_records(records)
    element_ids = List[ElementId]()
    for record in wrong_records:
        try:
            element_ids.Add(ElementId(int(record.get("element_id"))))
        except Exception:
            pass

    try:
        uidoc.Selection.SetElementIds(element_ids)
        selected_count = element_ids.Count
        selected_ids = set()
        for element_id in element_ids:
            try:
                selected_ids.add(element_id.IntegerValue)
            except Exception:
                pass
        for record in wrong_records:
            if record.get("element_id") in selected_ids:
                record["selected_wrong"] = True
        print("SELECT WRONG -> selected wrong-slope pipes: {}".format(selected_count))
        return selected_count
    except Exception as ex:
        print("SELECT WRONG -> selection failed: {}".format(ex))
        return 0


def count_force_eligible_wrong_pipes(pipes, target_slope_ratio):
    count = 0
    for pipe in pipes:
        try:
            record = classify_pipe(
                pipe,
                target_slope_ratio,
                allow_steep=bool(FORCE_ALLOW_STEEP_PIPE),
            )
            if record.get("classification") == STATUS_WRONG_SLOPE:
                count += 1
        except Exception:
            pass
    return count


def build_force_preflight_counts(pipes, target_slope_ratio):
    counts = {
        "selected": len(pipes),
        "force_correctable": 0,
        "already_correct": 0,
        "skipped_vertical": 0,
        "skipped_steep_pipe": 0,
        "skipped_angled_transition": 0,
        "skipped_other": 0,
    }
    for pipe in pipes:
        try:
            record = classify_pipe(
                pipe,
                target_slope_ratio,
                allow_steep=bool(FORCE_ALLOW_STEEP_PIPE),
            )
            classification = record.get("classification")
            if classification == STATUS_WRONG_SLOPE:
                counts["force_correctable"] += 1
            elif classification == STATUS_OK:
                counts["already_correct"] += 1
            elif classification == STATUS_SKIPPED_VERTICAL:
                counts["skipped_vertical"] += 1
            elif classification == STATUS_SKIPPED_STEEP_PIPE:
                counts["skipped_steep_pipe"] += 1
            elif classification == STATUS_SKIPPED_ANGLED_TRANSITION:
                counts["skipped_angled_transition"] += 1
            elif classification in [
                STATUS_SKIPPED_TOO_SHORT,
                STATUS_SKIPPED_NO_LOCATION,
                STATUS_SKIPPED_PINNED,
            ]:
                counts["skipped_other"] += 1
        except Exception:
            counts["skipped_other"] += 1
    return counts


def get_force_rolled_back_records(records):
    return [r for r in records if r.get("force_rolled_back")]


def build_force_rollback_diagnostics(records):
    rolled_back = get_force_rolled_back_records(records)
    owner_map = {}
    for record in rolled_back:
        for owner_id in record.get("connected_owner_ids", []):
            owner_map.setdefault(int(owner_id), []).append(record)

    diagnostics = []
    constrained_suffix = (
        " Likely constrained by shared fitting/junction. Single-pipe rewrite was reverted by Revit network regeneration."
    )
    for owner_id in sorted(owner_map.keys()):
        group_records = owner_map.get(owner_id, [])
        if len(group_records) < 2:
            continue
        pipe_ids = [r.get("element_id") for r in group_records]
        print(
            "ROLLBACK GROUP -> shared connected owner {} affects pipes {}".format(
                owner_id,
                ", ".join([str(x) for x in pipe_ids]),
            )
        )
        for record in group_records:
            shared_ids = record.setdefault("shared_rollback_owner_ids", [])
            if owner_id not in shared_ids:
                shared_ids.append(owner_id)
            reason = record.get("force_rollback_reason") or record.get("rollback_reason") or ""
            if (
                record.get("rollback_constraint_subtype")
                != ROLLBACK_CONSTRAINED_BY_ANGLED_TRANSITION
                and constrained_suffix.strip() not in reason
            ):
                record["force_rollback_reason"] = reason + constrained_suffix
                record["rollback_reason"] = record["force_rollback_reason"]
        diagnostics.append(
            {
                "owner_id": owner_id,
                "records": group_records,
            }
        )
    return diagnostics


def select_force_rolled_back_records(records):
    rolled_back = get_force_rolled_back_records(records)
    if not rolled_back:
        return 0
    element_ids = List[ElementId]()
    selected_ids = set()
    for record in rolled_back:
        try:
            selected_ids.add(int(record.get("element_id")))
        except Exception:
            pass
        for owner_id in record.get("connected_owner_ids", []):
            try:
                selected_ids.add(int(owner_id))
            except Exception:
                pass
        for angled_id in record.get("connected_angled_transition_ids", []):
            try:
                selected_ids.add(int(angled_id))
            except Exception:
                pass
    for selected_id in sorted(list(selected_ids)):
        try:
            element_ids.Add(ElementId(int(selected_id)))
        except Exception:
            pass
    try:
        uidoc.Selection.SetElementIds(element_ids)
        print("FORCE ROLLBACK SELECTION -> selected rolled-back pipes and connected owners: {} elements".format(element_ids.Count))
        print("Rolled-back pipe assemblies have been selected for manual inspection.")
        return element_ids.Count
    except Exception as ex:
        print("FORCE ROLLBACK SELECTION -> failed: {}".format(ex))
        return 0


def report_status(record):
    if record.get("force_rolled_back"):
        return "FORCE_ROLLED_BACK"
    if record.get("force_corrected"):
        return "FORCE_CORRECTED"
    if record.get("corrected"):
        return "CORRECTED"
    return record.get("classification") or STATUS_FAILED


def linkify_element_id(output, element_id):
    try:
        return output.linkify(ElementId(int(element_id)))
    except Exception:
        return str(element_id)


def slope_per_1000_text(slope_ratio):
    if slope_ratio is None:
        return "-"
    return str(ft_to_display_per_1000(slope_ratio))


def record_before_slope(record):
    if record.get("before_slope_ratio") is not None:
        return record.get("before_slope_ratio")
    return record.get("slope_ratio")


def record_after_slope(record):
    if record.get("after_slope_ratio") is not None:
        return record.get("after_slope_ratio")
    if record.get("force_rolled_back"):
        return record.get("attempted_after_slope_ratio")
    return None


def record_connected_owner_ids_text(record):
    return ",".join([str(x) for x in record.get("connected_owner_ids", [])])


def record_fitting_ids_text(record):
    return ",".join([str(x) for x in record.get("connected_fitting_ids", [])])


def record_connected_angled_transition_ids_text(record):
    return ",".join([str(x) for x in record.get("connected_angled_transition_ids", [])])


def angle_degrees_text(record):
    angle = record.get("angle_from_horizontal_degrees")
    if angle is None:
        return "-"
    return str(round(float(angle), 2))


def build_summary_counts(records):
    counts = {
        "checked": len(records),
        "measured_horizontal": 0,
        "already_correct": 0,
        "wrong_detected": 0,
        "corrected": 0,
        "failed": 0,
        "skipped_vertical": 0,
        "skipped_pinned": 0,
        "skipped_too_short": 0,
        "skipped_no_location": 0,
        "skipped_connected_network": 0,
        "skipped_steep_pipe": 0,
        "skipped_angled_transition": 0,
        "force_corrected": 0,
        "force_rolled_back": 0,
        "force_validation_warnings": 0,
        "selected_wrong": 0,
        "force_large_batch_confirmed": False,
    }

    for record in records:
        classification = record.get("classification")
        if record.get("measured_horizontal"):
            counts["measured_horizontal"] += 1
        if record.get("wrong_detected"):
            counts["wrong_detected"] += 1
        if classification == STATUS_OK:
            counts["already_correct"] += 1
        elif classification == STATUS_SKIPPED_VERTICAL:
            counts["skipped_vertical"] += 1
        elif classification == STATUS_SKIPPED_PINNED:
            counts["skipped_pinned"] += 1
        elif classification == STATUS_SKIPPED_TOO_SHORT:
            counts["skipped_too_short"] += 1
        elif classification == STATUS_SKIPPED_NO_LOCATION:
            counts["skipped_no_location"] += 1
        elif classification == STATUS_SKIPPED_CONNECTED_NETWORK:
            counts["skipped_connected_network"] += 1
        elif classification == STATUS_SKIPPED_STEEP_PIPE:
            counts["skipped_steep_pipe"] += 1
        elif classification == STATUS_SKIPPED_ANGLED_TRANSITION:
            counts["skipped_angled_transition"] += 1
        elif classification == STATUS_FAILED:
            counts["failed"] += 1

        if record.get("corrected"):
            counts["corrected"] += 1
        if record.get("force_corrected"):
            counts["force_corrected"] += 1
        if record.get("force_rolled_back"):
            counts["force_rolled_back"] += 1
        if record.get("selected_wrong"):
            counts["selected_wrong"] += 1
        counts["force_validation_warnings"] += int(
            record.get("force_validation_warnings", 0) or 0
        )
        if record.get("apply_failed"):
            counts["failed"] += 1
        if record.get("force_large_batch_confirmed"):
            counts["force_large_batch_confirmed"] = True

    return counts


def report_row_values(record, target_slope_ratio, output=None):
    element_id = record.get("element_id")
    if output is not None:
        element_value = linkify_element_id(output, element_id)
    else:
        element_value = str(element_id)
    return [
        element_value,
        record.get("type_name") or "",
        record.get("system_name") or "",
        slope_per_1000_text(record_before_slope(record)),
        slope_per_1000_text(record_after_slope(record)),
        str(format_target_per_1000(target_slope_ratio)),
        angle_degrees_text(record),
        report_status(record),
        record.get("rollback_constraint_subtype") or "-",
        record_connected_angled_transition_ids_text(record),
        str(bool(record.get("connected"))).lower(),
        str(bool(record.get("connected_with_fitting"))).lower(),
        record_fitting_ids_text(record),
        record_connected_owner_ids_text(record),
    ]


def print_pipe_scan_report(records, target_slope_ratio, mode_name, scope_name):
    output = None
    if script is not None:
        try:
            output = script.get_output()
        except Exception:
            output = None

    headers = [
        "ElementId",
        "Type",
        "System",
        "Before slope /1000",
        "After slope /1000",
        "Target /1000",
        "Angle deg",
        "Status",
        "Constraint subtype",
        "Connected angled transition IDs",
        "Connected",
        "Connected with fitting",
        "Fitting IDs",
        "Connected owner IDs",
    ]
    counts = build_summary_counts(records)
    wrong_records = [r for r in records if r.get("wrong_detected")]
    corrected_records = [
        r for r in records if report_status(r) in ["CORRECTED", "FORCE_CORRECTED"]
    ]
    skipped_connected = [
        r for r in records if r.get("classification") == STATUS_SKIPPED_CONNECTED_NETWORK
    ]
    skipped_steep = [
        r for r in records if r.get("classification") == STATUS_SKIPPED_STEEP_PIPE
    ]
    skipped_angled = [
        r for r in records if r.get("classification") == STATUS_SKIPPED_ANGLED_TRANSITION
    ]
    force_rolled_back = [r for r in records if r.get("force_rolled_back")]
    rollback_diagnostics = build_force_rollback_diagnostics(records)
    is_select_mode = mode_name == "Select Wrong-Slope Pipes"
    other_skipped = [
        r
        for r in records
        if r.get("classification")
        in [
            STATUS_SKIPPED_VERTICAL,
            STATUS_SKIPPED_PINNED,
            STATUS_SKIPPED_TOO_SHORT,
            STATUS_SKIPPED_NO_LOCATION,
        ]
    ]

    def emit(text):
        if output is not None:
            try:
                output.print_md(text)
                return
            except Exception:
                pass
        print(text)

    def emit_table(table_headers, table_rows):
        emit("| {} |".format(" | ".join(table_headers)))
        emit("| {} |".format(" | ".join(["---" for _h in table_headers])))
        if table_rows:
            for row in table_rows:
                emit("| {} |".format(" | ".join([str(x) for x in row])))
        else:
            emit("| {} |".format(" | ".join(["-" for _h in table_headers])))

    def element_value(record):
        if output is not None:
            return linkify_element_id(output, record.get("element_id"))
        return str(record.get("element_id"))

    emit("## Pipe Slope Guard Result")
    emit("")
    emit("- Mode: {}".format(mode_name))
    emit("- Scope: {}".format(scope_name))
    emit("- Target slope: {} / 1000".format(format_target_per_1000(target_slope_ratio)))
    emit("- Checked: {checked}".format(**counts))
    emit("- Measured horizontal: {measured_horizontal}".format(**counts))
    emit("- Already correct: {already_correct}".format(**counts))
    emit("- Wrong slope detected: {wrong_detected}".format(**counts))
    emit("- Selected wrong-slope pipes: {selected_wrong}".format(**counts))
    if not is_select_mode:
        emit("- Corrected: {corrected}".format(**counts))
        emit("- Failed: {failed}".format(**counts))
        emit("- Skipped connected network: {skipped_connected_network}".format(**counts))
    emit("- Skipped steep pipe: {skipped_steep_pipe}".format(**counts))
    emit("- Skipped angled transition: {skipped_angled_transition}".format(**counts))
    emit("- Skipped vertical: {skipped_vertical}".format(**counts))
    emit("- Skipped pinned: {skipped_pinned}".format(**counts))
    emit("- Skipped too short: {skipped_too_short}".format(**counts))
    emit("- Skipped no location: {skipped_no_location}".format(**counts))
    if not is_select_mode:
        emit("- Force corrected: {force_corrected}".format(**counts))
        emit("- Force rolled back: {force_rolled_back}".format(**counts))
        emit("- Force validation warnings: {force_validation_warnings}".format(**counts))
        emit(
            "- Force large batch confirmed: {}".format(
                str(bool(counts.get("force_large_batch_confirmed"))).lower()
            )
        )
    emit("")

    emit("### Wrong slope before action")
    emit_table(
        [
            "ElementId",
            "Before slope /1000",
            "Target /1000",
            "Connected with fitting",
            "Fitting IDs",
            "Connected owner IDs",
        ],
        [
            [
                element_value(r),
                slope_per_1000_text(record_before_slope(r)),
                str(format_target_per_1000(target_slope_ratio)),
                str(bool(r.get("connected_with_fitting"))).lower(),
                record_fitting_ids_text(r),
                record_connected_owner_ids_text(r),
            ]
            for r in wrong_records
        ],
    )

    if is_select_mode:
        emit("")
        emit("### Full scan/action table")
        emit_table(
            headers,
            [report_row_values(record, target_slope_ratio, output) for record in records],
        )
        return

    emit("")
    emit("### Corrected pipes")
    emit_table(
        [
            "ElementId",
            "Before slope /1000",
            "After slope /1000",
            "Target /1000",
            "Status",
            "Validation warnings count",
            "Force pass",
        ],
        [
            [
                element_value(r),
                slope_per_1000_text(record_before_slope(r)),
                slope_per_1000_text(record_after_slope(r)),
                str(format_target_per_1000(target_slope_ratio)),
                report_status(r),
                str(int(r.get("force_validation_warnings", 0) or 0)),
                str(r.get("force_pass") or "-"),
            ]
            for r in corrected_records
        ],
    )

    emit("")
    emit("### Skipped connected network")
    emit_table(
        [
            "ElementId",
            "Before slope /1000",
            "Target /1000",
            "Connected with fitting",
            "Fitting IDs",
            "Reason",
        ],
        [
            [
                element_value(r),
                slope_per_1000_text(record_before_slope(r)),
                str(format_target_per_1000(target_slope_ratio)),
                str(bool(r.get("connected_with_fitting"))).lower(),
                record_fitting_ids_text(r),
                r.get("skip_detail") or r.get("reason") or "",
            ]
            for r in skipped_connected
        ],
    )

    emit("")
    emit("### Skipped steep/transition pipes")
    emit_table(
        ["ElementId", "Before slope /1000", "Target /1000", "Reason"],
        [
            [
                element_value(r),
                slope_per_1000_text(record_before_slope(r)),
                str(format_target_per_1000(target_slope_ratio)),
                r.get("reason") or "",
            ]
            for r in skipped_steep
        ],
    )

    emit("")
    emit("### Skipped angled transition pipes")
    emit_table(
        [
            "ElementId",
            "Angle from horizontal",
            "Slope /1000",
            "Connected owner IDs",
            "Reason",
        ],
        [
            [
                element_value(r),
                angle_degrees_text(r),
                slope_per_1000_text(record_before_slope(r)),
                record_connected_owner_ids_text(r),
                r.get("reason") or "",
            ]
            for r in skipped_angled
        ],
    )

    emit("")
    emit("### Other skipped pipes")
    emit_table(
        ["ElementId", "Status", "Before slope /1000", "Reason"],
        [
            [
                element_value(r),
                report_status(r),
                slope_per_1000_text(record_before_slope(r)),
                r.get("reason") or "",
            ]
            for r in other_skipped
        ],
    )

    emit("")
    emit("### Force rolled back")
    emit_table(
        [
            "ElementId",
            "Before slope /1000",
            "Attempted after slope /1000",
            "Constraint subtype",
            "Reason",
        ],
        [
            [
                element_value(r),
                slope_per_1000_text(record_before_slope(r)),
                slope_per_1000_text(record_after_slope(r)),
                r.get("rollback_constraint_subtype") or "-",
                r.get("force_rollback_reason")
                or r.get("apply_fail_reason")
                or r.get("reason")
                or "",
            ]
            for r in force_rolled_back
        ],
    )

    emit("")
    emit("### Force rollback diagnostics")
    diagnostic_rows = []
    for diagnostic in rollback_diagnostics:
        group_records = diagnostic.get("records", [])
        diagnostic_rows.append(
            [
                str(diagnostic.get("owner_id")),
                ", ".join([str(r.get("element_id")) for r in group_records]),
                ", ".join([slope_per_1000_text(record_before_slope(r)) for r in group_records]),
                ", ".join([slope_per_1000_text(record_after_slope(r)) for r in group_records]),
                ", ".join(
                    [
                        record_connected_angled_transition_ids_text(r) or "-"
                        for r in group_records
                    ]
                ),
                " | ".join(
                    [
                        r.get("force_rollback_reason")
                        or r.get("rollback_reason")
                        or r.get("apply_fail_reason")
                        or ""
                        for r in group_records
                    ]
                ),
            ]
        )
    emit_table(
        [
            "Shared owner/fitting ID",
            "Rolled-back pipe IDs",
            "Pipe slopes before /1000",
            "Attempted after /1000",
            "Connected angled transition IDs",
            "Reason",
        ],
        diagnostic_rows,
    )

    emit("")
    emit("### Rolled-back connected assembly")
    emit_table(
        [
            "Rolled-back pipe ID",
            "Connected owner IDs",
            "Fitting IDs",
            "Connected angled transition IDs",
            "Constraint subtype",
            "Recommended action",
        ],
        [
            [
                element_value(r),
                record_connected_owner_ids_text(r),
                record_fitting_ids_text(r),
                record_connected_angled_transition_ids_text(r),
                r.get("rollback_constraint_subtype") or "-",
                ROLLBACK_CONNECTED_FITTING_ACTION,
            ]
            for r in force_rolled_back
        ],
    )

    emit("")
    emit("### Full scan/action table")
    emit_table(
        headers,
        [report_row_values(record, target_slope_ratio, output) for record in records],
    )


# =========================
# Failure handling
# =========================
def summarize(records, mode_name, target_slope_ratio):
    counts = {
        "checked": len(records),
        "measured_horizontal": 0,
        "already_correct": 0,
        "wrong_detected": 0,
        "corrected": 0,
        "failed": 0,
        "skipped_vertical": 0,
        "skipped_pinned": 0,
        "skipped_too_short": 0,
        "skipped_no_location": 0,
        "skipped_connected_network": 0,
        "skipped_steep_pipe": 0,
        "skipped_angled_transition": 0,
        "force_corrected": 0,
        "force_rolled_back": 0,
        "force_validation_warnings": 0,
    }

    for record in records:
        classification = record.get("classification")
        if record.get("measured_horizontal"):
            counts["measured_horizontal"] += 1
        if record.get("wrong_detected"):
            counts["wrong_detected"] += 1
        if classification == STATUS_OK:
            counts["already_correct"] += 1
        elif classification == STATUS_SKIPPED_VERTICAL:
            counts["skipped_vertical"] += 1
        elif classification == STATUS_SKIPPED_PINNED:
            counts["skipped_pinned"] += 1
        elif classification == STATUS_SKIPPED_TOO_SHORT:
            counts["skipped_too_short"] += 1
        elif classification == STATUS_SKIPPED_NO_LOCATION:
            counts["skipped_no_location"] += 1
        elif classification == STATUS_SKIPPED_CONNECTED_NETWORK:
            counts["skipped_connected_network"] += 1
        elif classification == STATUS_SKIPPED_STEEP_PIPE:
            counts["skipped_steep_pipe"] += 1
        elif classification == STATUS_SKIPPED_ANGLED_TRANSITION:
            counts["skipped_angled_transition"] += 1
        elif classification == STATUS_FAILED:
            counts["failed"] += 1

        if record.get("corrected"):
            counts["corrected"] += 1
        if record.get("force_corrected"):
            counts["force_corrected"] += 1
        if record.get("force_rolled_back"):
            counts["force_rolled_back"] += 1
        counts["force_validation_warnings"] += int(
            record.get("force_validation_warnings", 0) or 0
        )
        if record.get("apply_failed"):
            counts["failed"] += 1

    summary = TaskDialog("Pipe Slope Guard")
    summary.MainInstruction = "Pipe Slope Guard"
    summary.MainContent = (
        "Mode: {mode}\n"
        "Target slope: {target_per_1000} / 1000\n\n"
        "Checked: {checked}\n"
        "Measured horizontal: {measured_horizontal}\n"
        "Already correct: {already_correct}\n"
        "Wrong slope detected: {wrong_detected}\n"
        "Corrected: {corrected}\n"
        "Failed: {failed}\n"
        "Skipped vertical: {skipped_vertical}\n"
        "Skipped pinned: {skipped_pinned}\n"
        "Skipped too short: {skipped_too_short}\n"
        "Skipped no location: {skipped_no_location}\n"
        "Skipped connected network: {skipped_connected_network}\n"
        "Skipped steep pipe: {skipped_steep_pipe}\n"
        "Skipped angled transition: {skipped_angled_transition}\n"
        "Force corrected: {force_corrected}\n"
        "Force rolled back: {force_rolled_back}\n"
        "Force validation warnings: {force_validation_warnings}\n\n"
        "Open pyRevit output for details."
    ).format(
        mode=mode_name,
        target_per_1000=format_target_per_1000(target_slope_ratio),
        **counts
    )
    summary.Show()


# =========================
# Main
# =========================
def apply_records_for_pass(records, mode_info, target_slope_ratio, force_pass=None):
    is_force = bool(mode_info.get("force"))
    for record in records:
        if record.get("classification") != STATUS_WRONG_SLOPE:
            continue
        if record.get("force_rolled_back") or record.get("apply_failed"):
            continue

        pipe = record["pipe"]
        if is_vertical_or_near_vertical(pipe):
            continue

        if is_force:
            print(
                "PIPE {} FORCE PASS {} -> applying target slope.".format(
                    record["element_id"],
                    force_pass or 1,
                )
            )

        success, message = apply_pipe_target_slope(
            pipe,
            record,
            target_slope_ratio,
            force=is_force,
            force_pass=force_pass,
        )
        if success:
            new_slope = achieved_slope_ratio(pipe)
            record["corrected"] = True
            if record.get("force_validated_commit"):
                record["force_corrected"] = True
                record["force_pass"] = force_pass or 1
            record["post_slope_ratio"] = new_slope
            record["after_slope_ratio"] = new_slope
            print(
                "PIPE {} CORRECTED -> slope_ratio={} slope_per_1000={}".format(
                    record["element_id"],
                    "-" if new_slope is None else round(float(new_slope), 6),
                    ft_to_display_per_1000(new_slope),
                )
            )
        else:
            if record.get("classification") == STATUS_SKIPPED_CONNECTED_NETWORK:
                print(
                    "PIPE {} DETAIL -> Connected network and only curve rewrite was available; skipped in safe mode.".format(
                        record["element_id"]
                    )
                )
                continue
            if record.get("force_rolled_back"):
                record["apply_fail_reason"] = message
                record["force_pass"] = force_pass or 1
                continue
            record["apply_failed"] = True
            record["apply_fail_reason"] = message
            print(
                "PIPE {} APPLY FAILED -> {}".format(
                    record["element_id"], message
                )
            )


def find_remaining_force_wrong_records(records, target_slope_ratio):
    remaining = []
    for record in records:
        if (
            record.get("force_rolled_back")
            or record.get("apply_failed")
            or record.get("hard_constrained_no_retry")
        ):
            continue
        try:
            recheck = classify_pipe(
                record["pipe"],
                target_slope_ratio,
                allow_steep=bool(FORCE_ALLOW_STEEP_PIPE),
            )
            if recheck.get("classification") == STATUS_WRONG_SLOPE:
                record["classification"] = STATUS_WRONG_SLOPE
                record["slope_ratio"] = recheck.get("slope_ratio")
                record["after_slope_ratio"] = recheck.get("slope_ratio")
                remaining.append(record)
        except Exception as ex:
            record["apply_failed"] = True
            record["apply_fail_reason"] = "Force recheck exception: {}".format(ex)
    return remaining


def main():
    mode_info = choose_mode()
    if mode_info is None:
        return
    target_slope_ratio = mode_info.get("target_slope_ratio", DEFAULT_TARGET_SLOPE_RATIO)

    if mode_info["scope"] == "selected":
        pipes = collect_selected_pipes()
        if not pipes:
            show_info_dialog(
                "Pipe Slope Guard",
                "No PipeCurves are selected. Select one or more pipes and run again.",
            )
            return
    elif mode_info["scope"] == "entire_model":
        pipe_count = count_entire_model_pipes()
        if mode_info["mode"] == "safe_apply":
            if not confirm_entire_model_safe_apply():
                return
        else:
            if not confirm_entire_model_scan(pipe_count):
                return
        pipes = collect_entire_model_pipes()
        if not pipes:
            show_info_dialog(
                "Pipe Slope Guard",
                "No PipeCurves were found in the model.",
            )
            return
    else:
        pipes = collect_active_view_pipes()
        if not pipes:
            show_info_dialog(
                "Pipe Slope Guard",
                "No PipeCurves were found in the active view.",
            )
            return

    if mode_info.get("force"):
        if mode_info.get("scope") != "selected":
            show_info_dialog(
                "Pipe Slope Guard",
                "Force apply is only available for selected pipes.",
            )
            return
        preflight_counts = build_force_preflight_counts(pipes, target_slope_ratio)
        force_count = preflight_counts.get("force_correctable", 0)
        print(
            "FORCE PREFLIGHT -> selected_pipecurves={} force_eligible={} soft_warning_count={}".format(
                preflight_counts.get("selected", 0),
                force_count,
                FORCE_SELECTION_SOFT_WARNING_COUNT,
            )
        )
        if force_count == 0:
            show_no_force_correctable_warning()
            return
        if force_count > FORCE_SELECTION_SOFT_WARNING_COUNT:
            if not confirm_large_force_selection(force_count):
                print("FORCE LARGE BATCH -> user cancelled; no changes made")
                return
            mode_info["force_large_batch_confirmed"] = True
            print(
                "FORCE LARGE BATCH -> user confirmed force apply for {} pipes".format(
                    force_count
                )
            )
        else:
            mode_info["force_large_batch_confirmed"] = False
            if not confirm_force_apply(preflight_counts):
                return

    print("PIPE SLOPE GUARD -> mode: {}".format(mode_info["mode"]))
    print("PIPE SLOPE GUARD -> scope: {}".format(mode_info["scope"]))
    print("PIPE SLOPE GUARD -> force: {}".format(str(bool(mode_info.get("force"))).lower()))
    print("PIPE SLOPE GUARD -> target slope ratio: {}".format(target_slope_ratio))
    print("PIPE SLOPE GUARD -> pipe count collected: {}".format(len(pipes)))

    records = []
    for pipe in pipes:
        try:
            record = classify_pipe(
                pipe,
                target_slope_ratio,
                allow_steep=bool(mode_info.get("force") and FORCE_ALLOW_STEEP_PIPE),
            )
        except Exception as ex:
            connected_owner_ids = get_connected_owner_ids(pipe)
            connected_fitting_ids = get_connected_fitting_ids(pipe)
            record = {
                "pipe": pipe,
                "element_id": pipe.Id.IntegerValue,
                "type_name": get_pipe_type_name(pipe),
                "system_name": get_pipe_system_name(pipe),
                "connected": len(connected_owner_ids) > 0,
                "connected_owner_ids": connected_owner_ids,
                "connected_owner_count": len(connected_owner_ids),
                "connected_with_fitting": len(connected_fitting_ids) > 0,
                "connected_fitting_ids": connected_fitting_ids,
                "connected_angled_transition_ids": [],
                "slope_ratio": None,
                "before_slope_ratio": None,
                "after_slope_ratio": None,
                "attempted_after_slope_ratio": None,
                "angle_from_horizontal_degrees": None,
                "target_slope_ratio": target_slope_ratio,
                "classification": STATUS_FAILED,
                "reason": "Audit exception: {}".format(ex),
            }
        if mode_info.get("force_large_batch_confirmed"):
            record["force_large_batch_confirmed"] = True

        log_pipe_record(record, target_slope_ratio)
        if record.get("reason"):
            print("PIPE {} DETAIL -> {}".format(record["element_id"], record["reason"]))
        print(
            "PIPE {} CONNECTIVITY -> connected_owner_count={} connected_owner_ids={}".format(
                record["element_id"],
                record.get("connected_owner_count", 0),
                ",".join([str(x) for x in record.get("connected_owner_ids", [])]),
            )
        )
        records.append(record)

    if mode_info["mode"] == "select_wrong":
        selected_count = select_wrong_slope_records(records)
        if (
            mode_info.get("scope") == "entire_model"
            and selected_count > FORCE_SELECTION_SOFT_WARNING_COUNT
        ):
            show_broad_entire_model_selection_warning(selected_count)

    if mode_info["mode"] in ["safe_apply", "force_apply"]:
        if mode_info.get("force"):
            original_force_records = get_wrong_slope_records(records)
            apply_records_for_pass(
                original_force_records,
                mode_info,
                target_slope_ratio,
                force_pass=1,
            )
            try:
                doc.Regenerate()
            except Exception:
                pass
            if FORCE_LOCAL_MAX_PASSES > 1:
                remaining = find_remaining_force_wrong_records(
                    original_force_records,
                    target_slope_ratio,
                )
                if remaining:
                    print(
                        "FORCE PASS 2 -> retrying remaining wrong-slope selected pipes after Revit regeneration."
                    )
                    apply_records_for_pass(
                        remaining,
                        mode_info,
                        target_slope_ratio,
                        force_pass=2,
                    )
            select_force_rolled_back_records(original_force_records)
        else:
            apply_records_for_pass(records, mode_info, target_slope_ratio)

    if mode_info["mode"] == "audit":
        summary_mode = "Audit"
    elif mode_info["mode"] == "select_wrong":
        summary_mode = "Select Wrong-Slope Pipes"
    elif mode_info.get("force"):
        summary_mode = "Force Apply"
    else:
        summary_mode = "Safe Apply"
    print_pipe_scan_report(records, target_slope_ratio, summary_mode, mode_info["scope"])


try:
    main()
except Exception as ex:
    show_info_dialog(
        "Pipe Slope Guard",
        "Unhandled error. Open pyRevit output for details.\n\n{}".format(ex),
    )
    raise

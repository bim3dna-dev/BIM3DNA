# -*- coding: utf-8 -*-
__title__ = "Create \n Print Set"
__doc__ = """Create a print set from selected views, grouped by type, with template saving/loading and preview confirmation."""

import clr

clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")

import os
import json
import re
import time
import subprocess
import System
import System.Drawing as Drawing
from Autodesk.Revit.DB import (
    Transaction,
    ElementId,
    FilteredElementCollector,
    BuiltInCategory,
    PrintRange,
    View,
    ViewSet,
    ViewSheetSet,
)

try:
    from Autodesk.Revit.DB import Viewport
except Exception:
    Viewport = None
try:
    from Autodesk.Revit.DB import ScheduleSheetInstance
except Exception:
    ScheduleSheetInstance = None

try:
    from Autodesk.Revit.DB import PDFExportOptions
except Exception:
    PDFExportOptions = None
try:
    from Autodesk.Revit.DB import PDFExportQualityType
except Exception:
    PDFExportQualityType = None
try:
    from Autodesk.Revit.DB import RasterQualityType
except Exception:
    RasterQualityType = None
try:
    from Autodesk.Revit.DB import ColorDepthType
except Exception:
    ColorDepthType = None
try:
    from Autodesk.Revit.DB import PDFExportPaperFormat
except Exception:
    PDFExportPaperFormat = None
try:
    from Autodesk.Revit.DB import PDFExportPaperOrientation
except Exception:
    PDFExportPaperOrientation = None
try:
    from Autodesk.Revit.DB import PDFExportPaperPlacement
except Exception:
    PDFExportPaperPlacement = None
try:
    from Autodesk.Revit.DB import ZoomType
except Exception:
    ZoomType = None
from Autodesk.Revit.Exceptions import FileAccessException, InvalidPathArgumentException
from System.Collections.Generic import List
from pyrevit import revit
from pyrevit import script as pyscript
import System.Windows.Forms as WinForms

try:
    import System.Drawing.Printing as Printing
except Exception:
    Printing = None
from pyrevit import forms

# Access Revit document
doc = revit.doc
uidoc = revit.uidoc
logger = pyscript.get_logger()


def _log_enum_members(enum_type, enum_name):
    try:
        if enum_type is None:
            logger.warning("{} unavailable".format(enum_name))
            return
        names = list(System.Enum.GetNames(enum_type))
        logger.info("{} members: {}".format(enum_name, ", ".join(names)))
    except Exception as ex:
        logger.warning("Could not read {} members: {}".format(enum_name, ex))


def _log_pdf_options_properties():
    try:
        if PDFExportOptions is None:
            logger.warning("PDFExportOptions unavailable")
            return
        opts = PDFExportOptions()
        props = []
        for prop in opts.GetType().GetProperties():
            try:
                if prop.CanWrite:
                    props.append(prop.Name)
            except Exception:
                pass
        logger.info(
            "Writable PDFExportOptions properties: {}".format(", ".join(sorted(props)))
        )
    except Exception as ex:
        logger.warning("Could not inspect PDFExportOptions properties: {}".format(ex))


_log_enum_members(PDFExportQualityType, "PDFExportQualityType")
_log_enum_members(RasterQualityType, "RasterQualityType")
_log_enum_members(ColorDepthType, "ColorDepthType")
_log_pdf_options_properties()


def _show_silent_dialog(msg, title, options=None):
    """Build a minimal WinForms dialog with no system sound."""
    form = WinForms.Form()
    form.Text = str(title)
    form.StartPosition = WinForms.FormStartPosition.CenterScreen
    form.FormBorderStyle = WinForms.FormBorderStyle.FixedDialog
    form.MinimizeBox = False
    form.MaximizeBox = False
    form.ShowInTaskbar = False
    form.TopMost = True
    form.AutoSize = True
    form.AutoSizeMode = WinForms.AutoSizeMode.GrowAndShrink
    form.Padding = WinForms.Padding(12)

    # Message label
    lbl = WinForms.Label()
    lbl.AutoSize = True
    lbl.MaximumSize = Drawing.Size(520, 0)  # wrap the text
    lbl.Text = str(msg)

    # Buttons panel
    btn_panel = WinForms.FlowLayoutPanel()
    btn_panel.FlowDirection = WinForms.FlowDirection.LeftToRight
    btn_panel.AutoSize = True
    btn_panel.WrapContents = True
    btn_panel.Padding = WinForms.Padding(0, 12, 0, 0)

    result_holder = {"value": None}

    def make_handler(val):
        def _handler(sender, e):
            result_holder["value"] = val
            form.DialogResult = WinForms.DialogResult.OK
            form.Close()

        return _handler

    # If options given, make one button per option; otherwise make a single OK
    opts = options if (options and len(options) > 0) else ["OK"]
    for opt in opts:
        btn = WinForms.Button()
        btn.Text = str(opt)
        btn.AutoSize = True
        btn.Margin = WinForms.Padding(6)
        btn.Click += make_handler(opt)
        btn_panel.Controls.Add(btn)
        # Choose first button as AcceptButton for Enter key
        if form.AcceptButton is None:
            form.AcceptButton = btn
        # If we see a cancel-like option, let Esc close on it
        if str(opt).lower() in ("cancel", "close", "no", "exit"):
            form.CancelButton = btn

    # Layout container
    container = WinForms.TableLayoutPanel()
    container.AutoSize = True
    container.ColumnCount = 1
    container.RowCount = 2
    container.Controls.Add(lbl, 0, 0)
    container.Controls.Add(btn_panel, 0, 1)
    form.Controls.Add(container)

    form.ShowDialog()  # no sound is played
    return result_holder["value"]


# Keep a reference to the original in case you ever need it
_original_alert = forms.alert


def _silent_alert(msg, title="pyRevit", exitscript=False, **kwargs):
    """Silent alert that matches pyRevit semantics:
    - With options: return the chosen option (never auto-exit)
    - Without options: show message and exit if exitscript=True
    """
    options = kwargs.get("options", None)
    choice = _show_silent_dialog(msg, title, options=options)
    # Do not raise SystemExit from WinForms callbacks; callers must return cleanly.
    if not options and exitscript:
        logger.warning("Alert requested script exit; returning without SystemExit.")
    return choice


forms.alert = _silent_alert
# --- end silent override ---

# Template folder
template_folder = os.path.join(os.getenv("APPDATA"), "BatchPrintSetTemplates")
if not os.path.exists(template_folder):
    os.makedirs(template_folder)


def save_template(view_names, template_name):
    save_path = os.path.join(template_folder, template_name + ".json")
    with open(save_path, "w") as f:
        json.dump({"views": view_names}, f, indent=4)


def load_template():
    templates = [f[:-5] for f in os.listdir(template_folder) if f.endswith(".json")]
    if not templates:
        forms.alert("No saved templates found.", exitscript=True)

    chosen_template = forms.SelectFromList.show(
        templates,
        title="Select Template to Load",
        multiselect=False,
    )
    if not chosen_template:
        forms.alert("No template selected. Exiting.", exitscript=True)

    with open(os.path.join(template_folder, chosen_template + ".json"), "r") as f:
        data = json.load(f)
        return data.get("views", [])


def get_template_names():
    try:
        return sorted(
            [f[:-5] for f in os.listdir(template_folder) if f.endswith(".json")]
        )
    except Exception:
        return []


def load_template_by_name(template_name):
    if not template_name:
        return []
    try:
        with open(os.path.join(template_folder, template_name + ".json"), "r") as f:
            data = json.load(f)
            return data.get("views", [])
    except Exception as ex:
        logger.warning("Could not load template {}: {}".format(template_name, ex))
    return []


ACTION_MANUAL = "Manual Selection"
ACTION_TEMPLATE = "Load Template"
ACTION_MANUAL_EXPORT = "Manual Selection + Create Print Set + Compact Color PDF"
ACTION_TEMPLATE_EXPORT = "Load Template + Create Print Set + Compact Color PDF"
ACTION_EXISTING_EXPORT = "Export Existing Print Set as Compact Color PDF"
ACTION_ADVANCED = "Advanced PDF Export Options"
ACTION_EXIT = "Exit"

ADVANCED_NATIVE = "Native Vector Diagnostic"
ADVANCED_PRINTER = "PrintManager Custom Quality Diagnostic"
ADVANCED_DETECTION = "Enable detailed detection logging"
ADVANCED_BACK = "Back"

PDF_PROFILES = [
    "Native Balanced - Color",
    "Maximum Compression - Color",
    "Maximum Compression - Black Line",
]

EXPORT_BACKEND_RECOMMENDED = "Compact PDF - Use Current Revit Print Setup (Recommended)"
EXPORT_BACKEND_NATIVE = "Native Revit PDF Export - API Controlled"
EXPORT_BACKEND_ADVANCED_PRINTER = "Advanced: Choose PDF Printer"
EXPORT_BACKEND_PRINTMANAGER_CUSTOM = "PrintManager Custom Quality - Experimental"
PRINTMANAGER_PROFILE_CURRENT = "Compact Color PDF - Current Revit Print Setup"

PROCESSING_VECTOR = "Vector preferred - preserve linework"
PROCESSING_RASTER = "Rasterize all - review only / lower zoom quality"
PRESET_BALANCED = "Lowest MB - Color Review"
PRESET_DETAIL = "Better Detail - Color Review"
PRESET_SMALLER = "Lowest MB - Black Line Review"
PRESET_BLACKLINE_VECTOR = "Compact Vector PDF24 / Current Print Setup - Diagnostic"
PRESET_EXTREME = "Native Vector Diagnostic"
PRESET_BLACKLINE = "PrintManager Custom Quality Diagnostic"
PRESET_CUSTOM = "Custom"

NATIVE_DPI_VALUES = ["DPI72", "DPI144", "DPI300", "DPI600"]
RASTER_QUALITY_VALUES = ["Low", "Medium", "High", "Presentation"]
COLOR_MODE_VALUES = ["Color", "Grayscale", "Black Line"]
ZOOM_VALUES = ["100%", "Fit to page"]
PAPER_PLACEMENT_VALUES = ["Center", "Offset from corner"]
PAPER_SIZE_AUTO = "Auto from selected sheet"
PAPER_SIZE_DEFAULT = "Use current Revit setting"
PAPER_SIZE_CUSTOM = "Custom / printer-defined"
ISO_PAPER_SIZES_MM = {
    "ISO A5": (148.0, 210.0),
    "ISO A4": (210.0, 297.0),
    "ISO A3": (297.0, 420.0),
    "ISO A2": (420.0, 594.0),
    "ISO A1": (594.0, 841.0),
    "ISO A0": (841.0, 1189.0),
    "ISO 2A0": (1189.0, 1682.0),
    "ISO 4A0": (1682.0, 2378.0),
}
ISO_PAPER_SIZE_NAMES = [
    "ISO A5",
    "ISO A4",
    "ISO A3",
    "ISO A2",
    "ISO A1",
    "ISO A0",
    "ISO 2A0",
    "ISO 4A0",
]
ORIENTATION_VALUES = ["Landscape", "Portrait", "Auto"]

QPDF_PROFILE_SAFE_LOSSLESS = "Safe Lossless"
QPDF_PROFILE_SMALLER_COLOR = "Smaller Color PDF"
COMPRESS_ENGINE_PDF24 = "PDF24"
COMPRESS_ENGINE_QPDF = "qpdf"
COMPRESS_ENGINE_NONE = "None"
PDF24_PROFILE_BALANCED = "PDF24 Balanced"
PDF24_PROFILE_SMALLER = "PDF24 Smaller"
PDF24_PROFILE_MAXIMUM = "PDF24 Maximum"
PDF24_PROFILE_EXTREME = "PDF24 Extreme"
PDF24_PROFILE_CUSTOM = "Custom"
PDF24_PROFILES = {
    PDF24_PROFILE_BALANCED: {"dpi": 144, "quality": 75, "color_model": ""},
    PDF24_PROFILE_SMALLER: {"dpi": 120, "quality": 60, "color_model": ""},
    PDF24_PROFILE_MAXIMUM: {"dpi": 96, "quality": 45, "color_model": ""},
    PDF24_PROFILE_EXTREME: {"dpi": 72, "quality": 35, "color_model": ""},
}
RASTERIZE_PRESET_REVIEW_SMALL = "Review Small JPEG"
RASTERIZE_PRESET_REVIEW_SHARP = "Review Sharp JPEG"
RASTERIZE_PRESET_REVIEW_HIGH_DETAIL = "Review High Detail JPEG"
RASTERIZE_PRESET_LOSSLESS_PNG = "Lossless PNG"
RASTERIZE_PRESET_INDEXED_PNG = "Indexed PNG"
RASTERIZE_PRESET_BW_SHARP = "Black/White Sharp"
RASTERIZE_PRESET_CUSTOM = "Custom"
RASTERIZE_PRESETS = {
    RASTERIZE_PRESET_REVIEW_SMALL: {
        "dpi": 96,
        "quality": 50,
        "color_mode": "Color",
        "image_codec": "JPEG - smallest color PDF",
        "png_compression_level": 9,
        "indexed_colors": 128,
        "threshold": 200,
    },
    RASTERIZE_PRESET_REVIEW_SHARP: {
        "dpi": 150,
        "quality": 60,
        "color_mode": "Color",
        "image_codec": "JPEG - smallest color PDF",
        "png_compression_level": 9,
        "indexed_colors": 128,
        "threshold": 200,
    },
    RASTERIZE_PRESET_REVIEW_HIGH_DETAIL: {
        "dpi": 300,
        "quality": 50,
        "color_mode": "Color",
        "image_codec": "JPEG - smallest color PDF",
        "png_compression_level": 9,
        "indexed_colors": 128,
        "threshold": 200,
    },
    RASTERIZE_PRESET_LOSSLESS_PNG: {
        "dpi": 150,
        "quality": 50,
        "color_mode": "Color",
        "image_codec": "PNG / Flate - lossless sharper lines",
        "png_compression_level": 9,
        "indexed_colors": 128,
        "threshold": 200,
    },
    RASTERIZE_PRESET_INDEXED_PNG: {
        "dpi": 150,
        "quality": 50,
        "color_mode": "Color",
        "image_codec": "PNG Indexed - reduced colors",
        "png_compression_level": 9,
        "indexed_colors": 128,
        "threshold": 200,
    },
    RASTERIZE_PRESET_BW_SHARP: {
        "dpi": 300,
        "quality": 50,
        "color_mode": "Black and white",
        "image_codec": "Black/White CCITT - sharp monochrome, smallest if color not needed",
        "png_compression_level": 9,
        "indexed_colors": 128,
        "threshold": 200,
    },
}
RASTERIZE_COLOR_MODE_MAP = {
    "Color": "color",
    "Grayscale": "grayscale",
    "Black and white": "bw",
}
RASTERIZE_CODEC_LABEL_JPEG = "JPEG - smallest color PDF"
RASTERIZE_CODEC_LABEL_PNG = "PNG / Flate - lossless sharper lines"
RASTERIZE_CODEC_LABEL_INDEXED = "PNG Indexed - reduced colors"
RASTERIZE_CODEC_LABEL_BW = "Black/White CCITT - sharp monochrome, smallest if color not needed"
RASTERIZE_CODEC_MAP = {
    RASTERIZE_CODEC_LABEL_JPEG: "jpeg",
    RASTERIZE_CODEC_LABEL_PNG: "png",
    RASTERIZE_CODEC_LABEL_INDEXED: "png-indexed",
    RASTERIZE_CODEC_LABEL_BW: "bw",
}

DEFAULT_PDF_CONFIG = {
    "pdf_optimizer_enabled": False,
    "qpdf_profile": QPDF_PROFILE_SAFE_LOSSLESS,
    "qpdf_exe_path": "",
    "post_compressor_engine": COMPRESS_ENGINE_PDF24,
    "pdf24_compressor_enabled": True,
    "pdf24_profile": PDF24_PROFILE_BALANCED,
    "pdf24_dpi": 144,
    "pdf24_image_quality": 75,
    "pdf24_color_model": "",
    "pdf24_exe_path": "",
    "pdf_rasterizer_enabled": True,
    "pdf_rasterizer_helper_path": "",
    "pdf_rasterizer_python_path": "",
    "pdf_rasterizer_default_dpi": 72,
    "pdf_rasterizer_default_quality": 45,
    "pdf_rasterizer_default_color_mode": "Color",
    "pdf_rasterizer_default_image_codec": RASTERIZE_CODEC_LABEL_JPEG,
    "pdf_rasterizer_default_png_compression_level": 9,
    "pdf_rasterizer_default_indexed_colors": 128,
    "pdf_rasterizer_default_threshold": 200,
    "last_output_folder": "",
    "last_source_mode": "manual",
    "last_export_pdf_enabled": True,
    "last_save_print_set_enabled": True,
    "last_export_engine": EXPORT_BACKEND_RECOMMENDED,
    "last_processing_mode": PROCESSING_VECTOR,
    "last_native_dpi": "DPI300",
    "last_raster_quality": "High",
    "last_color_mode": "Color",
    "last_compression_preset": PRESET_DETAIL,
    "last_zoom": "100%",
    "last_paper_placement": "Center",
    "last_paper_size": PAPER_SIZE_AUTO,
    "last_orientation": "Landscape",
    "allow_raster_profiles": False,
    "pdf_detection_timeout_seconds": 300,
}


def get_printable_views():
    all_views = (
        FilteredElementCollector(doc)
        .OfClass(View)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    available_views = []
    for view in all_views:
        if (
            not view.IsTemplate
            and view.CanBePrinted
            and not view.Name.startswith("<Revision Schedule>")
        ):
            available_views.append(view)
    return available_views


def feet_to_mm(value):
    try:
        return float(value) * 304.8
    except Exception:
        return 0.0


def normalize_enum_name(value):
    return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()


def get_fuzzy_enum_member(enum_type, desired_names):
    if enum_type is None:
        return None
    try:
        available = list(System.Enum.GetNames(enum_type))
    except Exception:
        available = []
    desired = []
    for name in desired_names or []:
        if name:
            desired.append(str(name))
            cleaned = str(name).replace("ISO ", "").replace("ISO_", "")
            desired.append(cleaned)
            desired.append(cleaned.replace(" ", ""))
    for desired_name in desired:
        desired_key = normalize_enum_name(desired_name)
        for available_name in available:
            if normalize_enum_name(available_name) == desired_key:
                try:
                    return System.Enum.Parse(enum_type, available_name)
                except Exception:
                    pass
    for desired_name in desired:
        desired_key = normalize_enum_name(desired_name)
        for available_name in available:
            available_key = normalize_enum_name(available_name)
            if desired_key and (
                desired_key in available_key or available_key in desired_key
            ):
                try:
                    return System.Enum.Parse(enum_type, available_name)
                except Exception:
                    pass
    if desired_names:
        logger.warning(
            "Enum mapping failed for {}. Requested: {}. Available: {}".format(
                enum_type,
                ", ".join([str(x) for x in desired_names]),
                ", ".join(available),
            )
        )
    return None


def get_view_type_name(view):
    try:
        return str(view.ViewType)
    except Exception:
        return ""


def is_sheet_view(view):
    return get_view_type_name(view).lower() == "drawingsheet"


def match_iso_paper_size(width_mm, height_mm):
    if not width_mm or not height_mm:
        return None, None
    best_name = None
    best_delta = None
    w = float(width_mm)
    h = float(height_mm)
    for name, dims in ISO_PAPER_SIZES_MM.items():
        a = dims[0]
        b = dims[1]
        delta1 = abs(w - a) + abs(h - b)
        delta2 = abs(w - b) + abs(h - a)
        delta = min(delta1, delta2)
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best_name = name
    return best_name, best_delta


def get_sheet_size_info(view_sheet):
    info = {
        "width_mm": None,
        "height_mm": None,
        "nearest_iso_name": "",
        "orientation": "",
        "confidence": "unknown",
        "reason": "No titleblock bounding box found.",
    }
    try:
        titleblocks = (
            FilteredElementCollector(doc, view_sheet.Id)
            .OfCategory(BuiltInCategory.OST_TitleBlocks)
            .WhereElementIsNotElementType()
            .ToElements()
        )
    except Exception as ex:
        info["reason"] = "Titleblock collection failed: {}".format(ex)
        return info
    for titleblock in titleblocks:
        try:
            bbox = titleblock.get_BoundingBox(view_sheet)
            if bbox is None:
                bbox = titleblock.get_BoundingBox(None)
            if bbox is None:
                continue
            width_mm = feet_to_mm(abs(bbox.Max.X - bbox.Min.X))
            height_mm = feet_to_mm(abs(bbox.Max.Y - bbox.Min.Y))
            if width_mm <= 1.0 or height_mm <= 1.0:
                continue
            iso_name, delta = match_iso_paper_size(width_mm, height_mm)
            info["width_mm"] = width_mm
            info["height_mm"] = height_mm
            info["nearest_iso_name"] = iso_name or ""
            info["orientation"] = "Landscape" if width_mm >= height_mm else "Portrait"
            info["confidence"] = (
                "high" if delta is not None and delta <= 80.0 else "medium"
            )
            info["reason"] = "Detected from titleblock {}".format(
                titleblock.Id.IntegerValue
            )
            logger.info(
                "SHEET SIZE -> sheet={} width_mm={:.1f} height_mm={:.1f} iso={} confidence={}".format(
                    view_sheet.Id.IntegerValue,
                    width_mm,
                    height_mm,
                    info["nearest_iso_name"],
                    info["confidence"],
                )
            )
            return info
        except Exception:
            pass
    return info


def get_bbox_rect_mm_from_bbox(bbox):
    try:
        return {
            "x1": feet_to_mm(bbox.Min.X),
            "y1": feet_to_mm(bbox.Min.Y),
            "x2": feet_to_mm(bbox.Max.X),
            "y2": feet_to_mm(bbox.Max.Y),
        }
    except Exception:
        return None


def get_outline_rect_mm(outline):
    try:
        min_pt = outline.MinimumPoint
        max_pt = outline.MaximumPoint
    except Exception:
        try:
            min_pt = outline.GetMinimumPoint()
            max_pt = outline.GetMaximumPoint()
        except Exception:
            return None
    try:
        return {
            "x1": feet_to_mm(min_pt.X),
            "y1": feet_to_mm(min_pt.Y),
            "x2": feet_to_mm(max_pt.X),
            "y2": feet_to_mm(max_pt.Y),
        }
    except Exception:
        return None


def get_sheet_layout_info(view_sheet):
    info = {
        "sheet": None,
        "titleblock_rect": None,
        "viewport_rects": [],
        "schedule_rects": [],
        "status": "Unknown sheet bounds",
    }
    if view_sheet is None or not is_sheet_view(view_sheet):
        return info
    info["sheet"] = view_sheet
    try:
        titleblocks = (
            FilteredElementCollector(doc, view_sheet.Id)
            .OfCategory(BuiltInCategory.OST_TitleBlocks)
            .WhereElementIsNotElementType()
            .ToElements()
        )
        for titleblock in titleblocks:
            try:
                bbox = titleblock.get_BoundingBox(view_sheet)
                if bbox is None:
                    bbox = titleblock.get_BoundingBox(None)
                rect = get_bbox_rect_mm_from_bbox(bbox)
                if rect:
                    info["titleblock_rect"] = rect
                    break
            except Exception:
                pass
    except Exception:
        pass
    if Viewport is not None:
        try:
            viewports = (
                FilteredElementCollector(doc, view_sheet.Id)
                .OfClass(Viewport)
                .WhereElementIsNotElementType()
                .ToElements()
            )
            for viewport in viewports:
                try:
                    rect = get_outline_rect_mm(viewport.GetBoxOutline())
                    if rect:
                        info["viewport_rects"].append(rect)
                except Exception:
                    pass
        except Exception:
            pass
    if ScheduleSheetInstance is not None:
        try:
            schedules = (
                FilteredElementCollector(doc, view_sheet.Id)
                .OfClass(ScheduleSheetInstance)
                .WhereElementIsNotElementType()
                .ToElements()
            )
            for schedule in schedules:
                try:
                    bbox = schedule.get_BoundingBox(view_sheet)
                    rect = get_bbox_rect_mm_from_bbox(bbox)
                    if rect:
                        info["schedule_rects"].append(rect)
                except Exception:
                    pass
        except Exception:
            pass
    if info["titleblock_rect"]:
        info["status"] = "OK"
    return info


def get_sheet_size_context(selected_views):
    sheet_infos = []
    for view in selected_views or []:
        if is_sheet_view(view):
            info = get_sheet_size_info(view)
            if info.get("width_mm") and info.get("height_mm"):
                sheet_infos.append(info)
    mixed = False
    if sheet_infos:
        first_iso = sheet_infos[0].get("nearest_iso_name")
        for info in sheet_infos:
            if info.get("nearest_iso_name") != first_iso:
                mixed = True
                break
    return {
        "first": sheet_infos[0] if sheet_infos else None,
        "mixed": mixed,
        "count": len(sheet_infos),
    }


def get_printer_paper_size_names():
    names = []
    try:
        paper_sizes = doc.PrintManager.PaperSizes
        for paper_size in paper_sizes:
            try:
                name = str(paper_size.Name)
                if name and name not in names:
                    names.append(name)
            except Exception:
                pass
    except Exception as ex:
        logger.warning("Printer paper sizes unavailable: {}".format(ex))
    return names


def resolve_requested_paper_size_name(paper_size, sheet_context):
    if paper_size == PAPER_SIZE_AUTO:
        first = (sheet_context or {}).get("first")
        if first and first.get("nearest_iso_name"):
            return first.get("nearest_iso_name")
        return PAPER_SIZE_DEFAULT
    return paper_size


def resolve_orientation_value(orientation, sheet_context):
    if orientation != "Auto":
        return orientation, ""
    first = (sheet_context or {}).get("first")
    if (
        first
        and first.get("orientation")
        and first.get("confidence") in ("high", "medium")
    ):
        return first.get("orientation"), ""
    return "Landscape", "Auto orientation uncertain; using Landscape."


def build_grouped_view_lookup(available_views):
    grouped_view_names = []
    view_lookup = {}
    for view in sorted(available_views, key=lambda v: (v.ViewType.ToString(), v.Name)):
        view_type = view.ViewType.ToString().upper()
        grouped_name = "[{}] {}".format(view_type, view.Name)
        grouped_view_names.append(grouped_name)
        view_lookup[grouped_name] = view
    return grouped_view_names, view_lookup


def collect_views_manual(available_views):
    grouped_view_names, view_lookup = build_grouped_view_lookup(available_views)
    selected_grouped_names = forms.SelectFromList.show(
        grouped_view_names,
        title="Select Views for Print Set",
        multiselect=True,
    )
    if not selected_grouped_names:
        forms.alert("No views selected. Print set creation cancelled.", exitscript=True)
        pyscript.exit()

    selected_views = []
    selected_view_names = set()
    for grouped_name in selected_grouped_names:
        view = view_lookup.get(grouped_name)
        if view:
            if view.Name in selected_view_names:
                forms.alert(
                    "Duplicate view detected: '{}'. Exiting.".format(view.Name),
                    exitscript=True,
                )
            selected_view_names.add(view.Name)
            selected_views.append(view)

    save_now = forms.alert(
        "Do you want to save this selection as a Template?",
        options=["Yes", "No"],
        exitscript=False,
    )
    if save_now == "Yes":
        template_name = forms.ask_for_string(
            prompt="Enter a name for the template:",
            title="Save Template",
            default="NewTemplate",
        )
        if template_name:
            save_template([v.Name for v in selected_views], template_name)
            forms.alert("Template '{}' saved successfully.".format(template_name))
    return selected_views


def collect_views_from_template(available_views):
    template_view_names = load_template()
    selected_views = []
    missing_views = []
    available_view_names = {v.Name: v for v in available_views}
    for name in template_view_names:
        view = available_view_names.get(name)
        if view:
            selected_views.append(view)
        else:
            missing_views.append(name)
    if not selected_views:
        forms.alert(
            "No matching views found in this project. Exiting.", exitscript=True
        )
    if missing_views:
        forms.alert(
            "Some views were not found and skipped:\n\n{}".format(
                "\n".join(missing_views)
            )
        )
    return selected_views


def ask_print_set_name():
    print_set_name = forms.ask_for_string(
        prompt="Enter a name for the print set (default is 'ViewPrintSet').",
        title="Print Set Name",
        default="ViewPrintSet",
    )
    if not print_set_name:
        forms.alert("Print set creation cancelled. No name provided.", exitscript=True)
    return print_set_name


def confirm_print_set(print_set_name, selected_views):
    preview_list = ["- {}".format(view.Name) for view in selected_views]
    proceed = forms.alert(
        "You are about to create Print Set '{}'\n\nThe following views will be included:\n\n{}\n\nProceed?".format(
            print_set_name, "\n".join(preview_list)
        ),
        options=["Proceed", "Cancel"],
        exitscript=False,
    )
    if proceed != "Proceed":
        pyscript.exit()


def create_print_set(print_set_name, selected_views, notify=True):
    print_manager = doc.PrintManager
    print_manager.PrintRange = PrintRange.Select
    view_sheet_setting = print_manager.ViewSheetSetting

    view_set = ViewSet()
    for view in selected_views:
        view_set.Insert(view)

    existing_print_sets = {
        vss.Name: vss
        for vss in FilteredElementCollector(doc)
        .OfClass(ViewSheetSet)
        .WhereElementIsNotElementType()
        .ToElements()
    }

    with Transaction(doc, "Create Print Set") as transaction:
        transaction.Start()
        if print_set_name in existing_print_sets:
            view_sheet_setting.CurrentViewSheetSet = existing_print_sets[print_set_name]
            view_sheet_setting.Delete()
            logger.info("Deleted existing print set: " + print_set_name)
        view_sheet_setting.CurrentViewSheetSet.Views = view_set
        view_sheet_setting.SaveAs(print_set_name)
        transaction.Commit()
    if notify:
        forms.alert("Print set '{}' created successfully.".format(print_set_name))
    try:
        for vss in get_existing_print_sets():
            if vss.Name == print_set_name:
                return vss
    except Exception:
        pass
    return None


def get_view_count_from_print_set(print_set):
    if print_set is None:
        return 0
    try:
        count = 0
        for view in print_set.Views:
            count += 1
        return count
    except Exception:
        return 0


def ensure_printmanager_view_sheet_set(
    selected_views, requested_name, save_permanent, existing_print_set=None
):
    if existing_print_set is not None:
        count = get_view_count_from_print_set(existing_print_set)
        logger.info(
            "PRINT SET SOURCE -> existing set name={}, view count={}".format(
                existing_print_set.Name, count
            )
        )
        if count <= 0:
            return None
        return existing_print_set
    if not selected_views:
        return None
    set_name = requested_name if save_permanent else "__BIM3DNA_TEMP_PDF_EXPORT__"
    print_set = create_print_set(set_name, selected_views, notify=False)
    if print_set is not None:
        logger.info(
            "PRINT SET ASSIGNED -> name={}, view count={}".format(
                print_set.Name, get_view_count_from_print_set(print_set)
            )
        )
    return print_set


def get_existing_print_sets():
    sets = []
    for vss in (
        FilteredElementCollector(doc)
        .OfClass(ViewSheetSet)
        .WhereElementIsNotElementType()
        .ToElements()
    ):
        try:
            sets.append(vss)
        except Exception:
            continue
    return sorted(sets, key=lambda x: x.Name)


def choose_existing_print_set():
    print_sets = get_existing_print_sets()
    if not print_sets:
        forms.alert("No saved print sets found.", exitscript=True)
    names = [ps.Name for ps in print_sets]
    chosen = forms.SelectFromList.show(
        names,
        title="Select Existing Print Set",
        multiselect=False,
    )
    if not chosen:
        forms.alert("No print set selected. Exiting.", exitscript=True)
    lookup = {ps.Name: ps for ps in print_sets}
    return lookup.get(chosen)


def extract_views_from_print_set(view_sheet_set):
    views = []
    order_warning = False
    try:
        for view in view_sheet_set.Views:
            views.append(view)
        order_warning = True
    except Exception:
        forms.alert("Could not read views from selected print set.", exitscript=True)
    if order_warning:
        forms.alert(
            "Warning: Revit does not always expose saved print set order reliably. Export will use the order returned by the API."
        )
    return views


def sanitize_filename(value):
    clean = re.sub(r'[\\/:*?"<>|]+', "_", value or "ViewPrintSet")
    clean = clean.strip().strip(".")
    return clean or "ViewPrintSet"


def get_unique_pdf_path(path):
    if not os.path.exists(path):
        return path
    folder = os.path.dirname(path)
    base = os.path.splitext(os.path.basename(path))[0]
    ext = os.path.splitext(path)[1] or ".pdf"
    index = 1
    while index < 1000:
        candidate = os.path.join(folder, "{}_{:03d}{}".format(base, index, ext))
        if not os.path.exists(candidate):
            return candidate
        index += 1
    return os.path.join(folder, "{}_999{}".format(base, ext))


def get_enum_member(enum_type, member_names):
    if enum_type is None:
        return None
    for name in member_names:
        try:
            return getattr(enum_type, name)
        except Exception:
            continue
    logger.warning("PDF enum member unavailable: {}".format(", ".join(member_names)))
    return None


_MISSING_PDF_OPTIONS_WARNED = set()
_MISSING_PRINT_OPTIONS_WARNED = set()


def warn_once(cache, key, message):
    if key in cache:
        return
    cache.add(key)
    logger.warning(message)


def safe_set_pdf_option(options, prop_name, value):
    if value is None:
        return False
    try:
        setattr(options, prop_name, value)
        return True
    except Exception as ex:
        warn_once(
            _MISSING_PDF_OPTIONS_WARNED,
            prop_name,
            "PDF option {} unavailable: {}".format(prop_name, ex),
        )
        return False


def get_enum_type_name(enum_type):
    try:
        return enum_type.__name__
    except Exception:
        pass
    try:
        return enum_type.Name
    except Exception:
        pass
    return str(enum_type)


def set_pdf_enum_option(options, prop_name, enum_type, member_name):
    if enum_type is None:
        logger.warning(
            "PDF enum option failed: {} enum type unavailable".format(prop_name)
        )
        return False
    if not hasattr(options, prop_name):
        warn_once(
            _MISSING_PDF_OPTIONS_WARNED,
            prop_name,
            "PDF option unavailable: {}".format(prop_name),
        )
        return False
    enum_type_name = get_enum_type_name(enum_type)
    try:
        enum_value = System.Enum.Parse(enum_type, member_name)
        setattr(options, prop_name, enum_value)
        logger.info(
            "PDF option set: {} = {}.{}".format(prop_name, enum_type_name, member_name)
        )
        return True
    except Exception as ex:
        logger.warning(
            "PDF enum option failed: {} expected {}, member {}, error: {}".format(
                prop_name, enum_type_name, member_name, ex
            )
        )
        return False


def get_pdf_option_value(options, prop_name):
    try:
        return getattr(options, prop_name)
    except Exception:
        return "<unavailable>"


def log_applied_pdf_options(options):
    names = [
        "AlwaysUseRaster",
        "ExportQuality",
        "RasterQuality",
        "ColorDepth",
        "Combine",
        "ReplaceHalftoneWithThinLines",
    ]
    for name in names:
        logger.info(
            "PDF option applied value: {} = {}".format(
                name, get_pdf_option_value(options, name)
            )
        )


def get_color_depth_member(color_mode):
    if color_mode == "Grayscale":
        return "GrayScale"
    if color_mode == "Black Line":
        return "BlackLine"
    return "Color"


def get_orientation_member(orientation):
    if orientation == "Portrait":
        return "Portrait"
    if orientation == "Landscape":
        return "Landscape"
    return "Auto"


def get_paper_placement_member(placement):
    if placement == "Offset from corner":
        return "Margin"
    return "Center"


def build_pdf_export_options(
    profile_name, output_file_name, combine=True, export_settings=None
):
    if PDFExportOptions is None:
        forms.alert(
            "Native PDF export is not available in this Revit API version.",
            exitscript=True,
        )
    options = PDFExportOptions()
    safe_set_pdf_option(options, "Combine", combine)
    safe_set_pdf_option(options, "FileName", output_file_name)
    export_settings = export_settings or {}
    processing_mode = export_settings.get("processing_mode") or PROCESSING_VECTOR
    native_dpi = export_settings.get("native_dpi") or "DPI300"
    raster_quality = export_settings.get("raster_quality") or "Medium"
    color_mode = export_settings.get("color_mode") or "Color"
    zoom_value = export_settings.get("zoom") or "100%"
    paper_placement = export_settings.get("paper_placement") or "Center"
    paper_size = export_settings.get("paper_size") or PAPER_SIZE_AUTO
    sheet_context = export_settings.get("sheet_size_context") or {}
    resolved_paper_size = resolve_requested_paper_size_name(paper_size, sheet_context)
    orientation = export_settings.get("orientation") or "Landscape"
    resolved_orientation, orientation_warning = resolve_orientation_value(
        orientation, sheet_context
    )
    if orientation_warning:
        logger.warning(orientation_warning)

    safe_set_pdf_option(
        options, "AlwaysUseRaster", processing_mode == PROCESSING_RASTER
    )
    safe_set_pdf_option(options, "StopOnError", False)
    safe_set_pdf_option(options, "HideCropBoundaries", True)
    safe_set_pdf_option(options, "HideReferencePlane", True)
    safe_set_pdf_option(options, "HideScopeBoxes", True)
    safe_set_pdf_option(options, "HideUnreferencedViewTags", True)
    safe_set_pdf_option(options, "MaskCoincidentLines", True)
    safe_set_pdf_option(options, "ReplaceHalftoneWithThinLines", True)
    safe_set_pdf_option(options, "ViewLinksInBlue", False)

    set_pdf_enum_option(options, "ExportQuality", PDFExportQualityType, native_dpi)
    set_pdf_enum_option(options, "RasterQuality", RasterQualityType, raster_quality)
    set_pdf_enum_option(
        options, "ColorDepth", ColorDepthType, get_color_depth_member(color_mode)
    )
    logger.info("Native PDF profile applied: {}".format(profile_name))
    logger.info("Native PDF processing mode: {}".format(processing_mode))
    logger.info("Native PDF DPI: {}".format(native_dpi))
    logger.info("Native PDF raster quality: {}".format(raster_quality))
    logger.info("Native PDF color mode: {}".format(color_mode))
    if resolved_paper_size and resolved_paper_size not in [
        PAPER_SIZE_DEFAULT,
        PAPER_SIZE_CUSTOM,
    ]:
        paper_format = get_fuzzy_enum_member(
            PDFExportPaperFormat,
            [
                resolved_paper_size,
                resolved_paper_size.replace("ISO ", ""),
                resolved_paper_size.replace(" ", ""),
                resolved_paper_size.replace("ISO ", "ISO_"),
            ],
        )
        logger.info(
            "Native PDF paper size requested: {} resolved: {}".format(
                paper_size, resolved_paper_size
            )
        )
        safe_set_pdf_option(
            options,
            "PaperFormat",
            paper_format,
        )
    safe_set_pdf_option(
        options,
        "PaperOrientation",
        get_enum_member(
            PDFExportPaperOrientation,
            [get_orientation_member(resolved_orientation), "Landscape"],
        ),
    )
    safe_set_pdf_option(
        options,
        "PaperPlacement",
        get_enum_member(
            PDFExportPaperPlacement,
            [get_paper_placement_member(paper_placement), "Center", "Centered"],
        ),
    )
    if zoom_value == "Fit to page":
        safe_set_pdf_option(
            options, "ZoomType", get_enum_member(ZoomType, ["FitToPage"])
        )
    else:
        safe_set_pdf_option(options, "ZoomType", get_enum_member(ZoomType, ["Zoom"]))
        safe_set_pdf_option(options, "ZoomPercentage", 100)
    log_applied_pdf_options(options)
    return options


def choose_pdf_profile():
    forms.alert(
        "Native Revit PDF Export produced larger PDFs in BIM3DNA testing.\n\n"
        "For compact files, use Compact PDF - Current Revit Print Setup."
    )
    profile = forms.SelectFromList.show(
        PDF_PROFILES,
        title="Select PDF Export Profile",
        multiselect=False,
    )
    if not profile:
        forms.alert("No PDF profile selected. Export cancelled.", exitscript=True)
    logger.info("selected profile: {}".format(profile))
    return profile


def choose_output_folder():
    desktop = System.Environment.GetFolderPath(System.Environment.SpecialFolder.Desktop)
    default_folder = os.path.join(desktop, "BIM3DNA_PDF_Exports")
    dlg = WinForms.FolderBrowserDialog()
    dlg.Description = "Choose PDF output folder"
    dlg.SelectedPath = default_folder
    if dlg.ShowDialog() != WinForms.DialogResult.OK:
        forms.alert("No output folder selected. Export cancelled.", exitscript=True)
    output_folder = dlg.SelectedPath or default_folder
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
    test_path = os.path.join(output_folder, "_bim3dna_write_test.tmp")
    try:
        with open(test_path, "w") as f:
            f.write("test")
        os.remove(test_path)
    except Exception as ex:
        forms.alert(
            "Output folder is not writable:\n{}\n\n{}".format(output_folder, ex),
            exitscript=True,
        )
    logger.info("output folder: {}".format(output_folder))
    return output_folder


def build_view_id_list(selected_views):
    view_ids = List[ElementId]()
    seen = set()
    for view in selected_views:
        try:
            if view.Id.IntegerValue in seen:
                raise Exception("Duplicate view id {}".format(view.Id.IntegerValue))
            seen.add(view.Id.IntegerValue)
            view_ids.Add(view.Id)
        except Exception as ex:
            raise Exception(
                "Invalid view for PDF export: {} ({})".format(view.Name, ex)
            )
    return view_ids


def get_file_size_mb(path):
    try:
        if os.path.exists(path):
            return os.path.getsize(path) / (1024.0 * 1024.0)
    except Exception:
        pass
    return None


def snapshot_pdf_files(folder, log_before=False):
    snap = {}
    if not folder or not os.path.isdir(folder):
        return snap
    try:
        for name in os.listdir(folder):
            if not name.lower().endswith(".pdf"):
                continue
            path = os.path.join(folder, name)
            try:
                mtime = os.path.getmtime(path)
                size = os.path.getsize(path)
                snap[path] = (mtime, size)
                if log_before:
                    logger.info(
                        "DETECT BEFORE -> path={} size={} utc={}".format(
                            path, size, mtime
                        )
                    )
            except Exception:
                pass
    except Exception:
        pass
    return snap


def get_revit_project_folder():
    try:
        path = doc.PathName
        if path:
            folder = os.path.dirname(path)
            if folder and os.path.isdir(folder):
                return folder
    except Exception:
        pass
    return None


def get_pdf_detection_folders(output_folder):
    folders = []
    for folder in [
        output_folder,
        System.Environment.GetFolderPath(System.Environment.SpecialFolder.Desktop),
        System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments),
        get_revit_project_folder(),
    ]:
        if folder and os.path.isdir(folder) and folder not in folders:
            folders.append(folder)
    return folders


def snapshot_pdf_folders(folders, log_before=False):
    snap = {}
    for folder in folders:
        snap.update(snapshot_pdf_files(folder, log_before=log_before))
    return snap


def detect_generated_pdfs(before, after, expected_path):
    generated = []
    if expected_path and os.path.exists(expected_path):
        generated.append(expected_path)
    for path, data in after.items():
        if path not in before or before.get(path) != data:
            if path not in generated:
                generated.append(path)
    return generated


def get_pdf_detection_candidates(before, folders, expected_path, start_time):
    candidates = []
    seen = set()
    if expected_path and os.path.exists(expected_path):
        try:
            size = os.path.getsize(expected_path)
            mtime = os.path.getmtime(expected_path)
            if size > 0:
                candidates.append(
                    (expected_path, mtime, size, "exact requested path exists")
                )
                seen.add(expected_path)
        except Exception:
            pass
    after = snapshot_pdf_folders(folders, log_before=False)
    for path, data in after.items():
        if path in seen:
            continue
        mtime = data[0]
        size = data[1]
        if size <= 0:
            continue
        reason = None
        if path not in before:
            reason = "new PDF"
        else:
            old = before.get(path)
            if old[1] != size:
                reason = "size changed"
            elif old[0] != mtime:
                reason = "modified time changed"
        if reason is None and mtime >= start_time - 300.0:
            reason = "recent PDF in polling window"
        if reason:
            candidates.append((path, mtime, size, reason))
            seen.add(path)
    candidates.sort(
        key=lambda item: (
            0 if item[3] == "exact requested path exists" else 1,
            -item[1],
        )
    )
    for path, mtime, size, reason in candidates:
        logger.info(
            "DETECT CANDIDATE -> path={} size={} utc={} reason={}".format(
                path, size, mtime, reason
            )
        )
    return candidates


def is_stable_detection_candidate(path, previous_size):
    try:
        if not path or not os.path.exists(path):
            return False, None
        size = os.path.getsize(path)
        if size <= 0:
            return False, size
        return previous_size == size, size
    except Exception:
        return False, None


def wait_for_generated_pdfs(before, expected_path, timeout_seconds=10.0, folders=None):
    start = time.time()
    if folders is None:
        folder = os.path.dirname(expected_path) if expected_path else None
        folders = [folder] if folder else []
    stable_count = 0
    last_size = None
    while folders and (time.time() - start) < timeout_seconds:
        after = snapshot_pdf_folders(folders)
        generated = detect_generated_pdfs(before, after, expected_path)
        if generated:
            try:
                current_size = (
                    os.path.getsize(expected_path)
                    if os.path.exists(expected_path)
                    else None
                )
            except Exception:
                current_size = None
            if current_size is None:
                return after, generated
            if last_size == current_size:
                stable_count += 1
            else:
                stable_count = 0
                last_size = current_size
            if stable_count >= 2:
                return after, generated
        time.sleep(2.0)
    after = snapshot_pdf_folders(folders) if folders else {}
    return after, detect_generated_pdfs(before, after, expected_path)


def is_pdf_file_stable(path, previous_size, start_time=None):
    try:
        if not path or not os.path.exists(path):
            return False, None
        current_size = os.path.getsize(path)
        if current_size <= 0:
            return False, current_size
        if start_time is not None:
            try:
                mtime = os.path.getmtime(path)
                if mtime < start_time - 60.0:
                    return False, current_size
            except Exception:
                pass
        return previous_size == current_size, current_size
    except Exception:
        return False, None


def newest_recent_pdf(folders, start_time):
    newest_path = None
    newest_mtime = None
    threshold = start_time - 60.0
    for folder in folders:
        if not folder or not os.path.isdir(folder):
            continue
        try:
            for name in os.listdir(folder):
                if not name.lower().endswith(".pdf"):
                    continue
                path = os.path.join(folder, name)
                try:
                    size = os.path.getsize(path)
                    mtime = os.path.getmtime(path)
                    if size <= 0 or mtime < threshold:
                        continue
                    logger.info(
                        "DETECT PDF CANDIDATE -> path={} modified={} size_mb={} reason=modified during export window".format(
                            path,
                            mtime,
                            (
                                "{:.2f}".format(size / (1024.0 * 1024.0))
                                if size is not None
                                else "unknown"
                            ),
                        )
                    )
                    if newest_mtime is None or mtime > newest_mtime:
                        newest_mtime = mtime
                        newest_path = path
                except Exception:
                    pass
        except Exception:
            pass
    return newest_path


def detect_generated_pdf(
    expected_path,
    output_folder,
    searched_folders,
    start_time,
    timeout_seconds,
    before_snapshot=None,
):
    stable_sizes = {}
    start = time.time()
    before_snapshot = before_snapshot or {}
    while time.time() - start < timeout_seconds:
        logger.info("PDF detection poll. Expected path: {}".format(expected_path))
        folders = []
        if output_folder:
            folders.append(output_folder)
        for folder in searched_folders or []:
            if folder and folder not in folders:
                folders.append(folder)
        candidates = get_pdf_detection_candidates(
            before_snapshot, folders, expected_path, start_time
        )
        for candidate, mtime, size_value, reason in candidates:
            stable, size = is_stable_detection_candidate(
                candidate, stable_sizes.get(candidate)
            )
            stable_sizes[candidate] = size
            if stable:
                logger.info(
                    "DETECT ACCEPTED -> path={} reason={}".format(candidate, reason)
                )
                return candidate
        time.sleep(2.0)
    return None


def format_pdf_outputs(paths):
    if not paths:
        return "No generated PDF file detected."
    lines = []
    for path in paths:
        size_mb = get_file_size_mb(path)
        lines.append(
            "{} ({})".format(
                path,
                (
                    "{:.2f} MB".format(size_mb)
                    if size_mb is not None
                    else "size unavailable"
                ),
            )
        )
    return "\n".join(lines)


def get_extension_root():
    try:
        script_dir = os.path.dirname(__file__)
        return os.path.abspath(os.path.join(script_dir, "..", "..", ".."))
    except Exception:
        return os.getcwd()


def get_pdf_config_path():
    try:
        return os.path.join(
            os.path.dirname(__file__), "create_print_set_pdf_config.json"
        )
    except Exception:
        return os.path.join(os.getcwd(), "create_print_set_pdf_config.json")


def load_pdf_config():
    config = dict(DEFAULT_PDF_CONFIG)
    path = get_pdf_config_path()
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                config.update(data)
    except Exception as ex:
        logger.warning("Could not load PDF config {}: {}".format(path, ex))
    return config


def get_external_tools_config_path():
    appdata = os.getenv("APPDATA") or ""
    if appdata:
        return os.path.join(appdata, "BIM3DNA", "external_tools.json")
    return ""


def get_external_tools_qpdf_path():
    path = get_external_tools_config_path()
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            qpdf_path = data.get("qpdf_path") or ""
            if (
                qpdf_path
                and os.path.exists(qpdf_path)
                and os.path.basename(qpdf_path).lower() == "qpdf.exe"
            ):
                return qpdf_path
    except Exception as ex:
        logger.warning("Could not read external tools config {}: {}".format(path, ex))
    return ""


def save_pdf_config(config):
    path = get_pdf_config_path()
    try:
        with open(path, "w") as f:
            json.dump(config, f, indent=4)
        logger.info("PDF config saved: {}".format(path))
    except Exception as ex:
        logger.warning("Could not save PDF config {}: {}".format(path, ex))


def get_optimizer_enabled(config):
    return bool(config.get("pdf_optimizer_enabled", False))


def get_optimizer_profile(config):
    profile = config.get("qpdf_profile")
    if profile not in (QPDF_PROFILE_SAFE_LOSSLESS, QPDF_PROFILE_SMALLER_COLOR):
        return QPDF_PROFILE_SAFE_LOSSLESS
    return profile


def get_post_compressor_engine(config):
    engine = config.get("post_compressor_engine") or COMPRESS_ENGINE_PDF24
    if engine not in (
        COMPRESS_ENGINE_PDF24,
        COMPRESS_ENGINE_QPDF,
        COMPRESS_ENGINE_NONE,
    ):
        return COMPRESS_ENGINE_PDF24
    return engine


def get_pdf24_profile(config):
    profile = config.get("pdf24_profile") or PDF24_PROFILE_BALANCED
    if profile not in (
        PDF24_PROFILE_BALANCED,
        PDF24_PROFILE_SMALLER,
        PDF24_PROFILE_MAXIMUM,
        PDF24_PROFILE_EXTREME,
        PDF24_PROFILE_CUSTOM,
    ):
        return PDF24_PROFILE_BALANCED
    return profile


def find_file_recursive(root_folder, file_name):
    if not root_folder or not os.path.isdir(root_folder):
        return None
    try:
        for root, dirs, files in os.walk(root_folder):
            for name in files:
                if name.lower() == file_name.lower():
                    return os.path.join(root, name)
    except Exception as ex:
        logger.warning("Executable search failed under {}: {}".format(root_folder, ex))
    return None


def find_qpdf_exe():
    config = load_pdf_config()
    configured = config.get("qpdf_exe_path") or ""
    if (
        configured
        and os.path.exists(configured)
        and os.path.basename(configured).lower() == "qpdf.exe"
    ):
        return configured

    external_configured = get_external_tools_qpdf_path()
    if external_configured:
        return external_configured

    extension_root = get_extension_root()
    candidates = [
        # qpdf may be bundled by BIM3DNA installer under:
        # <extension_root>/_runtime/qpdf/bin/qpdf.exe
        os.path.join(extension_root, "_runtime", "qpdf", "bin", "qpdf.exe"),
        os.path.join(extension_root, "_runtime", "qpdf", "qpdf.exe"),
        os.path.join(extension_root, "bin", "qpdf", "bin", "qpdf.exe"),
        os.path.join(extension_root, "bin", "qpdf.exe"),
        "C:\\Program Files\\qpdf\\bin\\qpdf.exe",
        "C:\\Program Files (x86)\\qpdf\\bin\\qpdf.exe",
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate

    for base in ["C:\\Program Files", "C:\\Program Files (x86)"]:
        try:
            if os.path.isdir(base):
                for name in os.listdir(base):
                    if name.lower().startswith("qpdf"):
                        direct = os.path.join(base, name, "bin", "qpdf.exe")
                        if os.path.exists(direct):
                            return direct
        except Exception as ex:
            logger.warning(
                "qpdf versioned folder search failed under {}: {}".format(base, ex)
            )

    for base in ["C:\\Program Files", "C:\\Program Files (x86)"]:
        try:
            if os.path.isdir(base):
                for name in os.listdir(base):
                    if name.lower().startswith("qpdf"):
                        found = find_file_recursive(
                            os.path.join(base, name), "qpdf.exe"
                        )
                        if found:
                            return found
        except Exception as ex:
            logger.warning(
                "qpdf recursive folder search failed under {}: {}".format(base, ex)
            )

    path_value = os.environ.get("PATH", "")
    for folder in path_value.split(os.pathsep):
        candidate = os.path.join(folder.strip('"'), "qpdf.exe")
        if os.path.exists(candidate):
            return candidate

    try:
        process = subprocess.Popen(
            ["where", "qpdf"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout_data, stderr_data = process.communicate()
        if process.returncode == 0 and stdout_data:
            first = str(stdout_data).splitlines()[0].strip()
            if os.path.exists(first):
                return first
    except Exception:
        pass

    return None


def find_pdf24_doctool_exe():
    config = load_pdf_config()
    configured = config.get("pdf24_exe_path") or ""
    if (
        configured
        and os.path.exists(configured)
        and os.path.basename(configured).lower() == "pdf24-doctool.exe"
    ):
        return configured
    candidates = [
        "C:\\Program Files\\PDF24\\pdf24-DocTool.exe",
        "C:\\Program Files (x86)\\PDF24\\pdf24-DocTool.exe",
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    for base in ["C:\\Program Files", "C:\\Program Files (x86)"]:
        try:
            if not os.path.isdir(base):
                continue
            for name in os.listdir(base):
                if name.lower().startswith("pdf24"):
                    direct = os.path.join(base, name, "pdf24-DocTool.exe")
                    if os.path.exists(direct):
                        return direct
                    found = find_file_recursive(
                        os.path.join(base, name), "pdf24-DocTool.exe"
                    )
                    if found:
                        return found
        except Exception as ex:
            logger.warning("PDF24 folder search failed under {}: {}".format(base, ex))
    try:
        process = subprocess.Popen(
            ["where", "pdf24-DocTool.exe"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout_data, stderr_data = process.communicate()
        if process.returncode == 0 and stdout_data:
            for line in str(stdout_data).splitlines():
                candidate = line.strip()
                if os.path.exists(candidate):
                    return candidate
    except Exception as ex:
        logger.warning("PDF24 PATH lookup failed: {}".format(ex))
    return None


def find_python_exe_for_rasterizer(config):
    configured = config.get("pdf_rasterizer_python_path") or ""
    if configured and os.path.exists(configured):
        return configured
    known_working = (
        "C:\\Users\\User\\AppData\\Local\\Programs\\Python\\Python313\\python.exe"
    )
    if os.path.exists(known_working):
        config["pdf_rasterizer_python_path"] = known_working
        try:
            save_pdf_config(config)
        except Exception:
            pass
        return known_working
    for exe_name in ["python.exe", "python"]:
        try:
            process = subprocess.Popen(
                ["where", exe_name],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout_data, stderr_data = process.communicate()
            if process.returncode == 0 and stdout_data:
                for line in str(stdout_data).splitlines():
                    candidate = line.strip()
                    if os.path.exists(candidate):
                        return candidate
        except Exception:
            pass
    return ""


def find_repo_root_from_extension(extension_root):
    current = os.path.abspath(extension_root)
    for _idx in range(8):
        if os.path.isdir(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return ""


def resolve_pdf_rasterizer_helper(config=None):
    config = config or load_pdf_config()
    configured = config.get("pdf_rasterizer_helper_path") or ""
    if configured and os.path.exists(configured):
        if configured.lower().endswith(".exe"):
            return {"kind": "exe", "helper": configured, "python": ""}
        if configured.lower().endswith(".py"):
            python_exe = find_python_exe_for_rasterizer(config)
            if python_exe:
                return {"kind": "script", "helper": configured, "python": python_exe}

    extension_root = get_extension_root()
    bundled_exe = os.path.join(
        extension_root, "bin", "pdf_rasterizer", "pdf_rasterize_helper.exe"
    )
    if os.path.exists(bundled_exe):
        return {"kind": "exe", "helper": bundled_exe, "python": ""}

    # Dev mode helper. The installer should later package a compiled helper under
    # <extension_root>/bin/pdf_rasterizer/pdf_rasterize_helper.exe.
    script_candidates = [
        os.path.join(
            extension_root, "tools", "pdf_rasterize_helper", "rasterize_pdf.py"
        ),
    ]
    repo_root = find_repo_root_from_extension(extension_root)
    if repo_root:
        script_candidates.append(
            os.path.join(repo_root, "tools", "pdf_rasterize_helper", "rasterize_pdf.py")
        )
    python_exe = find_python_exe_for_rasterizer(config)
    if python_exe:
        for script_path in script_candidates:
            if os.path.exists(script_path):
                return {"kind": "script", "helper": script_path, "python": python_exe}
    return None


def get_pdf_rasterizer_status_text(config=None):
    helper = resolve_pdf_rasterizer_helper(config or load_pdf_config())
    if not helper:
        return (
            "PDF rasterizer helper not found. Configure helper path or install "
            "BIM3DNA PDF rasterizer component."
        )
    if helper.get("kind") == "exe":
        return "PDF rasterizer helper found - {}".format(helper.get("helper"))
    return "PDF rasterizer dev helper found - {} via {}".format(
        helper.get("helper"), helper.get("python")
    )


def get_unique_optimized_pdf_path(input_pdf, suffix="_qpdf"):
    folder = os.path.dirname(input_pdf)
    base = os.path.splitext(os.path.basename(input_pdf))[0]
    candidate = os.path.join(folder, base + suffix + ".pdf")
    if not os.path.exists(candidate):
        return candidate
    index = 1
    while index < 1000:
        candidate = os.path.join(folder, "{}{}_{:03d}.pdf".format(base, suffix, index))
        if not os.path.exists(candidate):
            return candidate
        index += 1
    return os.path.join(folder, base + suffix + "_999.pdf")


def get_unique_flattened_pdf_path(input_pdf, output_folder, dpi, quality, color_mode):
    folder = output_folder or os.path.dirname(input_pdf)
    base = sanitize_filename(os.path.splitext(os.path.basename(input_pdf))[0])
    suffix = "_flattened_{}dpi_q{}".format(int(dpi), int(quality))
    if color_mode == "grayscale":
        suffix += "_gray"
    elif color_mode == "bw":
        suffix += "_bw"
    candidate = os.path.join(folder, base + suffix + ".pdf")
    if not os.path.exists(candidate):
        return candidate
    index = 1
    while index < 1000:
        candidate = os.path.join(folder, "{}{}_{:03d}.pdf".format(base, suffix, index))
        if not os.path.exists(candidate):
            return candidate
        index += 1
    return os.path.join(folder, base + suffix + "_999.pdf")


def quote_command_arg(arg):
    text = str(arg)
    if " " in text or "\t" in text:
        return '"{}"'.format(text)
    return text


def format_command_args(args):
    return " ".join([quote_command_arg(arg) for arg in args])


def build_qpdf_command(qpdf_exe, input_pdf, output_pdf, profile):
    args = [
        qpdf_exe,
        "--compress-streams=y",
        "--decode-level=generalized",
        "--recompress-flate",
        "--compression-level=9",
        "--object-streams=generate",
    ]
    if profile == QPDF_PROFILE_SMALLER_COLOR:
        args.extend(
            [
                "--optimize-images",
                "--jpeg-quality=75",
            ]
        )
    args.append(input_pdf)
    args.append(output_pdf)
    return args


def build_pdf24_command(
    pdf24_exe, input_pdf, output_pdf, dpi, image_quality, color_model
):
    args = [
        pdf24_exe,
        "-compress",
        "-dpi",
        str(int(dpi)),
        "-imageQuality",
        str(int(image_quality)),
    ]
    if color_model:
        args.extend(["-colorModel", str(color_model)])
    args.extend(["-outputFile", output_pdf, input_pdf])
    return args


def run_optimizer_process(engine, exe_path, args, input_pdf, output_pdf, profile):
    original_size = get_file_size_mb(input_pdf)
    start = time.time()
    logger.info("PDF optimizer engine: {}".format(engine))
    logger.info("PDF optimizer executable path: {}".format(exe_path))
    logger.info("PDF optimizer input PDF path: {}".format(input_pdf))
    logger.info("PDF optimizer output PDF path: {}".format(output_pdf))
    logger.info("PDF optimizer selected profile: {}".format(profile))
    logger.info("PDF optimizer command arguments: {}".format(format_command_args(args)))

    try:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout_data, stderr_data = process.communicate()
        exit_code = process.returncode
    except Exception as ex:
        logger.exception("PDF optimizer failed to start")
        return {
            "engine": engine,
            "profile": profile,
            "input_pdf": input_pdf,
            "output_pdf": output_pdf,
            "original_size_mb": original_size,
            "optimized_size_mb": None,
            "reduction_percent": None,
            "elapsed": time.time() - start,
            "success": False,
            "skipped_reason": "start_failed: {}".format(ex),
        }

    elapsed = time.time() - start
    logger.info("PDF optimizer exit code: {}".format(exit_code))
    try:
        logger.info("PDF optimizer stdout: {}".format(stdout_data))
    except Exception:
        pass
    try:
        logger.info("PDF optimizer stderr: {}".format(stderr_data))
    except Exception:
        pass

    if exit_code != 0:
        return {
            "engine": engine,
            "profile": profile,
            "input_pdf": input_pdf,
            "output_pdf": output_pdf,
            "original_size_mb": original_size,
            "optimized_size_mb": None,
            "reduction_percent": None,
            "elapsed": elapsed,
            "success": False,
            "skipped_reason": "non_zero_exit",
        }

    if not os.path.exists(output_pdf) or os.path.getsize(output_pdf) <= 0:
        return {
            "engine": engine,
            "profile": profile,
            "input_pdf": input_pdf,
            "output_pdf": output_pdf,
            "original_size_mb": original_size,
            "optimized_size_mb": None,
            "reduction_percent": None,
            "elapsed": elapsed,
            "success": False,
            "skipped_reason": "output_missing",
        }

    optimized_size = get_file_size_mb(output_pdf)
    reduction = None
    if original_size and optimized_size is not None:
        reduction = ((original_size - optimized_size) / original_size) * 100.0
    logger.info("PDF optimizer original size MB: {}".format(format_mb(original_size)))
    logger.info("PDF optimizer optimized size MB: {}".format(format_mb(optimized_size)))
    logger.info("PDF optimizer reduction percent: {}".format(format_percent(reduction)))
    logger.info("PDF optimizer elapsed seconds: {:.2f}".format(elapsed))
    if (
        original_size is not None
        and optimized_size is not None
        and optimized_size > original_size
    ):
        logger.warning("Optimized PDF is larger than original. Keeping both files.")

    return {
        "engine": engine,
        "profile": profile,
        "input_pdf": input_pdf,
        "output_pdf": output_pdf,
        "original_size_mb": original_size,
        "optimized_size_mb": optimized_size,
        "reduction_percent": reduction,
        "elapsed": elapsed,
        "success": True,
        "optimized_larger": (
            original_size is not None
            and optimized_size is not None
            and optimized_size > original_size
        ),
    }


def run_pdf_optimization(generated_paths):
    if not generated_paths:
        return None
    input_pdf = generated_paths[0]
    config = load_pdf_config()
    if not get_optimizer_enabled(config):
        return {
            "engine": "qpdf",
            "profile": "disabled in settings",
            "input_pdf": input_pdf,
            "output_pdf": None,
            "original_size_mb": get_file_size_mb(input_pdf),
            "optimized_size_mb": None,
            "reduction_percent": None,
            "elapsed": 0.0,
            "success": False,
            "skipped_reason": "disabled",
        }
    profile = get_optimizer_profile(config)
    exe_path = find_qpdf_exe()
    if not exe_path:
        logger.warning("qpdf executable not found; optimization skipped.")
        return {
            "engine": "qpdf",
            "profile": "skipped - qpdf not found",
            "input_pdf": input_pdf,
            "output_pdf": None,
            "original_size_mb": get_file_size_mb(input_pdf),
            "optimized_size_mb": None,
            "reduction_percent": None,
            "elapsed": 0.0,
            "success": False,
            "skipped_reason": "missing",
        }
    if not os.path.exists(input_pdf):
        logger.warning("qpdf input PDF missing: {}".format(input_pdf))
        return {
            "engine": "qpdf",
            "profile": profile,
            "input_pdf": input_pdf,
            "output_pdf": None,
            "original_size_mb": None,
            "optimized_size_mb": None,
            "reduction_percent": None,
            "elapsed": 0.0,
            "success": False,
            "skipped_reason": "input_missing",
        }

    output_pdf = get_unique_optimized_pdf_path(input_pdf, suffix="_qpdf")
    args = build_qpdf_command(exe_path, input_pdf, output_pdf, profile)
    return run_optimizer_process("qpdf", exe_path, args, input_pdf, output_pdf, profile)


def get_pdf24_settings_from_config(config):
    profile = get_pdf24_profile(config)
    if profile != PDF24_PROFILE_CUSTOM:
        defaults = PDF24_PROFILES.get(profile, PDF24_PROFILES[PDF24_PROFILE_BALANCED])
        return profile, defaults["dpi"], defaults["quality"], defaults["color_model"]
    try:
        dpi = int(config.get("pdf24_dpi") or 144)
    except Exception:
        dpi = 144
    try:
        quality = int(config.get("pdf24_image_quality") or 75)
    except Exception:
        quality = 75
    color_model = config.get("pdf24_color_model") or ""
    return profile, max(72, min(300, dpi)), max(1, min(100, quality)), color_model


def run_pdf24_compressor(
    input_pdf, pdf24_exe, profile, dpi, image_quality, color_model
):
    output_pdf = get_unique_optimized_pdf_path(input_pdf, suffix="_pdf24")
    args = build_pdf24_command(
        pdf24_exe, input_pdf, output_pdf, dpi, image_quality, color_model
    )
    result = run_optimizer_process(
        "PDF24", pdf24_exe, args, input_pdf, output_pdf, profile
    )
    if result:
        logger.info(
            "PDF24_TEST_RESULT | profile={} | dpi={} | quality={} | original_mb={} | compressed_mb={} | reduction={} | elapsed={:.2f}".format(
                profile,
                dpi,
                image_quality,
                format_mb(result.get("original_size_mb")),
                format_mb(result.get("optimized_size_mb")),
                format_percent(result.get("reduction_percent")),
                result.get("elapsed") or 0.0,
            )
        )
    return result


def run_post_compression(generated_paths, export_settings=None):
    export_settings = export_settings or {}
    if not generated_paths:
        return None
    input_pdf = generated_paths[0]
    config = load_pdf_config()
    engine = export_settings.get(
        "post_compressor_engine"
    ) or get_post_compressor_engine(config)
    enabled = bool(
        export_settings.get(
            "post_compressor_enabled", config.get("pdf24_compressor_enabled", True)
        )
    )
    if not enabled or engine == COMPRESS_ENGINE_NONE:
        return {
            "engine": engine,
            "profile": "disabled",
            "input_pdf": input_pdf,
            "output_pdf": None,
            "original_size_mb": get_file_size_mb(input_pdf),
            "optimized_size_mb": None,
            "reduction_percent": None,
            "elapsed": 0.0,
            "success": False,
            "skipped_reason": "disabled",
        }
    if engine == COMPRESS_ENGINE_QPDF:
        return run_pdf_optimization(generated_paths)
    pdf24_exe = export_settings.get("pdf24_exe_path") or find_pdf24_doctool_exe()
    if not pdf24_exe:
        logger.warning("PDF24 compressor not found; compression skipped.")
        return {
            "engine": COMPRESS_ENGINE_PDF24,
            "profile": "skipped - PDF24 not found",
            "input_pdf": input_pdf,
            "output_pdf": None,
            "original_size_mb": get_file_size_mb(input_pdf),
            "optimized_size_mb": None,
            "reduction_percent": None,
            "elapsed": 0.0,
            "success": False,
            "skipped_reason": "missing",
        }
    profile = export_settings.get("pdf24_profile") or get_pdf24_profile(config)
    temp_config = dict(config)
    temp_config["pdf24_profile"] = profile
    temp_config["pdf24_dpi"] = export_settings.get("pdf24_dpi", config.get("pdf24_dpi"))
    temp_config["pdf24_image_quality"] = export_settings.get(
        "pdf24_image_quality", config.get("pdf24_image_quality")
    )
    temp_config["pdf24_color_model"] = export_settings.get(
        "pdf24_color_model", config.get("pdf24_color_model")
    )
    profile, dpi, quality, color_model = get_pdf24_settings_from_config(temp_config)
    return run_pdf24_compressor(
        input_pdf, pdf24_exe, profile, dpi, quality, color_model
    )


def format_mb(value):
    if value is None:
        return "unavailable"
    return "{:.2f} MB".format(value)


def format_percent(value):
    if value is None:
        return "unavailable"
    return "{:.1f}%".format(value)


def get_default_output_folder(config=None):
    if config:
        last = config.get("last_output_folder") or ""
        if last:
            return last
    desktop = System.Environment.GetFolderPath(System.Environment.SpecialFolder.Desktop)
    return os.path.join(desktop, "BIM3DNA_PDF_Exports")


def validate_output_folder(output_folder):
    if not output_folder:
        raise Exception("Output folder is empty.")
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
    test_path = os.path.join(output_folder, "_bim3dna_write_test.tmp")
    with open(test_path, "w") as f:
        f.write("test")
    os.remove(test_path)


def browse_output_folder(current_folder):
    dlg = WinForms.FolderBrowserDialog()
    dlg.Description = "Choose PDF output folder"
    dlg.SelectedPath = current_folder or get_default_output_folder()
    if dlg.ShowDialog() == WinForms.DialogResult.OK:
        return dlg.SelectedPath
    return None


def choose_pdf_file_name(current_name):
    clean = sanitize_filename(os.path.splitext(current_name or "ViewPrintSet.pdf")[0])
    return clean + ".pdf"


def get_view_display_label(view):
    try:
        return "[{}] {}".format(view.ViewType.ToString().upper(), view.Name)
    except Exception:
        return view.Name


def resolve_template_views(template_name, available_views):
    template_view_names = load_template_by_name(template_name)
    selected_views = []
    missing_views = []
    available_view_names = {v.Name: v for v in available_views}
    for name in template_view_names:
        view = available_view_names.get(name)
        if view:
            selected_views.append(view)
        else:
            missing_views.append(name)
    return selected_views, missing_views


def get_pdf_optimizer_status_text():
    found = find_pdf24_doctool_exe()
    if found:
        return "PDF24 compressor: found - {}".format(found)
    return "PDF24 compressor not found - compression will be skipped"


def browse_for_qpdf_exe():
    dlg = WinForms.OpenFileDialog()
    dlg.Title = "Select qpdf.exe"
    dlg.Filter = "qpdf.exe|qpdf.exe|Executables|*.exe"
    dlg.Multiselect = False
    result = dlg.ShowDialog()
    if result != WinForms.DialogResult.OK:
        return None
    path = dlg.FileName
    if (
        not path
        or not os.path.exists(path)
        or os.path.basename(path).lower() != "qpdf.exe"
    ):
        forms.alert(
            "This is not qpdf.exe. You may have selected the qpdf installer. "
            "Please select the installed command-line executable, usually under "
            "C:\\Program Files\\qpdf...\\bin\\qpdf.exe."
        )
        return None
    return path


def browse_for_pdf24_exe():
    dlg = WinForms.OpenFileDialog()
    dlg.Title = "Select pdf24-DocTool.exe"
    dlg.Filter = "pdf24-DocTool.exe|pdf24-DocTool.exe|Executables|*.exe"
    dlg.Multiselect = False
    result = dlg.ShowDialog()
    if result != WinForms.DialogResult.OK:
        return None
    path = dlg.FileName
    if (
        not path
        or not os.path.exists(path)
        or os.path.basename(path).lower() != "pdf24-doctool.exe"
    ):
        forms.alert("Please select pdf24-DocTool.exe.", exitscript=False)
        return None
    return path


def test_pdf24_version():
    exe = find_pdf24_doctool_exe()
    if not exe:
        return "PDF24 compressor: not found"
    try:
        process = subprocess.Popen(
            [exe, "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        stdout_data, stderr_data = process.communicate()
        if process.returncode == 0:
            return "PDF24 test OK: {}".format(str(stdout_data or stderr_data).strip())
        return "PDF24 test returned code {}: {}".format(
            process.returncode, str(stderr_data).strip()
        )
    except Exception as ex:
        return "PDF24 test exception: {}".format(ex)


def test_qpdf_version():
    qpdf_exe = find_qpdf_exe()
    if not qpdf_exe:
        return "qpdf: not found - optimization will be skipped"
    try:
        process = subprocess.Popen(
            [qpdf_exe, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout_data, stderr_data = process.communicate()
        if process.returncode == 0:
            return "qpdf test OK: {}".format(str(stdout_data).strip())
        return "qpdf test failed: {}".format(str(stderr_data).strip())
    except Exception as ex:
        return "qpdf test exception: {}".format(ex)


def choose_existing_pdf_file():
    dlg = WinForms.OpenFileDialog()
    dlg.Title = "Select PDF to optimize"
    dlg.Filter = "PDF files (*.pdf)|*.pdf|All files (*.*)|*.*"
    dlg.Multiselect = False
    result = dlg.ShowDialog()
    if result != WinForms.DialogResult.OK:
        return None
    path = dlg.FileName
    if not path or not os.path.exists(path) or not path.lower().endswith(".pdf"):
        forms.alert("Please select an existing PDF file.", exitscript=False)
        return None
    return path


def choose_created_pdf_file():
    return choose_existing_pdf_file()


def optimize_existing_pdf(configured_qpdf_path=None, profile=None):
    input_pdf = choose_existing_pdf_file()
    if not input_pdf:
        return None
    qpdf_exe = None
    if (
        configured_qpdf_path
        and os.path.exists(configured_qpdf_path)
        and os.path.basename(configured_qpdf_path).lower() == "qpdf.exe"
    ):
        qpdf_exe = configured_qpdf_path
    if not qpdf_exe:
        qpdf_exe = find_qpdf_exe()
    if not qpdf_exe:
        return "qpdf not found. Browse directly to qpdf.exe and try again."
    profile = profile or get_optimizer_profile(load_pdf_config())
    output_pdf = get_unique_optimized_pdf_path(input_pdf, suffix="_qpdf")
    args = build_qpdf_command(qpdf_exe, input_pdf, output_pdf, profile)
    result = run_optimizer_process(
        "qpdf", qpdf_exe, args, input_pdf, output_pdf, profile
    )
    if not result or not result.get("success"):
        return "qpdf optimization failed. Original PDF was kept."
    warning = ""
    original = result.get("original_size_mb")
    optimized = result.get("optimized_size_mb")
    if original is not None and optimized is not None and optimized > original:
        warning = (
            "\nWarning: optimized PDF is larger than original. Keeping both files."
        )
    return "Optimization complete.\nOriginal: {}\nOptimized: {}\nReduction: {}\nElapsed: {:.2f} seconds{}".format(
        format_mb(original),
        format_mb(optimized),
        format_percent(result.get("reduction_percent")),
        result.get("elapsed") or 0.0,
        warning,
    )


def decode_process_output(data):
    if data is None:
        return ""
    try:
        if hasattr(data, "decode"):
            return data.decode("utf-8", "replace")
    except Exception:
        pass
    return str(data)


def tail_lines(text, count):
    lines = (text or "").splitlines()
    return "\n".join(lines[-count:])


def extract_json_object(text):
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        pass
    start = raw.rfind("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except Exception:
            return None
    return None


def run_process_capture(args):
    try:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout_data, stderr_data = process.communicate()
        return {
            "exit_code": process.returncode,
            "stdout": decode_process_output(stdout_data),
            "stderr": decode_process_output(stderr_data),
            "start_failed": False,
        }
    except Exception as ex:
        logger.exception("Process failed to start")
        return {
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "start_failed": True,
            "error": str(ex),
        }


def build_pdf_rasterizer_command(
    helper,
    input_pdf,
    output_pdf,
    dpi,
    quality,
    color_mode,
    image_codec,
    threshold,
    png_compression_level,
    indexed_colors,
):
    if helper.get("kind") == "exe":
        args = [helper.get("helper")]
    else:
        args = [helper.get("python"), helper.get("helper")]
    args.extend(
        [
            "--input",
            input_pdf,
            "--output",
            output_pdf,
            "--dpi",
            str(int(dpi)),
            "--quality",
            str(int(quality)),
            "--color-mode",
            color_mode,
            "--image-codec",
            image_codec,
            "--png-compression-level",
            str(int(png_compression_level)),
            "--indexed-colors",
            str(int(indexed_colors)),
            "--threshold",
            str(int(threshold)),
        ]
    )
    return args


def build_pdf_rasterizer_self_test_command(helper):
    if helper.get("kind") == "exe":
        return [helper.get("helper"), "--self-test"]
    return [helper.get("python"), helper.get("helper"), "--self-test"]


def run_pdf_rasterizer_self_test():
    config = load_pdf_config()
    helper = resolve_pdf_rasterizer_helper(config)
    if not helper:
        return {
            "ok": False,
            "error": (
                "PDF rasterizer helper not found. Configure helper path or install "
                "BIM3DNA PDF rasterizer component."
            ),
            "helper": "",
            "python": "",
        }
    args = build_pdf_rasterizer_self_test_command(helper)
    logger.info(
        "PDF rasterizer self-test command: {}".format(format_command_args(args))
    )
    captured = run_process_capture(args)
    stdout_text = captured.get("stdout") or ""
    stderr_text = captured.get("stderr") or ""
    if stderr_text:
        for line in stderr_text.splitlines():
            logger.info(line)
    result = extract_json_object(stdout_text) or {}
    result["exit_code"] = captured.get("exit_code")
    result["stdout"] = stdout_text
    result["stderr"] = stderr_text
    result["helper"] = helper.get("helper") or ""
    result["python"] = helper.get("python") or result.get("python") or ""
    if captured.get("start_failed"):
        result["ok"] = False
        result["error"] = "Could not start PDF rasterizer: {}".format(
            captured.get("error") or ""
        )
    elif captured.get("exit_code") != 0 and result.get("ok") is not False:
        result["ok"] = False
        result["error"] = "PDF rasterizer self-test failed."
    return result


def format_rasterizer_self_test_result(result):
    if result and result.get("ok"):
        return "Rasterizer OK: Python {}, pypdfium2 {}, Pillow {}, img2pdf {}".format(
            result.get("python") or "-",
            result.get("pypdfium2") or "-",
            result.get("pillow") or "-",
            result.get("img2pdf") or "-",
        )
    install_hint = (result or {}).get("install_hint")
    if not install_hint:
        python_path = (result or {}).get("python") or "<python>"
        install_hint = "{} -m pip install pypdfium2 Pillow img2pdf".format(
            python_path
        )
    return "Rasterizer failed: {}\nRun: {}".format(
        (result or {}).get("error") or "missing pypdfium2/Pillow",
        install_hint,
    )


def format_rasterizer_failure(result):
    result = result or {}
    return (
        "Rasterize / Flatten failed.\n\n"
        "Exit code: {}\n"
        "Error: {}\n\n"
        "Python:\n{}\n\n"
        "Helper:\n{}\n\n"
        "stderr:\n{}\n\n"
        "stdout:\n{}"
    ).format(
        result.get("exit_code"),
        result.get("error") or "Unknown error",
        result.get("python") or "-",
        result.get("helper") or "-",
        tail_lines(result.get("stderr") or "", 30),
        tail_lines(result.get("stdout") or "", 10),
    )


def run_pdf_rasterizer(
    input_pdf,
    output_pdf,
    dpi,
    quality,
    color_mode,
    image_codec,
    threshold,
    png_compression_level,
    indexed_colors,
    skip_self_test=False,
):
    config = load_pdf_config()
    helper = resolve_pdf_rasterizer_helper(config)
    if not helper:
        return {
            "ok": False,
            "error": (
                "PDF rasterizer helper not found. Configure helper path or install "
                "BIM3DNA PDF rasterizer component."
            ),
        }
    if not input_pdf or not os.path.exists(input_pdf):
        return {"ok": False, "error": "Input PDF not found: {}".format(input_pdf)}
    if not skip_self_test:
        self_test = run_pdf_rasterizer_self_test()
        if not self_test.get("ok") and not (
            image_codec == "jpeg" and self_test.get("img2pdf") == "missing"
        ):
            return self_test
    output_folder = os.path.dirname(output_pdf)
    if not os.path.isdir(output_folder):
        try:
            os.makedirs(output_folder)
        except Exception as ex:
            return {
                "ok": False,
                "error": "Could not create output folder: {}".format(ex),
            }

    args = build_pdf_rasterizer_command(
        helper,
        input_pdf,
        output_pdf,
        dpi,
        quality,
        color_mode,
        image_codec,
        threshold,
        png_compression_level,
        indexed_colors,
    )
    logger.info(
        "RASTERIZE_PDF_START | input={} | output={}".format(input_pdf, output_pdf)
    )
    logger.info(
        "PDF rasterizer command arguments: {}".format(format_command_args(args))
    )
    start = time.time()
    captured = run_process_capture(args)
    stdout_text = captured.get("stdout") or ""
    stderr_text = captured.get("stderr") or ""
    if stderr_text:
        for line in stderr_text.splitlines():
            logger.info(line)
    exit_code = captured.get("exit_code")
    logger.info("PDF rasterizer exit code: {}".format(exit_code))
    result = extract_json_object(stdout_text)
    if not result:
        return {
            "ok": False,
            "error": "Could not parse PDF rasterizer JSON output.",
            "exit_code": exit_code,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "python": helper.get("python") or "",
            "helper": helper.get("helper") or "",
        }
    result["exit_code"] = exit_code
    result["stdout"] = stdout_text
    result["stderr"] = stderr_text
    result["python"] = helper.get("python") or result.get("python") or ""
    result["helper"] = helper.get("helper") or ""
    if exit_code != 0:
        result["ok"] = False
        if not result.get("error"):
            result["error"] = "PDF rasterizer failed with exit code {}.".format(
                exit_code
            )
        return result
    if not result.get("ok"):
        return result
    if not os.path.exists(output_pdf) or os.path.getsize(output_pdf) <= 0:
        result["ok"] = False
        result["error"] = "Rasterized output PDF was not created."
        return result
    result["elapsed_seconds"] = result.get("elapsed_seconds") or (time.time() - start)
    logger.info(
        "RASTERIZE_PDF_DONE | input_mb={} | output_mb={} | reduction={} | elapsed={:.2f}".format(
            format_mb(result.get("input_mb")),
            format_mb(result.get("output_mb")),
            format_percent(result.get("reduction_percent")),
            result.get("elapsed_seconds") or 0.0,
        )
    )
    return result


def format_rasterize_result(result):
    if not result or not result.get("ok"):
        return format_rasterizer_failure(result)
    warnings = "; ".join(result.get("warnings") or []) or "-"
    return (
        "Rasterize / Flatten completed.\n\n"
        "Input PDF:\n{} ({})\n\n"
        "Output PDF:\n{} ({})\n\n"
        "Pages:\n{}\n\n"
        "Settings:\nDPI: {}\nJPEG quality: {}%\nColor mode: {}\n\n"
        "Image compression: {}\nThreshold: {}\n\n"
        "PNG compression level: {}\nIndexed colors: {}\n\n"
        "Reduction:\n{}\n\n"
        "Warnings:\n{}\n\n"
        "Elapsed:\n{:.2f} seconds\n\n"
        "Notes:\n"
        "- Output is image-based review PDF.\n"
        "- Text/vector content is no longer selectable/searchable.\n"
        "- Lower DPI/quality reduces file size and detail."
    ).format(
        result.get("input"),
        format_mb(result.get("input_mb")),
        result.get("output"),
        format_mb(result.get("output_mb")),
        result.get("pages"),
        result.get("dpi"),
        result.get("quality"),
        result.get("color_mode"),
        result.get("image_codec"),
        result.get("threshold") if result.get("threshold") is not None else "-",
        result.get("png_compression_level"),
        result.get("indexed_colors") if result.get("indexed_colors") is not None else "-",
        format_percent(result.get("reduction_percent")),
        warnings,
        result.get("elapsed_seconds") or 0.0,
    )


def run_flatten_matrix(input_pdf, output_folder):
    if not input_pdf:
        return
    self_test = run_pdf_rasterizer_self_test()
    if not self_test.get("ok"):
        forms.alert(
            format_rasterizer_failure(self_test),
            title="Rasterize / Flatten Existing PDF",
            exitscript=False,
        )
        return
    profiles = [
        ("96 dpi / q50 / JPEG / Color", 96, 50, "color", "jpeg", 200, 9, 128, "_flat_96_q50_jpeg_color"),
        ("150 dpi / q60 / JPEG / Color", 150, 60, "color", "jpeg", 200, 9, 128, "_flat_150_q60_jpeg_color"),
        ("300 dpi / q50 / JPEG / Color", 300, 50, "color", "jpeg", 200, 9, 128, "_flat_300_q50_jpeg_color"),
        ("150 dpi / PNG / Color", 150, 50, "color", "png", 200, 9, 128, "_flat_150_png_color"),
        ("150 dpi / Indexed PNG / Color", 150, 50, "color", "png-indexed", 200, 9, 128, "_flat_150_pngindexed_128c_color"),
        ("300 dpi / Black/White CCITT", 300, 50, "bw", "bw", 200, 9, 128, "_flat_300_bw_t200"),
    ]
    folder = output_folder or os.path.dirname(input_pdf)
    base = sanitize_filename(os.path.splitext(os.path.basename(input_pdf))[0])
    rows = [
        "Profile | DPI | Codec | Quality | Mode | Status | Size MB | Reduction % | Elapsed | Warning"
    ]
    for label, dpi, quality, color_mode, image_codec, threshold, png_level, indexed_colors, suffix in profiles:
        output_pdf = get_unique_optimized_pdf_path(
            os.path.join(folder, base + ".pdf"), suffix=suffix
        )
        result = run_pdf_rasterizer(
            input_pdf,
            output_pdf,
            dpi,
            quality,
            color_mode,
            image_codec,
            threshold,
            png_level,
            indexed_colors,
            skip_self_test=True,
        )
        if result and result.get("ok"):
            warning_text = "; ".join(result.get("warnings") or []) or "-"
            rows.append(
                "{} | {} | {} | {} | {} | OK | {} | {} | {:.2f}s | {}".format(
                    label,
                    dpi,
                    image_codec,
                    quality,
                    color_mode,
                    format_mb(result.get("output_mb")),
                    format_percent(result.get("reduction_percent")),
                    result.get("elapsed_seconds") or 0.0,
                    warning_text,
                )
            )
        else:
            rows.append(
                "{} | {} | {} | {} | {} | Failed | - | - | - | {}".format(
                    label,
                    dpi,
                    image_codec,
                    quality,
                    color_mode,
                    (result or {}).get("error") or "unknown",
                )
            )
    forms.alert(
        "Flatten Matrix completed.\n\n{}".format("\n".join(rows)),
        title="Rasterize / Flatten Existing PDF",
        exitscript=False,
    )


def show_rasterize_existing_pdf_dialog():
    config = load_pdf_config()
    form = WinForms.Form()
    form.Text = "Rasterize / Flatten Existing PDF"
    form.StartPosition = WinForms.FormStartPosition.CenterScreen
    form.FormBorderStyle = WinForms.FormBorderStyle.FixedDialog
    form.ClientSize = Drawing.Size(720, 470)
    form.MinimizeBox = False
    form.MaximizeBox = False
    form.TopMost = True

    warning = WinForms.Label()
    warning.Text = (
        "Rasterize/Flatten converts pages to image-based PDF for review/sharing only. "
        "Text, linework, and annotations will no longer be vector/searchable/selectable."
    )
    warning.Location = Drawing.Point(12, 10)
    warning.Size = Drawing.Size(650, 38)
    warning.ForeColor = Drawing.Color.DarkRed
    form.Controls.Add(warning)

    input_label = WinForms.Label()
    input_label.Text = "Input PDF:"
    input_label.Location = Drawing.Point(12, 58)
    input_label.AutoSize = True
    form.Controls.Add(input_label)
    input_box = WinForms.TextBox()
    input_box.Location = Drawing.Point(112, 55)
    input_box.Size = Drawing.Size(440, 22)
    form.Controls.Add(input_box)
    input_browse = WinForms.Button()
    input_browse.Text = "Browse..."
    input_browse.Location = Drawing.Point(560, 53)
    input_browse.Size = Drawing.Size(95, 26)
    form.Controls.Add(input_browse)

    output_label = WinForms.Label()
    output_label.Text = "Output folder:"
    output_label.Location = Drawing.Point(12, 90)
    output_label.AutoSize = True
    form.Controls.Add(output_label)
    output_box = WinForms.TextBox()
    output_box.Location = Drawing.Point(112, 87)
    output_box.Size = Drawing.Size(440, 22)
    output_box.Text = get_default_output_folder(config)
    form.Controls.Add(output_box)
    output_browse = WinForms.Button()
    output_browse.Text = "Browse..."
    output_browse.Location = Drawing.Point(560, 85)
    output_browse.Size = Drawing.Size(95, 26)
    form.Controls.Add(output_browse)

    name_label = WinForms.Label()
    name_label.Text = "Output filename:"
    name_label.Location = Drawing.Point(12, 122)
    name_label.AutoSize = True
    form.Controls.Add(name_label)
    name_box = WinForms.TextBox()
    name_box.Location = Drawing.Point(112, 119)
    name_box.Size = Drawing.Size(300, 22)
    form.Controls.Add(name_box)

    preset_label = WinForms.Label()
    preset_label.Text = "Preset:"
    preset_label.Location = Drawing.Point(12, 158)
    preset_label.AutoSize = True
    form.Controls.Add(preset_label)
    preset_combo = WinForms.ComboBox()
    preset_combo.DropDownStyle = WinForms.ComboBoxStyle.DropDownList
    preset_combo.Location = Drawing.Point(112, 155)
    preset_combo.Size = Drawing.Size(220, 22)
    for preset in [
        RASTERIZE_PRESET_REVIEW_SMALL,
        RASTERIZE_PRESET_REVIEW_SHARP,
        RASTERIZE_PRESET_REVIEW_HIGH_DETAIL,
        RASTERIZE_PRESET_LOSSLESS_PNG,
        RASTERIZE_PRESET_INDEXED_PNG,
        RASTERIZE_PRESET_BW_SHARP,
        RASTERIZE_PRESET_CUSTOM,
    ]:
        preset_combo.Items.Add(preset)
    choose_combobox_value(preset_combo, RASTERIZE_PRESET_REVIEW_SHARP)
    form.Controls.Add(preset_combo)

    dpi_label = WinForms.Label()
    dpi_label.Text = "DPI:"
    dpi_label.Location = Drawing.Point(12, 195)
    dpi_label.AutoSize = True
    form.Controls.Add(dpi_label)
    dpi_num = WinForms.NumericUpDown()
    dpi_num.Location = Drawing.Point(112, 191)
    dpi_num.Size = Drawing.Size(70, 22)
    dpi_num.Minimum = 36
    dpi_num.Maximum = 300
    dpi_num.Value = int(config.get("pdf_rasterizer_default_dpi") or 72)
    form.Controls.Add(dpi_num)

    quality_label = WinForms.Label()
    quality_label.Text = "JPEG quality:"
    quality_label.Location = Drawing.Point(205, 195)
    quality_label.AutoSize = True
    form.Controls.Add(quality_label)
    quality_num = WinForms.NumericUpDown()
    quality_num.Location = Drawing.Point(295, 191)
    quality_num.Size = Drawing.Size(70, 22)
    quality_num.Minimum = 10
    quality_num.Maximum = 95
    quality_num.Value = int(config.get("pdf_rasterizer_default_quality") or 45)
    form.Controls.Add(quality_num)

    mode_label = WinForms.Label()
    mode_label.Text = "Color mode:"
    mode_label.Location = Drawing.Point(390, 195)
    mode_label.AutoSize = True
    form.Controls.Add(mode_label)
    mode_combo = WinForms.ComboBox()
    mode_combo.DropDownStyle = WinForms.ComboBoxStyle.DropDownList
    mode_combo.Location = Drawing.Point(470, 191)
    mode_combo.Size = Drawing.Size(135, 22)
    for mode in ["Color", "Grayscale", "Black and white"]:
        mode_combo.Items.Add(mode)
    choose_combobox_value(
        mode_combo, config.get("pdf_rasterizer_default_color_mode") or "Color"
    )
    form.Controls.Add(mode_combo)

    codec_label = WinForms.Label()
    codec_label.Text = "Image compression:"
    codec_label.Location = Drawing.Point(12, 225)
    codec_label.AutoSize = True
    form.Controls.Add(codec_label)
    codec_combo = WinForms.ComboBox()
    codec_combo.DropDownStyle = WinForms.ComboBoxStyle.DropDownList
    codec_combo.Location = Drawing.Point(112, 222)
    codec_combo.Size = Drawing.Size(330, 22)
    for codec_label_text in [
        RASTERIZE_CODEC_LABEL_JPEG,
        RASTERIZE_CODEC_LABEL_PNG,
        RASTERIZE_CODEC_LABEL_INDEXED,
        RASTERIZE_CODEC_LABEL_BW,
    ]:
        codec_combo.Items.Add(codec_label_text)
    choose_combobox_value(
        codec_combo,
        config.get("pdf_rasterizer_default_image_codec") or RASTERIZE_CODEC_LABEL_JPEG,
    )
    form.Controls.Add(codec_combo)

    threshold_label = WinForms.Label()
    threshold_label.Text = "B/W threshold:"
    threshold_label.Location = Drawing.Point(455, 225)
    threshold_label.AutoSize = True
    form.Controls.Add(threshold_label)
    threshold_num = WinForms.NumericUpDown()
    threshold_num.Location = Drawing.Point(545, 221)
    threshold_num.Size = Drawing.Size(65, 22)
    threshold_num.Minimum = 1
    threshold_num.Maximum = 255
    threshold_num.Value = int(config.get("pdf_rasterizer_default_threshold") or 200)
    form.Controls.Add(threshold_num)

    png_level_label = WinForms.Label()
    png_level_label.Text = "PNG level:"
    png_level_label.Location = Drawing.Point(455, 255)
    png_level_label.AutoSize = True
    form.Controls.Add(png_level_label)
    png_level_num = WinForms.NumericUpDown()
    png_level_num.Location = Drawing.Point(545, 251)
    png_level_num.Size = Drawing.Size(65, 22)
    png_level_num.Minimum = 0
    png_level_num.Maximum = 9
    png_level_num.Value = int(config.get("pdf_rasterizer_default_png_compression_level") or 9)
    form.Controls.Add(png_level_num)

    indexed_label = WinForms.Label()
    indexed_label.Text = "Indexed colors:"
    indexed_label.Location = Drawing.Point(455, 282)
    indexed_label.AutoSize = True
    form.Controls.Add(indexed_label)
    indexed_combo = WinForms.ComboBox()
    indexed_combo.DropDownStyle = WinForms.ComboBoxStyle.DropDownList
    indexed_combo.Location = Drawing.Point(545, 278)
    indexed_combo.Size = Drawing.Size(75, 22)
    for item in ["16", "32", "64", "128", "256"]:
        indexed_combo.Items.Add(item)
    choose_combobox_value(
        indexed_combo, str(config.get("pdf_rasterizer_default_indexed_colors") or 128)
    )
    form.Controls.Add(indexed_combo)

    page_label = WinForms.Label()
    page_label.Text = "Page range: All pages only. TODO: custom page ranges."
    page_label.Location = Drawing.Point(112, 255)
    page_label.Size = Drawing.Size(400, 22)
    form.Controls.Add(page_label)

    codec_note = WinForms.Label()
    codec_note.Text = "Quality % applies only to JPEG."
    codec_note.Location = Drawing.Point(112, 282)
    codec_note.Size = Drawing.Size(600, 22)
    codec_note.ForeColor = Drawing.Color.DarkBlue
    form.Controls.Add(codec_note)

    status_label = WinForms.Label()
    status_label.Text = get_pdf_rasterizer_status_text(config)
    status_label.Location = Drawing.Point(12, 310)
    status_label.Size = Drawing.Size(650, 48)
    status_label.ForeColor = Drawing.Color.DarkBlue
    form.Controls.Add(status_label)

    test_btn = WinForms.Button()
    test_btn.Text = "Test Rasterizer"
    test_btn.Location = Drawing.Point(125, 420)
    test_btn.Size = Drawing.Size(130, 30)
    form.Controls.Add(test_btn)

    rasterize_btn = WinForms.Button()
    rasterize_btn.Text = "Rasterize / Flatten"
    rasterize_btn.Location = Drawing.Point(265, 420)
    rasterize_btn.Size = Drawing.Size(130, 30)
    form.Controls.Add(rasterize_btn)
    matrix_btn = WinForms.Button()
    matrix_btn.Text = "Run Flatten Matrix"
    matrix_btn.Location = Drawing.Point(405, 420)
    matrix_btn.Size = Drawing.Size(130, 30)
    form.Controls.Add(matrix_btn)
    cancel_btn = WinForms.Button()
    cancel_btn.Text = "Cancel"
    cancel_btn.Location = Drawing.Point(545, 420)
    cancel_btn.Size = Drawing.Size(95, 30)
    form.Controls.Add(cancel_btn)

    filename_user_edited = {"value": False}
    updating_name = {"value": False}

    def update_default_name():
        path = input_box.Text.strip()
        if not path or filename_user_edited["value"]:
            return
        base = sanitize_filename(os.path.splitext(os.path.basename(path))[0])
        image_codec = RASTERIZE_CODEC_MAP.get(
            str(codec_combo.SelectedItem or RASTERIZE_CODEC_LABEL_JPEG), "jpeg"
        )
        codec_part = image_codec.replace("-", "")
        if image_codec == "jpeg":
            suffix = "_flat_{}dpi_q{}_{}".format(
                int(dpi_num.Value), int(quality_num.Value), codec_part
            )
        elif image_codec == "png-indexed":
            suffix = "_flat_{}dpi_pngindexed_{}c".format(
                int(dpi_num.Value), str(indexed_combo.SelectedItem or "128")
            )
        elif image_codec == "bw":
            suffix = "_flat_{}dpi_bw_t{}".format(
                int(dpi_num.Value), int(threshold_num.Value)
            )
        else:
            suffix = "_flat_{}dpi_{}".format(int(dpi_num.Value), codec_part)
        filename = "{}{}.pdf".format(base, suffix)
        updating_name["value"] = True
        name_box.Text = filename
        updating_name["value"] = False

    def ensure_output_filename_extension():
        filename = name_box.Text.strip()
        if filename and not filename.lower().endswith(".pdf"):
            updating_name["value"] = True
            name_box.Text = filename + ".pdf"
            updating_name["value"] = False

    def on_output_name_changed(sender, args):
        if not updating_name["value"]:
            filename_user_edited["value"] = True

    def refresh_codec_state():
        image_codec = RASTERIZE_CODEC_MAP.get(
            str(codec_combo.SelectedItem or RASTERIZE_CODEC_LABEL_JPEG), "jpeg"
        )
        jpeg_mode = image_codec == "jpeg"
        bw_mode = image_codec == "bw"
        png_mode = image_codec in ("png", "png-indexed")
        indexed_mode = image_codec == "png-indexed"
        quality_num.Enabled = jpeg_mode
        quality_label.Enabled = jpeg_mode
        threshold_num.Visible = bw_mode
        threshold_label.Visible = bw_mode
        png_level_num.Visible = png_mode
        png_level_label.Visible = png_mode
        indexed_combo.Visible = indexed_mode
        indexed_label.Visible = indexed_mode
        if bw_mode:
            choose_combobox_value(mode_combo, "Black and white")
            mode_combo.Enabled = False
            codec_note.Text = "Black/white removes color and uses thresholding."
        else:
            mode_combo.Enabled = True
            if image_codec == "png":
                codec_note.Text = "PNG is lossless. Quality % does not apply."
            elif image_codec == "png-indexed":
                codec_note.Text = "Indexed PNG reduces color count. Useful for drawings."
            else:
                codec_note.Text = "JPEG quality controls file size/detail. Lower quality means smaller files."
        update_default_name()

    def apply_flatten_preset(sender=None, args=None):
        preset = str(preset_combo.SelectedItem or RASTERIZE_PRESET_REVIEW_SHARP)
        settings = RASTERIZE_PRESETS.get(preset)
        if settings:
            dpi_num.Value = int(settings["dpi"])
            quality_num.Value = int(settings["quality"])
            choose_combobox_value(mode_combo, settings["color_mode"])
            choose_combobox_value(codec_combo, settings["image_codec"])
            threshold_num.Value = int(settings.get("threshold") or 200)
            png_level_num.Value = int(settings.get("png_compression_level") or 9)
            choose_combobox_value(
                indexed_combo, str(settings.get("indexed_colors") or 128)
            )
            refresh_codec_state()
            update_default_name()

    def on_input_browse(sender, args):
        selected = choose_existing_pdf_file()
        if selected:
            input_box.Text = selected
            output_box.Text = os.path.dirname(selected)
            filename_user_edited["value"] = False
            update_default_name()

    def on_output_browse(sender, args):
        selected = browse_output_folder(output_box.Text)
        if selected:
            output_box.Text = selected
            update_default_name()

    def validate_dialog_paths():
        input_pdf = input_box.Text.strip()
        if not input_pdf or not os.path.exists(input_pdf):
            forms.alert("Select an existing input PDF.", exitscript=False)
            return None
        output_folder = output_box.Text.strip() or os.path.dirname(input_pdf)
        try:
            validate_output_folder(output_folder)
        except Exception as ex:
            forms.alert(
                "Output folder is not writable:\n{}".format(ex), exitscript=False
            )
            return None
        filename = name_box.Text.strip()
        if not filename:
            filename_user_edited["value"] = False
            update_default_name()
            filename = name_box.Text.strip()
        if not filename.lower().endswith(".pdf"):
            filename += ".pdf"
            updating_name["value"] = True
            name_box.Text = filename
            updating_name["value"] = False
        output_pdf = get_unique_pdf_path(os.path.join(output_folder, filename))
        return input_pdf, output_pdf

    def save_rasterizer_defaults():
        config["pdf_rasterizer_default_dpi"] = int(dpi_num.Value)
        config["pdf_rasterizer_default_quality"] = int(quality_num.Value)
        config["pdf_rasterizer_default_color_mode"] = str(
            mode_combo.SelectedItem or "Color"
        )
        config["pdf_rasterizer_default_image_codec"] = str(
            codec_combo.SelectedItem or RASTERIZE_CODEC_LABEL_JPEG
        )
        config["pdf_rasterizer_default_png_compression_level"] = int(
            png_level_num.Value
        )
        try:
            config["pdf_rasterizer_default_indexed_colors"] = int(
                str(indexed_combo.SelectedItem or "128")
            )
        except Exception:
            config["pdf_rasterizer_default_indexed_colors"] = 128
        config["pdf_rasterizer_default_threshold"] = int(threshold_num.Value)
        helper = resolve_pdf_rasterizer_helper(config)
        if helper and helper.get("kind") == "script" and helper.get("python"):
            config["pdf_rasterizer_python_path"] = helper.get("python")
        save_pdf_config(config)

    def on_test_rasterizer(sender, args):
        result = run_pdf_rasterizer_self_test()
        status_label.Text = format_rasterizer_self_test_result(result)
        if result.get("ok"):
            helper_python = result.get("python") or ""
            if helper_python and os.path.exists(helper_python):
                config["pdf_rasterizer_python_path"] = helper_python
                save_pdf_config(config)

    def on_rasterize(sender, args):
        paths = validate_dialog_paths()
        if not paths:
            return
        save_rasterizer_defaults()
        input_pdf, output_pdf = paths
        color_mode = RASTERIZE_COLOR_MODE_MAP.get(
            str(mode_combo.SelectedItem or "Color"), "color"
        )
        image_codec = RASTERIZE_CODEC_MAP.get(
            str(codec_combo.SelectedItem or RASTERIZE_CODEC_LABEL_JPEG), "jpeg"
        )
        old_cursor = form.Cursor
        form.Cursor = WinForms.Cursors.WaitCursor
        try:
            try:
                indexed_colors = int(str(indexed_combo.SelectedItem or "128"))
            except Exception:
                indexed_colors = 128
            result = run_pdf_rasterizer(
                input_pdf,
                output_pdf,
                int(dpi_num.Value),
                int(quality_num.Value),
                color_mode,
                image_codec,
                int(threshold_num.Value),
                int(png_level_num.Value),
                indexed_colors,
            )
        finally:
            form.Cursor = old_cursor
        forms.alert(
            format_rasterize_result(result),
            title="Rasterize / Flatten Existing PDF",
            exitscript=False,
        )
        status_label.Text = (
            "Last result: {}".format(format_percent(result.get("reduction_percent")))
            if result and result.get("ok")
            else "Last result failed: {}".format((result or {}).get("error") or "")
        )

    def on_matrix(sender, args):
        paths = validate_dialog_paths()
        if not paths:
            return
        save_rasterizer_defaults()
        input_pdf, output_pdf = paths
        old_cursor = form.Cursor
        form.Cursor = WinForms.Cursors.WaitCursor
        try:
            run_flatten_matrix(input_pdf, os.path.dirname(output_pdf))
        finally:
            form.Cursor = old_cursor

    def on_cancel(sender, args):
        form.DialogResult = WinForms.DialogResult.Cancel
        form.Close()

    input_browse.Click += on_input_browse
    output_browse.Click += on_output_browse
    name_box.TextChanged += on_output_name_changed
    preset_combo.SelectedIndexChanged += apply_flatten_preset
    dpi_num.ValueChanged += lambda sender, args: update_default_name()
    quality_num.ValueChanged += lambda sender, args: update_default_name()
    mode_combo.SelectedIndexChanged += lambda sender, args: update_default_name()
    codec_combo.SelectedIndexChanged += lambda sender, args: refresh_codec_state()
    threshold_num.ValueChanged += lambda sender, args: update_default_name()
    png_level_num.ValueChanged += lambda sender, args: update_default_name()
    indexed_combo.SelectedIndexChanged += lambda sender, args: update_default_name()
    test_btn.Click += on_test_rasterizer
    rasterize_btn.Click += on_rasterize
    matrix_btn.Click += on_matrix
    cancel_btn.Click += on_cancel
    form.CancelButton = cancel_btn

    apply_flatten_preset()
    form.ShowDialog()


def choose_combobox_value(combo, value):
    try:
        for i in range(combo.Items.Count):
            if str(combo.Items[i]) == str(value):
                combo.SelectedIndex = i
                return
        if combo.Items.Count > 0:
            combo.SelectedIndex = 0
    except Exception:
        pass


def add_preview_items(listbox, views):
    listbox.Items.Clear()
    for view in views:
        listbox.Items.Add(get_view_display_label(view))


def show_create_print_set_pdf_dialog():
    config = load_pdf_config()
    available_views = get_printable_views()
    grouped_names, view_lookup = build_grouped_view_lookup(available_views)
    templates = get_template_names()
    print_sets = get_existing_print_sets()
    print_set_lookup = {ps.Name: ps for ps in print_sets}

    form = WinForms.Form()
    form.Text = "Create Print Set / Compact PDF"
    form.StartPosition = WinForms.FormStartPosition.CenterScreen
    form.FormBorderStyle = WinForms.FormBorderStyle.Sizable
    form.MinimizeBox = False
    form.MaximizeBox = False
    form.Size = Drawing.Size(1320, 900)
    form.MinimumSize = Drawing.Size(1280, 800)

    source_label = WinForms.Label()
    source_label.Text = "Source"
    source_label.Location = Drawing.Point(10, 14)
    source_label.AutoSize = True
    form.Controls.Add(source_label)
    source_combo = WinForms.ComboBox()
    source_combo.DropDownStyle = WinForms.ComboBoxStyle.DropDownList
    source_combo.Location = Drawing.Point(70, 10)
    source_combo.Size = Drawing.Size(240, 22)
    source_combo.Items.Add("Manual view selection")
    source_combo.Items.Add("Load saved template")
    source_combo.Items.Add("Existing print set")
    last_source = config.get("last_source_mode", "manual")
    if last_source == "template":
        choose_combobox_value(source_combo, "Load saved template")
    elif last_source == "existing":
        choose_combobox_value(source_combo, "Existing print set")
    else:
        choose_combobox_value(source_combo, "Manual view selection")
    form.Controls.Add(source_combo)

    selection_group = WinForms.GroupBox()
    selection_group.Text = "Selection"
    selection_group.Location = Drawing.Point(10, 42)
    selection_group.Size = Drawing.Size(610, 510)
    selection_group.Anchor = (
        WinForms.AnchorStyles.Top
        | WinForms.AnchorStyles.Bottom
        | WinForms.AnchorStyles.Left
        | WinForms.AnchorStyles.Right
    )
    form.Controls.Add(selection_group)

    search_label = WinForms.Label()
    search_label.Text = "Search:"
    search_label.Location = Drawing.Point(12, 24)
    search_label.AutoSize = True
    selection_group.Controls.Add(search_label)
    search_box = WinForms.TextBox()
    search_box.Location = Drawing.Point(70, 20)
    search_box.Size = Drawing.Size(520, 22)
    search_box.Anchor = (
        WinForms.AnchorStyles.Top
        | WinForms.AnchorStyles.Left
        | WinForms.AnchorStyles.Right
    )
    selection_group.Controls.Add(search_box)

    view_list = WinForms.ListView()
    view_list.View = WinForms.View.Details
    view_list.CheckBoxes = True
    view_list.FullRowSelect = True
    view_list.MultiSelect = True
    view_list.HideSelection = False
    view_list.Location = Drawing.Point(12, 50)
    view_list.Size = Drawing.Size(578, 270)
    view_list.Anchor = (
        WinForms.AnchorStyles.Top
        | WinForms.AnchorStyles.Bottom
        | WinForms.AnchorStyles.Left
        | WinForms.AnchorStyles.Right
    )
    view_list.Columns.Add("Type", 110)
    view_list.Columns.Add("Name", 360)
    view_list.Columns.Add("Id", 90)
    selection_group.Controls.Add(view_list)

    check_selected_btn = WinForms.Button()
    check_selected_btn.Text = "Check Selected"
    check_selected_btn.Location = Drawing.Point(12, 326)
    check_selected_btn.Size = Drawing.Size(110, 25)
    check_selected_btn.Anchor = (
        WinForms.AnchorStyles.Bottom | WinForms.AnchorStyles.Left
    )
    selection_group.Controls.Add(check_selected_btn)

    uncheck_selected_btn = WinForms.Button()
    uncheck_selected_btn.Text = "Uncheck Selected"
    uncheck_selected_btn.Location = Drawing.Point(128, 326)
    uncheck_selected_btn.Size = Drawing.Size(120, 25)
    uncheck_selected_btn.Anchor = (
        WinForms.AnchorStyles.Bottom | WinForms.AnchorStyles.Left
    )
    selection_group.Controls.Add(uncheck_selected_btn)

    check_all_btn = WinForms.Button()
    check_all_btn.Text = "Check All"
    check_all_btn.Location = Drawing.Point(254, 326)
    check_all_btn.Size = Drawing.Size(80, 25)
    check_all_btn.Anchor = WinForms.AnchorStyles.Bottom | WinForms.AnchorStyles.Left
    selection_group.Controls.Add(check_all_btn)

    check_none_btn = WinForms.Button()
    check_none_btn.Text = "Check None"
    check_none_btn.Location = Drawing.Point(340, 326)
    check_none_btn.Size = Drawing.Size(90, 25)
    check_none_btn.Anchor = WinForms.AnchorStyles.Bottom | WinForms.AnchorStyles.Left
    selection_group.Controls.Add(check_none_btn)

    invert_btn = WinForms.Button()
    invert_btn.Text = "Invert"
    invert_btn.Location = Drawing.Point(436, 326)
    invert_btn.Size = Drawing.Size(70, 25)
    invert_btn.Anchor = WinForms.AnchorStyles.Bottom | WinForms.AnchorStyles.Left
    selection_group.Controls.Add(invert_btn)

    template_label = WinForms.Label()
    template_label.Text = "Template:"
    template_label.Location = Drawing.Point(12, 362)
    template_label.AutoSize = True
    selection_group.Controls.Add(template_label)
    template_combo = WinForms.ComboBox()
    template_combo.DropDownStyle = WinForms.ComboBoxStyle.DropDownList
    template_combo.Location = Drawing.Point(80, 358)
    template_combo.Size = Drawing.Size(230, 22)
    for name in templates:
        template_combo.Items.Add(name)
    if template_combo.Items.Count > 0:
        template_combo.SelectedIndex = 0
    selection_group.Controls.Add(template_combo)

    printset_label = WinForms.Label()
    printset_label.Text = "Print set:"
    printset_label.Location = Drawing.Point(325, 362)
    printset_label.AutoSize = True
    selection_group.Controls.Add(printset_label)
    printset_combo = WinForms.ComboBox()
    printset_combo.DropDownStyle = WinForms.ComboBoxStyle.DropDownList
    printset_combo.Location = Drawing.Point(390, 358)
    printset_combo.Size = Drawing.Size(200, 22)
    for ps in print_sets:
        printset_combo.Items.Add(ps.Name)
    if printset_combo.Items.Count > 0:
        printset_combo.SelectedIndex = 0
    selection_group.Controls.Add(printset_combo)

    preview = WinForms.ListBox()
    preview.Location = Drawing.Point(12, 388)
    preview.Size = Drawing.Size(578, 78)
    preview.Anchor = (
        WinForms.AnchorStyles.Bottom
        | WinForms.AnchorStyles.Left
        | WinForms.AnchorStyles.Right
    )
    selection_group.Controls.Add(preview)

    warning_label = WinForms.Label()
    warning_label.Location = Drawing.Point(12, 474)
    warning_label.Size = Drawing.Size(578, 24)
    warning_label.ForeColor = Drawing.Color.DarkRed
    warning_label.Text = ""
    selection_group.Controls.Add(warning_label)

    settings_group = WinForms.GroupBox()
    settings_group.Text = "Print Set / PDF"
    settings_group.Location = Drawing.Point(630, 10)
    settings_group.Size = Drawing.Size(330, 300)
    settings_group.Anchor = WinForms.AnchorStyles.Top | WinForms.AnchorStyles.Right
    form.Controls.Add(settings_group)

    ps_name_label = WinForms.Label()
    ps_name_label.Text = "Print set name:"
    ps_name_label.Location = Drawing.Point(12, 26)
    ps_name_label.AutoSize = True
    settings_group.Controls.Add(ps_name_label)
    ps_name_box = WinForms.TextBox()
    ps_name_box.Location = Drawing.Point(120, 22)
    ps_name_box.Size = Drawing.Size(190, 22)
    ps_name_box.Text = "ViewPrintSet"
    settings_group.Controls.Add(ps_name_box)

    save_print_set_chk = WinForms.CheckBox()
    save_print_set_chk.Text = "Save / update Revit Print Set"
    save_print_set_chk.Location = Drawing.Point(12, 55)
    save_print_set_chk.Size = Drawing.Size(250, 24)
    save_print_set_chk.Checked = bool(config.get("last_save_print_set_enabled", True))
    settings_group.Controls.Add(save_print_set_chk)

    folder_label = WinForms.Label()
    folder_label.Text = "Output folder:"
    folder_label.Location = Drawing.Point(12, 86)
    folder_label.AutoSize = True
    settings_group.Controls.Add(folder_label)
    folder_box = WinForms.TextBox()
    folder_box.Location = Drawing.Point(12, 108)
    folder_box.Size = Drawing.Size(240, 22)
    folder_box.Text = get_default_output_folder(config)
    settings_group.Controls.Add(folder_box)
    browse_folder_btn = WinForms.Button()
    browse_folder_btn.Text = "Browse"
    browse_folder_btn.Location = Drawing.Point(258, 106)
    browse_folder_btn.Size = Drawing.Size(60, 25)
    settings_group.Controls.Add(browse_folder_btn)

    pdf_name_label = WinForms.Label()
    pdf_name_label.Text = "PDF file name:"
    pdf_name_label.Location = Drawing.Point(12, 142)
    pdf_name_label.AutoSize = True
    settings_group.Controls.Add(pdf_name_label)
    pdf_name_box = WinForms.TextBox()
    pdf_name_box.Location = Drawing.Point(12, 164)
    pdf_name_box.Size = Drawing.Size(298, 22)
    pdf_name_box.Text = "ViewPrintSet.pdf"
    settings_group.Controls.Add(pdf_name_box)

    advanced_btn = WinForms.Button()
    advanced_btn.Text = "Advanced..."
    advanced_btn.Location = Drawing.Point(12, 208)
    advanced_btn.Size = Drawing.Size(210, 28)
    settings_group.Controls.Add(advanced_btn)

    raster_matrix_btn = WinForms.Button()
    raster_matrix_btn.Text = "Run Raster Test Matrix"
    raster_matrix_btn.Location = Drawing.Point(12, 240)
    raster_matrix_btn.Size = Drawing.Size(210, 28)
    settings_group.Controls.Add(raster_matrix_btn)

    quality_group = WinForms.GroupBox()
    quality_group.Text = "Quality & Compression"
    quality_group.Location = Drawing.Point(970, 10)
    quality_group.Size = Drawing.Size(320, 542)
    quality_group.Anchor = WinForms.AnchorStyles.Top | WinForms.AnchorStyles.Right
    form.Controls.Add(quality_group)

    preset_label = WinForms.Label()
    preset_label.Text = "Compression preset:"
    preset_label.Location = Drawing.Point(12, 26)
    preset_label.AutoSize = True
    quality_group.Controls.Add(preset_label)
    preset_combo = WinForms.ComboBox()
    preset_combo.DropDownStyle = WinForms.ComboBoxStyle.DropDownList
    preset_combo.Location = Drawing.Point(12, 48)
    preset_combo.Size = Drawing.Size(292, 22)
    for item in [
        PRESET_BALANCED,
        PRESET_DETAIL,
        PRESET_SMALLER,
        PRESET_CUSTOM,
    ]:
        preset_combo.Items.Add(item)
    choose_combobox_value(
        preset_combo, config.get("last_compression_preset") or PRESET_DETAIL
    )
    quality_group.Controls.Add(preset_combo)

    apply_preset_btn = WinForms.Button()
    apply_preset_btn.Text = "Apply preset defaults"
    apply_preset_btn.Location = Drawing.Point(12, 74)
    apply_preset_btn.Size = Drawing.Size(160, 24)
    quality_group.Controls.Add(apply_preset_btn)

    engine_label = WinForms.Label()
    engine_label.Text = "Export engine:"
    engine_label.Location = Drawing.Point(12, 106)
    engine_label.AutoSize = True
    quality_group.Controls.Add(engine_label)
    engine_combo = WinForms.ComboBox()
    engine_combo.DropDownStyle = WinForms.ComboBoxStyle.DropDownList
    engine_combo.Location = Drawing.Point(12, 128)
    engine_combo.Size = Drawing.Size(292, 22)
    for item in [
        EXPORT_BACKEND_RECOMMENDED,
        EXPORT_BACKEND_NATIVE,
        EXPORT_BACKEND_PRINTMANAGER_CUSTOM,
    ]:
        engine_combo.Items.Add(item)
    choose_combobox_value(
        engine_combo, config.get("last_export_engine") or EXPORT_BACKEND_RECOMMENDED
    )
    quality_group.Controls.Add(engine_combo)

    engine_readonly_label = WinForms.Label()
    engine_readonly_label.Text = "Engine: Native Revit PDF Export"
    engine_readonly_label.Location = Drawing.Point(12, 128)
    engine_readonly_label.Size = Drawing.Size(292, 22)
    engine_readonly_label.ForeColor = Drawing.Color.DarkBlue
    quality_group.Controls.Add(engine_readonly_label)

    processing_label = WinForms.Label()
    processing_label.Text = "Vector output:"
    processing_label.Location = Drawing.Point(12, 162)
    processing_label.AutoSize = True
    quality_group.Controls.Add(processing_label)
    processing_combo = WinForms.ComboBox()
    processing_combo.DropDownStyle = WinForms.ComboBoxStyle.DropDownList
    processing_combo.Location = Drawing.Point(12, 184)
    processing_combo.Size = Drawing.Size(292, 22)
    processing_combo.Items.Add(PROCESSING_VECTOR)
    processing_combo.Items.Add(PROCESSING_RASTER)
    choose_combobox_value(
        processing_combo, config.get("last_processing_mode") or PROCESSING_VECTOR
    )
    quality_group.Controls.Add(processing_combo)

    rasterized_readonly_label = WinForms.Label()
    rasterized_readonly_label.Text = "Rasterized: Yes - review PDF"
    rasterized_readonly_label.Location = Drawing.Point(12, 184)
    rasterized_readonly_label.Size = Drawing.Size(292, 22)
    rasterized_readonly_label.ForeColor = Drawing.Color.DarkBlue
    quality_group.Controls.Add(rasterized_readonly_label)

    dpi_label = WinForms.Label()
    dpi_label.Text = "Native/vector export quality:"
    dpi_label.Location = Drawing.Point(12, 218)
    dpi_label.AutoSize = True
    quality_group.Controls.Add(dpi_label)
    dpi_combo = WinForms.ComboBox()
    dpi_combo.DropDownStyle = WinForms.ComboBoxStyle.DropDownList
    dpi_combo.Location = Drawing.Point(12, 240)
    dpi_combo.Size = Drawing.Size(130, 22)
    for item in NATIVE_DPI_VALUES:
        dpi_combo.Items.Add(item)
    choose_combobox_value(dpi_combo, config.get("last_native_dpi") or "DPI300")
    quality_group.Controls.Add(dpi_combo)

    raster_label = WinForms.Label()
    raster_label.Text = "Raster/image quality:"
    raster_label.Location = Drawing.Point(160, 218)
    raster_label.AutoSize = True
    quality_group.Controls.Add(raster_label)
    raster_combo = WinForms.ComboBox()
    raster_combo.DropDownStyle = WinForms.ComboBoxStyle.DropDownList
    raster_combo.Location = Drawing.Point(160, 240)
    raster_combo.Size = Drawing.Size(144, 22)
    for item in RASTER_QUALITY_VALUES:
        raster_combo.Items.Add(item)
    choose_combobox_value(raster_combo, config.get("last_raster_quality") or "High")
    quality_group.Controls.Add(raster_combo)

    color_label = WinForms.Label()
    color_label.Text = "Color mode:"
    color_label.Location = Drawing.Point(12, 274)
    color_label.AutoSize = True
    quality_group.Controls.Add(color_label)
    color_combo = WinForms.ComboBox()
    color_combo.DropDownStyle = WinForms.ComboBoxStyle.DropDownList
    color_combo.Location = Drawing.Point(12, 296)
    color_combo.Size = Drawing.Size(292, 22)
    for item in COLOR_MODE_VALUES:
        color_combo.Items.Add(item)
    choose_combobox_value(color_combo, config.get("last_color_mode") or "Color")
    quality_group.Controls.Add(color_combo)

    quality_note = WinForms.Label()
    quality_note.Location = Drawing.Point(12, 336)
    quality_note.Size = Drawing.Size(292, 105)
    quality_note.ForeColor = Drawing.Color.DarkBlue
    quality_note.Text = "For vector-heavy PDFs, DPI has limited impact. It mainly affects rasterized portions and native PDF export quality settings."
    quality_group.Controls.Add(quality_note)

    allow_raster_chk = WinForms.CheckBox()
    allow_raster_chk.Text = "Allow rasterized PDF profiles - review only"
    allow_raster_chk.Location = Drawing.Point(12, 454)
    allow_raster_chk.Size = Drawing.Size(292, 24)
    allow_raster_chk.Checked = bool(config.get("allow_raster_profiles", False))
    quality_group.Controls.Add(allow_raster_chk)
    allow_raster_chk.Visible = False

    page_group = WinForms.GroupBox()
    page_group.Text = "Page setup"
    page_group.Location = Drawing.Point(970, 560)
    page_group.Size = Drawing.Size(320, 160)
    page_group.Anchor = WinForms.AnchorStyles.Top | WinForms.AnchorStyles.Right
    form.Controls.Add(page_group)

    zoom_label = WinForms.Label()
    zoom_label.Text = "Zoom:"
    zoom_label.Location = Drawing.Point(12, 26)
    zoom_label.AutoSize = True
    page_group.Controls.Add(zoom_label)
    zoom_combo = WinForms.ComboBox()
    zoom_combo.DropDownStyle = WinForms.ComboBoxStyle.DropDownList
    zoom_combo.Location = Drawing.Point(90, 22)
    zoom_combo.Size = Drawing.Size(205, 22)
    for item in ZOOM_VALUES:
        zoom_combo.Items.Add(item)
    choose_combobox_value(zoom_combo, config.get("last_zoom") or "100%")
    page_group.Controls.Add(zoom_combo)

    placement_label = WinForms.Label()
    placement_label.Text = "Placement:"
    placement_label.Location = Drawing.Point(12, 58)
    placement_label.AutoSize = True
    page_group.Controls.Add(placement_label)
    placement_combo = WinForms.ComboBox()
    placement_combo.DropDownStyle = WinForms.ComboBoxStyle.DropDownList
    placement_combo.Location = Drawing.Point(90, 54)
    placement_combo.Size = Drawing.Size(205, 22)
    for item in PAPER_PLACEMENT_VALUES:
        placement_combo.Items.Add(item)
    choose_combobox_value(
        placement_combo, config.get("last_paper_placement") or "Center"
    )
    page_group.Controls.Add(placement_combo)

    paper_label = WinForms.Label()
    paper_label.Text = "Paper size:"
    paper_label.Location = Drawing.Point(12, 90)
    paper_label.AutoSize = True
    page_group.Controls.Add(paper_label)
    paper_combo = WinForms.ComboBox()
    paper_combo.DropDownStyle = WinForms.ComboBoxStyle.DropDownList
    paper_combo.Location = Drawing.Point(90, 86)
    paper_combo.Size = Drawing.Size(205, 22)
    paper_combo.Items.Add(PAPER_SIZE_AUTO)
    paper_combo.Items.Add(PAPER_SIZE_DEFAULT)
    for name in ISO_PAPER_SIZE_NAMES:
        paper_combo.Items.Add(name)
    paper_combo.Items.Add(PAPER_SIZE_CUSTOM)
    for name in get_printer_paper_size_names():
        if not paper_combo.Items.Contains(name):
            paper_combo.Items.Add(name)
    try:
        if PDFExportPaperFormat is not None:
            for name in System.Enum.GetNames(PDFExportPaperFormat):
                if not paper_combo.Items.Contains(str(name)):
                    paper_combo.Items.Add(str(name))
    except Exception:
        pass
    choose_combobox_value(paper_combo, config.get("last_paper_size") or PAPER_SIZE_AUTO)
    page_group.Controls.Add(paper_combo)

    orientation_label = WinForms.Label()
    orientation_label.Text = "Orientation:"
    orientation_label.Location = Drawing.Point(12, 122)
    orientation_label.AutoSize = True
    page_group.Controls.Add(orientation_label)
    orientation_combo = WinForms.ComboBox()
    orientation_combo.DropDownStyle = WinForms.ComboBoxStyle.DropDownList
    orientation_combo.Location = Drawing.Point(90, 118)
    orientation_combo.Size = Drawing.Size(205, 22)
    for item in ORIENTATION_VALUES:
        orientation_combo.Items.Add(item)
    choose_combobox_value(
        orientation_combo, config.get("last_orientation") or "Landscape"
    )
    page_group.Controls.Add(orientation_combo)

    full_bleed_chk = WinForms.CheckBox()
    full_bleed_chk.Text = "Prefer full bleed / no margins"
    full_bleed_chk.Location = Drawing.Point(12, 148)
    full_bleed_chk.Size = Drawing.Size(260, 22)
    full_bleed_chk.Checked = True
    page_group.Controls.Add(full_bleed_chk)

    zoom_note = WinForms.Label()
    zoom_note.Text = "100% preserves sheet scale. Match paper size to avoid margins."
    zoom_note.Location = Drawing.Point(12, 172)
    zoom_note.Size = Drawing.Size(292, 28)
    zoom_note.ForeColor = Drawing.Color.DarkBlue
    page_group.Controls.Add(zoom_note)

    bleed_note = WinForms.Label()
    bleed_note.Text = "Full bleed depends on the selected PDF printer paper definition."
    bleed_note.Location = Drawing.Point(12, 198)
    bleed_note.Size = Drawing.Size(292, 28)
    bleed_note.ForeColor = Drawing.Color.DarkBlue
    page_group.Controls.Add(bleed_note)

    page_group.Size = Drawing.Size(320, 230)

    preview_group = WinForms.GroupBox()
    preview_group.Text = "Paper Preview"
    preview_group.Location = Drawing.Point(10, 560)
    preview_group.Size = Drawing.Size(610, 160)
    preview_group.Anchor = (
        WinForms.AnchorStyles.Left
        | WinForms.AnchorStyles.Right
        | WinForms.AnchorStyles.Bottom
    )
    form.Controls.Add(preview_group)

    preview_panel = WinForms.Panel()
    preview_panel.Location = Drawing.Point(12, 22)
    preview_panel.Size = Drawing.Size(586, 126)
    preview_panel.Anchor = (
        WinForms.AnchorStyles.Top
        | WinForms.AnchorStyles.Bottom
        | WinForms.AnchorStyles.Left
        | WinForms.AnchorStyles.Right
    )
    preview_panel.BackColor = Drawing.Color.White
    preview_group.Controls.Add(preview_panel)

    opt_group = WinForms.GroupBox()
    opt_group.Text = "Post-export compression"
    opt_group.Location = Drawing.Point(630, 320)
    opt_group.Size = Drawing.Size(330, 334)
    opt_group.Anchor = WinForms.AnchorStyles.Top | WinForms.AnchorStyles.Right
    form.Controls.Add(opt_group)

    optimize_chk = WinForms.CheckBox()
    optimize_chk.Text = "Compress after export if available"
    optimize_chk.Location = Drawing.Point(12, 24)
    optimize_chk.Size = Drawing.Size(300, 24)
    optimize_chk.Checked = bool(config.get("pdf24_compressor_enabled", True))
    opt_group.Controls.Add(optimize_chk)

    compressor_engine_combo = WinForms.ComboBox()
    compressor_engine_combo.DropDownStyle = WinForms.ComboBoxStyle.DropDownList
    compressor_engine_combo.Location = Drawing.Point(12, 50)
    compressor_engine_combo.Size = Drawing.Size(95, 22)
    compressor_engine_combo.Items.Add(COMPRESS_ENGINE_PDF24)
    compressor_engine_combo.Items.Add(COMPRESS_ENGINE_QPDF)
    compressor_engine_combo.Items.Add(COMPRESS_ENGINE_NONE)
    choose_combobox_value(compressor_engine_combo, get_post_compressor_engine(config))
    opt_group.Controls.Add(compressor_engine_combo)

    pdf24_profile_combo = WinForms.ComboBox()
    pdf24_profile_combo.DropDownStyle = WinForms.ComboBoxStyle.DropDownList
    pdf24_profile_combo.Location = Drawing.Point(115, 50)
    pdf24_profile_combo.Size = Drawing.Size(195, 22)
    for item in [
        PDF24_PROFILE_BALANCED,
        PDF24_PROFILE_SMALLER,
        PDF24_PROFILE_MAXIMUM,
        PDF24_PROFILE_EXTREME,
        PDF24_PROFILE_CUSTOM,
    ]:
        pdf24_profile_combo.Items.Add(item)
    choose_combobox_value(pdf24_profile_combo, get_pdf24_profile(config))
    opt_group.Controls.Add(pdf24_profile_combo)

    pdf24_dpi_label = WinForms.Label()
    pdf24_dpi_label.Text = "DPI:"
    pdf24_dpi_label.Location = Drawing.Point(12, 82)
    pdf24_dpi_label.AutoSize = True
    opt_group.Controls.Add(pdf24_dpi_label)
    pdf24_dpi_num = WinForms.NumericUpDown()
    pdf24_dpi_num.Location = Drawing.Point(48, 78)
    pdf24_dpi_num.Size = Drawing.Size(60, 22)
    pdf24_dpi_num.Minimum = 72
    pdf24_dpi_num.Maximum = 300
    pdf24_dpi_num.Value = int(config.get("pdf24_dpi") or 144)
    opt_group.Controls.Add(pdf24_dpi_num)

    pdf24_quality_label = WinForms.Label()
    pdf24_quality_label.Text = "Quality %:"
    pdf24_quality_label.Location = Drawing.Point(118, 82)
    pdf24_quality_label.AutoSize = True
    opt_group.Controls.Add(pdf24_quality_label)
    pdf24_quality_num = WinForms.NumericUpDown()
    pdf24_quality_num.Location = Drawing.Point(185, 78)
    pdf24_quality_num.Size = Drawing.Size(55, 22)
    pdf24_quality_num.Minimum = 1
    pdf24_quality_num.Maximum = 100
    pdf24_quality_num.Value = int(config.get("pdf24_image_quality") or 75)
    opt_group.Controls.Add(pdf24_quality_num)

    pdf24_color_combo = WinForms.ComboBox()
    pdf24_color_combo.DropDownStyle = WinForms.ComboBoxStyle.DropDownList
    pdf24_color_combo.Location = Drawing.Point(248, 78)
    pdf24_color_combo.Size = Drawing.Size(62, 22)
    for item in ["Keep", "RGB", "Gray", "CMYK"]:
        pdf24_color_combo.Items.Add(item)
    color_model = config.get("pdf24_color_model") or "Keep"
    choose_combobox_value(pdf24_color_combo, color_model if color_model else "Keep")
    opt_group.Controls.Add(pdf24_color_combo)

    profile_combo = WinForms.ComboBox()
    profile_combo.DropDownStyle = WinForms.ComboBoxStyle.DropDownList
    profile_combo.Location = Drawing.Point(12, 108)
    profile_combo.Size = Drawing.Size(300, 22)
    profile_combo.Items.Add(QPDF_PROFILE_SAFE_LOSSLESS)
    profile_combo.Items.Add(QPDF_PROFILE_SMALLER_COLOR)
    choose_combobox_value(profile_combo, get_optimizer_profile(config))
    opt_group.Controls.Add(profile_combo)

    qpdf_path_label = WinForms.Label()
    qpdf_path_label.Text = "qpdf.exe:"
    qpdf_path_label.Location = Drawing.Point(12, 136)
    qpdf_path_label.AutoSize = True
    opt_group.Controls.Add(qpdf_path_label)

    qpdf_path_box = WinForms.TextBox()
    qpdf_path_box.Location = Drawing.Point(72, 134)
    qpdf_path_box.Size = Drawing.Size(240, 22)
    qpdf_path_box.Text = config.get("qpdf_exe_path") or ""
    opt_group.Controls.Add(qpdf_path_box)

    gs_status = WinForms.Label()
    gs_status.Location = Drawing.Point(12, 162)
    gs_status.Size = Drawing.Size(300, 38)
    gs_status.Text = get_pdf_optimizer_status_text()
    opt_group.Controls.Add(gs_status)

    browse_gs_btn = WinForms.Button()
    browse_gs_btn.Text = "Browse..."
    qpdf_note = WinForms.Label()
    qpdf_note.Text = "PDF24 can downsample/recompress. qpdf is optional cleanup only."
    qpdf_note.Location = Drawing.Point(12, 198)
    qpdf_note.Size = Drawing.Size(300, 34)
    qpdf_note.ForeColor = Drawing.Color.DarkBlue
    opt_group.Controls.Add(qpdf_note)

    browse_gs_btn.Location = Drawing.Point(12, 234)
    browse_gs_btn.Size = Drawing.Size(140, 28)
    opt_group.Controls.Add(browse_gs_btn)

    test_gs_btn = WinForms.Button()
    test_gs_btn.Text = "Test"
    test_gs_btn.Location = Drawing.Point(165, 234)
    test_gs_btn.Size = Drawing.Size(125, 28)
    opt_group.Controls.Add(test_gs_btn)

    optimize_existing_btn = WinForms.Button()
    optimize_existing_btn.Text = "Compress Existing PDF..."
    optimize_existing_btn.Location = Drawing.Point(12, 266)
    optimize_existing_btn.Size = Drawing.Size(178, 28)
    opt_group.Controls.Add(optimize_existing_btn)

    pdf24_matrix_btn = WinForms.Button()
    pdf24_matrix_btn.Text = "PDF24 Matrix..."
    pdf24_matrix_btn.Location = Drawing.Point(198, 266)
    pdf24_matrix_btn.Size = Drawing.Size(112, 28)
    opt_group.Controls.Add(pdf24_matrix_btn)

    flatten_existing_btn = WinForms.Button()
    flatten_existing_btn.Text = "Rasterize Existing PDF..."
    flatten_existing_btn.Location = Drawing.Point(12, 300)
    flatten_existing_btn.Size = Drawing.Size(178, 28)
    opt_group.Controls.Add(flatten_existing_btn)

    create_btn = WinForms.Button()
    create_btn.Text = "Create / Export"
    create_btn.Location = Drawing.Point(1040, 815)
    create_btn.Size = Drawing.Size(110, 32)
    create_btn.Anchor = WinForms.AnchorStyles.Bottom | WinForms.AnchorStyles.Right
    form.Controls.Add(create_btn)
    cancel_btn = WinForms.Button()
    cancel_btn.Text = "Cancel"
    cancel_btn.Location = Drawing.Point(1160, 815)
    cancel_btn.Size = Drawing.Size(90, 32)
    cancel_btn.Anchor = WinForms.AnchorStyles.Bottom | WinForms.AnchorStyles.Right
    form.Controls.Add(cancel_btn)

    selected_result = {"config": None}
    checked_ids = set()
    suppress_item_check = {"value": False}

    def view_sort_key(view):
        view_type = get_view_type_name(view)
        sheet_number = ""
        try:
            sheet_number = view.SheetNumber
        except Exception:
            sheet_number = ""
        try:
            name = view.Name
        except Exception:
            name = ""
        return (view_type, sheet_number, name)

    def add_view_list_item(view):
        view_type = get_view_type_name(view)
        try:
            view_id = view.Id.IntegerValue
        except Exception:
            view_id = -1
        try:
            name = view.Name
        except Exception:
            name = ""
        item = WinForms.ListViewItem(view_type)
        item.SubItems.Add(name)
        item.SubItems.Add(str(view_id))
        item.Tag = view_id
        item.Checked = view_id in checked_ids
        view_list.Items.Add(item)

    def refresh_manual_list():
        suppress_item_check["value"] = True
        view_list.Items.Clear()
        search = search_box.Text.lower()
        for view in sorted(available_views, key=view_sort_key):
            label = get_view_display_label(view)
            if not search or search in label.lower():
                add_view_list_item(view)
        suppress_item_check["value"] = False

    def get_manual_selected_views():
        selected = []
        for view in sorted(available_views, key=view_sort_key):
            try:
                if view.Id.IntegerValue in checked_ids:
                    selected.append(view)
            except Exception:
                pass
        return selected

    def set_visible_items_checked(only_selected, checked):
        suppress_item_check["value"] = True
        for item in view_list.Items:
            if (not only_selected) or item.Selected:
                item.Checked = checked
                try:
                    view_id = int(item.Tag)
                    if checked:
                        checked_ids.add(view_id)
                    elif view_id in checked_ids:
                        checked_ids.remove(view_id)
                except Exception:
                    pass
        suppress_item_check["value"] = False
        refresh_paper_preview()

    def invert_visible_items():
        suppress_item_check["value"] = True
        for item in view_list.Items:
            item.Checked = not item.Checked
            try:
                view_id = int(item.Tag)
                if item.Checked:
                    checked_ids.add(view_id)
                elif view_id in checked_ids:
                    checked_ids.remove(view_id)
            except Exception:
                pass
        suppress_item_check["value"] = False
        refresh_paper_preview()

    def on_view_item_checked(sender, args):
        if suppress_item_check["value"]:
            return
        try:
            view_id = int(args.Item.Tag)
            if args.Item.Checked:
                checked_ids.add(view_id)
            elif view_id in checked_ids:
                checked_ids.remove(view_id)
        except Exception:
            pass
        refresh_paper_preview()

    def get_current_source_mode():
        selected = str(source_combo.SelectedItem or "Manual view selection")
        if selected == "Load saved template":
            return "template"
        if selected == "Existing print set":
            return "existing"
        return "manual"

    def resolve_current_views():
        mode = get_current_source_mode()
        missing = []
        order_warning = False
        print_set = None
        if mode == "manual":
            return get_manual_selected_views(), missing, order_warning, print_set
        if mode == "template":
            views, missing = resolve_template_views(
                str(template_combo.SelectedItem or ""), available_views
            )
            return views, missing, order_warning, print_set
        name = str(printset_combo.SelectedItem or "")
        print_set = print_set_lookup.get(name)
        if print_set:
            views = []
            try:
                for view in print_set.Views:
                    views.append(view)
                order_warning = True
            except Exception:
                views = []
            return views, missing, order_warning, print_set
        return [], missing, order_warning, print_set

    def get_current_sheet_context():
        views, missing, order_warning, print_set = resolve_current_views()
        return get_sheet_size_context(views)

    def get_preview_paper_size_mm(sheet_context):
        selected_paper = str(paper_combo.SelectedItem or PAPER_SIZE_AUTO)
        resolved = resolve_requested_paper_size_name(selected_paper, sheet_context)
        dims = None
        label = resolved
        if resolved in ISO_PAPER_SIZES_MM:
            dims = ISO_PAPER_SIZES_MM[resolved]
        elif selected_paper == PAPER_SIZE_AUTO:
            first = sheet_context.get("first")
            if first and first.get("width_mm") and first.get("height_mm"):
                dims = (first.get("width_mm"), first.get("height_mm"))
                label = first.get("nearest_iso_name") or "Detected sheet"
        if dims is None:
            dims = ISO_PAPER_SIZES_MM.get("ISO A1")
            label = "Preview fallback ISO A1"
        orientation = str(orientation_combo.SelectedItem or "Landscape")
        if orientation == "Landscape" and dims[0] < dims[1]:
            dims = (dims[1], dims[0])
        elif orientation == "Portrait" and dims[0] > dims[1]:
            dims = (dims[1], dims[0])
        return label, dims

    def refresh_paper_preview():
        try:
            preview_panel.Invalidate()
        except Exception:
            pass

    def on_preview_paint(sender, args):
        graphics = args.Graphics
        rect = preview_panel.ClientRectangle
        graphics.Clear(Drawing.Color.White)
        sheet_context = get_current_sheet_context()
        paper_label_text, paper_dims = get_preview_paper_size_mm(sheet_context)
        first = sheet_context.get("first")
        if first and first.get("width_mm") and first.get("height_mm"):
            sheet_dims = (first.get("width_mm"), first.get("height_mm"))
            sheet_label = first.get("nearest_iso_name") or "Detected"
        else:
            sheet_dims = paper_dims
            sheet_label = "No sheet detected"
        margin = 10.0
        available_w = max(10.0, float(rect.Width) - 230.0)
        available_h = max(10.0, float(rect.Height) - 20.0)
        scale = min(
            available_w / max(1.0, paper_dims[0]),
            available_h / max(1.0, paper_dims[1]),
        )
        paper_w = paper_dims[0] * scale
        paper_h = paper_dims[1] * scale
        paper_x = margin
        paper_y = margin
        paper_rect = Drawing.Rectangle(
            int(paper_x), int(paper_y), int(paper_w), int(paper_h)
        )
        graphics.FillRectangle(Drawing.Brushes.WhiteSmoke, paper_rect)
        graphics.DrawRectangle(Drawing.Pens.DarkGray, paper_rect)
        sheet_w = sheet_dims[0] * scale
        sheet_h = sheet_dims[1] * scale
        placement = str(placement_combo.SelectedItem or "Center")
        if placement == "Center":
            sheet_x = paper_x + (paper_w - sheet_w) / 2.0
            sheet_y = paper_y + (paper_h - sheet_h) / 2.0
        else:
            sheet_x = paper_x + 4.0
            sheet_y = paper_y + 4.0
        sheet_rect = Drawing.Rectangle(
            int(sheet_x), int(sheet_y), int(sheet_w), int(sheet_h)
        )
        graphics.DrawRectangle(Drawing.Pens.RoyalBlue, sheet_rect)
        views_for_preview, _missing, _order_warning, _print_set = (
            resolve_current_views()
        )
        first_sheet_view = None
        for candidate_view in views_for_preview:
            if is_sheet_view(candidate_view):
                first_sheet_view = candidate_view
                break
        layout_info = get_sheet_layout_info(first_sheet_view)
        title_rect = layout_info.get("titleblock_rect")

        def draw_layout_rect(layout_rect, pen):
            if not layout_rect or not title_rect:
                return
            try:
                title_w = max(1.0, abs(title_rect["x2"] - title_rect["x1"]))
                title_h = max(1.0, abs(title_rect["y2"] - title_rect["y1"]))
                x1 = (
                    min(layout_rect["x1"], layout_rect["x2"])
                    - min(title_rect["x1"], title_rect["x2"])
                ) / title_w
                y1 = (
                    min(layout_rect["y1"], layout_rect["y2"])
                    - min(title_rect["y1"], title_rect["y2"])
                ) / title_h
                x2 = (
                    max(layout_rect["x1"], layout_rect["x2"])
                    - min(title_rect["x1"], title_rect["x2"])
                ) / title_w
                y2 = (
                    max(layout_rect["y1"], layout_rect["y2"])
                    - min(title_rect["y1"], title_rect["y2"])
                ) / title_h
                rx = sheet_x + x1 * sheet_w
                ry = sheet_y + (1.0 - y2) * sheet_h
                rw = max(1.0, (x2 - x1) * sheet_w)
                rh = max(1.0, (y2 - y1) * sheet_h)
                graphics.DrawRectangle(
                    pen,
                    Drawing.Rectangle(int(rx), int(ry), int(rw), int(rh)),
                )
            except Exception:
                pass

        if title_rect:
            graphics.DrawRectangle(Drawing.Pens.RoyalBlue, sheet_rect)
            viewport_area = 0.0
            for viewport_rect in layout_info.get("viewport_rects") or []:
                draw_layout_rect(viewport_rect, Drawing.Pens.ForestGreen)
                try:
                    viewport_area += abs(
                        viewport_rect["x2"] - viewport_rect["x1"]
                    ) * abs(viewport_rect["y2"] - viewport_rect["y1"])
                except Exception:
                    pass
            for schedule_rect in layout_info.get("schedule_rects") or []:
                draw_layout_rect(schedule_rect, Drawing.Pens.DarkOrange)
            try:
                title_area = abs(title_rect["x2"] - title_rect["x1"]) * abs(
                    title_rect["y2"] - title_rect["y1"]
                )
                viewport_percent = (
                    100.0 * viewport_area / title_area if title_area > 0 else None
                )
            except Exception:
                viewport_percent = None
        else:
            viewport_percent = None
        font = Drawing.Font("Segoe UI", 8.0)
        text_x = int(min(float(rect.Width) - 220.0, paper_x + paper_w + 15.0))
        status = "OK"
        if sheet_context.get("mixed"):
            status = "Mixed sheet sizes"
        elif not first_sheet_view:
            status = "Select a sheet to preview paper/layout"
        elif not title_rect:
            status = "Unknown sheet bounds"
        elif first and first.get("nearest_iso_name"):
            requested = resolve_requested_paper_size_name(
                str(paper_combo.SelectedItem or PAPER_SIZE_AUTO), sheet_context
            )
            if requested in ISO_PAPER_SIZES_MM and requested != first.get(
                "nearest_iso_name"
            ):
                status = "Paper mismatch"
        selected_count = len(views_for_preview or [])
        multi_note = ""
        if selected_count > 1:
            multi_note = " Previewing first selected sheet."
        lines = [
            "Paper: {} ({:.0f} x {:.0f} mm)".format(
                paper_label_text, paper_dims[0], paper_dims[1]
            ),
            "Sheet: {} ({:.0f} x {:.0f} mm)".format(
                sheet_label, sheet_dims[0], sheet_dims[1]
            ),
            "Orientation: {}".format(
                str(orientation_combo.SelectedItem or "Landscape")
            ),
            "Zoom: {}".format(str(zoom_combo.SelectedItem or "100%")),
            "Placement: {}".format(placement),
            "Status: {}{}".format(status, multi_note),
        ]
        lines.append(
            "Preview shows paper/layout only; it does not render actual Revit geometry."
        )
        if viewport_percent is not None:
            lines.append(
                "Viewport content occupies approx. {:.0f}% of sheet area".format(
                    viewport_percent
                )
            )
        lines.append(
            "100% prints the Revit sheet at true scale; adjust viewport scale/placement on the sheet to fill content."
        )
        for idx, line in enumerate(lines):
            graphics.DrawString(
                line,
                font,
                Drawing.Brushes.Black,
                float(text_x),
                float(12 + idx * 18),
            )

    def refresh_preview():
        mode = get_current_source_mode()
        manual = mode == "manual"
        template = mode == "template"
        existing = mode == "existing"
        view_list.Visible = manual
        check_selected_btn.Visible = manual
        uncheck_selected_btn.Visible = manual
        check_all_btn.Visible = manual
        check_none_btn.Visible = manual
        invert_btn.Visible = manual
        search_box.Visible = manual
        search_label.Visible = manual
        template_combo.Visible = template
        template_label.Visible = template
        printset_combo.Visible = existing
        printset_label.Visible = existing
        preview.Visible = not manual
        save_print_set_chk.Enabled = mode != "existing"
        if mode == "existing":
            save_print_set_chk.Checked = False
        views, missing, order_warning, print_set = resolve_current_views()
        add_preview_items(preview, views)
        messages = []
        if missing:
            messages.append("Missing template views: {}".format(", ".join(missing)))
        if order_warning:
            messages.append("Warning: API print set order may not be guaranteed.")
        warning_label.Text = " | ".join(messages)
        if mode == "existing" and print_set:
            ps_name_box.Text = print_set.Name
        if not pdf_name_box.Text:
            pdf_name_box.Text = sanitize_filename(ps_name_box.Text) + ".pdf"
        refresh_paper_preview()

    def on_source_changed(sender, args):
        refresh_preview()

    def on_search_changed(sender, args):
        refresh_manual_list()

    def on_template_changed(sender, args):
        refresh_preview()

    def on_printset_changed(sender, args):
        refresh_preview()

    def on_ps_name_changed(sender, args):
        if not pdf_name_box.Focused:
            pdf_name_box.Text = sanitize_filename(ps_name_box.Text) + ".pdf"

    def on_browse_folder(sender, args):
        chosen = browse_output_folder(folder_box.Text)
        if chosen:
            folder_box.Text = chosen

    applying_preset = {"value": False}

    def refresh_quality_state():
        engine = str(engine_combo.SelectedItem or EXPORT_BACKEND_RECOMMENDED)
        preset = str(preset_combo.SelectedItem or PRESET_DETAIL)
        dpi_combo.Enabled = True
        custom = preset == PRESET_CUSTOM
        engine_combo.Visible = custom
        processing_combo.Visible = custom
        engine_label.Visible = custom
        processing_label.Visible = custom
        engine_readonly_label.Visible = not custom
        rasterized_readonly_label.Visible = not custom
        engine_combo.Enabled = custom
        processing_combo.Enabled = custom
        if engine == EXPORT_BACKEND_PRINTMANAGER_CUSTOM:
            quality_note.Text = (
                "Raster/vector behavior may be controlled by the selected PDF printer. "
                "DPI may be controlled by the printer. Experimental: some settings may be ignored by the selected PDF printer."
            )
        elif engine == EXPORT_BACKEND_RECOMMENDED:
            quality_note.Text = "PDF24 may ask for Save As. Save into the selected output folder. Best quality compact baseline."
        elif str(processing_combo.SelectedItem or "") == PROCESSING_RASTER:
            quality_note.Text = "Lowest MB mode rasterizes sheets. Use for sharing/review; zoom/detail quality is reduced."
        else:
            quality_note.Text = "DPI and raster quality strongly affect rasterized export. Vector output may remain large."
        refresh_paper_preview()

    def refresh_compressor_state():
        engine = str(compressor_engine_combo.SelectedItem or COMPRESS_ENGINE_PDF24)
        pdf24_visible = engine == COMPRESS_ENGINE_PDF24
        qpdf_visible = engine == COMPRESS_ENGINE_QPDF
        pdf24_profile_combo.Visible = pdf24_visible
        pdf24_dpi_label.Visible = pdf24_visible
        pdf24_dpi_num.Visible = pdf24_visible
        pdf24_quality_label.Visible = pdf24_visible
        pdf24_quality_num.Visible = pdf24_visible
        pdf24_color_combo.Visible = pdf24_visible
        profile_combo.Visible = qpdf_visible
        qpdf_path_label.Visible = qpdf_visible
        qpdf_path_box.Visible = qpdf_visible
        if pdf24_visible:
            gs_status.Text = get_pdf_optimizer_status_text()
            browse_gs_btn.Text = "Browse PDF24"
            test_gs_btn.Text = "Test PDF24"
            profile = str(pdf24_profile_combo.SelectedItem or PDF24_PROFILE_BALANCED)
            if profile in PDF24_PROFILES:
                settings = PDF24_PROFILES[profile]
                pdf24_dpi_num.Value = int(settings["dpi"])
                pdf24_quality_num.Value = int(settings["quality"])
                choose_combobox_value(pdf24_color_combo, "Keep")
        elif qpdf_visible:
            found = find_qpdf_exe()
            gs_status.Text = (
                "qpdf found - {}".format(found)
                if found
                else "qpdf not found - cleanup will be skipped"
            )
            browse_gs_btn.Text = "Browse qpdf"
            test_gs_btn.Text = "Test qpdf"
        else:
            gs_status.Text = "Post-compression disabled."
            browse_gs_btn.Text = "Browse..."
            test_gs_btn.Text = "Test"

    def set_combo(combo, value):
        choose_combobox_value(combo, value)

    def apply_preset(preset):
        applying_preset["value"] = True
        if preset == PRESET_BALANCED:
            set_combo(engine_combo, EXPORT_BACKEND_NATIVE)
            set_combo(processing_combo, PROCESSING_RASTER)
            set_combo(dpi_combo, "DPI72")
            set_combo(raster_combo, "Low")
            set_combo(color_combo, "Color")
            set_combo(zoom_combo, "100%")
            set_combo(placement_combo, "Center")
            set_combo(orientation_combo, "Landscape")
            optimize_chk.Checked = False
            compressor_engine_combo.SelectedItem = COMPRESS_ENGINE_PDF24
            optimize_chk.Checked = True
            set_combo(pdf24_profile_combo, PDF24_PROFILE_SMALLER)
        elif preset == PRESET_DETAIL:
            set_combo(engine_combo, EXPORT_BACKEND_NATIVE)
            set_combo(processing_combo, PROCESSING_RASTER)
            set_combo(dpi_combo, "DPI72")
            set_combo(raster_combo, "High")
            set_combo(color_combo, "Color")
            set_combo(zoom_combo, "100%")
            set_combo(placement_combo, "Center")
            set_combo(orientation_combo, "Landscape")
            compressor_engine_combo.SelectedItem = COMPRESS_ENGINE_PDF24
            optimize_chk.Checked = True
            set_combo(pdf24_profile_combo, PDF24_PROFILE_SMALLER)
        elif preset == PRESET_SMALLER:
            set_combo(engine_combo, EXPORT_BACKEND_NATIVE)
            set_combo(processing_combo, PROCESSING_RASTER)
            set_combo(dpi_combo, "DPI72")
            set_combo(raster_combo, "Low")
            set_combo(color_combo, "Black Line")
            set_combo(zoom_combo, "100%")
            set_combo(placement_combo, "Center")
            set_combo(orientation_combo, "Landscape")
            optimize_chk.Checked = False
            compressor_engine_combo.SelectedItem = COMPRESS_ENGINE_PDF24
            optimize_chk.Checked = True
            set_combo(pdf24_profile_combo, PDF24_PROFILE_BALANCED)
        elif preset == PRESET_BLACKLINE_VECTOR:
            set_combo(engine_combo, EXPORT_BACKEND_RECOMMENDED)
            set_combo(processing_combo, PROCESSING_VECTOR)
            set_combo(dpi_combo, "DPI300")
            set_combo(raster_combo, "Medium")
            set_combo(color_combo, "Color")
            set_combo(zoom_combo, "100%")
            set_combo(placement_combo, "Center")
            set_combo(orientation_combo, "Landscape")
            optimize_chk.Checked = False
            set_combo(profile_combo, QPDF_PROFILE_SAFE_LOSSLESS)
        elif preset == PRESET_EXTREME:
            set_combo(engine_combo, EXPORT_BACKEND_NATIVE)
            set_combo(processing_combo, PROCESSING_VECTOR)
            set_combo(dpi_combo, "DPI300")
            set_combo(raster_combo, "Medium")
            set_combo(color_combo, "Color")
            set_combo(zoom_combo, "100%")
            set_combo(placement_combo, "Center")
            optimize_chk.Checked = False
            set_combo(profile_combo, QPDF_PROFILE_SAFE_LOSSLESS)
        elif preset == PRESET_BLACKLINE:
            set_combo(engine_combo, EXPORT_BACKEND_PRINTMANAGER_CUSTOM)
            set_combo(processing_combo, PROCESSING_VECTOR)
            set_combo(dpi_combo, "DPI300")
            set_combo(raster_combo, "Medium")
            set_combo(color_combo, "Color")
            set_combo(zoom_combo, "100%")
            set_combo(placement_combo, "Center")
            optimize_chk.Checked = False
            set_combo(profile_combo, QPDF_PROFILE_SAFE_LOSSLESS)
        applying_preset["value"] = False
        refresh_quality_state()

    def mark_custom(sender, args):
        if not applying_preset["value"]:
            set_combo(preset_combo, PRESET_CUSTOM)
        refresh_quality_state()

    def on_allow_raster_changed(sender, args):
        mark_custom(sender, args)

    def on_preset_changed(sender, args):
        preset = str(preset_combo.SelectedItem or PRESET_DETAIL)
        if preset != PRESET_CUSTOM:
            apply_preset(preset)
        refresh_quality_state()

    def on_apply_preset_defaults(sender, args):
        preset = str(preset_combo.SelectedItem or PRESET_DETAIL)
        if preset == PRESET_CUSTOM:
            return
        apply_preset(preset)

    def on_browse_gs(sender, args):
        engine = str(compressor_engine_combo.SelectedItem or COMPRESS_ENGINE_PDF24)
        selected = (
            browse_for_pdf24_exe()
            if engine == COMPRESS_ENGINE_PDF24
            else browse_for_qpdf_exe()
        )
        if selected:
            if engine == COMPRESS_ENGINE_PDF24:
                config["pdf24_exe_path"] = selected
            else:
                qpdf_path_box.Text = selected
                config["qpdf_exe_path"] = selected
            save_pdf_config(config)
            refresh_compressor_state()

    def on_test_gs(sender, args):
        engine = str(compressor_engine_combo.SelectedItem or COMPRESS_ENGINE_PDF24)
        if engine == COMPRESS_ENGINE_PDF24:
            gs_status.Text = test_pdf24_version()
        elif engine == COMPRESS_ENGINE_QPDF:
            qpdf_path = qpdf_path_box.Text.strip()
            if qpdf_path:
                config["qpdf_exe_path"] = qpdf_path
                save_pdf_config(config)
            gs_status.Text = test_qpdf_version()
        else:
            gs_status.Text = "Post-compression disabled."

    def on_optimize_existing(sender, args):
        engine = str(compressor_engine_combo.SelectedItem or COMPRESS_ENGINE_PDF24)
        if engine == COMPRESS_ENGINE_PDF24:
            input_pdf = choose_existing_pdf_file()
            result = None
            if input_pdf:
                profile = str(
                    pdf24_profile_combo.SelectedItem or PDF24_PROFILE_BALANCED
                )
                temp = {
                    "pdf24_profile": profile,
                    "pdf24_dpi": int(pdf24_dpi_num.Value),
                    "pdf24_image_quality": int(pdf24_quality_num.Value),
                    "pdf24_color_model": (
                        ""
                        if str(pdf24_color_combo.SelectedItem or "Keep") == "Keep"
                        else str(pdf24_color_combo.SelectedItem)
                    ),
                }
                profile, dpi, quality, color_model = get_pdf24_settings_from_config(
                    temp
                )
                exe = find_pdf24_doctool_exe()
                if exe:
                    compression = run_pdf24_compressor(
                        input_pdf, exe, profile, dpi, quality, color_model
                    )
                    result = (
                        "PDF24 compression complete.\nOriginal: {}\nCompressed: {}\nReduction: {}\nElapsed: {:.2f} seconds".format(
                            format_mb(compression.get("original_size_mb")),
                            format_mb(compression.get("optimized_size_mb")),
                            format_percent(compression.get("reduction_percent")),
                            compression.get("elapsed") or 0.0,
                        )
                        if compression
                        else "PDF24 compression failed."
                    )
                    if (
                        compression
                        and compression.get("success")
                        and compression.get("reduction_percent") is not None
                        and compression.get("reduction_percent") < 10.0
                    ):
                        result += (
                            "\n\nReduction is limited. This PDF is likely vector-heavy "
                            "or already compressed. Use Rasterize / Flatten Existing PDF "
                            "for a smaller review copy."
                        )
                else:
                    result = "PDF24 compressor not found."
        else:
            result = optimize_existing_pdf(
                qpdf_path_box.Text.strip(),
                str(profile_combo.SelectedItem or QPDF_PROFILE_SAFE_LOSSLESS),
            )
        if result:
            gs_status.Text = result

    def on_pdf24_matrix(sender, args):
        input_pdf = choose_existing_pdf_file()
        if input_pdf:
            run_pdf24_compression_matrix(input_pdf)

    def on_flatten_existing(sender, args):
        show_rasterize_existing_pdf_dialog()

    def on_advanced(sender, args):
        run_advanced_pdf_options()
        gs_status.Text = get_pdf_optimizer_status_text()

    def on_raster_matrix(sender, args):
        views, missing, order_warning, existing_print_set = resolve_current_views()
        if not views:
            forms.alert(
                "No views/sheets selected for raster test matrix.", exitscript=False
            )
            return
        print_set_name = ps_name_box.Text.strip() or "ViewPrintSet"
        output_folder = folder_box.Text.strip()
        sheet_context = get_sheet_size_context(views)
        run_raster_test_matrix(
            {
                "views": views,
                "print_set": existing_print_set,
                "print_set_name": print_set_name,
                "output_folder": output_folder,
                "paper_size": str(paper_combo.SelectedItem or PAPER_SIZE_AUTO),
                "full_bleed": bool(full_bleed_chk.Checked),
                "orientation": str(orientation_combo.SelectedItem or "Landscape"),
                "zoom": str(zoom_combo.SelectedItem or "100%"),
                "paper_placement": str(placement_combo.SelectedItem or "Center"),
                "sheet_size_context": sheet_context,
            }
        )

    def on_cancel(sender, args):
        form.DialogResult = WinForms.DialogResult.Cancel
        form.Close()

    def on_create(sender, args):
        views, missing, order_warning, existing_print_set = resolve_current_views()
        if not views:
            forms.alert("No views/sheets selected.", exitscript=False)
            return
        print_set_name = ps_name_box.Text.strip() or "ViewPrintSet"
        output_folder = folder_box.Text.strip()
        pdf_file_name = pdf_name_box.Text.strip() or (
            sanitize_filename(print_set_name) + ".pdf"
        )
        if not pdf_file_name.lower().endswith(".pdf"):
            pdf_file_name += ".pdf"
        export_pdf = True
        save_print_set = (
            bool(save_print_set_chk.Checked) and get_current_source_mode() != "existing"
        )
        if export_pdf:
            try:
                validate_output_folder(output_folder)
            except Exception as ex:
                forms.alert(
                    "Output folder is not writable:\n{}\n\n{}".format(
                        output_folder, ex
                    ),
                    exitscript=False,
                )
                return
        config["pdf_optimizer_enabled"] = bool(optimize_chk.Checked)
        config["pdf24_compressor_enabled"] = bool(optimize_chk.Checked)
        config["post_compressor_engine"] = str(
            compressor_engine_combo.SelectedItem or COMPRESS_ENGINE_PDF24
        )
        config["pdf24_profile"] = str(
            pdf24_profile_combo.SelectedItem or PDF24_PROFILE_BALANCED
        )
        config["pdf24_dpi"] = int(pdf24_dpi_num.Value)
        config["pdf24_image_quality"] = int(pdf24_quality_num.Value)
        config["pdf24_color_model"] = (
            ""
            if str(pdf24_color_combo.SelectedItem or "Keep") == "Keep"
            else str(pdf24_color_combo.SelectedItem)
        )
        config["qpdf_profile"] = str(
            profile_combo.SelectedItem or QPDF_PROFILE_SAFE_LOSSLESS
        )
        config["qpdf_exe_path"] = qpdf_path_box.Text.strip()
        config["last_output_folder"] = output_folder
        config["last_source_mode"] = get_current_source_mode()
        config["last_export_pdf_enabled"] = bool(export_pdf)
        config["last_save_print_set_enabled"] = bool(save_print_set)
        config["last_export_engine"] = str(
            engine_combo.SelectedItem or EXPORT_BACKEND_RECOMMENDED
        )
        config["last_processing_mode"] = str(
            processing_combo.SelectedItem or PROCESSING_VECTOR
        )
        config["last_native_dpi"] = str(dpi_combo.SelectedItem or "DPI300")
        config["last_raster_quality"] = str(raster_combo.SelectedItem or "Medium")
        config["last_color_mode"] = str(color_combo.SelectedItem or "Color")
        config["last_compression_preset"] = str(
            preset_combo.SelectedItem or PRESET_CUSTOM
        )
        config["last_zoom"] = str(zoom_combo.SelectedItem or "100%")
        config["last_paper_placement"] = str(placement_combo.SelectedItem or "Center")
        config["last_paper_size"] = str(paper_combo.SelectedItem or PAPER_SIZE_AUTO)
        config["last_orientation"] = str(orientation_combo.SelectedItem or "Landscape")
        config["allow_raster_profiles"] = bool(allow_raster_chk.Checked)
        save_pdf_config(config)
        sheet_context = get_sheet_size_context(views)
        selected_result["config"] = {
            "source_mode": get_current_source_mode(),
            "views": views,
            "print_set": existing_print_set,
            "print_set_name": print_set_name,
            "save_print_set": bool(save_print_set),
            "export_pdf": bool(export_pdf),
            "output_folder": output_folder,
            "pdf_file_name": pdf_file_name,
            "export_engine": str(
                engine_combo.SelectedItem or EXPORT_BACKEND_RECOMMENDED
            ),
            "compression_preset": str(preset_combo.SelectedItem or PRESET_CUSTOM),
            "processing_mode": str(processing_combo.SelectedItem or PROCESSING_VECTOR),
            "native_dpi": str(dpi_combo.SelectedItem or "DPI300"),
            "raster_quality": str(raster_combo.SelectedItem or "Medium"),
            "color_mode": str(color_combo.SelectedItem or "Color"),
            "zoom": str(zoom_combo.SelectedItem or "100%"),
            "paper_placement": str(placement_combo.SelectedItem or "Center"),
            "paper_size": str(paper_combo.SelectedItem or PAPER_SIZE_AUTO),
            "full_bleed": bool(full_bleed_chk.Checked),
            "orientation": str(orientation_combo.SelectedItem or "Landscape"),
            "sheet_size_context": sheet_context,
            "qpdf_enabled": bool(optimize_chk.Checked),
            "post_compressor_enabled": bool(optimize_chk.Checked),
            "post_compressor_engine": str(
                compressor_engine_combo.SelectedItem or COMPRESS_ENGINE_PDF24
            ),
            "pdf24_profile": str(
                pdf24_profile_combo.SelectedItem or PDF24_PROFILE_BALANCED
            ),
            "pdf24_dpi": int(pdf24_dpi_num.Value),
            "pdf24_image_quality": int(pdf24_quality_num.Value),
            "pdf24_color_model": (
                ""
                if str(pdf24_color_combo.SelectedItem or "Keep") == "Keep"
                else str(pdf24_color_combo.SelectedItem)
            ),
            "pdf24_exe_path": config.get("pdf24_exe_path") or "",
            "qpdf_profile": str(
                profile_combo.SelectedItem or QPDF_PROFILE_SAFE_LOSSLESS
            ),
            "qpdf_exe_path": qpdf_path_box.Text.strip(),
        }
        form.DialogResult = WinForms.DialogResult.OK
        form.Close()

    source_combo.SelectedIndexChanged += on_source_changed
    search_box.TextChanged += on_search_changed
    view_list.ItemChecked += on_view_item_checked
    check_selected_btn.Click += lambda sender, args: set_visible_items_checked(
        True, True
    )
    uncheck_selected_btn.Click += lambda sender, args: set_visible_items_checked(
        True, False
    )
    check_all_btn.Click += lambda sender, args: set_visible_items_checked(False, True)
    check_none_btn.Click += lambda sender, args: set_visible_items_checked(False, False)
    invert_btn.Click += lambda sender, args: invert_visible_items()
    template_combo.SelectedIndexChanged += on_template_changed
    printset_combo.SelectedIndexChanged += on_printset_changed
    ps_name_box.TextChanged += on_ps_name_changed
    browse_folder_btn.Click += on_browse_folder
    preset_combo.SelectedIndexChanged += on_preset_changed
    apply_preset_btn.Click += on_apply_preset_defaults
    engine_combo.SelectedIndexChanged += mark_custom
    processing_combo.SelectedIndexChanged += mark_custom
    dpi_combo.SelectedIndexChanged += mark_custom
    raster_combo.SelectedIndexChanged += mark_custom
    color_combo.SelectedIndexChanged += mark_custom
    zoom_combo.SelectedIndexChanged += mark_custom
    placement_combo.SelectedIndexChanged += mark_custom
    paper_combo.SelectedIndexChanged += mark_custom
    orientation_combo.SelectedIndexChanged += mark_custom
    allow_raster_chk.CheckedChanged += on_allow_raster_changed
    optimize_chk.CheckedChanged += mark_custom
    profile_combo.SelectedIndexChanged += mark_custom
    compressor_engine_combo.SelectedIndexChanged += (
        lambda sender, args: refresh_compressor_state()
    )
    pdf24_profile_combo.SelectedIndexChanged += (
        lambda sender, args: refresh_compressor_state()
    )
    full_bleed_chk.CheckedChanged += mark_custom
    preview_panel.Paint += on_preview_paint
    browse_gs_btn.Click += on_browse_gs
    test_gs_btn.Click += on_test_gs
    optimize_existing_btn.Click += on_optimize_existing
    pdf24_matrix_btn.Click += on_pdf24_matrix
    flatten_existing_btn.Click += on_flatten_existing
    advanced_btn.Click += on_advanced
    raster_matrix_btn.Click += on_raster_matrix
    create_btn.Click += on_create
    cancel_btn.Click += on_cancel
    form.AcceptButton = create_btn
    form.CancelButton = cancel_btn

    refresh_manual_list()
    refresh_preview()
    if str(preset_combo.SelectedItem or PRESET_DETAIL) != PRESET_CUSTOM:
        apply_preset(str(preset_combo.SelectedItem or PRESET_DETAIL))
    refresh_quality_state()
    refresh_compressor_state()
    result = form.ShowDialog()
    if result == WinForms.DialogResult.OK:
        return selected_result["config"]
    return None


def run_advanced_pdf_options():
    while True:
        action = forms.alert(
            "Advanced PDF Export Options",
            options=[
                ADVANCED_PRINTER,
                ADVANCED_NATIVE,
                PRESET_BLACKLINE_VECTOR,
                ADVANCED_DETECTION,
                ADVANCED_BACK,
            ],
            exitscript=False,
        )
        if not action or action == ADVANCED_BACK:
            return
        if action == ADVANCED_DETECTION:
            forms.alert(
                "Detailed PDF detection logging is enabled for export runs.",
                exitscript=False,
            )
            continue

        print_set = choose_existing_print_set()
        selected_views = extract_views_from_print_set(print_set)
        if not selected_views:
            forms.alert("Selected print set has no printable views.", exitscript=True)
        backend = EXPORT_BACKEND_PRINTMANAGER_CUSTOM
        export_settings = {}
        if action == ADVANCED_NATIVE:
            backend = EXPORT_BACKEND_NATIVE
            export_settings = {
                "compression_preset": ADVANCED_NATIVE,
                "processing_mode": PROCESSING_VECTOR,
                "native_dpi": "DPI300",
                "raster_quality": "Medium",
                "color_mode": "Color",
                "qpdf_enabled": False,
            }
        elif action == PRESET_BLACKLINE_VECTOR:
            backend = EXPORT_BACKEND_RECOMMENDED
            export_settings = {
                "compression_preset": PRESET_BLACKLINE_VECTOR,
                "processing_mode": PROCESSING_VECTOR,
                "native_dpi": "DPI300",
                "raster_quality": "Medium",
                "color_mode": "Color",
                "qpdf_enabled": False,
            }
        export_views_to_pdf(
            print_set.Name,
            selected_views,
            print_set,
            backend=backend,
            export_settings=export_settings,
        )
        return


def choose_printmanager_profile():
    profile = forms.SelectFromList.show(
        [PRINTMANAGER_PROFILE_CURRENT],
        title="Select PrintManager Profile",
        multiselect=False,
    )
    if not profile:
        forms.alert(
            "No PrintManager profile selected. Export cancelled.", exitscript=True
        )
    logger.info("selected profile: {}".format(profile))
    return profile


def get_installed_printer_names():
    names = []
    try:
        if Printing is not None:
            for printer_name in Printing.PrinterSettings.InstalledPrinters:
                names.append(str(printer_name))
    except Exception as ex:
        logger.warning("Could not read installed printers: {}".format(ex))
    return names


def choose_pdf_printer(print_manager):
    current = ""
    try:
        current = print_manager.PrinterName or ""
    except Exception:
        current = ""
    options = []
    if current:
        options.append("Use current Revit printer: {}".format(current))
    for name in get_installed_printer_names():
        if name and name not in options:
            options.append(name)
    if not options:
        logger.warning("No installed printers discovered; using current Revit printer.")
        return current
    chosen = forms.SelectFromList.show(
        options,
        title="Select PDF Printer",
        multiselect=False,
    )
    if not chosen:
        forms.alert("No PDF printer selected. Export cancelled.", exitscript=True)
    if chosen.startswith("Use current Revit printer:"):
        return current
    return chosen


def safe_set_print_option(obj, prop_name, value):
    try:
        setattr(obj, prop_name, value)
        logger.info("PrintManager option set: {} = {}".format(prop_name, value))
        return True
    except Exception as ex:
        warn_once(
            _MISSING_PRINT_OPTIONS_WARNED,
            prop_name,
            "PrintManager option {} unavailable: {}".format(prop_name, ex),
        )
        return False


def get_print_parameters(print_manager):
    try:
        return print_manager.PrintSetup.CurrentPrintSetting.PrintParameters
    except Exception as ex:
        logger.warning("PrintParameters unavailable: {}".format(ex))
    return None


def safe_set_print_parameter_enum(params, prop_name, enum_type, member_name):
    if params is None:
        return False
    if enum_type is None:
        logger.warning(
            "PrintParameters option {} skipped: enum unavailable".format(prop_name)
        )
        return False
    try:
        value = System.Enum.Parse(enum_type, member_name)
        setattr(params, prop_name, value)
        logger.info(
            "PrintParameters option set: {} = {}".format(prop_name, member_name)
        )
        return True
    except Exception as ex:
        warn_once(
            _MISSING_PRINT_OPTIONS_WARNED,
            prop_name,
            "PrintParameters option {} skipped: {}".format(prop_name, ex),
        )
        return False


def safe_set_print_parameter_bool(params, prop_name, value):
    if params is None:
        return False
    try:
        setattr(params, prop_name, value)
        logger.info("PrintParameters option set: {} = {}".format(prop_name, value))
        return True
    except Exception as ex:
        warn_once(
            _MISSING_PRINT_OPTIONS_WARNED,
            prop_name,
            "PrintParameters option {} skipped: {}".format(prop_name, ex),
        )
        return False


def match_printer_paper_size(print_manager, requested_name, prefer_full_bleed):
    requested_key = normalize_enum_name(requested_name)
    requested_iso = requested_key.replace("ISO", "")
    best = None
    try:
        paper_sizes = print_manager.PaperSizes
        for paper_size in paper_sizes:
            try:
                name = str(paper_size.Name)
            except Exception:
                continue
            name_key = normalize_enum_name(name)
            if requested_key and requested_key in name_key:
                if prefer_full_bleed and (
                    "FULLBLEED" in name_key
                    or "BORDERLESS" in name_key
                    or "NOMARGIN" in name_key
                ):
                    return paper_size
                if best is None:
                    best = paper_size
            elif requested_iso and requested_iso in name_key:
                if prefer_full_bleed and (
                    "FULLBLEED" in name_key
                    or "BORDERLESS" in name_key
                    or "NOMARGIN" in name_key
                ):
                    return paper_size
                if best is None:
                    best = paper_size
    except Exception as ex:
        logger.warning("PrintManager paper-size matching unavailable: {}".format(ex))
    return best


def apply_printmanager_custom_quality(print_manager, export_settings):
    params = get_print_parameters(print_manager)
    if params is None:
        return
    raster_quality = export_settings.get("raster_quality") or "Medium"
    color_mode = export_settings.get("color_mode") or "Color"
    zoom_value = export_settings.get("zoom") or "100%"
    placement = export_settings.get("paper_placement") or "Center"
    orientation = export_settings.get("orientation") or "Landscape"
    paper_size = export_settings.get("paper_size") or PAPER_SIZE_AUTO
    sheet_context = export_settings.get("sheet_size_context") or {}
    resolved_paper_size = resolve_requested_paper_size_name(paper_size, sheet_context)
    resolved_orientation, orientation_warning = resolve_orientation_value(
        orientation, sheet_context
    )
    if orientation_warning:
        logger.warning(orientation_warning)
    logger.info("PrintManager selected paper size: {}".format(paper_size))
    logger.info("PrintManager resolved paper size: {}".format(resolved_paper_size))
    safe_set_print_parameter_enum(
        params, "RasterQuality", RasterQualityType, raster_quality
    )
    safe_set_print_parameter_enum(
        params, "ColorDepth", ColorDepthType, get_color_depth_member(color_mode)
    )
    if zoom_value == "Fit to page":
        safe_set_print_parameter_enum(params, "ZoomType", ZoomType, "FitToPage")
    else:
        safe_set_print_parameter_enum(params, "ZoomType", ZoomType, "Zoom")
        safe_set_print_parameter_bool(params, "Zoom", 100)
    safe_set_print_parameter_enum(
        params,
        "PaperPlacement",
        PDFExportPaperPlacement,
        get_paper_placement_member(placement),
    )
    safe_set_print_parameter_enum(
        params,
        "PageOrientation",
        PDFExportPaperOrientation,
        get_orientation_member(resolved_orientation),
    )
    if resolved_paper_size and resolved_paper_size not in [
        PAPER_SIZE_DEFAULT,
        PAPER_SIZE_CUSTOM,
    ]:
        matched_paper = match_printer_paper_size(
            print_manager, resolved_paper_size, bool(export_settings.get("full_bleed"))
        )
        if matched_paper:
            safe_set_print_parameter_bool(params, "PaperSize", matched_paper)
            logger.info(
                "PrintManager matched printer paper size: {}".format(matched_paper)
            )
        else:
            logger.warning(
                "Selected paper size not found for current PDF printer. Current printer setting will be used: {}".format(
                    resolved_paper_size
                )
            )
    for prop_name in [
        "HideCropBoundaries",
        "HideReforWorkPlanes",
        "HideScopeBoxes",
        "HideUnreferencedViewTags",
        "MaskCoincidentLines",
        "ReplaceHalftoneWithThinLines",
    ]:
        safe_set_print_parameter_bool(params, prop_name, True)
    safe_set_print_parameter_bool(params, "ViewLinksInBlue", False)
    logger.info(
        "PrintManager custom quality requested. Raster/vector behavior may be controlled by the selected PDF printer."
    )


def configure_printmanager_for_export(
    print_manager,
    print_set,
    selected_views,
    pdf_path,
    printer_name,
    choose_printer,
    export_settings,
):
    result = {
        "success": False,
        "setup_duration": 0.0,
        "apply_duration": 0.0,
        "filename_set": False,
        "assigned_view_sheet_set": False,
        "combined_set": False,
        "print_to_file_set": False,
    }
    t = Transaction(doc, "BIM3DNA Configure PrintManager PDF Export")
    started = False
    setup_start = time.time()
    try:
        t.Start()
        started = True
        if choose_printer and printer_name:
            try:
                print_manager.SelectNewPrintDriver(printer_name)
            except Exception as ex:
                logger.warning(
                    "Could not select printer '{}': {}".format(printer_name, ex)
                )
        print_manager.PrintRange = PrintRange.Select
        view_sheet_setting = print_manager.ViewSheetSetting
        if print_set is not None:
            view_sheet_setting.CurrentViewSheetSet = print_set
            result["assigned_view_sheet_set"] = True
            logger.info(
                "PRINT SET ASSIGNED -> name={}, view count={}".format(
                    print_set.Name, get_view_count_from_print_set(print_set)
                )
            )
        else:
            logger.warning("No saved ViewSheetSet supplied for PrintManager export.")
            result["assigned_view_sheet_set"] = False
        logger.info(
            "CurrentViewSheetSet assigned successfully: {}".format(
                result["assigned_view_sheet_set"]
            )
        )
        result["combined_set"] = safe_set_print_option(
            print_manager, "CombinedFile", True
        )
        result["print_to_file_set"] = safe_set_print_option(
            print_manager, "PrintToFile", True
        )
        result["filename_set"] = safe_set_print_option(
            print_manager, "PrintToFileName", pdf_path
        )
        logger.info(
            "PrintManager CombinedFile set successfully: {}".format(
                result["combined_set"]
            )
        )
        logger.info(
            "PrintManager PrintToFile set successfully: {}".format(
                result["print_to_file_set"]
            )
        )
        logger.info(
            "PrintManager PrintToFileName set successfully: {}".format(
                result["filename_set"]
            )
        )
        apply_printmanager_custom_quality(print_manager, export_settings)
        apply_start = time.time()
        try:
            print_manager.Apply()
            result["apply_duration"] = time.time() - apply_start
        except Exception as ex:
            result["apply_duration"] = time.time() - apply_start
            logger.warning("PrintManager Apply failed or unavailable: {}".format(ex))
        t.Commit()
        started = False
        result["setup_duration"] = time.time() - setup_start
        result["success"] = True
        return result
    except Exception:
        if started:
            try:
                t.RollBack()
            except Exception:
                pass
        result["setup_duration"] = time.time() - setup_start
        logger.exception("PrintManager configuration failed")
        return result


def export_views_native(
    print_set_name, selected_views, output_folder, pdf_file_name, export_settings=None
):
    export_settings = export_settings or {}
    profile = export_settings.get("compression_preset") or "Native API Controlled"
    pdf_path = os.path.join(output_folder, pdf_file_name)
    options = build_pdf_export_options(
        profile, pdf_file_name, combine=True, export_settings=export_settings
    )
    view_ids = build_view_id_list(selected_views)
    detection_folders = get_pdf_detection_folders(output_folder)
    before = snapshot_pdf_folders(detection_folders, log_before=True)
    start = time.time()
    try:
        doc.Export(output_folder, view_ids, options)
    except FileAccessException as ex:
        logger.exception("PDF export failed with file access error")
        forms.alert(
            "PDF export failed. The target PDF may be open or locked:\n{}\n\n{}".format(
                pdf_path, ex
            ),
            exitscript=False,
        )
        return profile, time.time() - start, []
    except InvalidPathArgumentException as ex:
        logger.exception("PDF export failed with invalid path")
        forms.alert(
            "PDF export failed because the output path is invalid:\n{}\n\n{}".format(
                output_folder, ex
            ),
            exitscript=False,
        )
        return profile, time.time() - start, []
    except System.ArgumentException as ex:
        logger.exception("PDF export failed with invalid views/options")
        forms.alert(
            "PDF export failed because one or more views/options are invalid:\n\n{}".format(
                ex
            ),
            exitscript=False,
        )
        return profile, time.time() - start, []
    except Exception as ex:
        logger.exception("PDF export failed")
        forms.alert("PDF export failed:\n\n{}".format(ex), exitscript=False)
        return profile, time.time() - start, []
    elapsed = time.time() - start
    if os.path.exists(pdf_path) and get_file_size_mb(pdf_path) is not None:
        actual_pdf = pdf_path
    else:
        actual_pdf = detect_generated_pdf(
            pdf_path, output_folder, detection_folders, start, 60.0, before
        )
    generated = [actual_pdf] if actual_pdf else []
    return profile, elapsed, generated


def export_views_printmanager(
    print_set_name,
    selected_views,
    output_folder,
    pdf_file_name,
    print_set,
    choose_printer,
    export_settings=None,
    custom_quality=False,
):
    export_settings = export_settings or {}
    profile = PRINTMANAGER_PROFILE_CURRENT
    pdf_path = os.path.join(output_folder, pdf_file_name)
    print_manager = doc.PrintManager
    logger.info("selected profile: {}".format(profile))
    if choose_printer:
        printer_name = choose_pdf_printer(print_manager)
    else:
        try:
            printer_name = print_manager.PrinterName or ""
        except Exception:
            printer_name = ""
    logger.info("selected printer name: {}".format(printer_name))
    logger.info("print set view/sheet count: {}".format(len(selected_views)))
    logger.info("SELECTED VIEWS -> count={}".format(len(selected_views)))
    save_permanent = bool(export_settings.get("save_print_set"))
    assigned_print_set = ensure_printmanager_view_sheet_set(
        selected_views,
        print_set_name,
        save_permanent,
        existing_print_set=print_set,
    )
    if (
        assigned_print_set is None
        or get_view_count_from_print_set(assigned_print_set) <= 0
    ):
        forms.alert(
            "PrintManager has no active views/sheets. BIM3DNA could not assign the print set.",
            exitscript=False,
        )
        return profile, 0.0, []
    detection_folders = get_pdf_detection_folders(output_folder)
    logger.info("PDF output detection folders: {}".format(", ".join(detection_folders)))
    before = snapshot_pdf_folders(detection_folders, log_before=True)
    total_start = time.time()
    apply_duration = 0.0
    submit_duration = 0.0
    detection_duration = 0.0
    setup_duration = 0.0
    filename_set = False
    try:
        configure_result = configure_printmanager_for_export(
            print_manager,
            assigned_print_set,
            selected_views,
            pdf_path,
            printer_name,
            choose_printer,
            export_settings,
        )
        setup_duration = configure_result.get("setup_duration") or 0.0
        apply_duration = configure_result.get("apply_duration") or 0.0
        filename_set = bool(configure_result.get("filename_set"))
        if not configure_result.get("success"):
            forms.alert(
                "PrintManager configuration failed. No PDF export was started.",
                exitscript=False,
            )
            return profile, time.time() - total_start, []
        submit_start = time.time()
        try:
            print_manager.SubmitPrint()
            submit_duration = time.time() - submit_start
        except Exception as submit_ex:
            submit_duration = time.time() - submit_start
            logger.exception("PrintManager SubmitPrint failed")
            try:
                logger.warning(
                    "PRINTMANAGER FAILURE -> PrintRange={}".format(
                        print_manager.PrintRange
                    )
                )
                current_set = print_manager.ViewSheetSetting.CurrentViewSheetSet
                logger.warning(
                    "PRINTMANAGER FAILURE -> CurrentViewSheetSet={} views={}".format(
                        current_set.Name, get_view_count_from_print_set(current_set)
                    )
                )
            except Exception:
                pass
            forms.alert(
                "PrintManager has no active views/sheets. BIM3DNA could not assign the print set.\n\n{}".format(
                    submit_ex
                ),
                exitscript=False,
            )
            return profile, time.time() - total_start, []
        if not filename_set:
            logger.warning(
                "The selected PDF printer may require manual filename confirmation."
            )
    except Exception as ex:
        logger.exception("PrintManager PDF export failed")
        forms.alert(
            "PrintManager PDF export failed:\n\n{}".format(ex), exitscript=False
        )
        return profile, time.time() - total_start, []
    detection_start = time.time()
    timeout_seconds = 300.0
    try:
        timeout_seconds = float(
            export_settings.get("pdf_detection_timeout_seconds") or 300.0
        )
    except Exception:
        timeout_seconds = 300.0
    actual_pdf = detect_generated_pdf(
        pdf_path,
        output_folder,
        detection_folders,
        total_start,
        timeout_seconds,
        before,
    )
    generated = [actual_pdf] if actual_pdf else []
    detection_duration = time.time() - detection_start
    elapsed = time.time() - total_start
    logger.info("PRINTMANAGER TIMING -> setup: {:.1f}s".format(setup_duration))
    logger.info("PRINTMANAGER TIMING -> apply: {:.1f}s".format(apply_duration))
    logger.info("PRINTMANAGER TIMING -> submit_print: {:.1f}s".format(submit_duration))
    logger.info(
        "PRINTMANAGER TIMING -> output_detection: {:.1f}s".format(detection_duration)
    )
    logger.info("PRINTMANAGER TIMING -> total: {:.1f}s".format(elapsed))
    if generated and generated[0] != pdf_path:
        logger.warning(
            "Requested PDF path was not created directly; detected actual PDF instead: {}".format(
                generated[0]
            )
        )
    elif not generated:
        logger.warning(
            "PrintManager finished, but no PDF was detected. Requested path: {}. Searched folders: {}".format(
                pdf_path, ", ".join(detection_folders)
            )
        )
    return profile, elapsed, generated


def export_views_to_pdf(
    print_set_name,
    selected_views,
    print_set=None,
    backend=None,
    output_folder=None,
    pdf_file_name=None,
    export_settings=None,
):
    export_settings = export_settings or {}
    if backend is None:
        backend = export_settings.get("export_engine") or EXPORT_BACKEND_RECOMMENDED
    if output_folder is None:
        output_folder = choose_output_folder()
    sanitized = sanitize_filename(print_set_name)
    if not pdf_file_name:
        pdf_file_name = sanitized + ".pdf"
    if not pdf_file_name.lower().endswith(".pdf"):
        pdf_file_name += ".pdf"
    requested_path = get_unique_pdf_path(os.path.join(output_folder, pdf_file_name))
    pdf_file_name = os.path.basename(requested_path)

    logger.info("print set name: {}".format(print_set_name))
    logger.info("view/sheet count: {}".format(len(selected_views)))
    logger.info("requested output path: {}".format(requested_path))
    logger.info("output filename: {}".format(pdf_file_name))

    logger.info(
        "selected preset: {}".format(export_settings.get("compression_preset") or "N/A")
    )
    logger.info("export engine: {}".format(backend))
    logger.info(
        "processing mode: {}".format(export_settings.get("processing_mode") or "N/A")
    )
    logger.info("native DPI: {}".format(export_settings.get("native_dpi") or "N/A"))
    logger.info(
        "raster quality: {}".format(export_settings.get("raster_quality") or "N/A")
    )
    logger.info("color mode: {}".format(export_settings.get("color_mode") or "N/A"))
    logger.info("zoom: {}".format(export_settings.get("zoom") or "N/A"))
    logger.info(
        "paper placement: {}".format(export_settings.get("paper_placement") or "N/A")
    )
    logger.info("paper size: {}".format(export_settings.get("paper_size") or "N/A"))
    logger.info("orientation: {}".format(export_settings.get("orientation") or "N/A"))
    sheet_context = export_settings.get("sheet_size_context") or {}
    first_sheet = sheet_context.get("first")
    if first_sheet:
        logger.info(
            "detected sheet size: {} {:.1f} x {:.1f} mm confidence={}".format(
                first_sheet.get("nearest_iso_name") or "unknown",
                first_sheet.get("width_mm") or 0.0,
                first_sheet.get("height_mm") or 0.0,
                first_sheet.get("confidence") or "unknown",
            )
        )
    if sheet_context.get("mixed"):
        logger.warning("selected sheets contain mixed paper sizes")
    logger.info("qpdf enabled: {}".format(bool(export_settings.get("qpdf_enabled"))))
    logger.info("qpdf profile: {}".format(export_settings.get("qpdf_profile") or "N/A"))
    logger.info(
        "qpdf path: {}".format(export_settings.get("qpdf_exe_path") or "<auto-detect>")
    )

    if backend == EXPORT_BACKEND_NATIVE:
        profile, elapsed, generated = export_views_native(
            print_set_name,
            selected_views,
            output_folder,
            pdf_file_name,
            export_settings,
        )
    else:
        choose_printer = backend == EXPORT_BACKEND_ADVANCED_PRINTER
        custom_quality = backend == EXPORT_BACKEND_PRINTMANAGER_CUSTOM
        profile, elapsed, generated = export_views_printmanager(
            print_set_name,
            selected_views,
            output_folder,
            pdf_file_name,
            print_set,
            choose_printer,
            export_settings,
            custom_quality,
        )

    if not generated:
        searched_folders = get_pdf_detection_folders(output_folder)
        if backend == EXPORT_BACKEND_NATIVE:
            failed_text = "Native Revit PDF export finished, but no PDF was detected."
            choices = ["Continue without optimization", "Cancel"]
        else:
            failed_text = "PDF printer finished, but BIM3DNA could not detect the PDF automatically. Did the PDF get created?"
            choices = [
                "Select PDF",
                "Skip qpdf",
                "Cancel",
            ]
        selected_action = forms.alert(
            "{}\n\nRequested path:\n{}\n\nSearched folders:\n{}".format(
                failed_text, requested_path, "\n".join(searched_folders)
            ),
            options=choices,
            exitscript=False,
        )
        if selected_action == "Select PDF":
            selected_pdf = choose_created_pdf_file()
            if selected_pdf:
                generated = [selected_pdf]
                logger.info(
                    "User selected created PDF manually: {}".format(selected_pdf)
                )
            else:
                return
        else:
            return

    logger.info("elapsed time: {:.2f}".format(elapsed))
    logger.info("actual generated output paths: {}".format(", ".join(generated)))
    for path in generated:
        size_mb = get_file_size_mb(path)
        logger.info(
            "file size MB: {} {}".format(
                path, "{:.2f}".format(size_mb) if size_mb is not None else "unavailable"
            )
        )

    optimization_result = run_post_compression(generated, export_settings)
    optimization_elapsed = 0.0
    if optimization_result:
        optimization_elapsed = optimization_result.get("elapsed") or 0.0
    total_elapsed = elapsed + optimization_elapsed

    summary_backend = backend
    summary_profile = profile
    note = ""
    if backend == EXPORT_BACKEND_RECOMMENDED:
        summary_backend = "Compact PDF - Current Revit Print Setup"
        summary_profile = "Compact Color PDF"
        note = (
            "\n- Uses the same Revit print pipeline as manual printing. "
            "File size depends on the selected PDF printer/setup."
        )
    elif backend == EXPORT_BACKEND_ADVANCED_PRINTER:
        note = "\n- Uses Revit PrintManager with the selected PDF printer."
    else:
        note = "\n- Native Revit PDF Export is API-stable but may create larger PDFs."

    actual_pdf = generated[0] if generated else "N/A"
    actual_pdf_size = format_mb(get_file_size_mb(actual_pdf)) if generated else "N/A"
    actual_pdf_size_value = get_file_size_mb(actual_pdf) if generated else None
    requested_note = ""
    if generated and requested_path not in generated:
        requested_note = "\nRequested PDF path was not created directly; actual generated PDF was detected instead."
    vector_size_note = ""
    if (
        backend == EXPORT_BACKEND_NATIVE
        and export_settings.get("processing_mode") == PROCESSING_VECTOR
        and actual_pdf_size_value is not None
        and actual_pdf_size_value > 30.0
    ):
        vector_size_note = "\n- Native vector export can remain large because vector geometry is preserved."

    optimization_text = "Post-compression: skipped"
    if optimization_result:
        opt_profile = optimization_result.get("profile") or "skipped"
        opt_output = optimization_result.get("output_pdf") or "N/A"
        opt_output_size = format_mb(optimization_result.get("optimized_size_mb"))
        opt_reduction = format_percent(optimization_result.get("reduction_percent"))
        skipped_reason = optimization_result.get("skipped_reason")
        if optimization_result.get("success"):
            larger_note = ""
            if optimization_result.get("optimized_larger"):
                larger_note = (
                    "\nqpdf output is larger than original; original PDF kept."
                )
            optimization_text = "{} compression:\n- Profile: {}\n- Output PDF: {} ({})\n- Reduction: {}{}".format(
                optimization_result.get("engine") or "Post",
                opt_profile,
                opt_output,
                opt_output_size,
                opt_reduction,
                larger_note,
            )
        elif skipped_reason == "disabled":
            optimization_text = "Post-compression: disabled"
        elif skipped_reason == "missing":
            optimization_text = "Post-compression: skipped - compressor not found"
        else:
            optimization_text = "Post-compression: skipped - {}".format(opt_profile)
    qpdf_limited_note = ""
    if optimization_result and optimization_result.get("success"):
        reduction_value = optimization_result.get("reduction_percent")
        try:
            if reduction_value is not None and reduction_value < 10.0:
                qpdf_limited_note = "\n- qpdf reduction is limited because this PDF is mostly vector geometry."
        except Exception:
            pass
    rasterized = (
        "yes" if export_settings.get("processing_mode") == PROCESSING_RASTER else "no"
    )
    qpdf_mb = "N/A"
    if optimization_result and optimization_result.get("success"):
        qpdf_mb_value = optimization_result.get("optimized_size_mb")
        if qpdf_mb_value is not None:
            qpdf_mb = "{:.2f}".format(qpdf_mb_value)
    original_mb = "N/A"
    if actual_pdf_size_value is not None:
        original_mb = "{:.2f}".format(actual_pdf_size_value)
    logger.info(
        "PDF_TEST_RESULT | preset={} | engine={} | rasterized={} | dpi={} | raster={} | color={} | original_mb={} | post_compressor={} | compressed_mb={} | elapsed={:.2f}".format(
            export_settings.get("compression_preset") or "N/A",
            backend,
            rasterized,
            export_settings.get("native_dpi") or "N/A",
            export_settings.get("raster_quality") or "N/A",
            export_settings.get("color_mode") or "N/A",
            original_mb,
            (
                export_settings.get("post_compressor_engine")
                if bool(export_settings.get("post_compressor_enabled"))
                else "none"
            ),
            qpdf_mb,
            total_elapsed,
        )
    )

    forms.alert(
        "PDF export completed.\n\nPreset: {}\nEngine: {}\nRasterized: {}\nDPI: {}\nRaster quality: {}\nColor mode: {}\nRevit PDF: {} ({})\nViews/sheets: {}\nExport elapsed: {:.2f} seconds\n\n{}\nTotal elapsed: {:.2f} seconds\n\nNotes:\n- PDF24 compression may reduce file size by downsampling/recompressing images.\n- Lower DPI/quality reduces zoom/detail quality.{}{}{}{}".format(
            export_settings.get("compression_preset") or "N/A",
            backend,
            "Yes" if rasterized == "yes" else "No",
            export_settings.get("native_dpi") or "N/A",
            export_settings.get("raster_quality") or "N/A",
            export_settings.get("color_mode") or "N/A",
            actual_pdf,
            actual_pdf_size,
            len(selected_views),
            elapsed,
            optimization_text,
            total_elapsed,
            requested_note,
            vector_size_note,
            qpdf_limited_note,
            note,
        )
    )


def run_raster_test_matrix(run_config):
    selected_views = run_config.get("views") or []
    if not selected_views:
        forms.alert(
            "No views/sheets selected for raster test matrix.", exitscript=False
        )
        return
    output_folder = run_config.get("output_folder")
    try:
        validate_output_folder(output_folder)
    except Exception as ex:
        forms.alert(
            "Output folder is not writable:\n{}\n\n{}".format(output_folder, ex),
            exitscript=False,
        )
        return
    base_name = sanitize_filename(run_config.get("print_set_name") or "RasterTest")
    tests = [
        ("DPI72_Low_Color", "DPI72", "Low", "Color"),
        ("DPI144_Low_Color", "DPI144", "Low", "Color"),
        ("DPI144_Medium_Color", "DPI144", "Medium", "Color"),
        ("DPI300_Low_Color", "DPI300", "Low", "Color"),
    ]
    rows = []
    for test_name, dpi, raster_quality, color_mode in tests:
        file_name = "{}_{}.pdf".format(base_name, test_name)
        settings = dict(run_config)
        settings["compression_preset"] = test_name
        settings["processing_mode"] = PROCESSING_RASTER
        settings["native_dpi"] = dpi
        settings["raster_quality"] = raster_quality
        settings["color_mode"] = color_mode
        settings["qpdf_enabled"] = False
        profile, elapsed, generated = export_views_native(
            test_name,
            selected_views,
            output_folder,
            file_name,
            settings,
        )
        output_pdf = generated[0] if generated else None
        size_mb = get_file_size_mb(output_pdf) if output_pdf else None
        rows.append((test_name, size_mb, elapsed, output_pdf))
        logger.info(
            "PDF_TEST_RESULT | preset={} | engine={} | rasterized=yes | dpi={} | raster={} | color={} | original_mb={} | qpdf_enabled=no | qpdf_mb=none | elapsed={:.2f}".format(
                test_name,
                EXPORT_BACKEND_NATIVE,
                dpi,
                raster_quality,
                color_mode,
                "{:.2f}".format(size_mb) if size_mb is not None else "N/A",
                elapsed,
            )
        )
    lines = ["Preset | Size MB | Elapsed sec"]
    for test_name, size_mb, elapsed, output_pdf in rows:
        lines.append(
            "{} | {} | {:.2f}".format(
                test_name,
                "{:.2f}".format(size_mb) if size_mb is not None else "N/A",
                elapsed,
            )
        )
    forms.alert(
        "Raster Test Matrix completed.\n\n{}".format("\n".join(lines)), exitscript=False
    )


def run_pdf24_compression_matrix(input_pdf):
    exe = find_pdf24_doctool_exe()
    if not exe:
        forms.alert("PDF24 compressor not found.", exitscript=False)
        return
    profiles = [
        PDF24_PROFILE_BALANCED,
        PDF24_PROFILE_SMALLER,
        PDF24_PROFILE_MAXIMUM,
        PDF24_PROFILE_EXTREME,
    ]
    original_mb = get_file_size_mb(input_pdf)
    rows = ["Profile | DPI | Quality | Size MB | Reduction % | Elapsed"]
    for profile in profiles:
        settings = PDF24_PROFILES[profile]
        output_pdf = get_unique_optimized_pdf_path(
            input_pdf, suffix="_pdf24_{}".format(profile.replace("PDF24 ", "").lower())
        )
        args = build_pdf24_command(
            exe,
            input_pdf,
            output_pdf,
            settings["dpi"],
            settings["quality"],
            settings["color_model"],
        )
        result = run_optimizer_process(
            "PDF24", exe, args, input_pdf, output_pdf, profile
        )
        size_mb = result.get("optimized_size_mb") if result else None
        reduction = result.get("reduction_percent") if result else None
        elapsed = result.get("elapsed") if result else 0.0
        rows.append(
            "{} | {} | {} | {} | {} | {:.2f}".format(
                profile,
                settings["dpi"],
                settings["quality"],
                format_mb(size_mb),
                format_percent(reduction),
                elapsed,
            )
        )
        logger.info(
            "PDF24_TEST_RESULT | profile={} | dpi={} | quality={} | original_mb={} | compressed_mb={} | reduction={} | elapsed={:.2f}".format(
                profile,
                settings["dpi"],
                settings["quality"],
                format_mb(original_mb),
                format_mb(size_mb),
                format_percent(reduction),
                elapsed,
            )
        )
    forms.alert(
        "PDF24 Compression Matrix completed.\n\n{}".format("\n".join(rows)),
        exitscript=False,
    )


def run_create_print_set(action):
    available_views = get_printable_views()
    if action in (ACTION_MANUAL, ACTION_MANUAL_EXPORT):
        selected_views = collect_views_manual(available_views)
    else:
        selected_views = collect_views_from_template(available_views)
    print_set_name = ask_print_set_name()
    confirm_print_set(print_set_name, selected_views)
    is_export_action = action in (ACTION_MANUAL_EXPORT, ACTION_TEMPLATE_EXPORT)
    print_set = create_print_set(
        print_set_name, selected_views, notify=not is_export_action
    )
    if is_export_action:
        export_views_to_pdf(print_set_name, selected_views, print_set)


def run_export_existing_print_set():
    print_set = choose_existing_print_set()
    selected_views = extract_views_from_print_set(print_set)
    if not selected_views:
        forms.alert("Selected print set has no printable views.", exitscript=True)
    export_views_to_pdf(print_set.Name, selected_views, print_set)


def run_main_dialog_workflow():
    run_config = show_create_print_set_pdf_dialog()
    if not run_config:
        return
    selected_views = run_config.get("views") or []
    print_set_name = run_config.get("print_set_name") or "ViewPrintSet"
    print_set = run_config.get("print_set")
    save_print_set = bool(run_config.get("save_print_set"))
    export_pdf = bool(run_config.get("export_pdf"))

    if not selected_views:
        forms.alert("No views/sheets selected.", exitscript=False)
        return

    if save_print_set:
        print_set = create_print_set(
            print_set_name, selected_views, notify=not export_pdf
        )

    if export_pdf:
        export_views_to_pdf(
            print_set_name,
            selected_views,
            print_set,
            backend=run_config.get("export_engine") or EXPORT_BACKEND_RECOMMENDED,
            output_folder=run_config.get("output_folder"),
            pdf_file_name=run_config.get("pdf_file_name"),
            export_settings=run_config,
        )
    elif not save_print_set:
        forms.alert(
            "Nothing to do. No views/sheets were available for export.",
            exitscript=False,
        )


def main():
    run_main_dialog_workflow()


main()

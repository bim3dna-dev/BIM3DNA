# -*- coding: utf-8 -*-
__title__ = "AI"
__doc__ = """AI"""

import clr
import os
import sys
import json
import subprocess
import requests
import re
import time
import math
import tempfile
import codecs
import csv
import System

from System import Action

from pyrevit import revit, DB, forms, script
from pyrevit import script as pyscript
import System.Windows.Forms as WinForms

SCRIPT_DIR = os.path.dirname(__file__)
LIB_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "..", "lib"))
ROOT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "..", ".."))
MODEL_SERVICE_DIR = os.path.join(ROOT_DIR, "Model_Service")
if LIB_DIR not in sys.path:
    sys.path.append(LIB_DIR)
if MODEL_SERVICE_DIR not in sys.path:
    sys.path.append(MODEL_SERVICE_DIR)

from ai_agent_session import AgentSession
from ai_local_store import LocalSettingsStore
from ai_prompt_registry import PromptCatalog
from ai_reviewed_code import validate_reviewed_code
from ModelService import (
    get_openai_provider_state,
    get_openai_provider_self_test,
    normalize_intent_to_supported_action,
)

uidoc = revit.uidoc
doc = revit.doc

# === Config ===
OLLAMA_API_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "phi3:mini"
DEFAULT_PLANNER_MODEL = "gpt-4o-mini"
XAML_PATH = "UI.xaml"
PROMPT_CATALOG_PATH = os.path.join(LIB_DIR, "prompt_catalog.json")
APPROVED_RECIPES_PATH = os.path.join(LIB_DIR, "approved_recipes.json")
WINDOW_SETTINGS_FILE = "ai_window_settings.json"

QA_EXPORT_ACCEPTED_REPORT_HEADERS = (
    "[SELECTED ELEMENTS BY CATEGORY]",
    "[SELECTED ELEMENTS BY TYPE]",
    "[COUNT SELECTED ELEMENTS]",
    "[SELECTION HEALTH CHECK]",
    "[MISSING PARAMETERS FROM SELECTION]",
    "[ACTIVE VIEW MEP SUMMARY]",
    "[ACTIVE VIEW ELEMENTS BY CATEGORY]",
    "[ACTIVE VIEW ELEMENTS BY TYPE]",
    "[ACTIVE VIEW QA REPORT]",
    "[ACTIVE VIEW MISSING PARAMETERS]",
    "[SELECTED MEP SYSTEM ASSIGNMENT REPORT]",
    "[ACTIVE VIEW MEP SYSTEM ASSIGNMENT REPORT]",
    "[SELECTED DISCIPLINE QA REPORT]",
    "[ACTIVE VIEW DISCIPLINE QA REPORT]",
    "[PROJECT ONBOARDING CHECKLIST]",
    "[GUIDED PROJECT STARTUP PLAN]",
    "[PROJECT CONTEXT SUMMARY]",
    "[LINK COORDINATE HEALTH]",
    "[BIM BASIS / LEVELS & GRIDS]",
    "[REVIEWED ACTION PROPOSAL]",
    "[SPLIT SELECTED PIPES DRY RUN]",
    "[REVIEWED ACTION CONFIRMATION GUARD]",
    "[SPLIT SELECTED PIPES ROLLBACK TEST]",
    "[SPLIT SELECTED PIPE REVIEWED APPLY]",
    "[SPLIT APPLY VERIFICATION REPORT]",
)

PIPE_SPLIT_DRY_RUN_MAX_SELECTION = 200
PIPE_SPLIT_DRY_RUN_DETAIL_LIMIT = 25
PIPE_SPLIT_DRY_RUN_SAMPLE_LIMIT = 10
# Revit internal feet; approximately 1000 mm total and 500 mm per segment.
PIPE_SPLIT_DRY_RUN_MIN_TOTAL_LENGTH_FT = 3.28084
PIPE_SPLIT_DRY_RUN_MIN_SEGMENT_LENGTH_FT = 1.64042
PIPE_SPLIT_DRY_RUN_VERTICAL_Z_RATIO = 0.90
MAX_ROLLBACK_SPLIT_CANDIDATES = 5
ROLLBACK_SPLIT_LENGTH_TOLERANCE_FT = 1.0 / 304.8
ROLLBACK_TEST_CONFIRMATION_TOKEN = "ROLLBACK-TEST-OK"
MAX_PERSISTENT_SPLIT_CANDIDATES = 1
PERSISTENT_SPLIT_CONFIRMATION_TOKEN = "PERSISTENT-SPLIT-OK"
SPLIT_APPLY_VERIFICATION_LENGTH_TOLERANCE_FT = 0.00328084

THEMES = {
    "light": {
        "window_bg": "#eff6ff",
        "panel_bg": "#ffffff",
        "panel_alt": "#f8fafd",
        "border": "#7aaef7",
        "accent": "#0085ff",
        "accent_alt": "#00b894",
        "text": "#1f2937",
        "muted": "#4b5563",
        "warn": "#ef4444",
        "disabled_bg": "#e5e7eb",
        "disabled_fg": "#6b7280",
        "dropdown_bg": "#f8fafd",
        "dropdown_fg": "#1f2937",
        "dropdown_item_bg": "#ffffff",
        "dropdown_item_fg": "#1f2937",
        "dropdown_highlight_bg": "#dbeafe",
        "dropdown_highlight_fg": "#1f2937",
        "tree_bg": "#f8fafd",
        "tree_fg": "#1f2937",
        "tree_hover_bg": "#e0efff",
        "tree_selected_bg": "#dbeafe",
        "tree_selected_fg": "#1f2937",
    },
    "dark": {
        "window_bg": "#0f172a",
        "panel_bg": "#111827",
        "panel_alt": "#1f2937",
        "border": "#334155",
        "accent": "#38bdf8",
        "accent_alt": "#34d399",
        "text": "#f8fafc",
        "muted": "#cbd5e1",
        "warn": "#fca5a5",
        "disabled_bg": "#334155",
        "disabled_fg": "#e2e8f0",
        "dropdown_bg": "#111827",
        "dropdown_fg": "#f8fafc",
        "dropdown_item_bg": "#1f2937",
        "dropdown_item_fg": "#f8fafc",
        "dropdown_highlight_bg": "#0f766e",
        "dropdown_highlight_fg": "#f8fafc",
        "tree_bg": "#111827",
        "tree_fg": "#f8fafc",
        "tree_hover_bg": "#243244",
        "tree_selected_bg": "#0f766e",
        "tree_selected_fg": "#f8fafc",
    },
}

REVIEWED_CODE_STATE_COLORS = {
    "draft": "#f59e0b",
    "validated": "#10b981",
    "blocked": "#ef4444",
    "executed": "#3b82f6",
    "saved": "#8b5cf6",
}

# print("RUNNING AI script from:", __file__)

logger = pyscript.get_logger()

def _silent_alert(msg, title="pyRevit", exitscript=False, **kwargs):
    # No icon => no Windows system sound
    WinForms.MessageBox.Show(
        str(msg), str(title),
        WinForms.MessageBoxButtons.OK,
        WinForms.MessageBoxIcon.None
    )
    if exitscript:
        pyscript.exit()

# Replace pyRevit's alert with our silent version
forms.alert = _silent_alert
# --- end silent override---
# === Helper Functions ===


def do_nothing():
    pass


def safe_str(x):
    try:
        if x is None:
            return "(none)"
        return str(x)
    except:
        return "(err)"


def slugify_text(text):
    text = (text or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text or "approved-recipe"


def build_create_sheet_reviewed_code():
    return """from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector, ViewSheet, Transaction

title_block_types = FilteredElementCollector(doc) \\
    .OfCategory(BuiltInCategory.OST_TitleBlocks) \\
    .WhereElementIsElementType() \\
    .ToElements()

if not title_block_types:
    result = "Failed: no title block type exists in the current project."
else:
    title_block_type = title_block_types[0]
    transaction = Transaction(doc, "Create AI Sheet")
    transaction.Start()
    try:
        new_sheet = ViewSheet.Create(doc, title_block_type.Id)
        if new_sheet:
            try:
                new_sheet.Name = "AI Generated Sheet"
            except:
                pass
            try:
                new_sheet.SheetNumber = "AI-001"
            except:
                pass
            transaction.Commit()
            result = "Created sheet: {0} ({1})".format(new_sheet.SheetNumber, new_sheet.Name)
        else:
            transaction.RollBack()
            result = "Failed: ViewSheet.Create returned no sheet."
    except Exception as create_error:
        try:
            transaction.RollBack()
        except:
            pass
        result = "Failed to create sheet: {0}".format(str(create_error))
"""


def ollama_is_installed():
    try:
        result = subprocess.Popen(
            ["ollama", "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        out, err = result.communicate()
        return result.returncode == 0
    except Exception:
        return False


def ollama_model_installed(model_name):
    try:
        result = subprocess.Popen(
            ["ollama", "list"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        out, err = result.communicate()
        models = []
        if result.returncode == 0:
            lines = out.decode("utf-8").splitlines()
            for line in lines[1:]:
                parts = line.strip().split()
                if parts:
                    models.append(parts[0])
            for m in models:
                if m == model_name:
                    return True
        return False
    except Exception:
        return False


def pull_ollama_model(model_name):
    try:
        forms.alert(
            "Pulling model: {} ... This may take a few minutes on first install.".format(
                model_name
            ),
            title="Ollama Copilot",
        )
        result = subprocess.Popen(
            ["ollama", "pull", model_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        out, err = result.communicate()
        if result.returncode == 0:
            forms.alert(
                "Model {} installed successfully!".format(model_name),
                title="Ollama Copilot",
            )
            return True
        else:
            forms.alert("Error pulling model: {}".format(err), title="Ollama Copilot")
            return False
    except Exception as e:
        forms.alert("Error pulling model: {}".format(str(e)), title="Ollama Copilot")
        return False


def send_ollama_chat(model_name, prompt):
    data = {"model": model_name, "prompt": prompt}
    try:
        resp = requests.post(OLLAMA_API_URL, json=data, stream=True, timeout=60)
        full_reply = ""
        # resp.iter_lines() yields each JSON object as a line
        for line in resp.iter_lines():
            if line:
                try:
                    msg = json.loads(line)
                    if "response" in msg:
                        full_reply += msg["response"]
                    if "done" in msg and msg["done"]:
                        break
                except Exception as e:
                    # Skip lines that aren't valid JSON
                    continue
        return full_reply.strip()
    except Exception as e:
        return "Error: Could not connect to Ollama ({})".format(str(e))


def get_all_models():
    try:
        result = subprocess.Popen(
            ["ollama", "list"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        out, err = result.communicate()
        models = []
        if result.returncode == 0:
            lines = out.decode("utf-8").splitlines()
            # Skip header row
            for line in lines[1:]:
                parts = line.strip().split()
                if parts:
                    models.append(parts[0])
        return models
    except Exception as e:
        print("get_all_models error:", str(e))
        return []


def extract_python_code(text):
    """Extract python code blocks from markdown-formatted LLM output."""
    code_blocks = re.findall(r"```python\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if not code_blocks:
        # Try to catch blocks where 'python' is on its own line
        code_blocks = re.findall(
            r"```[\s\n]*python[\s\n]*(.*?)```", text, re.DOTALL | re.IGNORECASE
        )
    return [code.strip() for code in code_blocks]


def safe_str_list(lst):
    return [str(x) if x is not None else "(none)" for x in lst]


# --- STRUCTURAL ---
def select_all_columns(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector
    from System.Collections.Generic import List

    columns = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_StructuralColumns)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    ids = [col.Id for col in columns]
    rows = []
    total_volume = 0
    for col in columns:
        name = get_elem_name(col)
        t = col.Document.GetElement(col.GetTypeId())
        typ = t.Name if (t is not None and hasattr(t, "Name")) else "(no type)"
        mark = safe_str(
            col.LookupParameter("Mark").AsString()
            if col.LookupParameter("Mark")
            else "(none)"
        )
        vol_param = col.LookupParameter("Volume")
        v = 0
        if vol_param:
            try:
                v = vol_param.AsDouble() * 0.0283168  # ft3 to m3
            except:
                pass
        total_volume += v
        # Get materials
        mat_list = []
        for mid in col.GetMaterialIds(False):
            mat = doc.GetElement(mid)
            mname = safe_str(mat.Name if mat else str(mid.IntegerValue))
            try:
                mvol = col.GetMaterialVolume(mid) * 0.0283168
            except:
                mvol = 0
            mat_list.append("{}: {:.3f} m³".format(mname, float(mvol)))
        mat_str = ", ".join(mat_list) if mat_list else "(none)"
        rows.append(
            "Name: {}, Type: {}, Mark: {}, Volume: {:.3f} m³, Materials: {}".format(
                name, typ, mark, float(v), mat_str
            )
        )
    uidoc.Selection.SetElementIds(List[DB.ElementId](ids))
    msg = "Selected {} columns.\n".format(len(columns))
    msg += "\n".join(rows[:20])
    if len(rows) > 20:
        msg += "\n... (showing first 20 of {})".format(len(rows))
    msg += "\nTotal Volume: {:.3f} m³".format(float(total_volume))
    return msg


def select_all_beams(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector
    from System.Collections.Generic import List

    beams = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_StructuralFraming)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    ids = [b.Id for b in beams]
    rows = []
    total_length = 0.0
    for b in beams:
        name = get_elem_name(b)
        t = b.Document.GetElement(b.GetTypeId())
        typ = t.Name if (t is not None and hasattr(t, "Name")) else "(no type)"
        length_param = b.LookupParameter("Length")
        length = 0.0
        if length_param:
            try:
                length = float(length_param.AsDouble() * 0.3048)  # ft to m
            except:
                length = 0.0
        total_length += length
        # Material breakdown
        mat_list = []
        for mid in b.GetMaterialIds(False):
            mat = doc.GetElement(mid)
            mname = (
                mat.Name if (mat and hasattr(mat, "Name")) else str(mid.IntegerValue)
            )
            try:
                mvol = float(b.GetMaterialVolume(mid) * 0.0283168)
            except:
                mvol = 0.0
            try:
                marea = float(b.GetMaterialArea(mid) * 0.092903)
            except:
                marea = 0.0
            mat_list.append("{}: {:.2f} m³, {:.2f} m²".format(mname, mvol, marea))
        mat_str = ", ".join(mat_list) if mat_list else "(none)"
        rows.append(
            "Name: {}, Type: {}, Length: {:.2f} m, Materials: {}".format(
                name, typ, length, mat_str
            )
        )
    uidoc.Selection.SetElementIds(List[DB.ElementId](ids))
    msg = "Selected {} beams.\n".format(len(beams))
    msg += "\n".join(rows[:20])
    if len(rows) > 20:
        msg += "\n... (showing first 20 of {})".format(len(rows))
    msg += "\nTotal Length: {:.2f} m".format(total_length)
    return msg


def select_all_foundations(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector
    from System.Collections.Generic import List

    foundations = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_StructuralFoundation)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    ids = [f.Id for f in foundations]
    rows = []
    total_volume = 0.0
    for f in foundations:
        # Safer name access
        if hasattr(f, "Name"):
            name = safe_str(f.Name)
        else:
            name = "(no name)"
        t = f.Document.GetElement(f.GetTypeId())
        typ = t.Name if t and hasattr(t, "Name") else "(no type)"
        vol_param = f.LookupParameter("Volume")
        vol = 0.0
        if vol_param:
            try:
                vol = float(vol_param.AsDouble() * 0.0283168)
            except:
                vol = 0.0
        total_volume += vol
        mat_list = []
        for mid in f.GetMaterialIds(False):
            mat = doc.GetElement(mid)
            # Always check for .Name attribute
            mname = mat.Name if mat and hasattr(mat, "Name") else str(mid.IntegerValue)
            try:
                mvol = float(f.GetMaterialVolume(mid) * 0.0283168)
            except:
                mvol = 0.0
            try:
                marea = float(f.GetMaterialArea(mid) * 0.092903)
            except:
                marea = 0.0
            mat_list.append("{}: {:.2f} m³, {:.2f} m²".format(mname, mvol, marea))
        mat_str = ", ".join(mat_list) if mat_list else "(none)"
        rows.append(
            "Name: {}, Type: {}, Volume: {:.2f} m³, Materials: {}".format(
                name, typ, vol, mat_str
            )
        )
    uidoc.Selection.SetElementIds(List[DB.ElementId](ids))
    msg = "Selected {} foundations.\n".format(len(foundations))
    msg += "\n".join(rows[:20])
    if len(rows) > 20:
        msg += "\n... (showing first 20 of {})".format(len(rows))
    msg += "\nTotal Volume: {:.2f} m³".format(total_volume)
    return msg


def select_all_rebars(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector
    from System.Collections.Generic import List

    rebars = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Rebar)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    ids = [r.Id for r in rebars]
    rows = []
    total_length = 0.0
    total_count = 0
    total_volume = 0.0
    for r in rebars:
        # Get type/name
        try:
            t = r.Document.GetElement(r.GetTypeId())
            name = get_elem_name(r)
        except:
            name = "(no name)"

        # Length per bar
        try:
            bar_length = r.LookupParameter("Bar Length").AsDouble() * 0.3048
        except:
            bar_length = 0

        # Quantity
        try:
            count = r.LookupParameter("Quantity").AsInteger()
        except:
            count = 1

        # Volume per bar (if exists)
        try:
            bar_vol = r.LookupParameter("Bar Volume").AsDouble() * 0.0283168
        except:
            bar_vol = 0

        # Diameters
        try:
            diameter = r.LookupParameter("Bar Diameter").AsDouble() * 0.3048
        except:
            diameter = None

        # Material
        try:
            mats = r.GetMaterialIds(False)
            mat_name = None
            for mid in mats:
                mat = doc.GetElement(mid)
                if mat:
                    mat_name = get_elem_name(mat)
                    break
            if not mat_name:
                mat_name = "(none)"
        except:
            mat_name = "(none)"

        length_total = bar_length * count
        if bar_vol is not None:
            volume_total = bar_vol * count
        else:
            # Estimate volume if bar_vol not available (as solid cylinder)
            if diameter is not None and bar_length > 0:
                radius = float(diameter) / 2.0
                volume_total = 3.14159265 * (radius**2) * bar_length * count
            else:
                volume_total = 0.0

        total_length += length_total
        total_count += count
        total_volume += volume_total

        # Format values safely
        if diameter is not None and isinstance(diameter, (float, int)):
            diameter_str = "{:.3f}".format(float(diameter))
        else:
            diameter_str = "(none)"
        if bar_vol is not None and isinstance(bar_vol, (float, int)):
            bar_vol_str = "{:.4f}".format(float(bar_vol))
        else:
            bar_vol_str = "(est)" if volume_total > 0 else "(none)"
        if volume_total is not None and isinstance(volume_total, (float, int)):
            volume_total_str = "{:.4f}".format(float(volume_total))
        else:
            volume_total_str = "(none)"

        rows.append(
            "Name/Type: {}, Quantity: {}, Length per bar: {:.2f} m, Total Length: {:.2f} m, Diameter: {} m, Material: {}, Bar Volume: {}, Total Volume: {}".format(
                name,
                count,
                bar_length,
                length_total,
                diameter_str,
                mat_name,
                bar_vol_str,
                volume_total_str,
            )
        )

    uidoc.Selection.SetElementIds(List[DB.ElementId](ids))
    msg = "Selected {} rebars.\n".format(len(rebars))
    msg += "\n".join(rows[:20])
    if len(rows) > 20:
        msg += "\n... (showing first 20 of {})".format(len(rows))
    msg += "\nTotal rebars: {}\nTotal rebar length: {:.2f} m\nTotal rebar volume: {:.4f} m³".format(
        total_count, total_length, total_volume
    )
    return msg


def select_all_floors(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector
    from System.Collections.Generic import List

    floors = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Floors)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    ids = [f.Id for f in floors]
    rows = []
    total_area = 0.0
    total_vol = 0.0

    for f in floors:
        name = get_elem_name(f)
        t = f.Document.GetElement(f.GetTypeId())
        typ = t.Name if (t and hasattr(t, "Name")) else "(no type)"

        area = 0.0
        vol = 0.0
        ap = f.LookupParameter("Area")
        vp = f.LookupParameter("Volume")
        if ap:
            try:
                area = ap.AsDouble() * 0.092903
            except:
                pass
        if vp:
            try:
                vol = vp.AsDouble() * 0.0283168
            except:
                pass
        total_area += area
        total_vol += vol

        mat_list = []
        for mid in f.GetMaterialIds(False):
            mat = doc.GetElement(mid)
            mname = (
                mat.Name if (mat and hasattr(mat, "Name")) else str(mid.IntegerValue)
            )
            try:
                mvol = f.GetMaterialVolume(mid) * 0.0283168
            except:
                mvol = 0.0
            try:
                marea = f.GetMaterialArea(mid) * 0.092903
            except:
                marea = 0.0
            mat_list.append(
                "{}: {:.2f} m³, {:.2f} m²".format(mname, float(mvol), float(marea))
            )
        mat_str = ", ".join(mat_list) if mat_list else "(none)"

        rows.append(
            "Name: {}, Type: {}, Area: {:.2f} m², Volume: {:.2f} m³, Materials: {}".format(
                name, typ, float(area), float(vol), mat_str
            )
        )

    uidoc.Selection.SetElementIds(List[DB.ElementId](ids))
    msg = "Selected {} floors.\n".format(len(floors))
    msg += "\n".join(rows[:20])
    if len(rows) > 20:
        msg += "\n... (showing first 20 of {})".format(len(floors))
    msg += "\nTotal Area: {:.2f} m²\nTotal Volume: {:.2f} m³".format(
        float(total_area), float(total_vol)
    )
    return msg


def select_all_ceilings(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector
    from System.Collections.Generic import List

    ceilings = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Ceilings)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    ids = [c.Id for c in ceilings]
    rows = []
    total_area = 0.0
    total_vol = 0.0

    for c in ceilings:
        name = get_elem_name(c)
        t = c.Document.GetElement(c.GetTypeId())
        typ = t.Name if (t and hasattr(t, "Name")) else "(no type)"

        area = 0.0
        vol = 0.0
        ap = c.LookupParameter("Area")
        vp = c.LookupParameter("Volume")
        if ap:
            try:
                area = ap.AsDouble() * 0.092903
            except:
                pass
        if vp:
            try:
                vol = vp.AsDouble() * 0.0283168
            except:
                pass
        total_area += area
        total_vol += vol

        mat_list = []
        for mid in c.GetMaterialIds(False):
            mat = doc.GetElement(mid)
            mname = (
                mat.Name if (mat and hasattr(mat, "Name")) else str(mid.IntegerValue)
            )
            try:
                mvol = c.GetMaterialVolume(mid) * 0.0283168
            except:
                mvol = 0.0
            try:
                marea = c.GetMaterialArea(mid) * 0.092903
            except:
                marea = 0.0
            mat_list.append(
                "{}: {:.2f} m³, {:.2f} m²".format(mname, float(mvol), float(marea))
            )
        mat_str = ", ".join(mat_list) if mat_list else "(none)"

        rows.append(
            "Name: {}, Type: {}, Area: {:.2f} m², Volume: {:.2f} m³, Materials: {}".format(
                name, typ, float(area), float(vol), mat_str
            )
        )

    uidoc.Selection.SetElementIds(List[DB.ElementId](ids))
    msg = "Selected {} ceilings.\n".format(len(ceilings))
    msg += "\n".join(rows[:20])
    if len(rows) > 20:
        msg += "\n... (showing first 20 of {})".format(len(ceilings))
    msg += "\nTotal Area: {:.2f} m²\nTotal Volume: {:.2f} m³".format(
        float(total_area), float(total_vol)
    )
    return msg


def select_all_roofs(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector
    from System.Collections.Generic import List

    roofs = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Roofs)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    ids = [r.Id for r in roofs]
    rows = []
    total_area = 0.0
    total_vol = 0.0

    for r in roofs:
        name = get_elem_name(r)
        t = r.Document.GetElement(r.GetTypeId())
        typ = t.Name if (t and hasattr(t, "Name")) else "(no type)"

        area = 0.0
        vol = 0.0
        ap = r.LookupParameter("Area")
        vp = r.LookupParameter("Volume")
        if ap:
            try:
                area = ap.AsDouble() * 0.092903
            except:
                pass
        if vp:
            try:
                vol = vp.AsDouble() * 0.0283168
            except:
                pass
        total_area += area
        total_vol += vol

        mat_list = []
        for mid in r.GetMaterialIds(False):
            mat = doc.GetElement(mid)
            mname = (
                mat.Name if (mat and hasattr(mat, "Name")) else str(mid.IntegerValue)
            )
            try:
                mvol = r.GetMaterialVolume(mid) * 0.0283168
            except:
                mvol = 0.0
            try:
                marea = r.GetMaterialArea(mid) * 0.092903
            except:
                marea = 0.0
            mat_list.append(
                "{}: {:.2f} m³, {:.2f} m²".format(mname, float(mvol), float(marea))
            )
        mat_str = ", ".join(mat_list) if mat_list else "(none)"

        rows.append(
            "Name: {}, Type: {}, Area: {:.2f} m², Volume: {:.2f} m³, Materials: {}".format(
                name, typ, float(area), float(vol), mat_str
            )
        )

    uidoc.Selection.SetElementIds(List[DB.ElementId](ids))
    msg = "Selected {} roofs.\n".format(len(roofs))
    msg += "\n".join(rows[:20])
    if len(roofs) > 20:
        msg += "\n... (showing first 20 of {})".format(len(roofs))
    msg += "\nTotal Area: {:.2f} m²\nTotal Volume: {:.2f} m³".format(
        float(total_area), float(total_vol)
    )
    return msg


def count_floors(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector

    floors = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Floors)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    return "Total floors: {}".format(len(floors))


def count_ceilings(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector

    ceilings = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Ceilings)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    return "Total ceilings: {}".format(len(ceilings))


def count_roofs(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector

    roofs = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Roofs)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    return "Total roofs: {}".format(len(roofs))


def count_rebars(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector

    rebars = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Rebar)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    return "There are {} rebars in the model.".format(len(rebars))


def count_columns(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector

    cols = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_StructuralColumns)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    return "Total structural columns: {}".format(len(cols))


def delete_all_columns(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector, Transaction

    cols = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_StructuralColumns)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    ids = [c.Id for c in cols]
    t = Transaction(doc, "Delete all columns")
    t.Start()
    for i in ids:
        doc.Delete(i)
    t.Commit()
    return "Deleted {} columns.".format(len(ids))


def total_structural_volume(doc, uidoc):
    from Autodesk.Revit.DB import (
        BuiltInCategory,
        FilteredElementCollector,
        ParameterType,
    )

    elements = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_StructuralFraming)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    total_vol = 0
    for elem in elements:
        param = elem.LookupParameter("Volume")
        if param:
            try:
                total_vol += param.AsDouble()
            except:
                pass
    m3 = total_vol * 0.0283168  # Revit internal units to m3
    return "Total volume of structural framing: {:.2f} m³".format(m3)


# --- ARCHITECTURAL ---
def select_all_walls(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector
    from System.Collections.Generic import List

    walls = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Walls)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    ids = [w.Id for w in walls]
    rows = []
    total_area = 0
    total_vol = 0
    for w in walls:
        name = get_elem_name(w)
        t = w.Document.GetElement(w.GetTypeId())
        typ = t.Name if (t is not None and hasattr(t, "Name")) else "(no type)"
        mark = (
            w.LookupParameter("Mark").AsString()
            if w.LookupParameter("Mark")
            else "(none)"
        )
        area_param = w.LookupParameter("Area")
        area = 0
        if area_param:
            try:
                area = area_param.AsDouble() * 0.092903
            except:
                pass
        vol_param = w.LookupParameter("Volume")
        vol = 0
        if vol_param:
            try:
                vol = vol_param.AsDouble() * 0.0283168
            except:
                pass
        total_area += area
        total_vol += vol
        # Material breakdown
        mat_list = []
        for mid in w.GetMaterialIds(False):
            mat = doc.GetElement(mid)
            mname = (
                mat.Name if (mat and hasattr(mat, "Name")) else str(mid.IntegerValue)
            )
            try:
                mvol = w.GetMaterialVolume(mid) * 0.0283168
                marea = w.GetMaterialArea(mid) * 0.092903
            except:
                mvol = marea = 0
            mat_list.append(
                "{}: {:.2f} m³, {:.2f} m²".format(mname, float(mvol), float(marea))
            )
        mat_str = ", ".join(mat_list) if mat_list else "(none)"
        rows.append(
            "Name: {}, Type: {}, Mark: {}, Area: {:.2f} m², Volume: {:.2f} m³, Materials: {}".format(
                name, typ, mark, float(area), float(vol), mat_str
            )
        )
    uidoc.Selection.SetElementIds(List[DB.ElementId](ids))
    msg = "Selected {} walls.\n".format(len(walls))
    msg += "\n".join(rows[:20])
    if len(rows) > 20:
        msg += "\n... (showing first 20 of {})".format(len(rows))
    msg += "\nTotal Area: {:.2f} m²\nTotal Volume: {:.2f} m³".format(
        float(total_area), float(total_vol)
    )
    return msg


def select_all_windows(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector
    from System.Collections.Generic import List

    windows = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Windows)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    ids = [w.Id for w in windows]
    rows = []
    for w in windows:
        name = get_elem_name(w)  # for window/door/wall 'w'
        t = w.Document.GetElement(w.GetTypeId())
        typ = t.Name if (t is not None and hasattr(t, "Name")) else "(no type)"
        mark = safe_str(
            w.LookupParameter("Mark").AsString()
            if w.LookupParameter("Mark")
            else "(none)"
        )
        area = None
        vol = None
        area_param = w.LookupParameter("Area")
        vol_param = w.LookupParameter("Volume")
        if area_param:
            try:
                area = area_param.AsDouble() * 0.092903
            except:
                area = None
        if vol_param:
            try:
                vol = vol_param.AsDouble() * 0.0283168
            except:
                vol = None
        rows.append(
            "Name: {}, Type: {}, Mark: {}, Area: {} m², Volume: {} m³".format(
                name,
                typ,
                mark,
                "{:.2f}".format(area) if area else "(none)",
                "{:.2f}".format(vol) if vol else "(none)",
            )
        )
    uidoc.Selection.SetElementIds(List[DB.ElementId](ids))
    msg = "Selected {} windows.\n".format(len(windows))
    msg += "\n".join(rows[:20])
    if len(rows) > 20:
        msg += "\n... (showing first 20 of {})".format(len(rows))
    return msg


def select_all_doors(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector
    from System.Collections.Generic import List

    doors = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Doors)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    ids = [d.Id for d in doors]
    rows = []
    for d in doors:
        name = get_elem_name(d)
        t = d.Document.GetElement(d.GetTypeId())
        typ = t.Name if (t is not None and hasattr(t, "Name")) else "(no type)"
        mark = safe_str(
            d.LookupParameter("Mark").AsString()
            if d.LookupParameter("Mark")
            else "(none)"
        )
        area = None
        vol = None
        area_param = d.LookupParameter("Area")
        vol_param = d.LookupParameter("Volume")
        if area_param:
            try:
                area = area_param.AsDouble() * 0.092903
            except:
                area = None
        if vol_param:
            try:
                vol = vol_param.AsDouble() * 0.0283168
            except:
                vol = None
        rows.append(
            "Name: {}, Type: {}, Mark: {}, Area: {} m², Volume: {} m³".format(
                name,
                typ,
                mark,
                "{:.2f}".format(area) if area else "(none)",
                "{:.2f}".format(vol) if vol else "(none)",
            )
        )
    uidoc.Selection.SetElementIds(List[DB.ElementId](ids))
    msg = "Selected {} doors.\n".format(len(doors))
    msg += "\n".join(rows[:20])
    if len(rows) > 20:
        msg += "\n... (showing first 20 of {})".format(len(rows))
    return msg


def total_room_area(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector

    rooms = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Rooms)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    total_area = 0
    names = []
    levels = []
    for room in rooms:
        area = 0
        name = (
            room.LookupParameter("Name").AsString()
            if room.LookupParameter("Name")
            else "(no name)"
        )
        names.append(name)
        area_param = room.LookupParameter("Area")
        if area_param:
            try:
                area = area_param.AsDouble() * 0.092903  # ft2 to m2
            except:
                pass
        total_area += area
        level_param = room.LookupParameter("Level")
        if level_param:
            levels.append(level_param.AsValueString())
    msg = "Room count: {}\n".format(len(rooms))
    msg += "Room names: {}\n".format(
        ", ".join(names[:10]) + (" ..." if len(names) > 10 else "")
    )
    msg += "Levels: {}\n".format(
        ", ".join(set([l for l in levels if l])) if levels else "(none)"
    )
    msg += "Total room area: {:.2f} m²\n".format(total_area)
    return msg


def tag_all_rooms(doc, uidoc):
    from Autodesk.Revit.DB import (
        BuiltInCategory,
        FilteredElementCollector,
        Transaction,
        XYZ,
    )

    rooms = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Rooms)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    count = 0
    t = Transaction(doc, "Tag all rooms")
    t.Start()
    for room in rooms:
        loc = room.Location
        pt = None
        if hasattr(loc, "Point"):
            pt = loc.Point
        elif hasattr(loc, "Curve"):
            pt = loc.Curve.Evaluate(0.5, True)
        if pt:
            doc.Create.NewRoomTag(room, pt, None)
            count += 1
    t.Commit()
    return "Tagged {} rooms.".format(count)


def delete_all_doors(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector, Transaction

    doors = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Doors)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    ids = [d.Id for d in doors]
    count = 0
    t = Transaction(doc, "Delete all doors")
    t.Start()
    for i in ids:
        doc.Delete(i)
        count += 1
    t.Commit()
    return "Deleted {} doors.".format(count)


def delete_all_walls(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector, Transaction

    walls = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Walls)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    ids = [w.Id for w in walls]
    t = Transaction(doc, "Delete all walls")
    t.Start()
    for i in ids:
        doc.Delete(i)
    t.Commit()
    return "Deleted {} walls.".format(len(ids))


def delete_all_ceilings(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector, Transaction

    ceilings = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Ceilings)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    ids = [c.Id for c in ceilings]
    t = Transaction(doc, "Delete all ceilings")
    t.Start()
    for i in ids:
        doc.Delete(i)
    t.Commit()
    return "Deleted {} ceilings.".format(len(ids))


def delete_all_roofs(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector, Transaction

    roofs = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Roofs)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    ids = [r.Id for r in roofs]
    t = Transaction(doc, "Delete all roofs")
    t.Start()
    for i in ids:
        doc.Delete(i)
    t.Commit()
    return "Deleted {} roofs.".format(len(ids))


def total_ceiling_area_volume(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector

    ceilings = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Ceilings)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    total_area_ft2 = 0.0
    total_vol_ft3 = 0.0
    for c in ceilings:
        ap = c.LookupParameter("Area")
        vp = c.LookupParameter("Volume")
        if ap:
            try:
                total_area_ft2 += ap.AsDouble()
            except:
                pass
        if vp:
            try:
                total_vol_ft3 += vp.AsDouble()
            except:
                pass
    total_area_m2 = total_area_ft2 * 0.092903
    total_vol_m3 = total_vol_ft3 * 0.0283168
    return (
        "Ceilings — Count: {} | Total Area: {:.2f} m² | Total Volume: {:.2f} m³".format(
            len(ceilings), total_area_m2, total_vol_m3
        )
    )


def total_roof_area_volume(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector

    roofs = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Roofs)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    total_area_ft2 = 0.0
    total_vol_ft3 = 0.0
    for r in roofs:
        ap = r.LookupParameter("Area")
        vp = r.LookupParameter("Volume")
        if ap:
            try:
                total_area_ft2 += ap.AsDouble()
            except:
                pass
        if vp:
            try:
                total_vol_ft3 += vp.AsDouble()
            except:
                pass
    total_area_m2 = total_area_ft2 * 0.092903
    total_vol_m3 = total_vol_ft3 * 0.0283168
    return "Roofs — Count: {} | Total Area: {:.2f} m² | Total Volume: {:.2f} m³".format(
        len(roofs), total_area_m2, total_vol_m3
    )


def count_walls(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector

    walls = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Walls)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    return "Total walls: {}".format(len(walls))


def material_takeoff_walls(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector

    walls = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Walls)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    mat_vol = {}
    mat_area = {}
    for w in walls:
        mats = w.GetMaterialIds(False)
        for mid in mats:
            mat = doc.GetElement(mid)
            mname = (
                str(mat.Name)
                if mat and hasattr(mat, "Name") and mat.Name
                else str(mid.IntegerValue)
            )
            # Try to get material volume and area
            try:
                vol = w.GetMaterialVolume(mid)
                area = w.GetMaterialArea(mid)
            except:
                vol = 0
                area = 0
            if mname not in mat_vol:
                mat_vol[mname] = 0
                mat_area[mname] = 0
            mat_vol[mname] += vol * 0.0283168  # ft3 to m3
            mat_area[mname] += area * 0.092903  # ft2 to m2
    msg = "Wall Material Takeoff:\n"
    for mname in mat_vol:
        msg += "    {}: {:.2f} m³, {:.2f} m²\n".format(
            mname, mat_vol[mname], mat_area[mname]
        )
    return msg


def material_takeoff_floors(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector

    floors = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Floors)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    mat_vol = {}
    mat_area = {}
    for f in floors:
        mats = f.GetMaterialIds(False)
        for mid in mats:
            mat = doc.GetElement(mid)
            mname = mat.Name if mat else str(mid.IntegerValue)
            try:
                vol = f.GetMaterialVolume(mid)
                area = f.GetMaterialArea(mid)
            except:
                vol = 0
                area = 0
            if mname not in mat_vol:
                mat_vol[mname] = 0
                mat_area[mname] = 0
            mat_vol[mname] += vol * 0.0283168
            mat_area[mname] += area * 0.092903
    msg = "Floor Material Takeoff:\n"
    for mname in mat_vol:
        msg += "    {}: {:.2f} m³, {:.2f} m²\n".format(
            mname, mat_vol[mname], mat_area[mname]
        )
    return msg


def material_takeoff_columns(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector

    columns = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_StructuralColumns)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    vols = []
    for c in columns:
        vol_param = c.LookupParameter("Volume")
        v = 0
        if vol_param:
            try:
                v = vol_param.AsDouble() * 0.0283168  # ft³ to m³
            except:
                pass
        vols.append(v)
    msg = "Structural Column Total Volume: {:.3f} m³\n".format(sum(vols))
    return msg


def material_takeoff_beams(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector

    beams = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_StructuralFraming)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    mat_vol = {}
    mat_area = {}
    for b in beams:
        mats = b.GetMaterialIds(False)
        for mid in mats:
            mat = doc.GetElement(mid)
            mname = mat.Name if mat else str(mid.IntegerValue)
            try:
                vol = b.GetMaterialVolume(mid)
                area = b.GetMaterialArea(mid)
            except:
                vol = 0
                area = 0
            if mname not in mat_vol:
                mat_vol[mname] = 0
                mat_area[mname] = 0
            mat_vol[mname] += vol * 0.0283168
            mat_area[mname] += area * 0.092903
    msg = "Beam (Structural Framing) Material Takeoff:\n"
    for mname in mat_vol:
        msg += "    {}: {:.2f} m³, {:.2f} m²\n".format(
            mname, mat_vol[mname], mat_area[mname]
        )
    return msg


def material_takeoff_roofs(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector

    roofs = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Roofs)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    mat_vol = {}
    mat_area = {}
    for r in roofs:
        mats = r.GetMaterialIds(False)
        for mid in mats:
            mat = doc.GetElement(mid)
            mname = mat.Name if mat else str(mid.IntegerValue)
            try:
                vol = r.GetMaterialVolume(mid)
                area = r.GetMaterialArea(mid)
            except:
                vol = 0
                area = 0
            if mname not in mat_vol:
                mat_vol[mname] = 0
                mat_area[mname] = 0
            mat_vol[mname] += vol * 0.0283168
            mat_area[mname] += area * 0.092903
    msg = "Roof Material Takeoff:\n"
    for mname in mat_vol:
        msg += "    {}: {:.2f} m³, {:.2f} m²\n".format(
            mname, mat_vol[mname], mat_area[mname]
        )
    return msg


def material_takeoff_stairs(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector

    stairs = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Stairs)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    mat_vol = {}
    mat_area = {}
    for s in stairs:
        mats = s.GetMaterialIds(False)
        for mid in mats:
            mat = doc.GetElement(mid)
            mname = mat.Name if mat else str(mid.IntegerValue)
            try:
                vol = s.GetMaterialVolume(mid)
                area = s.GetMaterialArea(mid)
            except:
                vol = 0
                area = 0
            if mname not in mat_vol:
                mat_vol[mname] = 0
                mat_area[mname] = 0
            mat_vol[mname] += vol * 0.0283168
            mat_area[mname] += area * 0.092903
    msg = "Stair Material Takeoff:\n"
    for mname in mat_vol:
        msg += "    {}: {:.2f} m³, {:.2f} m²\n".format(
            mname, mat_vol[mname], mat_area[mname]
        )
    return msg


def material_takeoff_curtainpanels(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector

    panels = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_CurtainWallPanels)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    mat_vol = {}
    mat_area = {}
    for p in panels:
        mats = p.GetMaterialIds(False)
        for mid in mats:
            mat = doc.GetElement(mid)
            mname = mat.Name if mat else str(mid.IntegerValue)
            try:
                vol = p.GetMaterialVolume(mid)
                area = p.GetMaterialArea(mid)
            except:
                vol = 0
                area = 0
            if mname not in mat_vol:
                mat_vol[mname] = 0
                mat_area[mname] = 0
            mat_vol[mname] += vol * 0.0283168
            mat_area[mname] += area * 0.092903
    msg = "Curtain Panel Material Takeoff:\n"
    for mname in mat_vol:
        msg += "    {}: {:.2f} m³, {:.2f} m²\n".format(
            mname, mat_vol[mname], mat_area[mname]
        )
    return msg


def material_takeoff_rebar(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector

    rebars = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Rebar)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    mat_len = {}
    mat_count = {}
    for r in rebars:
        mats = r.GetMaterialIds(False)
        for mid in mats:
            mat = doc.GetElement(mid)
            mname = mat.Name if mat else str(mid.IntegerValue)
            try:
                length = (
                    r.LookupParameter("Bar Length").AsDouble() * 0.3048
                )  # feet to meters
                count = r.LookupParameter("Quantity").AsInteger()
            except:
                length = 0
                count = 0
            if mname not in mat_len:
                mat_len[mname] = 0
                mat_count[mname] = 0
            mat_len[mname] += length * count
            mat_count[mname] += count
    msg = "Rebar Material Takeoff:\n"
    for mname in mat_len:
        msg += "    {}: {:.2f} m total, {} bars\n".format(
            mname, mat_len[mname], mat_count[mname]
        )
    return msg


# --- MECHANICAL ---
def select_all_ducts(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector
    from System.Collections.Generic import List

    ducts = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_DuctCurves)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    ids = [d.Id for d in ducts]
    rows = []
    total_length = 0.0
    for d in ducts:
        t = d.Document.GetElement(d.GetTypeId())
        typ = t.Name if t and hasattr(t, "Name") else "(no type)"
        system_param = d.LookupParameter("System Name")
        system = system_param.AsString() if system_param else "(no system)"
        length_param = d.LookupParameter("Length")
        length = 0.0
        if length_param:
            try:
                length = float(length_param.AsDouble() * 0.3048)
            except:
                length = 0.0
        total_length += length
        rows.append(
            "Type: {}, System: {}, Length: {:.2f} m".format(typ, system, length)
        )
    uidoc.Selection.SetElementIds(List[DB.ElementId](ids))
    msg = "Selected {} ducts.\n".format(len(ducts))
    msg += "\n".join(rows[:20])
    if len(rows) > 20:
        msg += "\n... (showing first 20 of {})".format(len(rows))
    msg += "\nTotal Length: {:.2f} m".format(total_length)
    return msg


def select_all_pipes(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector
    from System.Collections.Generic import List

    pipes = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_PipeCurves)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    ids = [p.Id for p in pipes]
    rows = []
    total_length = 0.0
    for p in pipes:
        name = p.Name if hasattr(p, "Name") else "(no name)"
        t = p.Document.GetElement(p.GetTypeId())
        typ = t.Name if t and hasattr(t, "Name") else "(no type)"
        system_param = p.LookupParameter("System Name")
        system = system_param.AsString() if system_param else "(no system)"
        length_param = p.LookupParameter("Length")
        length = 0.0
        if length_param:
            try:
                length = float(length_param.AsDouble() * 0.3048)
            except:
                length = 0.0
        total_length += length
        rows.append(
            "Name: {}, Type: {}, System: {}, Length: {:.2f} m".format(
                name, typ, system, length
            )
        )
    uidoc.Selection.SetElementIds(List[DB.ElementId](ids))
    msg = "Selected {} pipes.\n".format(len(pipes))
    msg += "\n".join(rows[:20])
    if len(rows) > 20:
        msg += "\n... (showing first 20 of {})".format(len(rows))
    msg += "\nTotal Length: {:.2f} m".format(total_length)
    return msg


def list_ducts_in_active_view(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector

    active_view = uidoc.ActiveView
    ducts = (
        FilteredElementCollector(doc, active_view.Id)
        .OfCategory(BuiltInCategory.OST_DuctCurves)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    rows = []
    total_length = 0.0
    for duct in ducts:
        duct_type = doc.GetElement(duct.GetTypeId())
        type_name = get_elem_name(duct_type) if duct_type else "(no type)"
        system_name = "(no system)"
        system_param = duct.LookupParameter("System Name")
        if system_param:
            try:
                system_name = system_param.AsString() or "(no system)"
            except:
                pass
        length = 0.0
        length_param = duct.LookupParameter("Length")
        if length_param:
            try:
                length = float(length_param.AsDouble() * 0.3048)
            except:
                length = 0.0
        total_length += length
        rows.append(
            "Id: {0}, Type: {1}, System: {2}, Length: {3:.2f} m".format(
                duct.Id.IntegerValue, type_name, system_name, length
            )
        )

    msg = "Active view: {0}\nDucts found: {1}\n".format(active_view.Name, len(ducts))
    msg += "\n".join(rows[:20])
    if len(rows) > 20:
        msg += "\n... (showing first 20 of {0})".format(len(rows))
    msg += "\nTotal Length: {:.2f} m".format(total_length)
    return msg


def find_unconnected_fittings(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector
    from System.Collections.Generic import List

    fittings = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_DuctFitting)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    disconnected = []
    for fitting in fittings:
        try:
            connector_set = fitting.MEPModel.ConnectorManager.Connectors
        except:
            connector_set = None
        if connector_set is None:
            continue
        is_unconnected = False
        for connector in connector_set:
            try:
                if not connector.IsConnected:
                    is_unconnected = True
                    break
            except:
                continue
        if is_unconnected:
            disconnected.append(fitting)

    if disconnected:
        uidoc.Selection.SetElementIds(
            List[DB.ElementId]([item.Id for item in disconnected])
        )

    rows = []
    for fitting in disconnected[:20]:
        fitting_type = doc.GetElement(fitting.GetTypeId())
        rows.append(
            "Id: {0}, Type: {1}".format(
                fitting.Id.IntegerValue,
                get_elem_name(fitting_type) if fitting_type else "(no type)",
            )
        )

    msg = "Unconnected duct fittings: {0}\n".format(len(disconnected))
    msg += "\n".join(rows)
    if len(disconnected) > 20:
        msg += "\n... (showing first 20 of {0})".format(len(disconnected))
    return msg


def report_elements_without_system_assignment(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector

    categories = [
        ("Ducts", BuiltInCategory.OST_DuctCurves),
        ("Duct Fittings", BuiltInCategory.OST_DuctFitting),
        ("Pipes", BuiltInCategory.OST_PipeCurves),
        ("Pipe Fittings", BuiltInCategory.OST_PipeFitting),
    ]
    report = []
    total_missing = 0
    for label, category in categories:
        elems = (
            FilteredElementCollector(doc)
            .OfCategory(category)
            .WhereElementIsNotElementType()
            .ToElements()
        )
        missing = []
        for elem in elems:
            system_name = None
            param = elem.LookupParameter("System Name")
            if param:
                try:
                    system_name = param.AsString()
                except:
                    system_name = None
            if not system_name:
                missing.append(elem)
        total_missing += len(missing)
        report.append("{0}: {1} without system assignment".format(label, len(missing)))
        for elem in missing[:5]:
            report.append("  - Id {0}".format(elem.Id.IntegerValue))

    return "Elements without system assignment: {0}\n{1}".format(
        total_missing, "\n".join(report)
    )


def total_duct_length(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector

    ducts = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_DuctCurves)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    total_len = 0
    for duct in ducts:
        param = duct.LookupParameter("Length")
        if param:
            try:
                total_len += param.AsDouble()
            except:
                pass
    m = total_len * 0.3048  # Revit units to meters
    return "Total duct length: {:.2f} m".format(m)


def total_pipe_length(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector

    pipes = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_PipeCurves)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    total_len = 0
    for pipe in pipes:
        param = pipe.LookupParameter("Length")
        if param:
            try:
                total_len += param.AsDouble()
            except:
                pass
    m = total_len * 0.3048
    return "Total pipe length: {:.2f} m".format(m)


def count_ducts(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector

    ducts = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_DuctCurves)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    return "Total ducts: {}".format(len(ducts))


def count_selected_ducts(doc, uidoc):
    selected_ids = uidoc.Selection.GetElementIds()
    count = 0
    for element_id in selected_ids:
        elem = doc.GetElement(element_id)
        if (
            elem
            and hasattr(elem, "Category")
            and elem.Category is not None
            and elem.Category.Id.IntegerValue == int(DB.BuiltInCategory.OST_DuctCurves)
        ):
            count += 1
    return "Selected ducts: {}".format(count)


def count_ducts_in_active_view(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector

    active_view = uidoc.ActiveView
    ducts = (
        FilteredElementCollector(doc, active_view.Id)
        .OfCategory(BuiltInCategory.OST_DuctCurves)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    return "Ducts in active view '{0}': {1}".format(active_view.Name, len(ducts))


def _selected_elements_by_categories(doc, uidoc, built_in_categories):
    selected = []
    selected_ids = uidoc.Selection.GetElementIds()
    category_ids = [int(getattr(DB.BuiltInCategory, name)) if isinstance(name, str) else int(name) for name in built_in_categories]
    for element_id in selected_ids:
        elem = doc.GetElement(element_id)
        if (
            elem
            and hasattr(elem, "Category")
            and elem.Category is not None
            and elem.Category.Id.IntegerValue in category_ids
        ):
            selected.append(elem)
    return selected


def _lookup_param_double(elem, names):
    for name in names:
        param = elem.LookupParameter(name)
        if not param:
            continue
        try:
            return float(param.AsDouble())
        except:
            continue
    return None


def _derive_mep_curve_volume_ft3(elem):
    length_ft = _lookup_param_double(elem, ["Length", "Centerline Length"])
    if not length_ft or length_ft <= 0:
        return None, "missing length"

    diameter_ft = _lookup_param_double(elem, ["Diameter", "Overall Size"])
    if diameter_ft and diameter_ft > 0:
        radius_ft = diameter_ft / 2.0
        return 3.141592653589793 * radius_ft * radius_ft * length_ft, "derived from diameter and length"

    width_ft = _lookup_param_double(elem, ["Width"])
    height_ft = _lookup_param_double(elem, ["Height"])
    if width_ft and width_ft > 0 and height_ft and height_ft > 0:
        return width_ft * height_ft * length_ft, "derived from width, height, and length"

    area_ft2 = _lookup_param_double(elem, ["Cross-Sectional Area", "Area"])
    if area_ft2 and area_ft2 > 0:
        return area_ft2 * length_ft, "derived from cross-sectional area and length"

    return None, "missing usable section dimensions"


def report_total_selected_duct_length(doc, uidoc):
    ducts = _selected_elements_by_categories(doc, uidoc, [DB.BuiltInCategory.OST_DuctCurves])
    total_length = 0.0
    rows = []
    for duct in ducts:
        param = duct.LookupParameter("Length")
        length_m = 0.0
        if param:
            try:
                length_m = float(param.AsDouble() * 0.3048)
            except:
                pass
        total_length += length_m
        rows.append("Id: {0}, Length: {1:.2f} m".format(duct.Id.IntegerValue, length_m))
    lines = [
        "Selected ducts: {0}".format(len(ducts)),
        "Total selected duct length: {0:.2f} m".format(total_length),
    ]
    lines.extend(rows[:10])
    if len(rows) > 10:
        lines.append("...showing first 10 of {0}".format(len(rows)))
    if not ducts:
        lines.append("No selected ducts found.")
    return "\n".join(lines)


def report_total_selected_duct_volume_cubic_meters(doc, uidoc):
    ducts = _selected_elements_by_categories(doc, uidoc, [DB.BuiltInCategory.OST_DuctCurves])
    total_volume = 0.0
    direct_hits = 0
    derived_hits = 0
    missing_ids = []
    preview_rows = []
    for duct in ducts:
        param = duct.LookupParameter("Volume")
        volume_ft3 = None
        if param:
            try:
                volume_ft3 = float(param.AsDouble())
            except:
                volume_ft3 = None
        if volume_ft3 and volume_ft3 > 0:
            volume_m3 = volume_ft3 * 0.0283168
            total_volume += volume_m3
            direct_hits += 1
            preview_rows.append("Id: {0}, Volume: {1:.3f} m³ (direct)".format(duct.Id.IntegerValue, volume_m3))
            continue

        derived_volume_ft3, derived_reason = _derive_mep_curve_volume_ft3(duct)
        if derived_volume_ft3 and derived_volume_ft3 > 0:
            volume_m3 = derived_volume_ft3 * 0.0283168
            total_volume += volume_m3
            derived_hits += 1
            preview_rows.append(
                "Id: {0}, Volume: {1:.3f} m³ ({2})".format(
                    duct.Id.IntegerValue, volume_m3, derived_reason
                )
            )
        else:
            missing_ids.append(duct.Id.IntegerValue)

    lines = [
        "Selected ducts: {0}".format(len(ducts)),
        "Total selected duct volume: {0:.3f} m³".format(total_volume),
    ]
    if direct_hits:
        lines.append("Direct volume parameter used on {0} duct(s).".format(direct_hits))
    if derived_hits:
        lines.append("Derived volume used on {0} duct(s).".format(derived_hits))
    lines.extend(preview_rows[:10])
    if len(preview_rows) > 10:
        lines.append("...showing first 10 of {0}".format(len(preview_rows)))
    if missing_ids:
        lines.append(
            "Volume could not be resolved for {0} duct(s) due to missing usable volume/section data.".format(
                len(missing_ids)
            )
        )
        preview = ", ".join([str(item_id) for item_id in missing_ids[:10]])
        if preview:
            lines.append("Unresolved duct ids: {0}{1}".format(preview, " ..." if len(missing_ids) > 10 else ""))
    if not ducts:
        lines.append("No selected ducts found.")
    return "\n".join(lines)


def report_ducts_without_system_assignment(doc, uidoc):
    ducts = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_DuctCurves)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    missing = []
    for duct in ducts:
        param = duct.LookupParameter("System Name")
        system_name = None
        if param:
            try:
                system_name = param.AsString()
            except:
                pass
        if not system_name:
            missing.append(duct)
    lines = ["Ducts without system assignment: {0}".format(len(missing))]
    for duct in missing[:10]:
        lines.append("Id: {0}".format(duct.Id.IntegerValue))
    if len(missing) > 10:
        lines.append("...showing first 10 of {0}".format(len(missing)))
    return "\n".join(lines)


def count_selected_pipes(doc, uidoc):
    pipes = _selected_elements_by_categories(doc, uidoc, [DB.BuiltInCategory.OST_PipeCurves])
    return "Selected pipes: {0}".format(len(pipes))


def count_pipes_in_active_view(doc, uidoc):
    active_view = uidoc.ActiveView
    pipes = (
        DB.FilteredElementCollector(doc, active_view.Id)
        .OfCategory(DB.BuiltInCategory.OST_PipeCurves)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    return "Pipes in active view '{0}': {1}".format(active_view.Name, len(pipes))


def list_pipes_in_active_view(doc, uidoc):
    active_view = uidoc.ActiveView
    pipes = (
        DB.FilteredElementCollector(doc, active_view.Id)
        .OfCategory(DB.BuiltInCategory.OST_PipeCurves)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    rows = []
    total_length = 0.0
    for pipe in pipes:
        pipe_type = doc.GetElement(pipe.GetTypeId())
        type_name = get_elem_name(pipe_type) if pipe_type else "(no type)"
        system_name = "(no system)"
        system_param = pipe.LookupParameter("System Name")
        if system_param:
            try:
                system_name = system_param.AsString() or "(no system)"
            except:
                pass
        length = 0.0
        length_param = pipe.LookupParameter("Length")
        if length_param:
            try:
                length = float(length_param.AsDouble() * 0.3048)
            except:
                length = 0.0
        total_length += length
        rows.append(
            "Id: {0}, Type: {1}, System: {2}, Length: {3:.2f} m".format(
                pipe.Id.IntegerValue, type_name, system_name, length
            )
        )
    lines = ["Active view: {0}".format(active_view.Name), "Pipes found: {0}".format(len(pipes))]
    lines.extend(rows[:10])
    if len(rows) > 10:
        lines.append("...showing first 10 of {0}".format(len(rows)))
    lines.append("Total Length: {0:.2f} m".format(total_length))
    return "\n".join(lines)


def report_total_selected_pipe_length(doc, uidoc):
    pipes = _selected_elements_by_categories(doc, uidoc, [DB.BuiltInCategory.OST_PipeCurves])
    total_length = 0.0
    rows = []
    for pipe in pipes:
        param = pipe.LookupParameter("Length")
        length_m = 0.0
        if param:
            try:
                length_m = float(param.AsDouble() * 0.3048)
            except:
                pass
        total_length += length_m
        rows.append("Id: {0}, Length: {1:.2f} m".format(pipe.Id.IntegerValue, length_m))
    lines = [
        "Selected pipes: {0}".format(len(pipes)),
        "Total selected pipe length: {0:.2f} m".format(total_length),
    ]
    lines.extend(rows[:10])
    if len(rows) > 10:
        lines.append("...showing first 10 of {0}".format(len(rows)))
    if not pipes:
        lines.append("No selected pipes found.")
    return "\n".join(lines)


def find_unconnected_pipe_fittings(doc, uidoc):
    from System.Collections.Generic import List

    fittings = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_PipeFitting)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    disconnected = []
    for fitting in fittings:
        try:
            connector_set = fitting.MEPModel.ConnectorManager.Connectors
        except:
            connector_set = None
        if connector_set is None:
            continue
        for connector in connector_set:
            try:
                if not connector.IsConnected:
                    disconnected.append(fitting)
                    break
            except:
                continue
    if disconnected:
        uidoc.Selection.SetElementIds(List[DB.ElementId]([item.Id for item in disconnected]))
    lines = ["Unconnected pipe fittings: {0}".format(len(disconnected))]
    for fitting in disconnected[:10]:
        lines.append("Id: {0}".format(fitting.Id.IntegerValue))
    if len(disconnected) > 10:
        lines.append("...showing first 10 of {0}".format(len(disconnected)))
    return "\n".join(lines)


def report_pipes_without_system_assignment(doc, uidoc):
    pipes = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_PipeCurves)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    missing = []
    for pipe in pipes:
        param = pipe.LookupParameter("System Name")
        system_name = None
        if param:
            try:
                system_name = param.AsString()
            except:
                pass
        if not system_name:
            missing.append(pipe)
    lines = ["Pipes without system assignment: {0}".format(len(missing))]
    for pipe in missing[:10]:
        lines.append("Id: {0}".format(pipe.Id.IntegerValue))
    if len(missing) > 10:
        lines.append("...showing first 10 of {0}".format(len(missing)))
    return "\n".join(lines)


def _electrical_selection_categories():
    return [
        DB.BuiltInCategory.OST_ElectricalFixtures,
        DB.BuiltInCategory.OST_ElectricalEquipment,
        DB.BuiltInCategory.OST_LightingFixtures,
        DB.BuiltInCategory.OST_LightingDevices,
        DB.BuiltInCategory.OST_DataDevices,
        DB.BuiltInCategory.OST_FireAlarmDevices,
        DB.BuiltInCategory.OST_CommunicationDevices,
        DB.BuiltInCategory.OST_SecurityDevices,
        DB.BuiltInCategory.OST_NurseCallDevices,
    ]


def _electrical_qa_categories():
    return [
        DB.BuiltInCategory.OST_ElectricalFixtures,
        DB.BuiltInCategory.OST_ElectricalEquipment,
        DB.BuiltInCategory.OST_LightingFixtures,
        DB.BuiltInCategory.OST_LightingDevices,
        DB.BuiltInCategory.OST_DataDevices,
        DB.BuiltInCategory.OST_FireAlarmDevices,
        DB.BuiltInCategory.OST_CommunicationDevices,
        DB.BuiltInCategory.OST_SecurityDevices,
        DB.BuiltInCategory.OST_NurseCallDevices,
    ]


def _electrical_qa_category_labels():
    return [
        "Electrical Fixtures",
        "Electrical Equipment",
        "Lighting Fixtures",
        "Lighting Devices",
        "Data Devices",
        "Fire Alarm Devices",
        "Communication Devices",
        "Security Devices",
        "Nurse Call Devices",
    ]


def _collect_active_view_elements_by_categories(doc, uidoc, categories):
    collected = []
    for category in categories:
        try:
            collected.extend(
                list(
                    DB.FilteredElementCollector(doc, uidoc.ActiveView.Id)
                    .OfCategory(category)
                    .WhereElementIsNotElementType()
                    .ToElements()
                )
            )
        except:
            continue
    return collected


def select_all_electrical_fixtures_in_active_view(doc, uidoc):
    from System.Collections.Generic import List

    active_view = uidoc.ActiveView
    fixtures = (
        DB.FilteredElementCollector(doc, active_view.Id)
        .OfCategory(DB.BuiltInCategory.OST_ElectricalFixtures)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    uidoc.Selection.SetElementIds(List[DB.ElementId]([item.Id for item in fixtures]))
    return "Selected {0} electrical fixtures in active view '{1}'.".format(len(fixtures), active_view.Name)


def count_selected_fixtures_devices(doc, uidoc):
    elems = _selected_elements_by_categories(doc, uidoc, _electrical_selection_categories())
    return "Selected fixtures/devices: {0}".format(len(elems))


def report_devices_without_circuit_info(doc, uidoc):
    elems = _selected_elements_by_categories(doc, uidoc, _electrical_selection_categories())
    missing = []
    for elem in elems:
        values = []
        for param_name in ("Circuit Number", "Panel", "System Type"):
            param = elem.LookupParameter(param_name)
            if param:
                try:
                    values.append(param.AsString() or "")
                except:
                    values.append("")
        if not [value for value in values if value]:
            missing.append(elem)
    lines = [
        "Selected fixtures/devices inspected: {0}".format(len(elems)),
        "Selected devices without circuit/system info: {0}".format(len(missing)),
    ]
    for elem in missing[:10]:
        lines.append("Id: {0}, Category: {1}".format(elem.Id.IntegerValue, get_elem_name(elem.Category) if elem.Category else "(no category)"))
    if len(missing) > 10:
        lines.append("...showing first 10 of {0}".format(len(missing)))
    return "\n".join(lines)


def list_fixtures_by_type_in_active_view(doc, uidoc):
    active_view = uidoc.ActiveView
    fixtures = (
        DB.FilteredElementCollector(doc, active_view.Id)
        .OfCategory(DB.BuiltInCategory.OST_ElectricalFixtures)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    counts = {}
    for elem in fixtures:
        elem_type = doc.GetElement(elem.GetTypeId())
        type_name = get_elem_name(elem_type) if elem_type else "(no type)"
        counts[type_name] = counts.get(type_name, 0) + 1
    lines = ["Electrical fixtures by type in active view '{0}':".format(active_view.Name)]
    for type_name in sorted(counts.keys()):
        lines.append("{0}: {1}".format(type_name, counts[type_name]))
    return "\n".join(lines)


def list_electrical_fixtures_in_active_view(doc, uidoc):
    active_view = uidoc.ActiveView
    fixtures = (
        DB.FilteredElementCollector(doc, active_view.Id)
        .OfCategory(DB.BuiltInCategory.OST_ElectricalFixtures)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    lines = ["Electrical fixtures in active view '{0}': {1}".format(active_view.Name, len(fixtures))]
    for elem in fixtures[:10]:
        elem_type = doc.GetElement(elem.GetTypeId())
        lines.append(
            "Id: {0}, Type: {1}".format(
                elem.Id.IntegerValue,
                get_elem_name(elem_type) if elem_type else "(no type)",
            )
        )
    if len(fixtures) > 10:
        lines.append("...showing first 10 of {0}".format(len(fixtures)))
    return "\n".join(lines)


def select_electrical_qa_elements_in_active_view(doc, uidoc, context=None):
    from System.Collections.Generic import List

    elems = _collect_active_view_elements_by_categories(doc, uidoc, _electrical_qa_categories())
    uidoc.Selection.SetElementIds(List[DB.ElementId]([elem.Id for elem in elems]))
    return "\n".join(
        [
            "Select electrical QA elements in active view",
            "Active document: {0}".format(_document_title(doc)),
            "Active view: {0}".format(_active_view_title(doc, uidoc)),
            "Categories inspected: {0}".format(", ".join(_electrical_qa_category_labels())),
            "Selected electrical QA elements: {0}".format(len(elems)),
        ]
    )


def count_selected_electrical_qa_elements(doc, uidoc, context=None):
    elems = _selected_elements_by_categories(doc, uidoc, _electrical_qa_categories())
    return "\n".join(
        [
            "Count selected electrical QA elements",
            "Selection scope: active document only",
            "Active document: {0}".format(_document_title(doc)),
            "Active view: {0}".format(_active_view_title(doc, uidoc)),
            "Categories inspected: {0}".format(", ".join(_electrical_qa_category_labels())),
            "Selected electrical QA elements: {0}".format(len(elems)),
        ]
    )


def list_electrical_qa_elements_in_active_view(doc, uidoc, context=None):
    elems = _collect_active_view_elements_by_categories(doc, uidoc, _electrical_qa_categories())
    lines = [
        "List electrical QA elements in active view",
        "Active document: {0}".format(_document_title(doc)),
        "Active view: {0}".format(_active_view_title(doc, uidoc)),
        "Categories inspected: {0}".format(", ".join(_electrical_qa_category_labels())),
        "Elements found: {0}".format(len(elems)),
    ]
    for elem in elems[:20]:
        lines.append(
            "Id: {0}, Category: {1}, Type: {2}".format(
                _safe_int_id(elem),
                _category_name(elem),
                _family_and_type_text(doc, elem) or "(no type)",
            )
        )
    if len(elems) > 20:
        lines.append("...showing first 20 of {0}".format(len(elems)))
    return "\n".join(lines)


def report_electrical_qa_elements_without_assignment(doc, uidoc, context=None):
    elems = _selected_elements_by_categories(doc, uidoc, _electrical_qa_categories())
    if not elems:
        elems = _collect_active_view_elements_by_categories(doc, uidoc, _electrical_qa_categories())
        scope_text = "active view in active document"
    else:
        scope_text = "active document selection"
    missing = []
    for elem in elems:
        electrical_value = _electrical_assignment_value(elem)
        system_value = _system_assignment_value(elem) if _supports_system_assignment(elem) else None
        if not electrical_value and not system_value:
            missing.append(elem)
    lines = [
        "Report electrical QA elements without circuit or system assignment",
        "Active document: {0}".format(_document_title(doc)),
        "Active view: {0}".format(_active_view_title(doc, uidoc)),
        "Scope used: {0}".format(scope_text),
        "Categories inspected: {0}".format(", ".join(_electrical_qa_category_labels())),
        "Elements inspected: {0}".format(len(elems)),
        "Elements without circuit/system assignment: {0}".format(len(missing)),
    ]
    for elem in missing[:20]:
        lines.append(
            "Id: {0}, Category: {1}".format(
                _safe_int_id(elem),
                _category_name(elem),
            )
        )
    if len(missing) > 20:
        lines.append("...showing first 20 of {0}".format(len(missing)))
    return "\n".join(lines)


def _selected_elements(doc, uidoc):
    elements = []
    for element_id in uidoc.Selection.GetElementIds():
        elem = doc.GetElement(element_id)
        if elem is not None:
            elements.append(elem)
    return elements


def _sample_id_text(elements, limit=5):
    ids = []
    for elem in elements[:limit]:
        try:
            ids.append(str(elem.Id.IntegerValue))
        except:
            continue
    if not ids:
        return "(none)"
    suffix = " ..." if len(elements) > limit else ""
    return ", ".join(ids) + suffix


def _value_from_param(param):
    if not param:
        return None
    for accessor in ("AsString", "AsValueString"):
        try:
            value = getattr(param, accessor)()
            if value:
                return value
        except:
            pass
    try:
        if hasattr(param, "AsInteger"):
            value = param.AsInteger()
            if value not in (None, 0):
                return str(value)
    except:
        pass
    try:
        if hasattr(param, "AsDouble"):
            value = param.AsDouble()
            if value not in (None, 0):
                return str(value)
    except:
        pass
    return None


def _lookup_first_param_value(elem, names):
    for name in names:
        try:
            value = _value_from_param(elem.LookupParameter(name))
            if value:
                return value
        except:
            continue
    return None


def _family_and_type_text(doc, elem):
    elem_type = None
    try:
        elem_type = doc.GetElement(elem.GetTypeId()) if hasattr(elem, "GetTypeId") else None
    except:
        elem_type = None
    family_name = None
    type_name = None
    if elem_type is not None:
        try:
            family_name = getattr(elem_type, "FamilyName", None)
        except:
            family_name = None
        type_name = get_elem_name(elem_type)
    if not type_name:
        type_name = get_elem_name(elem)
    if family_name and type_name:
        return "{0} : {1}".format(family_name, type_name)
    return type_name or family_name or None


def _category_name(elem):
    category = getattr(elem, "Category", None)
    return get_elem_name(category) if category else "<No Category>"


def _element_level_name(doc, elem):
    value = _safe_param_text(elem, ["Reference Level", "Level", "Schedule Level", "Base Level", "Host Level"])
    if value:
        return value
    for attr_name in ("LevelId", "GenLevel"):
        try:
            value = getattr(elem, attr_name, None)
            if value is None:
                continue
            if attr_name == "GenLevel":
                return get_elem_name(value)
            level = doc.GetElement(value)
            level_name = get_elem_name(level)
            if level_name:
                return level_name
        except:
            continue
    return None


def _document_title(doc):
    try:
        title = getattr(doc, "Title", None)
        if title:
            return title
    except:
        pass
    return "(unknown document)"


def _active_view_title(doc, uidoc=None):
    try:
        active_view = getattr(uidoc, "ActiveView", None) if uidoc is not None else None
        if active_view is None:
            active_view = getattr(doc, "ActiveView", None)
        if active_view is not None:
            return get_elem_name(active_view) or "(unknown view)"
    except:
        pass
    return "(unknown view)"


PROJECT_CONTEXT_MAX_ITEMS = 20
PROJECT_CONTEXT_ORIGIN_TOLERANCE_FT = 1.0 / 304.8
PROJECT_CONTEXT_Z_TOLERANCE_FT = 1.0 / 304.8
PROJECT_CONTEXT_ANGLE_TOLERANCE_DEG = 0.01
PROJECT_CONTEXT_VECTOR_TOLERANCE = 0.0001
LEVEL_ELEVATION_TOLERANCE_MM = 5.0
GRID_LINEAR_OFFSET_TOLERANCE_MM = 10.0
GRID_ANGLE_TOLERANCE_DEG = 0.10
MAX_LINKS_FOR_LEVEL_GRID_SCAN = 10
MAX_GRID_GEOMETRY_COMPARISONS_PER_LINK = 250
MAX_REPORTED_LEVEL_ROWS = 50
MAX_REPORTED_GRID_ROWS = 50


def _safe_int(value, default=0):
    try:
        return int(value)
    except:
        return default


def _safe_element_id_value(elem_or_id):
    try:
        if hasattr(elem_or_id, "IntegerValue"):
            return int(elem_or_id.IntegerValue)
    except:
        pass
    try:
        return int(elem_or_id.Id.IntegerValue)
    except:
        return None


def _safe_param_text(elem, param_names):
    for name in param_names or []:
        try:
            param = elem.LookupParameter(name)
            value = _value_from_param(param)
            if value not in (None, ""):
                return safe_str(value)
        except:
            continue
    return None


def _collector_section(context, warnings, section_name, collector):
    try:
        context[section_name] = collector()
    except Exception as exc:
        warnings.append(
            {
                "section": section_name,
                "message": safe_str(exc),
            }
        )
        context[section_name] = {"error": safe_str(exc)}


def _active_view_info(doc, uidoc):
    active_view = None
    try:
        active_view = getattr(uidoc, "ActiveView", None)
    except:
        active_view = None
    if active_view is None:
        try:
            active_view = doc.ActiveView
        except:
            active_view = None
    info = {
        "name": get_elem_name(active_view) or "(unknown view)",
        "id": _safe_element_id_value(active_view),
        "view_type": None,
        "view_template_id": None,
        "view_template_name": None,
        "discipline": None,
    }
    if active_view is None:
        return info
    try:
        info["view_type"] = safe_str(active_view.ViewType)
    except:
        pass
    try:
        template_id = active_view.ViewTemplateId
        if template_id and template_id.IntegerValue > 0:
            info["view_template_id"] = int(template_id.IntegerValue)
            template = doc.GetElement(template_id)
            info["view_template_name"] = get_elem_name(template)
    except:
        pass
    try:
        info["discipline"] = _safe_param_text(active_view, ["Discipline"])
    except:
        pass
    return info


def _document_info(doc, uidoc):
    path_name = ""
    try:
        path_name = getattr(doc, "PathName", "") or ""
    except:
        path_name = ""
    is_workshared = None
    try:
        is_workshared = bool(doc.IsWorkshared)
    except:
        pass
    active_view = _active_view_info(doc, uidoc)
    return {
        "title": _document_title(doc),
        "path_name": path_name,
        "is_workshared": is_workshared,
        "active_view": active_view,
    }


def _collect_selection_ids(uidoc):
    try:
        return list(uidoc.Selection.GetElementIds())
    except:
        return []


def _collect_levels_context(doc):
    levels = []
    alias_groups = {}
    try:
        for level in DB.FilteredElementCollector(doc).OfClass(DB.Level):
            name = get_elem_name(level) or "(unnamed level)"
            simplified = re.sub(r"^\d+[_\-\s]+", "", name).strip()
            simplified_key = re.sub(r"[^a-z0-9]+", "", simplified.lower())
            elevation = None
            try:
                elevation = float(level.Elevation)
            except:
                pass
            entry = {
                "exact_name": name,
                "simplified_name": simplified,
                "simplified_key": simplified_key,
                "elevation": elevation,
                "ambiguous_alias": False,
            }
            levels.append(entry)
            alias_groups.setdefault(simplified_key, []).append(name)
    except:
        pass
    ambiguous = []
    for entry in levels:
        names = alias_groups.get(entry.get("simplified_key"), [])
        if len(set(names)) > 1:
            entry["ambiguous_alias"] = True
            ambiguous.append(
                {
                    "simplified_name": entry.get("simplified_name"),
                    "candidates": sorted(set(names)),
                }
            )
    return {
        "count": len(levels),
        "levels": levels,
        "ambiguous_aliases": ambiguous,
    }


def _collect_view_summary(doc, uidoc=None, sample_limit=PROJECT_CONTEXT_MAX_ITEMS):
    grouped = {}
    samples_by_type = {}
    total = 0
    active_id = None
    try:
        active_id = _safe_element_id_value(uidoc.ActiveView.Id)
    except:
        active_id = None
    try:
        for view in DB.FilteredElementCollector(doc).OfClass(DB.View):
            try:
                if getattr(view, "IsTemplate", False):
                    continue
            except:
                pass
            total += 1
            try:
                view_type = safe_str(view.ViewType)
            except:
                view_type = "Unknown"
            grouped[view_type] = grouped.get(view_type, 0) + 1
            samples = samples_by_type.setdefault(view_type, [])
            if len(samples) < sample_limit:
                view_id = _safe_element_id_value(view)
                samples.append(
                    {
                        "id": view_id,
                        "name": get_elem_name(view),
                        "is_active": bool(active_id is not None and view_id == active_id),
                    }
                )
    except:
        pass
    return {"total": total, "by_view_type": grouped, "samples_by_view_type": samples_by_type}


def _sheet_placed_view_count(sheet):
    try:
        return len(list(sheet.GetAllViewports()))
    except:
        return "not scanned"


def _collect_sheet_summary(doc, sample_limit=PROJECT_CONTEXT_MAX_ITEMS):
    count = 0
    samples = []
    try:
        for sheet in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet):
            count += 1
            if len(samples) < sample_limit:
                number = ""
                try:
                    number = sheet.SheetNumber
                except:
                    pass
                samples.append(
                    {
                        "id": _safe_element_id_value(sheet),
                        "number": number,
                        "name": get_elem_name(sheet),
                        "placed_view_count": _sheet_placed_view_count(sheet),
                    }
                )
    except:
        pass
    return {"count": count, "samples": samples}


def _project_schedule_body_row_count(schedule):
    try:
        table_data = schedule.GetTableData()
        try:
            section_data = table_data.GetSectionData(DB.SectionType.Body)
        except:
            section_data = table_data.GetSectionData(1)
        return int(section_data.NumberOfRows)
    except:
        return "unknown"


def _project_schedule_sort_fields(schedule, doc, sample_limit=10):
    fields = []
    try:
        definition = schedule.Definition
        for index in range(definition.GetSortGroupFieldCount()):
            try:
                sort_field = definition.GetSortGroupField(index)
                field = definition.GetField(sort_field.FieldId)
                fields.append(_schedule_field_name(field, doc) or "(unknown field)")
                if len(fields) >= sample_limit:
                    break
            except:
                continue
    except:
        pass
    return fields


def _project_schedule_filter_summaries(schedule, doc, sample_limit=10):
    filters = []
    try:
        definition = schedule.Definition
        for index in range(definition.GetFilterCount()):
            try:
                sched_filter = definition.GetFilter(index)
                field = definition.GetField(sched_filter.FieldId)
                field_name = _schedule_field_name(field, doc) or "(unknown field)"
                filter_type = None
                value_text = ""
                try:
                    filter_type = safe_str(sched_filter.FilterType)
                except:
                    filter_type = "unknown"
                for attr in ("GetStringValue", "GetDoubleValue", "GetIntegerValue", "GetElementIdValue"):
                    try:
                        value = getattr(sched_filter, attr)()
                        if value not in (None, ""):
                            value_text = safe_str(value)
                            break
                    except:
                        continue
                filters.append({"field": field_name, "operator": filter_type, "value": value_text})
                if len(filters) >= sample_limit:
                    break
            except:
                continue
    except:
        pass
    return filters


def _is_ai_generated_schedule_name(name):
    lower = (name or "").lower()
    prefixes = ["ai ", "ai-", "ai_", "aco pipe schedule", "aco pipe summary", "aco pipe fitting schedule", "aco pipe fitting summary"]
    return any(lower.startswith(prefix) for prefix in prefixes)


def _collect_schedule_inventory(doc, sample_limit=PROJECT_CONTEXT_MAX_ITEMS):
    schedules = []
    ai_generated = []
    template_like = []
    suspicious = []
    total_count = 0
    vendor_tokens = ["aco", "bunge", "geberit", "raminex", "wavin", "prefab", "total", "summary", "sheet", "template"]
    try:
        for schedule in DB.FilteredElementCollector(doc).OfClass(DB.ViewSchedule):
            total_count += 1
            name = get_elem_name(schedule) or "(unnamed schedule)"
            lower = name.lower()
            field_names = _schedule_definition_field_names(schedule, doc)
            filter_summaries = _project_schedule_filter_summaries(schedule, doc)
            sort_fields = _project_schedule_sort_fields(schedule, doc)
            row_count = _project_schedule_body_row_count(schedule)
            tokens = [token for token in vendor_tokens if token in lower or any(token in (field or "").lower() for field in field_names)]
            category_name = None
            try:
                definition = schedule.Definition
                cat_id = definition.CategoryId
                if cat_id and cat_id.IntegerValue > 0:
                    category = DB.Category.GetCategory(doc, cat_id)
                    category_name = get_elem_name(category)
            except:
                pass
            itemized = None
            try:
                itemized = bool(schedule.Definition.IsItemized)
            except:
                pass
            is_ai_generated = _is_ai_generated_schedule_name(name)
            is_template_like = bool("template" in lower or "master" in lower or ("aco" in lower and not is_ai_generated))
            is_suspicious = bool(row_count == 0 or is_ai_generated)
            entry = {
                "id": _safe_element_id_value(schedule),
                "name": name,
                "category": category_name,
                "field_names": field_names[:20],
                "filter_summaries": filter_summaries,
                "sort_group_fields": sort_fields,
                "is_itemized": itemized,
                "body_row_count": row_count,
                "appears_ai_generated": is_ai_generated,
                "appears_template_like": is_template_like,
                "vendor_project_tokens": tokens,
            }
            if len(schedules) < sample_limit:
                schedules.append(entry)
            if is_ai_generated:
                ai_generated.append(name)
            if is_template_like:
                template_like.append(name)
            if is_suspicious:
                suspicious.append({"name": name, "body_row_count": row_count, "reason": "empty or AI-generated"})
    except:
        pass
    return {
        "count": total_count,
        "sampled_count": len(schedules),
        "schedules": schedules,
        "ai_generated_schedule_names": ai_generated[:sample_limit],
        "template_like_schedule_candidates": template_like[:sample_limit],
        "suspicious_or_empty_schedule_candidates": suspicious[:sample_limit],
    }


def _model_path_to_user_visible_text(model_path):
    if model_path is None:
        return "<path not available>"
    try:
        text = DB.ModelPathUtils.ConvertModelPathToUserVisiblePath(model_path)
        if text:
            return safe_str(text)
    except:
        pass
    try:
        text = model_path.GetUserVisiblePath()
        if text:
            return safe_str(text)
    except:
        pass
    text = safe_str(model_path)
    if " object at 0x" in text.lower() or text.startswith("<"):
        return "<path not available>"
    return text or "<path not available>"


def _external_reference_path_text(external_ref):
    if external_ref is None:
        return "<path not available>"
    for getter in ("GetAbsolutePath", "GetPath"):
        try:
            value = getattr(external_ref, getter)()
            text = _model_path_to_user_visible_text(value)
            if text and text != "<path not available>":
                return text
        except:
            continue
    return "<path not available>"


def _xyz_summary(xyz):
    if xyz is None:
        return {"x": "unknown", "y": "unknown", "z": "unknown"}
    return {
        "x": _safe_float(getattr(xyz, "X", None), "unknown"),
        "y": _safe_float(getattr(xyz, "Y", None), "unknown"),
        "z": _safe_float(getattr(xyz, "Z", None), "unknown"),
    }


def _safe_float(value, fallback="unknown"):
    try:
        return float(value)
    except:
        return fallback


def _feet_to_mm_text(value):
    try:
        return "{0:.1f} mm".format(float(value) * 304.8)
    except:
        return "unknown"


def mm_to_internal_feet(mm):
    try:
        return float(mm) / 304.8
    except:
        return 0.0


def internal_feet_to_mm(value):
    try:
        return float(value) * 304.8
    except:
        return "unknown"


def _angle_to_degrees(angle_radians):
    try:
        return float(angle_radians) * 180.0 / math.pi
    except:
        return "unknown"


def _vector_length(vector):
    try:
        return float(vector.GetLength())
    except:
        try:
            return math.sqrt(vector.X * vector.X + vector.Y * vector.Y + vector.Z * vector.Z)
        except:
            return None


def _vector_dot(a, b):
    try:
        return float(a.DotProduct(b))
    except:
        try:
            return float(a.X * b.X + a.Y * b.Y + a.Z * b.Z)
        except:
            return None


def _transform_rotation_z_degrees(transform):
    try:
        basis_x = transform.BasisX
        return math.atan2(float(basis_x.Y), float(basis_x.X)) * 180.0 / math.pi
    except:
        return "unknown"


def _basis_appears_orthonormal(transform):
    try:
        bx = transform.BasisX
        by = transform.BasisY
        bz = transform.BasisZ
        lengths = [_vector_length(bx), _vector_length(by), _vector_length(bz)]
        dots = [_vector_dot(bx, by), _vector_dot(bx, bz), _vector_dot(by, bz)]
        if any(value is None for value in lengths + dots):
            return "unknown"
        return all(abs(length - 1.0) <= PROJECT_CONTEXT_VECTOR_TOLERANCE for length in lengths) and all(abs(dot) <= PROJECT_CONTEXT_VECTOR_TOLERANCE for dot in dots)
    except:
        return "unknown"


def _project_position_summary(doc):
    result = {
        "project_location_name": "unknown",
        "east_west": "unknown",
        "north_south": "unknown",
        "elevation": "unknown",
        "angle_degrees": "unknown",
    }
    try:
        location = doc.ActiveProjectLocation
        result["project_location_name"] = get_elem_name(location) or "unknown"
        position = location.GetProjectPosition(DB.XYZ(0, 0, 0))
        result["east_west"] = _safe_float(position.EastWest)
        result["north_south"] = _safe_float(position.NorthSouth)
        result["elevation"] = _safe_float(position.Elevation)
        result["angle_degrees"] = _angle_to_degrees(position.Angle)
    except:
        pass
    return result


def _base_point_summaries(doc):
    points = {"project_base_point": "unknown", "survey_point": "unknown"}
    try:
        for point in DB.FilteredElementCollector(doc).OfClass(DB.BasePoint):
            entry = {
                "id": _safe_element_id_value(point),
                "name": get_elem_name(point) or "(unnamed point)",
                "position": _xyz_summary(getattr(point, "Position", None)),
                "shared": "unknown",
            }
            try:
                entry["shared"] = bool(point.IsShared)
            except:
                pass
            key = "survey_point" if entry.get("shared") is True else "project_base_point"
            if points.get(key) == "unknown":
                points[key] = entry
    except:
        pass
    return points


def _collect_revit_links(doc, sample_limit=PROJECT_CONTEXT_MAX_ITEMS):
    by_type = {}
    instances = []
    loaded = []
    unavailable = []
    try:
        for instance in DB.FilteredElementCollector(doc).OfClass(DB.RevitLinkInstance):
            instance_name = get_elem_name(instance) or "(unnamed link instance)"
            type_name = None
            link_doc_title = None
            has_doc = False
            try:
                link_type = doc.GetElement(instance.GetTypeId())
                type_name = get_elem_name(link_type)
            except:
                link_type = None
            instance_id = _safe_element_id_value(instance)
            type_id = _safe_element_id_value(link_type) if link_type is not None else None
            try:
                link_doc = instance.GetLinkDocument()
                has_doc = link_doc is not None
                if link_doc is not None:
                    link_doc_title = _document_title(link_doc)
            except:
                pass
            status = "loaded" if has_doc else "unloaded_or_unavailable"
            path_info = ""
            try:
                external_ref = link_type.GetExternalFileReference() if link_type is not None else None
                if external_ref is not None:
                    path_info = _external_reference_path_text(external_ref)
            except:
                pass
            if not path_info:
                path_info = "<path not available>"
            transform_summary = ""
            try:
                origin = instance.GetTransform().Origin
                transform_summary = "origin=({0:.3f}, {1:.3f}, {2:.3f})".format(origin.X, origin.Y, origin.Z)
            except:
                pass
            display_name = link_doc_title or type_name or instance_name or "(unnamed Revit link)"
            key = display_name
            by_type.setdefault(key, 0)
            by_type[key] += 1
            entry = {
                "display_name": display_name,
                "instance_name": instance_name,
                "type_name": type_name,
                "instance_id": instance_id,
                "type_id": type_id,
                "status": status,
                "get_link_document_available": has_doc,
                "linked_document_title": link_doc_title,
                "path_info": path_info,
                "transform_summary": transform_summary,
            }
            if len(instances) < sample_limit:
                instances.append(entry)
            if has_doc:
                loaded.append(entry)
            else:
                unavailable.append(entry)
    except:
        pass
    duplicates = []
    for type_name, count in by_type.items():
        if count > 1:
            duplicates.append({"type_name": type_name, "instance_count": count})
    return {
        "instance_count": sum(by_type.values()),
        "instance_count_by_type": by_type,
        "instances": instances,
        "loaded_links": loaded[:sample_limit],
        "unavailable_links": unavailable[:sample_limit],
        "duplicate_link_types_or_instances": duplicates[:sample_limit],
    }


def _collect_linked_model_coordinate_health(doc, sample_limit=PROJECT_CONTEXT_MAX_ITEMS):
    host = {
        "document_title": _document_title(doc),
        "project_position": _project_position_summary(doc),
    }
    host.update(_base_point_summaries(doc))
    type_counts = {}
    raw = []
    try:
        for instance in DB.FilteredElementCollector(doc).OfClass(DB.RevitLinkInstance):
            link_type = None
            try:
                link_type = doc.GetElement(instance.GetTypeId())
            except:
                pass
            instance_name = get_elem_name(instance) or "(unnamed link instance)"
            type_name = get_elem_name(link_type) if link_type is not None else "unknown"
            link_doc_title = None
            has_doc = False
            try:
                link_doc = instance.GetLinkDocument()
                has_doc = link_doc is not None
                if link_doc is not None:
                    link_doc_title = _document_title(link_doc)
            except:
                pass
            path_info = "<path not available>"
            try:
                external_ref = link_type.GetExternalFileReference() if link_type is not None else None
                path_info = _external_reference_path_text(external_ref)
            except:
                pass
            transform_status = "unknown"
            origin = {"x": "unknown", "y": "unknown", "z": "unknown"}
            origin_display = {"x": "unknown", "y": "unknown", "z": "unknown"}
            rotation_z = "unknown"
            near_zero_origin = "unknown"
            near_zero_z = "unknown"
            close_to_identity = "unknown"
            basis_orthonormal = "unknown"
            flags = []
            try:
                transform = instance.GetTransform()
                transform_status = "read"
                raw_origin = transform.Origin
                origin = _xyz_summary(raw_origin)
                origin_display = {
                    "x": _feet_to_mm_text(origin.get("x")),
                    "y": _feet_to_mm_text(origin.get("y")),
                    "z": _feet_to_mm_text(origin.get("z")),
                }
                rotation_z = _transform_rotation_z_degrees(transform)
                basis_orthonormal = _basis_appears_orthonormal(transform)
                origin_len = math.sqrt(float(raw_origin.X) ** 2 + float(raw_origin.Y) ** 2 + float(raw_origin.Z) ** 2)
                near_zero_origin = origin_len <= PROJECT_CONTEXT_ORIGIN_TOLERANCE_FT
                near_zero_z = abs(float(raw_origin.Z)) <= PROJECT_CONTEXT_Z_TOLERANCE_FT
                rotated = rotation_z != "unknown" and abs(float(rotation_z)) > PROJECT_CONTEXT_ANGLE_TOLERANCE_DEG
                close_to_identity = bool(near_zero_origin and not rotated and basis_orthonormal is True)
                if not close_to_identity:
                    flags.append("non_identity_transform")
                if not near_zero_z:
                    flags.append("z_offset_detected")
                if rotated:
                    flags.append("rotated_link_detected")
                if basis_orthonormal != "unknown" and basis_orthonormal is not True:
                    flags.append("basis_not_orthonormal")
            except:
                flags.append("unknown_coordinate_state")
            if not has_doc:
                flags.append("unloaded_or_unavailable_link")
            key = type_name or instance_name
            type_counts.setdefault(key, 0)
            type_counts[key] += 1
            raw.append(
                {
                    "display_name": link_doc_title or type_name or instance_name,
                    "instance_name": instance_name,
                    "type_name": type_name,
                    "instance_id": _safe_element_id_value(instance),
                    "type_id": _safe_element_id_value(link_type) if link_type is not None else None,
                    "loaded_readable": bool(has_doc),
                    "linked_document_title": link_doc_title,
                    "path_info": path_info,
                    "pinned": _safe_bool_attr(instance, "Pinned"),
                    "transform_status": transform_status,
                    "origin_internal_feet": origin,
                    "origin_display": origin_display,
                    "rotation_z_degrees": rotation_z,
                    "close_to_identity": close_to_identity,
                    "near_zero_origin": near_zero_origin,
                    "near_zero_z": near_zero_z,
                    "basis_orthonormal": basis_orthonormal,
                    "coordinate_flags": sorted(set(flags)),
                }
            )
    except:
        pass
    duplicates = []
    for type_name, count in type_counts.items():
        if count > 1:
            duplicates.append({"type_name": type_name, "instance_count": count})
    links = []
    for item in raw:
        flags = set(item.get("coordinate_flags") or [])
        duplicate_count = type_counts.get(item.get("type_name") or item.get("instance_name"), 0)
        item["duplicate_type_instance_count"] = duplicate_count
        if duplicate_count > 1:
            flags.add("duplicate_link_instances")
        item["coordinate_flags"] = sorted(flags)
        if "unloaded_or_unavailable_link" in flags:
            item["status_label"] = "UNLOADED"
        elif "unknown_coordinate_state" in flags:
            item["status_label"] = "UNKNOWN"
        elif flags:
            item["status_label"] = "REVIEW"
        else:
            item["status_label"] = "OK"
        if len(links) < sample_limit:
            links.append(item)
    non_identity = [item for item in raw if "non_identity_transform" in item.get("coordinate_flags", [])]
    rotated = [item for item in raw if "rotated_link_detected" in item.get("coordinate_flags", [])]
    z_offset = [item for item in raw if "z_offset_detected" in item.get("coordinate_flags", [])]
    unavailable = [item for item in raw if "unloaded_or_unavailable_link" in item.get("coordinate_flags", [])]
    unknown = [item for item in raw if "unknown_coordinate_state" in item.get("coordinate_flags", [])]
    overall_status = "OK"
    if unavailable or unknown:
        overall_status = "UNKNOWN/REVIEW"
    elif non_identity or rotated or z_offset or duplicates:
        overall_status = "REVIEW"
    return {
        "host": host,
        "total_links": len(raw),
        "loaded_readable_count": len(raw) - len(unavailable),
        "unavailable_count": len(unavailable),
        "non_identity_transform_count": len(non_identity),
        "rotated_link_count": len(rotated),
        "z_offset_link_count": len(z_offset),
        "unknown_coordinate_state_count": len(unknown),
        "duplicate_link_instance_count": len(duplicates),
        "duplicate_link_instances": duplicates[:sample_limit],
        "overall_status": overall_status,
        "links": links,
    }


def _safe_bool_attr(obj, attr_name):
    try:
        return bool(getattr(obj, attr_name))
    except:
        return "unknown"


def _normalize_bim_basis_name(name):
    text = safe_str(name or "").strip().lower()
    text = re.sub(r"[^\w\s\-_]+", " ", text)
    text = re.sub(r"[\s\-_]+", " ", text)
    return text.strip()


def _duplicate_normalized_names(rows):
    groups = {}
    for row in rows or []:
        key = row.get("normalized_name") or ""
        if not key:
            continue
        groups.setdefault(key, []).append(row.get("name"))
    duplicates = []
    for key, names in groups.items():
        unique = sorted(set([name for name in names if name]))
        if len(unique) > 1 or len(names) > 1:
            duplicates.append({"normalized_name": key, "names": unique, "count": len(names)})
    return duplicates


def _collect_level_rows(level_doc, sample_limit=MAX_REPORTED_LEVEL_ROWS):
    rows = []
    try:
        for level in DB.FilteredElementCollector(level_doc).OfClass(DB.Level):
            name = get_elem_name(level) or "(unnamed level)"
            elevation = _safe_float(getattr(level, "Elevation", None))
            rows.append(
                {
                    "name": name,
                    "normalized_name": _normalize_bim_basis_name(name),
                    "elevation_internal_feet": elevation,
                    "elevation_mm": internal_feet_to_mm(elevation),
                    "id": _safe_element_id_value(level),
                }
            )
    except:
        pass
    rows.sort(key=lambda item: safe_str(item.get("name")))
    return rows[:sample_limit], len(rows), _duplicate_normalized_names(rows)


def _curve_type_name(curve):
    try:
        if isinstance(curve, DB.Line):
            return "Line"
    except:
        pass
    try:
        if isinstance(curve, DB.Arc):
            return "Arc"
    except:
        pass
    if curve is None:
        return "Unknown"
    return "Other"


def _apply_transform_point(transform, point):
    if transform is None:
        return point
    try:
        return transform.OfPoint(point)
    except:
        return None


def _linear_grid_geometry(grid, transform=None):
    result = {
        "curve_type": "Unknown",
        "origin": None,
        "direction": None,
        "angle_degrees": "unknown",
        "geometry_status": "unknown",
    }
    try:
        curve = grid.Curve
        result["curve_type"] = _curve_type_name(curve)
        if result["curve_type"] != "Line":
            result["geometry_status"] = "name-only"
            return result
        p0 = _apply_transform_point(transform, curve.GetEndPoint(0))
        p1 = _apply_transform_point(transform, curve.GetEndPoint(1))
        if p0 is None or p1 is None:
            result["geometry_status"] = "unknown"
            return result
        dx = float(p1.X) - float(p0.X)
        dy = float(p1.Y) - float(p0.Y)
        length = math.sqrt(dx * dx + dy * dy)
        if length <= 0.0000001:
            result["geometry_status"] = "unknown"
            return result
        ux = dx / length
        uy = dy / length
        result["origin"] = {"x": float(p0.X), "y": float(p0.Y), "z": float(p0.Z)}
        result["direction"] = {"x": ux, "y": uy}
        angle = math.atan2(uy, ux) * 180.0 / math.pi
        if angle < 0:
            angle += 180.0
        if angle >= 180.0:
            angle -= 180.0
        result["angle_degrees"] = angle
        result["geometry_status"] = "linear"
    except:
        result["geometry_status"] = "unknown"
    return result


def _collect_grid_rows(grid_doc, transform=None, sample_limit=MAX_REPORTED_GRID_ROWS):
    rows = []
    try:
        for grid in DB.FilteredElementCollector(grid_doc).OfClass(DB.Grid):
            name = get_elem_name(grid) or "(unnamed grid)"
            geometry = _linear_grid_geometry(grid, transform)
            row = {
                "name": name,
                "normalized_name": _normalize_bim_basis_name(name),
                "id": _safe_element_id_value(grid),
                "curve_type": geometry.get("curve_type"),
                "geometry_status": geometry.get("geometry_status"),
                "origin": geometry.get("origin"),
                "direction": geometry.get("direction"),
                "angle_degrees": geometry.get("angle_degrees"),
            }
            rows.append(row)
    except:
        pass
    rows.sort(key=lambda item: safe_str(item.get("name")))
    return rows[:sample_limit], len(rows), _duplicate_normalized_names(rows)


def _rows_by_normalized_name(rows):
    mapping = {}
    for row in rows or []:
        key = row.get("normalized_name") or ""
        if key and key not in mapping:
            mapping[key] = row
    return mapping


def _level_elevation_delta_mm(host_level, linked_level):
    host_elev = host_level.get("elevation_internal_feet")
    link_elev = linked_level.get("elevation_internal_feet")
    if not isinstance(host_elev, float) or not isinstance(link_elev, float):
        return "unknown"
    return abs(internal_feet_to_mm(host_elev - link_elev))


def _compare_level_rows(host_levels, link_levels):
    findings = []
    host_by_name = _rows_by_normalized_name(host_levels)
    link_by_name = _rows_by_normalized_name(link_levels)
    matched_host = set()
    matched_link = set()
    tolerance = float(LEVEL_ELEVATION_TOLERANCE_MM)
    for key, host_level in host_by_name.items():
        linked_level = link_by_name.get(key)
        if linked_level:
            matched_host.add(key)
            matched_link.add(key)
            delta = _level_elevation_delta_mm(host_level, linked_level)
            if delta != "unknown" and delta > tolerance:
                findings.append(
                    {
                        "type": "linked_level_elevation_mismatch",
                        "severity": "high" if delta > 50.0 else "medium",
                        "host_level": host_level.get("name"),
                        "linked_level": linked_level.get("name"),
                        "delta_mm": delta,
                        "message": "Matching level names have elevation delta {0:.1f} mm.".format(delta),
                    }
                )
        else:
            findings.append({"type": "host_level_missing_in_link", "severity": "medium", "host_level": host_level.get("name"), "message": "Host level is missing in linked model."})
    for key, linked_level in link_by_name.items():
        if key not in matched_link:
            findings.append({"type": "linked_level_missing_in_host", "severity": "medium", "linked_level": linked_level.get("name"), "message": "Linked level is missing in host model."})
    comparisons = 0
    for host_level in host_levels or []:
        for linked_level in link_levels or []:
            if comparisons >= MAX_REPORTED_LEVEL_ROWS:
                break
            if host_level.get("normalized_name") == linked_level.get("normalized_name"):
                continue
            delta = _level_elevation_delta_mm(host_level, linked_level)
            if delta != "unknown" and delta <= tolerance:
                findings.append(
                    {
                        "type": "linked_level_possible_name_mismatch",
                        "severity": "low",
                        "host_level": host_level.get("name"),
                        "linked_level": linked_level.get("name"),
                        "delta_mm": delta,
                        "message": "Host and linked levels share elevation but have different names.",
                    }
                )
                comparisons += 1
        if comparisons >= MAX_REPORTED_LEVEL_ROWS:
            break
    return findings[:MAX_REPORTED_LEVEL_ROWS]


def _angle_delta_degrees(a, b):
    try:
        delta = abs(float(a) - float(b))
        while delta > 180.0:
            delta -= 180.0
        if delta > 90.0:
            delta = 180.0 - delta
        return abs(delta)
    except:
        return "unknown"


def _linear_grid_offset_mm(host_grid, linked_grid):
    try:
        h_origin = host_grid.get("origin")
        h_dir = host_grid.get("direction")
        l_origin = linked_grid.get("origin")
        if not h_origin or not h_dir or not l_origin:
            return "unknown"
        nx = -float(h_dir.get("y"))
        ny = float(h_dir.get("x"))
        dx = float(l_origin.get("x")) - float(h_origin.get("x"))
        dy = float(l_origin.get("y")) - float(h_origin.get("y"))
        offset_ft = abs(dx * nx + dy * ny)
        return internal_feet_to_mm(offset_ft)
    except:
        return "unknown"


def _compare_grid_rows(host_grids, link_grids):
    findings = []
    host_by_name = _rows_by_normalized_name(host_grids)
    link_by_name = _rows_by_normalized_name(link_grids)
    comparisons = 0
    for key, host_grid in host_by_name.items():
        linked_grid = link_by_name.get(key)
        if not linked_grid:
            findings.append({"type": "host_grid_missing_in_link", "severity": "medium", "host_grid": host_grid.get("name"), "message": "Host grid is missing in linked model."})
            continue
        if host_grid.get("geometry_status") == "linear" and linked_grid.get("geometry_status") == "linear" and comparisons < MAX_GRID_GEOMETRY_COMPARISONS_PER_LINK:
            comparisons += 1
            angle_delta = _angle_delta_degrees(host_grid.get("angle_degrees"), linked_grid.get("angle_degrees"))
            offset_delta = _linear_grid_offset_mm(host_grid, linked_grid)
            if angle_delta == "unknown" or offset_delta == "unknown":
                findings.append({"type": "linked_grid_geometry_unknown", "severity": "low", "host_grid": host_grid.get("name"), "linked_grid": linked_grid.get("name"), "message": "Linear grid geometry comparison could not be completed."})
            elif angle_delta > GRID_ANGLE_TOLERANCE_DEG or offset_delta > GRID_LINEAR_OFFSET_TOLERANCE_MM:
                severity = "high" if offset_delta > 50.0 or angle_delta > 1.0 else "medium"
                findings.append(
                    {
                        "type": "linked_grid_geometry_mismatch",
                        "severity": severity,
                        "host_grid": host_grid.get("name"),
                        "linked_grid": linked_grid.get("name"),
                        "angle_delta_degrees": angle_delta,
                        "offset_delta_mm": offset_delta,
                        "message": "Matching grid names have geometry delta outside tolerance.",
                    }
                )
        else:
            findings.append(
                {
                    "type": "linked_grid_geometry_unknown",
                    "severity": "info",
                    "host_grid": host_grid.get("name"),
                    "linked_grid": linked_grid.get("name"),
                    "message": "Geometry comparison limited for non-linear or unknown grid.",
                }
            )
    for key, linked_grid in link_by_name.items():
        if key not in host_by_name:
            findings.append({"type": "linked_grid_missing_in_host", "severity": "medium", "linked_grid": linked_grid.get("name"), "message": "Linked grid is missing in host model."})
    return findings[:MAX_REPORTED_GRID_ROWS]


def _level_grid_finding_name(finding):
    if not isinstance(finding, dict):
        return None
    for key in ("host_level", "linked_level", "host_grid", "linked_grid", "name"):
        value = finding.get(key)
        if value:
            return value
    names = finding.get("names")
    if isinstance(names, list) and names:
        return ", ".join([str(name) for name in names[:3]])
    return None


def _aggregate_level_grid_findings(findings, sample_limit=5):
    grouped = {}
    ordered = []
    for finding in findings or []:
        if not isinstance(finding, dict):
            continue
        key = (finding.get("type") or "unknown", finding.get("severity") or "info")
        if key not in grouped:
            grouped[key] = {
                "type": key[0],
                "severity": key[1],
                "count": 0,
                "sample_names": [],
                "sample_messages": [],
            }
            ordered.append(grouped[key])
        row = grouped[key]
        row["count"] += 1
        name = _level_grid_finding_name(finding)
        if name and name not in row["sample_names"] and len(row["sample_names"]) < sample_limit:
            row["sample_names"].append(name)
        message = finding.get("message")
        if message and message not in row["sample_messages"] and len(row["sample_messages"]) < 2:
            row["sample_messages"].append(message)
    return sorted(ordered, key=lambda item: (-item.get("count", 0), item.get("type", "")))


def _level_grid_findings_by_type(findings):
    counts = {}
    for finding in findings or []:
        if not isinstance(finding, dict):
            continue
        key = finding.get("type") or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _format_level_grid_type_counts(counts):
    if not counts:
        return "none"
    return ", ".join(["{0}={1}".format(key, counts[key]) for key in sorted(counts.keys())])


def _link_level_grid_issue_types(link_row):
    counts = _level_grid_findings_by_type((link_row.get("level_findings") or []) + (link_row.get("grid_findings") or []))
    return _format_level_grid_type_counts(counts)


def _collect_host_link_level_grid_health(doc, sample_limit=PROJECT_CONTEXT_MAX_ITEMS):
    host_levels, host_level_count, host_level_duplicates = _collect_level_rows(doc)
    host_grids, host_grid_count, host_grid_duplicates = _collect_grid_rows(doc)
    compared = []
    skipped = []
    all_level_findings = []
    all_grid_findings = []
    link_index = 0
    try:
        link_instances = list(DB.FilteredElementCollector(doc).OfClass(DB.RevitLinkInstance))
    except:
        link_instances = []
    for instance in link_instances:
        if link_index >= MAX_LINKS_FOR_LEVEL_GRID_SCAN:
            skipped.append({"link_name": get_elem_name(instance) or "(unnamed link)", "status": "skipped", "reason": "link scan limit reached"})
            continue
        link_index += 1
        link_type = None
        try:
            link_type = doc.GetElement(instance.GetTypeId())
        except:
            pass
        link_name = get_elem_name(instance) or "(unnamed link)"
        type_name = get_elem_name(link_type) if link_type is not None else "unknown"
        transform = None
        try:
            transform = instance.GetTransform()
        except:
            pass
        try:
            link_doc = instance.GetLinkDocument()
        except:
            link_doc = None
        if link_doc is None:
            skipped.append({"link_name": link_name, "type_name": type_name, "status": "inaccessible", "reason": "linked document is not loaded/readable"})
            continue
        linked_title = _document_title(link_doc)
        link_levels, link_level_count, link_level_duplicates = _collect_level_rows(link_doc)
        link_grids, link_grid_count, link_grid_duplicates = _collect_grid_rows(link_doc, transform)
        level_findings = _compare_level_rows(host_levels, link_levels)
        grid_findings = _compare_grid_rows(host_grids, link_grids)
        if host_grid_count == 0 and link_grid_count > 0:
            for finding in grid_findings:
                if finding.get("type") == "linked_grid_missing_in_host":
                    finding["severity"] = "info"
                    finding["message"] = "Host contains no grids; linked grid comparison is basis-dependent."
        for duplicate in link_level_duplicates:
            level_findings.append({"type": "linked_duplicate_level_names", "severity": "medium", "message": "Duplicate normalized level names in linked model.", "names": duplicate.get("names")})
        for duplicate in link_grid_duplicates:
            grid_findings.append({"type": "linked_duplicate_grid_names", "severity": "medium", "message": "Duplicate normalized grid names in linked model.", "names": duplicate.get("names")})
        status = "OK"
        if level_findings or grid_findings:
            status = "PARTIAL" if all((finding.get("severity") == "info") for finding in (level_findings + grid_findings)) else "REVIEW"
        compared.append(
            {
                "link_name": link_name,
                "type_name": type_name,
                "linked_document_title": linked_title,
                "status": status,
                "coordinate_health_status": "unknown",
                "level_count": link_level_count,
                "grid_count": link_grid_count,
                "level_findings": level_findings[:MAX_REPORTED_LEVEL_ROWS],
                "grid_findings": grid_findings[:MAX_REPORTED_GRID_ROWS],
            }
        )
        all_level_findings.extend(level_findings)
        all_grid_findings.extend(grid_findings)
    for duplicate in host_level_duplicates:
        all_level_findings.append({"type": "host_duplicate_level_names", "severity": "medium", "message": "Duplicate normalized level names in host model.", "names": duplicate.get("names")})
    for duplicate in host_grid_duplicates:
        all_grid_findings.append({"type": "host_duplicate_grid_names", "severity": "medium", "message": "Duplicate normalized grid names in host model.", "names": duplicate.get("names")})
    grid_basis_limited = host_grid_count == 0 and any((row.get("grid_count") or 0) > 0 for row in compared)
    overall = "OK"
    if skipped and not compared:
        overall = "PARTIAL"
    elif skipped:
        overall = "PARTIAL"
    if all_level_findings or all_grid_findings:
        overall = "REVIEW" if not skipped else "PARTIAL"
    if grid_basis_limited:
        overall = "PARTIAL"
    issue_flags = sorted(set([item.get("type") for item in all_level_findings + all_grid_findings if item.get("type")]))
    level_type_counts = _level_grid_findings_by_type(all_level_findings)
    grid_type_counts = _level_grid_findings_by_type(all_grid_findings)
    top_links = sorted(
        [
            {
                "link_name": row.get("link_name"),
                "status": row.get("status"),
                "level_findings_count": len(row.get("level_findings") or []),
                "grid_findings_count": len(row.get("grid_findings") or []),
                "issue_types": _link_level_grid_issue_types(row),
            }
            for row in compared
            if (row.get("level_findings") or row.get("grid_findings"))
        ],
        key=lambda item: -(item.get("level_findings_count", 0) + item.get("grid_findings_count", 0)),
    )
    return {
        "profile": "Configurable BIM Basis / ILS-style",
        "level_elevation_tolerance_mm": LEVEL_ELEVATION_TOLERANCE_MM,
        "grid_linear_offset_tolerance_mm": GRID_LINEAR_OFFSET_TOLERANCE_MM,
        "grid_angle_tolerance_degrees": GRID_ANGLE_TOLERANCE_DEG,
        "host_level_count": host_level_count,
        "host_grid_count": host_grid_count,
        "host_duplicate_level_names": host_level_duplicates[:MAX_REPORTED_LEVEL_ROWS],
        "host_duplicate_grid_names": host_grid_duplicates[:MAX_REPORTED_GRID_ROWS],
        "links_compared_count": len(compared),
        "links_skipped_count": len(skipped),
        "links_compared": compared[:sample_limit],
        "links_skipped": skipped[:sample_limit],
        "level_findings_count": len(all_level_findings),
        "grid_findings_count": len(all_grid_findings),
        "level_findings": all_level_findings[:MAX_REPORTED_LEVEL_ROWS],
        "grid_findings": all_grid_findings[:MAX_REPORTED_GRID_ROWS],
        "level_findings_by_type": level_type_counts,
        "grid_findings_by_type": grid_type_counts,
        "top_affected_links": top_links[:sample_limit],
        "host_grid_basis_limited": grid_basis_limited,
        "status_wording": {
            "OK": "OK: no findings",
            "REVIEW": "REVIEW: findings exist and require coordination review",
            "PARTIAL": "PARTIAL: comparison limited by missing host grids, inaccessible links, or non-linear grids",
            "UNKNOWN": "UNKNOWN: required data unavailable",
        },
        "issue_flags": issue_flags,
        "overall_status": overall,
    }


def _collect_imports_context(doc, uidoc, sample_limit=PROJECT_CONTEXT_MAX_ITEMS):
    imports = []
    total_count = 0
    linked_count = 0
    imported_count = 0
    view_specific_count = 0
    layer_candidates = []
    active_view_id = None
    try:
        active_view_id = uidoc.ActiveView.Id
    except:
        pass
    try:
        for import_instance in DB.FilteredElementCollector(doc).OfClass(DB.ImportInstance):
            total_count += 1
            name = get_elem_name(import_instance) or "(unnamed import)"
            type_name = None
            try:
                type_name = get_elem_name(doc.GetElement(import_instance.GetTypeId()))
            except:
                pass
            category_name = _category_name(import_instance)
            linked = None
            try:
                linked = bool(import_instance.IsLinked)
            except:
                pass
            owner_view_id = _safe_element_id_value(getattr(import_instance, "OwnerViewId", None))
            owner_view_name = None
            if owner_view_id and owner_view_id > 0:
                try:
                    owner_view = doc.GetElement(import_instance.OwnerViewId)
                    owner_view_name = get_elem_name(owner_view)
                    view_specific_count += 1
                except:
                    pass
            if linked:
                linked_count += 1
            elif linked is False:
                imported_count += 1
            subcategories = []
            try:
                category = import_instance.Category
                for subcategory in category.SubCategories:
                    sub_name = get_elem_name(subcategory)
                    if sub_name:
                        subcategories.append(sub_name)
                        if len(layer_candidates) < sample_limit:
                            layer_candidates.append(sub_name)
                    if len(subcategories) >= 20:
                        break
            except:
                pass
            visible_in_active_view = "unknown"
            try:
                if active_view_id and owner_view_id:
                    visible_in_active_view = bool(owner_view_id == active_view_id.IntegerValue)
            except:
                pass
            if len(imports) < sample_limit:
                imports.append(
                    {
                        "instance_name": name,
                        "type_name": type_name,
                        "category": category_name,
                        "owner_view_id": owner_view_id,
                        "owner_view_name": owner_view_name,
                        "linked": linked,
                        "visible_in_active_view": visible_in_active_view,
                        "layer_or_subcategory_samples": subcategories[:20],
                    }
                )
    except:
        pass
    return {
        "total_import_instances": total_count,
        "linked_cad_count": linked_count,
        "imported_cad_count": imported_count,
        "view_specific_import_count": view_specific_count,
        "imports": imports,
        "imported_category_or_layer_candidates": sorted(set(layer_candidates))[:sample_limit],
    }


def _core_category_configs():
    return [
        ("Pipes", "OST_PipeCurves"),
        ("Pipe Fittings", "OST_PipeFitting"),
        ("Pipe Accessories", "OST_PipeAccessory"),
        ("Ducts", "OST_DuctCurves"),
        ("Duct Fittings", "OST_DuctFitting"),
        ("Duct Accessories", "OST_DuctAccessory"),
        ("Mechanical Equipment", "OST_MechanicalEquipment"),
        ("Electrical Fixtures", "OST_ElectricalFixtures"),
        ("Electrical Equipment", "OST_ElectricalEquipment"),
        ("Conduits", "OST_Conduit"),
        ("Cable Trays", "OST_CableTray"),
        ("Plumbing Fixtures", "OST_PlumbingFixtures"),
        ("Generic Models", "OST_GenericModel"),
        ("Structural Framing", "OST_StructuralFraming"),
        ("Floors", "OST_Floors"),
        ("Walls", "OST_Walls"),
        ("Doors", "OST_Doors"),
        ("Windows", "OST_Windows"),
        ("Revit Links", "OST_RvtLinks"),
        ("Import Instances", "OST_ImportObjectStyles"),
    ]


def _category_element_count(doc, bic, view_id=None):
    try:
        collector = DB.FilteredElementCollector(doc, view_id) if view_id is not None else DB.FilteredElementCollector(doc)
        return collector.OfCategory(bic).WhereElementIsNotElementType().GetElementCount()
    except:
        try:
            collector = DB.FilteredElementCollector(doc, view_id) if view_id is not None else DB.FilteredElementCollector(doc)
            return len(list(collector.OfCategory(bic).WhereElementIsNotElementType()))
        except:
            return "unknown"


def _collect_core_category_counts(doc, uidoc):
    selected_ids = set([_safe_element_id_value(item) for item in _collect_selection_ids(uidoc)])
    active_view_id = None
    try:
        active_view_id = uidoc.ActiveView.Id
    except:
        pass
    rows = []
    for label, bic_name in _core_category_configs():
        try:
            bic = getattr(DB.BuiltInCategory, bic_name)
        except:
            rows.append({"category": label, "total_count": "category unavailable", "active_view_count": "unknown", "selected_count": 0})
            continue
        total = _category_element_count(doc, bic)
        active_count = _category_element_count(doc, bic, active_view_id) if active_view_id is not None else "unknown"
        selected_count = 0
        if selected_ids:
            try:
                for elem_id in _collect_selection_ids(uidoc):
                    elem = doc.GetElement(elem_id)
                    try:
                        if elem and elem.Category and elem.Category.Id.IntegerValue == int(bic):
                            selected_count += 1
                    except:
                        continue
            except:
                selected_count = "unknown"
        rows.append({"category": label, "total_count": total, "active_view_count": active_count, "selected_count": selected_count})
    return rows


def _collect_selection_summary(doc, uidoc, sample_limit=PROJECT_CONTEXT_MAX_ITEMS):
    ids = _collect_selection_ids(uidoc)
    categories = {}
    types = {}
    levels = {}
    samples = []
    for elem_id in ids[:sample_limit]:
        try:
            elem = doc.GetElement(elem_id)
        except:
            elem = None
        if elem is None:
            continue
        cat_name = _category_name(elem)
        type_name = _family_and_type_text(doc, elem) or "(unknown type)"
        level_name = _safe_param_text(elem, ["Reference Level", "Level", "Schedule Level", "Base Level"])
        categories[cat_name] = categories.get(cat_name, 0) + 1
        types[type_name] = types.get(type_name, 0) + 1
        if level_name:
            levels[level_name] = levels.get(level_name, 0) + 1
        samples.append({"id": _safe_element_id_value(elem), "category": cat_name, "type": type_name, "level": level_name})
    return {
        "selected_element_count": len(ids),
        "selected_categories": categories,
        "selected_family_type_names": types,
        "selected_levels_or_reference_levels": levels,
        "samples": samples,
        "sample_limit": sample_limit,
    }


def _collect_warning_summary(doc, sample_limit=10):
    groups = {}
    total = 0
    try:
        warnings = list(doc.GetWarnings())
        total = len(warnings)
        for warning in warnings:
            try:
                description = warning.GetDescriptionText()
            except:
                description = "(unknown warning)"
            group = groups.setdefault(description, {"count": 0, "example_element_ids": []})
            group["count"] += 1
            try:
                failing_ids = list(warning.GetFailingElements())
                for elem_id in failing_ids:
                    value = _safe_element_id_value(elem_id)
                    if value is not None and len(group["example_element_ids"]) < 5:
                        group["example_element_ids"].append(value)
            except:
                pass
    except:
        pass
    ordered = []
    for description, value in groups.items():
        ordered.append({"description": description, "count": value.get("count", 0), "example_element_ids": value.get("example_element_ids", [])})
    ordered.sort(key=lambda item: -item.get("count", 0))
    return {"total_warning_count": total, "top_warning_groups": ordered[:sample_limit]}


def _top_category_counts(category_counts, limit=8):
    numeric = []
    for item in category_counts or []:
        total = item.get("total_count")
        if isinstance(total, int) and total > 0:
            numeric.append((total, item.get("category")))
    numeric.sort(reverse=True)
    return numeric[:limit]


def _get_linked_model_coordinate_health(context):
    if not isinstance(context, dict):
        return {}
    sections = context.get("sections") or {}
    if isinstance(sections, dict):
        value = sections.get("linked_model_coordinate_health")
        if isinstance(value, dict):
            return value
    value = context.get("linked_model_coordinate_health")
    if isinstance(value, dict):
        return value
    return {}


def _get_host_link_level_grid_health(context):
    return get_host_link_level_grid_health(context)


def get_host_link_level_grid_health(context):
    if not isinstance(context, dict):
        return {}
    sections = context.get("sections") or {}
    if isinstance(sections, dict):
        value = sections.get("host_link_level_grid_health")
        if isinstance(value, dict):
            return value
        value = sections.get("bim_basis_level_grid_health")
        if isinstance(value, dict):
            return value
    value = context.get("host_link_level_grid_health")
    if isinstance(value, dict):
        return value
    value = context.get("bim_basis_level_grid_health")
    if isinstance(value, dict):
        return value
    return {}


def _detect_project_issues(context):
    issues = []
    levels = context.get("levels", {})
    if levels.get("ambiguous_aliases"):
        issues.append({"id": "ambiguous_level_names", "severity": "medium", "message": "Ambiguous level aliases detected."})
    links = context.get("links", {})
    if links.get("unavailable_links"):
        issues.append({"id": "unloaded_or_unavailable_revit_links", "severity": "medium", "message": "One or more Revit links appear unloaded or unavailable."})
    imports = context.get("imports_cad", {})
    if imports.get("total_import_instances", 0):
        issues.append({"id": "cad_imports_present", "severity": "low", "message": "CAD/import instances are present."})
    if imports.get("view_specific_import_count", 0):
        issues.append({"id": "view_specific_imports_present", "severity": "low", "message": "View-specific CAD/import instances are present."})
    schedules = context.get("schedules", {})
    ai_generated = schedules.get("ai_generated_schedule_names") or []
    suspicious = schedules.get("suspicious_or_empty_schedule_candidates") or []
    template_like = schedules.get("template_like_schedule_candidates") or []
    if len(ai_generated) >= 5:
        issues.append({"id": "many_ai_generated_schedules", "severity": "low", "message": "Multiple AI-generated schedules were detected."})
    if suspicious:
        issues.append({"id": "empty_or_suspicious_schedules", "severity": "medium", "message": "Empty or suspicious schedules were detected."})
    if any("aco" in name.lower() or "bunge" in name.lower() for name in template_like):
        issues.append({"id": "aco_bunge_templates_present", "severity": "info", "message": "ACO/Bunge schedule templates appear to be present."})
    if template_like and not any("aco pipe schedule" == name.lower().strip() for name in template_like):
        issues.append({"id": "no_neutral_aco_master_template", "severity": "info", "message": "No neutral all-ACO pipe master template was observed in the sampled schedule candidates."})
    selection = context.get("selection", {})
    if selection.get("selected_element_count", 0):
        issues.append({"id": "selection_present", "severity": "info", "message": "Active-document selection is present."})
    else:
        issues.append({"id": "no_selection", "severity": "info", "message": "No active-document selection was found."})
    warnings = context.get("warnings_summary", {})
    if warnings.get("total_warning_count", 0) and warnings.get("total_warning_count", 0) >= 100:
        issues.append({"id": "high_warning_count", "severity": "medium", "message": "High Revit warning count detected."})
    coord_health = _get_linked_model_coordinate_health(context)
    if coord_health:
        if coord_health.get("unavailable_count", 0):
            issues.append({"id": "unloaded_or_unavailable_link", "severity": "medium", "message": "One or more Revit links are unloaded or unavailable."})
        if coord_health.get("non_identity_transform_count", 0):
            issues.append({"id": "non_identity_link_transform", "severity": "medium", "message": "One or more Revit links have non-identity transforms."})
        if coord_health.get("z_offset_link_count", 0):
            issues.append({"id": "link_z_offset_detected", "severity": "medium", "message": "One or more Revit links have a Z offset."})
        if coord_health.get("rotated_link_count", 0):
            issues.append({"id": "rotated_link_detected", "severity": "medium", "message": "One or more Revit links are rotated."})
        if coord_health.get("duplicate_link_instance_count", 0):
            issues.append({"id": "duplicate_link_instances", "severity": "medium", "message": "Duplicate Revit link instances/types were detected."})
        if coord_health.get("unknown_coordinate_state_count", 0):
            issues.append({"id": "unknown_coordinate_state", "severity": "low", "message": "Some link coordinate state could not be read."})
    level_grid = _get_host_link_level_grid_health(context)
    if level_grid:
        for flag in level_grid.get("issue_flags", [])[:12]:
            severity = "medium"
            if flag == "linked_level_elevation_mismatch":
                severity = "high"
            if flag in ("linked_level_possible_name_mismatch", "linked_grid_geometry_unknown"):
                severity = "low"
            if flag == "linked_grid_missing_in_host" and level_grid.get("host_grid_basis_limited"):
                severity = "info"
            issues.append({"id": flag, "severity": severity, "message": "BIM Basis / Levels & Grids finding: {0}".format(flag.replace("_", " "))})
        if level_grid.get("links_skipped_count", 0):
            issues.append({"id": "level_grid_scan_partial", "severity": "medium", "message": "Some linked models were skipped or inaccessible during level/grid comparison."})
    return issues


def _project_first_check_lines(context):
    issues = set([issue.get("id") for issue in context.get("detected_issues", [])])
    checks = []
    if "unloaded_or_unavailable_revit_links" in issues:
        checks.append("Review unloaded or unavailable Revit links before coordination checks.")
    if "cad_imports_present" in issues or "view_specific_imports_present" in issues:
        checks.append("Review CAD/import instances and view-specific imports for coordination risk.")
    if "empty_or_suspicious_schedules" in issues:
        checks.append("Inspect empty, suspicious, or AI-generated schedules before using them as templates.")
    schedules = context.get("schedules", {})
    schedule_samples = schedules.get("schedules", []) or []
    if schedule_samples:
        populated = len([item for item in schedule_samples if isinstance(item.get("body_row_count"), int) and item.get("body_row_count") > 0])
        empty = len([item for item in schedule_samples if item.get("body_row_count") == 0])
        unknown = len([item for item in schedule_samples if not isinstance(item.get("body_row_count"), int)])
        checks.append("Review schedule population/status: sampled {0}, populated {1}, empty {2}, unknown {3}.".format(len(schedule_samples), populated, empty, unknown))
    if "ambiguous_level_names" in issues:
        checks.append("Resolve level naming ambiguity before level-targeted schedule/template actions.")
    if context.get("warnings_summary", {}).get("total_warning_count", 0):
        checks.append("Review the top Revit warning groups.")
    categories = context.get("category_counts", []) or []
    if any(item.get("category") in ("Pipes", "Pipe Fittings", "Ducts", "Duct Fittings") and isinstance(item.get("total_count"), int) and item.get("total_count") > 0 for item in categories):
        checks.append("Run read-only MEP health checks or schedule summaries before modifying model data.")
    if not context.get("selection", {}).get("selected_element_count", 0):
        checks.append("Select elements before running selection-based reviewed checks, or use active-view/project checks first.")
    if not checks:
        checks.append("Run a standard project scan, then start with read-only active-view health checks.")
    return checks


def _onboarding_section(title, status, result, next_step, branch):
    return {
        "title": title,
        "status": status,
        "result": result,
        "next_step": next_step,
        "branch": branch,
    }


def _onboarding_status_rank(status):
    ranks = {"OK": 0, "INFO": 1, "UNKNOWN": 2, "PARTIAL": 3, "REVIEW": 4}
    return ranks.get(status, 2)


def build_project_onboarding_checklist(context):
    context = context if isinstance(context, dict) else {}
    doc_info = context.get("document", {}) if isinstance(context.get("document", {}), dict) else {}
    active_view = doc_info.get("active_view", {}) if isinstance(doc_info.get("active_view", {}), dict) else {}
    links = context.get("links", {}) if isinstance(context.get("links", {}), dict) else {}
    coord_health = _get_linked_model_coordinate_health(context)
    level_grid = get_host_link_level_grid_health(context)
    imports = context.get("imports_cad", {}) if isinstance(context.get("imports_cad", {}), dict) else {}
    warnings = context.get("warnings_summary", {}) if isinstance(context.get("warnings_summary", {}), dict) else {}
    schedules = context.get("schedules", {}) if isinstance(context.get("schedules", {}), dict) else {}
    levels = context.get("levels", {}) if isinstance(context.get("levels", {}), dict) else {}
    selection = context.get("selection", {}) if isinstance(context.get("selection", {}), dict) else {}
    issues = set([issue.get("id") for issue in context.get("detected_issues", []) if isinstance(issue, dict)])
    sections = []
    depth = context.get("scan_depth") or "unknown"
    if depth == "standard":
        scan_status = "OK"
        scan_result = "Standard project context is available."
    elif depth:
        scan_status = "PARTIAL"
        scan_result = "{0} context is available; standard Scan Project is recommended for onboarding.".format(str(depth).title())
    else:
        scan_status = "UNKNOWN"
        scan_result = "No project context scan is available."
    sections.append(_onboarding_section("Project Context Scan", scan_status, scan_result, "Run Scan Project before production decisions.", "Project"))

    link_total = links.get("instance_count")
    if link_total is None:
        link_status = "UNKNOWN"
        link_result = "Revit links were not scanned."
        link_next = "Run Scan Project."
    elif link_total == 0:
        link_status = "INFO"
        link_result = "No Revit link instances were found."
        link_next = "No link loading issue detected."
    elif links.get("unavailable_links"):
        link_status = "REVIEW"
        link_result = "{0} links found; {1} unavailable/unloaded.".format(link_total, len(links.get("unavailable_links") or []))
        link_next = "Review unloaded/unavailable links before coordination checks."
    else:
        link_status = "OK"
        link_result = "{0} links found; 0 unavailable/unloaded.".format(link_total)
        link_next = "No link loading issue detected."
    sections.append(_onboarding_section("Linked Models", link_status, link_result, link_next, "Links"))

    if coord_health:
        coord_status = coord_health.get("overall_status", "UNKNOWN")
        if coord_status == "OK":
            result = "No deterministic coordinate transform flags were detected."
            next_step = "Verify manually if the project BIM protocol requires it."
        elif coord_status == "PARTIAL":
            result = "Coordinate health scan was partial."
            next_step = "Review unavailable links and unknown coordinate states."
        else:
            result = "Coordinate flags: non-identity {0}, rotated {1}, z-offset {2}, unavailable {3}.".format(
                coord_health.get("non_identity_transform_count", 0),
                coord_health.get("rotated_link_count", 0),
                coord_health.get("z_offset_link_count", 0),
                coord_health.get("unavailable_count", 0),
            )
            next_step = "Review linked model coordinate health before model coordination."
        sections.append(_onboarding_section("Link Coordinate Health", coord_status if coord_status in ("OK", "REVIEW", "PARTIAL") else "UNKNOWN", result, next_step, "Links / Coordinate Health"))
    else:
        sections.append(_onboarding_section("Link Coordinate Health", "UNKNOWN", "Linked model coordinate health was not scanned.", "Run standard Scan Project.", "Links / Coordinate Health"))

    if level_grid:
        lg_status = level_grid.get("overall_status", "UNKNOWN")
        if level_grid.get("level_findings_count", 0) or level_grid.get("grid_findings_count", 0):
            lg_status = "PARTIAL" if lg_status == "PARTIAL" or level_grid.get("host_grid_basis_limited") else "REVIEW"
        result = "{0} level findings, {1} grid findings, {2} links compared.".format(
            level_grid.get("level_findings_count", 0),
            level_grid.get("grid_findings_count", 0),
            level_grid.get("links_compared_count", 0),
        )
        next_step = "Review host/link level and grid ownership before level-targeted automation."
        if not level_grid.get("level_findings_count", 0) and not level_grid.get("grid_findings_count", 0):
            next_step = "No level/grid findings detected by current checks."
        sections.append(_onboarding_section("BIM Basis / Levels & Grids", lg_status, result, next_step, "BIM Basis / Levels & Grids"))
    else:
        sections.append(_onboarding_section("BIM Basis / Levels & Grids", "UNKNOWN", "Level/grid health was not scanned.", "Run standard Scan Project.", "BIM Basis / Levels & Grids"))

    import_count = imports.get("total_import_instances")
    if import_count is None:
        sections.append(_onboarding_section("CAD / Imports", "UNKNOWN", "CAD/import instances were not scanned.", "Run standard Scan Project.", "Imports / CAD"))
    elif import_count == 0:
        sections.append(_onboarding_section("CAD / Imports", "OK", "No CAD/import instances found.", "No CAD/import review needed from current scan.", "Imports / CAD"))
    else:
        note = "{0} CAD/import instances found; {1} view-specific.".format(import_count, imports.get("view_specific_import_count", 0))
        sections.append(_onboarding_section("CAD / Imports", "REVIEW", note, "Review CAD/import ownership and view-specific imports.", "Imports / CAD"))

    warning_count = warnings.get("total_warning_count")
    if warning_count is None:
        sections.append(_onboarding_section("Revit Warnings", "UNKNOWN", "Warnings were not scanned.", "Run standard Scan Project.", "Warnings"))
    elif warning_count == 0:
        sections.append(_onboarding_section("Revit Warnings", "OK", "0 Revit warnings detected by scanner.", "No warning review needed from current scan.", "Warnings"))
    else:
        sections.append(_onboarding_section("Revit Warnings", "REVIEW", "{0} Revit warnings detected.".format(warning_count), "Review top warning groups before production automation.", "Warnings"))

    schedule_samples = schedules.get("schedules")
    if schedule_samples is None:
        sections.append(_onboarding_section("Schedules", "UNKNOWN", "Schedules were not scanned.", "Run standard Scan Project.", "Schedules"))
    elif not schedule_samples:
        sections.append(_onboarding_section("Schedules", "INFO", "No schedule details were sampled.", "Use schedule-specific checks if needed.", "Schedules"))
    elif schedules.get("suspicious_or_empty_schedule_candidates"):
        sections.append(_onboarding_section("Schedules", "REVIEW", "{0} sampled schedules; suspicious/empty candidates found.".format(len(schedule_samples)), "Review schedule population/status before using templates.", "Schedules"))
    else:
        sections.append(_onboarding_section("Schedules", "OK", "{0} sampled schedules; no suspicious/empty schedule candidates in current scan.".format(len(schedule_samples)), "Schedule actions can follow after context risks are reviewed.", "Schedules"))

    level_issues = []
    if levels.get("ambiguous_aliases"):
        level_issues.append("ambiguous level aliases")
    if level_grid.get("host_duplicate_level_names"):
        level_issues.append("duplicate host level names")
    if "linked_duplicate_level_names" in issues:
        level_issues.append("duplicate linked level names")
    if level_issues:
        sections.append(_onboarding_section("Levels / Level Naming", "REVIEW", "Level naming issues: {0}.".format(", ".join(level_issues)), "Resolve level naming ambiguity before level-targeted actions.", "Levels"))
    elif levels:
        sections.append(_onboarding_section("Levels / Level Naming", "OK", "{0} levels scanned; no level alias ambiguity detected.".format(levels.get("count", 0)), "No level naming issue detected by current scan.", "Levels"))
    else:
        sections.append(_onboarding_section("Levels / Level Naming", "UNKNOWN", "Levels were not scanned.", "Run standard Scan Project.", "Levels"))

    selected_count = selection.get("selected_element_count")
    if selected_count is None:
        sections.append(_onboarding_section("Active View / Selection", "UNKNOWN", "Selection was not scanned.", "Run standard Scan Project.", "Selection"))
    elif selected_count:
        sections.append(_onboarding_section("Active View / Selection", "OK", "{0} active-document selected elements.".format(selected_count), "Selection-based reviewed checks are available.", "Selection"))
    else:
        sections.append(_onboarding_section("Active View / Selection", "INFO", "No active-document selection.", "Use active-view/project checks first or select elements before selection-based checks.", "Selection"))

    blocking = [
        section for section in sections
        if section.get("title") not in ("Project Context Scan", "Reviewed Actions / Automation Readiness")
        and section.get("status") in ("REVIEW", "PARTIAL")
    ]
    readiness_status = "REVIEW" if blocking else "OK"
    readiness_result = "{0} review item(s) should be checked before automation.".format(len(blocking)) if blocking else "No blocking project-context risks detected by current checklist."
    sections.append(_onboarding_section("Reviewed Actions / Automation Readiness", readiness_status, readiness_result, "Run read-only diagnostics before model-modifying reviewed actions.", "Suggested Actions"))

    overall = "OK"
    for section in sections:
        if _onboarding_status_rank(section.get("status")) > _onboarding_status_rank(overall):
            overall = section.get("status")
    recommended = []
    for title in [
        "BIM Basis / Levels & Grids",
        "Link Coordinate Health",
        "CAD / Imports",
        "Revit Warnings",
        "Active View / Selection",
        "Schedules",
    ]:
        for section in sections:
            if section.get("title") == title and section.get("status") in ("REVIEW", "PARTIAL", "INFO"):
                recommended.append(section.get("next_step"))
                break
    if not recommended:
        recommended = ["Run read-only active-view health checks, then proceed to reviewed catalog actions as needed."]
    return {
        "project_title": doc_info.get("title", "(unknown document)"),
        "active_view": active_view.get("name", "(unknown view)"),
        "context_depth": depth,
        "scan_timestamp": context.get("timestamp_utc", "unknown"),
        "overall_status": overall,
        "sections": sections,
        "recommended_next_actions": recommended[:8],
        "blocking_review_items": [section.get("title") for section in sections if section.get("status") in ("REVIEW", "PARTIAL")],
        "automation_readiness_status": readiness_status,
    }


def format_project_onboarding_checklist(checklist):
    checklist = checklist if isinstance(checklist, dict) else {}
    lines = [
        "[PROJECT ONBOARDING CHECKLIST]",
        "",
        "Project",
        "- Document: {0}".format(checklist.get("project_title", "(unknown document)")),
        "- Active view: {0}".format(checklist.get("active_view", "(unknown view)")),
        "- Context depth: {0}".format(checklist.get("context_depth", "unknown")),
        "- Scan timestamp: {0}".format(checklist.get("scan_timestamp", "unknown")),
        "",
        "Overall status: {0}".format(checklist.get("overall_status", "UNKNOWN")),
        "",
        "Checklist",
    ]
    for index, section in enumerate(checklist.get("sections", []) or []):
        lines.append("{0}. {1} [{2}]".format(index + 1, section.get("title"), section.get("status", "UNKNOWN")))
        lines.append("   Result: {0}".format(section.get("result", "")))
        lines.append("   Next step: {0}".format(section.get("next_step", "")))
        lines.append("   Context branch: {0}".format(section.get("branch", "")))
    lines.extend(["", "Recommended Next Steps"])
    for index, action in enumerate(checklist.get("recommended_next_actions", [])[:8]):
        lines.append("{0}. {1}".format(index + 1, action))
    lines.extend(
        [
            "",
            "Safety",
            "- This checklist is read-only.",
            "- No model data was modified.",
        ]
    )
    return "\n".join(lines)


def _guided_plan_step(phase, title, action_key, action_type, risk, approval_required, reason, prerequisites=None):
    return {
        "phase": phase,
        "action_title": title,
        "action_key": action_key,
        "action_type": action_type,
        "risk": risk,
        "approval_required": bool(approval_required),
        "reason": reason,
        "prerequisites": prerequisites or [],
        "execution_status": "not_executed",
    }


def build_guided_project_startup_plan(context):
    context = context if isinstance(context, dict) else {}
    doc_info = context.get("document", {}) if isinstance(context.get("document", {}), dict) else {}
    active_view = doc_info.get("active_view", {}) if isinstance(doc_info.get("active_view", {}), dict) else {}
    onboarding = build_project_onboarding_checklist(context)
    coord_health = _get_linked_model_coordinate_health(context)
    level_grid = get_host_link_level_grid_health(context)
    imports = context.get("imports_cad", {}) if isinstance(context.get("imports_cad", {}), dict) else {}
    warnings = context.get("warnings_summary", {}) if isinstance(context.get("warnings_summary", {}), dict) else {}
    schedules = context.get("schedules", {}) if isinstance(context.get("schedules", {}), dict) else {}
    levels = context.get("levels", {}) if isinstance(context.get("levels", {}), dict) else {}
    selection = context.get("selection", {}) if isinstance(context.get("selection", {}), dict) else {}
    categories = context.get("category_counts", []) or []
    issues = set([issue.get("id") for issue in context.get("detected_issues", []) if isinstance(issue, dict)])
    phase1 = []
    phase2 = []
    phase3 = []
    blocked = []

    phase1.append(_guided_plan_step(
        "Phase 1 - Required read-only diagnostics",
        "Project onboarding checklist",
        "project-onboarding-checklist",
        "diagnostic",
        "low",
        False,
        "Start with the deterministic checklist. Overall status {0}; automation readiness {1}.".format(onboarding.get("overall_status", "UNKNOWN"), onboarding.get("automation_readiness_status", "UNKNOWN")),
    ))
    if level_grid and (level_grid.get("overall_status") in ("REVIEW", "PARTIAL") or level_grid.get("level_findings_count", 0) or level_grid.get("grid_findings_count", 0)):
        phase1.append(_guided_plan_step(
            "Phase 1 - Required read-only diagnostics",
            "BIM Basis / Levels & Grids review",
            "check-bim-basis-level-grid-health",
            "diagnostic",
            "low",
            False,
            "Level/grid findings exist: {0} level findings, {1} grid findings.".format(level_grid.get("level_findings_count", 0), level_grid.get("grid_findings_count", 0)),
        ))
    if coord_health and (coord_health.get("overall_status") in ("REVIEW", "PARTIAL") or coord_health.get("unavailable_count", 0) or coord_health.get("non_identity_transform_count", 0)):
        phase1.append(_guided_plan_step(
            "Phase 1 - Required read-only diagnostics",
            "Linked model coordinate health check",
            "check-linked-model-coordinate-health",
            "diagnostic",
            "low",
            False,
            "Linked coordinate health status {0}; unavailable {1}, non-identity {2}, rotated {3}, z-offset {4}.".format(coord_health.get("overall_status", "UNKNOWN"), coord_health.get("unavailable_count", 0), coord_health.get("non_identity_transform_count", 0), coord_health.get("rotated_link_count", 0), coord_health.get("z_offset_link_count", 0)),
        ))
    if imports.get("total_import_instances", 0):
        phase1.append(_guided_plan_step(
            "Phase 1 - Required read-only diagnostics",
            "CAD/import review",
            None,
            "diagnostic",
            "low",
            False,
            "{0} CAD/import instances found; {1} view-specific.".format(imports.get("total_import_instances", 0), imports.get("view_specific_import_count", 0)),
        ))
    if warnings.get("total_warning_count", 0):
        phase1.append(_guided_plan_step(
            "Phase 1 - Required read-only diagnostics",
            "Review Revit warnings summary",
            None,
            "diagnostic",
            "low",
            False,
            "{0} Revit warnings detected. Review warning groups before automation.".format(warnings.get("total_warning_count", 0)),
        ))
    if levels.get("ambiguous_aliases"):
        blocked.append(_guided_plan_step(
            "Blocked / not recommended yet",
            "Level-targeted automation",
            None,
            "blocked",
            "medium",
            False,
            "Ambiguous level aliases exist; level-targeted automation should wait until level naming is reviewed.",
            ["Resolve or review ambiguous level names."],
        ))

    phase2.append(_guided_plan_step(
        "Phase 2 - Optional project health checks",
        "Active-view health check",
        "health-check-active-view-selection",
        "reviewed_action",
        "low",
        False,
        "Run a read-only active-view health check after required diagnostics.",
    ))
    if selection.get("selected_element_count", 0):
        phase2.append(_guided_plan_step(
            "Phase 2 - Optional project health checks",
            "Report selected elements by category",
            "report-selected-elements-by-category",
            "reviewed_action",
            "low",
            False,
            "{0} selected elements are available for selection-based QA.".format(selection.get("selected_element_count", 0)),
        ))

    major_context_risk = bool(blocked or [step for step in phase1 if step.get("action_title") != "Project onboarding checklist"])
    pipe_count = 0
    fitting_count = 0
    for item in categories:
        if item.get("category") == "Pipes" and isinstance(item.get("total_count"), int):
            pipe_count = item.get("total_count")
        if item.get("category") == "Pipe Fittings" and isinstance(item.get("total_count"), int):
            fitting_count = item.get("total_count")
    if pipe_count:
        phase3.append(_guided_plan_step(
            "Phase 3 - Reviewed automation candidates",
            "Create pipe schedule by level",
            "create-pipe-schedule-by-level",
            "reviewed_action",
            "medium",
            True,
            "Pipes are present; schedule creation should run only after read-only diagnostics are reviewed.",
            ["Review Phase 1 diagnostics first."] if major_context_risk else [],
        ))
    if fitting_count:
        phase3.append(_guided_plan_step(
            "Phase 3 - Reviewed automation candidates",
            "Create pipe fitting schedule by level",
            "create-pipe-fitting-schedule-by-level",
            "reviewed_action",
            "medium",
            True,
            "Pipe fittings are present; schedule creation should run only after read-only diagnostics are reviewed.",
            ["Review Phase 1 diagnostics first."] if major_context_risk else [],
        ))
    if schedules.get("template_like_schedule_candidates") and "aco_bunge_templates_present" in issues:
        phase3.append(_guided_plan_step(
            "Phase 3 - Reviewed automation candidates",
            "ACO pipe fitting summary from template",
            "create-aco-pipe-fitting-summary-from-template",
            "reviewed_action",
            "medium",
            True,
            "ACO/Bunge template-like schedules were observed; use only after required diagnostics and template status are reviewed.",
            ["Review schedule population/status.", "Review Phase 1 diagnostics first."] if major_context_risk else ["Review schedule population/status."],
        ))
    if not phase3:
        phase3.append(_guided_plan_step(
            "Phase 3 - Reviewed automation candidates",
            "No reviewed automation candidate selected yet",
            None,
            "informational",
            "low",
            False,
            "No schedule/template automation candidate was selected from the current context.",
        ))
    steps = phase1 + phase2 + phase3 + blocked
    return {
        "title": "Guided Project Startup Plan",
        "project_title": doc_info.get("title", "(unknown document)"),
        "active_view": active_view.get("name", "(unknown view)"),
        "context_depth": context.get("scan_depth", "unknown"),
        "scan_timestamp": context.get("timestamp_utc", "unknown"),
        "overall_onboarding_status": onboarding.get("overall_status", "UNKNOWN"),
        "automation_readiness": onboarding.get("automation_readiness_status", "UNKNOWN"),
        "phase1": phase1,
        "phase2": phase2,
        "phase3": phase3,
        "blocked": blocked,
        "steps": steps,
        "execution_safe": True,
        "plan_only": True,
    }


def format_guided_project_startup_plan(plan):
    plan = plan if isinstance(plan, dict) else {}
    lines = [
        "[GUIDED PROJECT STARTUP PLAN]",
        "",
        "Project",
        "- Document: {0}".format(plan.get("project_title", "(unknown document)")),
        "- Active view: {0}".format(plan.get("active_view", "(unknown view)")),
        "- Context depth: {0}".format(plan.get("context_depth", "unknown")),
        "- Scan timestamp: {0}".format(plan.get("scan_timestamp", "unknown")),
        "- Overall onboarding status: {0}".format(plan.get("overall_onboarding_status", "UNKNOWN")),
        "- Automation readiness: {0}".format(plan.get("automation_readiness", "UNKNOWN")),
    ]

    def append_phase(title, steps):
        lines.extend(["", title])
        if not steps:
            lines.append("- none")
            return
        for index, step in enumerate(steps):
            lines.append("{0}. {1}".format(index + 1, step.get("action_title")))
            lines.append("   risk: {0}".format(step.get("risk", "low")))
            lines.append("   approval required: {0}".format("yes" if step.get("approval_required") else "no"))
            lines.append("   type: {0}".format(step.get("action_type", "diagnostic")))
            if step.get("action_key"):
                lines.append("   action key: {0}".format(step.get("action_key")))
            lines.append("   reason: {0}".format(step.get("reason", "")))
            prerequisites = step.get("prerequisites") or []
            if prerequisites:
                lines.append("   prerequisites: {0}".format("; ".join(prerequisites)))
            lines.append("   execution status: {0}".format(step.get("execution_status", "not_executed")))

    append_phase("Phase 1 - Required read-only diagnostics", plan.get("phase1") or [])
    append_phase("Phase 2 - Optional project health checks", plan.get("phase2") or [])
    append_phase("Phase 3 - Reviewed automation candidates", plan.get("phase3") or [])
    append_phase("Blocked / not recommended yet", plan.get("blocked") or [])
    lines.extend(
        [
            "",
            "Safety",
            "- This is a plan only.",
            "- No actions have been executed.",
            "- Execute Plan remains governed by reviewed/catalog approval.",
        ]
    )
    return "\n".join(lines)


def _summarize_project_context(context):
    context = context if isinstance(context, dict) else {}
    doc_info = context.get("document", {})
    active_view = doc_info.get("active_view", {})
    levels = context.get("levels", {})
    sheets = context.get("sheets", {})
    views = context.get("views", {})
    schedules = context.get("schedules", {})
    links = context.get("links", {})
    imports = context.get("imports_cad", {})
    selection = context.get("selection", {})
    warnings = context.get("warnings_summary", {})
    issues = context.get("detected_issues", [])
    coord_health = _get_linked_model_coordinate_health(context)
    level_grid = get_host_link_level_grid_health(context)
    lines = [
        "[PROJECT CONTEXT SUMMARY]",
        "Document: {0}".format(doc_info.get("title", "(unknown document)")),
        "Active view: {0} [{1}]".format(active_view.get("name", "(unknown view)"), active_view.get("view_type", "unknown")),
        "",
        "[KEY COUNTS]",
    ]
    level_names = [level.get("exact_name") for level in levels.get("levels", [])[:8] if level.get("exact_name")]
    lines.append("Levels: {0}{1}".format(levels.get("count", 0), " | {0}".format(", ".join(level_names)) if level_names else ""))
    if levels.get("ambiguous_aliases"):
        lines.append("Ambiguous level aliases: {0}".format(len(levels.get("ambiguous_aliases"))))
    view_groups = views.get("by_view_type", {}) or {}
    view_group_text = ", ".join(["{0}={1}".format(key, value) for key, value in sorted(view_groups.items())[:6]])
    lines.append("Views: {0}{1}".format(views.get("total", 0), " | {0}".format(view_group_text) if view_group_text else ""))
    sheet_names = []
    for sheet in sheets.get("samples", [])[:5]:
        label = "{0} {1}".format(sheet.get("number") or "", sheet.get("name") or "").strip()
        if label:
            sheet_names.append(label)
    lines.append("Sheets: {0}{1}".format(sheets.get("count", 0), " | {0}".format("; ".join(sheet_names)) if sheet_names else ""))
    loaded_links = links.get("loaded_links") or []
    unavailable_links = links.get("unavailable_links") or []
    link_names = [item.get("display_name") or item.get("type_name") or item.get("instance_name") for item in (loaded_links + unavailable_links)[:5]]
    lines.append("Revit links: {0} | loaded {1}, unavailable {2}{3}".format(links.get("instance_count", 0), len(loaded_links), len(unavailable_links), " | {0}".format(", ".join([name for name in link_names if name])) if link_names else ""))
    if coord_health:
        lines.append("Link coordinate health: {0} | non-identity {1}, rotated {2}, z-offset {3}".format(
            coord_health.get("overall_status", "unknown"),
            coord_health.get("non_identity_transform_count", 0),
            coord_health.get("rotated_link_count", 0),
            coord_health.get("z_offset_link_count", 0),
        ))
    if level_grid:
        lines.append("BIM Basis / Levels & Grids: {0} | level findings {1} | grid findings {2} | links compared {3}".format(
            level_grid.get("overall_status", "unknown"),
            level_grid.get("level_findings_count", 0),
            level_grid.get("grid_findings_count", 0),
            level_grid.get("links_compared_count", 0),
        ))
    import_names = [item.get("instance_name") for item in imports.get("imports", [])[:5] if item.get("instance_name")]
    lines.append("CAD/imports: {0}{1}".format(imports.get("total_import_instances", 0), " | {0}".format(", ".join(import_names)) if import_names else ""))
    top_counts = _top_category_counts(context.get("category_counts", []))
    if top_counts:
        lines.append("Top categories: {0}".format(", ".join(["{0}={1}".format(name, count) for count, name in top_counts])))
    schedule_samples = schedules.get("schedules", []) or []
    populated = len([item for item in schedule_samples if isinstance(item.get("body_row_count"), int) and item.get("body_row_count") > 0])
    empty = len([item for item in schedule_samples if item.get("body_row_count") == 0])
    unknown = len([item for item in schedule_samples if not isinstance(item.get("body_row_count"), int)])
    lines.append("Schedules: sampled {0} | populated {1}, empty {2}, unknown {3}".format(schedules.get("sampled_count", schedules.get("count", 0)), populated, empty, unknown))
    lines.append("Warnings: {0}".format(warnings.get("total_warning_count", 0)))
    if issues:
        lines.append("Detected issues: {0}".format(", ".join(["{0} ({1})".format(issue.get("id"), issue.get("severity", "info")) for issue in issues[:8]])))
    lines.append("Selected elements: {0}".format(selection.get("selected_element_count", 0)))
    checks = _project_first_check_lines(context)
    if checks:
        lines.append("")
        lines.append("[SUGGESTED FIRST CHECKS]")
        for index, check in enumerate(checks[:4]):
            lines.append("{0}. {1}".format(index + 1, check))
    scan_warnings = context.get("scanner_warnings", [])
    if scan_warnings:
        lines.append("")
        lines.append("[SCANNER WARNINGS]")
        lines.append("Count: {0}".format(len(scan_warnings)))
    return "\n".join(lines)


def collect_project_context(doc, uidoc, scan_depth="standard"):
    depth = (scan_depth or "standard").lower()
    if depth not in ("bootstrap", "standard", "deep"):
        depth = "standard"
    if depth == "deep":
        depth = "standard"
    context = {
        "scan_depth": depth,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "scanner_warnings": [],
    }
    warnings = context["scanner_warnings"]
    _collector_section(context, warnings, "document", lambda: _document_info(doc, uidoc))
    _collector_section(context, warnings, "levels", lambda: _collect_levels_context(doc))
    _collector_section(context, warnings, "selection", lambda: _collect_selection_summary(doc, uidoc))
    if depth == "bootstrap":
        _collector_section(context, warnings, "views", lambda: _collect_view_summary(doc, uidoc))
        _collector_section(context, warnings, "sheets", lambda: _collect_sheet_summary(doc))
        _collector_section(context, warnings, "schedules", lambda: {"count": _safe_int(len(list(DB.FilteredElementCollector(doc).OfClass(DB.ViewSchedule))), 0)})
        _collector_section(context, warnings, "links", lambda: _collect_revit_links(doc, 5))
        _collector_section(context, warnings, "linked_model_coordinate_health", lambda: _collect_linked_model_coordinate_health(doc, 5))
        _collector_section(context, warnings, "imports_cad", lambda: _collect_imports_context(doc, uidoc, 5))
    else:
        _collector_section(context, warnings, "views", lambda: _collect_view_summary(doc, uidoc))
        _collector_section(context, warnings, "sheets", lambda: _collect_sheet_summary(doc))
        _collector_section(context, warnings, "schedules", lambda: _collect_schedule_inventory(doc))
        _collector_section(context, warnings, "links", lambda: _collect_revit_links(doc))
        _collector_section(context, warnings, "linked_model_coordinate_health", lambda: _collect_linked_model_coordinate_health(doc))
        _collector_section(context, warnings, "host_link_level_grid_health", lambda: _collect_host_link_level_grid_health(doc))
        _collector_section(context, warnings, "imports_cad", lambda: _collect_imports_context(doc, uidoc))
        _collector_section(context, warnings, "category_counts", lambda: _collect_core_category_counts(doc, uidoc))
        _collector_section(context, warnings, "warnings_summary", lambda: _collect_warning_summary(doc))
    context["detected_issues"] = _detect_project_issues(context)
    summary = _summarize_project_context(context)
    return {
        "context": context,
        "summary": summary,
        "warnings": list(warnings),
    }


def scan_current_project_context(doc, uidoc, context=None):
    result = collect_project_context(doc, uidoc, "standard")
    return result.get("summary", "")


def summarize_current_project_context(doc, uidoc, context=None):
    result = collect_project_context(doc, uidoc, "bootstrap")
    return result.get("summary", "")


def ask_agent_for_project_plan_action(doc, uidoc, context=None):
    result = collect_project_context(doc, uidoc, "standard")
    plan = build_guided_project_startup_plan(result.get("context", {}))
    return format_guided_project_startup_plan(plan)


def guided_project_startup_plan_action(doc, uidoc, context=None):
    result = collect_project_context(doc, uidoc, "standard")
    plan = build_guided_project_startup_plan(result.get("context", {}))
    return format_guided_project_startup_plan(plan)


def check_linked_model_coordinate_health(doc, uidoc, context=None):
    result = collect_project_context(doc, uidoc, "standard")
    health = _get_linked_model_coordinate_health(result.get("context", {}))
    return "\n".join(
        [
            "[LINKED MODEL COORDINATE HEALTH]",
            "Total links: {0}".format(health.get("total_links", 0)),
            "Loaded/readable: {0}".format(health.get("loaded_readable_count", 0)),
            "Unavailable: {0}".format(health.get("unavailable_count", 0)),
            "Non-identity transforms: {0}".format(health.get("non_identity_transform_count", 0)),
            "Rotated links: {0}".format(health.get("rotated_link_count", 0)),
            "Z-offset links: {0}".format(health.get("z_offset_link_count", 0)),
            "Duplicate link instances: {0}".format(health.get("duplicate_link_instance_count", 0)),
            "Overall status: {0}".format(health.get("overall_status", "not scanned")),
            "No actions have been executed.",
        ]
    )


def check_bim_basis_level_grid_health(doc, uidoc, context=None):
    result = collect_project_context(doc, uidoc, "standard")
    health = _get_host_link_level_grid_health(result.get("context", {}))
    return "\n".join(
        [
            "[BIM BASIS / LEVELS & GRIDS]",
            "Profile: {0}".format(health.get("profile", "Configurable BIM Basis / ILS-style")),
            "Overall status: {0}".format(health.get("overall_status", "not scanned")),
            "Host levels: {0}".format(health.get("host_level_count", "not scanned")),
            "Host grids: {0}".format(health.get("host_grid_count", "not scanned")),
            "Links compared: {0}".format(health.get("links_compared_count", 0)),
            "Links skipped: {0}".format(health.get("links_skipped_count", 0)),
            "Level findings: {0}".format(health.get("level_findings_count", 0)),
            "Grid findings: {0}".format(health.get("grid_findings_count", 0)),
            "No actions have been executed.",
        ]
    )


def project_onboarding_checklist_action(doc, uidoc, context=None):
    result = collect_project_context(doc, uidoc, "standard")
    checklist = build_project_onboarding_checklist(result.get("context", {}))
    return format_project_onboarding_checklist(checklist)


def create_codex_task_brief_action(doc, uidoc, context=None):
    result = collect_project_context(doc, uidoc, "bootstrap")
    return "\n".join(
        [
            "Codex Task Brief",
            "",
            "Current document: {0}".format(_document_title(doc)),
            "Active view: {0}".format(_active_view_title(doc, uidoc)),
            "",
            "Latest scan summary:",
            result.get("summary", ""),
            "",
            "Observed issue:",
            "Runtime validation/export tooling for ModelMind actions.",
            "",
            "Forbidden changes:",
            "- window lifecycle",
            "- ExternalEvent / reviewed dispatch",
            "- modal/modeless behavior",
            "- create sheet / create 3D view / rename view paths",
            "- shared undo plumbing",
            "",
            "Validation commands:",
            "- python -m tabnanny AI.extension/AI.tab/Dev.panel/AI_01.pushbutton/script.py",
            "- python -m py_compile AI.extension/lib/ai_prompt_registry.py",
            "- python -m py_compile AI.extension/lib/ai_agent_session.py",
            "- parse AI.extension/lib/prompt_catalog.json",
        ]
    )


def _element_type_name(doc, elem):
    try:
        elem_type = doc.GetElement(elem.GetTypeId()) if hasattr(elem, "GetTypeId") else None
    except:
        elem_type = None
    return get_elem_name(elem_type) if elem_type else get_elem_name(elem)


def _supports_system_assignment(elem):
    try:
        category_id = elem.Category.Id.IntegerValue
    except:
        return False
    supported = set(
        [
            int(DB.BuiltInCategory.OST_DuctCurves),
            int(DB.BuiltInCategory.OST_DuctFitting),
            int(DB.BuiltInCategory.OST_PipeCurves),
            int(DB.BuiltInCategory.OST_PipeFitting),
            int(DB.BuiltInCategory.OST_MechanicalEquipment),
            int(DB.BuiltInCategory.OST_DuctAccessory),
            int(DB.BuiltInCategory.OST_PipeAccessory),
            int(DB.BuiltInCategory.OST_ElectricalFixtures),
            int(DB.BuiltInCategory.OST_ElectricalEquipment),
        ]
    )
    return category_id in supported


def _supports_electrical_assignment(elem):
    try:
        category_id = elem.Category.Id.IntegerValue
    except:
        return False
    supported = set(
        [
            int(DB.BuiltInCategory.OST_ElectricalFixtures),
            int(DB.BuiltInCategory.OST_ElectricalEquipment),
            int(DB.BuiltInCategory.OST_LightingFixtures),
            int(DB.BuiltInCategory.OST_LightingDevices),
        ]
    )
    return category_id in supported


def _system_assignment_value(elem):
    return _lookup_first_param_value(
        elem,
        ["System Name", "System Classification", "System Type", "RBS_SYSTEM_CLASSIFICATION_PARAM"],
    )


def _electrical_assignment_value(elem):
    return _lookup_first_param_value(
        elem,
        ["Circuit Number", "Panel", "System Type", "System Name", "System Classification"],
    )


def _safe_builtin_param_value(elem, builtin_param_names):
    for name in builtin_param_names:
        try:
            bip = getattr(DB.BuiltInParameter, name)
        except:
            continue
        try:
            value = _value_from_param(elem.get_Parameter(bip))
        except:
            value = None
        if value:
            return value
    return None


def _is_unconnected_fitting(elem):
    try:
        connector_set = elem.MEPModel.ConnectorManager.Connectors
    except:
        connector_set = None
    if connector_set is None:
        return False
    for connector in connector_set:
        try:
            if not connector.IsConnected:
                return True
        except:
            continue
    return False


def _meters_to_feet(value_m):
    return float(value_m) / 0.3048


def _feet_to_meters(value_ft):
    return float(value_ft) * 0.3048


def _feet3_to_m3(value_ft3):
    return float(value_ft3) * 0.0283168


def _safe_int_id(elem):
    try:
        return elem.Id.IntegerValue
    except:
        return None


def _round_key(value, precision=4):
    try:
        return round(float(value), precision)
    except:
        return 0.0


def _parse_metric_length_meters(text, default_m=1.5):
    lowered = (text or "").lower()
    mm_match = re.search(r"(\d+(?:\.\d+)?)\s*mm\b", lowered)
    if mm_match:
        try:
            return float(mm_match.group(1)) / 1000.0
        except:
            return default_m
    meter_match = re.search(r"(\d+(?:\.\d+)?)\s*m\b", lowered)
    if meter_match:
        try:
            return float(meter_match.group(1))
        except:
            return default_m
    return default_m


def _all_view_names(doc):
    names = set()
    try:
        for view in DB.FilteredElementCollector(doc).OfClass(DB.View):
            name = get_elem_name(view)
            if name:
                names.add(name)
    except:
        pass
    return names


def _unique_name(base_name, existing_names):
    if base_name not in existing_names:
        return base_name
    index = 2
    while True:
        candidate = "{0} ({1})".format(base_name, index)
        if candidate not in existing_names:
            return candidate
        index += 1


def _prompt_text_from_context(context):
    if isinstance(context, dict):
        return context.get("requested_prompt") or context.get("canonical_prompt") or context.get("prompt_text") or ""
    return str(context or "")


def _selection_scope_preamble(doc, uidoc, selection_count):
    return [
        "Selection scope: active document only",
        "Active document: {0}".format(_document_title(doc)),
        "Active view: {0}".format(_active_view_title(doc, uidoc)),
        "Current selection count: {0}".format(selection_count),
    ]


def _snapshot_selection_ids(uidoc):
    try:
        return list(uidoc.Selection.GetElementIds())
    except:
        return []


def _restore_selection_ids(uidoc, element_ids):
    from System.Collections.Generic import List

    try:
        uidoc.Selection.SetElementIds(List[DB.ElementId](element_ids or []))
    except:
        pass


def _category_ids_from_scope_spec(categories):
    resolved = []
    for category in categories or []:
        try:
            if isinstance(category, str):
                resolved.append(getattr(DB.BuiltInCategory, category))
            else:
                resolved.append(category)
        except:
            continue
    return resolved


def _collect_scope_element_ids(doc, uidoc, scope_spec):
    scope_spec = scope_spec or {}
    mode = scope_spec.get("mode")
    categories = _category_ids_from_scope_spec(scope_spec.get("categories") or [])
    elements = []
    if mode == "active_view_categories":
        for category in categories:
            try:
                elements.extend(
                    list(
                        DB.FilteredElementCollector(doc, uidoc.ActiveView.Id)
                        .OfCategory(category)
                        .WhereElementIsNotElementType()
                        .ToElements()
                    )
                )
            except:
                continue
    elif mode == "selection_categories":
        elements = _selected_elements_by_categories(doc, uidoc, categories)
    return [elem.Id for elem in elements if elem is not None]


def _all_doc_categories(doc):
    categories = []
    try:
        for category in doc.Settings.Categories:
            try:
                if category is not None and getattr(category, "Name", None):
                    categories.append(category)
            except:
                continue
    except:
        pass
    return categories


def _is_annotation_like_category(category):
    if category is None:
        return False
    name = (getattr(category, "Name", "") or "").lower()
    if " tag" in name or name.endswith(" tags"):
        return True
    try:
        return category.CategoryType == DB.CategoryType.Annotation
    except:
        return False


def _normalize_category_token(text):
    token = re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()
    if token.endswith("ies"):
        token = token[:-3] + "y"
    elif token.endswith("s") and not token.endswith("ss"):
        token = token[:-1]
    return token.strip()


def _extract_category_query(prompt_text):
    text = (prompt_text or "").lower()
    exact_multi = re.search(r"categories\s*:\s*(.+)$", text)
    if exact_multi:
        return exact_multi.group(1).strip()
    exact_single = re.search(r"category\s*:\s*(.+)$", text)
    if exact_single:
        return exact_single.group(1).strip()
    match = re.search(r"category\s+(.+)$", text)
    if match:
        return match.group(1).strip()
    prefixes = [
        "select all elements of category",
        "count all elements of category",
        "list all elements of category",
        "select all",
        "count all",
        "list all",
        "select",
        "count",
        "list",
    ]
    for prefix in prefixes:
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    return text.strip()


def _split_exact_category_terms(query):
    query = (query or "").strip()
    if not query:
        return []
    quoted = re.findall(r"\"([^\"]+)\"", query)
    if quoted:
        return quoted
    return [item.strip() for item in query.split(",") if item.strip()]


def _resolve_single_category_term(doc, query):
    query = (query or "").strip()
    query = re.sub(r"\b(in|on)\s+active\s+view\b", "", query).strip()
    query = re.sub(r"\bfrom\s+selection\b", "", query).strip()
    if not query:
        return (None, "Specify a category, for example: count all elements of category ducts.")

    alias_map = {
        "duct": "Ducts",
        "duct fitting": "Duct Fittings",
        "pipe": "Pipes",
        "pipe fitting": "Pipe Fittings",
        "wall": "Walls",
        "door": "Doors",
        "window": "Windows",
        "room": "Rooms",
        "space": "Spaces",
        "electrical fixture": "Electrical Fixtures",
        "electrical equipment": "Electrical Equipment",
        "lighting fixture": "Lighting Fixtures",
        "tag": "Tags",
    }
    normalized_query = _normalize_category_token(query)
    target_names = [alias_map.get(normalized_query, query)]
    matches = []
    for category in _all_doc_categories(doc):
        cat_name = getattr(category, "Name", "")
        normalized_name = _normalize_category_token(cat_name)
        if normalized_name == normalized_query:
            matches.append(category)
        elif normalized_query and normalized_query in normalized_name:
            matches.append(category)
        elif cat_name in target_names:
            matches.append(category)

    unique = []
    seen_ids = set()
    for category in matches:
        try:
            category_id = category.Id.IntegerValue
        except:
            category_id = id(category)
        if category_id in seen_ids:
            continue
        seen_ids.add(category_id)
        unique.append(category)

    if not unique:
        return (None, "No matching Revit category was found for '{0}'.".format(query))
    preferred = [category for category in unique if not _is_annotation_like_category(category)]
    if len(preferred) == 1:
        return (preferred[0], None)
    if len(preferred) > 1:
        unique = preferred
    if len(unique) > 1:
        suggestions = ", ".join([category.Name for category in unique[:5]])
        return (None, "Category '{0}' is ambiguous. Possible matches: {1}".format(query, suggestions))
    return (unique[0], None)


def _resolve_categories_from_prompt(doc, prompt_text):
    text = (prompt_text or "")
    lowered = text.lower()
    if "categories:" in lowered:
        terms = _split_exact_category_terms(text.split(":", 1)[1])
    elif "category:" in lowered:
        terms = _split_exact_category_terms(text.split(":", 1)[1])
    else:
        terms = [_extract_category_query(text)]
    categories = []
    seen_ids = set()
    for term in terms:
        category, issue = _resolve_single_category_term(doc, term)
        if issue:
            return (None, issue)
        if category is None:
            continue
        category_id = category.Id.IntegerValue
        if category_id in seen_ids:
            continue
        seen_ids.add(category_id)
        categories.append(category)
    if not categories:
        return (None, "Specify a category, for example: category:walls or categories:doors,windows")
    return (categories, None)


def _resolve_category_from_prompt(doc, prompt_text):
    categories, issue = _resolve_categories_from_prompt(doc, prompt_text)
    if issue:
        return (None, issue)
    if len(categories) > 1:
        names = ", ".join([category.Name for category in categories])
        return (None, "Multiple categories were provided. Use the multi-category syntax only with the generic category actions. Parsed categories: {0}".format(names))
    return (categories[0], None)


def _elements_of_category(doc, uidoc, category, active_view_only=False):
    if category is None:
        return []
    try:
        collector = (
            DB.FilteredElementCollector(doc, uidoc.ActiveView.Id)
            if active_view_only
            else DB.FilteredElementCollector(doc)
        )
        return list(
            collector.OfCategoryId(category.Id).WhereElementIsNotElementType().ToElements()
        )
    except:
        return []


def _element_location_signature(elem):
    location = getattr(elem, "Location", None)
    if location is None:
        return None
    try:
        point = getattr(location, "Point", None)
        if point is not None:
            return (
                "point",
                _round_key(point.X),
                _round_key(point.Y),
                _round_key(point.Z),
            )
    except:
        pass
    try:
        curve = getattr(location, "Curve", None)
        if curve is not None:
            start = curve.GetEndPoint(0)
            end = curve.GetEndPoint(1)
            points = sorted(
                [
                    (_round_key(start.X), _round_key(start.Y), _round_key(start.Z)),
                    (_round_key(end.X), _round_key(end.Y), _round_key(end.Z)),
                ]
            )
            return (
                "curve",
                points[0],
                points[1],
                _round_key(curve.Length),
            )
    except:
        pass
    return None


def _duplicate_scope_elements(doc, uidoc, prompt_text):
    prompt = (prompt_text or "").lower()
    if "active view" in prompt:
        elements = list(
            DB.FilteredElementCollector(doc, uidoc.ActiveView.Id)
            .WhereElementIsNotElementType()
            .ToElements()
        )
        return ("active view", elements)
    selected = _selected_elements(doc, uidoc)
    if selected:
        return ("selection", selected)
    elements = list(
        DB.FilteredElementCollector(doc, uidoc.ActiveView.Id)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    return ("active view", elements)


def _find_duplicate_groups(elements):
    grouped = {}
    skipped = []
    for elem in elements:
        signature = _element_location_signature(elem)
        if signature is None:
            skipped.append(elem)
            continue
        try:
            key = (
                elem.Category.Id.IntegerValue if elem.Category else None,
                elem.GetTypeId().IntegerValue if hasattr(elem, "GetTypeId") else None,
                signature,
            )
        except:
            skipped.append(elem)
            continue
        grouped.setdefault(key, []).append(elem)
    duplicates = []
    for items in grouped.values():
        if len(items) > 1:
            duplicates.append(sorted(items, key=lambda item: _safe_int_id(item) or 0))
    duplicates.sort(key=lambda group: (-(len(group)), _safe_int_id(group[0]) or 0))
    return duplicates, skipped


def _room_space_collections(doc):
    room_category = DB.BuiltInCategory.OST_Rooms
    space_category = getattr(DB.BuiltInCategory, "OST_MEPSpaces", None)
    rooms = list(
        DB.FilteredElementCollector(doc)
        .OfCategory(room_category)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    spaces = []
    if space_category is not None:
        spaces = list(
            DB.FilteredElementCollector(doc)
            .OfCategory(space_category)
            .WhereElementIsNotElementType()
            .ToElements()
        )
    return rooms, spaces


def _room_space_number(elem):
    value = _lookup_first_param_value(elem, ["Number"])
    return value or "(no number)"


def _room_space_name(elem):
    value = _lookup_first_param_value(elem, ["Name"])
    return value or get_elem_name(elem) or "(no name)"


def _room_space_maps(doc):
    rooms, spaces = _room_space_collections(doc)
    room_map = {}
    space_map = {}
    for room in rooms:
        room_map.setdefault(_room_space_number(room), []).append(room)
    for space in spaces:
        space_map.setdefault(_room_space_number(space), []).append(space)
    return room_map, space_map


ACTIVE_VIEW_MEP_DETAIL_CAP = 2000
ACTIVE_VIEW_MEP_CATEGORY_GROUP_CAP = 20
ACTIVE_VIEW_MEP_TYPE_GROUP_CAP = 20
ACTIVE_VIEW_MEP_LEVEL_GROUP_CAP = 10
ACTIVE_VIEW_MEP_SAMPLE_ID_CAP = 10


def _active_view_mep_category_specs():
    return [
        ("Piping", "Pipes", "OST_PipeCurves"),
        ("Piping", "Pipe Fittings", "OST_PipeFitting"),
        ("Piping", "Pipe Accessories", "OST_PipeAccessory"),
        ("Piping", "Plumbing Fixtures", "OST_PlumbingFixtures"),
        ("HVAC", "Ducts", "OST_DuctCurves"),
        ("HVAC", "Duct Fittings", "OST_DuctFitting"),
        ("HVAC", "Duct Accessories", "OST_DuctAccessory"),
        ("HVAC", "Air Terminals", "OST_DuctTerminal"),
        ("HVAC", "Mechanical Equipment", "OST_MechanicalEquipment"),
        ("HVAC", "Flex Ducts", "OST_FlexDuctCurves"),
        ("Electrical", "Electrical Fixtures", "OST_ElectricalFixtures"),
        ("Electrical", "Electrical Equipment", "OST_ElectricalEquipment"),
        ("Electrical", "Lighting Fixtures", "OST_LightingFixtures"),
        ("Electrical", "Lighting Devices", "OST_LightingDevices"),
        ("Electrical", "Data Devices", "OST_DataDevices"),
        ("Electrical", "Communication Devices", "OST_CommunicationDevices"),
        ("Electrical", "Fire Alarm Devices", "OST_FireAlarmDevices"),
        ("Electrical", "Conduits", "OST_Conduit"),
        ("Electrical", "Conduit Fittings", "OST_ConduitFitting"),
        ("Electrical", "Cable Trays", "OST_CableTray"),
        ("Electrical", "Cable Tray Fittings", "OST_CableTrayFitting"),
        ("Electrical", "Electrical Analytical Loads", "OST_ElectricalAnalyticalLoads"),
        ("Other supported / coordination", "Generic Models", "OST_GenericModel"),
        ("Other supported / coordination", "Revit Links", "OST_RvtLinks"),
    ]


def _safe_builtin_category(category_name):
    try:
        return getattr(DB.BuiltInCategory, category_name)
    except:
        return None


def _collect_active_view_mep_elements(doc, uidoc, discipline_filter=None):
    active_view = uidoc.ActiveView
    element_map = {}
    category_meta = {}
    warnings = []
    requested = (discipline_filter or "").lower().strip()
    for discipline, label, bic_name in _active_view_mep_category_specs():
        if requested and requested != discipline.lower():
            continue
        bic = _safe_builtin_category(bic_name)
        if bic is None:
            continue
        try:
            elems = list(
                DB.FilteredElementCollector(doc, active_view.Id)
                .OfCategory(bic)
                .WhereElementIsNotElementType()
                .ToElements()
            )
        except Exception as err:
            warnings.append("Skipped {0}: {1}".format(label, safe_str(err)))
            continue
        for elem in elems:
            if elem is None:
                continue
            key = _safe_int_id(elem)
            element_map[key] = elem
            category_meta[key] = {
                "discipline": discipline,
                "label": label,
            }

    if not requested or requested == "other supported / coordination":
        try:
            imports = list(
                DB.FilteredElementCollector(doc, active_view.Id)
                .OfClass(DB.ImportInstance)
                .WhereElementIsNotElementType()
                .ToElements()
            )
            for elem in imports:
                if elem is None:
                    continue
                key = _safe_int_id(elem)
                element_map[key] = elem
                category_meta[key] = {
                    "discipline": "Other supported / coordination",
                    "label": "CAD/import instances",
                }
        except Exception as err:
            warnings.append("Skipped CAD/import instances: {0}".format(safe_str(err)))

    elements = list(element_map.values())
    elements.sort(key=lambda elem: _safe_int_id(elem))
    return {
        "active_view": active_view,
        "elements": elements,
        "category_meta": category_meta,
        "warnings": warnings,
        "discipline_filter": discipline_filter,
    }


def _active_view_mep_groups(doc, collected, detail_cap=ACTIVE_VIEW_MEP_DETAIL_CAP):
    elems = collected.get("elements", [])
    inspected = elems[:detail_cap]
    meta = collected.get("category_meta", {})
    discipline_counts = {
        "Piping": 0,
        "HVAC": 0,
        "Electrical": 0,
        "Other supported / coordination": 0,
    }
    category_grouped = {}
    type_grouped = {}
    level_grouped = {}
    missing_level = []
    imports = []
    revit_links = []
    for elem in inspected:
        key = _safe_int_id(elem)
        info = meta.get(key, {})
        discipline = info.get("discipline") or "Other supported / coordination"
        category_name = info.get("label") or _category_name(elem)
        discipline_counts.setdefault(discipline, 0)
        discipline_counts[discipline] += 1
        category_grouped.setdefault(category_name, []).append(elem)
        type_grouped.setdefault(_family_and_type_text(doc, elem) or "(no type)", []).append(elem)
        level_name = _element_level_name(doc, elem)
        if level_name:
            level_grouped.setdefault(level_name, []).append(elem)
        else:
            missing_level.append(elem)
        if category_name == "CAD/import instances" or "Import" in category_name:
            imports.append(elem)
        if category_name == "Revit Links":
            revit_links.append(elem)
    return {
        "elements": elems,
        "inspected": inspected,
        "discipline_counts": discipline_counts,
        "category_grouped": category_grouped,
        "type_grouped": type_grouped,
        "level_grouped": level_grouped,
        "missing_level": missing_level,
        "imports": imports,
        "revit_links": revit_links,
        "capped": len(elems) > detail_cap,
    }


def _active_view_mep_header(title, doc, uidoc, collected, total_count):
    active_view = collected.get("active_view") or uidoc.ActiveView
    try:
        view_type = safe_str(active_view.ViewType)
    except:
        view_type = "unknown"
    return [
        title,
        "Selection scope: active view / active document only",
        "Active document: {0}".format(_document_title(doc)),
        "Active view: {0} [{1}]".format(_active_view_title(doc, uidoc), view_type),
        "Total supported MEP elements in active view: {0}".format(total_count),
    ]


def _append_active_view_group_lines(lines, title, grouped, limit):
    lines.append("")
    lines.append(title)
    ordered = sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0]))
    if not ordered:
        lines.append("- none")
        return
    for name, items in ordered[:limit]:
        lines.append("- {0}: {1} | sample ids: {2}".format(name, len(items), _sample_id_text(items)))
    if len(ordered) > limit:
        lines.append("...showing first {0} of {1} groups".format(limit, len(ordered)))


def _append_active_view_common_tail(lines, groups, collected, mode):
    elems = groups.get("elements", [])
    if groups.get("capped"):
        lines.append("")
        lines.append("Result set capped for readability/performance.")
    lines.append("")
    lines.append("Sample ElementIds: {0}".format(_sample_id_text(elems, limit=ACTIVE_VIEW_MEP_SAMPLE_ID_CAP)))
    lines.append("")
    lines.append("Notes / Warnings")
    notes = []
    if not elems:
        notes.append("No supported MEP elements found in the active view.")
    visible_disciplines = [name for name, count in groups.get("discipline_counts", {}).items() if count]
    if len(visible_disciplines) > 1:
        notes.append("Mixed disciplines visible: {0}".format(len(visible_disciplines)))
    if groups.get("missing_level"):
        notes.append("Missing level/reference-level data on {0} element(s).".format(len(groups.get("missing_level"))))
    if groups.get("imports"):
        notes.append("CAD/import instances visible in active view: {0}".format(len(groups.get("imports"))))
    if groups.get("revit_links"):
        notes.append("Revit links visible in active view: {0}".format(len(groups.get("revit_links"))))
    for warning in collected.get("warnings", []):
        notes.append(warning)
    if not notes:
        notes.append("No active-view QA warnings detected by this read-only report.")
    for note in notes:
        lines.append("- {0}".format(note))
    lines.append("")
    lines.append("Safety note:")
    lines.append("- read-only; no model data modified.")


def active_view_mep_report(doc, uidoc, mode="summary", discipline_filter=None):
    collected = _collect_active_view_mep_elements(doc, uidoc, discipline_filter=discipline_filter)
    groups = _active_view_mep_groups(doc, collected)
    elems = groups.get("elements", [])
    title_map = {
        "summary": "[ACTIVE VIEW MEP SUMMARY]",
        "count": "[ACTIVE VIEW MEP SUMMARY]",
        "category": "[ACTIVE VIEW ELEMENTS BY CATEGORY]",
        "type": "[ACTIVE VIEW ELEMENTS BY TYPE]",
        "qa": "[ACTIVE VIEW QA REPORT]",
    }
    title = title_map.get(mode, "[ACTIVE VIEW MEP SUMMARY]")
    lines = _active_view_mep_header(title, doc, uidoc, collected, len(elems))
    if discipline_filter:
        lines.append("Discipline filter: {0}".format(discipline_filter))
    if not elems:
        lines.append("")
        lines.append("No supported MEP elements found in the active view.")
        lines.append("")
        lines.append("Safety note:")
        lines.append("- read-only; no model data modified.")
        return "\n".join(lines)

    lines.append("")
    lines.append("By discipline")
    for discipline in ["Piping", "HVAC", "Electrical", "Other supported / coordination"]:
        lines.append("- {0}: {1}".format(discipline, groups.get("discipline_counts", {}).get(discipline, 0)))

    if mode == "type":
        _append_active_view_group_lines(lines, "Type / family-type counts", groups.get("type_grouped", {}), ACTIVE_VIEW_MEP_TYPE_GROUP_CAP)
        _append_active_view_group_lines(lines, "Category counts", groups.get("category_grouped", {}), ACTIVE_VIEW_MEP_CATEGORY_GROUP_CAP)
    else:
        _append_active_view_group_lines(lines, "By category", groups.get("category_grouped", {}), ACTIVE_VIEW_MEP_CATEGORY_GROUP_CAP)
        _append_active_view_group_lines(lines, "Top family/type groups", groups.get("type_grouped", {}), ACTIVE_VIEW_MEP_TYPE_GROUP_CAP)
    _append_active_view_group_lines(lines, "Sample levels", groups.get("level_grouped", {}), ACTIVE_VIEW_MEP_LEVEL_GROUP_CAP)
    _append_active_view_common_tail(lines, groups, collected, mode)
    return "\n".join(lines)


def report_active_view_missing_parameters(doc, uidoc, discipline_filter=None):
    collected = _collect_active_view_mep_elements(doc, uidoc, discipline_filter=discipline_filter)
    groups = _active_view_mep_groups(doc, collected)
    elems = groups.get("elements", [])
    inspected = groups.get("inspected", [])
    lines = _active_view_mep_header("[ACTIVE VIEW MISSING PARAMETERS]", doc, uidoc, collected, len(elems))
    if discipline_filter:
        lines.append("Discipline filter: {0}".format(discipline_filter))
    if not elems:
        lines.append("")
        lines.append("No supported MEP elements found in the active view.")
        lines.append("")
        lines.append("Safety note:")
        lines.append("- read-only; no model data modified.")
        return "\n".join(lines)

    checks = [
        ("Mark", lambda elem: _lookup_first_param_value(elem, ["Mark"])),
        ("Comments", lambda elem: _lookup_first_param_value(elem, ["Comments"])),
        ("Family and Type", lambda elem: _family_and_type_text(doc, elem)),
        (
            "System assignment",
            lambda elem: _system_assignment_value(elem) if _supports_system_assignment(elem) else "__not_applicable__",
        ),
        (
            "Electrical circuit/system",
            lambda elem: _electrical_assignment_value(elem) if _supports_electrical_assignment(elem) else "__not_applicable__",
        ),
    ]
    lines.append("Elements inspected for parameter details: {0}".format(len(inspected)))
    lines.append("Parameters checked: {0}".format(", ".join([label for label, _ in checks])))
    lines.append("")
    for label, resolver in checks:
        applicable = []
        missing = []
        unavailable = []
        for elem in inspected:
            try:
                value = resolver(elem)
            except Exception:
                value = "__not_applicable__"
            if value == "__not_applicable__":
                unavailable.append(elem)
                continue
            applicable.append(elem)
            if not value:
                missing.append(elem)
        lines.append(
            "{0}: missing/empty {1} | applicable {2} | unavailable/not applicable {3}".format(
                label,
                len(missing),
                len(applicable),
                len(unavailable),
            )
        )
        if missing:
            lines.append("  sample affected ElementIds: {0}".format(_sample_id_text(missing)))
    if groups.get("capped"):
        lines.append("")
        lines.append("Result set capped for readability/performance.")
    lines.append("")
    lines.append("Safety note:")
    lines.append("- read-only; no model data modified.")
    return "\n".join(lines)


def active_view_mep_summary(doc, uidoc, context=None):
    return active_view_mep_report(doc, uidoc, mode="summary")


def count_mep_elements_in_active_view(doc, uidoc, context=None):
    return active_view_mep_report(doc, uidoc, mode="count")


def report_active_view_elements_by_category(doc, uidoc, context=None):
    return active_view_mep_report(doc, uidoc, mode="category")


def report_active_view_elements_by_type(doc, uidoc, context=None):
    return active_view_mep_report(doc, uidoc, mode="type")


def active_view_qa_report(doc, uidoc, context=None):
    return active_view_mep_report(doc, uidoc, mode="qa")


def report_missing_parameters_in_active_view(doc, uidoc, context=None):
    return report_active_view_missing_parameters(doc, uidoc)


def active_view_piping_report(doc, uidoc, context=None):
    return active_view_mep_report(doc, uidoc, mode="summary", discipline_filter="Piping")


def active_view_hvac_report(doc, uidoc, context=None):
    return active_view_mep_report(doc, uidoc, mode="summary", discipline_filter="HVAC")


def active_view_electrical_report(doc, uidoc, context=None):
    return active_view_mep_report(doc, uidoc, mode="summary", discipline_filter="Electrical")


def _system_assignment_category_specs():
    return [
        ("Piping", "Pipes", "OST_PipeCurves"),
        ("Piping", "Pipe Fittings", "OST_PipeFitting"),
        ("Piping", "Pipe Accessories", "OST_PipeAccessory"),
        ("Piping", "Plumbing Fixtures", "OST_PlumbingFixtures"),
        ("HVAC", "Ducts", "OST_DuctCurves"),
        ("HVAC", "Duct Fittings", "OST_DuctFitting"),
        ("HVAC", "Duct Accessories", "OST_DuctAccessory"),
        ("HVAC", "Air Terminals", "OST_DuctTerminal"),
        ("HVAC", "Mechanical Equipment", "OST_MechanicalEquipment"),
        ("HVAC", "Flex Ducts", "OST_FlexDuctCurves"),
        ("Electrical", "Electrical Fixtures", "OST_ElectricalFixtures"),
        ("Electrical", "Electrical Equipment", "OST_ElectricalEquipment"),
        ("Electrical", "Lighting Fixtures", "OST_LightingFixtures"),
        ("Electrical", "Lighting Devices", "OST_LightingDevices"),
        ("Electrical", "Data Devices", "OST_DataDevices"),
        ("Electrical", "Communication Devices", "OST_CommunicationDevices"),
        ("Electrical", "Fire Alarm Devices", "OST_FireAlarmDevices"),
        ("Electrical", "Conduits", "OST_Conduit"),
        ("Electrical", "Conduit Fittings", "OST_ConduitFitting"),
        ("Electrical", "Cable Trays", "OST_CableTray"),
        ("Electrical", "Cable Tray Fittings", "OST_CableTrayFitting"),
        ("Electrical", "Electrical Analytical Loads", "OST_ElectricalAnalyticalLoads"),
    ]


def _system_assignment_category_lookup(discipline_filter=None):
    requested = (discipline_filter or "").lower().strip()
    lookup = {}
    for discipline, label, bic_name in _system_assignment_category_specs():
        if requested and requested != discipline.lower():
            continue
        bic = _safe_builtin_category(bic_name)
        if bic is None:
            continue
        try:
            lookup[int(bic)] = {
                "discipline": discipline,
                "label": label,
            }
        except:
            continue
    return lookup


def _collect_selected_system_assignment_elements(doc, uidoc, discipline_filter=None):
    lookup = _system_assignment_category_lookup(discipline_filter)
    elements = []
    meta = {}
    for element_id in uidoc.Selection.GetElementIds():
        elem = doc.GetElement(element_id)
        if elem is None:
            continue
        try:
            category_id = elem.Category.Id.IntegerValue
        except:
            continue
        if category_id not in lookup:
            continue
        key = _safe_int_id(elem)
        elements.append(elem)
        meta[key] = lookup[category_id]
    elements.sort(key=lambda elem: _safe_int_id(elem))
    return {
        "scope": "selection",
        "elements": elements,
        "category_meta": meta,
        "warnings": [],
        "discipline_filter": discipline_filter,
        "active_view": uidoc.ActiveView,
    }


def _collect_active_view_system_assignment_elements(doc, uidoc, discipline_filter=None):
    active_view = uidoc.ActiveView
    elements = []
    meta = {}
    warnings = []
    for discipline, label, bic_name in _system_assignment_category_specs():
        if discipline_filter and discipline.lower() != discipline_filter.lower():
            continue
        bic = _safe_builtin_category(bic_name)
        if bic is None:
            continue
        try:
            found = list(
                DB.FilteredElementCollector(doc, active_view.Id)
                .OfCategory(bic)
                .WhereElementIsNotElementType()
                .ToElements()
            )
        except Exception as err:
            warnings.append("Skipped {0}: {1}".format(label, safe_str(err)))
            continue
        for elem in found:
            if elem is None:
                continue
            key = _safe_int_id(elem)
            elements.append(elem)
            meta[key] = {
                "discipline": discipline,
                "label": label,
            }
    element_map = {}
    for elem in elements:
        element_map[_safe_int_id(elem)] = elem
    elements = list(element_map.values())
    elements.sort(key=lambda elem: _safe_int_id(elem))
    return {
        "scope": "active_view",
        "elements": elements,
        "category_meta": meta,
        "warnings": warnings,
        "discipline_filter": discipline_filter,
        "active_view": active_view,
    }


def _system_assignment_read(elem, discipline):
    try:
        if discipline == "Electrical":
            value = _lookup_first_param_value(
                elem,
                [
                    "Circuit Number",
                    "Electrical Circuit",
                    "Panel",
                    "System Name",
                    "Electrical System",
                    "System Type",
                    "System Classification",
                ],
            )
            if not value:
                value = _safe_builtin_param_value(
                    elem,
                    [
                        "RBS_ELEC_CIRCUIT_NUMBER",
                        "RBS_ELEC_PANEL_NAME",
                    ],
                )
            if value:
                return ("assigned", value)
            if _supports_electrical_assignment(elem):
                return ("missing", None)
            return ("unavailable", None)
        value = _lookup_first_param_value(
            elem,
            [
                "MEP System",
                "System Name",
                "System Type",
                "System Classification",
                "Piping System Type",
                "Duct System Type",
            ],
        )
        if not value:
            value = _safe_builtin_param_value(
                elem,
                [
                    "RBS_SYSTEM_NAME_PARAM",
                    "RBS_SYSTEM_CLASSIFICATION_PARAM",
                    "RBS_PIPING_SYSTEM_TYPE_PARAM",
                    "RBS_DUCT_SYSTEM_TYPE_PARAM",
                ],
            )
        if value:
            return ("assigned", value)
        if _supports_system_assignment(elem):
            return ("missing", None)
        return ("unavailable", None)
    except:
        return ("unknown", None)


def _system_assignment_analysis(doc, collected):
    elements = collected.get("elements", [])
    inspected = elements[:ACTIVE_VIEW_MEP_DETAIL_CAP]
    meta = collected.get("category_meta", {})
    discipline_counts = {"Piping": 0, "HVAC": 0, "Electrical": 0, "Other supported / coordination": 0}
    category_grouped = {}
    type_grouped = {}
    level_grouped = {}
    by_system = {}
    missing_by_category = {}
    unavailable_by_category = {}
    unknown_by_category = {}
    missing = []
    unavailable = []
    unknown = []
    assigned = []
    for elem in inspected:
        key = _safe_int_id(elem)
        info = meta.get(key, {})
        discipline = info.get("discipline") or "Other supported / coordination"
        category_name = info.get("label") or _category_name(elem)
        discipline_counts.setdefault(discipline, 0)
        discipline_counts[discipline] += 1
        category_grouped.setdefault(category_name, []).append(elem)
        type_grouped.setdefault(_family_and_type_text(doc, elem) or "(no type)", []).append(elem)
        level_name = _element_level_name(doc, elem)
        if level_name:
            level_grouped.setdefault(level_name, []).append(elem)
        status, value = _system_assignment_read(elem, discipline)
        if status == "assigned":
            assigned.append(elem)
            system_name = value or "(readable value)"
            by_system.setdefault(system_name, []).append(elem)
        elif status == "missing":
            missing.append(elem)
            missing_by_category.setdefault(category_name, []).append(elem)
        elif status == "unavailable":
            unavailable.append(elem)
            unavailable_by_category.setdefault(category_name, []).append(elem)
        else:
            unknown.append(elem)
            unknown_by_category.setdefault(category_name, []).append(elem)
    return {
        "elements": elements,
        "inspected": inspected,
        "discipline_counts": discipline_counts,
        "category_grouped": category_grouped,
        "type_grouped": type_grouped,
        "level_grouped": level_grouped,
        "by_system": by_system,
        "missing": missing,
        "unavailable": unavailable,
        "unknown": unknown,
        "assigned": assigned,
        "missing_by_category": missing_by_category,
        "unavailable_by_category": unavailable_by_category,
        "unknown_by_category": unknown_by_category,
        "capped": len(elements) > ACTIVE_VIEW_MEP_DETAIL_CAP,
    }


def _format_system_assignment_report(doc, uidoc, collected, title):
    analysis = _system_assignment_analysis(doc, collected)
    active_view = collected.get("active_view") or uidoc.ActiveView
    try:
        view_type = safe_str(active_view.ViewType)
    except:
        view_type = "unknown"
    scope = collected.get("scope")
    elements = analysis.get("elements", [])
    inspected = analysis.get("inspected", [])
    lines = [
        title,
        "Selection scope: {0} / active document only".format("selected elements" if scope == "selection" else "active view"),
        "Active document: {0}".format(_document_title(doc)),
        "Active view: {0} [{1}]".format(_active_view_title(doc, uidoc), view_type),
    ]
    if collected.get("discipline_filter"):
        lines.append("Discipline filter: {0}".format(collected.get("discipline_filter")))
    if scope == "selection":
        lines.append("Total selected supported MEP elements: {0}".format(len(elements)))
    else:
        lines.append("Total supported MEP elements in active view: {0}".format(len(elements)))
        lines.append("Elements inspected for system details: {0}".format(len(inspected)))
    if not elements:
        lines.append("")
        if scope == "selection":
            lines.append("No supported MEP elements selected. Select elements or use an active-view system report.")
        else:
            lines.append("No supported MEP elements found in the active view.")
        lines.append("")
        lines.append("Safety note:")
        lines.append("- read-only; no model data modified.")
        return "\n".join(lines)

    lines.append("")
    lines.append("By discipline")
    for discipline in ["Piping", "HVAC", "Electrical", "Other supported / coordination"]:
        lines.append("- {0}: {1}".format(discipline, analysis.get("discipline_counts", {}).get(discipline, 0)))

    lines.append("")
    lines.append("System assignment summary")
    lines.append("- Assigned/readable: {0}".format(len(analysis.get("assigned", []))))
    lines.append("- Missing/empty: {0}".format(len(analysis.get("missing", []))))
    lines.append("- Unavailable/not applicable: {0}".format(len(analysis.get("unavailable", []))))
    lines.append("- Unknown/error: {0}".format(len(analysis.get("unknown", []))))

    lines.append("")
    lines.append("By system / classification")
    systems = sorted(analysis.get("by_system", {}).items(), key=lambda item: (-len(item[1]), item[0]))
    if systems:
        for system_name, items in systems[:ACTIVE_VIEW_MEP_CATEGORY_GROUP_CAP]:
            categories = sorted(set([_category_name(elem) for elem in items]))
            lines.append("- {0}: {1} | categories: {2} | sample ids: {3}".format(system_name, len(items), ", ".join(categories[:5]), _sample_id_text(items)))
        if len(systems) > ACTIVE_VIEW_MEP_CATEGORY_GROUP_CAP:
            lines.append("...showing first {0} of {1} system groups".format(ACTIVE_VIEW_MEP_CATEGORY_GROUP_CAP, len(systems)))
    else:
        lines.append("- none")

    _append_active_view_group_lines(lines, "Missing / empty system assignment", analysis.get("missing_by_category", {}), ACTIVE_VIEW_MEP_CATEGORY_GROUP_CAP)
    _append_active_view_group_lines(lines, "Unavailable / not applicable", analysis.get("unavailable_by_category", {}), ACTIVE_VIEW_MEP_CATEGORY_GROUP_CAP)
    _append_active_view_group_lines(lines, "By category", analysis.get("category_grouped", {}), ACTIVE_VIEW_MEP_CATEGORY_GROUP_CAP)
    _append_active_view_group_lines(lines, "Top family/type groups", analysis.get("type_grouped", {}), ACTIVE_VIEW_MEP_TYPE_GROUP_CAP)
    _append_active_view_group_lines(lines, "Sample levels", analysis.get("level_grouped", {}), ACTIVE_VIEW_MEP_LEVEL_GROUP_CAP)

    lines.append("")
    lines.append("Sample ElementIds: {0}".format(_sample_id_text(elements, limit=ACTIVE_VIEW_MEP_SAMPLE_ID_CAP)))
    lines.append("")
    lines.append("Notes / Warnings")
    notes = []
    visible_disciplines = [name for name, count in analysis.get("discipline_counts", {}).items() if count]
    if scope == "selection" and len(visible_disciplines) > 1:
        notes.append("Mixed disciplines selected: {0}".format(len(visible_disciplines)))
    if analysis.get("missing"):
        notes.append("Missing/empty system assignment found on {0} element(s).".format(len(analysis.get("missing"))))
    if analysis.get("unavailable") and analysis.get("discipline_counts", {}).get("Electrical", 0):
        notes.append("Electrical circuit/system data unavailable for {0} element(s).".format(len(analysis.get("unavailable"))))
    if scope == "active_view":
        notes.append("Visible Revit links are not scanned for internal systems.")
    for warning in collected.get("warnings", []):
        notes.append(warning)
    if analysis.get("capped"):
        notes.append("Detailed system results are based on capped inspection.")
        notes.append("Result set capped for readability/performance.")
    if not notes:
        notes.append("No system-assignment QA warnings detected by this read-only report.")
    for note in notes:
        lines.append("- {0}".format(note))
    lines.append("")
    lines.append("Safety note:")
    lines.append("- read-only; no model data modified.")
    return "\n".join(lines)


def selected_mep_system_assignment_report(doc, uidoc, context=None, discipline_filter=None):
    collected = _collect_selected_system_assignment_elements(doc, uidoc, discipline_filter=discipline_filter)
    return _format_system_assignment_report(doc, uidoc, collected, "[SELECTED MEP SYSTEM ASSIGNMENT REPORT]")


def active_view_mep_system_assignment_report(doc, uidoc, context=None, discipline_filter=None):
    collected = _collect_active_view_system_assignment_elements(doc, uidoc, discipline_filter=discipline_filter)
    return _format_system_assignment_report(doc, uidoc, collected, "[ACTIVE VIEW MEP SYSTEM ASSIGNMENT REPORT]")


def selected_pipe_system_report(doc, uidoc, context=None):
    return selected_mep_system_assignment_report(doc, uidoc, context, discipline_filter="Piping")


def active_view_pipe_system_report(doc, uidoc, context=None):
    return active_view_mep_system_assignment_report(doc, uidoc, context, discipline_filter="Piping")


def selected_duct_system_report(doc, uidoc, context=None):
    return selected_mep_system_assignment_report(doc, uidoc, context, discipline_filter="HVAC")


def active_view_duct_system_report(doc, uidoc, context=None):
    return active_view_mep_system_assignment_report(doc, uidoc, context, discipline_filter="HVAC")


def selected_electrical_system_report(doc, uidoc, context=None):
    return selected_mep_system_assignment_report(doc, uidoc, context, discipline_filter="Electrical")


def active_view_electrical_system_report(doc, uidoc, context=None):
    return active_view_mep_system_assignment_report(doc, uidoc, context, discipline_filter="Electrical")


def safe_get_param_value(elem, candidate_names_or_builtins):
    names = []
    builtins = []
    for candidate in candidate_names_or_builtins or []:
        if isinstance(candidate, str):
            if candidate.startswith("RBS_") or candidate.startswith("INSTANCE_") or candidate.startswith("ALL_MODEL_"):
                builtins.append(candidate)
            names.append(candidate)
        else:
            try:
                value = _value_from_param(elem.get_Parameter(candidate))
            except:
                value = None
            if value:
                return value
    value = _lookup_first_param_value(elem, names)
    if value:
        return value
    return _safe_builtin_param_value(elem, builtins)


def safe_get_level_name(elem, doc):
    return _element_level_name(doc, elem)


def safe_get_type_label(elem, doc):
    return _family_and_type_text(doc, elem) or "(no type)"


def classify_category_to_discipline(category_name):
    text = (category_name or "").lower()
    if "pipe" in text or "plumbing" in text:
        return "Piping"
    if "duct" in text or "air terminal" in text or "mechanical equipment" in text:
        return "HVAC"
    if "electrical" in text or "lighting" in text or "conduit" in text or "cable tray" in text or "data device" in text or "communication" in text or "fire alarm" in text:
        return "Electrical"
    return "Other supported / coordination"


def _qa_result(rule_id, discipline, label, severity, result, elem, category, value=None, reason=""):
    return {
        "rule_id": rule_id,
        "discipline": discipline,
        "label": label,
        "severity": severity,
        "result": result,
        "category": category,
        "element_id": _safe_int_id(elem),
        "element": elem,
        "value": value,
        "reason": reason,
    }


def _qa_check_value(elem, candidates):
    try:
        value = safe_get_param_value(elem, candidates)
    except Exception as err:
        return ("error", None, safe_str(err))
    if value is None:
        return ("unavailable", None, "parameter unavailable")
    if safe_str(value).strip() == "":
        return ("fail", None, "parameter empty")
    return ("pass", value, "readable")


def _qa_presence_rule(elem, category, discipline, rule_id, label, candidates, missing_is_fail=True):
    status, value, reason = _qa_check_value(elem, candidates)
    if status == "unavailable" and missing_is_fail:
        return _qa_result(rule_id, discipline, label, "review", "fail", elem, category, value, "missing or empty")
    if status == "unavailable":
        return _qa_result(rule_id, discipline, label, "info", "unavailable", elem, category, value, reason)
    if status == "error":
        return _qa_result(rule_id, discipline, label, "info", "error", elem, category, value, reason)
    if status == "fail":
        return _qa_result(rule_id, discipline, label, "review", "fail", elem, category, value, reason)
    return _qa_result(rule_id, discipline, label, "info", "pass", elem, category, value, reason)


def _is_category_named(category_name, tokens):
    text = (category_name or "").lower()
    return any(token.lower() in text for token in tokens)


def _evaluate_discipline_qa_rules_for_element(doc, elem, meta):
    category = meta.get("label") or _category_name(elem)
    discipline = meta.get("discipline") or classify_category_to_discipline(category)
    results = []
    results.append(_qa_presence_rule(elem, category, discipline, "COMMON-001", "Mark present", ["Mark"]))
    results.append(_qa_presence_rule(elem, category, discipline, "COMMON-002", "Comments present", ["Comments"]))
    type_label = safe_get_type_label(elem, doc)
    if type_label and type_label != "(no type)":
        results.append(_qa_result("COMMON-003", discipline, "Family and Type readable", "info", "pass", elem, category, type_label, "readable"))
    else:
        results.append(_qa_result("COMMON-003", discipline, "Family and Type readable", "review", "fail", elem, category, None, "family/type not readable"))
    level_name = safe_get_level_name(elem, doc)
    if level_name:
        results.append(_qa_result("COMMON-004", discipline, "Level/reference level readable", "info", "pass", elem, category, level_name, "readable"))
    else:
        results.append(_qa_result("COMMON-004", discipline, "Level/reference level readable", "review", "fail", elem, category, None, "level/reference level not readable"))
    if category:
        results.append(_qa_result("COMMON-005", discipline, "Element has category", "info", "pass", elem, category, category, "readable"))
    else:
        results.append(_qa_result("COMMON-005", discipline, "Element has category", "review", "fail", elem, category, None, "category not readable"))

    if discipline == "Piping":
        status, value = _system_assignment_read(elem, discipline)
        if status == "assigned":
            results.append(_qa_result("PIP-001", discipline, "System assignment readable", "info", "pass", elem, category, value, "readable"))
        elif status == "missing":
            results.append(_qa_result("PIP-001", discipline, "System assignment readable", "review", "fail", elem, category, None, "missing or empty"))
        elif status == "unavailable":
            results.append(_qa_result("PIP-001", discipline, "System assignment readable", "info", "not_applicable", elem, category, None, "category-specific not applicable or unavailable"))
        else:
            results.append(_qa_result("PIP-001", discipline, "System assignment readable", "info", "error", elem, category, None, "read error"))
        results.append(_qa_presence_rule(elem, category, discipline, "PIP-002", "Level/reference level readable", ["Level", "Reference Level", "Schedule Level", "Base Level"], missing_is_fail=True))
        results.append(_qa_presence_rule(elem, category, discipline, "PIP-003", "Size / Diameter readable", ["Size", "Diameter", "Nominal Diameter", "Outside Diameter"], missing_is_fail=False))
        results.append(_qa_presence_rule(elem, category, discipline, "PIP-004", "Mark present", ["Mark"], missing_is_fail=True))
        results.append(_qa_presence_rule(elem, category, discipline, "PIP-005", "Comments present", ["Comments"], missing_is_fail=True))
        if _is_category_named(category, ["Pipes"]):
            results.append(_qa_presence_rule(elem, category, discipline, "PIP-006", "Pipe slope readable", ["Slope"], missing_is_fail=False))
            results.append(_qa_presence_rule(elem, category, discipline, "PIP-007", "Pipe length readable", ["Length"], missing_is_fail=False))
        else:
            results.append(_qa_result("PIP-006", discipline, "Pipe slope readable", "info", "not_applicable", elem, category, None, "not a pipe curve"))
            results.append(_qa_result("PIP-007", discipline, "Pipe length readable", "info", "not_applicable", elem, category, None, "not a pipe curve"))
    elif discipline == "HVAC":
        status, value = _system_assignment_read(elem, discipline)
        if status == "assigned":
            results.append(_qa_result("HVAC-001", discipline, "System assignment readable", "info", "pass", elem, category, value, "readable"))
        elif status == "missing":
            results.append(_qa_result("HVAC-001", discipline, "System assignment readable", "review", "fail", elem, category, None, "missing or empty"))
        elif status == "unavailable":
            results.append(_qa_result("HVAC-001", discipline, "System assignment readable", "info", "not_applicable", elem, category, None, "category-specific not applicable or unavailable"))
        else:
            results.append(_qa_result("HVAC-001", discipline, "System assignment readable", "info", "error", elem, category, None, "read error"))
        results.append(_qa_presence_rule(elem, category, discipline, "HVAC-002", "Level/reference level readable", ["Level", "Reference Level", "Schedule Level", "Base Level"], missing_is_fail=True))
        if _is_category_named(category, ["Duct", "Flex Duct"]):
            results.append(_qa_presence_rule(elem, category, discipline, "HVAC-003", "Size readable", ["Size", "Width", "Height", "Diameter"], missing_is_fail=False))
        else:
            results.append(_qa_result("HVAC-003", discipline, "Size readable", "info", "not_applicable", elem, category, None, "category-specific not applicable"))
        if _is_category_named(category, ["Air Terminal"]):
            results.append(_qa_presence_rule(elem, category, discipline, "HVAC-004", "Flow readable", ["Flow", "Air Flow", "Supply Airflow", "Return Airflow", "Exhaust Airflow"], missing_is_fail=False))
        else:
            results.append(_qa_result("HVAC-004", discipline, "Flow readable", "info", "not_applicable", elem, category, None, "category-specific not applicable"))
        results.append(_qa_presence_rule(elem, category, discipline, "HVAC-005", "Mark present", ["Mark"], missing_is_fail=True))
        results.append(_qa_presence_rule(elem, category, discipline, "HVAC-006", "Comments present", ["Comments"], missing_is_fail=True))
        results.append(_qa_presence_rule(elem, category, discipline, "HVAC-007", "Family and Type readable", ["Family and Type", "Type Name"], missing_is_fail=True))
    elif discipline == "Electrical":
        status, value = _system_assignment_read(elem, discipline)
        circuit_applicable = _supports_electrical_assignment(elem)
        if status == "assigned":
            results.append(_qa_result("ELEC-001", discipline, "Electrical circuit/system readable", "info", "pass", elem, category, value, "readable"))
        elif status == "missing" and circuit_applicable:
            results.append(_qa_result("ELEC-001", discipline, "Electrical circuit/system readable", "review", "fail", elem, category, None, "missing or empty"))
        else:
            results.append(_qa_result("ELEC-001", discipline, "Electrical circuit/system readable", "info", "not_applicable", elem, category, None, "category-specific not applicable or unavailable"))
        results.append(_qa_presence_rule(elem, category, discipline, "ELEC-002", "Panel readable", ["Panel", "Panel Name", "RBS_ELEC_PANEL_NAME"], missing_is_fail=circuit_applicable))
        results.append(_qa_presence_rule(elem, category, discipline, "ELEC-003", "Circuit Number readable", ["Circuit Number", "Electrical Circuit", "RBS_ELEC_CIRCUIT_NUMBER"], missing_is_fail=circuit_applicable))
        results.append(_qa_presence_rule(elem, category, discipline, "ELEC-004", "Level/reference level readable", ["Level", "Reference Level", "Schedule Level", "Base Level"], missing_is_fail=True))
        results.append(_qa_presence_rule(elem, category, discipline, "ELEC-005", "Mark present", ["Mark"], missing_is_fail=True))
        results.append(_qa_presence_rule(elem, category, discipline, "ELEC-006", "Comments present", ["Comments"], missing_is_fail=True))
        results.append(_qa_presence_rule(elem, category, discipline, "ELEC-007", "Family and Type readable", ["Family and Type", "Type Name"], missing_is_fail=True))
        if _is_category_named(category, ["Conduit", "Cable Tray"]):
            results.append(_qa_presence_rule(elem, category, discipline, "ELEC-008", "Conduit/cable tray size readable", ["Size", "Diameter", "Trade Size"], missing_is_fail=False))
        else:
            results.append(_qa_result("ELEC-008", discipline, "Conduit/cable tray size readable", "info", "not_applicable", elem, category, None, "category-specific not applicable"))
    return results


def summarize_rule_results(results):
    summary = {
        "pass": 0,
        "fail": 0,
        "unavailable": 0,
        "not_applicable": 0,
        "error": 0,
        "failed_element_ids": set(),
    }
    for result in results:
        state = result.get("result") or "error"
        if state in summary:
            summary[state] += 1
        else:
            summary["error"] += 1
        if state == "fail":
            summary["failed_element_ids"].add(result.get("element_id"))
    return summary


def _dedupe_rule_results_for_summary(results):
    discipline_identity_rule_ids = set(["PIP-004", "PIP-005", "HVAC-005", "HVAC-006", "ELEC-005", "ELEC-006"])
    element_ids_with_discipline_identity = set()
    for result in results:
        if result.get("rule_id") in discipline_identity_rule_ids:
            element_ids_with_discipline_identity.add(result.get("element_id"))
    filtered = []
    for result in results:
        if result.get("rule_id") in ("COMMON-001", "COMMON-002") and result.get("element_id") in element_ids_with_discipline_identity:
            continue
        filtered.append(result)
    return filtered


def _unique_elements_from_rule_results(items):
    unique = []
    seen = set()
    for item in items:
        elem = item.get("element")
        elem_id = item.get("element_id")
        if elem is None or elem_id in seen:
            continue
        unique.append(elem)
        seen.add(elem_id)
    return unique


def _collect_discipline_qa_elements(doc, uidoc, scope, discipline_filter=None):
    if scope == "selection":
        return _collect_selected_system_assignment_elements(doc, uidoc, discipline_filter=discipline_filter)
    return _collect_active_view_system_assignment_elements(doc, uidoc, discipline_filter=discipline_filter)


def _format_discipline_qa_report(doc, uidoc, scope, discipline_filter=None):
    collected = _collect_discipline_qa_elements(doc, uidoc, scope, discipline_filter)
    elements = collected.get("elements", [])
    inspected = elements[:ACTIVE_VIEW_MEP_DETAIL_CAP]
    meta = collected.get("category_meta", {})
    active_view = collected.get("active_view") or uidoc.ActiveView
    try:
        view_type = safe_str(active_view.ViewType)
    except:
        view_type = "unknown"
    title = "[SELECTED DISCIPLINE QA REPORT]" if scope == "selection" else "[ACTIVE VIEW DISCIPLINE QA REPORT]"
    lines = [
        title,
        "Selection scope: {0} / active document only".format("selected elements" if scope == "selection" else "active view"),
        "Active document: {0}".format(_document_title(doc)),
        "Active view: {0} [{1}]".format(_active_view_title(doc, uidoc), view_type),
        "Discipline filter: {0}".format(discipline_filter or "All"),
    ]
    if scope == "selection":
        lines.append("Total selected supported MEP elements: {0}".format(len(elements)))
    else:
        lines.append("Total supported MEP elements in active view: {0}".format(len(elements)))
    lines.append("Elements inspected for QA details: {0}".format(len(inspected)))
    if not elements:
        lines.append("")
        if scope == "selection":
            lines.append("No supported MEP elements selected. Select elements or use an active-view discipline QA report.")
        else:
            lines.append("No supported MEP elements found in the active view.")
        lines.append("")
        lines.append("Safety note:")
        lines.append("- read-only; no model data modified.")
        return "\n".join(lines)

    discipline_counts = {"Piping": 0, "HVAC": 0, "Electrical": 0, "Other supported / coordination": 0}
    results = []
    for elem in inspected:
        info = meta.get(_safe_int_id(elem), {})
        discipline = info.get("discipline") or classify_category_to_discipline(info.get("label") or _category_name(elem))
        discipline_counts.setdefault(discipline, 0)
        discipline_counts[discipline] += 1
        results.extend(_evaluate_discipline_qa_rules_for_element(doc, elem, info))
    results = _dedupe_rule_results_for_summary(results)
    summary = summarize_rule_results(results)
    fail_results = [result for result in results if result.get("result") == "fail"]
    unavailable_results = [result for result in results if result.get("result") in ("unavailable", "not_applicable")]

    def grouped_by_rule(items):
        grouped = {}
        for item in items:
            key = (item.get("rule_id"), item.get("label"))
            grouped.setdefault(key, []).append(item)
        return grouped

    def grouped_by_field(items, field):
        grouped = {}
        for item in items:
            key = item.get(field) or "(unknown)"
            grouped.setdefault(key, []).append(item)
        return grouped

    lines.append("")
    lines.append("By discipline")
    for discipline in ["Piping", "HVAC", "Electrical", "Other supported / coordination"]:
        lines.append("- {0}: {1}".format(discipline, discipline_counts.get(discipline, 0)))
    lines.append("")
    lines.append("QA result summary")
    lines.append("- Pass checks: {0}".format(summary.get("pass", 0)))
    lines.append("- Failed checks: {0}".format(summary.get("fail", 0)))
    lines.append("- Unavailable/not applicable checks: {0}".format(summary.get("unavailable", 0) + summary.get("not_applicable", 0)))
    lines.append("- Error checks: {0}".format(summary.get("error", 0)))
    lines.append("- Elements with one or more failed checks: {0}".format(len(summary.get("failed_element_ids", set()))))

    lines.append("")
    lines.append("Failed checks by rule")
    rule_groups = grouped_by_rule(fail_results)
    if rule_groups:
        for (rule_id, label), items in sorted(rule_groups.items(), key=lambda item: (-len(item[1]), item[0][0]))[:20]:
            elems = _unique_elements_from_rule_results(items)
            lines.append("- {0} {1}: {2} | sample ids: {3}".format(rule_id, label, len(items), _sample_id_text(elems)))
    else:
        lines.append("- none")

    lines.append("")
    lines.append("Failed checks by category")
    category_groups = grouped_by_field(fail_results, "category")
    if category_groups:
        for category, items in sorted(category_groups.items(), key=lambda item: (-len(item[1]), item[0]))[:20]:
            elems = _unique_elements_from_rule_results(items)
            lines.append("- {0}: {1} | sample ids: {2}".format(category, len(items), _sample_id_text(elems)))
    else:
        lines.append("- none")

    lines.append("")
    lines.append("Top affected family/type groups")
    type_groups = {}
    for item in fail_results:
        elem = item.get("element")
        if elem is None:
            continue
        type_groups.setdefault(safe_get_type_label(elem, doc), {})[_safe_int_id(elem)] = elem
    if type_groups:
        for type_name, elem_map in sorted(type_groups.items(), key=lambda item: (-len(item[1]), item[0]))[:20]:
            elems = list(elem_map.values())
            lines.append("- {0}: {1} | sample ids: {2}".format(type_name, len(elems), _sample_id_text(elems)))
    else:
        lines.append("- none")

    lines.append("")
    lines.append("Sample affected levels")
    level_groups = {}
    for item in fail_results:
        elem = item.get("element")
        if elem is None:
            continue
        level_groups.setdefault(safe_get_level_name(elem, doc) or "(level not found)", {})[_safe_int_id(elem)] = elem
    if level_groups:
        for level_name, elem_map in sorted(level_groups.items(), key=lambda item: (-len(item[1]), item[0]))[:10]:
            elems = list(elem_map.values())
            lines.append("- {0}: {1} | sample ids: {2}".format(level_name, len(elems), _sample_id_text(elems)))
    else:
        lines.append("- none")

    lines.append("")
    lines.append("Unavailable / not applicable highlights")
    unavailable_groups = grouped_by_rule(unavailable_results)
    if unavailable_groups:
        for (rule_id, label), items in sorted(unavailable_groups.items(), key=lambda item: (-len(item[1]), item[0][0]))[:10]:
            categories = sorted(set([item.get("category") or "(unknown)" for item in items]))
            elems = _unique_elements_from_rule_results(items)
            lines.append("- {0} {1}: {2} | categories: {3} | sample ids: {4}".format(rule_id, label, len(items), ", ".join(categories[:5]), _sample_id_text(elems)))
    else:
        lines.append("- none")

    failed_elems = []
    seen = set()
    for item in fail_results:
        elem = item.get("element")
        elem_id = item.get("element_id")
        if elem is not None and elem_id not in seen:
            failed_elems.append(elem)
            seen.add(elem_id)
    lines.append("")
    lines.append("Sample ElementIds with failures: {0}".format(_sample_id_text(failed_elems, limit=ACTIVE_VIEW_MEP_SAMPLE_ID_CAP)))
    lines.append("")
    lines.append("Notes / Warnings")
    notes = []
    visible_disciplines = [name for name, count in discipline_counts.items() if count]
    if len(visible_disciplines) > 1:
        notes.append("Mixed disciplines {0}: {1}".format("selected" if scope == "selection" else "visible", len(visible_disciplines)))
    notes.append("Counts represent rule evaluations; sample ElementIds are deduplicated.")
    if scope == "active_view":
        notes.append("Visible Revit links are not scanned for internal QA.")
    if len(elements) > ACTIVE_VIEW_MEP_DETAIL_CAP:
        notes.append("Detailed QA results are based on capped inspection.")
        notes.append("Result set capped for readability/performance.")
    notes.append("This is a rule-based read-only diagnostic. It does not modify parameters or systems.")
    for note in notes:
        lines.append("- {0}".format(note))
    lines.append("")
    lines.append("Safety note:")
    lines.append("- read-only; no model data modified.")
    return "\n".join(lines)


def selected_discipline_qa_report(doc, uidoc, context=None, discipline_filter=None):
    return _format_discipline_qa_report(doc, uidoc, "selection", discipline_filter=discipline_filter)


def active_view_discipline_qa_report(doc, uidoc, context=None, discipline_filter=None):
    return _format_discipline_qa_report(doc, uidoc, "active_view", discipline_filter=discipline_filter)


def selected_piping_qa_report(doc, uidoc, context=None):
    return selected_discipline_qa_report(doc, uidoc, context, discipline_filter="Piping")


def active_view_piping_qa_report(doc, uidoc, context=None):
    return active_view_discipline_qa_report(doc, uidoc, context, discipline_filter="Piping")


def selected_hvac_qa_report(doc, uidoc, context=None):
    return selected_discipline_qa_report(doc, uidoc, context, discipline_filter="HVAC")


def active_view_hvac_qa_report(doc, uidoc, context=None):
    return active_view_discipline_qa_report(doc, uidoc, context, discipline_filter="HVAC")


def selected_electrical_qa_report(doc, uidoc, context=None):
    return selected_discipline_qa_report(doc, uidoc, context, discipline_filter="Electrical")


def active_view_electrical_qa_report(doc, uidoc, context=None):
    return active_view_discipline_qa_report(doc, uidoc, context, discipline_filter="Electrical")


def report_selected_elements_by_category(doc, uidoc):
    elems = _selected_elements(doc, uidoc)
    type_grouped = {}
    level_grouped = {}
    lines = [
        "[SELECTED ELEMENTS BY CATEGORY]",
        "Selection scope: active document only",
        "Active document: {0}".format(_document_title(doc)),
        "Active view: {0}".format(_active_view_title(doc, uidoc)),
        "Total selected: {0}".format(len(elems)),
    ]
    if not elems:
        lines.append("No elements selected. Select elements or use an active-view report.")
        lines.append("Safety note: read-only; no model data modified.")
        return "\n".join(lines)
    grouped = {}
    try:
        for elem in elems:
            if not elem:
                continue
            category_name = _category_name(elem)
            grouped.setdefault(category_name, []).append(elem)
            type_grouped.setdefault(_family_and_type_text(doc, elem) or "(no type)", []).append(elem)
            level_grouped.setdefault(_element_level_name(doc, elem) or "(level not found)", []).append(elem)
    except Exception as err:
        lines.append("Unable to group selected elements by category.")
        lines.append("Diagnostic: {0}".format(safe_str(err)))
        return "\n".join(lines)
    if not grouped:
        lines.append("No elements selected. Select elements or use an active-view report.")
        lines.append("Safety note: read-only; no model data modified.")
        return "\n".join(lines)
    lines.append("")
    lines.append("Category counts")
    ordered = sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0]))
    for index, (category_name, items) in enumerate(ordered[:20]):
        lines.append("- {0}: {1} | sample ids: {2}".format(category_name, len(items), _sample_id_text(items)))
    if len(ordered) > 20:
        lines.append("...showing first 20 of {0} category groups".format(len(ordered)))
    lines.append("")
    lines.append("Top family/type groups")
    for type_name, items in sorted(type_grouped.items(), key=lambda item: (-len(item[1]), item[0]))[:5]:
        lines.append("- {0}: {1} | sample ids: {2}".format(type_name, len(items), _sample_id_text(items)))
    lines.append("")
    lines.append("Sample levels")
    for level_name, items in sorted(level_grouped.items(), key=lambda item: (-len(item[1]), item[0]))[:5]:
        lines.append("- {0}: {1}".format(level_name, len(items)))
    lines.append("")
    lines.append("Sample ElementIds: {0}".format(_sample_id_text(elems, limit=10)))
    lines.append("Safety note: read-only; no model data modified.")
    return "\n".join(lines)


def report_selected_elements_by_type(doc, uidoc):
    elems = _selected_elements(doc, uidoc)
    grouped = {}
    category_grouped = {}
    level_grouped = {}
    for elem in elems:
        if not elem:
            continue
        type_name = _family_and_type_text(doc, elem) or "(no type)"
        grouped.setdefault(type_name, []).append(elem)
        category_grouped.setdefault(_category_name(elem), []).append(elem)
        level_grouped.setdefault(_element_level_name(doc, elem) or "(level not found)", []).append(elem)
    lines = [
        "[SELECTED ELEMENTS BY TYPE]",
        "Selection scope: active document only",
        "Active document: {0}".format(_document_title(doc)),
        "Active view: {0}".format(_active_view_title(doc, uidoc)),
        "Total selected: {0}".format(len(elems)),
    ]
    if not elems:
        lines.append("No elements selected. Select elements or use an active-view report.")
        lines.append("Safety note: read-only; no model data modified.")
        return "\n".join(lines)
    if not grouped:
        lines.append("No elements selected. Select elements or use an active-view report.")
        lines.append("Safety note: read-only; no model data modified.")
        return "\n".join(lines)
    lines.append("")
    lines.append("Type / family-type counts")
    ordered = sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0]))
    for type_name, items in ordered[:20]:
        lines.append("- {0}: {1} | sample ids: {2}".format(type_name, len(items), _sample_id_text(items)))
    if len(ordered) > 20:
        lines.append("...showing first 20 of {0} type groups".format(len(ordered)))
    lines.append("")
    lines.append("By category")
    for category_name, items in sorted(category_grouped.items(), key=lambda item: (-len(item[1]), item[0]))[:10]:
        lines.append("- {0}: {1} | sample ids: {2}".format(category_name, len(items), _sample_id_text(items)))
    lines.append("")
    lines.append("Sample levels")
    for level_name, items in sorted(level_grouped.items(), key=lambda item: (-len(item[1]), item[0]))[:5]:
        lines.append("- {0}: {1}".format(level_name, len(items)))
    lines.append("")
    lines.append("Sample ElementIds: {0}".format(_sample_id_text(elems, limit=10)))
    lines.append("Safety note: read-only; no model data modified.")
    return "\n".join(lines)


def count_selected_elements(doc, uidoc):
    elems = _selected_elements(doc, uidoc)
    grouped = {}
    type_grouped = {}
    level_grouped = {}
    for elem in elems:
        if not elem:
            continue
        grouped.setdefault(_category_name(elem), []).append(elem)
        type_grouped.setdefault(_family_and_type_text(doc, elem) or "(no type)", []).append(elem)
        level_name = _element_level_name(doc, elem) or "(level not found)"
        level_grouped.setdefault(level_name, []).append(elem)
    lines = [
        "[COUNT SELECTED ELEMENTS]",
        "Selection scope: active document only",
        "Active document: {0}".format(_document_title(doc)),
        "Active view: {0}".format(_active_view_title(doc, uidoc)),
        "Total selected: {0}".format(len(elems)),
    ]
    if not elems:
        lines.append("No elements selected. Select elements or use an active-view report.")
        lines.append("Safety note: read-only; no model data modified.")
        return "\n".join(lines)
    lines.append("")
    lines.append("By category")
    for category_name, items in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0]))[:20]:
        lines.append("- {0}: {1} | sample ids: {2}".format(category_name, len(items), _sample_id_text(items)))
    lines.append("")
    lines.append("Top family/type groups")
    for type_name, items in sorted(type_grouped.items(), key=lambda item: (-len(item[1]), item[0]))[:10]:
        lines.append("- {0}: {1} | sample ids: {2}".format(type_name, len(items), _sample_id_text(items)))
    lines.append("")
    lines.append("Sample levels")
    for level_name, items in sorted(level_grouped.items(), key=lambda item: (-len(item[1]), item[0]))[:10]:
        lines.append("- {0}: {1}".format(level_name, len(items)))
    lines.append("")
    lines.append("Sample ElementIds: {0}".format(_sample_id_text(elems, limit=10)))
    lines.append("Safety note: read-only; no model data modified.")
    return "\n".join(lines)


def health_check_selected_elements(doc, uidoc):
    elems = _selected_elements(doc, uidoc)
    grouped = {}
    type_grouped = {}
    level_grouped = {}
    missing_level = []
    linked_or_import = []
    for elem in elems:
        if not elem:
            continue
        category_name = _category_name(elem)
        grouped.setdefault(category_name, []).append(elem)
        type_grouped.setdefault(_family_and_type_text(doc, elem) or "(no type)", []).append(elem)
        level_name = _element_level_name(doc, elem)
        if level_name:
            level_grouped.setdefault(level_name, []).append(elem)
        else:
            missing_level.append(elem)
        try:
            if isinstance(elem, DB.ImportInstance):
                linked_or_import.append(elem)
        except:
            pass
        if category_name in ("Revit Links", "Imports in Families") or "Import" in category_name or "Link" in category_name:
            linked_or_import.append(elem)

    lines = [
        "[SELECTION HEALTH CHECK]",
        "Selection scope: active document only",
        "Active document: {0}".format(_document_title(doc)),
        "Active view: {0}".format(_active_view_title(doc, uidoc)),
        "Total selected: {0}".format(len(elems)),
    ]
    if not elems:
        lines.append("No elements selected. Select elements or use an active-view report.")
        lines.append("Safety note: read-only; no model data modified.")
        return "\n".join(lines)

    lines.append("")
    lines.append("Category distribution")
    for category_name, items in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0]))[:20]:
        lines.append("- {0}: {1} | sample ids: {2}".format(category_name, len(items), _sample_id_text(items)))
    lines.append("")
    lines.append("Type distribution")
    for type_name, items in sorted(type_grouped.items(), key=lambda item: (-len(item[1]), item[0]))[:10]:
        lines.append("- {0}: {1} | sample ids: {2}".format(type_name, len(items), _sample_id_text(items)))
    lines.append("")
    lines.append("Level distribution")
    if level_grouped:
        for level_name, items in sorted(level_grouped.items(), key=lambda item: (-len(item[1]), item[0]))[:10]:
            lines.append("- {0}: {1}".format(level_name, len(items)))
    else:
        lines.append("- No level data found on sampled selected elements.")
    lines.append("")
    lines.append("Warnings")
    warning_count = 0
    if len(grouped) > 1:
        warning_count += 1
        lines.append("- Mixed categories selected: {0}".format(len(grouped)))
    if missing_level:
        warning_count += 1
        lines.append("- Missing level/reference-level data on {0} selected element(s); sample ids: {1}".format(len(missing_level), _sample_id_text(missing_level)))
    if linked_or_import:
        warning_count += 1
        lines.append("- Linked/import-style elements encountered: {0}; sample ids: {1}".format(len(linked_or_import), _sample_id_text(linked_or_import)))
    if not warning_count:
        lines.append("- No selection health warnings detected by this read-only check.")
    lines.append("")
    lines.append("Sample ElementIds: {0}".format(_sample_id_text(elems, limit=10)))
    lines.append("Safety note: read-only; no model data modified.")
    return "\n".join(lines)


def health_check_for_active_view_selection(doc, uidoc):
    active_view = uidoc.ActiveView
    category_specs = [
        ("Ducts", DB.BuiltInCategory.OST_DuctCurves, "system"),
        ("Duct Fittings", DB.BuiltInCategory.OST_DuctFitting, "fitting"),
        ("Pipes", DB.BuiltInCategory.OST_PipeCurves, "system"),
        ("Pipe Fittings", DB.BuiltInCategory.OST_PipeFitting, "fitting"),
        ("Electrical Fixtures", DB.BuiltInCategory.OST_ElectricalFixtures, "electrical"),
        ("Electrical Equipment", DB.BuiltInCategory.OST_ElectricalEquipment, "electrical"),
    ]
    total_count = 0
    missing_system = []
    unconnected_fittings = []
    missing_electrical = []
    category_counts = {}
    for label, category, inspection_mode in category_specs:
        elems = list(
            DB.FilteredElementCollector(doc, active_view.Id)
            .OfCategory(category)
            .WhereElementIsNotElementType()
            .ToElements()
        )
        category_counts[label] = elems
        total_count += len(elems)
        for elem in elems:
            if inspection_mode == "system" and not _system_assignment_value(elem):
                missing_system.append(elem)
            elif inspection_mode == "fitting" and _is_unconnected_fitting(elem):
                unconnected_fittings.append(elem)
            elif inspection_mode == "electrical" and not _electrical_assignment_value(elem):
                missing_electrical.append(elem)
    lines = [
        "Active-view MEP health check",
        "View scope: active view in active document",
        "Active document: {0}".format(_document_title(doc)),
        "Active view: {0}".format(active_view.Name),
        "Supported MEP elements in active view: {0}".format(total_count),
        "Elements without system assignment: {0}".format(len(missing_system)),
        "Unconnected fittings found: {0}".format(len(unconnected_fittings)),
        "Electrical fixtures/equipment without circuit/system info: {0}".format(len(missing_electrical)),
    ]
    for label, _, _ in category_specs:
        lines.append("{0}: {1}".format(label, len(category_counts.get(label, []))))
    if missing_system:
        lines.append("System-missing sample ids: {0}".format(_sample_id_text(missing_system)))
    if unconnected_fittings:
        lines.append("Unconnected-fitting sample ids: {0}".format(_sample_id_text(unconnected_fittings)))
    if missing_electrical:
        lines.append("Electrical-missing sample ids: {0}".format(_sample_id_text(missing_electrical)))
    if not (missing_system or unconnected_fittings or missing_electrical):
        lines.append("No missing-system or unconnected-fitting findings were detected in the active view summary.")
    return "\n".join(lines)


def report_missing_parameters_from_selection(doc, uidoc):
    elems = _selected_elements(doc, uidoc)
    lines = [
        "[MISSING PARAMETERS FROM SELECTION]",
        "Selection scope: active document only",
        "Active document: {0}".format(_document_title(doc)),
        "Active view: {0}".format(_active_view_title(doc, uidoc)),
        "Total selected: {0}".format(len(elems)),
    ]
    if not elems:
        lines.append("No elements selected. Select elements or use an active-view report.")
        lines.append("Safety note: read-only; no model data modified.")
        return "\n".join(lines)

    checks = [
        ("Mark", lambda elem: _lookup_first_param_value(elem, ["Mark"])),
        ("Comments", lambda elem: _lookup_first_param_value(elem, ["Comments"])),
        ("Family and Type", lambda elem: _family_and_type_text(doc, elem)),
        (
            "System assignment",
            lambda elem: _system_assignment_value(elem) if _supports_system_assignment(elem) else "__not_applicable__",
        ),
        (
            "Electrical circuit/system",
            lambda elem: _electrical_assignment_value(elem) if _supports_electrical_assignment(elem) else "__not_applicable__",
        ),
    ]

    lines.append("Parameters checked: {0}".format(", ".join([label for label, _ in checks])))
    lines.append("")

    findings = []
    for label, resolver in checks:
        applicable = []
        missing = []
        unavailable = []
        for elem in elems:
            try:
                value = resolver(elem)
            except Exception:
                value = "__not_applicable__"
            if value == "__not_applicable__":
                unavailable.append(elem)
                continue
            applicable.append(elem)
            if not value:
                missing.append(elem)
        findings.append((label, applicable, missing, unavailable))

    if not findings:
        lines.append("No reviewed parameter checks were applicable to the current selection.")
        lines.append("Safety note: read-only; no model data modified.")
        return "\n".join(lines)

    missing_any = False
    for label, applicable, missing, unavailable in findings:
        lines.append(
            "{0}: missing/empty {1} | applicable {2} | unavailable/not applicable {3}".format(
                label, len(missing), len(applicable), len(unavailable)
            )
        )
        if missing:
            missing_any = True
            lines.append("  sample affected ElementIds: {0}".format(_sample_id_text(missing)))
    if not missing_any:
        lines.append("Nothing is missing from the reviewed baseline parameter set for the current selection.")
    lines.append("Safety note: read-only; no model data modified.")
    return "\n".join(lines)


def _split_pipe_dry_run_bucket():
    return {"count": 0, "ids": [], "categories": {}}


def _split_pipe_dry_run_add_bucket(groups, key, elem, limit=PIPE_SPLIT_DRY_RUN_SAMPLE_LIMIT):
    bucket = groups.setdefault(key, _split_pipe_dry_run_bucket())
    bucket["count"] += 1
    elem_id = _safe_int_id(elem)
    if elem_id is not None and elem_id not in bucket["ids"] and len(bucket["ids"]) < limit:
        bucket["ids"].append(elem_id)
    category = _category_name(elem)
    if category:
        bucket["categories"][category] = bucket["categories"].get(category, 0) + 1


def _split_pipe_dry_run_bucket_line(label, bucket):
    bucket = bucket or _split_pipe_dry_run_bucket()
    parts = ["- {0}: {1}".format(label, bucket.get("count", 0))]
    ids = bucket.get("ids") or []
    if ids:
        parts.append("sample ids: {0}".format(", ".join([safe_str(item) for item in ids[:PIPE_SPLIT_DRY_RUN_SAMPLE_LIMIT]])))
    categories = bucket.get("categories") or {}
    if categories:
        category_text = ", ".join(["{0}={1}".format(key, value) for key, value in sorted(categories.items())[:5]])
        parts.append("sample categories: {0}".format(category_text))
    return " | ".join(parts)


def _split_pipe_dry_run_is_pipe(elem):
    try:
        return elem.Category.Id.IntegerValue == int(DB.BuiltInCategory.OST_PipeCurves)
    except:
        return _category_name(elem) == "Pipes"


def _split_pipe_dry_run_is_line_curve(curve):
    try:
        if isinstance(curve, DB.Line):
            return True
    except:
        pass
    try:
        return curve.GetType().Name == "Line"
    except:
        return False


def _split_pipe_dry_run_endpoints(curve):
    try:
        return curve.GetEndPoint(0), curve.GetEndPoint(1)
    except:
        return None, None


def _split_pipe_dry_run_length_from_endpoints(p0, p1):
    try:
        dx = float(p1.X - p0.X)
        dy = float(p1.Y - p0.Y)
        dz = float(p1.Z - p0.Z)
        return math.sqrt(dx * dx + dy * dy + dz * dz)
    except:
        return None


def _split_pipe_dry_run_is_near_vertical(p0, p1):
    length = _split_pipe_dry_run_length_from_endpoints(p0, p1)
    if not length:
        return False
    try:
        return abs(float(p1.Z - p0.Z) / length) > PIPE_SPLIT_DRY_RUN_VERTICAL_Z_RATIO
    except:
        return False


def _split_pipe_dry_run_midpoint(p0, p1):
    try:
        return DB.XYZ((p0.X + p1.X) / 2.0, (p0.Y + p1.Y) / 2.0, (p0.Z + p1.Z) / 2.0)
    except:
        return None


def _split_pipe_dry_run_length_text(length_ft):
    if length_ft is None:
        return "(unreadable)"
    try:
        return "{0:.3f} ft ({1:.0f} mm)".format(float(length_ft), internal_feet_to_mm(length_ft))
    except:
        return "{0:.3f} ft".format(float(length_ft))


def _split_pipe_dry_run_xyz_text(point):
    if point is None:
        return "(unreadable)"
    try:
        ft_text = "internal ft: ({0:.6f}, {1:.6f}, {2:.6f})".format(point.X, point.Y, point.Z)
    except:
        return "(unreadable)"
    try:
        mm_text = "approx mm: ({0:.0f}, {1:.0f}, {2:.0f})".format(
            internal_feet_to_mm(point.X),
            internal_feet_to_mm(point.Y),
            internal_feet_to_mm(point.Z),
        )
        return "{0}; {1}".format(ft_text, mm_text)
    except:
        return ft_text


def _split_pipe_dry_run_safe_curve(elem):
    try:
        location = elem.Location
    except:
        return None, "unreadable_location_curve"
    try:
        if not isinstance(location, DB.LocationCurve):
            return None, "unreadable_location_curve"
    except:
        if not hasattr(location, "Curve"):
            return None, "unreadable_location_curve"
    try:
        curve = location.Curve
    except:
        return None, "unreadable_location_curve"
    if curve is None:
        return None, "unreadable_location_curve"
    try:
        if hasattr(curve, "IsBound") and not curve.IsBound:
            return curve, "unbounded_curve"
    except:
        pass
    p0, p1 = _split_pipe_dry_run_endpoints(curve)
    if p0 is None or p1 is None:
        return curve, "unreadable_endpoints"
    if not _split_pipe_dry_run_is_line_curve(curve):
        return curve, "unsupported_curve_type"
    return curve, "ok"


def _split_pipe_dry_run_candidate(doc, elem, curve):
    p0, p1 = _split_pipe_dry_run_endpoints(curve)
    try:
        length = float(curve.Length)
    except:
        length = _split_pipe_dry_run_length_from_endpoints(p0, p1)
    return {
        "element": elem,
        "id": _safe_int_id(elem),
        "type": _family_and_type_text(doc, elem) or "(unreadable)",
        "level": _element_level_name(doc, elem) or "(unreadable)",
        "system": _system_assignment_value(elem) or "(unreadable)",
        "diameter_size": _safe_param_text(elem, ["Diameter", "Size", "Nominal Diameter", "Outside Diameter"]) or "(unreadable)",
        "slope": _safe_param_text(elem, ["Slope"]) or "(unreadable)",
        "length": length,
        "point": _split_pipe_dry_run_midpoint(p0, p1),
        "segment_a": length / 2.0 if length is not None else None,
        "segment_b": length / 2.0 if length is not None else None,
    }


def _split_pipe_dry_run_prompt_has_interval_request(prompt_text):
    normalized = safe_str(prompt_text).lower()
    if " every " in normalized or " interval" in normalized:
        return True
    return bool(re.search(r"\b\d+(?:\.\d+)?\s*(mm|m|meter|meters)\b", normalized))


def split_selected_pipes_dry_run(doc, uidoc, context=None):
    prompt_text = _prompt_text_from_context(context)
    selected = _selected_elements(doc, uidoc)
    total_selected = len(selected)
    inspected_elements = selected[:PIPE_SPLIT_DRY_RUN_MAX_SELECTION]
    capped = len(selected) > PIPE_SPLIT_DRY_RUN_MAX_SELECTION
    skipped = {}
    warnings = {}
    candidates = []
    total_original_length = 0.0

    for elem in inspected_elements:
        try:
            category = _category_name(elem)
            if not _split_pipe_dry_run_is_pipe(elem):
                if category == "Pipe Fittings":
                    _split_pipe_dry_run_add_bucket(skipped, "pipe_fittings", elem)
                elif category == "Pipe Accessories":
                    _split_pipe_dry_run_add_bucket(skipped, "pipe_accessories", elem)
                else:
                    _split_pipe_dry_run_add_bucket(skipped, "non_pipe_categories", elem)
                continue
            try:
                if bool(elem.Pinned):
                    _split_pipe_dry_run_add_bucket(skipped, "pinned_pipes", elem)
                    continue
            except:
                pass
            try:
                group_id = getattr(elem, "GroupId", None)
                if group_id is not None and group_id != DB.ElementId.InvalidElementId:
                    _split_pipe_dry_run_add_bucket(skipped, "grouped_pipes", elem)
                    continue
            except:
                pass
            try:
                design_option = getattr(elem, "DesignOption", None)
                if design_option is not None:
                    _split_pipe_dry_run_add_bucket(skipped, "design_option_pipes", elem)
                    continue
            except:
                pass
            curve, status = _split_pipe_dry_run_safe_curve(elem)
            if status != "ok":
                _split_pipe_dry_run_add_bucket(skipped, status, elem)
                continue
            p0, p1 = _split_pipe_dry_run_endpoints(curve)
            if _split_pipe_dry_run_is_near_vertical(p0, p1):
                _split_pipe_dry_run_add_bucket(skipped, "near_vertical_pipes", elem)
                continue
            try:
                length = float(curve.Length)
            except:
                length = _split_pipe_dry_run_length_from_endpoints(p0, p1)
            if length is None:
                _split_pipe_dry_run_add_bucket(skipped, "unreadable_location_curve", elem)
                continue
            if length < PIPE_SPLIT_DRY_RUN_MIN_TOTAL_LENGTH_FT or (length / 2.0) < PIPE_SPLIT_DRY_RUN_MIN_SEGMENT_LENGTH_FT:
                _split_pipe_dry_run_add_bucket(skipped, "too_short_pipes", elem)
                continue
            candidate = _split_pipe_dry_run_candidate(doc, elem, curve)
            candidates.append(candidate)
            total_original_length += length
            if candidate.get("level") == "(unreadable)":
                _split_pipe_dry_run_add_bucket(warnings, "missing_unreadable_level", elem)
            if candidate.get("system") == "(unreadable)":
                _split_pipe_dry_run_add_bucket(warnings, "missing_unreadable_system", elem)
            if candidate.get("diameter_size") == "(unreadable)":
                _split_pipe_dry_run_add_bucket(warnings, "missing_unreadable_diameter_size", elem)
            if candidate.get("slope") == "(unreadable)":
                _split_pipe_dry_run_add_bucket(warnings, "missing_unreadable_slope", elem)
        except:
            _split_pipe_dry_run_add_bucket(skipped, "calculation_errors", elem)

    skipped_count = sum([bucket.get("count", 0) for bucket in skipped.values()])
    warning_count = sum([bucket.get("count", 0) for bucket in warnings.values()])
    try:
        active_view_type = safe_str(uidoc.ActiveView.ViewType)
    except:
        active_view_type = "(unknown type)"

    lines = [
        "[SPLIT SELECTED PIPES DRY RUN]",
        "",
        "Scope:",
        "selected elements / active document only",
        "",
        "Active document:",
        _document_title(doc),
        "",
        "Active view:",
        "{0} [{1}]".format(_active_view_title(doc, uidoc), active_view_type),
        "",
    ]
    if total_selected == 0:
        lines.extend(
            [
                "Total selected elements: 0",
                "",
                "Dry-run result:",
                "Not ready",
                "",
                "No elements selected. Select pipes before running split dry-run.",
                "",
                "Safety:",
                "- Dry-run only.",
                "- Read-only.",
                "- No transaction opened.",
                "- No pipe was split.",
                "- Revit model data was not modified.",
            ]
        )
        return "\n".join(lines)

    lines.extend(
        [
            "Candidate strategy:",
            "Midpoint candidate only. One proposed split point per eligible straight pipe.",
            "",
            "Selection summary:",
            "- Total selected elements: {0}".format(total_selected),
            "- Elements inspected: {0}".format(len(inspected_elements)),
            "- Eligible pipes: {0}".format(len(candidates)),
            "- Candidate split points: {0}".format(len(candidates)),
            "- Skipped elements: {0}".format(skipped_count),
            "- Warnings: {0}".format(warning_count),
            "",
            "Candidate split summary:",
            "- Total original pipe length: {0}".format(_split_pipe_dry_run_length_text(total_original_length if candidates else 0.0)),
            "- Estimated segment count if later applied: {0}".format(len(candidates) * 2),
            "- Minimum segment length rule: total >= {0}; each estimated segment >= {1}".format(
                _split_pipe_dry_run_length_text(PIPE_SPLIT_DRY_RUN_MIN_TOTAL_LENGTH_FT),
                _split_pipe_dry_run_length_text(PIPE_SPLIT_DRY_RUN_MIN_SEGMENT_LENGTH_FT),
            ),
            "- Candidate points generated: {0}".format(len(candidates)),
            "",
            "Candidate pipes:",
        ]
    )
    if not candidates:
        lines.append("- none")
    else:
        for index, candidate in enumerate(candidates[:PIPE_SPLIT_DRY_RUN_DETAIL_LIMIT]):
            lines.extend(
                [
                    "{0}. Pipe {1}".format(index + 1, candidate.get("id")),
                    "   - Type: {0}".format(candidate.get("type")),
                    "   - Level: {0}".format(candidate.get("level")),
                    "   - System: {0}".format(candidate.get("system")),
                    "   - Diameter/Size: {0}".format(candidate.get("diameter_size")),
                    "   - Slope: {0}".format(candidate.get("slope")),
                    "   - Original length: {0}".format(_split_pipe_dry_run_length_text(candidate.get("length"))),
                    "   - Candidate split point: {0}".format(_split_pipe_dry_run_xyz_text(candidate.get("point"))),
                    "   - Estimated segment A: {0}".format(_split_pipe_dry_run_length_text(candidate.get("segment_a"))),
                    "   - Estimated segment B: {0}".format(_split_pipe_dry_run_length_text(candidate.get("segment_b"))),
                    "   - Status: dry-run only / not applied",
                ]
            )
        if len(candidates) > PIPE_SPLIT_DRY_RUN_DETAIL_LIMIT:
            lines.append("...showing first {0} of {1} eligible pipes".format(PIPE_SPLIT_DRY_RUN_DETAIL_LIMIT, len(candidates)))

    lines.extend(
        [
            "",
            "Skipped elements:",
            _split_pipe_dry_run_bucket_line("Pipe fittings", skipped.get("pipe_fittings")),
            _split_pipe_dry_run_bucket_line("Pipe accessories", skipped.get("pipe_accessories")),
            _split_pipe_dry_run_bucket_line("Non-pipe categories", skipped.get("non_pipe_categories")),
            _split_pipe_dry_run_bucket_line("Pinned pipes", skipped.get("pinned_pipes")),
            _split_pipe_dry_run_bucket_line("Grouped pipes", skipped.get("grouped_pipes")),
            _split_pipe_dry_run_bucket_line("Design-option pipes", skipped.get("design_option_pipes")),
            _split_pipe_dry_run_bucket_line("Near-vertical pipes", skipped.get("near_vertical_pipes")),
            _split_pipe_dry_run_bucket_line("Too-short pipes", skipped.get("too_short_pipes")),
            _split_pipe_dry_run_bucket_line("Unreadable location curve", skipped.get("unreadable_location_curve")),
            _split_pipe_dry_run_bucket_line("Unbounded curve", skipped.get("unbounded_curve")),
            _split_pipe_dry_run_bucket_line("Unsupported curve type", skipped.get("unsupported_curve_type")),
            _split_pipe_dry_run_bucket_line("Calculation errors", skipped.get("calculation_errors")),
            "",
            "Preflight warnings:",
            _split_pipe_dry_run_bucket_line("Missing/unreadable level", warnings.get("missing_unreadable_level")),
            _split_pipe_dry_run_bucket_line("Missing/unreadable system", warnings.get("missing_unreadable_system")),
            _split_pipe_dry_run_bucket_line("Missing/unreadable diameter/size", warnings.get("missing_unreadable_diameter_size")),
            _split_pipe_dry_run_bucket_line("Missing/unreadable slope", warnings.get("missing_unreadable_slope")),
            "",
            "Risks:",
            "- Future pipe splitting can affect connected fittings, slopes, systems, tags, dimensions, schedules, and downstream coordination.",
            "- MEP-WR-001 does not inspect connectors or connected networks.",
            "- MEP-WR-001 does not apply changes.",
            "- Future reviewed apply must use explicit confirmation and rollback.",
            "",
            "Dry-run result:",
            "Ready for reviewed apply design" if candidates else "Not ready",
        ]
    )
    if not candidates:
        lines.extend(["", "Reason:", "No selected pipes passed the dry-run eligibility checks."])
    if _split_pipe_dry_run_prompt_has_interval_request(prompt_text):
        lines.extend(["", "Note:", "Interval-based splitting is not implemented in MEP-WR-001. Current dry-run uses midpoint candidate only."])
    if capped:
        lines.extend(["", "Note:", "Result set capped for readability/performance."])
    lines.extend(
        [
            "",
            "Next step:",
            "Future feature MEP-WR-002 should implement reviewed apply with explicit confirmation, transaction rollback, before/after evidence, and conservative skip handling.",
            "",
            "Safety:",
            "- Dry-run only.",
            "- Read-only.",
            "- No transaction opened.",
            "- No pipe was split.",
            "- Revit model data was not modified.",
        ]
    )
    return "\n".join(lines)


def split_selected_pipes_rollback_test(doc, uidoc, context=None):
    try:
        active_view_type = safe_str(uidoc.ActiveView.ViewType)
    except:
        active_view_type = "(unknown type)"
    return "\n".join(
        [
            "[SPLIT SELECTED PIPES ROLLBACK TEST]",
            "",
            "Rollback Test ID:",
            "MEP-WR-002-{0}".format(time.strftime("%Y%m%d_%H%M%S")),
            "",
            "Scope:",
            "latest split dry-run candidates / active document only",
            "",
            "Active document:",
            _document_title(doc),
            "",
            "Active view:",
            "{0} [{1}]".format(_active_view_title(doc, uidoc), active_view_type),
            "",
            "Source dry-run:",
            "- Source status: unavailable",
            "",
            "Execution status:",
            "- Rollback test requested: true",
            "- Confirmation token present: {0}".format("true" if _has_rollback_test_token(_prompt_text_from_context(context)) else "false"),
            "- Execution available: false",
            "- Transaction opened: false",
            "- BreakCurve called: false",
            "- Transaction group rolled back: false",
            "- Persistent model changes: false",
            "",
            "Rollback-test result:",
            "Not ready",
            "",
            "Reason:",
            "No valid split selected pipes dry-run is available in this stateless command path.",
            "",
            "Required previous step:",
            "dry run split selected pipes",
            "",
            "Safety:",
            "- Rollback-test guard only.",
            "- No transaction opened.",
            "- BreakCurve was not called.",
            "- Revit model data was not modified.",
        ]
    )


def split_selected_pipe_reviewed_apply(doc, uidoc, context=None):
    try:
        active_view_type = safe_str(uidoc.ActiveView.ViewType)
    except:
        active_view_type = "(unknown type)"
    return "\n".join(
        [
            "[SPLIT SELECTED PIPE REVIEWED APPLY]",
            "",
            "Reviewed Apply ID:",
            "MEP-WR-003-{0}".format(time.strftime("%Y%m%d_%H%M%S")),
            "",
            "Request:",
            "persistent apply requested",
            "",
            "Scope:",
            "single split candidate / active document only",
            "",
            "Active document:",
            _document_title(doc),
            "",
            "Active view:",
            "{0} [{1}]".format(_active_view_title(doc, uidoc), active_view_type),
            "",
            "Reviewed apply result:",
            "Not ready",
            "",
            "Reason:",
            "No valid split dry-run and rollback-test state is available in this stateless command path.",
            "",
            "Required previous steps:",
            "1. dry run split selected pipes",
            "2. run split rollback test ROLLBACK-TEST-OK",
            "",
            "Safety:",
            "- No transaction opened.",
            "- No pipe was split.",
            "- Revit model data was not modified.",
        ]
    )


def split_apply_verification_report(doc, uidoc, context=None):
    try:
        active_view_type = safe_str(uidoc.ActiveView.ViewType)
    except:
        active_view_type = "(unknown type)"
    return "\n".join(
        [
            "[SPLIT APPLY VERIFICATION REPORT]",
            "",
            "Verification ID:",
            "MEP-WR-004-{0}".format(time.strftime("%Y%m%d_%H%M%S")),
            "",
            "Scope:",
            "latest reviewed split apply result / active document only",
            "",
            "Active document:",
            _document_title(doc),
            "",
            "Active view:",
            "{0} [{1}]".format(_active_view_title(doc, uidoc), active_view_type),
            "",
            "Source reviewed apply:",
            "- Source status: unavailable",
            "",
            "Execution status:",
            "- Verification requested: true",
            "- Transaction opened: false",
            "- BreakCurve called: false",
            "- Model modified: false",
            "",
            "Verification result:",
            "Not ready",
            "",
            "Reason:",
            "No latest MEP-WR-003 reviewed apply state is available in this stateless command path.",
            "",
            "Safety:",
            "- Read-only verification only.",
            "- No transaction opened.",
            "- No pipe was split.",
            "- Revit model data was not modified.",
        ]
    )


def split_selected_pipes(doc, uidoc, context=None):
    prompt_text = _prompt_text_from_context(context)
    selected_elements = _selected_elements(doc, uidoc)
    selected_pipes = _selected_elements_by_categories(doc, uidoc, [DB.BuiltInCategory.OST_PipeCurves])
    skipped_non_pipes = max(len(selected_elements) - len(selected_pipes), 0)
    interval_m = _parse_metric_length_meters(prompt_text, default_m=1.5)
    interval_ft = _meters_to_feet(interval_m)
    if not selected_elements:
        lines = [
            "Split selected pipes",
        ]
        lines.extend(_selection_scope_preamble(doc, uidoc, 0))
        lines.append("No selected elements found in the active Revit document.")
        return "\n".join(lines)
    if not selected_pipes:
        lines = [
            "Split selected pipes",
        ]
        lines.extend(_selection_scope_preamble(doc, uidoc, len(selected_elements)))
        lines.append("No selected pipes were found. Non-pipe elements were ignored.")
        return "\n".join(lines)

    try:
        from Autodesk.Revit.DB.Plumbing import PlumbingUtils
    except:
        from Autodesk.Revit.DB import PlumbingUtils

    transaction = DB.Transaction(doc, "AI Split Selected Pipes")
    transaction.Start()
    created_segment_ids = []
    changed_pipes = 0
    skipped_ids = []
    try:
        for pipe in selected_pipes:
            current_pipe = pipe
            splits_for_pipe = 0
            while True:
                location_curve = getattr(getattr(current_pipe, "Location", None), "Curve", None)
                if location_curve is None:
                    skipped_ids.append(_safe_int_id(current_pipe))
                    break
                try:
                    current_length_ft = float(location_curve.Length)
                except:
                    skipped_ids.append(_safe_int_id(current_pipe))
                    break
                if current_length_ft <= interval_ft + 0.01:
                    break
                try:
                    split_point = location_curve.Evaluate(interval_ft / current_length_ft, True)
                    new_pipe_id = PlumbingUtils.BreakCurve(doc, current_pipe.Id, split_point)
                    if new_pipe_id is None or new_pipe_id == DB.ElementId.InvalidElementId:
                        skipped_ids.append(_safe_int_id(current_pipe))
                        break
                    created_segment_ids.append(new_pipe_id.IntegerValue)
                    changed_pipes += 1
                    splits_for_pipe += 1
                    current_pipe = doc.GetElement(new_pipe_id)
                    if current_pipe is None:
                        break
                except:
                    skipped_ids.append(_safe_int_id(current_pipe))
                    break
            if splits_for_pipe == 0 and current_pipe is pipe and _safe_int_id(pipe) not in skipped_ids:
                pass
        transaction.Commit()
    except Exception as exc:
        try:
            transaction.RollBack()
        except:
            pass
        return "Failed to split selected pipes: {0}".format(str(exc))

    lines = ["Split selected pipes"]
    lines.extend(_selection_scope_preamble(doc, uidoc, len(selected_elements)))
    lines.append("Selected pipe count: {0}".format(len(selected_pipes)))
    lines.append("Split rule used: max segment length {0:.3f} m".format(interval_m))
    lines.append("New segments created: {0}".format(len(created_segment_ids)))
    lines.append("Changed pipe operations: {0}".format(changed_pipes))
    if skipped_non_pipes:
        lines.append("Skipped non-pipe selected elements: {0}".format(skipped_non_pipes))
    if skipped_ids:
        lines.append("Skipped pipes: {0}".format(", ".join([str(item) for item in skipped_ids[:10]])))
        if len(skipped_ids) > 10:
            lines.append("...showing first 10 of {0} skipped pipe ids".format(len(skipped_ids)))
    lines.append("Undo unavailable: split-pipe rollback is not safely implemented in this pass.")
    return "\n".join(lines)


def report_duplicates(doc, uidoc, context=None):
    prompt_text = _prompt_text_from_context(context)
    scope_label, elements = _duplicate_scope_elements(doc, uidoc, prompt_text)
    duplicates, skipped = _find_duplicate_groups(elements)
    lines = [
        "Report duplicates",
        "Exact duplicate rule: same category, same type, and same point/curve location signature in the current scope.",
        "Active document: {0}".format(_document_title(doc)),
        "Active view: {0}".format(_active_view_title(doc, uidoc)),
        "Scope used: {0}".format(scope_label),
        "Elements inspected: {0}".format(len(elements)),
        "Duplicate groups found: {0}".format(len(duplicates)),
    ]
    for index, group in enumerate(duplicates[:20]):
        keep_elem = group[0]
        remove_elems = group[1:]
        lines.append(
            "Group {0}: {1} x {2} | keep id {3} | remove ids {4}".format(
                index + 1,
                _category_name(keep_elem),
                len(group),
                _safe_int_id(keep_elem),
                ", ".join([str(_safe_int_id(elem)) for elem in remove_elems[:5]]),
            )
        )
    if len(duplicates) > 20:
        lines.append("...showing first 20 of {0} duplicate groups".format(len(duplicates)))
    if skipped:
        lines.append("Skipped elements without reliable location signature: {0}".format(len(skipped)))
    if not duplicates:
        lines.append("No duplicates were detected in the reviewed scope.")
    return "\n".join(lines)


def remove_duplicates(doc, uidoc, context=None):
    prompt_text = _prompt_text_from_context(context)
    scope_label, elements = _duplicate_scope_elements(doc, uidoc, prompt_text)
    duplicates, skipped = _find_duplicate_groups(elements)
    remove_ids = []
    kept_ids = []
    for group in duplicates:
        kept_ids.append(_safe_int_id(group[0]))
        for elem in group[1:]:
            remove_ids.append(elem.Id)
    lines = [
        "Remove duplicates",
        "Exact duplicate rule: same category, same type, and same point/curve location signature in the current scope.",
        "Active document: {0}".format(_document_title(doc)),
        "Active view: {0}".format(_active_view_title(doc, uidoc)),
        "Scope used: {0}".format(scope_label),
        "Duplicate groups found: {0}".format(len(duplicates)),
        "Elements kept: {0}".format(len(kept_ids)),
        "Elements removed: {0}".format(len(remove_ids)),
    ]
    if not remove_ids:
        if skipped:
            lines.append("Skipped elements without reliable location signature: {0}".format(len(skipped)))
        lines.append("No duplicates were removed because no duplicate groups were detected.")
        return "\n".join(lines)

    transaction = DB.Transaction(doc, "AI Remove Duplicates")
    transaction.Start()
    try:
        doc.Delete(System.Collections.Generic.List[DB.ElementId](remove_ids))
        transaction.Commit()
    except Exception as exc:
        try:
            transaction.RollBack()
        except:
            pass
        return "Failed to remove duplicates: {0}".format(str(exc))

    lines.append("Kept sample ids: {0}".format(", ".join([str(item) for item in kept_ids[:10]])))
    lines.append(
        "Removed sample ids: {0}".format(
            ", ".join([str(item.IntegerValue) for item in remove_ids[:10]])
        )
    )
    if skipped:
        lines.append("Skipped elements without reliable location signature: {0}".format(len(skipped)))
    lines.append("Undo unavailable: duplicate-removal rollback is not safely implemented in this pass.")
    return "\n".join(lines)


def categories_list_and_id(doc, uidoc, context=None):
    prompt_text = _prompt_text_from_context(context).lower()
    categories = []
    if "selection" in prompt_text:
        seen = {}
        for elem in _selected_elements(doc, uidoc):
            category = getattr(elem, "Category", None)
            if category is None:
                continue
            seen[category.Id.IntegerValue] = category
        categories = list(seen.values())
        scope_label = "selection categories in active document"
    elif "active view" in prompt_text:
        seen = {}
        for elem in DB.FilteredElementCollector(doc, uidoc.ActiveView.Id).WhereElementIsNotElementType().ToElements():
            category = getattr(elem, "Category", None)
            if category is None:
                continue
            seen[category.Id.IntegerValue] = category
        categories = list(seen.values())
        scope_label = "categories present in active view"
    else:
        categories = _all_doc_categories(doc)
        scope_label = "all categories in active document"

    categories = sorted(categories, key=lambda category: getattr(category, "Name", ""))
    lines = [
        "Categories list + ID",
        "Active document: {0}".format(_document_title(doc)),
        "Active view: {0}".format(_active_view_title(doc, uidoc)),
        "Scope used: {0}".format(scope_label),
        "Categories found: {0}".format(len(categories)),
    ]
    for category in categories[:50]:
        try:
            lines.append("{0}: {1}".format(category.Name, category.Id.IntegerValue))
        except:
            continue
    if len(categories) > 50:
        lines.append("...showing first 50 of {0}".format(len(categories)))
    return "\n".join(lines)


def select_all_elements_of_category(doc, uidoc, context=None):
    from System.Collections.Generic import List

    prompt_text = _prompt_text_from_context(context)
    categories, issue = _resolve_categories_from_prompt(doc, prompt_text)
    if issue:
        return issue
    elements = []
    for category in categories:
        elements.extend(_elements_of_category(doc, uidoc, category, active_view_only=False))
    uidoc.Selection.SetElementIds(List[DB.ElementId]([elem.Id for elem in elements]))
    lines = ["Select all elements of category"]
    lines.extend(_selection_scope_preamble(doc, uidoc, len(elements)))
    lines.append("Categories: {0}".format(", ".join([category.Name for category in categories])))
    lines.append("Selected elements: {0}".format(len(elements)))
    return "\n".join(lines)


def count_all_elements_of_category(doc, uidoc, context=None):
    prompt_text = _prompt_text_from_context(context)
    categories, issue = _resolve_categories_from_prompt(doc, prompt_text)
    if issue:
        return issue
    elements = []
    for category in categories:
        elements.extend(_elements_of_category(doc, uidoc, category, active_view_only=False))
    lines = [
        "Count all elements of category",
        "Active document: {0}".format(_document_title(doc)),
        "Categories: {0}".format(", ".join([category.Name for category in categories])),
        "Total elements: {0}".format(len(elements)),
    ]
    return "\n".join(lines)


def list_all_elements_of_category(doc, uidoc, context=None):
    prompt_text = _prompt_text_from_context(context)
    categories, issue = _resolve_categories_from_prompt(doc, prompt_text)
    if issue:
        return issue
    elements = []
    for category in categories:
        elements.extend(_elements_of_category(doc, uidoc, category, active_view_only=False))
    lines = [
        "List all elements of category",
        "Active document: {0}".format(_document_title(doc)),
        "Categories: {0}".format(", ".join([category.Name for category in categories])),
        "Total elements: {0}".format(len(elements)),
    ]
    for elem in elements[:20]:
        lines.append(
            "Id: {0}, Type: {1}".format(
                _safe_int_id(elem),
                _family_and_type_text(doc, elem) or "(no type)",
            )
        )
    if len(elements) > 20:
        lines.append("...showing first 20 of {0}".format(len(elements)))
    return "\n".join(lines)


def report_rooms_without_matching_spaces(doc, uidoc, context=None):
    room_map, space_map = _room_space_maps(doc)
    missing = []
    for number, rooms in room_map.items():
        if number not in space_map:
            missing.extend(rooms)
    lines = [
        "Report rooms without matching spaces",
        "Active document: {0}".format(_document_title(doc)),
        "Matching rule: room Number must match space Number in the active document.",
        "Rooms without matching spaces: {0}".format(len(missing)),
    ]
    for room in missing[:20]:
        lines.append(
            "{0} | {1} | id {2}".format(
                _room_space_number(room),
                _room_space_name(room),
                _safe_int_id(room),
            )
        )
    if len(missing) > 20:
        lines.append("...showing first 20 of {0}".format(len(missing)))
    return "\n".join(lines)


def report_spaces_without_matching_rooms(doc, uidoc, context=None):
    room_map, space_map = _room_space_maps(doc)
    missing = []
    for number, spaces in space_map.items():
        if number not in room_map:
            missing.extend(spaces)
    lines = [
        "Report spaces without matching rooms",
        "Active document: {0}".format(_document_title(doc)),
        "Matching rule: space Number must match room Number in the active document.",
        "Spaces without matching rooms: {0}".format(len(missing)),
    ]
    for space in missing[:20]:
        lines.append(
            "{0} | {1} | id {2}".format(
                _room_space_number(space),
                _room_space_name(space),
                _safe_int_id(space),
            )
        )
    if len(missing) > 20:
        lines.append("...showing first 20 of {0}".format(len(missing)))
    return "\n".join(lines)


def report_room_space_mismatches(doc, uidoc, context=None):
    room_map, space_map = _room_space_maps(doc)
    mismatches = []
    for number in sorted(set(room_map.keys()).intersection(set(space_map.keys()))):
        room_names = sorted(set([_room_space_name(item) for item in room_map[number]]))
        space_names = sorted(set([_room_space_name(item) for item in space_map[number]]))
        if room_names != space_names:
            mismatches.append((number, room_map[number], space_map[number], room_names, space_names))
    lines = [
        "Report room/space mismatches",
        "Active document: {0}".format(_document_title(doc)),
        "Matching rule: room Number must match space Number; mismatch is reported when the paired names differ.",
        "Room/space mismatches: {0}".format(len(mismatches)),
    ]
    for number, rooms, spaces, room_names, space_names in mismatches[:20]:
        lines.append(
            "Number {0} | rooms: {1} | spaces: {2} | sample room ids: {3} | sample space ids: {4}".format(
                number,
                ", ".join(room_names[:3]),
                ", ".join(space_names[:3]),
                _sample_id_text(rooms),
                _sample_id_text(spaces),
            )
        )
    if len(mismatches) > 20:
        lines.append("...showing first 20 of {0}".format(len(mismatches)))
    if not mismatches:
        lines.append("No room/space mismatches were detected with the reviewed number-plus-name rule.")
    return "\n".join(lines)


def rename_active_view(doc, uidoc, context=None):
    prompt_text = _prompt_text_from_context(context)
    active_view = uidoc.ActiveView
    explicit_match = re.search(r"rename active view to\s+(.+)$", prompt_text, re.IGNORECASE)
    if explicit_match:
        base_name = explicit_match.group(1).strip()
    else:
        view_type = ""
        try:
            view_type = active_view.ViewType.ToString()
        except:
            view_type = "View"
        base_name = "AI - {0} - {1}".format(view_type, get_elem_name(active_view))
    existing_names = _all_view_names(doc)
    old_name = get_elem_name(active_view) or "(unnamed view)"
    new_name = _unique_name(base_name, existing_names.difference(set([old_name])))
    transaction = DB.Transaction(doc, "AI Rename Active View")
    transaction.Start()
    try:
        active_view.Name = new_name
        transaction.Commit()
    except Exception as exc:
        try:
            transaction.RollBack()
        except:
            pass
        return "Failed to rename active view: {0}".format(str(exc))
    return {
        "message": "Renamed active view from '{0}' to '{1}'.".format(old_name, new_name),
        "undo_context": {
            "action_id": "rename-active-view",
            "action_title": "Rename active view",
            "role": "modifying",
            "document_identity": _document_identity(doc),
            "view_id": active_view.Id.IntegerValue,
            "old_view_name": old_name,
            "new_view_name": new_name,
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "session_marker": "reviewed-current-session",
            "undo_available": True,
        },
    }


def align_selected_tags(doc, uidoc, context=None):
    prompt_text = _prompt_text_from_context(context).lower()
    mode = "vertical" if "vertical" in prompt_text else "horizontal"
    selected = _selected_elements(doc, uidoc)
    tags = []
    skipped = 0
    for elem in selected:
        if isinstance(elem, DB.IndependentTag):
            tags.append(elem)
        else:
            skipped += 1
    if len(tags) < 2:
        return "Select at least two tags in the active view to align them."
    anchor = tags[0]
    anchor_point = anchor.TagHeadPosition
    transaction = DB.Transaction(doc, "AI Align Selected Tags")
    transaction.Start()
    moved = 0
    try:
        for tag in tags[1:]:
            point = tag.TagHeadPosition
            if mode == "vertical":
                tag.TagHeadPosition = DB.XYZ(anchor_point.X, point.Y, point.Z)
            else:
                tag.TagHeadPosition = DB.XYZ(point.X, anchor_point.Y, point.Z)
            moved += 1
        transaction.Commit()
    except Exception as exc:
        try:
            transaction.RollBack()
        except:
            pass
        return "Failed to align selected tags: {0}".format(str(exc))
    lines = [
        "Align selected tags",
        "Active document: {0}".format(_document_title(doc)),
        "Active view: {0}".format(_active_view_title(doc, uidoc)),
        "Alignment mode: {0}".format(mode),
        "Tags aligned: {0}".format(moved + 1),
    ]
    if skipped:
        lines.append("Skipped non-tag selected elements: {0}".format(skipped))
    lines.append("Undo unavailable: tag-alignment rollback is not safely implemented in this pass.")
    return "\n".join(lines)


def report_total_length_selected_linear_mep(doc, uidoc, context=None):
    categories = [
        DB.BuiltInCategory.OST_DuctCurves,
        DB.BuiltInCategory.OST_PipeCurves,
        DB.BuiltInCategory.OST_CableTray,
        DB.BuiltInCategory.OST_Conduit,
    ]
    elems = _selected_elements_by_categories(doc, uidoc, categories)
    total_m = 0.0
    for elem in elems:
        length_ft = _lookup_param_double(elem, ["Length", "Centerline Length"]) or 0.0
        total_m += _feet_to_meters(length_ft)
    lines = ["Report total length of selected linear MEP elements"]
    lines.extend(_selection_scope_preamble(doc, uidoc, len(_selected_elements(doc, uidoc))))
    lines.append("Supported linear MEP elements found: {0}".format(len(elems)))
    lines.append("Total length: {0:.2f} m".format(total_m))
    return "\n".join(lines)


def report_total_length_active_view_linear_mep(doc, uidoc, context=None):
    categories = [
        DB.BuiltInCategory.OST_DuctCurves,
        DB.BuiltInCategory.OST_PipeCurves,
        DB.BuiltInCategory.OST_CableTray,
        DB.BuiltInCategory.OST_Conduit,
    ]
    elems = []
    for category in categories:
        elems.extend(
            list(
                DB.FilteredElementCollector(doc, uidoc.ActiveView.Id)
                .OfCategory(category)
                .WhereElementIsNotElementType()
                .ToElements()
            )
        )
    total_m = 0.0
    for elem in elems:
        length_ft = _lookup_param_double(elem, ["Length", "Centerline Length"]) or 0.0
        total_m += _feet_to_meters(length_ft)
    return "\n".join(
        [
            "Report total length in active view for supported linear MEP categories",
            "Active document: {0}".format(_document_title(doc)),
            "Active view: {0}".format(_active_view_title(doc, uidoc)),
            "Supported linear MEP elements in active view: {0}".format(len(elems)),
            "Total length: {0:.2f} m".format(total_m),
        ]
    )


def _bip(name):
    try:
        return getattr(DB.BuiltInParameter, name)
    except:
        return None


def _schedule_action_configs():
    return {
        "pipe": {
            "key": "pipe",
            "title": "Pipe",
            "discipline": "Piping",
            "category": DB.BuiltInCategory.OST_PipeCurves,
            "keywords": ["pipe", "pipes"],
            "level_bips": [
                "RBS_START_LEVEL_PARAM",
                "INSTANCE_REFERENCE_LEVEL_PARAM",
                "SCHEDULE_LEVEL_PARAM",
                "FAMILY_LEVEL_PARAM",
            ],
            "level_names": ["Reference Level", "Level"],
        },
        "pipe_fitting": {
            "key": "pipe_fitting",
            "title": "Pipe Fitting",
            "discipline": "Piping",
            "category": DB.BuiltInCategory.OST_PipeFitting,
            "keywords": ["pipe fitting", "pipe fittings"],
            "level_bips": [
                "FAMILY_LEVEL_PARAM",
                "INSTANCE_REFERENCE_LEVEL_PARAM",
                "RBS_REFERENCE_LEVEL_PARAM",
                "SCHEDULE_LEVEL_PARAM",
            ],
            "level_names": ["Reference Level", "Level"],
        },
        "duct": {
            "key": "duct",
            "title": "Duct",
            "discipline": "HVAC",
            "category": DB.BuiltInCategory.OST_DuctCurves,
            "keywords": ["duct", "ducts"],
            "level_bips": [
                "RBS_START_LEVEL_PARAM",
                "INSTANCE_REFERENCE_LEVEL_PARAM",
                "SCHEDULE_LEVEL_PARAM",
                "FAMILY_LEVEL_PARAM",
            ],
            "level_names": ["Reference Level", "Level"],
        },
        "duct_fitting": {
            "key": "duct_fitting",
            "title": "Duct Fitting",
            "discipline": "HVAC",
            "category": DB.BuiltInCategory.OST_DuctFitting,
            "keywords": ["duct fitting", "duct fittings"],
            "level_bips": [
                "FAMILY_LEVEL_PARAM",
                "INSTANCE_REFERENCE_LEVEL_PARAM",
                "RBS_REFERENCE_LEVEL_PARAM",
                "SCHEDULE_LEVEL_PARAM",
            ],
            "level_names": ["Reference Level", "Level"],
        },
        "conduit": {
            "key": "conduit",
            "title": "Conduit",
            "discipline": "Electrical",
            "category": DB.BuiltInCategory.OST_Conduit,
            "keywords": ["conduit", "conduits"],
            "level_bips": [
                "RBS_START_LEVEL_PARAM",
                "INSTANCE_REFERENCE_LEVEL_PARAM",
                "SCHEDULE_LEVEL_PARAM",
                "FAMILY_LEVEL_PARAM",
            ],
            "level_names": ["Reference Level", "Level"],
        },
        "electrical_fixture": {
            "key": "electrical_fixture",
            "title": "Electrical Fixture",
            "discipline": "Electrical",
            "category": DB.BuiltInCategory.OST_ElectricalFixtures,
            "keywords": ["electrical fixture", "electrical fixtures", "fixture", "fixtures"],
            "level_bips": [
                "FAMILY_LEVEL_PARAM",
                "INSTANCE_SCHEDULE_ONLY_LEVEL_PARAM",
                "INSTANCE_REFERENCE_LEVEL_PARAM",
                "SCHEDULE_LEVEL_PARAM",
            ],
            "level_names": ["Level", "Reference Level"],
        },
        "electrical_equipment": {
            "key": "electrical_equipment",
            "title": "Electrical Equipment",
            "discipline": "Electrical",
            "category": DB.BuiltInCategory.OST_ElectricalEquipment,
            "keywords": ["electrical equipment", "equipment"],
            "level_bips": [
                "FAMILY_LEVEL_PARAM",
                "INSTANCE_SCHEDULE_ONLY_LEVEL_PARAM",
                "INSTANCE_REFERENCE_LEVEL_PARAM",
                "SCHEDULE_LEVEL_PARAM",
            ],
            "level_names": ["Level", "Reference Level"],
        },
    }


def _schedule_mode_from_prompt(prompt_text):
    lowered = (prompt_text or "").lower()
    if "summary" in lowered:
        return "summary"
    return "detailed"


def _prefer_reference_level(prompt_text):
    return "reference level" in (prompt_text or "").lower()


def _parse_schedule_prefab_filter(prompt_text):
    text = prompt_text or ""
    patterns = [
        r'ai_prefabcode\s*:\s*"([^"]+)"',
        r'prefabcode\s*:\s*"([^"]+)"',
        r'ai_prefabcode\s*:\s*([^\s,]+)',
        r'prefabcode\s*:\s*([^\s,]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            if value:
                return value
    return None


def _parse_template_request(prompt_text):
    lowered = (prompt_text or "").lower()
    if "template" not in lowered and "source schedule" not in lowered:
        return (False, None)
    patterns = [
        r'from template\s+"([^"]+)"',
        r'template\s*:\s*"([^"]+)"',
        r'from source schedule\s+"([^"]+)"',
        r'from template\s+([^\n]+)$',
    ]
    for pattern in patterns:
        match = re.search(pattern, prompt_text or "", re.IGNORECASE)
        if match:
            candidate = (match.group(1) or "").strip()
            candidate = re.sub(r"\s+(by|with|in)\s+.*$", "", candidate, flags=re.IGNORECASE).strip()
            if candidate:
                return (True, candidate)
    return (True, None)


def _all_schedule_names(doc):
    names = set()
    try:
        for schedule in DB.FilteredElementCollector(doc).OfClass(DB.ViewSchedule):
            try:
                names.add(get_elem_name(schedule))
            except:
                pass
    except:
        pass
    return names


def _schedulable_field_name(field, doc):
    try:
        return field.GetName(doc)
    except:
        pass
    try:
        return field.GetName()
    except:
        pass
    return ""


def _schedule_field_name(field, doc):
    try:
        return field.GetName()
    except:
        pass
    try:
        schedulable = field.GetSchedulableField()
        return _schedulable_field_name(schedulable, doc)
    except:
        pass
    try:
        return getattr(field, "ColumnHeading", "")
    except:
        return ""


def _schedule_field_matches(field_or_schedulable, doc, bip_names=None, field_names=None):
    bip_names = bip_names or []
    field_names = field_names or []
    try:
        param_id = field_or_schedulable.ParameterId
        for bip_name in bip_names:
            bip = _bip(bip_name)
            if bip is None:
                continue
            try:
                if param_id.IntegerValue == int(bip):
                    return True
            except:
                pass
    except:
        pass
    try:
        display_name = (_schedulable_field_name(field_or_schedulable, doc) or "").lower()
    except:
        display_name = (_schedule_field_name(field_or_schedulable, doc) or "").lower()
    wanted = [name.lower() for name in field_names or []]
    if display_name in wanted:
        return True
    display_norm = re.sub(r"[^a-z0-9]+", "", display_name)
    wanted_norm = [re.sub(r"[^a-z0-9]+", "", name.lower()) for name in field_names or []]
    return display_norm in wanted_norm


def _existing_schedule_field(definition, doc, bip_names=None, field_names=None):
    try:
        for field_id in definition.GetFieldOrder():
            field = definition.GetField(field_id)
            if _schedule_field_matches(field, doc, bip_names, field_names):
                return field
    except:
        pass
    return None


def _ensure_schedule_field(definition, doc, bip_names=None, field_names=None):
    existing = _existing_schedule_field(definition, doc, bip_names, field_names)
    if existing is not None:
        return existing
    try:
        for schedulable in definition.GetSchedulableFields():
            if _schedule_field_matches(schedulable, doc, bip_names, field_names):
                return definition.AddField(schedulable)
    except:
        pass
    return None


def _clear_schedule_grouping(definition):
    try:
        while definition.GetSortGroupFieldCount() > 0:
            definition.RemoveSortGroupField(0)
    except:
        pass


def _clear_schedule_filters(definition):
    try:
        while definition.GetFilterCount() > 0:
            definition.RemoveFilter(0)
    except:
        pass


def _add_schedule_sort_group(definition, field, show_header=False, show_footer=False):
    if field is None:
        return False
    try:
        sort_group = DB.ScheduleSortGroupField(field.FieldId)
        try:
            sort_group.ShowHeader = show_header
        except:
            pass
        try:
            sort_group.ShowFooter = show_footer
        except:
            pass
        definition.AddSortGroupField(sort_group)
        return True
    except:
        return False


def _enable_schedule_grand_totals(definition):
    try:
        definition.ShowGrandTotal = True
    except:
        pass
    try:
        definition.ShowGrandTotalCount = True
    except:
        pass
    try:
        definition.ShowGrandTotalTitle = True
    except:
        pass


def _find_schedule_template(doc, config, prompt_text):
    template_requested, requested_name = _parse_template_request(prompt_text)
    if not template_requested:
        return (None, "Native schedule definition used.")

    schedules = []
    try:
        schedules = list(DB.FilteredElementCollector(doc).OfClass(DB.ViewSchedule))
    except:
        schedules = []

    chosen = None
    if requested_name:
        requested_lower = requested_name.lower()
        exact = []
        partial = []
        for schedule in schedules:
            schedule_name = (get_elem_name(schedule) or "").strip()
            if not schedule_name:
                continue
            if schedule_name.lower() == requested_lower:
                exact.append(schedule)
            elif requested_lower in schedule_name.lower():
                partial.append(schedule)
        if exact:
            chosen = exact[0]
        elif partial:
            chosen = sorted(partial, key=lambda item: len(get_elem_name(item) or ""))[0]
        if chosen is not None:
            return (chosen, "Template schedule duplicated: {0}".format(get_elem_name(chosen)))
        return (None, "Template '{0}' was not found. Native schedule definition used.".format(requested_name))

    heuristic = []
    for schedule in schedules:
        schedule_name = (get_elem_name(schedule) or "").lower()
        if "template" not in schedule_name:
            continue
        if any(keyword in schedule_name for keyword in config.get("keywords", [])):
            heuristic.append(schedule)
    if heuristic:
        chosen = sorted(heuristic, key=lambda item: len(get_elem_name(item) or ""))[0]
        return (chosen, "Template schedule duplicated: {0}".format(get_elem_name(chosen)))
    return (None, "No matching schedule template was found. Native schedule definition used.")


def _schedule_name_for_config(config, mode, prefer_reference_level):
    level_phrase = "Reference Level" if prefer_reference_level else "Level"
    if mode == "summary":
        return "AI {0} Summary Schedule by {1}".format(config.get("title"), level_phrase)
    return "AI {0} Schedule by {1}".format(config.get("title"), level_phrase)


def _configure_schedule_definition(schedule, doc, config, mode, prompt_text, prefab_value=None):
    definition = schedule.Definition
    _clear_schedule_grouping(definition)
    _clear_schedule_filters(definition)

    prefer_reference_level = _prefer_reference_level(prompt_text)
    level_names = list(config.get("level_names") or [])
    if prefer_reference_level:
        level_names = sorted(level_names, key=lambda name: 0 if "reference" in name.lower() else 1)

    level_field = _ensure_schedule_field(definition, doc, config.get("level_bips"), level_names)
    family_type_field = _ensure_schedule_field(
        definition,
        doc,
        ["ELEM_FAMILY_AND_TYPE_PARAM", "SYMBOL_FAMILY_AND_TYPE_NAMES_PARAM"],
        ["Family and Type", "Type", "Type Name"],
    )
    count_field = _ensure_schedule_field(definition, doc, [], ["Count"])
    mark_field = None
    comments_field = None
    if mode != "summary":
        mark_field = _ensure_schedule_field(definition, doc, ["ALL_MODEL_MARK"], ["Mark"])
        comments_field = _ensure_schedule_field(definition, doc, ["ALL_MODEL_INSTANCE_COMMENTS"], ["Comments"])

    notes = []
    if level_field is None:
        notes.append("Level/reference-level field could not be resolved for this category.")
    if family_type_field is None:
        notes.append("Family/type field could not be resolved; grouping uses the available level field only.")

    try:
        definition.IsItemized = (mode != "summary")
    except:
        notes.append("Schedule itemization mode could not be adjusted.")

    if level_field is not None:
        _add_schedule_sort_group(definition, level_field, show_header=True, show_footer=(mode == "summary"))
    if family_type_field is not None:
        _add_schedule_sort_group(definition, family_type_field, show_header=(mode == "summary"), show_footer=(mode == "summary"))

    if mode == "summary":
        _enable_schedule_grand_totals(definition)
        if count_field is None:
            notes.append("Grand total count field was not available in this schedule definition.")
    elif mark_field is None and comments_field is None:
        notes.append("Detailed mode used the native available fields; Mark/Comments were not both available.")

    if prefab_value:
        prefab_field = _ensure_schedule_field(
            definition,
            doc,
            [],
            ["AI_PrefabCode", "PrefabCode"],
        )
        if prefab_field is not None:
            try:
                definition.AddFilter(DB.ScheduleFilter(prefab_field.FieldId, DB.ScheduleFilterType.Equal, prefab_value))
                notes.append("Applied prefab filter: {0}".format(prefab_value))
            except Exception as exc:
                notes.append("Prefab filter could not be applied: {0}".format(str(exc)))
        else:
            notes.append("Prefab filter requested, but no AI_PrefabCode/PrefabCode schedulable field was available.")

    return notes


def _create_schedule_for_config(doc, config, prompt_text):
    mode = _schedule_mode_from_prompt(prompt_text)
    prefer_reference_level = _prefer_reference_level(prompt_text)
    prefab_value = _parse_schedule_prefab_filter(prompt_text)
    source_schedule, template_note = _find_schedule_template(doc, config, prompt_text)
    existing_names = _all_schedule_names(doc)
    schedule_name = _unique_name(_schedule_name_for_config(config, mode, prefer_reference_level), existing_names)
    transaction = DB.Transaction(doc, "AI {0} Schedule".format(config.get("title")))
    transaction.Start()
    try:
        if source_schedule is not None:
            new_id = source_schedule.Duplicate(DB.ViewDuplicateOption.Duplicate)
            schedule = doc.GetElement(new_id)
        else:
            schedule = DB.ViewSchedule.CreateSchedule(doc, DB.ElementId(config.get("category")))
        if schedule is None:
            transaction.RollBack()
            return {"ok": False, "message": "Failed to create {0} schedule: no schedule view was returned.".format(config.get("title").lower())}
        schedule.Name = schedule_name
        notes = _configure_schedule_definition(schedule, doc, config, mode, prompt_text, prefab_value=prefab_value)
        transaction.Commit()
        return {
            "ok": True,
            "schedule_name": schedule_name,
            "mode": mode,
            "template_note": template_note,
            "notes": notes,
        }
    except Exception as exc:
        try:
            transaction.RollBack()
        except:
            pass
        return {"ok": False, "message": "Failed to create {0} schedule: {1}".format(config.get("title").lower(), str(exc))}


def _format_schedule_creation_result(header, doc, uidoc, results, scope_note=None):
    lines = [
        header,
        "Active document: {0}".format(_document_title(doc)),
        "Active view: {0}".format(_active_view_title(doc, uidoc)),
    ]
    if scope_note:
        lines.append(scope_note)
    lines.append("Schedules processed: {0}".format(len(results)))
    created = [item for item in results if item.get("ok")]
    failed = [item for item in results if not item.get("ok")]
    lines.append("Schedules created: {0}".format(len(created)))
    if failed:
        lines.append("Schedules failed: {0}".format(len(failed)))
    for item in created:
        lines.append(
            "{0}: {1} | mode: {2} | {3}".format(
                item.get("label", "Schedule"),
                item.get("schedule_name"),
                item.get("mode", "detailed"),
                item.get("template_note", "Native schedule definition used."),
            )
        )
        for note in item.get("notes", [])[:4]:
            lines.append("  - {0}".format(note))
    for item in failed[:10]:
        lines.append("{0}: {1}".format(item.get("label", "Schedule"), item.get("message")))
    if len(failed) > 10:
        lines.append("...showing first 10 of {0} failures".format(len(failed)))
    return "\n".join(lines)


def _create_single_schedule_action(doc, uidoc, config_key, context=None):
    prompt_text = _prompt_text_from_context(context)
    config = _schedule_action_configs().get(config_key)
    if config is None:
        return "Failed: unsupported schedule configuration '{0}'.".format(config_key)
    result = _create_schedule_for_config(doc, config, prompt_text)
    result["label"] = config.get("title")
    header = "Create {0} schedule by level".format(config.get("title").lower())
    return _format_schedule_creation_result(
        header,
        doc,
        uidoc,
        [result],
        scope_note="Reviewed mode: {0} schedule grouped by category-specific level/reference-level and family/type.".format(result.get("mode", _schedule_mode_from_prompt(prompt_text))),
    )


def create_pipe_schedule_by_level(doc, uidoc, context=None):
    return _create_single_schedule_action(doc, uidoc, "pipe", context)


def create_pipe_fitting_schedule_by_level(doc, uidoc, context=None):
    return _create_single_schedule_action(doc, uidoc, "pipe_fitting", context)


def create_duct_schedule_by_level(doc, uidoc, context=None):
    return _create_single_schedule_action(doc, uidoc, "duct", context)


def create_duct_fitting_schedule_by_level(doc, uidoc, context=None):
    return _create_single_schedule_action(doc, uidoc, "duct_fitting", context)


def create_conduit_schedule_by_level(doc, uidoc, context=None):
    return _create_single_schedule_action(doc, uidoc, "conduit", context)


def create_electrical_fixture_equipment_schedule_by_level(doc, uidoc, context=None):
    prompt_text = _prompt_text_from_context(context)
    configs = _schedule_action_configs()
    results = []
    for key in ["electrical_fixture", "electrical_equipment"]:
        result = _create_schedule_for_config(doc, configs[key], prompt_text)
        result["label"] = configs[key].get("title")
        results.append(result)
    return _format_schedule_creation_result(
        "Create electrical fixture/equipment schedule by level",
        doc,
        uidoc,
        results,
        scope_note="Reviewed mode: {0} schedule set. This action creates separate category-specific schedules for fixtures and equipment.".format(
            _schedule_mode_from_prompt(prompt_text)
        ),
    )


def create_schedule_bundle_by_level(doc, uidoc, context=None):
    prompt_text = _prompt_text_from_context(context)
    configs = _schedule_action_configs()
    lowered = (prompt_text or "").lower()
    requested_keys = []
    ordered_keys = [
        "pipe_fitting",
        "pipe",
        "duct_fitting",
        "duct",
        "conduit",
        "electrical_fixture",
        "electrical_equipment",
    ]
    for key in ordered_keys:
        config = configs.get(key)
        if config and any(keyword in lowered for keyword in config.get("keywords", [])):
            requested_keys.append(key)
    if not requested_keys:
        requested_keys = ordered_keys

    results = []
    for key in requested_keys:
        result = _create_schedule_for_config(doc, configs[key], prompt_text)
        result["label"] = configs[key].get("title")
        results.append(result)

    return _format_schedule_creation_result(
        "Create schedule bundle by level",
        doc,
        uidoc,
        results,
        scope_note="Reviewed bundle mode: {0}. Supported categories are created as separate deterministic schedules grouped by category-specific level/reference-level and family/type.".format(
            _schedule_mode_from_prompt(prompt_text)
        ),
    )


def _schedule_tokens(text):
    return [token for token in re.split(r"[^a-z0-9]+", (text or "").lower()) if token]


def _schedule_definition_field_names(schedule, doc):
    names = []
    try:
        definition = schedule.Definition
        for field_id in definition.GetFieldOrder():
            try:
                field = definition.GetField(field_id)
                name = _schedule_field_name(field, doc)
                if name:
                    names.append(name)
            except:
                continue
    except:
        pass
    return names


def _schedule_filter_field_names(schedule, doc):
    names = []
    try:
        definition = schedule.Definition
        for index in range(definition.GetFilterCount()):
            try:
                sched_filter = definition.GetFilter(index)
                field = definition.GetField(sched_filter.FieldId)
                name = _schedule_field_name(field, doc)
                if name:
                    names.append(name)
            except:
                continue
    except:
        pass
    return names


def _aco_pipe_product_profile(label, base_name, product_tokens, summary=False):
    recipe = {
        "label": label,
        "base_name": base_name,
        "source_kind": "token_summary_or_master" if summary else "token_master_only",
        "summary": summary,
        "require_non_empty_source": True,
        "required_source_tokens": ["aco", "pipe"] + list(product_tokens or []),
        "required_field_tokens": [
            "segment description",
            "size",
            "outside diameter",
            "length",
            "inside diameter",
            "roughness",
            "article number",
            "definition",
            "gtin",
            "weight",
            "manufacturer",
            "count",
            "reference level",
        ],
        "minimum_field_hits": 7,
        "level_field_names": ["Reference Level"],
    }
    if summary:
        recipe["summary_group_fields"] = ["Segment Description", "Size", "Article number", "Reference Level"]
    return recipe


def _aco_template_profiles():
    return {
        "aco_pipe_schedule": {
            "label": "ACO Pipe Schedule",
            "base_name": "ACO Pipe Schedule",
            "source_kind": "blocked_missing_neutral_master",
            "summary": False,
            "canonical_master_source_names": [],
            "known_project_master_names": [
                "ACO pipe EPDM 1.4301 single socket",
                "ACO pipe EPDM 1.4404 single socket",
                "ACO pipe EPDM 1.4404 double socket",
            ],
            "required_field_tokens": [
                "segment description",
                "size",
                "outside diameter",
                "length",
                "inside diameter",
                "roughness",
                "article number",
                "definition",
                "gtin",
                "weight",
                "manufacturer",
                "count",
                "reference level",
            ],
            "minimum_field_hits": 7,
            "level_field_names": ["Reference Level"],
            "block_reason": "No neutral all-ACO pipe master template is registered for this project family. Reviewed template creation is blocked until a true master source exists or separate reviewed product-family actions are added for 1.4301 single socket, 1.4404 single socket, and 1.4404 double socket pipe schedules.",
        },
        "aco_pipe_fitting_schedule": {
            "label": "ACO Pipe Fitting Schedule",
            "base_name": "ACO Pipe Fitting Schedule",
            "source_kind": "exact_master_only",
            "summary": False,
            "canonical_master_source_names": ["ACO pipe fittings"],
            "required_field_tokens": [
                "count",
                "family",
                "rsen_p_c01_diameter",
                "rsen_p_c02_diameter",
                "rsen_p_c03_diameter",
                "rsen_p_c01_angle",
                "rsen_c_description",
                "rsen_c_type_comments",
                "rsen_c_code_article",
                "rsen_c_code_gtin",
                "rsen_s_net_mass",
                "option",
                "manufacturer",
                "description",
                "category",
                "productrange",
                "level",
            ],
            "minimum_field_hits": 8,
            "level_field_names": ["Level"],
        },
        "aco_pipe_summary": {
            "label": "ACO Pipe Summary",
            "base_name": "ACO Pipe Summary",
            "source_kind": "blocked_missing_neutral_master",
            "summary": True,
            "canonical_summary_source_names": [],
            "canonical_master_source_names": [],
            "known_project_master_names": [
                "ACO pipe EPDM 1.4301 single socket",
                "ACO pipe EPDM 1.4404 single socket",
                "ACO pipe EPDM 1.4404 double socket",
            ],
            "required_field_tokens": [
                "segment description",
                "size",
                "length",
                "article number",
                "manufacturer",
                "count",
                "reference level",
            ],
            "minimum_field_hits": 5,
            "summary_group_fields": ["Segment Description", "Size", "Article number", "Reference Level"],
            "level_field_names": ["Reference Level"],
            "block_reason": "No neutral all-ACO pipe master or summary template is registered for this project family. Reviewed template summary creation is blocked until a true summary-compatible canonical source exists or separate reviewed product-family summary actions are added.",
        },
        "aco_pipe_fitting_summary": {
            "label": "ACO Pipe Fitting Summary",
            "base_name": "ACO Pipe Fitting Summary",
            "source_kind": "exact_summary_or_master",
            "summary": True,
            "canonical_summary_source_names": [],
            "canonical_master_source_names": ["ACO pipe fittings"],
            "required_field_tokens": [
                "count",
                "family",
                "rsen_p_c01_diameter",
                "rsen_p_c03_diameter",
                "rsen_c_code_article",
                "manufacturer",
                "description",
                "productrange",
                "level",
            ],
            "minimum_field_hits": 6,
            "summary_group_fields": [
                "Description",
                "RSen_P_c01_diameter",
                "RSen_P_c03_diameter",
                "RSen_C_code_article",
            ],
            "level_field_names": ["Level"],
        },
        "aco_pipe_14301_single_socket_schedule": _aco_pipe_product_profile(
            "ACO 1.4301 Single Socket Pipe Schedule",
            "ACO 1.4301 Single Socket Pipe Schedule",
            ["1.4301", "single", "socket"],
            summary=False,
        ),
        "aco_pipe_14404_single_socket_schedule": _aco_pipe_product_profile(
            "ACO 1.4404 Single Socket Pipe Schedule",
            "ACO 1.4404 Single Socket Pipe Schedule",
            ["1.4404", "single", "socket"],
            summary=False,
        ),
        "aco_pipe_14404_double_socket_schedule": _aco_pipe_product_profile(
            "ACO 1.4404 Double Socket Pipe Schedule",
            "ACO 1.4404 Double Socket Pipe Schedule",
            ["1.4404", "double", "socket"],
            summary=False,
        ),
        "aco_pipe_14301_single_socket_summary": _aco_pipe_product_profile(
            "ACO 1.4301 Single Socket Pipe Summary",
            "ACO 1.4301 Single Socket Pipe Summary",
            ["1.4301", "single", "socket"],
            summary=True,
        ),
        "aco_pipe_14404_single_socket_summary": _aco_pipe_product_profile(
            "ACO 1.4404 Single Socket Pipe Summary",
            "ACO 1.4404 Single Socket Pipe Summary",
            ["1.4404", "single", "socket"],
            summary=True,
        ),
        "aco_pipe_14404_double_socket_summary": _aco_pipe_product_profile(
            "ACO 1.4404 Double Socket Pipe Summary",
            "ACO 1.4404 Double Socket Pipe Summary",
            ["1.4404", "double", "socket"],
            summary=True,
        ),
    }


def _parse_schedule_template_filters(prompt_text):
    text = prompt_text or ""
    filters = {
        "prefer_reference_level": _prefer_reference_level(text),
    }
    level_match = re.search(r"\bfor\s+(.+?)\s+from template\b", text, re.IGNORECASE)
    if level_match:
        filters["level_value"] = level_match.group(1).strip()
    else:
        level_match = re.search(r"\b(?:reference level|level)\s*:\s*([^\n,]+)", text, re.IGNORECASE)
        if level_match:
            filters["level_value"] = level_match.group(1).strip()

    manufacturer_match = re.search(r"\bmanufacturer\s*:\s*([^\n,]+)", text, re.IGNORECASE)
    if manufacturer_match:
        filters["manufacturer"] = manufacturer_match.group(1).strip()

    description_match = re.search(r"\bdescription contains\s*:\s*([^\n,]+)", text, re.IGNORECASE)
    if description_match:
        filters["description_contains"] = description_match.group(1).strip()

    productrange_match = re.search(r"\bproductrange\s*:\s*([^\n,]+)", text, re.IGNORECASE)
    if productrange_match:
        filters["productrange"] = productrange_match.group(1).strip()

    article_match = re.search(r"\b(?:article|code)\s*:\s*([^\n,]+)", text, re.IGNORECASE)
    if article_match:
        filters["article_code"] = article_match.group(1).strip()

    return filters


def _is_generated_schedule_name(schedule_name):
    name = (schedule_name or "").strip().lower()
    if not name:
        return True
    generated_prefixes = [
        "ai ",
        "ai-",
        "ai_",
        "aco pipe schedule",
        "aco pipe summary",
        "aco pipe fitting schedule",
        "aco pipe fitting summary",
        "aco 1.4301 single socket pipe schedule",
        "aco 1.4301 single socket pipe summary",
        "aco 1.4404 single socket pipe schedule",
        "aco 1.4404 single socket pipe summary",
        "aco 1.4404 double socket pipe schedule",
        "aco 1.4404 double socket pipe summary",
    ]
    return any(name.startswith(prefix) for prefix in generated_prefixes)


def _schedule_name_has_level_token(schedule_name):
    name = (schedule_name or "").lower()
    patterns = [
        r"\bground floor\b",
        r"\bfirst floor\b",
        r"\bsecond floor\b",
        r"\bthird floor\b",
        r"\bfourth floor\b",
        r"\bfifth floor\b",
        r"\bl\d+\b",
        r"\blevel\s+\d+\b",
    ]
    for pattern in patterns:
        if re.search(pattern, name):
            return True
    return False


def _schedule_field_hits(info, recipe):
    hits = 0
    field_names = info.get("field_names", [])
    for token in recipe.get("required_field_tokens", []):
        if any(token in field_name for field_name in field_names):
            hits += 1
    return hits


def _build_schedule_candidate_info(schedule, doc):
    name = (get_elem_name(schedule) or "").strip()
    field_names = [item.lower() for item in _schedule_definition_field_names(schedule, doc)]
    filter_names = [item.lower() for item in _schedule_filter_field_names(schedule, doc)]
    return {
        "schedule": schedule,
        "name": name,
        "name_lower": name.lower(),
        "field_names": field_names,
        "filter_names": filter_names,
        "all_text": " ".join([name.lower()] + field_names + filter_names),
    }


def _is_disallowed_template_source_name(schedule_name, summary_expected=False):
    name = (schedule_name or "").strip()
    name_lower = name.lower()
    if _is_generated_schedule_name(name_lower):
        return True
    if "sheet" in name_lower:
        return True
    if not summary_expected and "summary" in name_lower:
        return True
    if _schedule_name_has_level_token(name_lower):
        return True
    return False


def _schedule_meets_recipe_requirements(info, recipe):
    minimum_field_hits = recipe.get("minimum_field_hits", 0)
    if minimum_field_hits and _schedule_field_hits(info, recipe) < minimum_field_hits:
        return False
    return True


def _all_schedule_infos(doc):
    infos = []
    try:
        for schedule in DB.FilteredElementCollector(doc).OfClass(DB.ViewSchedule):
            infos.append(_build_schedule_candidate_info(schedule, doc))
    except:
        pass
    return infos


def _find_explicit_template_source(doc, recipe, candidate_names, summary_expected=False):
    if not candidate_names:
        return None
    wanted = {}
    for candidate_name in candidate_names:
        wanted[(candidate_name or "").strip().lower()] = candidate_name
    for info in _all_schedule_infos(doc):
        schedule_name = info.get("name") or ""
        lookup_name = schedule_name.strip().lower()
        if lookup_name not in wanted:
            continue
        if _is_disallowed_template_source_name(schedule_name, summary_expected=summary_expected):
            continue
        if not _schedule_meets_recipe_requirements(info, recipe):
            continue
        return info
    return None


def _find_token_template_source(doc, recipe, summary_expected=False):
    tokens = [token.lower() for token in recipe.get("required_source_tokens", []) if token]
    if not tokens:
        return None
    matches = []
    for info in _all_schedule_infos(doc):
        schedule_name = info.get("name") or ""
        name_lower = schedule_name.lower()
        if _is_disallowed_template_source_name(schedule_name, summary_expected=summary_expected):
            continue
        if not all(token in name_lower for token in tokens):
            continue
        if not _schedule_meets_recipe_requirements(info, recipe):
            continue
        matches.append(info)
    if not matches:
        return None
    matches.sort(key=lambda item: (len(item.get("name") or ""), item.get("name") or ""))
    return matches[0]


def _select_recipe_source_schedule(doc, recipe, prompt_text):
    source_kind = recipe.get("source_kind")
    label = recipe.get("label") or "template action"
    if source_kind == "blocked_missing_neutral_master":
        known_sources = recipe.get("known_project_master_names") or []
        lines = [recipe.get("block_reason") or "Canonical reviewed source template is missing for {0}.".format(label)]
        if known_sources:
            lines.append("Known narrower project templates: {0}".format(", ".join(known_sources)))
        return (None, None, "\n".join(lines))

    if source_kind == "exact_master_only":
        info = _find_explicit_template_source(doc, recipe, recipe.get("canonical_master_source_names"), summary_expected=False)
        if info is None:
            return (
                None,
                None,
                "Missing canonical reviewed master template for {0}. Expected one of: {1}".format(
                    label,
                    ", ".join(recipe.get("canonical_master_source_names") or []),
                ),
            )
        return (
            info.get("schedule"),
            "master",
            "Canonical reviewed master template selected: {0}".format(info.get("name")),
        )

    if source_kind == "exact_summary_or_master":
        summary_info = _find_explicit_template_source(doc, recipe, recipe.get("canonical_summary_source_names"), summary_expected=True)
        if summary_info is not None:
            return (
                summary_info.get("schedule"),
                "summary",
                "Canonical reviewed summary template selected: {0}".format(summary_info.get("name")),
            )
        master_info = _find_explicit_template_source(doc, recipe, recipe.get("canonical_master_source_names"), summary_expected=False)
        if master_info is not None:
            return (
                master_info.get("schedule"),
                "master",
                "Canonical reviewed master template selected for summary transform: {0}".format(master_info.get("name")),
            )
        return (
            None,
            None,
            "Missing canonical reviewed source template for {0}. Expected summary or master source from: {1}".format(
                label,
                ", ".join((recipe.get("canonical_summary_source_names") or []) + (recipe.get("canonical_master_source_names") or [])),
            ),
        )

    if source_kind == "token_master_only":
        info = _find_token_template_source(doc, recipe, summary_expected=False)
        if info is None:
            return (
                None,
                None,
                "Missing matching product-family source template for {0}. Required source-name tokens: {1}".format(
                    label,
                    ", ".join(recipe.get("required_source_tokens") or []),
                ),
            )
        return (
            info.get("schedule"),
            "master",
            "Product-family source template selected: {0}".format(info.get("name")),
        )

    if source_kind == "token_summary_or_master":
        summary_info = _find_token_template_source(doc, recipe, summary_expected=True)
        if summary_info is not None:
            return (
                summary_info.get("schedule"),
                "summary",
                "Product-family summary source template selected: {0}".format(summary_info.get("name")),
            )
        master_info = _find_token_template_source(doc, recipe, summary_expected=False)
        if master_info is not None:
            return (
                master_info.get("schedule"),
                "master",
                "Product-family source template selected for summary transform: {0}".format(master_info.get("name")),
            )
        return (
            None,
            None,
            "Missing matching product-family source template for {0}. Required source-name tokens: {1}".format(
                label,
                ", ".join(recipe.get("required_source_tokens") or []),
            ),
        )

    return (None, None, "Unsupported reviewed source-template policy for {0}.".format(label))


def _find_matching_filter_index(definition, doc, field_names=None):
    wanted = [name.lower() for name in (field_names or [])]
    wanted_norm = [re.sub(r"[^a-z0-9]+", "", name) for name in wanted]
    try:
        for index in range(definition.GetFilterCount()):
            try:
                sched_filter = definition.GetFilter(index)
                field = definition.GetField(sched_filter.FieldId)
                field_name = (_schedule_field_name(field, doc) or "").lower()
                field_norm = re.sub(r"[^a-z0-9]+", "", field_name)
                if field_name in wanted or field_norm in wanted_norm:
                    return index
            except:
                continue
    except:
        pass
    return None


def _replace_or_add_schedule_filter(definition, doc, field, filter_type, value, field_names):
    if field is None or value in (None, ""):
        return False
    new_filter = DB.ScheduleFilter(field.FieldId, filter_type, value)
    existing_index = _find_matching_filter_index(definition, doc, field_names)
    try:
        if existing_index is not None:
            try:
                definition.SetFilter(existing_index, new_filter)
            except:
                definition.RemoveFilter(existing_index)
                definition.AddFilter(new_filter)
        else:
            definition.AddFilter(new_filter)
        return True
    except:
        return False


def _all_doc_level_names(doc):
    names = []
    try:
        for level in DB.FilteredElementCollector(doc).OfClass(DB.Level):
            level_name = get_elem_name(level)
            if level_name:
                names.append(level_name)
    except:
        pass
    return names


def _normalize_level_alias(text):
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _resolve_doc_level_name(doc, raw_level_name):
    if not raw_level_name:
        return (None, None)
    level_names = _all_doc_level_names(doc)
    if not level_names:
        return (None, "No levels were found in the active document.")

    raw = raw_level_name.strip()
    raw_norm = _normalize_level_alias(raw)
    if not raw_norm:
        return (None, "Requested level name is empty.")

    exact_matches = []
    for level_name in level_names:
        if _normalize_level_alias(level_name) == raw_norm:
            exact_matches.append(level_name)
    unique_exact = sorted(set(exact_matches))
    if len(unique_exact) == 1:
        return (unique_exact[0], None)
    if len(unique_exact) > 1:
        return (None, "Requested level '{0}' matches multiple exact document levels: {1}".format(raw, ", ".join(unique_exact[:5])))

    alias_map = {
        "groundfloor": ["groundfloor", "gf", "level0", "l0"],
        "firstfloor": ["firstfloor", "1stfloor", "level1", "l1"],
        "secondfloor": ["secondfloor", "2ndfloor", "level2", "l2"],
        "thirdfloor": ["thirdfloor", "3rdfloor", "level3", "l3"],
        "fourthfloor": ["fourthfloor", "4thfloor", "level4", "l4"],
        "fifthfloor": ["fifthfloor", "5thfloor", "level5", "l5"],
    }

    target_canonical = None
    for canonical, variants in alias_map.items():
        if raw_norm == canonical or raw_norm in variants:
            target_canonical = canonical
            break
    if target_canonical is None:
        return (None, "Requested level '{0}' could not be mapped safely to an active-document level.".format(raw))

    matches = []
    for level_name in level_names:
        norm = _normalize_level_alias(level_name)
        if norm == target_canonical or norm.endswith(target_canonical):
            matches.append(level_name)
    unique = sorted(set(matches))
    if len(unique) == 1:
        return (unique[0], None)
    if len(unique) > 1:
        return (None, "Requested level '{0}' is ambiguous across document levels: {1}".format(raw, ", ".join(unique[:5])))
    return (None, "Requested level '{0}' could not be mapped safely to an active-document level.".format(raw))


def _validate_template_recipe_request(doc, recipe, prompt_text):
    filters = _parse_schedule_template_filters(prompt_text)
    resolved_level_name = None
    if filters.get("level_value"):
        resolved_level_name, level_issue = _resolve_doc_level_name(doc, filters.get("level_value"))
        if level_issue:
            return (None, level_issue)
    filters["resolved_level_name"] = resolved_level_name
    return (filters, None)


def _apply_summary_transform(schedule, doc, recipe):
    definition = schedule.Definition
    notes = []
    group_fields = []
    for field_name in recipe.get("summary_group_fields", []):
        field = _ensure_schedule_field(definition, doc, [], [field_name])
        if field is None:
            return (False, ["Summary transform blocked: required field '{0}' is unavailable.".format(field_name)])
        group_fields.append(field)
    try:
        definition.IsItemized = False
    except:
        return (False, ["Summary transform blocked: schedule could not be made non-itemized."])
    _clear_schedule_grouping(definition)
    for field in group_fields:
        _add_schedule_sort_group(definition, field, show_header=True, show_footer=True)
    _enable_schedule_grand_totals(definition)
    notes.append("Applied reviewed summary transform using the canonical master template.")
    return (True, notes)


def _schedule_can_resolve_field(definition, doc, bip_names=None, field_names=None):
    if _existing_schedule_field(definition, doc, bip_names, field_names) is not None:
        return True
    try:
        for schedulable in definition.GetSchedulableFields():
            if _schedule_field_matches(schedulable, doc, bip_names, field_names):
                return True
    except:
        pass
    return False


def _schedule_visible_body_row_count(schedule):
    try:
        table_data = schedule.GetTableData()
        try:
            section_data = table_data.GetSectionData(DB.SectionType.Body)
        except:
            section_data = table_data.GetSectionData(1)
        return int(section_data.NumberOfRows)
    except:
        return None


def _preflight_template_schedule_request(source_schedule, doc, recipe, request_filters, source_kind):
    definition = source_schedule.Definition
    if recipe.get("require_non_empty_source"):
        row_count = _schedule_visible_body_row_count(source_schedule)
        if row_count == 0:
            return (
                False,
                "Selected source template exists but has no visible schedule rows/elements, so reviewed creation was blocked to avoid producing an empty schedule.",
            )
    if request_filters.get("resolved_level_name"):
        if not _schedule_can_resolve_field(
            definition,
            doc,
            ["INSTANCE_REFERENCE_LEVEL_PARAM", "RBS_REFERENCE_LEVEL_PARAM", "RBS_START_LEVEL_PARAM", "FAMILY_LEVEL_PARAM", "SCHEDULE_LEVEL_PARAM"],
            recipe.get("level_field_names", ["Reference Level", "Level"]),
        ):
            return (False, "Level retargeting could not be applied safely to the canonical source template.")
    if recipe.get("summary") and source_kind == "master":
        for field_name in recipe.get("summary_group_fields", []):
            if not _schedule_can_resolve_field(definition, doc, [], [field_name]):
                return (False, "Summary transform blocked: required field '{0}' is unavailable on the canonical source template.".format(field_name))
    return (True, None)


def _apply_template_schedule_adjustments(schedule, doc, recipe, prompt_text, request_filters, source_kind):
    notes = []
    definition = schedule.Definition

    if request_filters.get("resolved_level_name"):
        level_field = _ensure_schedule_field(
            definition,
            doc,
            ["INSTANCE_REFERENCE_LEVEL_PARAM", "RBS_REFERENCE_LEVEL_PARAM", "RBS_START_LEVEL_PARAM", "FAMILY_LEVEL_PARAM", "SCHEDULE_LEVEL_PARAM"],
            recipe.get("level_field_names", ["Reference Level", "Level"]),
        )
        if not _replace_or_add_schedule_filter(
            definition,
            doc,
            level_field,
            DB.ScheduleFilterType.Equal,
            request_filters.get("resolved_level_name"),
            recipe.get("level_field_names", ["Reference Level", "Level"]),
        ):
            return (False, ["Level retargeting could not be applied safely to this source template."])
        notes.append("Applied level retargeting: {0}".format(request_filters.get("resolved_level_name")))
    else:
        notes.append("No level retargeting requested.")

    if request_filters.get("manufacturer"):
        manufacturer_field = _ensure_schedule_field(definition, doc, ["ALL_MODEL_MANUFACTURER"], ["Manufacturer"])
        if _replace_or_add_schedule_filter(definition, doc, manufacturer_field, DB.ScheduleFilterType.Contains, request_filters.get("manufacturer"), ["Manufacturer"]):
            notes.append("Applied manufacturer filter: {0}".format(request_filters.get("manufacturer")))
        else:
            notes.append("Manufacturer filter could not be safely adjusted.")

    if request_filters.get("description_contains"):
        description_field = _ensure_schedule_field(definition, doc, [], ["Description", "Segment Description"])
        if _replace_or_add_schedule_filter(definition, doc, description_field, DB.ScheduleFilterType.Contains, request_filters.get("description_contains"), ["Description", "Segment Description"]):
            notes.append("Applied description filter: {0}".format(request_filters.get("description_contains")))
        else:
            notes.append("Description filter could not be safely adjusted.")

    if request_filters.get("productrange"):
        productrange_field = _ensure_schedule_field(definition, doc, [], ["productrange", "ProductRange"])
        if _replace_or_add_schedule_filter(definition, doc, productrange_field, DB.ScheduleFilterType.Contains, request_filters.get("productrange"), ["productrange", "ProductRange"]):
            notes.append("Applied productrange filter: {0}".format(request_filters.get("productrange")))
        else:
            notes.append("productrange filter could not be safely adjusted.")

    if request_filters.get("article_code"):
        article_field = _ensure_schedule_field(definition, doc, [], ["Article number", "Article Number", "Code", "Article", "GTIN"])
        if _replace_or_add_schedule_filter(definition, doc, article_field, DB.ScheduleFilterType.Contains, request_filters.get("article_code"), ["Article number", "Article Number", "Code", "Article", "GTIN"]):
            notes.append("Applied article/code filter: {0}".format(request_filters.get("article_code")))
        else:
            notes.append("Article/code filter could not be safely adjusted.")

    if recipe.get("summary") and source_kind == "master":
        ok, summary_notes = _apply_summary_transform(schedule, doc, recipe)
        if not ok:
            return (False, summary_notes)
        notes.extend(summary_notes)
    elif recipe.get("summary"):
        notes.append("Summary-compatible canonical source template preserved.")

    return (True, notes)


def _template_schedule_name(recipe, prompt_text, request_filters):
    base_name = recipe.get("base_name")
    suffix_parts = []
    if request_filters.get("resolved_level_name"):
        suffix_parts.append(request_filters.get("resolved_level_name"))
    elif request_filters.get("prefer_reference_level"):
        suffix_parts.append("Reference Level")
    if request_filters.get("manufacturer"):
        suffix_parts.append(request_filters.get("manufacturer"))
    if suffix_parts:
        return "{0} - {1}".format(base_name, " - ".join(suffix_parts))
    return base_name


def _create_template_only_schedule_action(doc, uidoc, profile_key, context=None):
    prompt_text = _prompt_text_from_context(context)
    profile = _aco_template_profiles().get(profile_key)
    if profile is None:
        return "Failed: unsupported template schedule profile '{0}'.".format(profile_key)
    request_filters, request_issue = _validate_template_recipe_request(doc, profile, prompt_text)
    if request_issue:
        return "\n".join(
            [
                profile.get("label"),
                "Active document: {0}".format(_document_title(doc)),
                "Active view: {0}".format(_active_view_title(doc, uidoc)),
                request_issue,
                "Template-only action: generic native fallback was not used.",
            ]
        )
    source_schedule, source_kind, template_note = _select_recipe_source_schedule(doc, profile, prompt_text)
    if source_schedule is None:
        return "\n".join(
            [
                profile.get("label"),
                "Active document: {0}".format(_document_title(doc)),
                "Active view: {0}".format(_active_view_title(doc, uidoc)),
                template_note,
                "Template-only action: generic native fallback was not used.",
            ]
        )
    preflight_ok, preflight_issue = _preflight_template_schedule_request(source_schedule, doc, profile, request_filters, source_kind)
    if not preflight_ok:
        return "\n".join(
            [
                profile.get("label"),
                "Active document: {0}".format(_document_title(doc)),
                "Active view: {0}".format(_active_view_title(doc, uidoc)),
                template_note,
                preflight_issue,
                "Template-only action: generic native fallback was not used.",
            ]
        )

    existing_names = _all_schedule_names(doc)
    schedule_name = _unique_name(_template_schedule_name(profile, prompt_text, request_filters), existing_names)
    transaction = DB.Transaction(doc, "AI {0}".format(profile.get("label")))
    transaction.Start()
    try:
        new_id = source_schedule.Duplicate(DB.ViewDuplicateOption.Duplicate)
        schedule = doc.GetElement(new_id)
        if schedule is None:
            transaction.RollBack()
            return "Failed to duplicate template schedule for {0}.".format(profile.get("label"))
        ok, notes = _apply_template_schedule_adjustments(schedule, doc, profile, prompt_text, request_filters, source_kind)
        if not ok:
            transaction.RollBack()
            return "\n".join(
                [
                    profile.get("label"),
                    "Active document: {0}".format(_document_title(doc)),
                    "Active view: {0}".format(_active_view_title(doc, uidoc)),
                    template_note,
                    notes[0],
                    "Template-only action: generic native fallback was not used.",
                ]
            )
        schedule.Name = schedule_name
        transaction.Commit()
    except Exception as exc:
        try:
            transaction.RollBack()
        except:
            pass
        return "Failed to create {0}: {1}".format(profile.get("label").lower(), str(exc))

    lines = [
        profile.get("label"),
        "Active document: {0}".format(_document_title(doc)),
        "Active view: {0}".format(_active_view_title(doc, uidoc)),
        "{0}".format(template_note),
        "Created schedule: {0}".format(schedule_name),
        "Recipe type: {0}".format(source_kind),
        "Level retargeting: {0}".format("applied" if request_filters.get("resolved_level_name") else "not requested"),
        "Template-only action: generic native fallback was not used.",
    ]
    for note in notes[:6]:
        lines.append(note)
    return "\n".join(lines)


def create_aco_pipe_schedule_from_template(doc, uidoc, context=None):
    return _create_template_only_schedule_action(doc, uidoc, "aco_pipe_schedule", context)


def create_aco_pipe_fitting_schedule_from_template(doc, uidoc, context=None):
    return _create_template_only_schedule_action(doc, uidoc, "aco_pipe_fitting_schedule", context)


def create_aco_pipe_summary_from_template(doc, uidoc, context=None):
    return _create_template_only_schedule_action(doc, uidoc, "aco_pipe_summary", context)


def create_aco_pipe_fitting_summary_from_template(doc, uidoc, context=None):
    return _create_template_only_schedule_action(doc, uidoc, "aco_pipe_fitting_summary", context)


def create_aco_14301_single_socket_pipe_schedule_from_template(doc, uidoc, context=None):
    return _create_template_only_schedule_action(doc, uidoc, "aco_pipe_14301_single_socket_schedule", context)


def create_aco_14404_single_socket_pipe_schedule_from_template(doc, uidoc, context=None):
    return _create_template_only_schedule_action(doc, uidoc, "aco_pipe_14404_single_socket_schedule", context)


def create_aco_14404_double_socket_pipe_schedule_from_template(doc, uidoc, context=None):
    return _create_template_only_schedule_action(doc, uidoc, "aco_pipe_14404_double_socket_schedule", context)


def create_aco_14301_single_socket_pipe_summary_from_template(doc, uidoc, context=None):
    return _create_template_only_schedule_action(doc, uidoc, "aco_pipe_14301_single_socket_summary", context)


def create_aco_14404_single_socket_pipe_summary_from_template(doc, uidoc, context=None):
    return _create_template_only_schedule_action(doc, uidoc, "aco_pipe_14404_single_socket_summary", context)


def create_aco_14404_double_socket_pipe_summary_from_template(doc, uidoc, context=None):
    return _create_template_only_schedule_action(doc, uidoc, "aco_pipe_14404_double_socket_summary", context)


def create_aco_prefab_schedule_bundle_from_template(doc, uidoc, context=None):
    prompt_text = _prompt_text_from_context(context)
    results = []
    request_filters, request_issue = _validate_template_recipe_request(doc, _aco_template_profiles().get("aco_pipe_schedule"), prompt_text)
    if request_issue:
        return _format_schedule_creation_result(
            "Create ACO prefab schedule bundle from template",
            doc,
            uidoc,
            [{"ok": False, "label": "ACO Prefab Bundle", "message": request_issue}],
            scope_note="Template-only reviewed action. Generic native fallback is intentionally disabled for this bundle.",
        )
    for profile_key in [
        "aco_pipe_schedule",
        "aco_pipe_fitting_schedule",
        "aco_pipe_summary",
        "aco_pipe_fitting_summary",
    ]:
        profile = _aco_template_profiles().get(profile_key)
        source_schedule, source_kind, template_note = _select_recipe_source_schedule(doc, profile, prompt_text)
        if source_schedule is None:
            results.append({"ok": False, "label": profile.get("label"), "message": template_note})
            continue
        preflight_ok, preflight_issue = _preflight_template_schedule_request(source_schedule, doc, profile, request_filters, source_kind)
        if not preflight_ok:
            results.append({"ok": False, "label": profile.get("label"), "message": "{0}\n{1}".format(template_note, preflight_issue)})
            continue
        existing_names = _all_schedule_names(doc)
        schedule_name = _unique_name(_template_schedule_name(profile, prompt_text, request_filters), existing_names)
        transaction = DB.Transaction(doc, "AI {0}".format(profile.get("label")))
        transaction.Start()
        try:
            new_id = source_schedule.Duplicate(DB.ViewDuplicateOption.Duplicate)
            schedule = doc.GetElement(new_id)
            if schedule is None:
                transaction.RollBack()
                results.append({"ok": False, "label": profile.get("label"), "message": "Template duplication returned no schedule."})
                continue
            ok, notes = _apply_template_schedule_adjustments(schedule, doc, profile, prompt_text, request_filters, source_kind)
            if not ok:
                transaction.RollBack()
                results.append({"ok": False, "label": profile.get("label"), "message": notes[0]})
                continue
            schedule.Name = schedule_name
            transaction.Commit()
            results.append(
                {
                    "ok": True,
                    "label": profile.get("label"),
                    "schedule_name": schedule_name,
                    "mode": source_kind,
                    "template_note": template_note,
                    "notes": notes,
                }
            )
        except Exception as exc:
            try:
                transaction.RollBack()
            except:
                pass
            results.append({"ok": False, "label": profile.get("label"), "message": str(exc)})

    return _format_schedule_creation_result(
        "Create ACO prefab schedule bundle from template",
        doc,
        uidoc,
        results,
        scope_note="Template-only reviewed action. Generic native fallback is intentionally disabled for this bundle.",
    )


def create_3d_view_from_selection(doc, uidoc):
    from Autodesk.Revit.DB import FilteredElementCollector, Transaction, View3D, ViewFamily, ViewFamilyType

    view_type = None
    for candidate in FilteredElementCollector(doc).OfClass(ViewFamilyType):
        try:
            if candidate.ViewFamily == ViewFamily.ThreeDimensional:
                view_type = candidate
                break
        except:
            continue
    if view_type is None:
        return "Failed: no 3D view family type exists in the current project."

    transaction = Transaction(doc, "Create AI 3D View")
    transaction.Start()
    try:
        view3d = View3D.CreateIsometric(doc, view_type.Id)
        if view3d is None:
            transaction.RollBack()
            return "Failed: View3D.CreateIsometric returned no view."
        try:
            view3d.Name = "AI 3D View"
        except:
            pass
        transaction.Commit()
        return "Created 3D view: {0}".format(view3d.Name)
    except Exception as exc:
        try:
            transaction.RollBack()
        except:
            pass
        return "Failed to create 3D view: {0}".format(str(exc))


def _document_identity(doc):
    try:
        return doc.PathName or doc.Title or "(unsaved document)"
    except:
        return "(unknown document)"


def execute_create_3d_view_with_undo(doc, uidoc):
    from Autodesk.Revit.DB import FilteredElementCollector, Transaction, View3D, ViewFamily, ViewFamilyType

    view_type = None
    for candidate in FilteredElementCollector(doc).OfClass(ViewFamilyType):
        try:
            if candidate.ViewFamily == ViewFamily.ThreeDimensional:
                view_type = candidate
                break
        except:
            continue
    if view_type is None:
        return {"message": "Failed: no 3D view family type exists in the current project."}

    transaction = Transaction(doc, "Create AI 3D View")
    transaction.Start()
    try:
        view3d = View3D.CreateIsometric(doc, view_type.Id)
        if view3d is None:
            transaction.RollBack()
            return {"message": "Failed: View3D.CreateIsometric returned no view."}
        try:
            view3d.Name = "AI 3D View"
        except:
            pass
        transaction.Commit()
        return {
            "message": "Created 3D view: {0}".format(view3d.Name),
            "undo_context": {
                "action_id": "create-3d-view-from-selection",
                "action_title": "Create 3D view from current selection/context",
                "role": "modifying",
                "document_identity": _document_identity(doc),
                "created_element_ids": [view3d.Id.IntegerValue],
                "created_view_id": view3d.Id.IntegerValue,
                "created_view_name": view3d.Name,
                "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "session_marker": "agent-current-session",
                "undo_available": True,
            },
        }
    except Exception as exc:
        try:
            transaction.RollBack()
        except:
            pass
        return {"message": "Failed to create 3D view: {0}".format(str(exc))}


def undo_create_3d_view_action(doc, undo_context):
    if not undo_context:
        return {"ok": False, "message": "Undo unavailable: no undo context recorded."}
    if undo_context.get("action_id") != "create-3d-view-from-selection":
        return {"ok": False, "message": "Undo unavailable: last action is not a reversible create-3D-view action."}
    if not undo_context.get("undo_available"):
        return {"ok": False, "message": "Undo unavailable: context is not marked reversible."}
    if undo_context.get("document_identity") != _document_identity(doc):
        return {"ok": False, "message": "Undo unavailable: document changed or invalid context."}

    view_id_value = undo_context.get("created_view_id")
    if view_id_value is None:
        return {"ok": False, "message": "Undo unavailable: created view identifier was not recorded."}

    created_view = None
    try:
        created_view = doc.GetElement(DB.ElementId(int(view_id_value)))
    except:
        created_view = None
    if created_view is None:
        return {"ok": False, "message": "Undo failed: created view no longer exists."}

    transaction = DB.Transaction(doc, "Undo AI 3D View")
    transaction.Start()
    try:
        view_name = undo_context.get("created_view_name") or get_elem_name(created_view)
        doc.Delete(created_view.Id)
        transaction.Commit()
        return {
            "ok": True,
            "message": "UNDONE: Create 3D view from current selection/context\nDeleted created 3D view: {0}".format(view_name),
        }
    except Exception as exc:
        try:
            transaction.RollBack()
        except:
            pass
        return {"ok": False, "message": "Undo failed: {0}".format(str(exc))}


def undo_create_sheet_action(doc, undo_context):
    if not undo_context:
        return {"ok": False, "message": "Undo unavailable: no undo context recorded."}
    if undo_context.get("action_id") != "create-sheet-reviewed-template":
        return {"ok": False, "message": "Undo unavailable: last action is not a reversible create-sheet action."}
    if not undo_context.get("undo_available"):
        return {"ok": False, "message": "Undo unavailable: context is not marked reversible."}
    if undo_context.get("document_identity") != _document_identity(doc):
        return {"ok": False, "message": "Undo unavailable: document changed or invalid context."}

    sheet_id_value = undo_context.get("created_sheet_id")
    if sheet_id_value is None:
        return {"ok": False, "message": "Undo unavailable: created sheet identifier was not recorded."}

    created_sheet = None
    try:
        created_sheet = doc.GetElement(DB.ElementId(int(sheet_id_value)))
    except:
        created_sheet = None
    if created_sheet is None:
        return {"ok": False, "message": "Undo failed: created sheet no longer exists."}

    transaction = DB.Transaction(doc, "Undo AI Sheet")
    transaction.Start()
    try:
        sheet_number = undo_context.get("created_sheet_number") or getattr(created_sheet, "SheetNumber", "(no number)")
        sheet_name = undo_context.get("created_sheet_name") or getattr(created_sheet, "Name", "(no name)")
        doc.Delete(created_sheet.Id)
        transaction.Commit()
        return {
            "ok": True,
            "message": "UNDONE: Create sheet\nDeleted created sheet: {0} ({1})".format(
                sheet_number,
                sheet_name,
            ),
        }
    except Exception as exc:
        try:
            transaction.RollBack()
        except:
            pass
        return {"ok": False, "message": "Undo failed: {0}".format(str(exc))}


def undo_rename_active_view_action(doc, undo_context):
    if not undo_context:
        return {"ok": False, "message": "Undo unavailable: no undo context recorded."}
    if undo_context.get("action_id") != "rename-active-view":
        return {"ok": False, "message": "Undo unavailable: last action is not a reversible rename-active-view action."}
    if not undo_context.get("undo_available"):
        return {"ok": False, "message": "Undo unavailable: context is not marked reversible."}
    if undo_context.get("document_identity") != _document_identity(doc):
        return {"ok": False, "message": "Undo unavailable: document changed or invalid context."}

    view_id_value = undo_context.get("view_id")
    old_name = undo_context.get("old_view_name")
    if view_id_value is None or not old_name:
        return {"ok": False, "message": "Undo unavailable: rename metadata is incomplete."}

    renamed_view = None
    try:
        renamed_view = doc.GetElement(DB.ElementId(int(view_id_value)))
    except:
        renamed_view = None
    if renamed_view is None:
        return {"ok": False, "message": "Undo failed: renamed view no longer exists."}

    transaction = DB.Transaction(doc, "Undo AI Rename Active View")
    transaction.Start()
    try:
        renamed_view.Name = old_name
        transaction.Commit()
        return {
            "ok": True,
            "message": "UNDONE: Rename active view\nRestored active view name: {0}".format(old_name),
        }
    except Exception as exc:
        try:
            transaction.RollBack()
        except:
            pass
        return {"ok": False, "message": "Undo failed: {0}".format(str(exc))}


def delete_all_ducts(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector, Transaction

    ducts = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_DuctCurves)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    ids = [d.Id for d in ducts]
    t = Transaction(doc, "Delete all ducts")
    t.Start()
    for i in ids:
        doc.Delete(i)
    t.Commit()
    return "Deleted {} ducts.".format(len(ids))


# --- ELECTRICAL ---
def select_all_lights(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector
    from System.Collections.Generic import List

    lights = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_LightingFixtures)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    ids = [l.Id for l in lights]
    rows = []
    for l in lights:
        name = get_elem_name(l)
        t = l.Document.GetElement(l.GetTypeId())
        typ = t.Name if (t is not None and hasattr(t, "Name")) else "(no type)"
        rows.append("Name: {}, Type: {}".format(name, typ))
    uidoc.Selection.SetElementIds(List[DB.ElementId](ids))
    msg = "Selected {} lighting fixtures.\n".format(len(lights))
    msg += "\n".join(rows[:20])
    if len(rows) > 20:
        msg += "\n... (showing first 20 of {})".format(len(rows))
    return msg


def select_all_equip(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector
    from System.Collections.Generic import List

    equip = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_ElectricalEquipment)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    ids = [e.Id for e in equip]
    rows = []
    for e in equip:
        name = get_elem_name(e)
        t = e.Document.GetElement(e.GetTypeId())
        typ = t.Name if (t is not None and hasattr(t, "Name")) else "(no type)"
        rows.append("Name: {}, Type: {}".format(name, typ))
    uidoc.Selection.SetElementIds(List[DB.ElementId](ids))
    msg = "Selected {} electrical equipment.\n".format(len(equip))
    msg += "\n".join(rows[:20])
    if len(rows) > 20:
        msg += "\n... (showing first 20 of {})".format(len(rows))
    return msg


def total_cabletray_length(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector

    trays = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_CableTray)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    total_len = 0
    for tray in trays:
        param = tray.LookupParameter("Length")
        if param:
            try:
                total_len += param.AsDouble()
            except:
                pass
    m = total_len * 0.3048  # Revit units to meters
    return "Total cable tray length: {:.2f} m".format(m)


def select_all_cabletrays(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector
    from System.Collections.Generic import List

    trays = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_CableTray)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    ids = [t.Id for t in trays]
    rows = []
    total_length = 0
    for t in trays:
        name = get_elem_name(t)
        type_obj = t.Document.GetElement(t.GetTypeId())
        typ = (
            type_obj.Name
            if (type_obj is not None and hasattr(type_obj, "Name"))
            else "(no type)"
        )
        length_param = t.LookupParameter("Length")
        length = 0
        if length_param:
            try:
                length = length_param.AsDouble() * 0.3048
            except:
                pass
        total_length += length
        rows.append("Type: {}, Length: {:.2f} m".format(typ, length))
    uidoc.Selection.SetElementIds(List[DB.ElementId](ids))
    msg = "Selected {} cable trays.\n".format(len(trays))
    msg += "\n".join(rows[:20])
    if len(rows) > 20:
        msg += "\n... (showing first 20 of {})".format(len(rows))
    msg += "\nTotal Length: {:.2f} m".format(total_length)
    return msg


def count_loaded_families(doc, uidoc):
    from Autodesk.Revit.DB import FilteredElementCollector, Family

    fams = FilteredElementCollector(doc).OfClass(Family).ToElements()
    names = [f.Name for f in fams]
    msg = "Loaded families: {}\n".format(
        ", ".join(names[:10]) + (" ..." if len(names) > 10 else "")
    )
    msg += "Total loaded families: {}".format(len(fams))
    return msg


def count_lights(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector

    lights = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_LightingFixtures)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    return "Total lighting fixtures: {}".format(len(lights))


def delete_all_lights(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector, Transaction

    lights = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_LightingFixtures)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    ids = [l.Id for l in lights]
    t = Transaction(doc, "Delete all lighting fixtures")
    t.Start()
    for i in ids:
        doc.Delete(i)
    t.Commit()
    return "Deleted {} lighting fixtures.".format(len(ids))


# --- GENERAL ---
def get_elem_name(elem):
    if elem is None:
        return None
    try:
        elem_name = getattr(elem, "Name", None)
        if elem_name:
            return elem_name
    except:
        pass
    try:
        if hasattr(elem, "Document") and hasattr(elem, "GetTypeId"):
            type_id = elem.GetTypeId()
            if type_id and hasattr(type_id, "IntegerValue") and type_id.IntegerValue > 0:
                elem_type = elem.Document.GetElement(type_id)
                type_name = getattr(elem_type, "Name", None) if elem_type is not None else None
                if type_name:
                    return type_name
    except:
        pass
    try:
        return "ElementId: {}".format(elem.Id.IntegerValue)
    except:
        pass
    return safe_str(elem)


def all_parameters_for_category(doc, category):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector

    elements = (
        FilteredElementCollector(doc)
        .OfCategory(category)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    if not elements:
        return "No elements in category."
    msg = ""
    for elem in elements:
        msg += "\nElement: {} (Id {})\n".format(
            elem.Name if hasattr(elem, "Name") else "(no name)", elem.Id.IntegerValue
        )
        for param in elem.Parameters:
            try:
                pname = param.Definition.Name
                if param.StorageType == DB.StorageType.String:
                    pval = param.AsString()
                elif param.StorageType == DB.StorageType.Double:
                    pval = param.AsDouble()
                elif param.StorageType == DB.StorageType.Integer:
                    pval = param.AsInteger()
                elif param.StorageType == DB.StorageType.ElementId:
                    val = param.AsElementId()
                    pval = val.IntegerValue if val else None
                else:
                    pval = str(param)
                msg += "    {}: {}\n".format(pname, pval)
            except Exception as e:
                msg += "    {}: <error reading>\n".format(param.Definition.Name)
    return msg or "No readable parameters."


def report_all_parameters_of_selection(doc, uidoc):
    selection = uidoc.Selection.GetElementIds()
    if not selection or len(selection) == 0:
        return "No elements selected."
    msg = ""
    for eid in selection:
        elem = doc.GetElement(eid)
        if not elem:
            continue
        msg += "\nElement: {} (Id {})\n".format(
            elem.Name if hasattr(elem, "Name") else "(no name)", eid.IntegerValue
        )
        for param in elem.Parameters:
            try:
                pname = param.Definition.Name
                if param.StorageType == DB.StorageType.String:
                    pval = param.AsString()
                elif param.StorageType == DB.StorageType.Double:
                    pval = param.AsDouble()
                elif param.StorageType == DB.StorageType.Integer:
                    pval = param.AsInteger()
                elif param.StorageType == DB.StorageType.ElementId:
                    val = param.AsElementId()
                    pval = val.IntegerValue if val else None
                else:
                    pval = str(param)
                msg += "    {}: {}\n".format(pname, pval)
            except Exception as e:
                msg += "    {}: <error reading>\n".format(param.Definition.Name)
    return msg or "No readable parameters."


def report_phases_of_selection(doc, uidoc):
    selection = uidoc.Selection.GetElementIds()
    if not selection or len(selection) == 0:
        return "No elements selected."
    msg = ""
    for eid in selection:
        elem = doc.GetElement(eid)
        if not elem:
            continue
        phase_created = None
        phase_demolished = None
        if hasattr(elem, "CreatedPhaseId"):
            pc = elem.CreatedPhaseId
            pd = elem.DemolishedPhaseId
            phase_created = doc.GetElement(pc).Name if pc.IntegerValue > 0 else "None"
            phase_demolished = (
                doc.GetElement(pd).Name if pd and pd.IntegerValue > 0 else "None"
            )
        msg += "Element: {} (Id {})\n".format(
            elem.Name if hasattr(elem, "Name") else "(no name)", eid.IntegerValue
        )
        msg += "    Created in phase: {}\n".format(phase_created)
        msg += "    Demolished in phase: {}\n".format(phase_demolished)
    return msg or "No readable phase data."


def list_all_phases(doc, uidoc):
    from Autodesk.Revit.DB import FilteredElementCollector, Phase

    phases = FilteredElementCollector(doc).OfClass(Phase).ToElements()
    if not phases:
        return "No phases found."
    msg = "Phases in project:\n"
    for p in phases:
        msg += "    {} (Id: {})\n".format(p.Name, p.Id.IntegerValue)
    return msg


def tag_selected_elements(doc, uidoc):
    from Autodesk.Revit.DB import Transaction, XYZ

    eids = uidoc.Selection.GetElementIds()
    if not eids or len(eids) == 0:
        return "No elements selected."
    t = Transaction(doc, "Tag elements")
    t.Start()
    count = 0
    for eid in eids:
        elem = doc.GetElement(eid)
        # Try to tag if taggable (Rooms, Walls, Doors, Windows)
        pt = None
        loc = getattr(elem, "Location", None)
        if hasattr(loc, "Point"):
            pt = loc.Point
        elif hasattr(loc, "Curve"):
            pt = loc.Curve.Evaluate(0.5, True)
        # You can expand: check element category for what tag to create
        if pt:
            try:
                doc.Create.NewTag(
                    None,
                    elem,
                    False,
                    DB.TagMode.TM_ADDBY_CATEGORY,
                    DB.TagOrientation.Horizontal,
                    pt,
                )
                count += 1
            except:
                pass
    t.Commit()
    return "Tagged {} elements.".format(count)


def count_all_sheets(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector

    sheets = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Sheets)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    names = [
        s.LookupParameter("Sheet Number").AsString()
        + " "
        + s.LookupParameter("Sheet Name").AsString()
        for s in sheets
        if s.LookupParameter("Sheet Number") and s.LookupParameter("Sheet Name")
    ]
    msg = "Total sheets: {}\n".format(len(sheets))
    msg += "Sheets: {}\n".format(
        ", ".join(names[:10]) + (" ..." if len(names) > 10 else "")
    )
    return msg


def list_all_sheets(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector

    sheets = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Sheets)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    msg = "Sheets:\n"
    for s in sheets:
        num = (
            s.LookupParameter("Sheet Number").AsString()
            if s.LookupParameter("Sheet Number")
            else "?"
        )
        name = (
            s.LookupParameter("Sheet Name").AsString()
            if s.LookupParameter("Sheet Name")
            else "?"
        )
        msg += "    {} - {}\n".format(num, name)
    return msg


def count_all_views(doc, uidoc):
    from Autodesk.Revit.DB import View, FilteredElementCollector

    views = FilteredElementCollector(doc).OfClass(View).ToElements()
    names = [v.Name for v in views if hasattr(v, "Name")]
    msg = "Total views: {}\n".format(len(views))
    msg += "View names: {}\n".format(
        ", ".join(names[:10]) + (" ..." if len(names) > 10 else "")
    )
    return msg


def list_all_views(doc, uidoc):
    from Autodesk.Revit.DB import View, FilteredElementCollector

    views = FilteredElementCollector(doc).OfClass(View).ToElements()
    msg = "Views:\n"
    for v in views:
        msg += "    {}\n".format(v.Name)
    return msg


def list_all_families(doc, uidoc):
    from Autodesk.Revit.DB import Family, FilteredElementCollector

    fams = FilteredElementCollector(doc).OfClass(Family).ToElements()
    msg = "Families:\n"
    for f in fams:
        msg += "    {}\n".format(f.Name)
    return msg


def list_all_levels(doc, uidoc):
    from Autodesk.Revit.DB import Level, FilteredElementCollector

    lvls = FilteredElementCollector(doc).OfClass(Level).ToElements()
    msg = "Levels:\n"
    for l in lvls:
        msg += "    {} (Elevation: {:.2f} m)\n".format(l.Name, l.Elevation * 0.3048)
    return msg


def export_schedule_names(doc, uidoc):
    from Autodesk.Revit.DB import ViewSchedule, FilteredElementCollector

    schedules = FilteredElementCollector(doc).OfClass(ViewSchedule).ToElements()
    names = [sch.Name for sch in schedules]
    msg = "Schedules in project: {}\n".format(
        ", ".join(names[:10]) + (" ..." if len(names) > 10 else "")
    )
    msg += "Total schedules: {}".format(len(schedules))
    return msg


def export_all_schedule_data(doc, uidoc):
    from Autodesk.Revit.DB import ViewSchedule, FilteredElementCollector
    import codecs

    schedules = FilteredElementCollector(doc).OfClass(ViewSchedule).ToElements()
    output = []
    for sch in schedules:
        output.append("Schedule: {}".format(sch.Name))
        # Optionally: Export fields, rows, etc!
    with codecs.open(r"C:\\exported_schedules.txt", "w", "utf-8") as f:
        f.write("\n".join(output))
    return "Exported {} schedule names to C:\\exported_schedules.txt".format(
        len(schedules)
    )


def clash_check_category_vs_category(doc, cat1, cat2, tol=0.01):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector, Outline

    elements1 = (
        FilteredElementCollector(doc)
        .OfCategory(cat1)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    elements2 = (
        FilteredElementCollector(doc)
        .OfCategory(cat2)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    clashes = []
    for el1 in elements1:
        bb1 = el1.get_BoundingBox(None)
        if not bb1:
            continue
        outline1 = Outline(bb1.Min, bb1.Max)
        for el2 in elements2:
            bb2 = el2.get_BoundingBox(None)
            if not bb2:
                continue
            outline2 = Outline(bb2.Min, bb2.Max)
            if outline1.Intersects(outline2, tol):
                clashes.append((el1.Id.IntegerValue, el2.Id.IntegerValue))
    return clashes


def check_clashes_walls_vs_columns(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory

    clashes = clash_check_category_vs_category(
        doc, BuiltInCategory.OST_Walls, BuiltInCategory.OST_StructuralColumns
    )
    msg = "Wall-Column clashes found: {}\n".format(len(clashes))
    if len(clashes) > 0:
        msg += "Sample clashes (wallId,columnId): {}\n".format(
            ", ".join(str(pair) for pair in clashes[:10])
        )
    return msg


def check_clashes_ducts_vs_beams(doc, uidoc):
    from Autodesk.Revit.DB import BuiltInCategory

    clashes = clash_check_category_vs_category(
        doc, BuiltInCategory.OST_DuctCurves, BuiltInCategory.OST_StructuralFraming
    )
    return "Duct-Beam clashes found: {}".format(len(clashes))


def model_health_check(doc, uidoc):
    report = []
    from Autodesk.Revit.DB import (
        FilteredElementCollector,
        BuiltInCategory,
        View,
        Family,
        Phase,
        ViewSchedule,
        ElementType,
        Level,
        ViewSheet,
        Group,
        Grid,
        ReferencePlane,
        ImportInstance,
        RevitLinkInstance,
        DesignOption,  # (NO DesignOptionSet!)
        ParameterFilterElement,
        View3D,
        ViewPlan,
        ViewDrafting,
        WorksharingUtils,
        ProjectLocation,
        Workset,
    )
    import datetime
    import math

    errors = []
    warnings = []
    score = 100

    def section(title):
        report.append("\n== {} ==".format(title))

    def item(txt):
        report.append(" - {}".format(txt))

    # File Info
    section("File Info")
    try:
        file_path = doc.PathName or "(unsaved)"
        item("File: {}".format(file_path))
        file_size = (
            os.path.getsize(file_path) / (1024 * 1024)
            if os.path.exists(file_path)
            else None
        )
        if file_size:
            item("Size: {:.1f} MB".format(file_size))
    except:
        item("File size: N/A")

    # Worksharing Info
    section("Worksharing")
    try:
        is_workshared = WorksharingUtils.IsWorkshared(doc)
        item("Workshared: {}".format("Yes" if is_workshared else "No"))
        if is_workshared:
            item("Central Path: {}".format(WorksharingUtils.GetCentralServerPath(doc)))
            try:
                item(
                    "Worksets: {}".format(
                        len(list(FilteredElementCollector(doc).OfClass(Workset)))
                    )
                )
            except:
                item("Worksets: N/A")
    except:
        item("N/A")

    # Project Info
    section("Project Info")
    try:
        pi = doc.ProjectInformation
        item("Project Name: {}".format(pi.Name))
        item("Project Number: {}".format(pi.Number))
    except:
        item("N/A")

    # Basic Counts
    section("Main Element Counts")

    def count(cat):
        return len(
            list(
                FilteredElementCollector(doc)
                .OfCategory(cat)
                .WhereElementIsNotElementType()
            )
        )

    main_cats = [
        ("Walls", BuiltInCategory.OST_Walls),
        ("Columns", BuiltInCategory.OST_StructuralColumns),
        ("Beams", BuiltInCategory.OST_StructuralFraming),
        ("Floors", BuiltInCategory.OST_Floors),
        ("Roofs", BuiltInCategory.OST_Roofs),
        ("Doors", BuiltInCategory.OST_Doors),
        ("Windows", BuiltInCategory.OST_Windows),
        ("Rooms", BuiltInCategory.OST_Rooms),
        ("Areas", BuiltInCategory.OST_Areas),
        ("Grids", BuiltInCategory.OST_Grids),
        ("Ref Planes", BuiltInCategory.OST_CLines),
        ("Model Groups", BuiltInCategory.OST_IOSModelGroups),
        ("Curtain Panels", BuiltInCategory.OST_CurtainWallPanels),
        ("Rebars", BuiltInCategory.OST_Rebar),
        ("Stairs", BuiltInCategory.OST_Stairs),
        ("Ducts", BuiltInCategory.OST_DuctCurves),
        ("Pipes", BuiltInCategory.OST_PipeCurves),
        ("Sheets", BuiltInCategory.OST_Sheets),
    ]
    for name, cat in main_cats:
        c = count(cat)
        item("{}: {}".format(name, c))
        if name in ["Walls", "Floors", "Roofs"] and c > 2000:
            warnings.append("Too many {} ({})".format(name, c))
            score -= 2

    # Families/Types
    fams = list(FilteredElementCollector(doc).OfClass(Family))
    fam_types = list(FilteredElementCollector(doc).OfClass(ElementType))
    item("Families: {}".format(len(fams)))
    item("Element Types: {}".format(len(fam_types)))

    # Views/Schedules/Phases/Levels
    section("Views, Schedules, Levels, Phases")
    item("Views: {}".format(len(list(FilteredElementCollector(doc).OfClass(View)))))
    item(
        "3D Views: {}".format(len(list(FilteredElementCollector(doc).OfClass(View3D))))
    )
    item(
        "Plan Views: {}".format(
            len(list(FilteredElementCollector(doc).OfClass(ViewPlan)))
        )
    )
    item(
        "Drafting Views: {}".format(
            len(list(FilteredElementCollector(doc).OfClass(ViewDrafting)))
        )
    )
    sheets = list(FilteredElementCollector(doc).OfClass(ViewSheet))
    item("Sheets: {}".format(len(sheets)))
    schedules = list(FilteredElementCollector(doc).OfClass(ViewSchedule))
    item("Schedules: {}".format(len(schedules)))
    levels = list(FilteredElementCollector(doc).OfClass(Level))
    item("Levels: {}".format(len(levels)))
    phases = list(FilteredElementCollector(doc).OfClass(Phase))
    item("Phases: {}".format(len(phases)))
    view_templates = [
        v
        for v in FilteredElementCollector(doc).OfClass(View)
        if hasattr(v, "IsTemplate") and v.IsTemplate
    ]
    item("View Templates: {}".format(len(view_templates)))

    # Unplaced/Empty/Problematic
    section("Potential Problems")
    unplaced_rooms = [
        r
        for r in FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Rooms)
        .WhereElementIsNotElementType()
        if hasattr(r, "Area") and r.Area == 0
    ]
    item("Unplaced Rooms: {}".format(len(unplaced_rooms)))
    if unplaced_rooms:
        warnings.append("Unplaced rooms present")
        score -= 2
    unplaced_areas = [
        a
        for a in FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Areas)
        .WhereElementIsNotElementType()
        if hasattr(a, "Area") and a.Area == 0
    ]
    item("Unplaced Areas: {}".format(len(unplaced_areas)))
    unused_families = [f for f in fams if not list(f.GetFamilySymbolIds())]
    item("Families with No Types: {}".format(len(unused_families)))
    # unused_levels = [
    #     l for l in levels if not list(l.FindInserts(True, True, True, True))
    # ]
    # item("Unused Levels: {}".format(len(unused_levels)))

    # Empty Views, Sheets without views
    item("Unused Levels: (not checked)")
    empty_views = [
        v
        for v in FilteredElementCollector(doc).OfClass(View)
        if hasattr(v, "GetDependentElements")
        and len(list(v.GetDependentElements(None))) == 0
        and not v.IsTemplate
    ]
    item("Empty Views: {}".format(len(empty_views)))
    empty_sheets = [s for s in sheets if len(s.GetAllPlacedViews()) == 0]
    item("Sheets Without Views: {}".format(len(empty_sheets)))
    if empty_sheets:
        score -= 2

    # Outdated/Unloaded Links
    section("Links & Imports")
    links = list(FilteredElementCollector(doc).OfClass(RevitLinkInstance))
    item("Linked RVT: {}".format(len(links)))
    cad_imports = list(FilteredElementCollector(doc).OfClass(ImportInstance))
    item("Linked/Imported CAD: {}".format(len(cad_imports)))
    outdated_links = [l for l in links if hasattr(l, "IsLoaded") and not l.IsLoaded]
    item("Outdated/Unloaded RVT Links: {}".format(len(outdated_links)))
    if outdated_links:
        score -= 3

    # Shared Coordinates
    locations = list(FilteredElementCollector(doc).OfClass(ProjectLocation))
    shared_coords = any(
        [getattr(loc, "AreSharedCoordinatesInitialized", False) for loc in locations]
    )
    item("Shared Coordinates: {}".format("Yes" if shared_coords else "No"))
    if not shared_coords:
        warnings.append("Shared coordinates not set")

    # Model Groups
    groups = list(FilteredElementCollector(doc).OfClass(Group))
    item("Groups: {}".format(len(groups)))

    # Parameter Filters
    param_filters = list(FilteredElementCollector(doc).OfClass(ParameterFilterElement))
    item("Parameter Filters: {}".format(len(param_filters)))

    # Design Options
    try:
        optMgr = doc.GetDesignOptionManager()
        sets = [optMgr.GetOptionSet(i) for i in range(optMgr.NumberOfOptionSets)]
        item("Design Option Sets: {}".format(len(sets)))
        dos = list(FilteredElementCollector(doc).OfClass(DB.DesignOption))
        item("Design Options: {}".format(len(dos)))
    except:
        item("Design Option Sets: N/A")
        item("Design Options: N/A")

    # Last Saved
    try:
        from System import DateTime

        save_time = doc.LastSavedTime if hasattr(doc, "LastSavedTime") else None
        if save_time:
            if isinstance(save_time, DateTime):
                save_time = save_time.ToLocalTime()
            item("Last Saved: {}".format(save_time))
    except:
        item("Last Saved: (N/A)")

    # Warnings
    section("Warnings")
    try:
        doc_warnings = doc.GetWarnings()
        n_warn = doc_warnings.Count
        item("Total Warnings: {}".format(n_warn))
        # Top 10 warning types summary
        warn_types = {}
        for w in doc_warnings:
            wt = w.GetDescriptionText()
            if wt in warn_types:
                warn_types[wt] += 1
            else:
                warn_types[wt] = 1
        if warn_types:
            for t, c in sorted(warn_types.items(), key=lambda x: -x[1])[:10]:
                item("Warning: {} ({}x)".format(t, c))
        if n_warn > 0:
            score -= min(10, n_warn // 50)  # Penalty per 50 warnings
    except:
        item("Could not read warnings.")

    # Duplicate Mark Checks
    section("Data Quality Checks")
    marks = {}
    duplicates = set()
    for el in FilteredElementCollector(doc).WhereElementIsNotElementType():
        try:
            mark = (
                el.LookupParameter("Mark").AsString()
                if el.LookupParameter("Mark")
                else None
            )
            if mark and mark not in ["", None]:
                if mark in marks:
                    duplicates.add(mark)
                else:
                    marks[mark] = 1
        except:
            continue
    item(
        "Duplicate Marks: {}".format(
            ", ".join(list(duplicates)) if duplicates else "None"
        )
    )
    if duplicates:
        score -= 3

    # Large Elements
    section("Largest Elements")
    # Walls, Floors, Roofs by volume
    try:
        biggest_walls = sorted(
            [
                w
                for w in FilteredElementCollector(doc)
                .OfCategory(BuiltInCategory.OST_Walls)
                .WhereElementIsNotElementType()
                if w.LookupParameter("Volume")
            ],
            key=lambda x: x.LookupParameter("Volume").AsDouble(),
            reverse=True,
        )[:3]
        for w in biggest_walls:
            v = w.LookupParameter("Volume").AsDouble() * 0.0283168
            item(
                "Big Wall: '{}' {:.2f} m³".format(
                    getattr(w, "Name", w.Id.IntegerValue), v
                )
            )
    except:
        pass
    try:
        biggest_floors = sorted(
            [
                f
                for f in FilteredElementCollector(doc)
                .OfCategory(BuiltInCategory.OST_Floors)
                .WhereElementIsNotElementType()
                if f.LookupParameter("Volume")
            ],
            key=lambda x: x.LookupParameter("Volume").AsDouble(),
            reverse=True,
        )[:3]
        for f in biggest_floors:
            v = f.LookupParameter("Volume").AsDouble() * 0.0283168
            item(
                "Big Floor: '{}' {:.2f} m³".format(
                    getattr(f, "Name", f.Id.IntegerValue), v
                )
            )
    except:
        pass

    # Families/Types Most Common
    section("Most Common Families / Types")
    fam_count = {}
    for el in FilteredElementCollector(doc).WhereElementIsNotElementType():
        try:
            t = el.Document.GetElement(el.GetTypeId())
            n = t.Name if t else "(unknown)"
            fam_count[n] = fam_count.get(n, 0) + 1
        except:
            continue
    for fam, cnt in sorted(fam_count.items(), key=lambda x: -x[1])[:5]:
        item("{}: {} instances".format(fam, cnt))

    # Schedules with no rows
    empty_schedules = []
    for s in schedules:
        try:
            if s.Definition.GetTableData().GetSectionData(0).NumberOfRows == 0:
                empty_schedules.append(s.Name)
        except:
            continue
    if empty_schedules:
        section("Empty Schedules")
        for n in empty_schedules:
            item("Empty schedule: {}".format(n))

    # Views not on sheets
    views_not_on_sheet = []
    for v in FilteredElementCollector(doc).OfClass(View):
        if (
            not v.IsTemplate
            and hasattr(v, "CanBePrinted")
            and v.CanBePrinted
            and v.ViewType.ToString() not in ["DrawingSheet", "Schedule"]
        ):
            placed = False
            for s in sheets:
                if v.Id in s.GetAllPlacedViews():
                    placed = True
                    break
            if not placed:
                views_not_on_sheet.append(v.Name)
    section("Views Not On Sheets")
    item("Views not on sheets: {}".format(len(views_not_on_sheet)))

    # Health Score
    section("Health Score")
    if score > 90:
        status = "Excellent"
    elif score > 75:
        status = "Good"
    elif score > 60:
        status = "Warning"
    else:
        status = "Critical"
    item("Health Score: {} ({})".format(score, status))

    # Warnings/Errors
    if errors:
        section("Critical Errors")
        for e in errors:
            item(e)
    if warnings:
        section("Warnings")
        for w in warnings:
            item(w)

    section("End of Health Check")
    return "\n".join(report)


def super_select(doc, uidoc, categories):
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector
    from System.Collections.Generic import List

    # Map string to BuiltInCategory safely
    cat_map = {
        "walls": BuiltInCategory.OST_Walls,
        "columns": BuiltInCategory.OST_StructuralColumns,
        "ceilings": BuiltInCategory.OST_Ceilings,
        "beams": BuiltInCategory.OST_StructuralFraming,
        "foundations": BuiltInCategory.OST_StructuralFoundation,
        "floors": BuiltInCategory.OST_Floors,
        "windows": BuiltInCategory.OST_Windows,
        "doors": BuiltInCategory.OST_Doors,
        "rebars": BuiltInCategory.OST_Rebar,
        "roofs": BuiltInCategory.OST_Roofs,
        "ducts": BuiltInCategory.OST_DuctCurves,
        "pipes": BuiltInCategory.OST_PipeCurves,
        # Add more as needed!
    }
    selected_ids = []
    summary = []
    for cat in categories:
        cat = cat.strip().lower()
        if cat not in cat_map:
            summary.append("Category not found: {}".format(cat))
            continue
        elems = (
            FilteredElementCollector(doc)
            .OfCategory(cat_map[cat])
            .WhereElementIsNotElementType()
            .ToElements()
        )
        ids = [e.Id for e in elems]
        selected_ids.extend(ids)
        summary.append("Selected {} {}.".format(len(elems), cat))
    # Set the selection in Revit
    if selected_ids:
        uidoc.Selection.SetElementIds(List[DB.ElementId](selected_ids))
    return "\n".join(summary)


# Add more public command functions as needed!
MODELMIND_COMMANDS = [
    "select all columns",
    "select all walls",
    "select all floors",
    "select all ceilings",
    "select all roofs",
    "select all beams",
    "select all foundations",
    "select all rebars",
    "count rebars",
    "count columns",
    "count walls",
    "count floors",
    "count ceilings",
    "count roofs",
    "count ducts",
    "count selected ducts",
    "count all ducts in active view",
    "count lights",
    "total structural volume",
    "select all windows",
    "select all doors",
    "total room area",
    "delete all doors",
    "delete all columns",
    "delete all walls",
    "delete all ceilings",
    "delete all roofs",
    "delete all ducts",
    "delete all lights",
    "select all ducts",
    "select all pipes",
    "total duct length",
    "total pipe length",
    "total ceiling area and volume",
    "total roof area and volume",
    "select all lights",
    "select all electrical equipment",
    "total cable tray length",
    "count all sheets",
    "count all views",
    "export schedule names",
    "export all schedule data",
    "clash check",
    "report parameters",
    "report phases",
    "material takeoff wall",
    "material takeoff floor",
    "material takeoff columns",
    "material takeoff beams",
    "material takeoff roofs",
    "material takeoff stairs",
    "material takeoff curtain panel",
    "material takeoff rebar",
    "tag selection",
    "count loaded families",
    "all parameters for walls",
    "all parameters for rooms",
    "list all phases",
    "list all sheets",
    "list all views",
    "list all families",
    "list all levels",
    "list ducts in active view",
    "find unconnected fittings",
    "report elements without system assignment",
    "health check",
    "super-select walls, columns, beams",  # add any combination you like!
    # Add more as you invent them!
]


REVIEWED_ACTION_HANDLERS = {
    "select_all_ducts": select_all_ducts,
    "count_selected_ducts": count_selected_ducts,
    "count_ducts_in_active_view": count_ducts_in_active_view,
    "list_ducts_in_active_view": list_ducts_in_active_view,
    "report_total_selected_duct_length": report_total_selected_duct_length,
    "report_total_selected_duct_volume_cubic_meters": report_total_selected_duct_volume_cubic_meters,
    "find_unconnected_duct_fittings": find_unconnected_fittings,
    "report_ducts_without_system_assignment": report_ducts_without_system_assignment,
    "select_all_pipes": select_all_pipes,
    "count_selected_pipes": count_selected_pipes,
    "count_pipes_in_active_view": count_pipes_in_active_view,
    "list_pipes_in_active_view": list_pipes_in_active_view,
    "report_total_selected_pipe_length": report_total_selected_pipe_length,
    "find_unconnected_pipe_fittings": find_unconnected_pipe_fittings,
    "report_pipes_without_system_assignment": report_pipes_without_system_assignment,
    "select_all_electrical_fixtures_in_active_view": select_all_electrical_fixtures_in_active_view,
    "count_selected_fixtures_devices": count_selected_fixtures_devices,
    "list_electrical_fixtures_in_active_view": list_electrical_fixtures_in_active_view,
    "list_electrical_qa_elements_in_active_view": list_electrical_qa_elements_in_active_view,
    "report_devices_without_circuit_info": report_devices_without_circuit_info,
    "report_electrical_qa_elements_without_assignment": report_electrical_qa_elements_without_assignment,
    "list_fixtures_by_type_in_active_view": list_fixtures_by_type_in_active_view,
    "active_view_mep_summary": active_view_mep_summary,
    "count_mep_elements_in_active_view": count_mep_elements_in_active_view,
    "report_active_view_elements_by_category": report_active_view_elements_by_category,
    "report_active_view_elements_by_type": report_active_view_elements_by_type,
    "active_view_qa_report": active_view_qa_report,
    "report_missing_parameters_in_active_view": report_missing_parameters_in_active_view,
    "active_view_piping_report": active_view_piping_report,
    "active_view_hvac_report": active_view_hvac_report,
    "active_view_electrical_report": active_view_electrical_report,
    "selected_mep_system_assignment_report": selected_mep_system_assignment_report,
    "active_view_mep_system_assignment_report": active_view_mep_system_assignment_report,
    "selected_pipe_system_report": selected_pipe_system_report,
    "active_view_pipe_system_report": active_view_pipe_system_report,
    "selected_duct_system_report": selected_duct_system_report,
    "active_view_duct_system_report": active_view_duct_system_report,
    "selected_electrical_system_report": selected_electrical_system_report,
    "active_view_electrical_system_report": active_view_electrical_system_report,
    "selected_discipline_qa_report": selected_discipline_qa_report,
    "active_view_discipline_qa_report": active_view_discipline_qa_report,
    "selected_piping_qa_report": selected_piping_qa_report,
    "active_view_piping_qa_report": active_view_piping_qa_report,
    "selected_hvac_qa_report": selected_hvac_qa_report,
    "active_view_hvac_qa_report": active_view_hvac_qa_report,
    "selected_electrical_qa_report": selected_electrical_qa_report,
    "active_view_electrical_qa_report": active_view_electrical_qa_report,
    "report_selected_elements_by_category": report_selected_elements_by_category,
    "report_selected_elements_by_type": report_selected_elements_by_type,
    "count_selected_elements": count_selected_elements,
    "health_check_for_active_view_selection": health_check_for_active_view_selection,
    "report_missing_parameters_from_selection": report_missing_parameters_from_selection,
    "split_selected_pipes_dry_run": split_selected_pipes_dry_run,
    "split_selected_pipes_rollback_test": split_selected_pipes_rollback_test,
    "split_selected_pipe_reviewed_apply": split_selected_pipe_reviewed_apply,
    "split_apply_verification_report": split_apply_verification_report,
    "create_3d_view_from_selection": create_3d_view_from_selection,
    "split_selected_pipes": split_selected_pipes,
    "report_duplicates": report_duplicates,
    "remove_duplicates": remove_duplicates,
    "categories_list_and_id": categories_list_and_id,
    "select_all_elements_of_category": select_all_elements_of_category,
    "count_all_elements_of_category": count_all_elements_of_category,
    "list_all_elements_of_category": list_all_elements_of_category,
    "report_rooms_without_matching_spaces": report_rooms_without_matching_spaces,
    "report_spaces_without_matching_rooms": report_spaces_without_matching_rooms,
    "report_room_space_mismatches": report_room_space_mismatches,
    "rename_active_view": rename_active_view,
    "align_selected_tags": align_selected_tags,
    "report_total_length_selected_linear_mep": report_total_length_selected_linear_mep,
    "report_total_length_active_view_linear_mep": report_total_length_active_view_linear_mep,
    "create_pipe_schedule_by_level": create_pipe_schedule_by_level,
    "create_pipe_fitting_schedule_by_level": create_pipe_fitting_schedule_by_level,
    "create_duct_schedule_by_level": create_duct_schedule_by_level,
    "create_duct_fitting_schedule_by_level": create_duct_fitting_schedule_by_level,
    "create_conduit_schedule_by_level": create_conduit_schedule_by_level,
    "create_electrical_fixture_equipment_schedule_by_level": create_electrical_fixture_equipment_schedule_by_level,
    "create_schedule_bundle_by_level": create_schedule_bundle_by_level,
    "create_aco_pipe_schedule_from_template": create_aco_pipe_schedule_from_template,
    "create_aco_pipe_fitting_schedule_from_template": create_aco_pipe_fitting_schedule_from_template,
    "create_aco_pipe_summary_from_template": create_aco_pipe_summary_from_template,
    "create_aco_pipe_fitting_summary_from_template": create_aco_pipe_fitting_summary_from_template,
    "create_aco_14301_single_socket_pipe_schedule_from_template": create_aco_14301_single_socket_pipe_schedule_from_template,
    "create_aco_14404_single_socket_pipe_schedule_from_template": create_aco_14404_single_socket_pipe_schedule_from_template,
    "create_aco_14404_double_socket_pipe_schedule_from_template": create_aco_14404_double_socket_pipe_schedule_from_template,
    "create_aco_14301_single_socket_pipe_summary_from_template": create_aco_14301_single_socket_pipe_summary_from_template,
    "create_aco_14404_single_socket_pipe_summary_from_template": create_aco_14404_single_socket_pipe_summary_from_template,
    "create_aco_14404_double_socket_pipe_summary_from_template": create_aco_14404_double_socket_pipe_summary_from_template,
    "create_aco_prefab_schedule_bundle_from_template": create_aco_prefab_schedule_bundle_from_template,
    "scan_current_project_context": scan_current_project_context,
    "summarize_current_project_context": summarize_current_project_context,
    "check_linked_model_coordinate_health": check_linked_model_coordinate_health,
    "check_bim_basis_level_grid_health": check_bim_basis_level_grid_health,
    "project_onboarding_checklist_action": project_onboarding_checklist_action,
    "ask_agent_for_project_plan_action": ask_agent_for_project_plan_action,
    "guided_project_startup_plan_action": guided_project_startup_plan_action,
    "create_codex_task_brief_action": create_codex_task_brief_action,
}


def _normalize_deterministic_route_text(prompt):
    text = safe_str(prompt).lower().strip()
    text = re.sub(r"[\u2010-\u2015]+", "-", text)
    text = re.sub(r"\s*/\s*", " and ", text)
    text = re.sub(r"[-_]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _is_split_selected_pipes_dry_run_prompt(prompt):
    normalized = _normalize_deterministic_route_text(prompt)
    routes = [
        "split selected pipes dry run",
        "dry run split selected pipes",
        "preview split selected pipes",
        "calculate split selected pipes",
        "calculate pipe split candidates",
        "split pipe candidates",
        "selected pipe split dry run",
        "selected pipes split dry run",
        "preview selected pipe splits",
        "proposed split selected pipes",
        "pipe split dry run",
    ]
    if normalized in routes:
        return True
    if "split" in normalized and "pipe" in normalized and any(token in normalized for token in ["dry run", "preview", "candidate", "calculate", "proposed"]):
        return True
    return False


def _is_split_rollback_test_prompt(prompt):
    normalized = _normalize_deterministic_route_text(prompt)
    route_text = re.sub(r"\brollback\s+test\s+ok\b", " ", normalized)
    route_text = re.sub(r"\s+", " ", route_text).strip()
    routes = [
        "split selected pipes rollback test",
        "rollback test split selected pipes",
        "run split rollback test",
        "test split selected pipes with rollback",
        "simulate split selected pipes rollback",
        "validate split apply rollback",
        "reviewed split rollback test",
        "pipe split rollback test",
        "split pipe write api rollback test",
        "probe split selected pipes rollback",
    ]
    if route_text in routes:
        return True
    for route in routes:
        if route in route_text:
            return True
    return "rollback" in route_text and "split" in route_text and "pipe" in route_text


def _has_rollback_test_token(prompt):
    raw_prompt = safe_str(prompt)
    normalized = _normalize_deterministic_route_text(raw_prompt)
    return ROLLBACK_TEST_CONFIRMATION_TOKEN in raw_prompt or "rollback test ok" in normalized


def _split_reviewed_apply_request_kind(prompt):
    normalized = _normalize_deterministic_route_text(prompt)
    route_text = re.sub(r"\bpersistent\s+split\s+ok\b", " ", normalized)
    route_text = re.sub(r"\s+", " ", route_text).strip()
    readiness_routes = [
        "show split apply candidates",
        "show reviewed split apply candidates",
        "split apply readiness",
        "reviewed split apply readiness",
        "can apply split candidate",
        "can apply pipe split",
    ]
    apply_routes = [
        "apply split candidate",
        "apply pipe split candidate",
        "reviewed apply split candidate",
        "persist split candidate",
        "persist pipe split candidate",
        "execute split candidate",
        "execute pipe split candidate",
        "apply selected pipe split candidate",
        "reviewed pipe split apply",
    ]
    if route_text in readiness_routes:
        return "status"
    if route_text in apply_routes:
        return "apply"
    for route in readiness_routes:
        if route in route_text:
            return "status"
    for route in apply_routes:
        if route in route_text:
            return "apply"
    return None


def _has_persistent_split_token(prompt):
    raw_prompt = safe_str(prompt)
    normalized = _normalize_deterministic_route_text(raw_prompt)
    return PERSISTENT_SPLIT_CONFIRMATION_TOKEN in raw_prompt or "persistent split ok" in normalized


def _parse_split_apply_candidate_selection(prompt):
    normalized = _normalize_deterministic_route_text(prompt)
    route_text = re.sub(r"\bpersistent\s+split\s+ok\b", " ", normalized)
    route_text = re.sub(r"\s+", " ", route_text).strip()
    if re.search(r"\b(apply|split|persist|execute)\s+all\b", route_text) or re.search(r"\bcandidates?\s+\d+\s+\d+", route_text) or "all split candidates" in route_text or "selected pipe splits" in route_text:
        return {"kind": "batch", "value": None}
    number_matches = re.findall(r"\b(?:candidate|candidates)\s+(\d+)\b", route_text)
    if len(number_matches) > 1:
        return {"kind": "batch", "value": None}
    if number_matches:
        value = int(number_matches[0])
        if value > 9999 and ("pipe split candidate" in route_text or "pipe candidate" in route_text):
            return {"kind": "pipe_id", "value": value}
        return {"kind": "candidate_number", "value": value}
    pipe_match = re.search(r"\bpipe(?:\s+id)?\s+(\d+)\b", route_text)
    if pipe_match:
        return {"kind": "pipe_id", "value": int(pipe_match.group(1))}
    return {"kind": "missing", "value": None}


def _is_split_apply_verification_prompt(prompt):
    normalized = _normalize_deterministic_route_text(prompt)
    routes = [
        "verify latest split apply",
        "verify latest pipe split",
        "verify split apply result",
        "verify pipe split result",
        "verify latest reviewed split apply",
        "verify latest reviewed pipe split",
        "show latest split apply result",
        "show latest pipe split result",
        "check latest split apply",
        "check pipe split result",
        "post apply split verification",
        "post apply split verification",
        "split apply verification",
        "pipe split verification",
        "verify split result pipe",
        "verify pipe split result",
    ]
    if normalized in routes:
        return True
    for route in routes:
        if normalized.startswith(route) or route in normalized:
            return True
    return False


def _parse_explicit_split_verification_ids(prompt):
    normalized = _normalize_deterministic_route_text(prompt)
    match = re.search(r"\bverify\s+split\s+result\s+pipe\s+(\d+)\s+new\s+pipe\s+(\d+)\b", normalized)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = re.search(r"\bverify\s+pipe\s+split\s+result\s+(\d+)\s+(\d+)\b", normalized)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None, None


def _reviewed_action_confirmation_guard_request_kind(prompt):
    normalized = _normalize_deterministic_route_text(prompt)
    status_routes = [
        "show latest reviewed action",
        "show latest reviewed proposal",
        "show latest dry run",
        "reviewed action status",
        "reviewed proposal status",
        "dry run status",
        "can i apply latest action",
        "can i execute latest proposal",
    ]
    confirmation_routes = [
        "confirm reviewed action",
        "confirm latest reviewed action",
        "confirm latest proposal",
        "confirm latest dry run",
        "confirm split selected pipes",
        "apply reviewed action",
        "apply latest reviewed action",
        "execute reviewed action",
        "execute latest proposal",
        "run reviewed action",
        "approve reviewed action",
        "approve latest proposal",
    ]
    if normalized in confirmation_routes:
        return "confirmation"
    if normalized in status_routes:
        return "status"
    if any(token in normalized for token in ["confirm", "apply", "execute", "approve", "run reviewed action"]):
        if "reviewed" in normalized or "proposal" in normalized or "dry run" in normalized or "split selected pipes" in normalized:
            return "confirmation"
    if ("status" in normalized or normalized.startswith("show latest") or normalized.startswith("can i ")) and (
        "reviewed" in normalized or "proposal" in normalized or "dry run" in normalized or "latest action" in normalized
    ):
        return "status"
    return None


def execute_reviewed_action_handler(handler_name, doc, uidoc, context=None):
    handler = REVIEWED_ACTION_HANDLERS.get(handler_name)
    if handler is None:
        return None
    try:
        return handler(doc, uidoc, context)
    except TypeError:
        return handler(doc, uidoc)


def handle_public_command(prompt, doc, uidoc):
    p = prompt.lower()
    if _is_split_apply_verification_prompt(prompt):
        return "\n".join(
            [
                "[SPLIT APPLY VERIFICATION REPORT]",
                "",
                "Verification result:",
                "Not ready",
                "",
                "Reason:",
                "No latest MEP-WR-003 reviewed apply state is available in this stateless command path.",
                "",
                "Safety:",
                "- Read-only verification only.",
                "- No transaction opened.",
                "- No pipe was split.",
                "- Revit model data was not modified.",
            ]
        )
    if _split_reviewed_apply_request_kind(prompt):
        return "\n".join(
            [
                "[SPLIT SELECTED PIPE REVIEWED APPLY]",
                "",
                "Reviewed apply result:",
                "Not ready",
                "",
                "Reason:",
                "No valid split dry-run and rollback-test state is available in this stateless command path.",
                "",
                "Required previous steps:",
                "1. dry run split selected pipes",
                "2. run split rollback test ROLLBACK-TEST-OK",
                "",
                "Safety:",
                "- No transaction opened.",
                "- No pipe was split.",
                "- Revit model data was not modified.",
            ]
        )
    if _is_split_rollback_test_prompt(prompt):
        return split_selected_pipes_rollback_test(doc, uidoc, {"requested_prompt": prompt, "prompt_text": prompt})
    if _reviewed_action_confirmation_guard_request_kind(prompt):
        return "\n".join(
            [
                "[REVIEWED ACTION CONFIRMATION GUARD]",
                "",
                "Latest reviewed source:",
                "- Source type: none",
                "- Source status: unavailable",
                "",
                "Execution status:",
                "- Execution requested: true",
                "- Execution available: false",
                "- Execution performed: false",
                "",
                "Confirmation result:",
                "Not ready",
                "",
                "Reason:",
                "No reviewed action proposal or split dry-run is available in this stateless command path. Use the AI Workbench chat session after running a proposal or dry-run.",
                "",
                "Safety:",
                "- Confirmation guard only.",
                "- Read-only.",
                "- No transaction opened.",
                "- No action was applied.",
                "- No pipe was split.",
                "- Revit model data was not modified.",
            ]
        )
    if _is_split_selected_pipes_dry_run_prompt(prompt):
        return split_selected_pipes_dry_run(doc, uidoc, {"requested_prompt": prompt, "prompt_text": prompt})
    if "split" in p and "pipe" in p:
        return split_selected_pipes(doc, uidoc, {"requested_prompt": prompt, "prompt_text": prompt})
    if "remove duplicates" in p or "remove duplicate" in p or "clean duplicates" in p or "delete duplicate" in p:
        return remove_duplicates(doc, uidoc, {"requested_prompt": prompt, "prompt_text": prompt})
    if "report duplicates" in p or "find duplicates" in p or "duplicate elements" in p:
        return report_duplicates(doc, uidoc, {"requested_prompt": prompt, "prompt_text": prompt})
    if "categories list" in p or "category ids" in p or "categories list + id" in p:
        return categories_list_and_id(doc, uidoc, {"requested_prompt": prompt, "prompt_text": prompt})
    if (p.startswith("select all ") or "select all elements of category" in p) and p.strip() not in ("select all ducts", "select all pipes") and "electrical fixtures in active view" not in p:
        generic_result = select_all_elements_of_category(doc, uidoc, {"requested_prompt": prompt, "prompt_text": prompt})
        if generic_result and not str(generic_result).startswith("No matching Revit category"):
            return generic_result
    if (p.startswith("count all ") or "count all elements of category" in p) and "active view" not in p:
        generic_result = count_all_elements_of_category(doc, uidoc, {"requested_prompt": prompt, "prompt_text": prompt})
        if generic_result and not str(generic_result).startswith("No matching Revit category"):
            return generic_result
    if (p.startswith("list all ") or "list all elements of category" in p) and "active view" not in p:
        generic_result = list_all_elements_of_category(doc, uidoc, {"requested_prompt": prompt, "prompt_text": prompt})
        if generic_result and not str(generic_result).startswith("No matching Revit category"):
            return generic_result
    if "rooms without spaces" in p:
        return report_rooms_without_matching_spaces(doc, uidoc, {"requested_prompt": prompt, "prompt_text": prompt})
    if "spaces without rooms" in p:
        return report_spaces_without_matching_rooms(doc, uidoc, {"requested_prompt": prompt, "prompt_text": prompt})
    if "room to space" in p or "space vs room" in p or "room space check" in p or "rooms vs spaces" in p:
        return report_room_space_mismatches(doc, uidoc, {"requested_prompt": prompt, "prompt_text": prompt})
    if "rename active view" in p:
        return rename_active_view(doc, uidoc, {"requested_prompt": prompt, "prompt_text": prompt})
    if "align" in p and "tag" in p:
        return align_selected_tags(doc, uidoc, {"requested_prompt": prompt, "prompt_text": prompt})
    if "total length" in p and ("linear" in p or "mep" in p):
        if "active view" in p:
            return report_total_length_active_view_linear_mep(doc, uidoc, {"requested_prompt": prompt, "prompt_text": prompt})
        return report_total_length_selected_linear_mep(doc, uidoc, {"requested_prompt": prompt, "prompt_text": prompt})
    if "active view mep summary" in p or "active view mep report" in p or "count mep elements in active view" in p or "count mep in active view" in p:
        return active_view_mep_summary(doc, uidoc)
    if "report active view elements by category" in p or "active view category report" in p:
        return report_active_view_elements_by_category(doc, uidoc)
    if "report active view elements by type" in p or "active view type report" in p:
        return report_active_view_elements_by_type(doc, uidoc)
    if "active view qa report" in p or "active view health check" in p:
        return active_view_qa_report(doc, uidoc)
    if "report missing parameters in active view" in p or "missing parameters in active view" in p or "active view missing parameters" in p:
        return report_missing_parameters_in_active_view(doc, uidoc)
    if "report pipes in active view" in p or "active view pipe report" in p or "active view piping report" in p:
        return active_view_piping_report(doc, uidoc)
    if "report ducts in active view" in p or "active view duct report" in p or "active view hvac report" in p:
        return active_view_hvac_report(doc, uidoc)
    if "count electrical elements in active view" in p or "report electrical elements in active view" in p or "active view electrical report" in p:
        return active_view_electrical_report(doc, uidoc)
    if "selected piping qa report" in p or "selected pipe qa report" in p:
        return selected_piping_qa_report(doc, uidoc)
    if "active view piping qa report" in p or "active view pipe qa report" in p:
        return active_view_piping_qa_report(doc, uidoc)
    if "selected hvac qa report" in p or "selected duct qa report" in p:
        return selected_hvac_qa_report(doc, uidoc)
    if "active view hvac qa report" in p or "active view duct qa report" in p:
        return active_view_hvac_qa_report(doc, uidoc)
    if "selected electrical qa report" in p or "selected electrical circuit qa report" in p:
        return selected_electrical_qa_report(doc, uidoc)
    if "active view electrical qa report" in p or "active view electrical circuit qa report" in p:
        return active_view_electrical_qa_report(doc, uidoc)
    if "selected discipline qa report" in p or "selected mep qa rules report" in p or "check selected mep qa rules" in p:
        return selected_discipline_qa_report(doc, uidoc)
    if "active view discipline qa report" in p or "active view mep qa rules report" in p or "check active view mep qa rules" in p:
        return active_view_discipline_qa_report(doc, uidoc)
    if "selected pipe system report" in p or "check selected pipe systems" in p:
        return selected_pipe_system_report(doc, uidoc)
    if "active view pipe system report" in p or "check pipe systems in active view" in p:
        return active_view_pipe_system_report(doc, uidoc)
    if "selected duct system report" in p or "check selected duct systems" in p:
        return selected_duct_system_report(doc, uidoc)
    if "active view duct system report" in p or "check duct systems in active view" in p:
        return active_view_duct_system_report(doc, uidoc)
    if "selected electrical system report" in p or "check selected electrical systems" in p or "selected circuit report" in p:
        return selected_electrical_system_report(doc, uidoc)
    if "active view electrical system report" in p or "check electrical systems in active view" in p or "active view circuit report" in p:
        return active_view_electrical_system_report(doc, uidoc)
    if "selected mep system assignment report" in p or "selected mep systems" in p or "selected system qa report" in p or "selected elements system report" in p or "report selected elements by system" in p:
        return selected_mep_system_assignment_report(doc, uidoc)
    if "active view mep system assignment report" in p or "active view mep systems" in p or "active view system qa report" in p or "report active view elements by system" in p or "count systems in active view" in p:
        return active_view_mep_system_assignment_report(doc, uidoc)
    if "schedule bundle" in p and ("level" in p or "reference level" in p):
        return create_schedule_bundle_by_level(doc, uidoc, {"requested_prompt": prompt, "prompt_text": prompt})
    if "pipe fitting" in p and "schedule" in p and ("level" in p or "reference level" in p):
        return create_pipe_fitting_schedule_by_level(doc, uidoc, {"requested_prompt": prompt, "prompt_text": prompt})
    if "duct fitting" in p and "schedule" in p and ("level" in p or "reference level" in p):
        return create_duct_fitting_schedule_by_level(doc, uidoc, {"requested_prompt": prompt, "prompt_text": prompt})
    if "pipe" in p and "schedule" in p and ("level" in p or "reference level" in p):
        return create_pipe_schedule_by_level(doc, uidoc, {"requested_prompt": prompt, "prompt_text": prompt})
    if "duct" in p and "schedule" in p and ("level" in p or "reference level" in p):
        return create_duct_schedule_by_level(doc, uidoc, {"requested_prompt": prompt, "prompt_text": prompt})
    if "conduit" in p and "schedule" in p and ("level" in p or "reference level" in p):
        return create_conduit_schedule_by_level(doc, uidoc, {"requested_prompt": prompt, "prompt_text": prompt})
    if "electrical" in p and "schedule" in p and ("fixture" in p or "equipment" in p) and ("level" in p or "reference level" in p):
        return create_electrical_fixture_equipment_schedule_by_level(doc, uidoc, {"requested_prompt": prompt, "prompt_text": prompt})
    if "report total selected duct length" in p or "total selected duct length" in p or "length of selected ducts" in p:
        return report_total_selected_duct_length(doc, uidoc)
    if (
        "report total selected duct volume in cubic meters" in p
        or "total selected duct volume in m3" in p
        or "total selected duct volume in cubic meters" in p
        or "volume of selected ducts" in p
        or "selected duct volume" in p
    ):
        return report_total_selected_duct_volume_cubic_meters(doc, uidoc)
    if "report ducts without system assignment" in p:
        return report_ducts_without_system_assignment(doc, uidoc)
    if "count selected pipes" in p or "how many pipes are selected" in p:
        return count_selected_pipes(doc, uidoc)
    if "count pipes in active view" in p or "count all pipes in active view" in p:
        return active_view_piping_report(doc, uidoc)
    if "list pipes in active view" in p or "list all pipes in active view" in p:
        return list_pipes_in_active_view(doc, uidoc)
    if "report total selected pipe length" in p or "total selected pipe length" in p or "length of selected pipes" in p:
        return report_total_selected_pipe_length(doc, uidoc)
    if "find unconnected pipe fittings" in p or "find disconnected pipe fittings" in p:
        return find_unconnected_pipe_fittings(doc, uidoc)
    if "report pipes without system assignment" in p:
        return report_pipes_without_system_assignment(doc, uidoc)
    if "select all electrical fixtures in active view" in p or "select electrical fixtures in active view" in p:
        return select_all_electrical_fixtures_in_active_view(doc, uidoc)
    if "count selected fixtures/devices" in p or "count selected fixtures" in p or "count selected devices" in p:
        return count_selected_fixtures_devices(doc, uidoc)
    if "list electrical fixtures in active view" in p or "electrical devices in active view" in p:
        return list_electrical_fixtures_in_active_view(doc, uidoc)
    if "report devices without circuit/system info" in p or "report devices without circuit info" in p:
        return report_devices_without_circuit_info(doc, uidoc)
    if "list fixtures by type in active view" in p or "list electrical fixtures by type" in p:
        return list_fixtures_by_type_in_active_view(doc, uidoc)
    if "report selected elements by category" in p or "selection category report" in p or "selected elements by category" in p or "report selection by category" in p:
        return report_selected_elements_by_category(doc, uidoc)
    if "report selected elements by type" in p or "selection type report" in p or "selected elements by type" in p or "report selection by type" in p:
        return report_selected_elements_by_type(doc, uidoc)
    if "count selected elements" in p or "selected element count" in p or "count current selection" in p or "count selection" in p or "bim qa count selected elements" in p:
        return count_selected_elements(doc, uidoc)
    if "selection health check" in p or "health check selected elements" in p or "health check selection" in p:
        return health_check_selected_elements(doc, uidoc)
    if "health check for active view selection" in p:
        return health_check_for_active_view_selection(doc, uidoc)
    if "report missing parameters from selection" in p or "report missing parameters from selected elements" in p or "missing parameters from selection" in p or "missing selected parameters" in p:
        return report_missing_parameters_from_selection(doc, uidoc)
    if "create 3d view from selection/context" in p or "create 3d view from selection" in p or "create 3d view" in p:
        return create_3d_view_from_selection(doc, uidoc)
    if p.startswith("super-select"):
        # Example: "super-select walls, columns, beams"
        cats = re.findall(r"super-select\s+(.*)", p)
        if cats:
            cat_list = [c.strip() for c in cats[0].split(",")]
            return super_select(doc, uidoc, cat_list)
        else:
            return "Usage: super-select walls, columns, beams"
    if "select all columns" in p:
        return select_all_columns(doc, uidoc)
    if "select all walls" in p:
        return select_all_walls(doc, uidoc)
    if "select all floors" in p:
        return select_all_floors(doc, uidoc)
    if "select all ceilings" in p:
        return select_all_ceilings(doc, uidoc)
    if "select all roofs" in p:
        return select_all_roofs(doc, uidoc)
    if "select all beams" in p:
        return select_all_beams(doc, uidoc)
    if "select all foundations" in p:
        return select_all_foundations(doc, uidoc)
    if "select all rebars" in p:
        return select_all_rebars(doc, uidoc)
    if "select all cable trays" in p:
        return select_all_cabletrays(doc, uidoc)
    if "count rebars" in p:
        return count_rebars(doc, uidoc)
    if "count columns" in p:
        return count_columns(doc, uidoc)
    if "count walls" in p:
        return count_walls(doc, uidoc)
    if "count floors" in p:
        return count_floors(doc, uidoc)
    if "count ceilings" in p:
        return count_ceilings(doc, uidoc)
    if "count roofs" in p:
        return count_roofs(doc, uidoc)
    if "count ducts" in p:
        return count_ducts(doc, uidoc)
    if "count selected ducts" in p or "count the selected ducts" in p or "how many selected ducts" in p:
        return count_selected_ducts(doc, uidoc)
    if "count ducts in active view" in p or "count all ducts in active view" in p:
        return active_view_hvac_report(doc, uidoc)
    if "count lights" in p:
        return count_lights(doc, uidoc)
    if "total structural volume" in p:
        return total_structural_volume(doc, uidoc)
    if "select all windows" in p:
        return select_all_windows(doc, uidoc)
    if "select all doors" in p:
        return select_all_doors(doc, uidoc)
    if "total room area" in p:
        return total_room_area(doc, uidoc)
    if "delete all doors" in p:
        return delete_all_doors(doc, uidoc)
    if "delete all columns" in p:
        return delete_all_columns(doc, uidoc)
    if "delete all walls" in p:
        return delete_all_walls(doc, uidoc)
    if "delete all ceilings" in p:
        return delete_all_ceilings(doc, uidoc)
    if "delete all roofs" in p:
        return delete_all_roofs(doc, uidoc)
    if "delete all ducts" in p:
        return delete_all_ducts(doc, uidoc)
    if "delete all lights" in p:
        return delete_all_lights(doc, uidoc)
    if "select all ducts" in p:
        return select_all_ducts(doc, uidoc)
    if "select all pipes" in p:
        return select_all_pipes(doc, uidoc)
    if "total duct length" in p:
        return total_duct_length(doc, uidoc)
    if "total pipe length" in p:
        return total_pipe_length(doc, uidoc)
    if "select all lights" in p:
        return select_all_lights(doc, uidoc)
    if "select all electrical equipment" in p or "select all equip" in p:
        return select_all_equip(doc, uidoc)
    if "total cable tray length" in p:
        return total_cabletray_length(doc, uidoc)
    if "total ceiling area and volume" in p:
        return total_ceiling_area_volume(doc, uidoc)
    if "total roof area and volume" in p:
        return total_roof_area_volume(doc, uidoc)
    if "count all sheets" in p:
        return count_all_sheets(doc, uidoc)
    if "count all views" in p:
        return count_all_views(doc, uidoc)
    if "export schedule names" in p:
        return export_schedule_names(doc, uidoc)
    if "export all schedule data" in p:
        return export_all_schedule_data(doc, uidoc)
    if "clash check" in p or "check clashes" in p:
        return check_clashes_walls_vs_columns(doc, uidoc)
    if "clash check walls columns" in p:
        return check_clashes_walls_vs_columns(doc, uidoc)
    if "clash check ducts beams" in p:
        return check_clashes_ducts_vs_beams(doc, uidoc)
    if "report parameters" in p or "list parameters" in p:
        return report_all_parameters_of_selection(doc, uidoc)
    if "report phases" in p or "phase info" in p:
        return report_phases_of_selection(doc, uidoc)
    if "material takeoff wall" in p:
        return material_takeoff_walls(doc, uidoc)
    if "material takeoff floor" in p:
        return material_takeoff_floors(doc, uidoc)
    if "material takeoff columns" in p:
        return material_takeoff_columns(doc, uidoc)
    if "material takeoff beams" in p:
        return material_takeoff_beams(doc, uidoc)
    if "material takeoff roofs" in p:
        return material_takeoff_roofs(doc, uidoc)
    if "material takeoff stairs" in p:
        return material_takeoff_stairs(doc, uidoc)
    if "material takeoff curtain panel" in p:
        return material_takeoff_curtainpanels(doc, uidoc)
    if "material takeoff rebar" in p:
        return material_takeoff_rebar(doc, uidoc)
    if "tag selection" in p or "tag selected" in p:
        return tag_selected_elements(doc, uidoc)
    if "count loaded families" in p:
        return count_loaded_families(doc, uidoc)
    if "all parameters for walls" in p:
        return all_parameters_for_category(doc, DB.BuiltInCategory.OST_Walls)
    if "all parameters for rooms" in p:
        return all_parameters_for_category(doc, DB.BuiltInCategory.OST_Rooms)
    if "all parameters for" in p and "category" in p:
        # Parse out BuiltInCategory from user input
        # e.g. "all parameters for category OST_Floors"
        cat_name = p.split("category")[-1].strip()
        try:
            category = getattr(DB.BuiltInCategory, cat_name)
            return all_parameters_for_category(doc, category)
        except:
            return "Unknown category: {}".format(cat_name)
    if "list all phases" in p or "show phases" in p:
        return list_all_phases(doc, uidoc)
    if "list all sheets" in p:
        return list_all_sheets(doc, uidoc)
    if "list all views" in p:
        return list_all_views(doc, uidoc)
    if "list all families" in p:
        return list_all_families(doc, uidoc)
    if "list all levels" in p:
        return list_all_levels(doc, uidoc)
    if "list ducts in active view" in p or "list all ducts in active view" in p:
        return list_ducts_in_active_view(doc, uidoc)
    if "find unconnected fittings" in p:
        return find_unconnected_fittings(doc, uidoc)
    if "report elements without system assignment" in p:
        return report_elements_without_system_assignment(doc, uidoc)
    if "health check" in p or "model audit" in p:
        return model_health_check(doc, uidoc)

    # Add more keyword->function mappings here
    return None  # No public command matched


def sanitize_llm_code(code):
    """
    Cleans LLM-generated code for safe execution in the Revit pyRevit context.
    - Removes extra imports and context assignments
    - Keeps only actual command logic
    """
    lines = code.splitlines()
    keep = []
    skip_prefixes = [
        "import DB",  # LLM import, not needed
        "import revit",  # Unneeded imports
        "from pyrevit",  # Unneeded imports
        "uidoc =",  # LLM context assignment
        "doc =",  # LLM context assignment
        "DB.Application.",  # Not valid in IronPython
        "DB.ActiveUIDocument",  # Not valid as assignment
        "#",  # Usually explanations, skip
        "import Autodesk.",  # Also not needed
        "import clr",  # Already handled
    ]
    for line in lines:
        l = line.strip()
        # Remove lines that are just triple backticks or language markers
        if l.startswith("```"):
            continue
        # Remove unwanted lines
        if any(l.startswith(prefix) for prefix in skip_prefixes):
            continue
        # Remove empty lines (optional)
        if l == "":
            continue
        keep.append(line)
    return "\n".join(keep)


def llm_output_safety_filter(code):
    banned_patterns = [
        "doc.Database",
        "doc.Levels",
        "NewGeometryReference",
        "UIDoc.ActiveDoc",
        "DB.ActiveUIDocument",
        "UIApplication",
        "revitUIApplication",
        "Dynamo",
        "Rhino",
        "import revit",
        "SetCamera",
        "GetInput",
    ]
    for pattern in banned_patterns:
        if pattern in code:
            return "# Not possible with current API"
    return code


def run_code_in_revit(code, doc, uidoc):
    local_vars = {"doc": doc, "uidoc": uidoc, "DB": DB}
    try:
        exec(code, globals(), local_vars)
        message = "Code executed successfully."
        if "result" in local_vars and local_vars["result"] is not None:
            message = str(local_vars["result"])

        undo_context = local_vars.get("undo_context")
        if not undo_context:
            new_sheet = local_vars.get("new_sheet")
            if new_sheet is not None and str(message).lower().startswith("created sheet:"):
                try:
                    undo_context = {
                        "action_id": "create-sheet-reviewed-template",
                        "action_title": "Create sheet",
                        "role": "modifying",
                        "document_identity": _document_identity(doc),
                        "created_sheet_id": new_sheet.Id.IntegerValue,
                        "created_sheet_number": getattr(new_sheet, "SheetNumber", None),
                        "created_sheet_name": getattr(new_sheet, "Name", None),
                        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "session_marker": "reviewed-current-session",
                        "undo_available": True,
                    }
                except:
                    undo_context = None
        if undo_context:
            return {"message": message, "undo_context": undo_context}
        return message
    except Exception as e:
        return "Error executing code: {}".format(str(e))


def execution_result_message(execution_result):
    if isinstance(execution_result, dict):
        return str(execution_result.get("message", ""))
    return str(execution_result)


class ApprovedRecipeMetadataDialog(WinForms.Form):
    def __init__(self, defaults):
        WinForms.Form.__init__(self)
        self.Text = "Save Approved Recipe"
        self.Width = 420
        self.Height = 340
        self.FormBorderStyle = WinForms.FormBorderStyle.FixedDialog
        self.StartPosition = WinForms.FormStartPosition.CenterScreen
        self.MaximizeBox = False
        self.MinimizeBox = False
        self.AcceptButton = None
        self.CancelButton = None
        self.metadata = None

        labels = [
            ("ID", "id", defaults.get("id", "")),
            ("Title", "title", defaults.get("title", "")),
            ("Category", "category", defaults.get("category", "")),
            ("Role", "role", defaults.get("role", "")),
            ("Risk Level", "risk_level", defaults.get("risk_level", "")),
            ("Source Prompt", "source_prompt", defaults.get("source_prompt", "")),
        ]

        self.inputs = {}
        top = 15
        for caption, key, value in labels:
            label = WinForms.Label()
            label.Text = caption
            label.Left = 12
            label.Top = top + 4
            label.Width = 95
            self.Controls.Add(label)

            if key in ("category", "role", "risk_level"):
                control = WinForms.ComboBox()
                control.Left = 115
                control.Top = top
                control.Width = 270
                control.DropDownStyle = WinForms.ComboBoxStyle.DropDownList
                if key == "category":
                    for item in [
                        "Delete",
                        "Count",
                        "Reports",
                        "Lists",
                        "Materials",
                        "Tags / Tools",
                        "Select All",
                        "Totals",
                        "Clash Check",
                        "Approved Recipes",
                    ]:
                        control.Items.Add(item)
                elif key == "role":
                    for item in ["read", "modify"]:
                        control.Items.Add(item)
                elif key == "risk_level":
                    for item in ["low", "medium", "high"]:
                        control.Items.Add(item)
                if value:
                    try:
                        control.SelectedItem = value
                    except:
                        pass
                if control.SelectedItem is None and control.Items.Count > 0:
                    control.SelectedIndex = 0
            else:
                control = WinForms.TextBox()
                control.Left = 115
                control.Top = top
                control.Width = 270
                control.Text = value
            self.inputs[key] = control
            self.Controls.Add(control)
            top += 36

        self.enabled_checkbox = WinForms.CheckBox()
        self.enabled_checkbox.Text = "Enabled"
        self.enabled_checkbox.Left = 115
        self.enabled_checkbox.Top = top + 2
        self.enabled_checkbox.Checked = bool(defaults.get("enabled", True))
        self.Controls.Add(self.enabled_checkbox)
        top += 38

        save_button = WinForms.Button()
        save_button.Text = "Save"
        save_button.Left = 225
        save_button.Top = top
        save_button.Width = 75
        save_button.Click += self._on_save
        self.Controls.Add(save_button)

        cancel_button = WinForms.Button()
        cancel_button.Text = "Cancel"
        cancel_button.Left = 310
        cancel_button.Top = top
        cancel_button.Width = 75
        cancel_button.Click += self._on_cancel
        self.Controls.Add(cancel_button)

    def _collect_metadata(self):
        metadata = {}
        for key, control in self.inputs.items():
            metadata[key] = (control.Text or "").strip()
        metadata["enabled"] = bool(self.enabled_checkbox.Checked)
        return metadata

    def _on_save(self, sender, args):
        metadata = self._collect_metadata()
        required_keys = ["id", "title", "category", "role", "risk_level", "source_prompt"]
        missing = [key for key in required_keys if not metadata.get(key)]
        if missing:
            WinForms.MessageBox.Show(
                "Required metadata is missing: {0}".format(", ".join(missing)),
                "ModelMind",
                WinForms.MessageBoxButtons.OK,
                WinForms.MessageBoxIcon.Warning,
            )
            return
        self.metadata = metadata
        self.DialogResult = WinForms.DialogResult.OK
        self.Close()

    def _on_cancel(self, sender, args):
        self.DialogResult = WinForms.DialogResult.Cancel
        self.Close()


# === WPF Window Logic ===


class OllamaAIChat(forms.WPFWindow):
    def __init__(self, xaml_path):
        forms.WPFWindow.__init__(self, xaml_path)
        self.catalog = PromptCatalog(PROMPT_CATALOG_PATH, APPROVED_RECIPES_PATH)
        self.settings_store = LocalSettingsStore(WINDOW_SETTINGS_FILE)
        self.settings = self.settings_store.load(
            {
                "theme": "light",
                "recent_prompts": [],
                "window": {
                    "width": 1100.0,
                    "height": 640.0,
                    "left": None,
                    "top": None,
                },
            }
        )
        self.current_theme = self.settings.get("theme", "light")
        self.agent_session = AgentSession(self.catalog.get_agent_commands())
        self.model = DEFAULT_MODEL
        self.planner_model = DEFAULT_PLANNER_MODEL
        self.pending_ai_code = None
        self.pending_ai_prompt = ""
        self.pending_source_entry = None
        self.selected_prompt_entry = None
        self.pending_validated_code = None
        self.last_successful_reviewed_code = None
        self.last_reviewed_recipe_metadata = None
        self.last_provider_notice = None
        self.current_matched_action_id = ""
        self.latest_project_context = None
        self.latest_project_context_timestamp = None
        self.latest_project_context_scan_depth = None
        self.latest_project_context_doc_title = None
        self.latest_project_context_active_view_id = None
        self.latest_project_context_summary = ""
        self.latest_codex_brief = ""
        self.latest_deterministic_report = None
        self.latest_chat_output_is_deterministic_report = False
        self.latest_reviewed_action_proposal = None
        self.latest_split_pipe_dry_run = None
        self.latest_reviewed_action_confirmation_guard = None
        self.latest_split_rollback_test_state = None
        self.latest_split_reviewed_apply_state = None
        self.latest_split_apply_verification_state = None

        self.populate_model_selector()
        self.ModelSelector.SelectionChanged += self.on_model_selected
        self.refresh_model_info()

        # Ollama Chat: low-risk conversational help and prompt experimentation.
        self.SendButton.Click += self.on_send_chat
        if hasattr(self, "ProjectScanButton"):
            self.ProjectScanButton.Click += self.on_project_scan
        if hasattr(self, "ProjectRefreshButton"):
            self.ProjectRefreshButton.Click += self.on_project_scan
        if hasattr(self, "ProjectAgentPlanButton"):
            self.ProjectAgentPlanButton.Click += self.on_project_agent_plan
        if hasattr(self, "ProjectCodexBriefButton"):
            self.ProjectCodexBriefButton.Click += self.on_project_codex_brief
        for quick_name in [
            "ProjectQuickSummaryButton",
            "ProjectQuickOnboardingButton",
            "ProjectQuickLevelsButton",
            "ProjectQuickViewsButton",
            "ProjectQuickSheetsButton",
            "ProjectQuickLinksButton",
            "ProjectQuickLinkCoordinatesButton",
            "ProjectQuickLevelsGridsButton",
            "ProjectQuickImportsButton",
            "ProjectQuickCategoriesButton",
            "ProjectQuickSchedulesButton",
            "ProjectQuickEmptySchedulesButton",
            "ProjectQuickWarningsButton",
            "ProjectQuickIssuesButton",
            "ProjectQuickFirstChecksButton",
        ]:
            if hasattr(self, quick_name):
                getattr(self, quick_name).Click += self.on_project_context_quick_action

        # ModelMind: primary end-user workflow for deterministic and semi-generative tasks.
        self.ModelMindSendButton.Click += self.on_modelmind_send
        self.ApproveCodeButton.Click += self.on_approve_code
        self.SaveRecipeButton.Click += self.on_save_recipe
        self.ModelMindUndoButton.Click += self.on_modelmind_undo
        self.ToggleReviewedCodeButton.Click += self.on_toggle_reviewed_code
        self.ModelMindInput.KeyDown += self.on_modelmindinput_keydown
        self.PromptCatalogSearchBox.TextChanged += self.on_prompt_catalog_search_changed
        self.PromptCatalogClearButton.Click += self.on_prompt_catalog_clear
        self.PromptTreeExpandButton.Click += self.on_prompt_tree_expand_all
        self.PromptTreeCollapseButton.Click += self.on_prompt_tree_collapse_all
        self.PromptTree.MouseDoubleClick += self.on_prompt_tree_doubleclick
        self.PromptTree.KeyDown += self.on_prompt_tree_keydown
        self.PromptTree.SelectedItemChanged += self.on_prompt_tree_selected
        self.ModelMindInput.TextChanged += self.on_modelmind_input_changed

        # AI Agent: advanced planning/execution surface with destructive actions off by default.
        self.AgentRunButton.Click += self.on_agent_run
        self.AgentExecuteButton.Click += self.on_agent_execute
        self.AgentToggleCommandButton.Click += self.on_agent_toggle_command
        self.AgentResetCommandsButton.Click += self.on_agent_reset_commands
        self.AgentUndoButton.Click += self.on_agent_undo
        self.AgentCommandSelector.SelectionChanged += self.on_agent_step_selected
        self.AgentRuntimeSelector.SelectionChanged += self.on_planner_mode_changed
        self.AgentAllowDestructive.Checked += self.on_agent_destructive_toggled
        self.AgentAllowDestructive.Unchecked += self.on_agent_destructive_toggled

        # Window controls.
        self.UpgradeButton.Click += self.on_upgrade_model
        self.CloseButton.Click += self.on_close
        self.ThemeToggleButton.Click += self.on_toggle_theme
        self.Loaded += self.on_window_loaded
        self.Closing += self.on_window_closing

        if hasattr(self, "HeaderBar"):
            self.HeaderBar.MouseLeftButtonDown += self._on_header_drag

        self.ApproveCodeButton.IsEnabled = False
        self.SaveRecipeButton.IsEnabled = False
        self.ModelMindUndoButton.IsEnabled = False
        self.ToggleReviewedCodeButton.IsEnabled = False
        self.AgentAllowDestructive.IsChecked = False
        self.AgentUndoButton.IsEnabled = False
        self.populate_prompt_tree()
        self.populate_agent_command_selector()
        self.configure_planner_provider()
        self.apply_theme(self.current_theme)
        self._restore_window_geometry()
        self.update_window_status("idle")
        self.update_reviewed_code_state("draft", "Awaiting reviewed code.")
        self.update_agent_warning()
        self.set_agent_status(self.agent_session.status)
        self.update_prompt_details(None)
        self.load_bootstrap_project_context()

    def _set_thinking(self, text):
        try:
            self.ThinkingLabel.Text = text
        except:
            try:
                self.ThinkingLabel.Content = text
            except:
                pass

    def show_busy(self):
        self.BusyBar.Visibility = System.Windows.Visibility.Visible
        self.ThinkingLabel.Visibility = System.Windows.Visibility.Visible

    def hide_busy(self):
        self.BusyBar.Visibility = System.Windows.Visibility.Collapsed
        self.ThinkingLabel.Visibility = System.Windows.Visibility.Collapsed

    def _window_settings(self):
        window_settings = self.settings.get("window", {})
        if not isinstance(window_settings, dict):
            window_settings = {}
            self.settings["window"] = window_settings
        return window_settings

    def _restore_window_geometry(self):
        window_settings = self._window_settings()
        try:
            width = float(window_settings.get("width") or 1100.0)
            height = float(window_settings.get("height") or 640.0)
            self.Width = max(width, float(self.MinWidth))
            self.Height = max(height, float(self.MinHeight))
        except:
            pass
        try:
            left = window_settings.get("left")
            top = window_settings.get("top")
            if left is not None and top is not None:
                self.Left = float(left)
                self.Top = float(top)
        except:
            pass

    def _persist_window_geometry(self):
        try:
            window_settings = self._window_settings()
            window_settings["width"] = float(self.Width)
            window_settings["height"] = float(self.Height)
            window_settings["left"] = float(self.Left)
            window_settings["top"] = float(self.Top)
            self.settings_store.save(self.settings)
        except:
            pass

    def on_window_loaded(self, sender, args):
        self._restore_window_geometry()

    def on_window_closing(self, sender, args):
        self._persist_window_geometry()

    def _build_recent_prompt_entry(self, prompt_text):
        prompt_text = (prompt_text or "").strip()
        if not prompt_text:
            return None
        source_entry = self.resolve_prompt_entry(prompt_text) or {}
        title = source_entry.get("title") or prompt_text
        category = source_entry.get("category") or "QA / BIM"
        group = source_entry.get("group") or "Recent"
        return {
            "id": "recent-{0}".format(slugify_text(prompt_text)[:40]),
            "title": title,
            "category": category,
            "group": group,
            "role": source_entry.get("role", "read"),
            "risk_level": source_entry.get("risk_level", "low"),
            "canonical_prompt": source_entry.get("canonical_prompt", source_entry.get("prompt_text", prompt_text)),
            "prompt_text": source_entry.get("prompt_text", prompt_text),
            "aliases": list(source_entry.get("aliases") or source_entry.get("planner_aliases") or []),
            "example_prompts": list(source_entry.get("example_prompts") or []),
            "validation_state": source_entry.get("validation_state", "recent"),
            "visible_in_modelmind": False,
            "available_to_agent": bool(
                source_entry.get(
                    "available_to_agent",
                    bool(source_entry.get("deterministic_handler") or source_entry.get("reviewed_steps")),
                )
            ),
            "deterministic_handler": source_entry.get("deterministic_handler", ""),
            "reviewed_steps": list(source_entry.get("reviewed_steps") or []),
            "source": "recent_prompt",
        }

    def remember_recent_prompt(self, prompt_text):
        entry = self._build_recent_prompt_entry(prompt_text)
        if not entry:
            return
        recent = []
        for item in self.settings.get("recent_prompts", []):
            if item.get("prompt_text") != entry.get("prompt_text"):
                recent.append(item)
        recent.insert(0, entry)
        self.settings["recent_prompts"] = recent[:8]
        self.settings_store.save(self.settings)

    def get_recent_prompt_entries(self):
        recent_entries = []
        for item in self.settings.get("recent_prompts", []):
            if isinstance(item, dict) and item.get("prompt_text"):
                recent_entries.append(item)
        return recent_entries

    def populate_prompt_list(self):
        if not hasattr(self, "PromptListBox"):
            return
        self.PromptListBox.Items.Clear()
        for cmd in MODELMIND_COMMANDS:
            self.PromptListBox.Items.Add(cmd)

    def _brush(self, color_value):
        import System.Windows.Media as Media

        return Media.BrushConverter().ConvertFromString(color_value)

    def _apply_control_style(self, control_name, background=None, foreground=None, border=None):
        control = getattr(self, control_name, None)
        if control is None:
            return
        try:
            if background is not None and hasattr(control, "Background"):
                control.Background = self._brush(background)
            if foreground is not None and hasattr(control, "Foreground"):
                control.Foreground = self._brush(foreground)
            if border is not None and hasattr(control, "BorderBrush"):
                control.BorderBrush = self._brush(border)
        except:
            pass

    def _set_theme_resource(self, resource_name, color_value):
        try:
            self.Resources[resource_name] = self._brush(color_value)
        except:
            pass

    def _apply_button_state_style(self, button_name, enabled, active_bg, active_fg):
        palette = THEMES.get(self.current_theme, THEMES["light"])
        button = getattr(self, button_name, None)
        if button is None:
            return
        button.IsEnabled = enabled
        if enabled:
            self._apply_control_style(button_name, active_bg, active_fg, active_bg)
            try:
                button.Opacity = 1.0
            except:
                pass
        else:
            self._apply_control_style(
                button_name,
                palette["disabled_bg"],
                palette["disabled_fg"],
                palette["border"],
            )
            try:
                button.Opacity = 1.0
            except:
                pass

    def refresh_action_button_states(self):
        selected_agent_step = self.get_selected_agent_step() if hasattr(self, "AgentCommandSelector") else None
        has_plan = self.agent_session.has_plan() if hasattr(self, "agent_session") else False
        can_execute, execute_reason = self._get_execute_plan_status() if hasattr(self, "agent_session") else (False, "")
        if hasattr(self, "AgentToggleCommandButton"):
            self.AgentToggleCommandButton.IsEnabled = bool(has_plan and selected_agent_step)
            try:
                self.AgentToggleCommandButton.ToolTip = (
                    "Enable or disable the selected reviewed plan step for this session."
                    if self.AgentToggleCommandButton.IsEnabled
                    else "Available only when a current reviewed plan step is selected."
                )
            except:
                pass
        if hasattr(self, "AgentResetCommandsButton"):
            self.AgentResetCommandsButton.IsEnabled = bool(has_plan)
        if hasattr(self, "AgentExecuteButton"):
            self.AgentExecuteButton.IsEnabled = bool(can_execute)
            try:
                self.AgentExecuteButton.ToolTip = execute_reason
            except:
                pass
        if hasattr(self, "AgentUndoButton"):
            self.AgentUndoButton.IsEnabled = bool(
                hasattr(self, "agent_session") and self.agent_session.has_undo_context()
            )
            try:
                self.AgentUndoButton.ToolTip = (
                    "Undo the last reversible modifying action in this session."
                    if self.AgentUndoButton.IsEnabled
                    else "Undo is available only when a real reversible modifying action completed successfully in this session."
                )
            except:
                pass
        if hasattr(self, "ModelMindUndoButton"):
            self.ModelMindUndoButton.IsEnabled = bool(
                hasattr(self, "agent_session") and self.agent_session.has_undo_context()
            )
            try:
                self.ModelMindUndoButton.ToolTip = (
                    "Undo the last reversible reviewed action in this session."
                    if self.ModelMindUndoButton.IsEnabled
                    else "Undo is available only when a real reversible reviewed action completed successfully in this session."
                )
            except:
                pass

        self._apply_button_state_style(
            "AgentExecuteButton",
            bool(self.AgentExecuteButton.IsEnabled),
            THEMES.get(self.current_theme, THEMES["light"])["accent_alt"],
            "#ffffff",
        )
        self._apply_button_state_style(
            "AgentUndoButton",
            bool(self.AgentUndoButton.IsEnabled),
            THEMES.get(self.current_theme, THEMES["light"])["panel_alt"],
            THEMES.get(self.current_theme, THEMES["light"])["text"],
        )
        self._apply_button_state_style(
            "ModelMindUndoButton",
            bool(self.ModelMindUndoButton.IsEnabled),
            THEMES.get(self.current_theme, THEMES["light"])["panel_alt"],
            THEMES.get(self.current_theme, THEMES["light"])["text"],
        )
        self._apply_button_state_style(
            "ApproveCodeButton",
            bool(self.ApproveCodeButton.IsEnabled),
            "#e17055",
            "#ffffff",
        )
        self._apply_button_state_style(
            "SaveRecipeButton",
            bool(self.SaveRecipeButton.IsEnabled),
            "#3b82f6",
            "#ffffff",
        )
        self._apply_button_state_style(
            "ToggleReviewedCodeButton",
            bool(self.ToggleReviewedCodeButton.IsEnabled),
            "#475569",
            "#ffffff",
        )
        self._apply_button_state_style(
            "AgentToggleCommandButton",
            bool(self.AgentToggleCommandButton.IsEnabled),
            THEMES.get(self.current_theme, THEMES["light"])["panel_alt"],
            THEMES.get(self.current_theme, THEMES["light"])["text"],
        )
        self._apply_button_state_style(
            "AgentResetCommandsButton",
            bool(self.AgentResetCommandsButton.IsEnabled),
            THEMES.get(self.current_theme, THEMES["light"])["panel_alt"],
            THEMES.get(self.current_theme, THEMES["light"])["text"],
        )

    def get_selected_planner_mode(self):
        selected = self.AgentRuntimeSelector.SelectedItem
        if selected is None:
            return "local"
        text = ""
        try:
            text = selected.Content
        except:
            text = str(selected)
        return "cloud" if "OpenAI" in str(text) else "local"

    def update_planner_provider_ui(self, state, detail=None):
        diagnostics = self.cloud_provider_state or {}
        label = "Planner provider: Local deterministic planner"
        if state == "provider_ready":
            label = "Planner provider: OpenAI ready"
        elif state == "missing_openai_module":
            label = "Planner provider: OpenAI module missing"
        elif state == "client_init_failed":
            label = "Planner provider: OpenAI client init failed"
        elif state == "key_present":
            label = "Planner provider: OpenAI key present"
        elif state == "missing_key":
            label = "Planner provider: OpenAI missing key"
        elif state == "auth_failed":
            label = "Planner provider: OpenAI auth failed"
        elif state == "network_failed":
            label = "Planner provider: OpenAI network failed"
        elif state == "request_failed":
            label = "Planner provider: OpenAI request failed"
        elif state == "local_only":
            label = "Planner provider: Local deterministic planner"
        elif state == "local":
            label = "Planner provider: Local deterministic planner"
        try:
            self.AgentProviderLabel.Text = label
            if detail:
                self.AgentProviderLabel.ToolTip = detail
        except:
            pass
        try:
            diagnostic = "Key present: {0} | Provider reachable: {1} | Last error: {2}".format(
                "yes" if diagnostics.get("key_present") else "no",
                "yes" if diagnostics.get("provider_reachable") else "no",
                diagnostics.get("last_error_category") or "none",
            )
            if state == "provider_ready":
                self.AgentProviderHelp.Text = (
                    "OpenAI planning is available for intent normalization only. "
                    "Execution still runs through reviewed deterministic actions. "
                    + diagnostic
                )
            elif state == "missing_key":
                self.AgentProviderHelp.Text = (
                    "Cloud planning is unavailable because OPENAI_API_KEY is missing from the current process environment. "
                    "Local deterministic planning remains available. "
                    + diagnostic
                )
            elif state == "missing_openai_module":
                self.AgentProviderHelp.Text = (
                    "OPENAI_API_KEY is visible, but the Python runtime used by the cloud planner cannot import the openai module. "
                    "Local deterministic planning remains available. "
                    + diagnostic
                )
            elif state == "client_init_failed":
                self.AgentProviderHelp.Text = (
                    "OPENAI_API_KEY is visible, but OpenAI client initialization failed in the runtime used by the cloud planner. "
                    "Local deterministic planning remains available. "
                    + diagnostic
                )
            elif state == "auth_failed":
                self.AgentProviderHelp.Text = (
                    "Cloud planning reached OpenAI but authentication or permission failed. "
                    "Local deterministic planning remains available. "
                    + diagnostic
                )
            elif state == "network_failed":
                self.AgentProviderHelp.Text = (
                    "Cloud planning could not reach OpenAI due to a network or timeout issue. "
                    "Local deterministic planning remains available. "
                    + diagnostic
                )
            elif state == "request_failed":
                self.AgentProviderHelp.Text = (
                    "Cloud planning failed due to request, quota, or model configuration issues. "
                    "Local deterministic planning remains available. "
                    + diagnostic
                )
            elif state == "key_present":
                self.AgentProviderHelp.Text = (
                    "OpenAI key is present. Cloud planning remains a reviewed normalization step only. "
                    + diagnostic
                )
            else:
                self.AgentProviderHelp.Text = (
                    "Local deterministic planning is active. Cloud planning is optional and never executes raw code. "
                    + diagnostic
                )
        except:
            pass

    def configure_planner_provider(self):
        provider_state = get_openai_provider_state()
        self.cloud_provider_state = provider_state
        selected_mode = self.get_selected_planner_mode()
        try:
            if self.AgentRuntimeSelector.Items.Count > 1:
                openai_item = self.AgentRuntimeSelector.Items[1]
                openai_item.IsEnabled = bool(provider_state.get("key_present"))
        except:
            pass

        if provider_state.get("state") == "missing_key" and selected_mode == "cloud":
            self.AgentRuntimeSelector.SelectedIndex = 0
            selected_mode = "local"

        if selected_mode == "cloud" and provider_state.get("state") == "provider_ready":
            self.update_planner_provider_ui("provider_ready", provider_state.get("message"))
        else:
            state = "local"
            if selected_mode == "cloud":
                state = provider_state.get("state", "request_failed")
            elif provider_state.get("state") == "missing_key":
                state = "local_only"
            self.update_planner_provider_ui(state, provider_state.get("message"))

        if provider_state.get("state") == "missing_key":
            try:
                notice = "{0}\nSet OPENAI_API_KEY in the environment to enable cloud planning.".format(
                    provider_state.get("message", "Cloud unavailable")
                )
                if notice != self.last_provider_notice:
                    self.AgentHistory.AppendText("{0}\n\n".format(notice))
                    self.last_provider_notice = notice
            except:
                pass
        elif provider_state.get("state") in ("auth_failed", "network_failed", "request_failed"):
            try:
                notice = "{0}\nLocal deterministic planning remains available.".format(
                    provider_state.get("message", "Cloud planner request failed.")
                )
                if notice != self.last_provider_notice:
                    self.AgentHistory.AppendText("{0}\n\n".format(notice))
                    self.last_provider_notice = notice
            except:
                pass
        elif provider_state.get("state") in ("missing_openai_module", "client_init_failed"):
            try:
                notice = "{0}\nUse 'cloud planner self test' in AI Agent to inspect the runtime and dependency state.".format(
                    provider_state.get("message", "Cloud planner initialization failed.")
                )
                if notice != self.last_provider_notice:
                    self.AgentHistory.AppendText("{0}\n\n".format(notice))
                    self.last_provider_notice = notice
            except:
                pass

    def _is_cloud_self_test_request(self, goal):
        goal_text = (goal or "").strip().lower()
        return (
            "cloud planner self test" in goal_text
            or "cloud self test" in goal_text
            or "planner self test" in goal_text
            or "openai self test" in goal_text
        )

    def _format_provider_self_test(self, result):
        return (
            "Cloud planner self test\n"
            "env_key_present: {0}\n"
            "openai_module_importable: {1}\n"
            "client_init_ok: {2}\n"
            "test_request_ok: {3}\n"
            "failure_category: {4}\n"
            "failure_message_safe: {5}\n"
            "runtime_executable: {6}\n"
            "runtime_version: {7}\n"
            "runtime_command: {8}\n"
        ).format(
            "yes" if result.get("env_key_present") else "no",
            "yes" if result.get("openai_module_importable") else "no",
            "yes" if result.get("client_init_ok") else "no",
            "yes" if result.get("test_request_ok") else "no",
            result.get("failure_category", ""),
            result.get("failure_message_safe", ""),
            result.get("runtime_executable", ""),
            result.get("runtime_version", ""),
            result.get("runtime_command", ""),
        )

    def run_cloud_planner_self_test(self):
        result = get_openai_provider_self_test()
        self.cloud_provider_state = get_openai_provider_state()
        self.update_planner_provider_ui(
            self.cloud_provider_state.get("state", "local"),
            self.cloud_provider_state.get("message"),
        )
        self.AgentHistory.AppendText("{0}\n".format(self._format_provider_self_test(result)))
        if result.get("failure_category") == "provider_ready":
            self.set_agent_status("idle")
            self.update_window_status("idle", "Cloud planner self test passed")
        else:
            self.set_agent_status("failed")
            self.update_window_status("failed", "Cloud planner self test")

    def apply_theme(self, theme_name):
        palette = THEMES.get(theme_name, THEMES["light"])
        self.current_theme = theme_name
        self._set_theme_resource("ComboBackgroundBrush", palette["dropdown_bg"])
        self._set_theme_resource("ComboForegroundBrush", palette["dropdown_fg"])
        self._set_theme_resource("ComboBorderBrush", palette["border"])
        self._set_theme_resource("ComboItemBackgroundBrush", palette["dropdown_item_bg"])
        self._set_theme_resource("ComboItemForegroundBrush", palette["dropdown_item_fg"])
        self._set_theme_resource("ComboItemHighlightBrush", palette["dropdown_highlight_bg"])
        self._set_theme_resource("ComboItemHighlightForegroundBrush", palette["dropdown_highlight_fg"])
        self._set_theme_resource("TreeBackgroundBrush", palette["tree_bg"])
        self._set_theme_resource("TreeForegroundBrush", palette["tree_fg"])
        self._set_theme_resource("TreeItemHoverBrush", palette["tree_hover_bg"])
        self._set_theme_resource("TreeItemSelectedBrush", palette["tree_selected_bg"])
        self._set_theme_resource("TreeItemSelectedForegroundBrush", palette["tree_selected_fg"])
        self._set_theme_resource("TreeBorderBrush", palette["border"])
        self._apply_control_style("MainWindow", palette["window_bg"], palette["text"])
        self._apply_control_style("MainBorder", palette["panel_bg"], palette["text"], palette["border"])
        self._apply_control_style("HeaderBar", palette["panel_bg"], palette["text"], None)
        self._apply_control_style("HeaderTitle", None, palette["text"])
        self._apply_control_style("HeaderSubtitle", None, palette["muted"])
        self._apply_control_style("ModelInfo", None, palette["accent"])
        self._apply_control_style("StatusLabel", None, palette["accent"])
        self._apply_control_style("ThinkingLabel", None, palette["accent"])
        self._apply_control_style("BusyBar", palette["panel_alt"], None, None)
        self._apply_control_style("CloseButton", palette["accent"], "#ffffff", palette["accent"])
        self._apply_control_style("ThemeToggleButton", palette["panel_alt"], palette["text"], palette["border"])
        self._apply_control_style("UpgradeButton", palette["panel_alt"], palette["accent"], palette["accent"])
        self._apply_control_style("ModelSelector", palette["panel_alt"], palette["text"], palette["border"])
        self._apply_control_style("MainTabs", palette["panel_bg"], palette["text"], palette["border"])
        self._apply_control_style("ChatHelpText", None, palette["muted"])
        self._apply_control_style("ChatHistory", palette["panel_alt"], palette["text"], palette["border"])
        self._apply_control_style("ChatInput", palette["panel_alt"], palette["text"], palette["border"])
        self._apply_control_style("SendButton", palette["accent"], "#ffffff", palette["accent"])
        self._apply_control_style("ProjectContextStatusText", None, palette["muted"])
        self._apply_control_style("ProjectContextPanelBorder", palette["panel_alt"], palette["text"], palette["border"])
        self._apply_control_style("ProjectContextTitleText", None, palette["text"])
        self._apply_control_style("ProjectContextSplitter", palette["panel_alt"], palette["text"], palette["border"])
        self._apply_control_style("ProjectContextTree", palette["panel_alt"], palette["text"], palette["border"])
        self._apply_control_style("ProjectScanButton", palette["accent"], "#ffffff", palette["accent"])
        self._apply_control_style("ProjectRefreshButton", palette["panel_alt"], palette["text"], palette["border"])
        self._apply_control_style("ProjectAgentPlanButton", "#6c5ce7", "#ffffff", "#6c5ce7")
        self._apply_control_style("ProjectCodexBriefButton", "#475569", "#ffffff", "#475569")
        for name in [
            "ProjectQuickSummaryButton",
            "ProjectQuickOnboardingButton",
            "ProjectQuickLevelsButton",
            "ProjectQuickViewsButton",
            "ProjectQuickSheetsButton",
            "ProjectQuickLinksButton",
            "ProjectQuickLinkCoordinatesButton",
            "ProjectQuickLevelsGridsButton",
            "ProjectQuickImportsButton",
            "ProjectQuickCategoriesButton",
            "ProjectQuickSchedulesButton",
            "ProjectQuickEmptySchedulesButton",
            "ProjectQuickWarningsButton",
            "ProjectQuickIssuesButton",
            "ProjectQuickFirstChecksButton",
        ]:
            self._apply_control_style(name, palette["panel_alt"], palette["text"], palette["border"])
        self._apply_control_style("AgentIntroText", None, palette["muted"])
        self._apply_control_style("AgentWarningText", None, palette["warn"])
        self._apply_control_style("AgentProviderHelp", None, palette["muted"])
        self._apply_control_style("AgentCommandLegend", None, palette["muted"])
        self._apply_control_style("AgentPlanStepLabel", None, palette["text"])
        self._apply_control_style("AgentStepStateText", None, palette["muted"])
        self._apply_control_style("AgentUndoStatusText", None, palette["muted"])
        self._apply_control_style("AgentProviderLabel", None, palette["muted"])
        self._apply_control_style("AgentStatus", None, palette["accent"])
        self._apply_control_style("AgentHistory", palette["panel_alt"], palette["text"], palette["border"])
        self._apply_control_style("AgentGoalInput", palette["panel_alt"], palette["text"], palette["border"])
        self._apply_control_style("AgentRunButton", palette["accent"], "#ffffff", palette["accent"])
        self._apply_control_style("AgentExecuteButton", palette["accent_alt"], "#ffffff", palette["accent_alt"])
        self._apply_control_style("AgentCommandSelector", palette["panel_alt"], palette["text"], palette["border"])
        self._apply_control_style("AgentToggleCommandButton", palette["panel_alt"], palette["text"], palette["border"])
        self._apply_control_style("AgentResetCommandsButton", palette["panel_alt"], palette["text"], palette["border"])
        self._apply_control_style("AgentUndoButton", palette["panel_alt"], palette["muted"], palette["border"])
        self._apply_control_style("ModelMindIntroText", None, palette["muted"])
        self._apply_control_style("ModelMindHistory", palette["panel_alt"], palette["text"], palette["border"])
        self._apply_control_style("ReviewedCodeGroup", palette["panel_bg"], palette["text"], palette["border"])
        self._apply_control_style("ReviewedCodePreview", palette["panel_alt"], palette["text"], palette["border"])
        self._apply_control_style("ModelMindInput", palette["panel_alt"], palette["text"], palette["border"])
        self._apply_control_style("ModelMindSendButton", palette["accent_alt"], "#ffffff", palette["accent_alt"])
        self._apply_control_style("ApproveCodeButton", palette["accent"], "#ffffff", palette["accent"])
        self._apply_control_style("SaveRecipeButton", palette["accent_alt"], "#ffffff", palette["accent_alt"])
        self._apply_control_style("ModelMindUndoButton", palette["panel_alt"], palette["text"], palette["border"])
        self._apply_control_style("ToggleReviewedCodeButton", "#475569", "#ffffff", "#475569")
        self._apply_control_style("ReviewedCodeStateLabel", None, palette["accent"])
        self._apply_control_style("PromptDetailsGroup", palette["panel_bg"], palette["text"], palette["border"])
        self._apply_control_style("PromptDetailTitle", None, palette["text"])
        self._apply_control_style("PromptDetailMeta", None, palette["muted"])
        self._apply_control_style("PromptDetailBody", palette["panel_alt"], palette["text"], palette["border"])
        self._apply_control_style("PromptCatalogSearchBox", palette["panel_alt"], palette["text"], palette["border"])
        self._apply_control_style("PromptCatalogClearButton", palette["panel_alt"], palette["text"], palette["border"])
        self._apply_control_style("PromptTreeExpandButton", palette["panel_alt"], palette["text"], palette["border"])
        self._apply_control_style("PromptTreeCollapseButton", palette["panel_alt"], palette["text"], palette["border"])
        self._apply_control_style("PromptCatalogStatusText", None, palette["muted"])
        self._apply_control_style("PromptTreeHint", None, palette["muted"])
        self._apply_control_style("PromptTreeGroup", palette["panel_bg"], palette["text"], palette["border"])
        self._apply_control_style("PromptTree", palette["panel_alt"], palette["text"], palette["border"])
        self._apply_control_style("ModelMindSplitter", palette["panel_alt"], palette["text"], palette["border"])
        try:
            self.ThemeToggleButton.Content = "Theme: {0}".format(theme_name.title())
        except:
            pass
        self.refresh_action_button_states()
        self.settings["theme"] = theme_name
        self.settings_store.save(self.settings)

    def on_toggle_theme(self, sender, args):
        next_theme = "dark" if self.current_theme == "light" else "light"
        self.apply_theme(next_theme)

    def on_planner_mode_changed(self, sender, args):
        self.configure_planner_provider()

    def update_window_status(self, status, detail=None):
        labels = {
            "idle": "Ready",
            "planning": "Planning",
            "ready_to_execute": "Ready",
            "executing": "Working",
            "failed": "Needs review",
        }
        label = labels.get(status, status.replace("_", " ").title())
        if detail:
            label = "{0} - {1}".format(label, detail)
        try:
            self.StatusLabel.Text = label
        except:
            pass

    def update_reviewed_code_state(self, state, reason=None):
        state = (state or "draft").lower()
        display = {
            "draft": "Reviewed code: Draft",
            "validated": "Reviewed code: Validated",
            "blocked": "Reviewed code: Blocked",
            "executed": "Reviewed code: Executed",
            "saved": "Reviewed code: Saved",
        }
        label = display.get(state, "Reviewed code: {0}".format(state.title()))
        if reason:
            label = "{0} - {1}".format(label, reason)
        try:
            self.ReviewedCodeStateLabel.Text = label
            color = REVIEWED_CODE_STATE_COLORS.get(state)
            if color:
                self.ReviewedCodeStateLabel.Foreground = self._brush(color)
        except:
            pass

    def reset_reviewed_code_state(self, state="draft", reason="Awaiting reviewed code."):
        self.pending_ai_code = None
        self.pending_validated_code = None
        self.ApproveCodeButton.IsEnabled = False
        self.SaveRecipeButton.IsEnabled = False
        self.ToggleReviewedCodeButton.IsEnabled = False
        self.ReviewedCodePreview.Text = ""
        self.ReviewedCodeGroup.Visibility = System.Windows.Visibility.Collapsed
        self.ToggleReviewedCodeButton.Content = "Show reviewed code"
        self.refresh_action_button_states()
        self.update_reviewed_code_state(state, reason)

    def set_reviewed_code_preview(self, code_text, allow_toggle=True):
        self.ReviewedCodePreview.Text = code_text or ""
        self.ToggleReviewedCodeButton.IsEnabled = bool(code_text) and allow_toggle
        self.ToggleReviewedCodeButton.Content = "Show reviewed code"
        self.ReviewedCodeGroup.Visibility = System.Windows.Visibility.Collapsed
        self.refresh_action_button_states()

    def on_toggle_reviewed_code(self, sender, args):
        if self.ReviewedCodeGroup.Visibility == System.Windows.Visibility.Visible:
            self.ReviewedCodeGroup.Visibility = System.Windows.Visibility.Collapsed
            self.ToggleReviewedCodeButton.Content = "Show reviewed code"
        else:
            self.ReviewedCodeGroup.Visibility = System.Windows.Visibility.Visible
            self.ToggleReviewedCodeButton.Content = "Hide reviewed code"

    def validate_and_prepare_reviewed_code(self, raw_code, prompt_text):
        sanitized_code = sanitize_llm_code(raw_code)
        sanitized_code = llm_output_safety_filter(sanitized_code)
        validation = validate_reviewed_code(sanitized_code)
        if sanitized_code.strip() == "# Not possible with current API":
            validation["is_valid"] = False
            if "Unsupported API pattern" not in validation["errors"]:
                validation["errors"].append("Unsupported API pattern found in generated code.")
        validation["sanitized_code"] = sanitized_code
        if validation.get("is_valid"):
            self.pending_ai_code = raw_code
            self.pending_validated_code = sanitized_code
            self.ApproveCodeButton.IsEnabled = True
            self.set_reviewed_code_preview(sanitized_code, allow_toggle=True)
            self.update_reviewed_code_state("validated", "Ready for reviewed execution.")
        else:
            self.pending_ai_code = raw_code
            self.pending_validated_code = None
            self.ApproveCodeButton.IsEnabled = False
            self.set_reviewed_code_preview(sanitized_code, allow_toggle=True)
            reason = "Blocked: {0}".format("; ".join(validation.get("blocked_hits") or validation.get("errors")))
            self.update_reviewed_code_state("blocked", reason)
            self.ModelMindHistory.AppendText(
                "Reviewed code blocked.\nUnsupported items: {0}\n".format(
                    ", ".join(validation.get("blocked_hits") or validation.get("errors"))
                )
            )
        self.refresh_action_button_states()
        return validation

    def get_default_recipe_metadata(self):
        source_entry = self.pending_source_entry or {}
        title = source_entry.get("title") or self.pending_ai_prompt or "Approved recipe"
        source_prompt = self.pending_ai_prompt or source_entry.get("prompt_text") or title
        return {
            "id": slugify_text(title + "-" + time.strftime("%Y%m%d-%H%M%S")),
            "title": "{0} [{1}]".format(title, time.strftime("%Y-%m-%d %H:%M")),
            "category": source_entry.get("category", "Approved Recipes"),
            "role": source_entry.get("role", "modify"),
            "risk_level": source_entry.get("risk_level", "medium"),
            "source_prompt": source_prompt,
            "enabled": True,
        }

    def set_agent_status(self, status):
        labels = {
            "idle": "Planner ready",
            "planning": "Planning",
            "ready_to_execute": "Plan ready",
            "executing": "Executing plan",
            "failed": "Needs guidance",
        }
        readable = labels.get(status, status.replace("_", " ").title())
        try:
            self.AgentStatus.Text = readable
        except:
            pass
        self.update_window_status(status)

    def populate_prompt_tree(self, filter_text=None):
        from System.Windows.Controls import TreeViewItem

        self.PromptTree.Items.Clear()
        sections = self.catalog.get_tree_sections(
            filter_text=filter_text,
            recent_prompts=self.get_recent_prompt_entries(),
        )
        for section in sections:
            self.PromptTree.Items.Add(
                self._build_prompt_tree_node(section, filter_text=filter_text, depth=0)
            )
        self.update_prompt_tree_status(filter_text, sections)

    def get_catalog_filter_text(self):
        try:
            return (self.PromptCatalogSearchBox.Text or "").strip()
        except:
            return ""

    def _count_prompt_tree_matches(self, node):
        total = len(node.get("items", []) or [])
        for child in node.get("groups", []) or []:
            total += self._count_prompt_tree_matches(child)
        return total

    def update_prompt_tree_status(self, filter_text, sections):
        filter_text = (filter_text or "").strip()
        match_count = 0
        for section in sections or []:
            match_count += self._count_prompt_tree_matches(section)
        if filter_text:
            status = (
                "Showing {0} matching actions/presets for '{1}'. "
                "Catalog search filters titles, aliases, examples, and grouping only."
            ).format(match_count, filter_text)
        else:
            status = (
                "Showing the full reviewed catalog. "
                "Use the catalog filter to narrow actions, aliases, and examples without running anything."
            )
        try:
            self.PromptCatalogStatusText.Text = status
            self.PromptCatalogClearButton.IsEnabled = bool(filter_text)
        except:
            pass

    def _set_prompt_tree_expansion(self, expanded):
        def _walk(items):
            for item in items:
                try:
                    item.IsExpanded = expanded
                    if hasattr(item, "Items") and item.Items.Count:
                        _walk(item.Items)
                except:
                    pass

        try:
            _walk(self.PromptTree.Items)
        except:
            pass

    def _build_prompt_tree_node(self, node, filter_text=None, depth=0):
        from System.Windows.Controls import TreeViewItem

        item = TreeViewItem()
        item.Header = node.get("header", "(untitled)")
        item.Tag = node.get("tag")
        item.ToolTip = node.get("tooltip", self._branch_tooltip(node))
        item.IsExpanded = True if filter_text else depth < 1 or node.get("kind") in ("approved", "recent")
        for child in node.get("groups", []):
            item.Items.Add(self._build_prompt_tree_node(child, filter_text=filter_text, depth=depth + 1))
        for entry in node.get("items", []):
            leaf = TreeViewItem()
            leaf.Header = self._format_prompt_header(entry, node.get("kind"))
            leaf.Tag = entry
            leaf.ToolTip = self._build_prompt_tooltip(entry)
            item.Items.Add(leaf)
        return item

    def _branch_tooltip(self, node):
        kind = node.get("kind")
        if kind == "approved":
            return "Approved recipes are reviewed code assets kept separate from the canonical catalog."
        if kind == "recent":
            return "Recent prompts are convenience shortcuts only; they are not canonical reviewed actions."
        return "Shared reviewed action catalog for ModelMind and AI Agent."

    def _format_prompt_header(self, entry, branch_kind):
        title = entry.get("title", entry.get("prompt_text", "(untitled)"))
        if len(title) > 44:
            title = "{0}...".format(title[:41])
        prefix = ""
        if branch_kind == "approved":
            prefix = "Approved | "
        elif branch_kind == "recent":
            prefix = "Recent | "
        return "{0}{1}".format(prefix, title)

    def _build_prompt_tooltip(self, entry):
        return "Role: {0} | Risk: {1} | Validation: {2}\nCanonical prompt: {3}".format(
            entry.get("role", "read"),
            entry.get("risk_level", "low"),
            entry.get("validation_state", "structural_only"),
            entry.get("canonical_prompt", entry.get("prompt_text", "")),
        )

    def _resolve_prompt_details_entry(self, entry):
        if not isinstance(entry, dict):
            return entry
        if entry.get("source") != "recent_prompt":
            return entry
        prompt_text = entry.get("canonical_prompt") or entry.get("prompt_text") or entry.get("title")
        canonical = self.resolve_prompt_entry(prompt_text)
        if canonical:
            merged = dict(canonical)
            merged["source"] = "recent_prompt"
            merged["recent_prompt_text"] = entry.get("prompt_text")
            return merged
        return entry

    def update_prompt_details(self, entry):
        title = "Select a reviewed action"
        meta = "Role, risk, validation state, and agent availability will appear here."
        body = (
            "Canonical prompt, aliases, and examples stay attached to the selected reviewed action. "
            "They do not create duplicate catalog nodes."
        )
        if entry:
            entry = self._resolve_prompt_details_entry(entry)
            title = entry.get("title", entry.get("prompt_text", "Selected action"))
            meta = "Role: {0} | Risk: {1} | Validation: {2} | Agent: {3}".format(
                entry.get("role", "read"),
                entry.get("risk_level", "low"),
                entry.get("validation_state", "structural_only"),
                "available" if entry.get("available_to_agent", False) else "not available",
            )
            aliases = entry.get("aliases") or entry.get("planner_aliases") or []
            examples = entry.get("example_prompts") or []
            purpose = (
                entry.get("description")
                or entry.get("purpose")
                or entry.get("summary")
                or self._derive_prompt_purpose(entry)
            )
            lines = [
                "Purpose: {0}".format(purpose),
                "Domain: {0}".format(entry.get("category", "General")),
                "Group: {0}".format(entry.get("group", "Report")),
                "Canonical prompt: {0}".format(
                    entry.get("canonical_prompt", entry.get("prompt_text", ""))
                ),
                "Discipline: {0}".format(entry.get("discipline", "General")),
                "Scope: {0}".format(entry.get("scope_type", "project")),
                "Available to agent: {0}".format(
                    "Yes" if entry.get("available_to_agent", False) else "No"
                ),
            ]
            if aliases:
                lines.append("Aliases: {0}".format(", ".join(aliases[:6])))
            if examples:
                lines.append("Examples: {0}".format(", ".join(examples[:4])))
            if entry.get("reviewed_steps"):
                lines.append("Reviewed preset steps: {0}".format(len(entry.get("reviewed_steps") or [])))
            if entry.get("source") == "approved_recipe":
                lines.append("Approved recipe source: reviewed code store")
            elif entry.get("source") == "recent_prompt":
                lines.append("Recent prompt shortcut resolved to canonical reviewed action metadata.")
            body = "\n".join(lines)
        try:
            self.PromptDetailTitle.Text = title
            self.PromptDetailMeta.Text = meta
            self.PromptDetailBody.Text = body
        except:
            pass

    def _derive_prompt_purpose(self, entry):
        if entry.get("source") == "approved_recipe":
            return "Reviewed reusable recipe kept separate from the canonical action catalog."
        if entry.get("source") == "recent_prompt":
            return "Recent prompt shortcut that resolves back to the canonical reviewed action when available."
        steps = entry.get("reviewed_steps") or []
        group = entry.get("group", "workflow").lower()
        category = entry.get("category", "General")
        if steps:
            return "Reviewed preset for {0} with {1} governed step(s).".format(
                category, len(steps)
            )
        if entry.get("role") == "modify":
            return "Deterministic modifying reviewed action for {0} {1} work.".format(
                category, group
            )
        return "Deterministic read-only reviewed action for {0} {1} work.".format(
            category, group
        )

    def _select_prompt_entry(self, entry, run_if_recipe=False):
        if not entry:
            self.selected_prompt_entry = None
            self.update_prompt_details(None)
            return
        self.selected_prompt_entry = entry
        self.update_prompt_details(entry)
        self.ModelMindInput.Text = entry.get("canonical_prompt", entry.get("prompt_text", ""))
        self.ModelMindInput.CaretIndex = len(self.ModelMindInput.Text)
        self.ModelMindInput.Focus()
        if run_if_recipe and entry.get("source") == "approved_recipe":
            self.ModelMindHistory.AppendText(
                "Running approved recipe from tree: {}\n".format(
                    entry.get("title", "Approved recipe")
                )
            )
            self.on_modelmind_send(self, None)

    def on_prompt_tree_selected(self, sender, args):
        sel = self.PromptTree.SelectedItem
        try:
            entry = sel.Tag if sel is not None and hasattr(sel, "Tag") else None
            if isinstance(entry, dict):
                self.selected_prompt_entry = entry
                self.update_prompt_details(entry)
            else:
                self.update_prompt_details(None)
        except:
            self.update_prompt_details(None)

    def on_prompt_tree_doubleclick(self, sender, args):
        sel = self.PromptTree.SelectedItem
        try:
            if sel is not None and hasattr(sel, "Items") and sel.Items.Count == 0:
                entry = sel.Tag if hasattr(sel, "Tag") else None
                if entry:
                    self._select_prompt_entry(entry, run_if_recipe=True)
        except:
            pass

    def on_prompt_tree_keydown(self, sender, args):
        import System.Windows.Input as wpfInput

        if args.Key in (wpfInput.Key.Enter, wpfInput.Key.Tab):
            sel = self.PromptTree.SelectedItem
            try:
                if sel is not None and hasattr(sel, "Items") and sel.Items.Count == 0:
                    entry = sel.Tag if hasattr(sel, "Tag") else None
                    if entry:
                        self._select_prompt_entry(entry, run_if_recipe=True)
                        args.Handled = True
            except:
                pass

    def filter_prompt_tree(self, text):
        # simple rebuild with filter
        self.populate_prompt_tree(filter_text=text)

    def on_prompt_catalog_search_changed(self, sender, args):
        self.filter_prompt_tree(self.get_catalog_filter_text())

    def on_prompt_catalog_clear(self, sender, args):
        try:
            self.PromptCatalogSearchBox.Text = ""
            self.PromptCatalogSearchBox.Focus()
        except:
            pass

    def on_prompt_tree_expand_all(self, sender, args):
        self._set_prompt_tree_expansion(True)

    def on_prompt_tree_collapse_all(self, sender, args):
        self._set_prompt_tree_expansion(False)

    def on_modelmind_input_changed(self, sender, args):
        typed = self.ModelMindInput.Text or ""
        selected_prompt = ""
        if self.selected_prompt_entry:
            selected_prompt = self.selected_prompt_entry.get(
                "canonical_prompt",
                self.selected_prompt_entry.get("prompt_text", ""),
            )
        if typed.strip() and typed.strip() != (selected_prompt or "").strip():
            self.selected_prompt_entry = None
        if typed.strip() != (self.pending_ai_prompt or "").strip():
            self.pending_ai_code = None
            self.pending_validated_code = None
            self.last_successful_reviewed_code = None
            self.last_reviewed_recipe_metadata = None
            self.ApproveCodeButton.IsEnabled = False
            self.SaveRecipeButton.IsEnabled = False
            self.ToggleReviewedCodeButton.IsEnabled = False
            self.ReviewedCodeGroup.Visibility = System.Windows.Visibility.Collapsed
            self.ReviewedCodePreview.Text = ""
            self.ToggleReviewedCodeButton.Content = "Show reviewed code"
            self.refresh_action_button_states()
            self.update_reviewed_code_state("draft", "Prompt changed; reviewed code must be regenerated.")
        self.update_prompt_details(self.selected_prompt_entry)

    def on_modelmindinput_keydown(self, sender, args):
        import System.Windows.Input as wpfInput

        if args.Key == wpfInput.Key.Enter:
            self.on_modelmind_send(sender, args)
            args.Handled = True

    def on_prompt_listbox_doubleclick(self, sender, args):
        if not hasattr(self, "PromptListBox"):
            return
        if self.PromptListBox.SelectedItem:
            self.ModelMindInput.Text = self.PromptListBox.SelectedItem

    def on_prompt_listbox_keydown(self, sender, args):
        if not hasattr(self, "PromptListBox"):
            return
        import System.Windows.Input as wpfInput

        # Check if user pressed Enter or Tab
        if args.Key == wpfInput.Key.Enter or args.Key == wpfInput.Key.Tab:
            if self.PromptListBox.SelectedItem:
                self.ModelMindInput.Text = self.PromptListBox.SelectedItem
                self.ModelMindInput.CaretIndex = len(self.ModelMindInput.Text)
                self.ModelMindInput.Focus()  # Put focus back to input box
                args.Handled = True  # Stop further event processing

    def populate_model_selector(self):
        models = get_all_models()
        self.ModelSelector.Items.Clear()
        for m in models:
            self.ModelSelector.Items.Add(m)
        if self.model in models:
            self.ModelSelector.SelectedItem = self.model

    def on_model_selected(self, sender, args):
        selected = self.ModelSelector.SelectedItem
        if selected:
            self.model = selected
            self.refresh_model_info()

    def refresh_model_info(self):
        if not ollama_is_installed():
            self.ModelInfo.Text = "Ollama not found. Please install Ollama."
            return

        if not ollama_model_installed(self.model):
            self.ModelInfo.Text = "Model '{}' not installed.".format(self.model)
        else:
            note = ""
            if self.model != DEFAULT_MODEL:
                note = " Runtime note: heavier local models may be unstable in this runtime; phi3:mini remains the stable recommended model."
            self.ModelInfo.Text = "Model: {} (active).{}".format(self.model, note)

    def _set_project_context_status(self, text):
        try:
            self.ProjectContextStatusText.Text = text
        except:
            pass

    def _chat_separator(self):
        return "------------------------------------------------------------"

    def append_chat_turn(self, prompt, reply, source_label="AI"):
        try:
            self.ChatHistory.AppendText("{0}\nYOU: {1}\n{0}\n{2}: {3}\n\n".format(self._chat_separator(), prompt, source_label, reply))
        except:
            self.ChatHistory.AppendText("YOU: {0}\n{1}: {2}\n\n".format(prompt, source_label, reply))

    def append_chat_notice(self, title, body):
        try:
            self.ChatHistory.AppendText("{0}\n{1}\n{0}\n{2}\n\n".format(self._chat_separator(), title, body))
        except:
            self.ChatHistory.AppendText("{0}\n{1}\n\n".format(title, body))

    def _extract_report_header(self, report_text):
        for line in safe_str(report_text).splitlines():
            candidate = line.strip()
            if candidate.startswith("[") and candidate.endswith("]"):
                return candidate
        return ""

    def _is_exportable_report_text(self, report_text):
        header = self._extract_report_header(report_text)
        return header in QA_EXPORT_ACCEPTED_REPORT_HEADERS

    def _detect_report_scope(self, report_text):
        header = self._extract_report_header(report_text)
        if header == "[SPLIT APPLY VERIFICATION REPORT]":
            return "latest reviewed split apply result / active document only"
        if header == "[SPLIT SELECTED PIPE REVIEWED APPLY]":
            return "single split candidate / active document only"
        if header == "[SPLIT SELECTED PIPES ROLLBACK TEST]":
            return "latest split dry-run candidates / active document only"
        if header == "[REVIEWED ACTION CONFIRMATION GUARD]":
            return "session-local reviewed action state / active document only"
        text = safe_str(report_text).lower()
        if "selection scope: selected elements" in text or "total selected" in text or "selected supported" in text:
            return "selected elements / active document only"
        if "selection scope: active view" in text or "active view" in text:
            return "active view / active document only"
        if "project context" in text or "project onboarding" in text:
            return "project context snapshot"
        if "guided project startup plan" in text:
            return "project-context agent plan"
        return "deterministic AI Workbench report"

    def remember_latest_deterministic_report(self, prompt, report_text):
        if not self._is_exportable_report_text(report_text):
            self.latest_chat_output_is_deterministic_report = False
            return False
        self.latest_deterministic_report = {
            "source_prompt": safe_str(prompt),
            "report_text": safe_str(report_text),
            "report_header": self._extract_report_header(report_text),
            "report_timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "report_scope": self._detect_report_scope(report_text),
            "deterministic": True,
        }
        self.latest_chat_output_is_deterministic_report = True
        return True

    def _is_qa_export_request(self, prompt):
        normalized = self._normalize_context_prompt(prompt)
        routes = [
            "export latest qa report",
            "export current qa report",
            "save latest qa report",
            "save current qa report",
            "create qa evidence snapshot",
            "create evidence snapshot",
            "export ai workbench report",
            "export latest diagnostic report",
            "save latest diagnostic report",
        ]
        return normalized in routes

    def _qa_export_base_folder(self):
        return self._qa_export_root()

    def _qa_export_primary_root(self):
        user_profile = os.environ.get("USERPROFILE") or os.path.expanduser("~")
        desktop = os.path.join(user_profile, "Desktop")
        return os.path.join(desktop, "Results", "AI_Workbench", "QA_Exports")

    def _qa_export_temp_root(self):
        return os.path.join(tempfile.gettempdir(), "AI_Workbench", "QA_Exports")

    def _qa_export_root(self):
        primary = self._qa_export_primary_root()
        user_profile = os.environ.get("USERPROFILE") or os.path.expanduser("~")
        desktop = os.path.join(user_profile, "Desktop")
        if desktop and os.path.isdir(desktop):
            return primary
        return self._qa_export_temp_root()

    def _qa_export_index_dir(self, root_folder=None):
        return os.path.join(root_folder or self._qa_export_root(), "_index")

    def _qa_export_index_candidates(self):
        candidates = []
        primary = self._qa_export_index_dir(self._qa_export_primary_root())
        fallback = self._qa_export_index_dir(self._qa_export_temp_root())
        for item in [primary, fallback]:
            if item and item not in candidates:
                candidates.append(item)
        return candidates

    def _qa_index_csv_escape(self, value):
        text = safe_str(value)
        if text == "(none)":
            text = ""
        text = text.replace("\r", " ").replace("\n", " ")
        if '"' in text:
            text = text.replace('"', '""')
        if "," in text or '"' in text:
            text = '"{0}"'.format(text)
        return text

    def _qa_index_csv_row(self, values):
        return ",".join([self._qa_index_csv_escape(value) for value in values])

    def _report_header_family(self, header):
        text = safe_str(header).strip().strip("[]")
        if not text:
            return "unknown"
        return slugify_text(text)

    def _qa_export_file_paths(self, export_folder):
        return {
            "report_file": os.path.join(export_folder, "report.md"),
            "text_file": os.path.join(export_folder, "report.txt"),
            "metadata_file": os.path.join(export_folder, "metadata.json"),
            "manifest_file": os.path.join(export_folder, "artifact_manifest.txt"),
        }

    def _write_qa_export_index_entry(self, metadata):
        index_dir = self._qa_export_index_dir()
        try:
            if not os.path.isdir(index_dir):
                os.makedirs(index_dir)
            export_folder = metadata.get("export_folder") or ""
            file_paths = self._qa_export_file_paths(export_folder)
            entry = dict(metadata)
            entry["feature_id"] = "MEP-RO-006"
            entry["source_feature_id"] = "MEP-RO-005"
            entry["index_written"] = True
            entry["report_file"] = file_paths.get("report_file")
            entry["text_file"] = file_paths.get("text_file")
            entry["metadata_file"] = file_paths.get("metadata_file")
            entry["manifest_file"] = file_paths.get("manifest_file")
            entry["export_folder_exists"] = bool(export_folder and os.path.isdir(export_folder))
            entry["report_header_family"] = self._report_header_family(metadata.get("source_report_header"))
            entry["document_key"] = slugify_text(metadata.get("document_title") or "unknown-document")
            entry["active_view_key"] = slugify_text(metadata.get("active_view_name") or "unknown-view")

            jsonl_path = os.path.join(index_dir, "qa_export_index.jsonl")
            stream = codecs.open(jsonl_path, "a", "utf-8")
            try:
                stream.write(json.dumps(entry, sort_keys=True))
                stream.write("\n")
            finally:
                stream.close()

            csv_path = os.path.join(index_dir, "qa_export_index.csv")
            csv_fields = [
                "feature_id",
                "source_feature_id",
                "export_timestamp_local",
                "document_title",
                "active_view_name",
                "active_view_type",
                "source_prompt",
                "source_report_header",
                "report_scope",
                "export_folder",
                "deterministic_route",
                "read_only",
                "model_modified",
                "linked_documents_scanned",
                "connector_traversal_used",
                "geometry_extraction_used",
                "report_header_family",
                "document_key",
                "active_view_key",
            ]
            csv_exists = os.path.exists(csv_path)
            csv_stream = codecs.open(csv_path, "a", "utf-8")
            try:
                if not csv_exists:
                    csv_stream.write(self._qa_index_csv_row(csv_fields))
                    csv_stream.write("\n")
                csv_stream.write(self._qa_index_csv_row([entry.get(field) for field in csv_fields]))
                csv_stream.write("\n")
            finally:
                csv_stream.close()

            latest_path = os.path.join(index_dir, "latest_export.json")
            latest_stream = codecs.open(latest_path, "w", "utf-8")
            try:
                latest_stream.write(json.dumps(entry, indent=2, sort_keys=True))
            finally:
                latest_stream.close()

            return {
                "ok": True,
                "index_dir": index_dir,
                "jsonl_path": jsonl_path,
                "csv_path": csv_path,
                "latest_path": latest_path,
                "entry": entry,
            }
        except Exception as exc:
            return {
                "ok": False,
                "index_dir": index_dir,
                "warning": "{0}: {1}".format(exc.__class__.__name__, str(exc)),
            }

    def _read_qa_export_index_entries(self, limit=None):
        last_error = None
        for index_dir in self._qa_export_index_candidates():
            jsonl_path = os.path.join(index_dir, "qa_export_index.jsonl")
            csv_path = os.path.join(index_dir, "qa_export_index.csv")
            if os.path.exists(jsonl_path):
                try:
                    entries = []
                    stream = codecs.open(jsonl_path, "r", "utf-8")
                    try:
                        for line in stream:
                            line = line.strip()
                            if not line:
                                continue
                            loaded = json.loads(line)
                            if isinstance(loaded, dict):
                                entries.append(loaded)
                    finally:
                        stream.close()
                    if limit and len(entries) > limit:
                        return entries[-int(limit):], index_dir, None
                    return entries, index_dir, None
                except Exception as exc:
                    last_error = "{0}: {1}".format(exc.__class__.__name__, str(exc))
                    return None, index_dir, last_error
            if os.path.exists(csv_path):
                try:
                    entries = []
                    stream = codecs.open(csv_path, "r", "utf-8")
                    try:
                        reader = csv.DictReader(stream)
                        for row in reader:
                            if isinstance(row, dict):
                                row["generated_files"] = ["report.md", "report.txt", "metadata.json", "artifact_manifest.txt"]
                                entries.append(row)
                    finally:
                        stream.close()
                    if limit and len(entries) > limit:
                        return entries[-int(limit):], index_dir, None
                    return entries, index_dir, None
                except Exception as exc:
                    last_error = "{0}: {1}".format(exc.__class__.__name__, str(exc))
                    return None, index_dir, last_error
        return [], self._qa_export_index_dir(), None

    def _read_latest_qa_export(self):
        last_error = None
        for index_dir in self._qa_export_index_candidates():
            latest_path = os.path.join(index_dir, "latest_export.json")
            if not os.path.exists(latest_path):
                continue
            try:
                stream = codecs.open(latest_path, "r", "utf-8")
                try:
                    loaded = json.loads(stream.read())
                finally:
                    stream.close()
                if isinstance(loaded, dict):
                    return loaded, index_dir, None
            except Exception as exc:
                last_error = "{0}: {1}".format(exc.__class__.__name__, str(exc))
                return None, index_dir, last_error
        entries, index_dir, error = self._read_qa_export_index_entries()
        if error:
            return None, index_dir, error
        if entries:
            return entries[-1], index_dir, None
        return None, self._qa_export_index_dir(), last_error

    def _format_qa_export_index_report(self):
        entries, index_dir, error = self._read_qa_export_index_entries()
        if error:
            return "\n".join(
                [
                    "[QA EXPORT INDEX]",
                    "QA export index exists but could not be read safely. Generate a new evidence snapshot or inspect the index file manually.",
                    "Index folder: {0}".format(index_dir),
                    "Error: {0}".format(error),
                ]
            )
        if not entries:
            return "\n".join(
                [
                    "[QA EXPORT INDEX]",
                    "No QA export index was found. Run an evidence snapshot export first.",
                ]
            )
        latest = entries[-1]
        lines = [
            "[QA EXPORT INDEX]",
            "",
            "Index folder:",
            index_dir,
            "",
            "Total indexed exports: {0}".format(len(entries)),
            "",
            "Latest export:",
            "- timestamp: {0}".format(latest.get("export_timestamp_local") or "unknown"),
            "- document: {0}".format(latest.get("document_title") or "unknown"),
            "- active view: {0} [{1}]".format(latest.get("active_view_name") or "unknown", latest.get("active_view_type") or "unknown"),
            "- source prompt: {0}".format(latest.get("source_prompt") or "unknown"),
            "- source header: {0}".format(latest.get("source_report_header") or "unknown"),
            "- scope: {0}".format(latest.get("report_scope") or "unknown"),
            "- export folder: {0}".format(latest.get("export_folder") or "unknown"),
            "",
            "Recent exports:",
        ]
        recent = entries[-10:]
        recent.reverse()
        for index, item in enumerate(recent):
            lines.append(
                "{0}. {1} | {2} | {3} | {4} | {5}".format(
                    index + 1,
                    item.get("export_timestamp_local") or "unknown",
                    item.get("document_title") or "unknown",
                    item.get("source_report_header") or "unknown",
                    item.get("source_prompt") or "unknown",
                    item.get("export_folder") or "unknown",
                )
            )
        lines.extend(
            [
                "",
                "Safety:",
                "- Index read only.",
                "- Revit model data was not modified.",
            ]
        )
        return "\n".join(lines)

    def _format_latest_qa_export_report(self):
        latest, index_dir, error = self._read_latest_qa_export()
        if error:
            return "\n".join(
                [
                    "[QA EXPORT INDEX]",
                    "QA export index exists but could not be read safely. Generate a new evidence snapshot or inspect the index file manually.",
                    "Index folder: {0}".format(index_dir),
                    "Error: {0}".format(error),
                ]
            )
        if not latest:
            return "\n".join(
                [
                    "[QA EXPORT INDEX]",
                    "No QA export index was found. Run an evidence snapshot export first.",
                ]
            )
        files = latest.get("generated_files") or []
        lines = [
            "[LATEST QA EXPORT]",
            "",
            "Timestamp: {0}".format(latest.get("export_timestamp_local") or "unknown"),
            "Document: {0}".format(latest.get("document_title") or "unknown"),
            "Active view: {0} [{1}]".format(latest.get("active_view_name") or "unknown", latest.get("active_view_type") or "unknown"),
            "Source prompt: {0}".format(latest.get("source_prompt") or "unknown"),
            "Source header: {0}".format(latest.get("source_report_header") or "unknown"),
            "Scope: {0}".format(latest.get("report_scope") or "unknown"),
            "Export folder: {0}".format(latest.get("export_folder") or "unknown"),
            "Generated files:",
        ]
        for filename in files:
            lines.append("- {0}".format(filename))
        lines.extend(
            [
                "",
                "Safety:",
                "- Index read only.",
                "- Revit model data was not modified.",
            ]
        )
        return "\n".join(lines)

    def _qa_export_index_query_kind(self, prompt):
        normalized = self._normalize_context_prompt(prompt)
        latest_routes = [
            "show latest qa export",
            "latest qa evidence snapshot",
            "latest qa export",
        ]
        list_routes = [
            "list qa evidence snapshots",
            "list qa exports",
            "show qa export index",
            "qa export index summary",
            "evidence snapshot index",
            "show evidence snapshots",
        ]
        if normalized in latest_routes:
            return "latest"
        if normalized in list_routes:
            return "list"
        return None

    def answer_qa_export_index_question(self, prompt):
        kind = self._qa_export_index_query_kind(prompt)
        if kind == "latest":
            return self._format_latest_qa_export_report()
        if kind == "list":
            return self._format_qa_export_index_report()
        return None

    def _parse_split_dry_run_candidates(self, report_text):
        candidates = []
        pattern = re.compile(
            r"(?ms)^\d+\. Pipe\s+(\d+).*?"
            r"Candidate split point:\s+internal ft:\s+\(([^,]+),\s*([^,]+),\s*([^)]+)\).*?"
            r"Estimated segment A:\s+([0-9.]+)\s+ft.*?"
            r"Estimated segment B:\s+([0-9.]+)\s+ft"
        )
        for match in pattern.finditer(safe_str(report_text)):
            try:
                pipe_id = int(match.group(1))
                point = DB.XYZ(float(match.group(2)), float(match.group(3)), float(match.group(4)))
                segment_a = float(match.group(5))
                segment_b = float(match.group(6))
                candidates.append(
                    {
                        "pipe_id": pipe_id,
                        "point": point,
                        "estimated_segment_a": segment_a,
                        "estimated_segment_b": segment_b,
                    }
                )
            except:
                continue
        return candidates

    def _latest_split_dry_run_source_for_rollback(self):
        state = self.latest_split_pipe_dry_run
        if state and state.get("report_text"):
            parsed_candidates = self._parse_split_dry_run_candidates(state.get("report_text", ""))
            return {
                "source_status": "available",
                "source_feature_id": "MEP-WR-001",
                "source_prompt": state.get("source_prompt", ""),
                "source_header": "[SPLIT SELECTED PIPES DRY RUN]",
                "eligible_pipe_count": state.get("eligible_pipe_count", 0) or len(parsed_candidates),
                "candidate_count": state.get("candidate_count", 0) or len(parsed_candidates),
                "dry_run_result": state.get("dry_run_result", ""),
                "dry_run_id": state.get("dry_run_id", ""),
                "document_title": state.get("document_title", ""),
                "document_path": state.get("document_path", None),
                "report_text": state.get("report_text", ""),
                "candidates": parsed_candidates,
            }
        report = self.latest_deterministic_report
        if report and report.get("report_header") == "[SPLIT SELECTED PIPES DRY RUN]":
            report_text = report.get("report_text", "")
            result_match = re.search(r"Dry-run result:\s*\n([^\n]+)", safe_str(report_text))
            parsed_candidates = self._parse_split_dry_run_candidates(report_text)
            return {
                "source_status": "available",
                "source_feature_id": "MEP-WR-001",
                "source_prompt": report.get("source_prompt", ""),
                "source_header": "[SPLIT SELECTED PIPES DRY RUN]",
                "eligible_pipe_count": len(parsed_candidates),
                "candidate_count": len(parsed_candidates),
                "dry_run_result": result_match.group(1).strip() if result_match else "",
                "dry_run_id": "",
                "document_title": _document_title(doc),
                "document_path": self._safe_document_path(),
                "report_text": report_text,
                "candidates": parsed_candidates,
            }
        return {
            "source_status": "unavailable",
            "source_feature_id": "none",
            "source_prompt": "none",
            "source_header": "none",
            "eligible_pipe_count": 0,
            "candidate_count": 0,
            "dry_run_result": "",
            "dry_run_id": "",
            "report_text": "",
            "candidates": [],
        }

    def _point_distance_to_pipe_segment(self, point, p0, p1):
        try:
            vx = float(p1.X - p0.X)
            vy = float(p1.Y - p0.Y)
            vz = float(p1.Z - p0.Z)
            wx = float(point.X - p0.X)
            wy = float(point.Y - p0.Y)
            wz = float(point.Z - p0.Z)
            length_sq = vx * vx + vy * vy + vz * vz
            if length_sq <= 0:
                return None
            t = (wx * vx + wy * vy + wz * vz) / length_sq
            if t < 0.0:
                t = 0.0
            if t > 1.0:
                t = 1.0
            cx = p0.X + t * vx
            cy = p0.Y + t * vy
            cz = p0.Z + t * vz
            dx = point.X - cx
            dy = point.Y - cy
            dz = point.Z - cz
            return math.sqrt(dx * dx + dy * dy + dz * dz)
        except:
            return None

    def _validate_split_rollback_candidate(self, candidate):
        pipe_id = candidate.get("pipe_id")
        try:
            elem = doc.GetElement(DB.ElementId(int(pipe_id)))
        except:
            elem = None
        if elem is None:
            return None, "original pipe id no longer resolves"
        if not _split_pipe_dry_run_is_pipe(elem):
            return None, "candidate element is no longer a pipe"
        try:
            if bool(elem.Pinned):
                return None, "pipe is pinned"
        except:
            pass
        try:
            group_id = getattr(elem, "GroupId", None)
            if group_id is not None and group_id != DB.ElementId.InvalidElementId:
                return None, "pipe is in a group"
        except:
            pass
        try:
            design_option = getattr(elem, "DesignOption", None)
            if design_option is not None:
                return None, "pipe is in a design option"
        except:
            pass
        curve, status = _split_pipe_dry_run_safe_curve(elem)
        if status != "ok":
            return None, status.replace("_", " ")
        p0, p1 = _split_pipe_dry_run_endpoints(curve)
        if _split_pipe_dry_run_is_near_vertical(p0, p1):
            return None, "pipe is near vertical"
        point = candidate.get("point")
        if point is None:
            return None, "candidate split point is unavailable"
        distance = self._point_distance_to_pipe_segment(point, p0, p1)
        if distance is None or distance > ROLLBACK_SPLIT_LENGTH_TOLERANCE_FT:
            return None, "candidate split point is not on or near the current pipe curve"
        segment_a = _split_pipe_dry_run_length_from_endpoints(p0, point)
        segment_b = _split_pipe_dry_run_length_from_endpoints(point, p1)
        if segment_a is None or segment_b is None:
            return None, "candidate segment lengths are unreadable"
        if segment_a < PIPE_SPLIT_DRY_RUN_MIN_SEGMENT_LENGTH_FT or segment_b < PIPE_SPLIT_DRY_RUN_MIN_SEGMENT_LENGTH_FT:
            return None, "candidate would create a segment below the minimum length rule"
        try:
            original_length = float(curve.Length)
        except:
            original_length = _split_pipe_dry_run_length_from_endpoints(p0, p1)
        return {
            "pipe": elem,
            "pipe_id": pipe_id,
            "point": point,
            "original_length": original_length,
            "segment_a": segment_a,
            "segment_b": segment_b,
        }, ""

    def _safe_pipe_length_by_id(self, pipe_id):
        try:
            elem = doc.GetElement(DB.ElementId(int(pipe_id)))
        except:
            elem = None
        if elem is None:
            return None
        try:
            curve, status = _split_pipe_dry_run_safe_curve(elem)
            if status != "ok":
                return None
            return float(curve.Length)
        except:
            return None

    def _run_split_rollback_batch(self, validated):
        results = []
        failures = []
        transaction_opened = False
        breakcurve_called = False
        group_rolled_back = False
        group = None
        try:
            try:
                from Autodesk.Revit.DB.Plumbing import PlumbingUtils
            except:
                from Autodesk.Revit.DB import PlumbingUtils
            group = DB.TransactionGroup(doc, "AI Split Selected Pipes Rollback Test")
            group.Start()
            transaction_opened = True
            for item in validated:
                trans = None
                new_id_value = None
                try:
                    trans = DB.Transaction(doc, "AI rollback pipe split candidate")
                    trans.Start()
                    breakcurve_called = True
                    new_id = PlumbingUtils.BreakCurve(doc, item.get("pipe").Id, item.get("point"))
                    try:
                        doc.Regenerate()
                    except:
                        pass
                    try:
                        new_id_value = new_id.IntegerValue
                    except:
                        new_id_value = None
                    temp_original_length = self._safe_pipe_length_by_id(item.get("pipe_id"))
                    temp_new_length = self._safe_pipe_length_by_id(new_id_value) if new_id_value is not None else None
                    trans.Commit()
                    results.append(
                        {
                            "pipe_id": item.get("pipe_id"),
                            "point": item.get("point"),
                            "original_length": item.get("original_length"),
                            "new_id": new_id_value,
                            "temp_original_length": temp_original_length,
                            "temp_new_length": temp_new_length,
                            "status": "temporary split succeeded / rolled back",
                        }
                    )
                except Exception as exc:
                    try:
                        if trans is not None:
                            trans.RollBack()
                    except:
                        pass
                    failures.append({"pipe_id": item.get("pipe_id"), "reason": safe_str(exc)})
            try:
                group.RollBack()
                group_rolled_back = True
            except Exception as exc:
                failures.append({"pipe_id": "(group)", "reason": "TransactionGroup rollback failed: {0}".format(safe_str(exc))})
        except Exception as exc:
            failures.append({"pipe_id": "(setup)", "reason": safe_str(exc)})
            try:
                if group is not None:
                    group.RollBack()
                    group_rolled_back = True
            except:
                pass
        return {
            "results": results,
            "failures": failures,
            "transaction_opened": transaction_opened,
            "breakcurve_called": breakcurve_called,
            "group_rolled_back": group_rolled_back,
        }

    def _format_split_rollback_test_report(self, prompt, source, token_present, rollback_data=None, skipped=None, reason=None):
        rollback_data = rollback_data or {}
        skipped = skipped or []
        rollback_test_id = "MEP-WR-002-{0}".format(time.strftime("%Y%m%d_%H%M%S"))
        has_source = source.get("source_status") == "available"
        candidates = source.get("candidates") or []
        results = rollback_data.get("results") or []
        failures = rollback_data.get("failures") or []
        transaction_opened = bool(rollback_data.get("transaction_opened"))
        breakcurve_called = bool(rollback_data.get("breakcurve_called"))
        group_rolled_back = bool(rollback_data.get("group_rolled_back"))
        if not has_source:
            result_text = "Not ready"
            reason_text = "No valid split selected pipes dry-run is available in the current AI Workbench session."
        elif not token_present:
            result_text = "Confirmation required"
            reason_text = "Confirmation token is required before opening a rollback-only transaction group."
        elif failures and results:
            result_text = "Passed with warnings" if group_rolled_back else "Failed and rolled back"
            reason_text = reason or "Some candidates failed during rollback-test validation or temporary split."
        elif failures and not results:
            result_text = "Failed and rolled back" if group_rolled_back else "Failed"
            reason_text = reason or "No candidate completed a temporary split."
        else:
            result_text = "Passed" if group_rolled_back and results else "Not ready"
            reason_text = reason or ""
        execution_available = bool(token_present and has_source and source.get("dry_run_result") == "Ready for reviewed apply design" and transaction_opened)

        lines = [
            "[SPLIT SELECTED PIPES ROLLBACK TEST]",
            "",
            "Rollback Test ID:",
            rollback_test_id,
            "",
            "Scope:",
            "latest split dry-run candidates / active document only",
            "",
            "Active document:",
            _document_title(doc),
            "",
            "Active view:",
            "{0} [{1}]".format(self._safe_active_view_name(), self._safe_active_view_type()),
            "",
            "Source dry-run:",
            "- Source status: {0}".format(source.get("source_status")),
        ]
        if has_source:
            lines.extend(
                [
                    "- Source feature: {0}".format(source.get("source_feature_id")),
                    "- Source prompt: {0}".format(source.get("source_prompt")),
                    "- Source header: {0}".format(source.get("source_header")),
                    "- Eligible pipes: {0}".format(source.get("eligible_pipe_count")),
                    "- Candidate split points: {0}".format(source.get("candidate_count") or len(candidates)),
                    "- Dry-run result: {0}".format(source.get("dry_run_result")),
                ]
            )
        lines.extend(
            [
                "",
                "Execution status:",
                "- Rollback test requested: true",
                "- Confirmation token present: {0}".format("true" if token_present else "false"),
                "- Execution available: {0}".format("true" if execution_available else "false"),
                "- Transaction opened: {0}".format("true" if transaction_opened else "false"),
                "- BreakCurve called: {0}".format("true" if breakcurve_called else "false"),
                "- Transaction group rolled back: {0}".format("true" if group_rolled_back else "false"),
                "- Persistent model changes: false",
            ]
        )
        if not has_source:
            lines.extend(
                [
                    "",
                    "Rollback-test result:",
                    result_text,
                    "",
                    "Reason:",
                    reason_text,
                    "",
                    "Required previous step:",
                    "dry run split selected pipes",
                ]
            )
        elif not token_present:
            lines.extend(
                [
                    "",
                    "Rollback-test result:",
                    result_text,
                    "",
                    "Required command:",
                    "run split rollback test ROLLBACK-TEST-OK",
                ]
            )
        else:
            verification_warnings = 0
            original_resolve = 0
            new_removed = 0
            length_restored = 0
            for result in results:
                after_length = self._safe_pipe_length_by_id(result.get("pipe_id"))
                if after_length is not None:
                    original_resolve += 1
                if result.get("new_id") is not None and self._safe_pipe_length_by_id(result.get("new_id")) is None:
                    new_removed += 1
                if after_length is not None and result.get("original_length") is not None and abs(after_length - result.get("original_length")) <= ROLLBACK_SPLIT_LENGTH_TOLERANCE_FT:
                    length_restored += 1
                else:
                    verification_warnings += 1
            lines.extend(
                [
                    "",
                    "Rollback test summary:",
                    "- Candidates available from dry-run: {0}".format(len(candidates)),
                    "- Candidates selected for rollback test: {0}".format(min(len(candidates), MAX_ROLLBACK_SPLIT_CANDIDATES)),
                    "- Successful temporary splits: {0}".format(len(results)),
                    "- Failed temporary splits: {0}".format(len(failures)),
                    "- Skipped candidates: {0}".format(len(skipped)),
                    "- Processing cap: {0}".format(MAX_ROLLBACK_SPLIT_CANDIDATES),
                    "- Result: Rolled back" if group_rolled_back else "- Result: Rollback incomplete or not opened",
                    "",
                    "Candidate results:",
                ]
            )
            if results:
                for index, result in enumerate(results):
                    lines.extend(
                        [
                            "{0}. Pipe {1}".format(index + 1, result.get("pipe_id")),
                            "   - Candidate point: {0}".format(_split_pipe_dry_run_xyz_text(result.get("point"))),
                            "   - Original length before rollback test: {0}".format(_split_pipe_dry_run_length_text(result.get("original_length"))),
                            "   - Temporary returned new pipe id: {0}".format(result.get("new_id")),
                            "   - Temporary original segment length: {0}".format(_split_pipe_dry_run_length_text(result.get("temp_original_length"))),
                            "   - Temporary new segment length: {0}".format(_split_pipe_dry_run_length_text(result.get("temp_new_length"))),
                            "   - Rollback verification: original restored check performed after TransactionGroup rollback",
                            "   - Status: {0}".format(result.get("status")),
                        ]
                    )
            else:
                lines.append("- none")
            lines.append("")
            lines.append("Failed candidates:")
            if failures:
                for failure in failures:
                    lines.append("- Pipe {0} | reason: {1}".format(failure.get("pipe_id"), failure.get("reason")))
            else:
                lines.append("- none")
            lines.append("")
            lines.append("Skipped candidates:")
            if skipped:
                for item in skipped:
                    lines.append("- Pipe {0} | reason: {1}".format(item.get("pipe_id"), item.get("reason")))
            else:
                lines.append("- none")
            lines.extend(
                [
                    "",
                    "Rollback verification:",
                    "- Original pipe ids still resolve after rollback: {0}/{1}".format(original_resolve, len(results)),
                    "- Temporary returned new pipe ids no longer resolve after rollback: {0}/{1}".format(new_removed, len(results)),
                    "- Original lengths restored within tolerance: {0}/{1}".format(length_restored, len(results)),
                    "- Verification warnings: {0}".format(verification_warnings),
                    "",
                    "Rollback-test result:",
                    result_text,
                ]
            )
            if reason_text:
                lines.extend(["", "Reason:", reason_text])
        lines.extend(
            [
                "",
                "Safety:",
                "- Rollback test only.",
                "- TransactionGroup was rolled back." if group_rolled_back else "- No persistent TransactionGroup assimilation occurred.",
                "- No persistent pipe split was applied.",
                "- Revit model data should be unchanged.",
                "- This is not a reviewed apply feature.",
                "",
                "Next required feature:",
                "MEP-WR-003 or MEP-WR-002B - Split Selected Pipes Persistent Reviewed Apply",
            ]
        )
        report_text = "\n".join(lines)
        self.latest_split_rollback_test_state = {
            "rollback_test_id": rollback_test_id,
            "feature_id": "MEP-WR-002",
            "source_prompt": safe_str(prompt),
            "created_timestamp_local": time.strftime("%Y-%m-%d %H:%M:%S"),
            "document_title": _document_title(doc),
            "document_path": self._safe_document_path(),
            "active_view_name": self._safe_active_view_name(),
            "active_view_type": self._safe_active_view_type(),
            "source_dry_run_id": source.get("dry_run_id"),
            "source_feature_id": source.get("source_feature_id"),
            "source_report_header": source.get("source_header"),
            "rollback_report_header": "[SPLIT SELECTED PIPES ROLLBACK TEST]",
            "confirmation_token_present": token_present,
            "transaction_opened": transaction_opened,
            "breakcurve_called": breakcurve_called,
            "transaction_group_rolled_back": group_rolled_back,
            "persistent_model_changes": False,
            "candidates_available": len(candidates),
            "candidates_tested": len(results) + len(failures),
            "temporary_success_count": len(results),
            "failure_count": len(failures),
            "skipped_count": len(skipped),
            "verification_warning_count": verification_warnings if token_present and has_source else 0,
            "rollback_test_result": result_text,
            "deterministic_route": True,
            "report_text": report_text,
        }
        return report_text

    def _latest_split_apply_sources(self):
        dry_source = self._latest_split_dry_run_source_for_rollback()
        rollback_state = self.latest_split_rollback_test_state
        rollback_report = rollback_state.get("report_text", "") if rollback_state else ""
        rollback_result = rollback_state.get("rollback_test_result", "") if rollback_state else ""
        success_ids = []
        if rollback_report:
            try:
                candidate_section = rollback_report.split("Candidate results:", 1)[1].split("Failed candidates:", 1)[0]
            except:
                candidate_section = ""
            for match in re.finditer(r"(?m)^\d+\. Pipe\s+(\d+)", safe_str(candidate_section)):
                try:
                    success_ids.append(int(match.group(1)))
                except:
                    pass
        rollback_source = {
            "source_status": "available" if rollback_state and rollback_report else "unavailable",
            "source_feature_id": "MEP-WR-002" if rollback_state else "none",
            "source_prompt": rollback_state.get("source_prompt", "") if rollback_state else "none",
            "source_header": rollback_state.get("rollback_report_header", "none") if rollback_state else "none",
            "rollback_test_result": rollback_result or "Unavailable",
            "successful_pipe_ids": success_ids,
            "document_title": rollback_state.get("document_title", "") if rollback_state else "",
            "document_path": rollback_state.get("document_path", None) if rollback_state else None,
            "report_text": rollback_report,
        }
        return dry_source, rollback_source

    def _split_apply_candidate_lists(self, dry_source, rollback_source):
        dry_candidates = dry_source.get("candidates") or []
        rollback_success_ids = set(rollback_source.get("successful_pipe_ids") or [])
        eligible = []
        blocked = []
        for index, candidate in enumerate(dry_candidates):
            item = dict(candidate)
            item["candidate_number"] = index + 1
            pipe_id = item.get("pipe_id")
            if pipe_id in rollback_success_ids:
                eligible.append(item)
            else:
                item["blocked_reason"] = "not rollback-tested / capped in rollback test"
                blocked.append(item)
        return eligible, blocked

    def _selected_split_apply_candidate(self, selection, dry_source):
        candidates = dry_source.get("candidates") or []
        kind = selection.get("kind")
        value = selection.get("value")
        if kind == "candidate_number":
            if value is None or value < 1 or value > len(candidates):
                return None
            item = dict(candidates[value - 1])
            item["candidate_number"] = value
            return item
        if kind == "pipe_id":
            for index, candidate in enumerate(candidates):
                try:
                    if int(candidate.get("pipe_id")) == int(value):
                        item = dict(candidate)
                        item["candidate_number"] = index + 1
                        return item
                except:
                    pass
        return None

    def _validate_persistent_split_candidate(self, candidate, rollback_source):
        if not candidate:
            return None, "candidate not found"
        try:
            pipe_id = int(candidate.get("pipe_id"))
        except:
            return None, "candidate pipe id is unreadable"
        if pipe_id not in set(rollback_source.get("successful_pipe_ids") or []):
            return None, "candidate was not validated by MEP-WR-002 rollback test"
        valid, reason = self._validate_split_rollback_candidate(candidate)
        if not valid:
            return None, reason
        return valid, ""

    def _run_single_split_persistent_apply(self, validated):
        transaction_opened = False
        breakcurve_called = False
        group_assimilated = False
        group = None
        trans = None
        warnings = []
        try:
            try:
                from Autodesk.Revit.DB.Plumbing import PlumbingUtils
            except:
                from Autodesk.Revit.DB import PlumbingUtils
            group = DB.TransactionGroup(doc, "AI Split Selected Pipe Reviewed Apply")
            group.Start()
            transaction_opened = True
            trans = DB.Transaction(doc, "AI apply one pipe split candidate")
            trans.Start()
            breakcurve_called = True
            new_id = PlumbingUtils.BreakCurve(doc, validated.get("pipe").Id, validated.get("point"))
            try:
                doc.Regenerate()
            except:
                pass
            try:
                new_id_value = new_id.IntegerValue
            except:
                new_id_value = None
            original_segment = self._safe_pipe_length_by_id(validated.get("pipe_id"))
            new_segment = self._safe_pipe_length_by_id(new_id_value) if new_id_value is not None else None
            if new_id_value is None or doc.GetElement(DB.ElementId(int(new_id_value))) is None:
                try:
                    trans.RollBack()
                except:
                    pass
                try:
                    group.RollBack()
                except:
                    pass
                return {
                    "transaction_opened": transaction_opened,
                    "breakcurve_called": breakcurve_called,
                    "group_assimilated": False,
                    "persistent_model_changes": False,
                    "apply_result": "Failed",
                    "reason": "BreakCurve did not return a resolvable new pipe id.",
                    "warnings": warnings,
                }
            if original_segment is None or new_segment is None:
                try:
                    trans.RollBack()
                except:
                    pass
                try:
                    group.RollBack()
                except:
                    pass
                return {
                    "transaction_opened": transaction_opened,
                    "breakcurve_called": breakcurve_called,
                    "group_assimilated": False,
                    "persistent_model_changes": False,
                    "apply_result": "Failed",
                    "reason": "Post-split segment lengths were unreadable.",
                    "warnings": warnings,
                }
            combined = original_segment + new_segment
            original_length = validated.get("original_length")
            length_delta = abs(combined - original_length) if original_length is not None else None
            if length_delta is not None and length_delta > ROLLBACK_SPLIT_LENGTH_TOLERANCE_FT:
                warnings.append("Combined segment length differs from original length beyond tolerance.")
            trans.Commit()
            group.Assimilate()
            group_assimilated = True
            return {
                "transaction_opened": transaction_opened,
                "breakcurve_called": breakcurve_called,
                "group_assimilated": group_assimilated,
                "persistent_model_changes": True,
                "apply_result": "Applied",
                "returned_new_pipe_id": new_id_value,
                "original_segment_length_after_apply": original_segment,
                "new_segment_length_after_apply": new_segment,
                "combined_segment_length": combined,
                "length_delta": length_delta,
                "warnings": warnings,
            }
        except Exception as exc:
            try:
                if trans is not None:
                    trans.RollBack()
            except:
                pass
            try:
                if group is not None and not group_assimilated:
                    group.RollBack()
            except:
                pass
            return {
                "transaction_opened": transaction_opened,
                "breakcurve_called": breakcurve_called,
                "group_assimilated": group_assimilated,
                "persistent_model_changes": group_assimilated,
                "apply_result": "Failed",
                "reason": safe_str(exc),
                "warnings": warnings,
            }

    def _format_split_reviewed_apply_report(self, prompt, request_kind, dry_source, rollback_source, selection, selected_candidate=None, apply_data=None, reason=None):
        apply_data = apply_data or {}
        reviewed_apply_id = "MEP-WR-003-{0}".format(time.strftime("%Y%m%d_%H%M%S"))
        token_present = _has_persistent_split_token(prompt)
        persistent_requested = request_kind == "apply"
        candidate_selected = selection.get("kind") in ("candidate_number", "pipe_id")
        transaction_opened = bool(apply_data.get("transaction_opened"))
        breakcurve_called = bool(apply_data.get("breakcurve_called"))
        group_assimilated = bool(apply_data.get("group_assimilated"))
        persistent_changes = bool(apply_data.get("persistent_model_changes"))
        eligible, blocked = self._split_apply_candidate_lists(dry_source, rollback_source)
        dry_available = dry_source.get("source_status") == "available"
        rollback_available = rollback_source.get("source_status") == "available"
        rollback_passed = rollback_source.get("rollback_test_result") in ("Passed", "Passed with warnings")
        if request_kind == "status":
            result_text = "Status only" if dry_available and rollback_available else "Not ready"
        elif selection.get("kind") == "batch":
            result_text = "Blocked"
        elif not candidate_selected:
            result_text = "Candidate selection required"
        elif not token_present:
            result_text = "Confirmation required"
        elif apply_data.get("apply_result") == "Applied":
            result_text = "Applied"
        else:
            result_text = "Not ready"

        lines = [
            "[SPLIT SELECTED PIPE REVIEWED APPLY]",
            "",
            "Reviewed Apply ID:",
            reviewed_apply_id,
            "",
            "Request:",
            "persistent apply requested" if persistent_requested else "status / readiness",
            "",
            "Scope:",
            "single split candidate / active document only",
            "",
            "Active document:",
            _document_title(doc),
            "",
            "Active view:",
            "{0} [{1}]".format(self._safe_active_view_name(), self._safe_active_view_type()),
            "",
            "Source dry-run:",
            "- Source status: {0}".format(dry_source.get("source_status")),
            "- Source feature: {0}".format(dry_source.get("source_feature_id")),
            "- Source prompt: {0}".format(dry_source.get("source_prompt")),
            "- Source header: {0}".format(dry_source.get("source_header")),
            "- Candidate split points: {0}".format(dry_source.get("candidate_count") or len(dry_source.get("candidates") or [])),
            "- Dry-run result: {0}".format(dry_source.get("dry_run_result")),
            "",
            "Source rollback test:",
            "- Source status: {0}".format(rollback_source.get("source_status")),
            "- Source feature: {0}".format(rollback_source.get("source_feature_id")),
            "- Source prompt: {0}".format(rollback_source.get("source_prompt")),
            "- Source header: {0}".format(rollback_source.get("source_header")),
            "- Rollback-test result: {0}".format(rollback_source.get("rollback_test_result")),
            "- Successful rollback-tested candidates: {0}".format(len(rollback_source.get("successful_pipe_ids") or [])),
        ]
        if request_kind == "status":
            lines.extend(["", "Eligible persistent apply candidates:"])
            if eligible:
                for item in eligible:
                    lines.append("{0}. Candidate {0} | Pipe {1} | rollback-tested: yes | original length: {2}".format(
                        item.get("candidate_number"),
                        item.get("pipe_id"),
                        _split_pipe_dry_run_length_text((item.get("estimated_segment_a") or 0.0) + (item.get("estimated_segment_b") or 0.0)),
                    ))
            else:
                lines.append("- none")
            lines.extend(["", "Blocked candidates:"])
            if blocked:
                for item in blocked:
                    lines.append("{0}. Candidate {0} | Pipe {1} | reason: {2}".format(item.get("candidate_number"), item.get("pipe_id"), item.get("blocked_reason")))
            else:
                lines.append("- none")
        else:
            lines.extend(
                [
                    "",
                    "Selected candidate:",
                    "- Candidate: {0}".format(selection.get("value") if selection.get("value") is not None else "none"),
                    "- Candidate status: {0}".format("available" if selected_candidate else "unavailable"),
                ]
            )
            if selected_candidate:
                lines.extend(
                    [
                        "- Candidate number: {0}".format(selected_candidate.get("candidate_number")),
                        "- Original pipe id: {0}".format(selected_candidate.get("pipe_id")),
                        "- Candidate point: {0}".format(_split_pipe_dry_run_xyz_text(selected_candidate.get("point"))),
                    ]
                )
        lines.extend(
            [
                "",
                "Execution status:",
                "- Persistent apply requested: {0}".format("true" if persistent_requested else "false"),
                "- Candidate selected: {0}".format("true" if candidate_selected else "false"),
                "- Confirmation token present: {0}".format("true" if token_present else "false"),
                "- Execution available: {0}".format("true" if transaction_opened and token_present and candidate_selected else "false"),
                "- Transaction opened: {0}".format("true" if transaction_opened else "false"),
                "- BreakCurve called: {0}".format("true" if breakcurve_called else "false"),
                "- Transaction group assimilated: {0}".format("true" if group_assimilated else "false"),
                "- Persistent model changes: {0}".format("true" if persistent_changes else "false"),
            ]
        )
        if apply_data.get("apply_result") == "Applied":
            original_length = selected_candidate.get("estimated_segment_a") + selected_candidate.get("estimated_segment_b")
            lines.extend(
                [
                    "",
                    "Apply result:",
                    "- Returned new pipe id: {0}".format(apply_data.get("returned_new_pipe_id")),
                    "- Original pipe id after apply: {0}".format(selected_candidate.get("pipe_id")),
                    "- Original segment length after apply: {0}".format(_split_pipe_dry_run_length_text(apply_data.get("original_segment_length_after_apply"))),
                    "- New segment length after apply: {0}".format(_split_pipe_dry_run_length_text(apply_data.get("new_segment_length_after_apply"))),
                    "- Combined segment length: {0}".format(_split_pipe_dry_run_length_text(apply_data.get("combined_segment_length"))),
                    "- Length delta from original: {0}".format(_split_pipe_dry_run_length_text(apply_data.get("length_delta"))),
                    "- Warnings: {0}".format(len(apply_data.get("warnings") or [])),
                    "",
                    "Expected source lengths:",
                    "- Original length before apply: {0}".format(_split_pipe_dry_run_length_text(original_length)),
                    "- Expected segment A: {0}".format(_split_pipe_dry_run_length_text(selected_candidate.get("estimated_segment_a"))),
                    "- Expected segment B: {0}".format(_split_pipe_dry_run_length_text(selected_candidate.get("estimated_segment_b"))),
                ]
            )
        lines.extend(["", "Reviewed apply result:", result_text])
        if reason or apply_data.get("reason"):
            lines.extend(["", "Reason:", reason or apply_data.get("reason")])
        if result_text == "Candidate selection required":
            lines.extend(
                [
                    "",
                    "Suggested commands:",
                    "- apply split candidate 1 PERSISTENT-SPLIT-OK",
                    "- apply pipe split candidate 3003513 PERSISTENT-SPLIT-OK",
                ]
            )
        elif result_text == "Confirmation required":
            lines.extend(["", "Required command:", "apply split candidate <n> PERSISTENT-SPLIT-OK"])
        elif result_text == "Not ready":
            lines.extend(["", "Required previous steps:", "1. dry run split selected pipes", "2. run split rollback test ROLLBACK-TEST-OK"])
        elif result_text == "Status only":
            lines.extend(["", "Suggested command:", "apply split candidate 1 PERSISTENT-SPLIT-OK"])
        lines.extend(
            [
                "",
                "Safety:",
                "- Persistent reviewed apply was explicitly confirmed." if persistent_changes else "- No persistent split was applied.",
                "- Exactly one candidate was applied." if persistent_changes else "- No transaction opened." if not transaction_opened else "- Transaction was rolled back or not assimilated.",
                "- No batch apply was performed.",
                "- No connector traversal was performed.",
                "- No geometry extraction was performed.",
                "- Revit model data was modified by this reviewed apply." if persistent_changes else "- Revit model data was not modified.",
            ]
        )
        if persistent_changes:
            lines.extend(["", "Next recommended step:", "Run dry run split selected pipes again to verify the updated candidate state."])
        report_text = "\n".join(lines)
        self.latest_split_reviewed_apply_state = {
            "reviewed_apply_id": reviewed_apply_id,
            "feature_id": "MEP-WR-003",
            "source_dry_run_feature_id": "MEP-WR-001",
            "source_rollback_feature_id": "MEP-WR-002",
            "source_prompt": safe_str(prompt),
            "created_timestamp_local": time.strftime("%Y-%m-%d %H:%M:%S"),
            "document_title": _document_title(doc),
            "document_path": self._safe_document_path(),
            "active_view_name": self._safe_active_view_name(),
            "active_view_type": self._safe_active_view_type(),
            "source_dry_run_header": "[SPLIT SELECTED PIPES DRY RUN]",
            "source_rollback_header": "[SPLIT SELECTED PIPES ROLLBACK TEST]",
            "report_header": "[SPLIT SELECTED PIPE REVIEWED APPLY]",
            "selected_candidate_number": selected_candidate.get("candidate_number") if selected_candidate else None,
            "selected_pipe_id": selected_candidate.get("pipe_id") if selected_candidate else None,
            "returned_new_pipe_id": apply_data.get("returned_new_pipe_id"),
            "original_length_before_apply": (selected_candidate.get("estimated_segment_a") + selected_candidate.get("estimated_segment_b")) if selected_candidate else None,
            "original_segment_length_after_apply": apply_data.get("original_segment_length_after_apply"),
            "new_segment_length_after_apply": apply_data.get("new_segment_length_after_apply"),
            "combined_segment_length": apply_data.get("combined_segment_length"),
            "length_delta": apply_data.get("length_delta"),
            "confirmation_token_present": token_present,
            "transaction_opened": transaction_opened,
            "breakcurve_called": breakcurve_called,
            "transaction_group_assimilated": group_assimilated,
            "persistent_model_changes": persistent_changes,
            "apply_result": result_text,
            "deterministic_route": True,
            "report_text": report_text,
        }
        return report_text

    def _parse_reviewed_apply_report_source(self, report_text):
        text = safe_str(report_text)
        def int_match(pattern):
            match = re.search(pattern, text, re.I)
            try:
                return int(match.group(1)) if match else None
            except:
                return None
        def length_match(pattern):
            match = re.search(pattern, text, re.I)
            try:
                return float(match.group(1)) if match else None
            except:
                return None
        return {
            "source_status": "available",
            "source_feature_id": "MEP-WR-003",
            "source_prompt": self.latest_deterministic_report.get("source_prompt", "") if self.latest_deterministic_report else "",
            "source_header": "[SPLIT SELECTED PIPE REVIEWED APPLY]",
            "apply_result": "Applied" if re.search(r"Reviewed apply result:\s*\nApplied", text, re.I) else "",
            "persistent_model_changes": bool(re.search(r"Persistent model changes:\s*true", text, re.I)),
            "original_pipe_id": int_match(r"Original pipe id(?: after apply)?:\s*(\d+)"),
            "returned_new_pipe_id": int_match(r"Returned new pipe id:\s*(\d+)"),
            "source_original_length_before_apply": length_match(r"Original length before apply:\s*([0-9.]+)\s*ft"),
            "source_original_segment_length_after_apply": length_match(r"Original segment length after apply:\s*([0-9.]+)\s*ft"),
            "source_new_segment_length_after_apply": length_match(r"New segment length after apply:\s*([0-9.]+)\s*ft"),
            "source_combined_length": length_match(r"Combined segment length:\s*([0-9.]+)\s*ft"),
            "source_length_delta": length_match(r"Length delta from original:\s*([0-9.]+)\s*ft"),
            "document_title": _document_title(doc),
            "document_path": self._safe_document_path(),
        }

    def _latest_split_apply_verification_source(self, prompt):
        state = self.latest_split_reviewed_apply_state
        if state and state.get("apply_result") == "Applied":
            return {
                "source_status": "available",
                "source_feature_id": "MEP-WR-003",
                "source_prompt": state.get("source_prompt", ""),
                "source_header": state.get("report_header", "[SPLIT SELECTED PIPE REVIEWED APPLY]"),
                "apply_result": state.get("apply_result", ""),
                "persistent_model_changes": bool(state.get("persistent_model_changes")),
                "original_pipe_id": state.get("selected_pipe_id"),
                "returned_new_pipe_id": state.get("returned_new_pipe_id"),
                "source_original_length_before_apply": state.get("original_length_before_apply"),
                "source_original_segment_length_after_apply": state.get("original_segment_length_after_apply"),
                "source_new_segment_length_after_apply": state.get("new_segment_length_after_apply"),
                "source_combined_length": state.get("combined_segment_length"),
                "source_length_delta": state.get("length_delta"),
                "document_title": state.get("document_title", ""),
                "document_path": state.get("document_path", None),
            }
        report = self.latest_deterministic_report
        if report and report.get("report_header") == "[SPLIT SELECTED PIPE REVIEWED APPLY]":
            parsed = self._parse_reviewed_apply_report_source(report.get("report_text", ""))
            if parsed.get("original_pipe_id") and parsed.get("returned_new_pipe_id"):
                parsed["source_prompt"] = report.get("source_prompt", "")
                return parsed
        original_id, new_id = _parse_explicit_split_verification_ids(prompt)
        if original_id and new_id:
            return {
                "source_status": "explicit prompt IDs",
                "source_feature_id": "explicit prompt IDs",
                "source_prompt": safe_str(prompt),
                "source_header": "explicit prompt IDs",
                "apply_result": "unknown",
                "persistent_model_changes": False,
                "original_pipe_id": original_id,
                "returned_new_pipe_id": new_id,
                "source_original_length_before_apply": None,
                "source_original_segment_length_after_apply": None,
                "source_new_segment_length_after_apply": None,
                "source_combined_length": None,
                "source_length_delta": None,
                "document_title": _document_title(doc),
                "document_path": self._safe_document_path(),
            }
        return {
            "source_status": "unavailable",
            "source_feature_id": "none",
            "source_prompt": "none",
            "source_header": "none",
            "apply_result": "Unavailable",
            "persistent_model_changes": False,
            "original_pipe_id": None,
            "returned_new_pipe_id": None,
        }

    def _pipe_verification_info(self, pipe_id):
        info = {
            "pipe_id": pipe_id,
            "resolves": False,
            "is_pipe": False,
            "length_readable": False,
            "length": None,
            "reason": "",
        }
        try:
            elem = doc.GetElement(DB.ElementId(int(pipe_id)))
        except Exception as exc:
            info["reason"] = safe_str(exc)
            return info
        if elem is None:
            info["reason"] = "element id does not resolve"
            return info
        info["resolves"] = True
        info["is_pipe"] = bool(_split_pipe_dry_run_is_pipe(elem))
        if not info["is_pipe"]:
            info["reason"] = "element is not a pipe"
            return info
        try:
            curve, status = _split_pipe_dry_run_safe_curve(elem)
            if status != "ok":
                info["reason"] = status.replace("_", " ")
                return info
            info["length"] = float(curve.Length)
            info["length_readable"] = True
        except Exception as exc:
            info["reason"] = safe_str(exc)
        return info

    def _length_delta(self, value, reference):
        if value is None or reference is None:
            return None
        try:
            return abs(float(value) - float(reference))
        except:
            return None

    def _split_apply_verification_check(self, source):
        original = self._pipe_verification_info(source.get("original_pipe_id"))
        returned = self._pipe_verification_info(source.get("returned_new_pipe_id"))
        combined = None
        if original.get("length_readable") and returned.get("length_readable"):
            combined = original.get("length") + returned.get("length")
        source_combined = source.get("source_combined_length")
        source_original = source.get("source_original_length_before_apply")
        comparison_source = source_combined if source_combined is not None else source_original
        delta_combined = self._length_delta(combined, source_combined)
        delta_original = self._length_delta(combined, source_original)
        delta_comparison = self._length_delta(combined, comparison_source)
        delta_original_segment = self._length_delta(original.get("length"), source.get("source_original_segment_length_after_apply"))
        delta_new_segment = self._length_delta(returned.get("length"), source.get("source_new_segment_length_after_apply"))
        warnings = []
        failures = []
        both_resolve = bool(original.get("resolves") and returned.get("resolves"))
        both_pipes = bool(original.get("is_pipe") and returned.get("is_pipe"))
        both_lengths_readable = bool(original.get("length_readable") and returned.get("length_readable"))
        combined_check = "not evaluated"
        if both_resolve and both_pipes and both_lengths_readable and combined is not None and comparison_source is not None:
            if delta_comparison is not None and delta_comparison <= SPLIT_APPLY_VERIFICATION_LENGTH_TOLERANCE_FT:
                combined_check = "pass"
            else:
                combined_check = "fail"
        source_persistent_check = "not evaluated"
        if source.get("source_status") != "explicit prompt IDs":
            if source.get("source_feature_id") == "MEP-WR-003" and source.get("apply_result") == "Applied" and bool(source.get("persistent_model_changes")):
                source_persistent_check = "pass"
            elif source.get("source_feature_id") == "MEP-WR-003" or source.get("apply_result") or source.get("persistent_model_changes") is not None:
                source_persistent_check = "fail"
        if not original.get("resolves"):
            failures.append("Original pipe id does not resolve.")
        if not returned.get("resolves"):
            failures.append("Returned new pipe id does not resolve.")
        if original.get("resolves") and not original.get("is_pipe"):
            failures.append("Original element is not a pipe.")
        if returned.get("resolves") and not returned.get("is_pipe"):
            failures.append("Returned new element is not a pipe.")
        if original.get("is_pipe") and not original.get("length_readable"):
            failures.append("Original pipe length is unreadable.")
        if returned.get("is_pipe") and not returned.get("length_readable"):
            failures.append("Returned new pipe length is unreadable.")
        if delta_combined is not None and delta_combined > SPLIT_APPLY_VERIFICATION_LENGTH_TOLERANCE_FT:
            failures.append("Combined length differs from source combined length beyond tolerance.")
        if delta_original is not None and delta_original > SPLIT_APPLY_VERIFICATION_LENGTH_TOLERANCE_FT:
            failures.append("Combined length differs from source original length beyond tolerance.")
        if combined_check == "fail" and not any("Combined length differs" in failure for failure in failures):
            failures.append("Combined length differs from available source length beyond tolerance.")
        if delta_original_segment is not None and delta_original_segment > SPLIT_APPLY_VERIFICATION_LENGTH_TOLERANCE_FT:
            warnings.append("Current original segment differs from apply report segment length beyond tolerance.")
        if delta_new_segment is not None and delta_new_segment > SPLIT_APPLY_VERIFICATION_LENGTH_TOLERANCE_FT:
            warnings.append("Current returned segment differs from apply report segment length beyond tolerance.")
        if source.get("source_status") == "explicit prompt IDs" and (source_combined is None and source_original is None):
            warnings.append("Explicit ID source has no original pre-apply length for full source comparison.")
        if source_persistent_check == "fail":
            warnings.append("Source apply result does not explicitly confirm MEP-WR-003 Applied with persistent model changes true.")
        if source.get("document_title") and source.get("document_title") != _document_title(doc):
            warnings.append("Source document title differs from active document title.")
        if failures:
            result = "Failed"
        elif source.get("source_status") == "unavailable":
            result = "Not ready"
        elif combined_check == "pass" and source_persistent_check in ("pass", "not evaluated") and not warnings:
            result = "Verified"
        elif both_resolve and both_pipes and both_lengths_readable:
            result = "Verified with warnings"
        elif warnings:
            result = "Verified with warnings"
        else:
            result = "Failed"
        return {
            "original": original,
            "returned": returned,
            "combined": combined,
            "delta_combined": delta_combined,
            "delta_original": delta_original,
            "delta_comparison": delta_comparison,
            "combined_check": combined_check,
            "source_persistent_check": source_persistent_check,
            "delta_original_segment": delta_original_segment,
            "delta_new_segment": delta_new_segment,
            "warnings": warnings,
            "failures": failures,
            "result": result,
        }

    def _format_split_apply_verification_report(self, prompt, source):
        verification_id = "MEP-WR-004-{0}".format(time.strftime("%Y%m%d_%H%M%S"))
        has_source = source.get("source_status") != "unavailable"
        data = self._split_apply_verification_check(source) if has_source else {}
        original = data.get("original", {})
        returned = data.get("returned", {})
        lines = [
            "[SPLIT APPLY VERIFICATION REPORT]",
            "",
            "Verification ID:",
            verification_id,
            "",
            "Request:",
            "verification",
            "",
            "Scope:",
            "latest reviewed split apply result / active document only",
            "",
            "Active document:",
            _document_title(doc),
            "",
            "Active view:",
            "{0} [{1}]".format(self._safe_active_view_name(), self._safe_active_view_type()),
            "",
            "Source reviewed apply:",
            "- Source status: {0}".format(source.get("source_status")),
        ]
        if has_source:
            lines.extend(
                [
                    "- Source feature: {0}".format(source.get("source_feature_id")),
                    "- Source prompt: {0}".format(source.get("source_prompt")),
                    "- Source header: {0}".format(source.get("source_header")),
                    "- Reviewed apply result: {0}".format(source.get("apply_result")),
                    "- Persistent model changes: {0}".format("true" if source.get("persistent_model_changes") else "false"),
                    "",
                    "Source split result:",
                    "- Original pipe id: {0}".format(source.get("original_pipe_id")),
                    "- Returned new pipe id: {0}".format(source.get("returned_new_pipe_id")),
                    "- Original length before apply: {0}".format(_split_pipe_dry_run_length_text(source.get("source_original_length_before_apply"))),
                    "- Source original segment length after apply: {0}".format(_split_pipe_dry_run_length_text(source.get("source_original_segment_length_after_apply"))),
                    "- Source new segment length after apply: {0}".format(_split_pipe_dry_run_length_text(source.get("source_new_segment_length_after_apply"))),
                    "- Source combined segment length: {0}".format(_split_pipe_dry_run_length_text(source.get("source_combined_length"))),
                    "- Source length delta: {0}".format(_split_pipe_dry_run_length_text(source.get("source_length_delta"))),
                    "",
                    "Current verification:",
                    "- Original pipe resolves: {0}".format("true" if original.get("resolves") else "false"),
                    "- Returned new pipe resolves: {0}".format("true" if returned.get("resolves") else "false"),
                    "- Original element is pipe: {0}".format("true" if original.get("is_pipe") else "false"),
                    "- Returned new element is pipe: {0}".format("true" if returned.get("is_pipe") else "false"),
                    "- Original segment length readable: {0}".format("true" if original.get("length_readable") else "false"),
                    "- Returned new segment length readable: {0}".format("true" if returned.get("length_readable") else "false"),
                    "- Current original segment length: {0}".format(_split_pipe_dry_run_length_text(original.get("length"))),
                    "- Current new segment length: {0}".format(_split_pipe_dry_run_length_text(returned.get("length"))),
                    "- Current combined segment length: {0}".format(_split_pipe_dry_run_length_text(data.get("combined"))),
                    "- Delta from source combined length: {0}".format(_split_pipe_dry_run_length_text(data.get("delta_combined"))),
                    "- Delta from source original length before apply: {0}".format(_split_pipe_dry_run_length_text(data.get("delta_original"))),
                    "- Length tolerance: {0}".format(_split_pipe_dry_run_length_text(SPLIT_APPLY_VERIFICATION_LENGTH_TOLERANCE_FT)),
                    "",
                    "Verification checks:",
                    "- Original pipe id still exists: {0}".format("pass" if original.get("resolves") else "fail"),
                    "- Returned new pipe id exists: {0}".format("pass" if returned.get("resolves") else "fail"),
                    "- Both elements are pipes: {0}".format("pass" if original.get("is_pipe") and returned.get("is_pipe") else "fail"),
                    "- Segment lengths readable: {0}".format("pass" if original.get("length_readable") and returned.get("length_readable") else "fail"),
                    "- Combined length within tolerance: {0}".format(data.get("combined_check", "not evaluated")),
                    "- Source apply result was persistent: {0}".format(data.get("source_persistent_check", "not evaluated")),
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    "Reason:",
                    "No latest MEP-WR-003 reviewed apply state is available in the current AI Workbench session.",
                    "",
                    "Supported fallback:",
                    "verify split result pipe <original pipe id> new pipe <returned new pipe id>",
                ]
            )
        lines.extend(
            [
                "",
                "Execution status:",
                "- Verification requested: true",
                "- Transaction opened: false",
                "- BreakCurve called: false",
                "- Model modified: false",
                "",
                "Verification result:",
                data.get("result", "Not ready") if has_source else "Not ready",
                "",
                "Warnings:",
            ]
        )
        warnings = data.get("warnings") or []
        failures = data.get("failures") or []
        if warnings:
            for warning in warnings:
                lines.append("- {0}".format(warning))
        else:
            lines.append("- none")
        if failures:
            lines.extend(["", "Failures:"])
            for failure in failures:
                lines.append("- {0}".format(failure))
        lines.extend(
            [
                "",
                "Safety:",
                "- Read-only verification only.",
                "- No transaction opened.",
                "- No pipe was split.",
                "- Revit model data was not modified.",
            ]
        )
        report_text = "\n".join(lines)
        self.latest_split_apply_verification_state = {
            "verification_id": verification_id,
            "feature_id": "MEP-WR-004",
            "source_feature_id": source.get("source_feature_id"),
            "source_prompt": source.get("source_prompt"),
            "created_timestamp_local": time.strftime("%Y-%m-%d %H:%M:%S"),
            "document_title": _document_title(doc),
            "document_path": self._safe_document_path(),
            "active_view_name": self._safe_active_view_name(),
            "active_view_type": self._safe_active_view_type(),
            "report_header": "[SPLIT APPLY VERIFICATION REPORT]",
            "source_report_header": source.get("source_header"),
            "original_pipe_id": source.get("original_pipe_id"),
            "returned_new_pipe_id": source.get("returned_new_pipe_id"),
            "original_pipe_resolves": original.get("resolves", False),
            "returned_new_pipe_resolves": returned.get("resolves", False),
            "original_is_pipe": original.get("is_pipe", False),
            "returned_new_is_pipe": returned.get("is_pipe", False),
            "original_length_current": original.get("length"),
            "returned_new_length_current": returned.get("length"),
            "combined_length_current": data.get("combined"),
            "source_original_length_before_apply": source.get("source_original_length_before_apply"),
            "source_combined_length": source.get("source_combined_length"),
            "delta_from_source_combined": data.get("delta_combined"),
            "delta_from_original_before_apply": data.get("delta_original"),
            "verification_result": data.get("result", "Not ready") if has_source else "Not ready",
            "warning_count": len(data.get("warnings") or []),
            "deterministic_route": True,
            "model_modified": False,
            "transaction_opened": False,
            "breakcurve_called": False,
            "report_text": report_text,
        }
        return report_text

    def answer_split_apply_verification_question(self, prompt):
        if not _is_split_apply_verification_prompt(prompt):
            return None
        source = self._latest_split_apply_verification_source(prompt)
        return self._format_split_apply_verification_report(prompt, source)

    def answer_split_reviewed_apply_question(self, prompt):
        request_kind = _split_reviewed_apply_request_kind(prompt)
        if not request_kind:
            return None
        dry_source, rollback_source = self._latest_split_apply_sources()
        selection = _parse_split_apply_candidate_selection(prompt)
        selected_candidate = self._selected_split_apply_candidate(selection, dry_source)
        if request_kind == "status":
            return self._format_split_reviewed_apply_report(prompt, request_kind, dry_source, rollback_source, selection, selected_candidate)
        if selection.get("kind") == "batch":
            return self._format_split_reviewed_apply_report(prompt, request_kind, dry_source, rollback_source, selection, selected_candidate, reason="MEP-WR-003 applies exactly one explicitly selected candidate per command. Batch apply is not implemented.")
        if selection.get("kind") == "missing":
            return self._format_split_reviewed_apply_report(prompt, request_kind, dry_source, rollback_source, selection, selected_candidate, reason="MEP-WR-003 applies exactly one candidate. Specify candidate number or pipe id.")
        if not _has_persistent_split_token(prompt):
            return self._format_split_reviewed_apply_report(prompt, request_kind, dry_source, rollback_source, selection, selected_candidate)
        if dry_source.get("source_status") != "available":
            return self._format_split_reviewed_apply_report(prompt, request_kind, dry_source, rollback_source, selection, selected_candidate, reason="Latest MEP-WR-001 split dry-run state is missing.")
        if rollback_source.get("source_status") != "available":
            return self._format_split_reviewed_apply_report(prompt, request_kind, dry_source, rollback_source, selection, selected_candidate, reason="Latest MEP-WR-002 rollback-test state is missing.")
        if dry_source.get("document_title") and dry_source.get("document_title") != _document_title(doc):
            return self._format_split_reviewed_apply_report(prompt, request_kind, dry_source, rollback_source, selection, selected_candidate, reason="Latest dry-run document does not match the active document.")
        if rollback_source.get("document_title") and rollback_source.get("document_title") != _document_title(doc):
            return self._format_split_reviewed_apply_report(prompt, request_kind, dry_source, rollback_source, selection, selected_candidate, reason="Latest rollback-test document does not match the active document.")
        if rollback_source.get("rollback_test_result") not in ("Passed", "Passed with warnings"):
            return self._format_split_reviewed_apply_report(prompt, request_kind, dry_source, rollback_source, selection, selected_candidate, reason="Rollback test did not pass.")
        if not selected_candidate:
            return self._format_split_reviewed_apply_report(prompt, request_kind, dry_source, rollback_source, selection, selected_candidate, reason="Selected candidate was not found in the latest dry-run.")
        validated, reason = self._validate_persistent_split_candidate(selected_candidate, rollback_source)
        if not validated:
            return self._format_split_reviewed_apply_report(prompt, request_kind, dry_source, rollback_source, selection, selected_candidate, reason=reason)
        apply_data = self._run_single_split_persistent_apply(validated)
        return self._format_split_reviewed_apply_report(prompt, request_kind, dry_source, rollback_source, selection, selected_candidate, apply_data, apply_data.get("reason"))

    def answer_split_selected_pipes_rollback_test_question(self, prompt):
        if not _is_split_rollback_test_prompt(prompt):
            return None
        source = self._latest_split_dry_run_source_for_rollback()
        token_present = _has_rollback_test_token(prompt)
        if source.get("source_status") != "available":
            return self._format_split_rollback_test_report(prompt, source, token_present)
        if not token_present:
            return self._format_split_rollback_test_report(prompt, source, token_present)
        if source.get("dry_run_result") != "Ready for reviewed apply design":
            return self._format_split_rollback_test_report(prompt, source, token_present, reason="Source dry-run result is not ready for reviewed apply design.")
        candidates = source.get("candidates") or []
        validated = []
        skipped = []
        for candidate in candidates[:MAX_ROLLBACK_SPLIT_CANDIDATES]:
            valid, reason = self._validate_split_rollback_candidate(candidate)
            if valid:
                validated.append(valid)
            else:
                skipped.append({"pipe_id": candidate.get("pipe_id"), "reason": reason})
        if len(candidates) > MAX_ROLLBACK_SPLIT_CANDIDATES:
            for candidate in candidates[MAX_ROLLBACK_SPLIT_CANDIDATES:]:
                skipped.append({"pipe_id": candidate.get("pipe_id"), "reason": "rollback test capped for safety"})
        if not validated:
            return self._format_split_rollback_test_report(prompt, source, token_present, {"results": [], "failures": [], "transaction_opened": False, "breakcurve_called": False, "group_rolled_back": False}, skipped, "No candidates passed current rollback-test validation.")
        rollback_data = self._run_split_rollback_batch(validated)
        return self._format_split_rollback_test_report(prompt, source, token_present, rollback_data, skipped)

    def answer_split_selected_pipes_dry_run_question(self, prompt):
        if not _is_split_selected_pipes_dry_run_prompt(prompt):
            return None
        report_text = split_selected_pipes_dry_run(doc, uidoc, {"requested_prompt": prompt, "prompt_text": prompt})
        def metric(label):
            match = re.search(r"- {0}: (\d+)".format(re.escape(label)), report_text)
            if not match and label == "Total selected elements":
                match = re.search(r"Total selected elements: (\d+)", report_text)
            try:
                return int(match.group(1)) if match else 0
            except:
                return 0
        result_match = re.search(r"Dry-run result:\s*\n([^\n]+)", report_text)
        self.latest_split_pipe_dry_run = {
            "dry_run_id": "MEP-WR-001-{0}".format(time.strftime("%Y%m%d_%H%M%S")),
            "feature_id": "MEP-WR-001",
            "source_prompt": safe_str(prompt),
            "created_timestamp_local": time.strftime("%Y-%m-%d %H:%M:%S"),
            "document_title": _document_title(doc),
            "document_path": self._safe_document_path(),
            "active_view_name": self._safe_active_view_name(),
            "active_view_type": self._safe_active_view_type(),
            "scope": "selected elements / active document only",
            "total_selected": metric("Total selected elements"),
            "inspected_count": metric("Elements inspected"),
            "eligible_pipe_count": metric("Eligible pipes"),
            "candidate_count": metric("Candidate split points"),
            "skipped_count": metric("Skipped elements"),
            "warning_count": metric("Warnings"),
            "dry_run_result": result_match.group(1).strip() if result_match else "",
            "candidate_summary": "midpoint candidate only",
            "report_text": report_text,
            "deterministic_route": True,
            "read_only": True,
            "model_modified": False,
            "execution_available": False,
        }
        return report_text

    def _latest_guard_source_state(self, prompt):
        normalized = _normalize_deterministic_route_text(prompt)
        prefer_proposal = "proposal" in normalized or "reviewed action" in normalized or "reviewed proposal" in normalized
        prefer_dry_run = "dry run" in normalized

        def split_state():
            state = self.latest_split_pipe_dry_run
            if not state:
                return None
            return {
                "source_state_type": "split_dry_run",
                "source_feature_id": "MEP-WR-001",
                "source_report_header": "[SPLIT SELECTED PIPES DRY RUN]",
                "source_report_prompt": state.get("source_prompt", ""),
                "source_report_timestamp": state.get("created_timestamp_local", ""),
                "source_status": "available",
                "summary": [
                    "- Eligible pipes: {0}".format(state.get("eligible_pipe_count", 0)),
                    "- Candidate split points: {0}".format(state.get("candidate_count", 0)),
                    "- Skipped elements: {0}".format(state.get("skipped_count", 0)),
                    "- Dry-run result: {0}".format(state.get("dry_run_result", "")),
                    "- Exportable source header: [SPLIT SELECTED PIPES DRY RUN]",
                ],
            }

        def proposal_state():
            state = self.latest_reviewed_action_proposal
            if not state:
                return None
            return {
                "source_state_type": "reviewed_proposal",
                "source_feature_id": "MEP-ACT-001",
                "source_report_header": "[REVIEWED ACTION PROPOSAL]",
                "source_report_prompt": state.get("source_prompt", ""),
                "source_report_timestamp": state.get("created_timestamp_local", ""),
                "source_status": "available",
                "summary": [
                    "- Action label: {0}".format(state.get("action_label", "")),
                    "- Proposal status: {0}".format(state.get("proposal_status", "")),
                    "- Eligible count: {0}".format(state.get("eligible_count", 0)),
                    "- Skipped count: {0}".format(state.get("skipped_count", 0)),
                    "- Warning count: {0}".format(state.get("warning_count", 0)),
                ],
            }

        def deterministic_state():
            state = self.latest_deterministic_report
            if not state:
                return None
            header = state.get("report_header", "")
            if header == "[SPLIT SELECTED PIPES DRY RUN]":
                source_feature = "MEP-WR-001"
                source_type = "deterministic_report"
            elif header == "[REVIEWED ACTION PROPOSAL]":
                source_feature = "MEP-ACT-001"
                source_type = "deterministic_report"
            else:
                return None
            return {
                "source_state_type": source_type,
                "source_feature_id": source_feature,
                "source_report_header": header,
                "source_report_prompt": state.get("source_prompt", ""),
                "source_report_timestamp": state.get("report_timestamp", ""),
                "source_status": "available",
                "summary": [
                    "- Source report scope: {0}".format(state.get("report_scope", "")),
                    "- Exportable source header: {0}".format(header),
                ],
            }

        if prefer_dry_run:
            ordered = [split_state, proposal_state, deterministic_state]
        elif prefer_proposal:
            ordered = [proposal_state, split_state, deterministic_state]
        else:
            ordered = [split_state, proposal_state, deterministic_state]
        for getter in ordered:
            source = getter()
            if source:
                return source
        return {
            "source_state_type": "none",
            "source_feature_id": "none",
            "source_report_header": "none",
            "source_report_prompt": "none",
            "source_report_timestamp": "",
            "source_status": "unavailable",
            "summary": [],
        }

    def answer_reviewed_action_confirmation_guard_question(self, prompt):
        kind = _reviewed_action_confirmation_guard_request_kind(prompt)
        if not kind:
            return None
        execution_requested = kind == "confirmation"
        source = self._latest_guard_source_state(prompt)
        has_source = source.get("source_state_type") != "none"
        guard_id = "MEP-ACT-002-{0}".format(time.strftime("%Y%m%d_%H%M%S"))
        if not has_source:
            confirmation_result = "Not ready"
            reason = "No reviewed action proposal or split dry-run is available in the current AI Workbench session. Run a proposal or dry-run first."
        elif execution_requested:
            confirmation_result = "Blocked"
            reason = "The latest reviewed proposal or dry-run cannot be applied because MEP-WR-002 reviewed apply is not implemented. MEP-ACT-002 only validates confirmation flow and prevents accidental model mutation."
        else:
            confirmation_result = "Status only"
            reason = "MEP-ACT-002 is a confirmation guard only. No reviewed apply feature is implemented yet."
        request_label = "confirmation / apply requested" if execution_requested else "status"

        lines = [
            "[REVIEWED ACTION CONFIRMATION GUARD]",
            "",
            "Guard ID:",
            guard_id,
            "",
            "Request:",
            request_label,
            "",
            "Scope:",
            "session-local reviewed action state / active document only",
            "",
            "Active document:",
            _document_title(doc),
            "",
            "Active view:",
            "{0} [{1}]".format(self._safe_active_view_name(), self._safe_active_view_type()),
            "",
            "Latest reviewed source:",
            "- Source type: {0}".format(source.get("source_state_type")),
            "- Source feature: {0}".format(source.get("source_feature_id")),
            "- Source prompt: {0}".format(source.get("source_report_prompt")),
            "- Source header: {0}".format(source.get("source_report_header")),
            "- Source status: {0}".format(source.get("source_status")),
        ]
        if source.get("source_report_timestamp"):
            lines.append("- Source timestamp: {0}".format(source.get("source_report_timestamp")))
        if source.get("summary"):
            lines.extend(["", "Source summary:"])
            lines.extend(source.get("summary"))
        lines.extend(
            [
                "",
                "Execution status:",
                "- Execution requested: {0}".format("true" if execution_requested else "false"),
                "- Execution available: false",
                "- Execution performed: false",
                "",
                "Confirmation result:",
                confirmation_result,
                "",
                "Reason:",
                reason,
            ]
        )
        if not has_source:
            lines.extend(
                [
                    "",
                    "Suggested prompts:",
                    "- propose split selected pipes",
                    "- dry run split selected pipes",
                    "- split selected pipes dry run",
                ]
            )
        lines.extend(
            [
                "",
                "Next required feature:",
                "MEP-WR-002 - Split Selected Pipes Reviewed Apply",
                "",
                "Safety:",
                "- Confirmation guard only.",
                "- Read-only.",
                "- No transaction opened.",
                "- No action was applied.",
                "- No pipe was split.",
                "- Revit model data was not modified.",
            ]
        )
        report_text = "\n".join(lines)
        self.latest_reviewed_action_confirmation_guard = {
            "guard_id": guard_id,
            "feature_id": "MEP-ACT-002",
            "source_prompt": safe_str(prompt),
            "created_timestamp_local": time.strftime("%Y-%m-%d %H:%M:%S"),
            "document_title": _document_title(doc),
            "document_path": self._safe_document_path(),
            "active_view_name": self._safe_active_view_name(),
            "active_view_type": self._safe_active_view_type(),
            "source_state_type": source.get("source_state_type"),
            "source_feature_id": source.get("source_feature_id"),
            "source_report_header": source.get("source_report_header"),
            "source_report_prompt": source.get("source_report_prompt"),
            "source_report_timestamp": source.get("source_report_timestamp"),
            "execution_requested": execution_requested,
            "execution_available": False,
            "execution_performed": False,
            "confirmation_result": confirmation_result,
            "reason": reason,
            "next_required_feature": "MEP-WR-002",
            "deterministic_route": True,
            "read_only": True,
            "model_modified": False,
            "transaction_opened": False,
            "report_text": report_text,
        }
        return report_text

    def _reviewed_action_proposal_route(self, prompt):
        normalized = self._normalize_context_prompt(prompt)
        split_routes = [
            "propose split selected pipes",
            "propose splitting selected pipes",
            "review split selected pipes",
            "prepare split selected pipes",
            "plan split selected pipes",
            "split selected pipes proposal",
            "preflight split selected pipes",
        ]
        future_routes = {
            "propose tag selected elements": "tag_selected_mep_elements",
            "propose tag selected mep elements": "tag_selected_mep_elements",
            "propose fill missing marks": "fill_missing_marks",
            "propose create schedule from report": "create_schedule_from_report",
            "propose export qa report": "export_latest_qa_report",
            "propose create qa snapshot": "create_qa_snapshot",
        }
        general_routes = [
            "propose reviewed action",
            "create reviewed action proposal",
            "reviewed action proposal",
            "plan reviewed action",
            "prepare reviewed action",
            "action proposal",
            "propose next action",
            "what action can you propose",
        ]
        if normalized in split_routes:
            return "split_selected_pipes"
        if normalized in future_routes:
            return future_routes.get(normalized)
        if normalized in general_routes:
            return "unknown_reviewed_action"
        if "split" in normalized and "selected pipe" in normalized:
            return "split_selected_pipes"
        if "tag" in normalized and ("selected" in normalized or "mep" in normalized):
            return "tag_selected_mep_elements"
        if "fill" in normalized and "missing mark" in normalized:
            return "fill_missing_marks"
        if "schedule" in normalized and "report" in normalized and "propose" in normalized:
            return "create_schedule_from_report"
        if "export" in normalized and "qa report" in normalized and "propose" in normalized:
            return "export_latest_qa_report"
        if "qa snapshot" in normalized and "propose" in normalized:
            return "create_qa_snapshot"
        return None

    def _proposal_action_label(self, action_key):
        labels = {
            "split_selected_pipes": "Split selected pipes",
            "tag_selected_mep_elements": "Tag selected MEP elements",
            "fill_missing_marks": "Fill missing marks",
            "export_latest_qa_report": "Export QA report",
            "create_qa_snapshot": "QA evidence snapshot",
            "create_schedule_from_report": "Schedule from report",
            "unknown_reviewed_action": "Unknown reviewed action",
        }
        return labels.get(action_key, "Unknown reviewed action")

    def _proposal_id(self, action_key):
        return "MEP-ACT-001-{0}-{1}".format(time.strftime("%Y%m%d_%H%M%S"), slugify_text(action_key))

    def _pipe_split_category_group(self, category_name):
        if category_name == "Pipes":
            return "pipes"
        if category_name == "Pipe Fittings":
            return "pipe_fittings"
        if category_name == "Pipe Accessories":
            return "pipe_accessories"
        return "non_pipe"

    def _append_sample(self, groups, key, elem, limit=10):
        bucket = groups.setdefault(key, {"count": 0, "ids": [], "categories": {}})
        bucket["count"] += 1
        elem_id = _safe_int_id(elem)
        if elem_id is not None and len(bucket["ids"]) < limit and elem_id not in bucket["ids"]:
            bucket["ids"].append(elem_id)
        cat = _category_name(elem)
        if cat:
            bucket["categories"][cat] = bucket["categories"].get(cat, 0) + 1

    def _pipe_location_curve_status(self, elem):
        try:
            location = elem.Location
        except:
            location = None
        try:
            if not isinstance(location, DB.LocationCurve):
                return None, "unreadable"
        except:
            if not hasattr(location, "Curve"):
                return None, "unreadable"
        try:
            curve = location.Curve
        except:
            return None, "unreadable"
        if curve is None:
            return None, "unreadable"
        try:
            _ = curve.GetEndPoint(0)
            _ = curve.GetEndPoint(1)
        except:
            return curve, "unsupported"
        return curve, "ok"

    def _pipe_is_near_vertical(self, curve):
        try:
            p0 = curve.GetEndPoint(0)
            p1 = curve.GetEndPoint(1)
            dx = float(p1.X - p0.X)
            dy = float(p1.Y - p0.Y)
            dz = float(p1.Z - p0.Z)
            length = math.sqrt(dx * dx + dy * dy + dz * dz)
            if length <= 0:
                return False
            return abs(dz / length) > 0.90
        except:
            return False

    def _pipe_curve_length(self, curve):
        try:
            return float(curve.Length)
        except:
            return None

    def _pipe_too_short(self, curve):
        length = self._pipe_curve_length(curve)
        if length is None:
            return False
        try:
            threshold = _meters_to_feet(1.0)
            return length < threshold
        except:
            return False

    def _group_line(self, label, bucket):
        ids = bucket.get("ids") or []
        categories = bucket.get("categories") or {}
        parts = ["- {0}: {1}".format(label, bucket.get("count", 0))]
        if ids:
            parts.append("sample ids: {0}".format(", ".join([safe_str(item) for item in ids[:10]])))
        if categories:
            cat_text = ", ".join(["{0}={1}".format(key, value) for key, value in sorted(categories.items())[:5]])
            parts.append("categories: {0}".format(cat_text))
        return " | ".join(parts)

    def _store_reviewed_action_proposal(self, proposal, proposal_text):
        proposal["proposal_text"] = proposal_text
        self.latest_reviewed_action_proposal = proposal
        self.remember_latest_deterministic_report(proposal.get("source_prompt"), proposal_text)

    def _build_split_selected_pipes_proposal(self, prompt):
        elements = _selected_elements(doc, uidoc)
        total_selected = len(elements)
        proposal_id = self._proposal_id("split_selected_pipes")
        doc_title = _document_title(doc)
        active_view_name = self._safe_active_view_name()
        active_view_type = self._safe_active_view_type()
        groups = {}
        warnings = {}
        eligible = []
        inspected = 0
        capped = False
        max_items = 200

        for elem in elements[:max_items]:
            inspected += 1
            cat = _category_name(elem)
            group = self._pipe_split_category_group(cat)
            if group != "pipes":
                self._append_sample(groups, group, elem)
                continue
            try:
                if bool(elem.Pinned):
                    self._append_sample(groups, "pinned_pipes", elem)
                    continue
            except:
                pass
            try:
                group_id = getattr(elem, "GroupId", None)
                if group_id is not None and group_id != DB.ElementId.InvalidElementId:
                    self._append_sample(groups, "grouped_pipes", elem)
                    continue
            except:
                pass
            try:
                design_option = getattr(elem, "DesignOption", None)
                if design_option is not None:
                    self._append_sample(groups, "design_option_pipes", elem)
                    continue
            except:
                pass
            curve, status = self._pipe_location_curve_status(elem)
            if status == "unreadable":
                self._append_sample(groups, "unreadable_location_curve", elem)
                continue
            if status == "unsupported":
                self._append_sample(groups, "unsupported_curve_type", elem)
                continue
            if self._pipe_is_near_vertical(curve):
                self._append_sample(groups, "near_vertical_pipes", elem)
                continue
            if self._pipe_too_short(curve):
                self._append_sample(groups, "too_short_pipes", elem)
                continue
            eligible.append(elem)
            if not _element_level_name(doc, elem):
                self._append_sample(warnings, "missing_level", elem)
            if not _system_assignment_value(elem):
                self._append_sample(warnings, "missing_system", elem)
            if not _safe_param_text(elem, ["Diameter", "Size", "Nominal Diameter", "Outside Diameter"]):
                self._append_sample(warnings, "missing_diameter_or_size", elem)
            if not _safe_param_text(elem, ["Length"]):
                self._append_sample(warnings, "missing_length", elem)
            if not _safe_param_text(elem, ["Slope"]):
                self._append_sample(warnings, "missing_slope", elem)
        if len(elements) > max_items:
            capped = True

        skipped_count = sum([bucket.get("count", 0) for bucket in groups.values()])
        warning_count = sum([bucket.get("count", 0) for bucket in warnings.values()])
        ready = bool(eligible)
        lines = [
            "[REVIEWED ACTION PROPOSAL]",
            "",
            "Proposal ID:",
            proposal_id,
            "",
            "Action:",
            "Split selected pipes",
            "",
            "Status:",
            "Proposal only / not executed",
            "",
            "Scope:",
            "selected elements / active document only",
            "",
            "Active document:",
            doc_title,
            "",
            "Active view:",
            "{0} [{1}]".format(active_view_name, active_view_type),
            "",
            "Selection summary:",
            "- Total selected elements: {0}".format(total_selected),
            "- Elements inspected for preflight: {0}".format(inspected),
            "- Eligible pipes: {0}".format(len(eligible)),
            "- Skipped elements: {0}".format(skipped_count),
            "- Warnings: {0}".format(warning_count),
            "",
        ]
        if total_selected == 0:
            lines.extend(
                [
                    "Preflight result:",
                    "Not ready",
                    "",
                    "No elements selected. Select pipes before requesting a split proposal.",
                    "",
                    "Safety:",
                    "- Proposal only.",
                    "- Read-only.",
                    "- No transaction opened.",
                    "- Revit model data was not modified.",
                ]
            )
        else:
            lines.extend(
                [
                    "Eligible pipes:",
                    "- {0} | sample ids: {1}".format(len(eligible), _sample_id_text(eligible, 10)),
                    "",
                    "Skipped elements:",
                    self._group_line("Pipe fittings", groups.get("pipe_fittings", {"count": 0, "ids": [], "categories": {}})),
                    self._group_line("Pipe accessories", groups.get("pipe_accessories", {"count": 0, "ids": [], "categories": {}})),
                    self._group_line("Non-pipe categories", groups.get("non_pipe", {"count": 0, "ids": [], "categories": {}})),
                    self._group_line("Near-vertical pipes", groups.get("near_vertical_pipes", {"count": 0, "ids": [], "categories": {}})),
                    self._group_line("Too-short pipes", groups.get("too_short_pipes", {"count": 0, "ids": [], "categories": {}})),
                    self._group_line("Unreadable location curve", groups.get("unreadable_location_curve", {"count": 0, "ids": [], "categories": {}})),
                    self._group_line("Pinned pipes", groups.get("pinned_pipes", {"count": 0, "ids": [], "categories": {}})),
                    self._group_line("Grouped pipes", groups.get("grouped_pipes", {"count": 0, "ids": [], "categories": {}})),
                    self._group_line("Design-option pipes", groups.get("design_option_pipes", {"count": 0, "ids": [], "categories": {}})),
                    self._group_line("Unsupported curve type", groups.get("unsupported_curve_type", {"count": 0, "ids": [], "categories": {}})),
                    "",
                    "Preflight warnings:",
                ]
            )
            if warnings:
                for key in sorted(warnings.keys()):
                    lines.append(self._group_line(key.replace("_", " ").title(), warnings.get(key)))
            else:
                lines.append("- none")
            if capped:
                lines.append("- Selection preflight was capped at 200 elements for readability/performance.")
            lines.extend(
                [
                    "",
                    "Risks:",
                    "- Pipe splitting can affect connected fittings, slopes, systems, tags, dimensions, schedules, and downstream coordination.",
                    "- MEP-ACT-001 does not inspect connectors or modify networks.",
                    "- Actual execution must be implemented as a separate reviewed write action with rollback.",
                    "",
                    "Preflight result:",
                    "Ready for future dry-run" if ready else "Not ready",
                    "",
                    "Proposed split count:",
                    "not calculated in MEP-ACT-001",
                    "",
                    "Next step:",
                    "Future feature MEP-WR-001 should implement split selected pipes dry-run. Future feature MEP-WR-002 should implement reviewed apply with explicit confirmation and rollback.",
                    "",
                    "Safety:",
                    "- Proposal only.",
                    "- Read-only.",
                    "- No transaction opened.",
                    "- Revit model data was not modified.",
                ]
            )
        text = "\n".join(lines)
        proposal = {
            "proposal_id": proposal_id,
            "feature_id": "MEP-ACT-001",
            "action_key": "split_selected_pipes",
            "action_label": "Split selected pipes",
            "source_prompt": safe_str(prompt),
            "created_timestamp_local": time.strftime("%Y-%m-%d %H:%M:%S"),
            "document_title": doc_title,
            "document_path": self._safe_document_path(),
            "active_view_name": active_view_name,
            "active_view_type": active_view_type,
            "scope": "selected elements / active document only",
            "eligible_count": len(eligible),
            "skipped_count": skipped_count,
            "warning_count": warning_count,
            "proposed_next_command": "MEP-WR-001 split selected pipes dry-run",
            "proposal_status": "ready_for_future_dry_run" if ready else "not_ready",
            "deterministic_route": True,
            "read_only": True,
            "model_modified": False,
            "execution_available": False,
        }
        self._store_reviewed_action_proposal(proposal, text)
        return text

    def _build_future_reviewed_action_proposal(self, prompt, action_key):
        proposal_id = self._proposal_id(action_key)
        label = self._proposal_action_label(action_key)
        doc_title = _document_title(doc)
        active_view_name = self._safe_active_view_name()
        active_view_type = self._safe_active_view_type()
        scope = "selected elements / active document only" if action_key in ("tag_selected_mep_elements", "fill_missing_marks") else "current session / active document only"
        text = "\n".join(
            [
                "[REVIEWED ACTION PROPOSAL]",
                "",
                "Proposal ID:",
                proposal_id,
                "",
                "Action:",
                label,
                "",
                "Status:",
                "Proposal only / future action not implemented",
                "",
                "Scope:",
                scope,
                "",
                "Active document:",
                doc_title,
                "",
                "Active view:",
                "{0} [{1}]".format(active_view_name, active_view_type),
                "",
                "Eligibility summary:",
                "- Eligible elements: 0",
                "- Skipped elements: 0",
                "- Warnings: 0",
                "",
                "Preflight result:",
                "Future reviewed action recognized, but execution/dry-run logic is not implemented in MEP-ACT-001.",
                "",
                "Next step:",
                "Implement a dedicated dry-run feature before any reviewed apply action.",
                "",
                "Safety:",
                "- Proposal only.",
                "- Read-only.",
                "- No transaction opened.",
                "- Revit model data was not modified.",
            ]
        )
        proposal = {
            "proposal_id": proposal_id,
            "feature_id": "MEP-ACT-001",
            "action_key": action_key,
            "action_label": label,
            "source_prompt": safe_str(prompt),
            "created_timestamp_local": time.strftime("%Y-%m-%d %H:%M:%S"),
            "document_title": doc_title,
            "document_path": self._safe_document_path(),
            "active_view_name": active_view_name,
            "active_view_type": active_view_type,
            "scope": scope,
            "eligible_count": 0,
            "skipped_count": 0,
            "warning_count": 0,
            "proposed_next_command": "dedicated dry-run feature required",
            "proposal_status": "future_action_not_implemented",
            "deterministic_route": True,
            "read_only": True,
            "model_modified": False,
            "execution_available": False,
        }
        self._store_reviewed_action_proposal(proposal, text)
        return text

    def _build_unknown_reviewed_action_proposal(self, prompt):
        proposal_id = self._proposal_id("unknown_reviewed_action")
        doc_title = _document_title(doc)
        active_view_name = self._safe_active_view_name()
        active_view_type = self._safe_active_view_type()
        text = "\n".join(
            [
                "[REVIEWED ACTION PROPOSAL]",
                "",
                "Proposal ID:",
                proposal_id,
                "",
                "Action:",
                "Unknown reviewed action",
                "",
                "Status:",
                "Proposal only / not executed",
                "",
                "Active document:",
                doc_title,
                "",
                "Active view:",
                "{0} [{1}]".format(active_view_name, active_view_type),
                "",
                "Preflight result:",
                "No specific reviewed action could be classified from the prompt.",
                "",
                "Suggested supported proposal prompts:",
                "- propose split selected pipes",
                "- dry run split selected pipes",
                "- propose tag selected MEP elements",
                "- propose fill missing marks",
                "- propose create QA snapshot",
                "",
                "Safety:",
                "- Proposal only.",
                "- Read-only.",
                "- No transaction opened.",
                "- Revit model data was not modified.",
            ]
        )
        proposal = {
            "proposal_id": proposal_id,
            "feature_id": "MEP-ACT-001",
            "action_key": "unknown_reviewed_action",
            "action_label": "Unknown reviewed action",
            "source_prompt": safe_str(prompt),
            "created_timestamp_local": time.strftime("%Y-%m-%d %H:%M:%S"),
            "document_title": doc_title,
            "document_path": self._safe_document_path(),
            "active_view_name": active_view_name,
            "active_view_type": active_view_type,
            "scope": "active document only",
            "eligible_count": 0,
            "skipped_count": 0,
            "warning_count": 0,
            "proposed_next_command": "use a supported proposal prompt",
            "proposal_status": "unknown_action",
            "deterministic_route": True,
            "read_only": True,
            "model_modified": False,
            "execution_available": False,
        }
        self._store_reviewed_action_proposal(proposal, text)
        return text

    def answer_reviewed_action_proposal_question(self, prompt):
        action_key = self._reviewed_action_proposal_route(prompt)
        if not action_key:
            return None
        if action_key == "split_selected_pipes":
            return self._build_split_selected_pipes_proposal(prompt)
        if action_key == "unknown_reviewed_action":
            return self._build_unknown_reviewed_action_proposal(prompt)
        return self._build_future_reviewed_action_proposal(prompt, action_key)

    def _safe_document_path(self):
        try:
            path_name = doc.PathName
            if path_name:
                return safe_str(path_name)
        except:
            pass
        return None

    def _safe_active_view_name(self):
        try:
            if uidoc and uidoc.ActiveView:
                return safe_str(uidoc.ActiveView.Name)
        except:
            pass
        return "(unknown view)"

    def _safe_active_view_type(self):
        try:
            if uidoc and uidoc.ActiveView:
                return safe_str(uidoc.ActiveView.ViewType)
        except:
            pass
        return "unknown"

    def _write_utf8_file(self, path, text):
        stream = codecs.open(path, "w", "utf-8")
        try:
            stream.write(safe_str(text))
        finally:
            stream.close()

    def export_latest_qa_report(self, export_prompt):
        if not self.latest_deterministic_report:
            return "\n".join(
                [
                    "[QA REPORT EXPORT]",
                    "No exportable deterministic report is available yet. Run a read-only report first.",
                ]
            )
        if not self.latest_chat_output_is_deterministic_report:
            return "\n".join(
                [
                    "[QA REPORT EXPORT]",
                    "Latest output is not a deterministic AI Workbench QA report. Run a read-only deterministic report first.",
                ]
            )
        report = self.latest_deterministic_report
        report_text = report.get("report_text") or ""
        if not self._is_exportable_report_text(report_text):
            return "\n".join(
                [
                    "[QA REPORT EXPORT]",
                    "Latest output is not a deterministic AI Workbench QA report. Run a read-only deterministic report first.",
                ]
            )

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        export_folder = os.path.join(self._qa_export_base_folder(), timestamp)
        try:
            if not os.path.isdir(export_folder):
                os.makedirs(export_folder)
        except Exception as exc:
            return "\n".join(
                [
                    "[QA REPORT EXPORT]",
                    "Export failed: {0}".format(str(exc)),
                ]
            )

        doc_title = _document_title(doc)
        active_view_name = self._safe_active_view_name()
        active_view_type = self._safe_active_view_type()
        header = report.get("report_header") or self._extract_report_header(report_text)
        source_prompt = report.get("source_prompt") or ""
        scope = report.get("report_scope") or self._detect_report_scope(report_text)
        generated_files = ["report.md", "report.txt", "metadata.json", "artifact_manifest.txt"]

        metadata = {
            "feature_id": "MEP-RO-005",
            "export_timestamp_local": time.strftime("%Y-%m-%d %H:%M:%S"),
            "document_title": doc_title,
            "document_path": self._safe_document_path(),
            "active_view_name": active_view_name,
            "active_view_type": active_view_type,
            "source_prompt": source_prompt,
            "source_report_header": header,
            "report_scope": scope,
            "deterministic_route": True,
            "read_only": True,
            "model_modified": False,
            "linked_documents_scanned": False,
            "connector_traversal_used": False,
            "geometry_extraction_used": False,
            "export_folder": export_folder,
            "generated_files": generated_files,
        }

        report_md = "\n".join(
            [
                "# AI Workbench QA Evidence Snapshot",
                "",
                "## Metadata",
                "- Document: {0}".format(doc_title),
                "- Active view: {0} [{1}]".format(active_view_name, active_view_type),
                "- Export timestamp: {0}".format(metadata.get("export_timestamp_local")),
                "- Source prompt: {0}".format(source_prompt),
                "- Source report header: {0}".format(header),
                "- Scope: {0}".format(scope),
                "- Safety: read-only; no model data modified.",
                "",
                "## Report",
                report_text,
                "",
            ]
        )
        report_txt = "\n".join(
            [
                "AI Workbench QA Evidence Snapshot",
                "",
                "Document: {0}".format(doc_title),
                "Active view: {0} [{1}]".format(active_view_name, active_view_type),
                "Export timestamp: {0}".format(metadata.get("export_timestamp_local")),
                "Source prompt: {0}".format(source_prompt),
                "Source report header: {0}".format(header),
                "Scope: {0}".format(scope),
                "Safety: read-only; no model data modified.",
                "",
                report_text,
                "",
            ]
        )
        manifest = "\n".join(
            [
                "AI Workbench QA Evidence Snapshot Manifest",
                "Feature: MEP-RO-005",
                "Export timestamp: {0}".format(metadata.get("export_timestamp_local")),
                "Export folder: {0}".format(export_folder),
                "",
                "Generated files:",
                "- report.md",
                "- report.txt",
                "- metadata.json",
                "- artifact_manifest.txt",
                "",
                "Safety:",
                "- Export only.",
                "- Revit model data was not modified.",
                "- Linked document internals, connector data, and geometry were not exported.",
            ]
        )

        try:
            self._write_utf8_file(os.path.join(export_folder, "report.md"), report_md)
            self._write_utf8_file(os.path.join(export_folder, "report.txt"), report_txt)
            metadata_path = os.path.join(export_folder, "metadata.json")
            metadata_stream = codecs.open(metadata_path, "w", "utf-8")
            try:
                metadata_stream.write(json.dumps(metadata, indent=2, sort_keys=True))
            finally:
                metadata_stream.close()
            self._write_utf8_file(os.path.join(export_folder, "artifact_manifest.txt"), manifest)
        except Exception as exc:
            return "\n".join(
                [
                    "[QA REPORT EXPORT]",
                    "Export failed: {0}".format(str(exc)),
                ]
            )

        index_result = self._write_qa_export_index_entry(metadata)
        lines = [
            "[QA REPORT EXPORT COMPLETE]",
            "",
            "Export folder:",
            export_folder,
            "",
            "Generated files:",
            "- report.md",
            "- report.txt",
            "- metadata.json",
            "- artifact_manifest.txt",
            "",
            "Index:",
        ]
        if index_result.get("ok"):
            lines.extend(
                [
                    "- qa_export_index.jsonl updated",
                    "- qa_export_index.csv updated",
                    "- latest_export.json updated",
                ]
            )
        else:
            lines.extend(
                [
                    "Index warning:",
                    "- {0}".format(index_result.get("warning") or "Index write failed."),
                    "- Export files were still created.",
                ]
            )
        lines.extend(
            [
                "",
                "Source:",
                "- prompt: {0}".format(source_prompt),
                "- header: {0}".format(header),
                "- document: {0}".format(doc_title),
                "- active view: {0} [{1}]".format(active_view_name, active_view_type),
                "",
                "Safety:",
                "- Export only.",
                "- Revit model data was not modified.",
            ]
        )
        return "\n".join(lines)

    def _context_tree_item(self, header, children=None):
        from System.Windows.Controls import TreeViewItem

        item = TreeViewItem()
        item.Header = header
        for child in children or []:
            if hasattr(child, "Header"):
                item.Items.Add(child)
            else:
                item.Items.Add(self._context_tree_item(safe_str(child)))
        return item

    def _context_not_scanned(self, value, fallback="not scanned"):
        if value is None or value == "":
            return fallback
        return value

    def _schedule_status_label(self, row_count):
        if isinstance(row_count, int):
            if row_count > 0:
                return "populated ({0} body rows)".format(row_count)
            return "empty (0 body rows)"
        return "unknown body row count"

    def _coord_number(self, value, precision=3):
        try:
            return "{0:.{1}f}".format(float(value), int(precision))
        except:
            return "unknown"

    def _coord_origin_text(self, item):
        origin = item.get("origin_internal_feet") or {}
        return "X={0}, Y={1}, Z={2}".format(
            self._coord_number(origin.get("x")),
            self._coord_number(origin.get("y")),
            self._coord_number(origin.get("z")),
        )

    def _coord_rotation_text(self, item):
        return "{0} deg".format(self._coord_number(item.get("rotation_z_degrees")))

    def _link_load_status_text(self, item):
        if item.get("loaded_readable") is True:
            return "loaded"
        if item.get("status_label") == "UNLOADED" or "unloaded_or_unavailable_link" in (item.get("coordinate_flags") or []):
            return "unloaded"
        return "unavailable"

    def _coord_transform_text(self, item):
        if item.get("close_to_identity") is True:
            return "identity / near identity"
        if item.get("close_to_identity") is False:
            return "non-identity"
        return "unknown"

    def _coord_reason_list(self, item):
        flags = item.get("coordinate_flags") or []
        reason_map = {
            "unloaded_or_unavailable_link": "link document is not readable",
            "non_identity_transform": "transform differs from identity",
            "z_offset_detected": "Z offset detected",
            "rotated_link_detected": "rotation around Z detected",
            "duplicate_link_instances": "duplicate link type/instance detected",
            "unknown_coordinate_state": "coordinate state could not be read",
            "basis_not_orthonormal": "basis vectors are not orthonormal",
        }
        return [reason_map.get(flag, flag.replace("_", " ")) for flag in flags] or ["none"]

    def _coordinate_health_counts(self, health):
        links = health.get("links", []) if isinstance(health, dict) else []
        counts = {"OK": 0, "REVIEW": 0, "UNLOADED": 0, "UNKNOWN": 0}
        for item in links:
            label = item.get("status_label") or "UNKNOWN"
            counts[label] = counts.get(label, 0) + 1
        return counts

    def _coordinate_health_main_flags(self, health):
        flags = []
        for item in (health.get("links", []) if isinstance(health, dict) else []):
            flags.extend(item.get("coordinate_flags") or [])
        unique = sorted(set(flags))
        return unique or ["none"]

    def _coordinate_health_reason_summary(self, health):
        if not isinstance(health, dict):
            return "Linked model coordinate health was not scanned."
        return "status {0}; links {1}; review {2}; unloaded {3}; unknown {4}; flags {5}".format(
            health.get("overall_status", "unknown"),
            health.get("total_links", 0),
            self._coordinate_health_counts(health).get("REVIEW", 0),
            health.get("unavailable_count", 0),
            self._coordinate_health_counts(health).get("UNKNOWN", 0),
            ", ".join(self._coordinate_health_main_flags(health)),
        )

    def _issue_suggested_action(self, issue_id):
        mapping = {
            "ambiguous_level_names": "Use exact level names in level-targeted schedule prompts.",
            "unloaded_or_unavailable_revit_links": "Review links from the Project Context Links branch.",
            "unloaded_or_unavailable_link": "Review unloaded links in Project Context > Links / Coordinate Health.",
            "non_identity_link_transform": "Run the read-only linked model coordinate health check before coordination work.",
            "link_z_offset_detected": "Review link Z offsets with the BIM coordinator before modelling against linked geometry.",
            "rotated_link_detected": "Review rotated link transforms before coordination checks.",
            "duplicate_link_instances": "Review duplicate link instances/types before coordination work.",
            "unknown_coordinate_state": "Review links with unreadable coordinate state before relying on alignment assumptions.",
            "host_duplicate_level_names": "Review duplicate host level names before BIM Basis / ILS-style exchanges.",
            "linked_duplicate_level_names": "Review duplicate linked level names with the model author.",
            "linked_level_elevation_mismatch": "Review level elevation deltas against the project BIM Basis / ILS rules.",
            "linked_level_missing_in_host": "Review linked-only levels before coordination or schedule filtering.",
            "host_level_missing_in_link": "Review host levels missing from linked models with the coordination team.",
            "linked_level_possible_name_mismatch": "Review level naming consistency for levels with matching elevations.",
            "host_duplicate_grid_names": "Review duplicate host grid names before coordination.",
            "linked_duplicate_grid_names": "Review duplicate linked grid names with the model author.",
            "linked_grid_missing_in_host": "Review linked-only grids before coordination.",
            "host_grid_missing_in_link": "Review host grids missing from linked models with the coordination team.",
            "linked_grid_geometry_mismatch": "Review grid geometry offsets/angles manually before coordination.",
            "linked_grid_geometry_unknown": "Review non-linear or unknown grid geometry manually.",
            "level_grid_scan_partial": "Run Scan Project with accessible loaded links or review skipped links manually.",
            "cad_imports_present": "Run read-only active-view health checks before coordination work.",
            "view_specific_imports_present": "Inspect view-specific imports before relying on sheet/view outputs.",
            "empty_or_suspicious_schedules": "Use Show Schedules or Empty / Suspicious Schedules before template-backed actions.",
            "aco_bunge_templates_present": "Consider reviewed ACO/Bunge schedule actions only after checking source templates.",
            "no_selection": "Start with active-view or project-level read-only checks.",
            "selection_present": "Selection-based reviewed reports can be used safely.",
            "high_warning_count": "Review top warning groups before modifying model data.",
        }
        return mapping.get(issue_id, "Use read-only reviewed inspection before modifying model data.")

    def populate_project_context_tree(self):
        if not hasattr(self, "ProjectContextTree"):
            return
        self.ProjectContextTree.Items.Clear()
        context = self.latest_project_context or {}
        if not context:
            self.ProjectContextTree.Items.Add(
                self._context_tree_item(
                    "Recommended first steps",
                    [
                        "1. Scan current project",
                        "2. Review links/imports/categories",
                        "3. Ask Ollama Chat project questions",
                        "4. Ask AI Agent for a plan",
                        "5. Approve only reviewed ModelMind/catalog actions",
                    ],
                )
            )
            return

        doc_info = context.get("document", {})
        active_view = doc_info.get("active_view", {})
        levels = context.get("levels", {})
        links = context.get("links", {})
        imports = context.get("imports_cad", {})
        schedules = context.get("schedules", {})
        coord_health = _get_linked_model_coordinate_health(context)
        level_grid_health = _get_host_link_level_grid_health(context)
        selection = context.get("selection", {})
        warnings = context.get("warnings_summary", {})
        issues = context.get("detected_issues", [])
        categories = context.get("category_counts", [])
        views = context.get("views", {})
        sheets = context.get("sheets", {})
        scan_timestamp = context.get("timestamp_utc") or self.latest_project_context_timestamp

        self.ProjectContextTree.Items.Add(
            self._context_tree_item(
                "Project",
                [
                    "Document: {0}".format(doc_info.get("title", "(unknown)")),
                    "Path: {0}".format(doc_info.get("path_name") or "(not available)"),
                    "Workshared: {0}".format(self._context_not_scanned(doc_info.get("is_workshared"))),
                    "Scan depth: {0}".format(context.get("scan_depth")),
                    "Scan timestamp: {0}".format(scan_timestamp or "not scanned"),
                ],
            )
        )
        self.ProjectContextTree.Items.Add(
            self._context_tree_item(
                "Active View",
                [
                    "Name: {0}".format(active_view.get("name", "(unknown view)")),
                    "Type: {0}".format(active_view.get("view_type", "unknown")),
                    "Id: {0}".format(active_view.get("id")),
                    "Discipline: {0}".format(active_view.get("discipline") or "not scanned"),
                    "View template: {0}".format(active_view.get("view_template_name") or "not scanned"),
                    "Active-document selection count: {0}".format(selection.get("selected_element_count", 0)),
                ],
            )
        )
        level_children = ["Count: {0}".format(levels.get("count", 0))]
        for level in levels.get("levels", [])[:PROJECT_CONTEXT_MAX_ITEMS]:
            elevation = level.get("elevation")
            elevation_text = "not scanned" if elevation is None else "{0:.3f}".format(elevation)
            alias_note = " | alias: {0}".format(level.get("simplified_name")) if level.get("simplified_name") else ""
            if level.get("ambiguous_alias"):
                alias_note += " | ambiguous alias"
            level_children.append("{0} | elevation: {1}{2}".format(level.get("exact_name"), elevation_text, alias_note))
        for ambiguous in levels.get("ambiguous_aliases", [])[:5]:
            level_children.append("Ambiguous: {0} -> {1}".format(ambiguous.get("simplified_name"), ", ".join(ambiguous.get("candidates", []))))
        self.ProjectContextTree.Items.Add(self._context_tree_item("Levels", level_children))

        view_children = ["Total: {0}".format(views.get("total", 0))]
        samples_by_type = views.get("samples_by_view_type", {}) or {}
        for view_type, count in sorted((views.get("by_view_type", {}) or {}).items()):
            sample_children = ["Count: {0}".format(count)]
            for sample in samples_by_type.get(view_type, [])[:8]:
                active_marker = " [active]" if sample.get("is_active") else ""
                sample_children.append("{0} (id {1}){2}".format(sample.get("name") or "(unnamed view)", sample.get("id"), active_marker))
            view_children.append(self._context_tree_item("{0}".format(view_type), sample_children))
        self.ProjectContextTree.Items.Add(self._context_tree_item("Views", view_children))

        sheet_children = ["Total: {0}".format(sheets.get("count", 0))]
        for sheet in sheets.get("samples", [])[:PROJECT_CONTEXT_MAX_ITEMS]:
            placed = sheet.get("placed_view_count", "not scanned")
            if placed == "not scanned":
                placed_text = "placement status not scanned"
            elif placed == 0:
                placed_text = "no placed views"
            else:
                placed_text = "{0} placed views".format(placed)
            label = "{0} - {1}".format(sheet.get("number") or "(no number)", sheet.get("name") or "(unnamed sheet)")
            sheet_children.append("{0} | {1}".format(label, placed_text))
        self.ProjectContextTree.Items.Add(self._context_tree_item("Sheets", sheet_children))

        link_children = [
            "Instances: {0}".format(links.get("instance_count", 0)),
            "Loaded: {0}".format(len(links.get("loaded_links") or [])),
            "Unavailable: {0}".format(len(links.get("unavailable_links") or [])),
        ]
        for item in links.get("instances", [])[:PROJECT_CONTEXT_MAX_ITEMS]:
            label = "{0} [{1}]".format(item.get("display_name") or item.get("type_name") or item.get("instance_name") or "(unnamed link)", item.get("status") or "unknown")
            details = []
            details.append("instance name: {0}".format(item.get("instance_name") or "not scanned"))
            details.append("type name: {0}".format(item.get("type_name") or "not scanned"))
            details.append("instance id: {0}".format(item.get("instance_id") or "not scanned"))
            details.append("type id: {0}".format(item.get("type_id") or "not scanned"))
            if item.get("linked_document_title"):
                details.append("linked document: {0}".format(item.get("linked_document_title")))
            details.append("path/reference: {0}".format(item.get("path_info") or "<path not available>"))
            if item.get("transform_summary"):
                details.append(item.get("transform_summary"))
            link_children.append(self._context_tree_item(label, details or ["No additional link detail scanned."]))
        for duplicate in links.get("duplicate_link_types_or_instances", [])[:5]:
            link_children.append("Duplicate link type/instances: {0} ({1})".format(duplicate.get("type_name"), duplicate.get("instance_count")))
        self.ProjectContextTree.Items.Add(
            self._context_tree_item("Links", link_children)
        )

        coord_children = [
            "Total links: {0}".format(coord_health.get("total_links", 0)),
            "Loaded/readable: {0}".format(coord_health.get("loaded_readable_count", 0)),
            "Unavailable: {0}".format(coord_health.get("unavailable_count", 0)),
            "Non-identity transforms: {0}".format(coord_health.get("non_identity_transform_count", 0)),
            "Rotated links: {0}".format(coord_health.get("rotated_link_count", 0)),
            "Z-offset links: {0}".format(coord_health.get("z_offset_link_count", 0)),
            "Duplicate link instances/types: {0}".format(coord_health.get("duplicate_link_instance_count", 0)),
            "Overall status: {0}".format(coord_health.get("overall_status", "not scanned")),
        ]
        host = coord_health.get("host", {}) if isinstance(coord_health, dict) else {}
        project_position = host.get("project_position", {}) if isinstance(host, dict) else {}
        coord_children.append(
            self._context_tree_item(
                "Host coordinate summary",
                [
                    "Document: {0}".format(host.get("document_title", "not scanned")),
                    "Project location: {0}".format(project_position.get("project_location_name", "unknown")),
                    "East/West: {0}".format(project_position.get("east_west", "unknown")),
                    "North/South: {0}".format(project_position.get("north_south", "unknown")),
                    "Elevation: {0}".format(project_position.get("elevation", "unknown")),
                    "Angle: {0} deg".format(project_position.get("angle_degrees", "unknown")),
                    "Project base point: {0}".format(host.get("project_base_point", "unknown")),
                    "Survey point: {0}".format(host.get("survey_point", "unknown")),
                ],
            )
        )
        for item in coord_health.get("links", [])[:PROJECT_CONTEXT_MAX_ITEMS] if isinstance(coord_health, dict) else []:
            reasons = self._coord_reason_list(item)
            load_status = self._link_load_status_text(item)
            duplicate_text = "yes ({0})".format(item.get("duplicate_type_instance_count")) if item.get("duplicate_type_instance_count", 0) and item.get("duplicate_type_instance_count", 0) > 1 else "no"
            coord_children.append(
                self._context_tree_item(
                    "{0} [{1}] [{2}]".format(
                        item.get("display_name") or item.get("type_name") or item.get("instance_name"),
                        load_status,
                        item.get("status_label", "UNKNOWN"),
                    ),
                    [
                        "instance: {0} (id {1})".format(item.get("instance_name"), item.get("instance_id")),
                        "type: {0} (id {1})".format(item.get("type_name"), item.get("type_id")),
                        "loaded/unloaded/unavailable: {0}".format(load_status),
                        "linked document: {0}".format(item.get("linked_document_title") or "not loaded/not scanned"),
                        "pinned: {0}".format(item.get("pinned")),
                        "path: {0}".format(item.get("path_info") or "<path not available>"),
                        "origin internal ft: {0}".format(self._coord_origin_text(item)),
                        "rotation Z: {0}".format(self._coord_rotation_text(item)),
                        "transform: {0}".format(self._coord_transform_text(item)),
                        "Z offset flag: {0}".format("yes" if "z_offset_detected" in (item.get("coordinate_flags") or []) else "no"),
                        "basis orthonormal: {0}".format(item.get("basis_orthonormal")),
                        "duplicate link/type: {0}".format(duplicate_text),
                        "final status: {0}".format(item.get("status_label", "UNKNOWN")),
                        "reasons: {0}".format("; ".join(reasons)),
                    ],
                )
            )
        self.ProjectContextTree.Items.Add(
            self._context_tree_item("Links / Coordinate Health", coord_children)
        )

        lg_children = [
            "Profile: {0}".format(level_grid_health.get("profile", "Configurable BIM Basis / ILS-style")),
            "Level elevation tolerance: {0} mm".format(level_grid_health.get("level_elevation_tolerance_mm", LEVEL_ELEVATION_TOLERANCE_MM)),
            "Grid offset tolerance: {0} mm".format(level_grid_health.get("grid_linear_offset_tolerance_mm", GRID_LINEAR_OFFSET_TOLERANCE_MM)),
            "Grid angle tolerance: {0} deg".format(level_grid_health.get("grid_angle_tolerance_degrees", GRID_ANGLE_TOLERANCE_DEG)),
            "Host level count: {0}".format(level_grid_health.get("host_level_count", "not scanned")),
            "Host grid count: {0}".format(level_grid_health.get("host_grid_count", "not scanned")),
            "Links compared: {0}".format(level_grid_health.get("links_compared_count", 0)),
            "Links skipped/inaccessible: {0}".format(level_grid_health.get("links_skipped_count", 0)),
            "Overall status: {0}".format(level_grid_health.get("overall_status", "not scanned")),
            "Status meaning: {0}".format((level_grid_health.get("status_wording") or {}).get(level_grid_health.get("overall_status"), "not scanned")),
        ]
        if level_grid_health.get("host_grid_basis_limited"):
            lg_children.append("Host contains no grids; linked grid comparison is basis-dependent.")
            lg_children.append("If grids are intentionally maintained in the architectural/structural link, this is not necessarily an error.")
        lg_children.append(
            self._context_tree_item(
                "Level findings summary",
                [
                    "host duplicate names: {0}".format(len(level_grid_health.get("host_duplicate_level_names", []) or [])),
                    "all level findings: {0}".format(level_grid_health.get("level_findings_count", 0)),
                    "by type: {0}".format(_format_level_grid_type_counts(level_grid_health.get("level_findings_by_type") or {})),
                    "sample affected levels: {0}".format("; ".join([name for row in _aggregate_level_grid_findings(level_grid_health.get("level_findings") or [], 5) for name in row.get("sample_names", [])][:8]) or "none"),
                ],
            )
        )
        lg_children.append(
            self._context_tree_item(
                "Grid findings summary",
                [
                    "host duplicate names: {0}".format(len(level_grid_health.get("host_duplicate_grid_names", []) or [])),
                    "all grid findings: {0}".format(level_grid_health.get("grid_findings_count", 0)),
                    "by type: {0}".format(_format_level_grid_type_counts(level_grid_health.get("grid_findings_by_type") or {})),
                    "sample affected grids: {0}".format("; ".join([name for row in _aggregate_level_grid_findings(level_grid_health.get("grid_findings") or [], 5) for name in row.get("sample_names", [])][:8]) or "none"),
                ],
            )
        )
        top_links = level_grid_health.get("top_affected_links") or []
        if top_links:
            lg_children.append(
                self._context_tree_item(
                    "Top affected links",
                    [
                        "{0}: level findings {1}, grid findings {2} | {3}".format(
                            item.get("link_name"),
                            item.get("level_findings_count", 0),
                            item.get("grid_findings_count", 0),
                            item.get("issue_types", "none"),
                        )
                        for item in top_links[:8]
                    ],
                )
            )
        for link_row in level_grid_health.get("links_compared", [])[:PROJECT_CONTEXT_MAX_ITEMS] if isinstance(level_grid_health, dict) else []:
            coord_status = "unknown"
            for coord_link in coord_health.get("links", []) if isinstance(coord_health, dict) else []:
                if coord_link.get("display_name") == link_row.get("linked_document_title") or coord_link.get("instance_name") == link_row.get("link_name"):
                    coord_status = coord_link.get("status_label", "unknown")
                    break
            level_groups = _aggregate_level_grid_findings(link_row.get("level_findings") or [], 5)
            grid_groups = _aggregate_level_grid_findings(link_row.get("grid_findings") or [], 5)
            lg_children.append(
                self._context_tree_item(
                    "{0} [{1}]".format(link_row.get("link_name"), link_row.get("status", "UNKNOWN")),
                    [
                        "linked document: {0}".format(link_row.get("linked_document_title")),
                        "coordinate-health status: {0}".format(coord_status),
                        "levels: {0}".format(link_row.get("level_count")),
                        "grids: {0}".format(link_row.get("grid_count")),
                        "level findings: {0}".format(len(link_row.get("level_findings") or [])),
                        "grid findings: {0}".format(len(link_row.get("grid_findings") or [])),
                        "issue types: {0}".format(_link_level_grid_issue_types(link_row)),
                        "sample levels: {0}".format("; ".join([name for row in level_groups for name in row.get("sample_names", [])][:5]) or "none"),
                        "sample grids: {0}".format("; ".join([name for row in grid_groups for name in row.get("sample_names", [])][:5]) or "none"),
                    ],
                )
            )
        for skipped in level_grid_health.get("links_skipped", [])[:PROJECT_CONTEXT_MAX_ITEMS] if isinstance(level_grid_health, dict) else []:
            lg_children.append("Skipped: {0} | {1}".format(skipped.get("link_name"), skipped.get("reason")))
        self.ProjectContextTree.Items.Add(
            self._context_tree_item("BIM Basis / Levels & Grids", lg_children)
        )

        import_children = [
            "Instances: {0}".format(imports.get("total_import_instances", 0)),
            "Linked CAD: {0}".format(imports.get("linked_cad_count", 0)),
            "Imported CAD: {0}".format(imports.get("imported_cad_count", 0)),
            "View-specific: {0}".format(imports.get("view_specific_import_count", 0)),
        ]
        if imports.get("total_import_instances", 0) == 0:
            import_children.append("No CAD/import instances found")
        for item in imports.get("imports", [])[:PROJECT_CONTEXT_MAX_ITEMS]:
            scope = "view-specific" if item.get("owner_view_name") else "model-wide"
            linked = "linked" if item.get("linked") else ("imported" if item.get("linked") is False else "linked/imported unknown")
            label = "{0} | {1}, {2}".format(item.get("instance_name") or "(unnamed import)", linked, scope)
            details = [
                "type: {0}".format(item.get("type_name") or "not scanned"),
                "category: {0}".format(item.get("category") or "not scanned"),
                "owner view: {0}".format(item.get("owner_view_name") or "not view-specific/not scanned"),
                "visible in active view: {0}".format(item.get("visible_in_active_view")),
            ]
            layers = item.get("layer_or_subcategory_samples") or []
            if layers:
                details.append("layer/subcategory samples: {0}".format(", ".join(layers[:8])))
            import_children.append(self._context_tree_item(label, details))
        self.ProjectContextTree.Items.Add(
            self._context_tree_item("Imports / CAD", import_children)
        )

        category_children = ["Core categories scanned: {0}".format(len(categories))]
        sorted_categories = sorted(categories, key=lambda item: item.get("total_count") if isinstance(item.get("total_count"), int) else -1, reverse=True)
        for item in sorted_categories[:PROJECT_CONTEXT_MAX_ITEMS]:
            category_children.append("{0}: total {1}, active view {2}, selected {3}".format(item.get("category"), item.get("total_count"), item.get("active_view_count"), item.get("selected_count")))
        self.ProjectContextTree.Items.Add(self._context_tree_item("Categories", category_children or ["Not scanned"]))

        schedule_children = [
            "Total: {0}".format(schedules.get("count", "not scanned")),
            "Sampled: {0}".format(schedules.get("sampled_count", schedules.get("count", 0))),
            "AI-generated: {0}".format(len(schedules.get("ai_generated_schedule_names") or [])),
            "Template-like: {0}".format(len(schedules.get("template_like_schedule_candidates") or [])),
            "Suspicious/empty: {0}".format(len(schedules.get("suspicious_or_empty_schedule_candidates") or [])),
        ]
        for item in schedules.get("schedules", [])[:PROJECT_CONTEXT_MAX_ITEMS]:
            status = self._schedule_status_label(item.get("body_row_count"))
            details = [
                "category: {0}".format(item.get("category") or "not scanned"),
                "fields: {0}".format(len(item.get("field_names") or [])),
                "filters: {0}".format(len(item.get("filter_summaries") or [])),
                "sorting/grouping: {0}".format(len(item.get("sort_group_fields") or [])),
                "itemized: {0}".format(self._context_not_scanned(item.get("is_itemized"))),
                "status: {0}".format(status),
                "AI-generated: {0}".format(bool(item.get("appears_ai_generated"))),
                "template-like: {0}".format(bool(item.get("appears_template_like"))),
                "vendor tokens: {0}".format(", ".join(item.get("vendor_project_tokens") or []) or "none"),
            ]
            filters = item.get("filter_summaries") or []
            if filters:
                details.append("filter fields: {0}".format(", ".join(["{0} {1}".format(f.get("field"), f.get("operator")) for f in filters[:5]])))
            sort_fields = item.get("sort_group_fields") or []
            if sort_fields:
                details.append("sort fields: {0}".format(", ".join(sort_fields[:5])))
            schedule_children.append(self._context_tree_item("{0} | {1}".format(item.get("name") or "(unnamed schedule)", status), details))
        self.ProjectContextTree.Items.Add(
            self._context_tree_item("Schedules", schedule_children)
        )

        selection_children = [
            "Selected elements: {0}".format(selection.get("selected_element_count", 0)),
            "Categories: {0}".format(", ".join(["{0}={1}".format(k, v) for k, v in (selection.get("selected_categories") or {}).items()]) if selection.get("selected_categories") else "No active-document selection"),
        ]
        if selection.get("selected_family_type_names"):
            selection_children.append("Family/type samples: {0}".format(", ".join(list(selection.get("selected_family_type_names", {}).keys())[:8])))
        if selection.get("selected_levels_or_reference_levels"):
            selection_children.append("Level/reference samples: {0}".format(", ".join(list(selection.get("selected_levels_or_reference_levels", {}).keys())[:8])))
        for sample in selection.get("samples", [])[:10]:
            selection_children.append("id {0}: {1} | {2} | level {3}".format(sample.get("id"), sample.get("category"), sample.get("type"), sample.get("level") or "not scanned"))
        self.ProjectContextTree.Items.Add(
            self._context_tree_item("Selection", selection_children)
        )

        warning_children = ["Total: {0}".format(warnings.get("total_warning_count", 0))]
        if warnings.get("total_warning_count", 0) == 0:
            warning_children.append("No Revit warnings detected by scanner")
        for group in warnings.get("top_warning_groups", [])[:10]:
            ids = group.get("example_element_ids") or []
            warning_children.append("{0} ({1}) | samples: {2}".format(group.get("description"), group.get("count"), ", ".join([safe_str(item) for item in ids]) if ids else "none"))
        self.ProjectContextTree.Items.Add(
            self._context_tree_item("Warnings", warning_children)
        )
        self.ProjectContextTree.Items.Add(
            self._context_tree_item(
                "Detected Issues",
                [self._context_tree_item("{0}: {1}".format(issue.get("id"), issue.get("message")), ["severity: {0}".format(issue.get("severity", "info")), "suggested next safe step: {0}".format(self._issue_suggested_action(issue.get("id")))]) for issue in issues[:10]] or ["No issues detected in scanned sections."],
            )
        )
        onboarding = build_project_onboarding_checklist(context)
        onboarding_children = [
            "Overall status: {0}".format(onboarding.get("overall_status", "UNKNOWN")),
            "Automation readiness: {0}".format(onboarding.get("automation_readiness_status", "UNKNOWN")),
        ]
        onboarding_children.append(
            self._context_tree_item(
                "Checklist sections",
                [
                    "{0} [{1}] - {2}".format(section.get("title"), section.get("status"), section.get("result"))
                    for section in (onboarding.get("sections") or [])
                ],
            )
        )
        onboarding_children.append(
            self._context_tree_item(
                "Recommended next actions",
                ["{0}. {1}".format(index + 1, action) for index, action in enumerate(onboarding.get("recommended_next_actions", [])[:8])] or ["No onboarding recommendations available."],
            )
        )
        onboarding_children.append("Safety: read-only checklist; no model data was modified.")
        self.ProjectContextTree.Items.Add(
            self._context_tree_item("Project Onboarding", onboarding_children)
        )
        self.ProjectContextTree.Items.Add(
            self._context_tree_item(
                "Suggested Actions",
                [self._context_tree_item(action.get("title"), ["reason: {0}".format(action.get("recommendation_reason")), "risk: {0}".format(action.get("risk_level", action.get("risk", "low"))), "approval required: {0}".format("yes" if action.get("role") == "modify" or action.get("command_role") == "modify" else "no"), "read-only: {0}".format("yes" if action.get("role") != "modify" and action.get("command_role") != "modify" else "no")]) for action in self._recommended_project_actions_from_context(context)] or ["No reviewed suggestions available from current scan."],
            )
        )
        brief_children = ["Use Create Codex Brief for a copyable development/debug instruction."]
        if self.latest_codex_brief:
            first_lines = [line for line in self.latest_codex_brief.splitlines() if line.strip()][:5]
            brief_children.append("Latest brief preview:")
            brief_children.extend(first_lines)
        self.ProjectContextTree.Items.Add(
            self._context_tree_item("Developer / Codex Brief", brief_children)
        )

    def _store_project_context_result(self, result, depth):
        context = result.get("context", {}) if isinstance(result, dict) else {}
        self.latest_project_context = context
        self.latest_project_context_summary = result.get("summary", "") if isinstance(result, dict) else ""
        self.latest_project_context_timestamp = context.get("timestamp_utc")
        self.latest_project_context_scan_depth = context.get("scan_depth", depth)
        doc_info = context.get("document", {})
        active_view = doc_info.get("active_view", {})
        self.latest_project_context_doc_title = doc_info.get("title")
        self.latest_project_context_active_view_id = active_view.get("id")
        warning_count = len(result.get("warnings", [])) if isinstance(result, dict) else 0
        status = "{0} context loaded".format((self.latest_project_context_scan_depth or depth).title())
        if warning_count:
            status = "{0}; partial scan warnings: {1}".format(status, warning_count)
        self._set_project_context_status(status)
        self.populate_project_context_tree()

    def get_latest_project_context(self, require_standard=False, allow_scan=True):
        context = self.latest_project_context or {}
        current_depth = (context.get("scan_depth") or self.latest_project_context_scan_depth or "").lower()
        if require_standard and current_depth != "standard" and allow_scan:
            result = self.run_project_context_scan("standard")
            context = result.get("context", {}) if isinstance(result, dict) else {}
        elif not context and allow_scan:
            result = self.run_project_context_scan("bootstrap")
            context = result.get("context", {}) if isinstance(result, dict) else {}
        return context

    def get_latest_project_context_summary(self, require_standard=False):
        context = self.get_latest_project_context(require_standard=require_standard)
        if require_standard or not self.latest_project_context_summary:
            self.latest_project_context_summary = _summarize_project_context(context)
        return self.latest_project_context_summary or "(no scan summary available)"

    def run_project_context_scan(self, depth="standard"):
        result = collect_project_context(doc, uidoc, depth)
        self._store_project_context_result(result, depth)
        return result

    def load_bootstrap_project_context(self):
        try:
            result = self.run_project_context_scan("bootstrap")
            self._set_project_context_status("Bootstrap context loaded")
            return result
        except Exception as exc:
            self._set_project_context_status("Scan failed partially: {0}".format(str(exc)))
            self.populate_project_context_tree()
            return None

    def on_project_scan(self, sender, args):
        self.update_window_status("executing", "Project context scan")
        self._set_thinking("Scanning project context...")
        self.show_busy()
        self._pump_ui()
        try:
            result = self.run_project_context_scan("standard")
            summary = result.get("summary", "")
            self.remember_latest_deterministic_report("scan current project", summary)
            self.append_chat_notice("PROJECT SCAN COMPLETE", summary)
            self.update_window_status("ready_to_execute", "Standard project scan complete")
        except Exception as exc:
            self.append_chat_notice("PROJECT SCAN FAILED", str(exc))
            self._set_project_context_status("Scan failed partially")
            self.update_window_status("failed", "Project scan failed")
        finally:
            self.hide_busy()
            self._set_thinking("Thinking...")

    def _normalize_context_prompt(self, prompt):
        text = safe_str(prompt).lower().strip()
        text = text.replace("&", " and ")
        text = text.replace("/", " and ")
        text = text.replace("\\", " and ")
        text = re.sub(r"[-_]+", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _is_scan_request(self, prompt):
        text = self._normalize_context_prompt(prompt)
        return text in ("scan current project", "refresh project context", "project context") or "scan current project" in text or "refresh project context" in text

    def _is_project_context_question(self, prompt):
        text = self._normalize_context_prompt(prompt)
        triggers = [
            "summarize current project",
            "what is in this revit model",
            "summary",
            "what links are loaded",
            "what linked models are loaded",
            "list revit links",
            "show revit links",
            "links",
            "check linked model coordinates",
            "linked model coordinate health",
            "link coordinates",
            "are the links aligned",
            "do the linked models match coordinates",
            "check revit link transforms",
            "are any links unloaded",
            "coordinate health check",
            "project coordinates check",
            "project onboarding checklist",
            "project onboarding",
            "onboarding",
            "onboarding checklist",
            "start project checklist",
            "startup checklist",
            "project startup checklist",
            "bim startup checklist",
            "project readiness",
            "what should i check first when opening a project",
            "what should i do first in this project",
            "guide me through this project",
            "project readiness check",
            "automation readiness check",
            "guided project startup plan",
            "project startup plan",
            "startup plan",
            "create project startup plan",
            "what should the agent do first",
            "agent project plan",
            "project diagnostic plan",
            "check bim basis",
            "bim basis ils check",
            "iso style check",
            "iso 19650 style check",
            "bim basis",
            "bim basis and levels and grids",
            "compare host and linked levels",
            "compare linked model levels",
            "compare host and linked grids",
            "compare linked model grids",
            "compare host and linked levels and grids",
            "check levels and grids",
            "levels and grids",
            "levels grids",
            "host link levels and grids",
            "host vs linked levels",
            "host vs linked grids",
            "host vs linked model level grid check",
            "are linked model levels aligned",
            "are linked model grids aligned",
            "level grid health check",
            "host vs link level grid check",
            "what cad imports",
            "cad and imports",
            "list cad imports",
            "show imports",
            "imports",
            "what schedules",
            "list schedules",
            "show schedules",
            "schedules",
            "empty schedules",
            "empty and suspicious",
            "suspicious schedules",
            "template schedules",
            "list levels",
            "show levels",
            "levels",
            "list views",
            "show views",
            "views",
            "list sheets",
            "show sheets",
            "sheets",
            "what categories",
            "show category counts",
            "categories",
            "show warnings",
            "warnings",
            "main bim issues",
            "issues",
            "what should i check first",
            "what should i test first",
            "what should i inspect first",
            "where should i start",
            "first checks",
            "recommended checks",
            "project first checks",
            "recommended project checks",
            "project context",
            "loaded links",
            "ai-generated",
            "ai generated",
        ]
        return any(trigger in text for trigger in triggers)

    def _is_codex_brief_request(self, prompt):
        text = (prompt or "").lower()
        return any(trigger in text for trigger in ["create codex task brief", "generate developer task", "prepare codex instruction", "make implementation brief"])

    def _ensure_project_context(self, depth="standard"):
        return self.get_latest_project_context(require_standard=(depth == "standard"))

    def _context_query_kind(self, prompt):
        normalized = self._normalize_context_prompt(prompt)
        exact = {
            "scan current project": "scan",
            "refresh project context": "scan",
            "summarize current project": "summary",
            "summary": "summary",
            "project context": "summary",
            "project onboarding checklist": "onboarding",
            "project onboarding": "onboarding",
            "onboarding": "onboarding",
            "onboarding checklist": "onboarding",
            "start project checklist": "onboarding",
            "startup checklist": "onboarding",
            "project startup checklist": "onboarding",
            "bim startup checklist": "onboarding",
            "project readiness": "onboarding",
            "what should i check first when opening a project": "onboarding",
            "what should i check first in this project": "onboarding",
            "what should i test first in this project": "onboarding",
            "what should i do first in this project": "onboarding",
            "guide me through this project": "onboarding",
            "project first checks": "onboarding",
            "recommended project checks": "onboarding",
            "project readiness check": "onboarding",
            "automation readiness check": "onboarding",
            "guided project startup plan": "guided_startup_plan",
            "project startup plan": "guided_startup_plan",
            "startup plan": "guided_startup_plan",
            "create project startup plan": "guided_startup_plan",
            "what should the agent do first": "guided_startup_plan",
            "agent project plan": "guided_startup_plan",
            "project diagnostic plan": "guided_startup_plan",
            "list levels": "levels",
            "show levels": "levels",
            "levels": "levels",
            "list views": "views",
            "show views": "views",
            "views": "views",
            "list sheets": "sheets",
            "show sheets": "sheets",
            "sheets": "sheets",
            "what links are loaded": "links",
            "what linked models are loaded": "link_coordinate_health",
            "are any links unloaded": "link_coordinate_health",
            "list revit links": "links",
            "show revit links": "links",
            "links": "links",
            "check linked model coordinates": "link_coordinate_health",
            "linked model coordinate health": "link_coordinate_health",
            "link coordinates": "link_coordinate_health",
            "are the links aligned": "link_coordinate_health",
            "do the linked models match coordinates": "link_coordinate_health",
            "check revit link transforms": "link_coordinate_health",
            "coordinate health check": "link_coordinate_health",
            "project coordinates check": "link_coordinate_health",
            "check bim basis": "level_grid_health",
            "bim basis": "level_grid_health",
            "bim basis and levels and grids": "level_grid_health",
            "bim basis ils check": "level_grid_health",
            "iso style check": "level_grid_health",
            "iso 19650 style check": "level_grid_health",
            "compare host and linked levels": "level_grid_health",
            "compare linked model levels": "level_grid_health",
            "compare host and linked grids": "level_grid_health",
            "compare linked model grids": "level_grid_health",
            "compare host and linked levels and grids": "level_grid_health",
            "check levels and grids": "level_grid_health",
            "levels and grids": "level_grid_health",
            "levels grids": "level_grid_health",
            "host link levels and grids": "level_grid_health",
            "host vs linked levels": "level_grid_health",
            "host vs linked grids": "level_grid_health",
            "host vs linked model level grid check": "level_grid_health",
            "are linked model levels aligned": "level_grid_health",
            "are linked model grids aligned": "level_grid_health",
            "level grid health check": "level_grid_health",
            "host vs link level grid check": "level_grid_health",
            "what cad imports are in this model": "imports",
            "cad and imports": "imports",
            "list cad imports": "imports",
            "show imports": "imports",
            "imports": "imports",
            "what schedules exist": "schedules",
            "list schedules": "schedules",
            "show schedules": "schedules",
            "schedules": "schedules",
            "show empty schedules": "empty_schedules",
            "show suspicious schedules": "empty_schedules",
            "empty and suspicious": "empty_schedules",
            "show template schedules": "template_schedules",
            "what categories are in this model": "categories",
            "show category counts": "categories",
            "categories": "categories",
            "what are the main bim issues": "issues",
            "show warnings": "warnings",
            "warnings": "warnings",
            "issues": "issues",
            "what should i inspect first": "first_checks",
            "where should i start": "first_checks",
            "first checks": "first_checks",
            "recommended checks": "first_checks",
            "project first checks": "onboarding",
            "summary": "summary",
            "first checks": "first_checks",
            "empty schedules": "empty_schedules",
        }
        if normalized in exact:
            return exact[normalized]
        if "scan current project" in normalized or "refresh project context" in normalized:
            return "scan"
        if "project onboarding" in normalized or normalized == "onboarding" or "onboarding checklist" in normalized or "startup checklist" in normalized or "bim startup" in normalized or "project readiness" in normalized or "automation readiness" in normalized or "guide me through this project" in normalized or "what should i do first in this project" in normalized or "what should i check first when opening a project" in normalized or "what should i check first in this project" in normalized or "what should i test first in this project" in normalized or "recommended project checks" in normalized:
            return "onboarding"
        if "guided project startup plan" in normalized or "project startup plan" in normalized or normalized == "startup plan" or "create project startup plan" in normalized or "what should the agent do first" in normalized or "agent project plan" in normalized or "project diagnostic plan" in normalized:
            return "guided_startup_plan"
        if "empty" in normalized and "schedule" in normalized:
            return "empty_schedules"
        if "suspicious" in normalized and "schedule" in normalized:
            return "empty_schedules"
        if "template" in normalized and "schedule" in normalized:
            return "template_schedules"
        if "schedule" in normalized:
            return "schedules"
        if ("coordinate" in normalized and ("link" in normalized or "project" in normalized)) or "link transform" in normalized or "links aligned" in normalized or "linked models match coordinates" in normalized or "links unloaded" in normalized or "linked models are loaded" in normalized:
            return "link_coordinate_health"
        if "bim basis" in normalized or "ils check" in normalized or "iso style" in normalized or "iso 19650 style" in normalized or "level grid" in normalized or ("level" in normalized and "grid" in normalized) or ("linked model levels" in normalized) or ("linked model grids" in normalized) or ("host and linked levels" in normalized) or ("host and linked grids" in normalized):
            return "level_grid_health"
        if "cad" in normalized or "import" in normalized:
            return "imports"
        if "link" in normalized:
            return "links"
        if "warning" in normalized:
            return "warnings"
        if "issue" in normalized or "bim issue" in normalized:
            return "issues"
        if "check first" in normalized or "test first" in normalized or "inspect first" in normalized or "first check" in normalized or "recommended check" in normalized or "where should i start" in normalized:
            return "first_checks"
        if "level" in normalized:
            return "levels"
        if "view" in normalized:
            return "views"
        if "sheet" in normalized:
            return "sheets"
        if "categor" in normalized:
            return "categories"
        if "summarize current project" in normalized or "what is in this revit model" in normalized:
            return "summary"
        return None

    def _level_grid_query_focus(self, prompt):
        text = (prompt or "").strip().lower()
        normalized = re.sub(r"\s+", " ", text)
        if "grid" in normalized and "level" not in normalized:
            return "grids"
        if "levels" in normalized and "grid" not in normalized:
            return "levels"
        if "linked model levels" in normalized or "host and linked levels" in normalized or "levels aligned" in normalized:
            return "levels"
        if "linked model grids" in normalized or "host and linked grids" in normalized or "grids aligned" in normalized:
            return "grids"
        return "both"

    def _format_context_levels(self, context):
        levels = context.get("levels", {})
        lines = ["Levels", "- Count: {0}".format(levels.get("count", 0))]
        for level in levels.get("levels", [])[:PROJECT_CONTEXT_MAX_ITEMS]:
            elevation = level.get("elevation")
            elevation_text = "not scanned" if elevation is None else "{0:.3f}".format(elevation)
            alias = level.get("simplified_name") or "not scanned"
            note = " | ambiguous alias" if level.get("ambiguous_alias") else ""
            lines.append("- {0} | elevation: {1} | alias: {2}{3}".format(level.get("exact_name"), elevation_text, alias, note))
        for ambiguous in levels.get("ambiguous_aliases", [])[:10]:
            lines.append("- Ambiguous: {0} -> {1}".format(ambiguous.get("simplified_name"), ", ".join(ambiguous.get("candidates", []))))
        return "\n".join(lines)

    def _format_context_views(self, context):
        views = context.get("views", {})
        lines = ["Views", "- Total: {0}".format(views.get("total", 0))]
        samples_by_type = views.get("samples_by_view_type", {}) or {}
        for view_type, count in sorted((views.get("by_view_type", {}) or {}).items()):
            lines.append("- {0}: {1}".format(view_type, count))
            for sample in samples_by_type.get(view_type, [])[:6]:
                marker = " [active]" if sample.get("is_active") else ""
                lines.append("  - {0} (id {1}){2}".format(sample.get("name"), sample.get("id"), marker))
        return "\n".join(lines)

    def _format_context_sheets(self, context):
        sheets = context.get("sheets", {})
        lines = ["Sheets", "- Total: {0}".format(sheets.get("count", 0))]
        for sheet in sheets.get("samples", [])[:PROJECT_CONTEXT_MAX_ITEMS]:
            placed = sheet.get("placed_view_count", "not scanned")
            if placed == "not scanned":
                placed_text = "placement status not scanned"
            elif placed == 0:
                placed_text = "no placed views"
            else:
                placed_text = "{0} placed views".format(placed)
            lines.append("- {0} - {1} | {2}".format(sheet.get("number") or "(no number)", sheet.get("name") or "(unnamed sheet)", placed_text))
        return "\n".join(lines)

    def _format_context_links(self, context):
        links = context.get("links", {})
        lines = [
            "[REVIT LINKS]",
            "Instances: {0} | Loaded: {1} | Unavailable/unloaded: {2}".format(
                links.get("instance_count", 0),
                len(links.get("loaded_links") or []),
                len(links.get("unavailable_links") or []),
            ),
            "",
            "[LINK DETAILS]",
        ]
        for item in links.get("instances", [])[:PROJECT_CONTEXT_MAX_ITEMS]:
            lines.append("- {0} [{1}]".format(
                item.get("display_name") or item.get("type_name") or item.get("instance_name") or "(unnamed)",
                item.get("status") or "unknown",
            ))
            lines.append("  type: {0} | document: {1}".format(
                item.get("type_name") or "unknown",
                item.get("linked_document_title") or "not loaded/not scanned",
            ))
            lines.append("  path: {0}".format(item.get("path_info") or "<path not available>"))
        for duplicate in links.get("duplicate_link_types_or_instances", [])[:5]:
            lines.append("- Duplicate: {0} ({1} instances)".format(duplicate.get("type_name"), duplicate.get("instance_count")))
        return "\n".join(lines)

    def _format_context_imports(self, context):
        imports = context.get("imports_cad", {})
        lines = [
            "[CAD / IMPORTS]",
            "Total: {0} | Linked CAD: {1} | Imported CAD: {2} | View-specific: {3}".format(
                imports.get("total_import_instances", 0),
                imports.get("linked_cad_count", 0),
                imports.get("imported_cad_count", 0),
                imports.get("view_specific_import_count", 0),
            ),
        ]
        if imports.get("total_import_instances", 0) == 0:
            lines.append("")
            lines.append("No CAD/import instances found.")
            return "\n".join(lines)
        lines.append("")
        lines.append("[IMPORT DETAILS]")
        for item in imports.get("imports", [])[:PROJECT_CONTEXT_MAX_ITEMS]:
            linked = "linked" if item.get("linked") else ("imported" if item.get("linked") is False else "linked/imported unknown")
            scope = "view-specific: {0}".format(item.get("owner_view_name")) if item.get("owner_view_name") else "model-wide/not owner-view scoped"
            lines.append("- {0} | {1} | {2} | category: {3}".format(item.get("instance_name") or "(unnamed)", linked, scope, item.get("category") or "not scanned"))
        return "\n".join(lines)

    def _format_link_coordinate_health(self, context):
        health = _get_linked_model_coordinate_health(context)
        counts = self._coordinate_health_counts(health)
        lines = [
            "[LINK COORDINATE HEALTH]",
            "",
            "Summary",
            "- Links scanned: {0}".format(health.get("total_links", 0)),
            "- Loaded: {0}".format(health.get("loaded_readable_count", 0)),
            "- Unloaded/unavailable: {0}".format(health.get("unavailable_count", 0)),
            "- OK: {0}".format(counts.get("OK", 0)),
            "- Review: {0}".format(counts.get("REVIEW", 0)),
            "- Unknown: {0}".format(counts.get("UNKNOWN", 0)),
            "- Non-identity transforms: {0}".format(health.get("non_identity_transform_count", 0)),
            "- Rotated links: {0}".format(health.get("rotated_link_count", 0)),
            "- Z-offset links: {0}".format(health.get("z_offset_link_count", 0)),
            "- Duplicate link instances/types: {0}".format(health.get("duplicate_link_instance_count", 0)),
            "- Overall status: {0}".format(health.get("overall_status", "not scanned")),
            "",
        ]
        links = health.get("links", []) or []
        suspicious = [item for item in links if item.get("status_label") != "OK"]
        if links and not suspicious:
            lines.append("Links")
            lines.append("All sampled links are currently marked OK by the deterministic transform checks.")
            lines.append("Full per-link origin/rotation details remain available in Project Context > Links / Coordinate Health.")
        elif links:
            ordered = suspicious + [item for item in links if item.get("status_label") == "OK"]
            lines.append("Links")
            for index, item in enumerate(ordered[:10]):
                reasons = self._coord_reason_list(item)
                flags = item.get("coordinate_flags") or []
                load_status = self._link_load_status_text(item)
                lines.append("{0}. {1} [{2}] [{3}]".format(index + 1, item.get("display_name") or item.get("type_name") or item.get("instance_name"), load_status, item.get("status_label", "UNKNOWN")))
                lines.append("   - Origin: {0}".format(self._coord_origin_text(item)))
                lines.append("   - Rotation Z: {0}".format(self._coord_rotation_text(item)))
                lines.append("   - Transform: {0}".format(self._coord_transform_text(item)))
                if flags:
                    lines.append("   - Flags: {0}".format(", ".join(flags)))
                    lines.append("   - Suggested safe step: inspect link placement and coordinate setup manually.")
                else:
                    lines.append("   - Notes: no coordinate flags detected")
                if item.get("status_label") in ("REVIEW", "UNLOADED", "UNKNOWN"):
                    lines.append("   - Reasons: {0}".format("; ".join(reasons)))
            if len(ordered) > 10:
                lines.append("")
                lines.append("More links are available in Project Context > Links / Coordinate Health.")
        else:
            lines.append("No link coordinate details were scanned. Run Scan Project first.")
        lines.append("")
        lines.append("Safety")
        lines.append("- This is a read-only diagnostic.")
        lines.append("- Linked-model placement and coordinate state were not changed.")
        return "\n".join(lines)

    def _format_level_grid_health(self, context, focus="both"):
        health = _get_host_link_level_grid_health(context)
        if not health:
            return "\n".join(
                [
                    "[BIM BASIS / LEVELS & GRIDS]",
                    "",
                    "Level/grid health is not available; run standard Scan Project first.",
                    "",
                    "Safety",
                    "- This is a read-only diagnostic.",
                    "- No levels, grids, links, coordinates, or model data were modified.",
                ]
            )
        lines = [
            "[BIM BASIS / LEVELS & GRIDS]",
            "",
            "Summary",
            "- Profile: {0}".format(health.get("profile", "Configurable BIM Basis / ILS-style")),
            "- Note: project coordination diagnostic, not formal ISO 19650 certification.",
            "- Overall status: {0}".format(health.get("overall_status", "UNKNOWN")),
            "- Host levels: {0}".format(health.get("host_level_count", "not scanned")),
            "- Host grids: {0}".format(health.get("host_grid_count", "not scanned")),
            "- Links compared: {0}".format(health.get("links_compared_count", 0)),
            "- Links skipped: {0}".format(health.get("links_skipped_count", 0)),
            "- Level tolerance: {0} mm".format(health.get("level_elevation_tolerance_mm", LEVEL_ELEVATION_TOLERANCE_MM)),
            "- Grid offset tolerance: {0} mm".format(health.get("grid_linear_offset_tolerance_mm", GRID_LINEAR_OFFSET_TOLERANCE_MM)),
            "- Grid angle tolerance: {0} deg".format(health.get("grid_angle_tolerance_degrees", GRID_ANGLE_TOLERANCE_DEG)),
        ]
        status_text = (health.get("status_wording") or {}).get(health.get("overall_status"))
        if status_text:
            lines.append("- Status wording: {0}".format(status_text))
        if health.get("host_grid_basis_limited"):
            lines.append("- Grid basis note: Host contains no grids; linked grid comparison is basis-dependent.")
            lines.append("- Coordination note: If grids are intentionally maintained in the architectural/structural link, this is not necessarily an error.")
        lines.extend(["", "Compact Findings"])
        if focus in ("both", "levels"):
            lines.append("- Level findings by type: {0}".format(_format_level_grid_type_counts(health.get("level_findings_by_type") or {})))
        if focus in ("both", "grids"):
            lines.append("- Grid findings by type: {0}".format(_format_level_grid_type_counts(health.get("grid_findings_by_type") or {})))
        top_links = health.get("top_affected_links") or []
        if top_links:
            lines.append("- Top affected links: {0}".format("; ".join([
                "{0} (L{1}/G{2})".format(item.get("link_name"), item.get("level_findings_count", 0), item.get("grid_findings_count", 0))
                for item in top_links[:5]
            ])))
        if focus in ("both", "levels"):
            lines.extend(["", "Level Findings"])
            lines.append("- Total: {0}".format(health.get("level_findings_count", 0)))
            groups = _aggregate_level_grid_findings(health.get("level_findings") or [], 5)
            for group in groups[:10]:
                sample = ", ".join(group.get("sample_names") or [])
                lines.append("  - {0} [{1}]: {2}{3}".format(group.get("type"), group.get("severity"), group.get("count"), " | samples: {0}".format(sample) if sample else ""))
            if not groups:
                lines.append("  - none")
        if focus in ("both", "grids"):
            lines.extend(["", "Grid Findings", "- Total: {0}".format(health.get("grid_findings_count", 0))])
            groups = _aggregate_level_grid_findings(health.get("grid_findings") or [], 5)
            for group in groups[:10]:
                sample = ", ".join(group.get("sample_names") or [])
                lines.append("  - {0} [{1}]: {2}{3}".format(group.get("type"), group.get("severity"), group.get("count"), " | samples: {0}".format(sample) if sample else ""))
            if not groups:
                lines.append("  - none")
        lines.extend(["", "Per-Link Findings"])
        link_rows = health.get("links_compared") or []
        for index, link_row in enumerate(link_rows[:10]):
            all_findings = (link_row.get("level_findings") or []) + (link_row.get("grid_findings") or [])
            lines.append("{0}. {1} [{2}]".format(index + 1, link_row.get("link_name"), link_row.get("status", "UNKNOWN")))
            lines.append("   - linked document: {0}".format(link_row.get("linked_document_title")))
            if focus in ("both", "levels"):
                level_groups = _aggregate_level_grid_findings(link_row.get("level_findings") or [], 5)
                lines.append("   - Levels: {0} | level findings: {1}".format(link_row.get("level_count"), len(link_row.get("level_findings") or [])))
                lines.append("   - Level issue types: {0}".format(_format_level_grid_type_counts(_level_grid_findings_by_type(link_row.get("level_findings") or []))))
                lines.append("   - Sample levels: {0}".format(", ".join([name for row in level_groups for name in row.get("sample_names", [])][:5]) or "none"))
            if focus in ("both", "grids"):
                grid_groups = _aggregate_level_grid_findings(link_row.get("grid_findings") or [], 5)
                lines.append("   - Grids: {0} | grid findings: {1}".format(link_row.get("grid_count"), len(link_row.get("grid_findings") or [])))
                lines.append("   - Grid issue types: {0}".format(_format_level_grid_type_counts(_level_grid_findings_by_type(link_row.get("grid_findings") or []))))
                lines.append("   - Sample grids: {0}".format(", ".join([name for row in grid_groups for name in row.get("sample_names", [])][:5]) or "none"))
            if focus == "both" and not all_findings:
                lines.append("   - Findings: none")
        if len(link_rows) > 10:
            lines.append("More per-link findings are available in Project Context > BIM Basis / Levels & Grids.")
        skipped = health.get("links_skipped") or []
        if skipped:
            lines.append("")
            lines.append("Skipped / Inaccessible Links")
            for item in skipped[:8]:
                lines.append("- {0}: {1}".format(item.get("link_name"), item.get("reason")))
        lines.extend(
            [
                "",
                "Safety",
                "- This is a read-only diagnostic.",
                "- No levels, grids, links, coordinates, or model data were modified.",
            ]
        )
        return "\n".join(lines)

    def _format_context_categories(self, context):
        categories = context.get("category_counts", []) or []
        lines = ["Category Counts", "- Core categories scanned: {0}".format(len(categories))]
        sorted_categories = sorted(categories, key=lambda item: item.get("total_count") if isinstance(item.get("total_count"), int) else -1, reverse=True)
        for item in sorted_categories[:PROJECT_CONTEXT_MAX_ITEMS]:
            lines.append("- {0}: total {1}, active view {2}, selected {3}".format(item.get("category"), item.get("total_count"), item.get("active_view_count"), item.get("selected_count")))
        if not categories:
            lines.append("- Category counts were not scanned.")
        return "\n".join(lines)

    def _format_context_schedules(self, context, mode="all"):
        schedules = context.get("schedules", {})
        items = schedules.get("schedules", []) or []
        sampled_count = schedules.get("sampled_count", len(items))
        if sampled_count == 0 and items:
            sampled_count = len(items)
        all_populated_count = len([item for item in items if isinstance(item.get("body_row_count"), int) and item.get("body_row_count") > 0])
        all_empty_count = len([item for item in items if item.get("body_row_count") == 0])
        all_unknown_count = len([item for item in items if not isinstance(item.get("body_row_count"), int)])
        revision_items = [item for item in items if "revision" in (item.get("name") or "").lower()]
        revision_names = set([item.get("name") for item in revision_items])
        populated_items = [item for item in items if item.get("name") not in revision_names and isinstance(item.get("body_row_count"), int) and item.get("body_row_count") > 0]
        empty_items = [item for item in items if item.get("name") not in revision_names and item.get("body_row_count") == 0]
        unknown_items = [item for item in items if item.get("name") not in revision_names and not isinstance(item.get("body_row_count"), int)]
        detail_limit = 15
        populated_limit = 10
        empty_limit = 5
        if mode == "empty":
            names = schedules.get("suspicious_or_empty_schedule_candidates") or []
            lines = ["[EMPTY / SUSPICIOUS SCHEDULES]", "Candidates: {0}".format(len(names))]
            for item in names[:PROJECT_CONTEXT_MAX_ITEMS]:
                lines.append("- {0} | rows: {1} | {2}".format(item.get("name"), item.get("body_row_count"), item.get("reason")))
            return "\n".join(lines)
        if mode == "template":
            names = schedules.get("template_like_schedule_candidates") or []
            lines = ["[TEMPLATE-LIKE SCHEDULES]", "Candidates: {0}".format(len(names))]
            for name in names[:PROJECT_CONTEXT_MAX_ITEMS]:
                lines.append("- {0}".format(name))
            return "\n".join(lines)
        lines = [
            "[SCHEDULES]",
            "Total: {0} | Sampled: {1} | Populated: {2} | Empty: {3} | Unknown: {4}".format(
                schedules.get("count", "not scanned"),
                sampled_count,
                all_populated_count,
                all_empty_count,
                all_unknown_count,
            ),
        ]
        if revision_items:
            lines.append("Revision schedules: {0}".format(len(revision_items)))
        if not items:
            lines.append("")
            lines.append("No schedule details were scanned.")
            return "\n".join(lines)

        def append_schedule_row(item):
            filters = item.get("filter_summaries") or []
            sort_fields = item.get("sort_group_fields") or []
            lines.append("- {0} | category: {1} | {2} | fields {3}, filters {4}, sort/group {5} | itemized {6} | AI {7} | template {8} | tokens {9}".format(
                item.get("name") or "(unnamed schedule)",
                item.get("category") or "not scanned",
                self._schedule_status_label(item.get("body_row_count")),
                len(item.get("field_names") or []),
                len(filters),
                len(sort_fields),
                self._context_not_scanned(item.get("is_itemized")),
                bool(item.get("appears_ai_generated")),
                bool(item.get("appears_template_like")),
                ", ".join(item.get("vendor_project_tokens") or []) or "none",
            ))
            if filters:
                lines.append("  filters: {0}".format(", ".join(["{0} {1}".format(f.get("field"), f.get("operator")) for f in filters[:5]])))
            if sort_fields:
                lines.append("  sort/group: {0}".format(", ".join(sort_fields[:5])))

        shown = 0
        if populated_items:
            lines.append("")
            lines.append("[POPULATED SCHEDULES]")
            for item in populated_items[:populated_limit]:
                append_schedule_row(item)
                shown += 1
        if empty_items:
            lines.append("")
            lines.append("[EMPTY / SUSPICIOUS SCHEDULES]")
            for item in empty_items[:empty_limit]:
                append_schedule_row(item)
                shown += 1
            if len(empty_items) > empty_limit:
                lines.append("  More empty/suspicious schedules are available in the Project Context > Schedules tree.")
        if unknown_items and shown < detail_limit:
            lines.append("")
            lines.append("[UNKNOWN ROW COUNT SCHEDULES]")
            for item in unknown_items[:detail_limit - shown]:
                append_schedule_row(item)
                shown += 1
        if len(items) > shown:
            lines.append("")
            lines.append("More schedules are available in the Project Context > Schedules tree.")
        return "\n".join(lines)

    def _format_context_warnings(self, context):
        warnings = context.get("warnings_summary", {})
        lines = ["Warnings", "- Total: {0}".format(warnings.get("total_warning_count", 0))]
        if warnings.get("total_warning_count", 0) == 0:
            lines.append("- No Revit warnings detected by scanner")
        for group in warnings.get("top_warning_groups", [])[:10]:
            ids = group.get("example_element_ids") or []
            lines.append("- {0}: {1} | sample ids: {2}".format(group.get("description"), group.get("count"), ", ".join([safe_str(item) for item in ids]) if ids else "none"))
        return "\n".join(lines)

    def _format_context_issues(self, context):
        issues = context.get("detected_issues", []) or []
        lines = ["[DETECTED BIM ISSUES]", "Issue flags: {0}".format(len(issues))]
        if not issues:
            lines.append("")
            lines.append("No issue flags detected in scanned sections.")
        for issue in issues[:PROJECT_CONTEXT_MAX_ITEMS]:
            lines.append("- {0} [{1}]: {2}".format(issue.get("id"), issue.get("severity", "info"), issue.get("message")))
            lines.append("  Suggested safe step: {0}".format(self._issue_suggested_action(issue.get("id"))))
        return "\n".join(lines)

    def _format_context_first_checks(self, context):
        lines = ["[SUGGESTED FIRST CHECKS]"]
        for index, check in enumerate(_project_first_check_lines(context)[:8]):
            lines.append("{0}. {1}".format(index + 1, check))
        return "\n".join(lines)

    def _format_project_onboarding_checklist(self, context):
        depth = (context.get("scan_depth") or self.latest_project_context_scan_depth or "").lower()
        if depth != "standard":
            return "\n".join(
                [
                    "[PROJECT ONBOARDING CHECKLIST]",
                    "",
                    "Project onboarding checklist requires a standard Scan Project run.",
                    "Current context depth: {0}".format(depth or "not scanned"),
                    "",
                    "Safety",
                    "- This checklist is read-only.",
                    "- No model data was modified.",
                ]
            )
        return format_project_onboarding_checklist(build_project_onboarding_checklist(context))

    def _active_view_report_kind(self, prompt):
        normalized = self._normalize_context_prompt(prompt)
        exact = {
            "active view mep summary": ("summary", None),
            "active view mep report": ("summary", None),
            "count mep elements in active view": ("count", None),
            "count mep in active view": ("count", None),
            "report active view elements by category": ("category", None),
            "active view category report": ("category", None),
            "report active view elements by type": ("type", None),
            "active view type report": ("type", None),
            "active view qa report": ("qa", None),
            "active view health check": ("qa", None),
            "report missing parameters in active view": ("missing_parameters", None),
            "missing parameters in active view": ("missing_parameters", None),
            "active view missing parameters": ("missing_parameters", None),
            "count pipes in active view": ("summary", "Piping"),
            "report pipes in active view": ("summary", "Piping"),
            "active view pipe report": ("summary", "Piping"),
            "active view piping report": ("summary", "Piping"),
            "count ducts in active view": ("summary", "HVAC"),
            "report ducts in active view": ("summary", "HVAC"),
            "active view duct report": ("summary", "HVAC"),
            "active view hvac report": ("summary", "HVAC"),
            "count electrical elements in active view": ("summary", "Electrical"),
            "report electrical elements in active view": ("summary", "Electrical"),
            "active view electrical report": ("summary", "Electrical"),
        }
        if normalized in exact:
            return exact[normalized]
        if "active view" not in normalized:
            return None
        if "missing" in normalized and "parameter" in normalized:
            return ("missing_parameters", None)
        if "category" in normalized and "report" in normalized:
            return ("category", None)
        if "type" in normalized and "report" in normalized:
            return ("type", None)
        if "qa report" in normalized or "health check" in normalized:
            return ("qa", None)
        if "pipe" in normalized or "piping" in normalized:
            return ("summary", "Piping")
        if "duct" in normalized or "hvac" in normalized:
            return ("summary", "HVAC")
        if "electrical" in normalized:
            return ("summary", "Electrical")
        if "mep" in normalized:
            return ("summary", None)
        return None

    def answer_active_view_report_question(self, prompt):
        route = self._active_view_report_kind(prompt)
        if not route:
            return None
        mode, discipline = route
        if mode == "missing_parameters":
            return report_active_view_missing_parameters(doc, uidoc, discipline_filter=discipline)
        return active_view_mep_report(doc, uidoc, mode=mode, discipline_filter=discipline)

    def _system_assignment_report_route(self, prompt):
        normalized = self._normalize_context_prompt(prompt)
        exact = {
            "selected mep system assignment report": ("selection", None),
            "check selected mep systems": ("selection", None),
            "selected system qa report": ("selection", None),
            "selected elements system report": ("selection", None),
            "report selected elements by system": ("selection", None),
            "selected pipe system report": ("selection", "Piping"),
            "check selected pipe systems": ("selection", "Piping"),
            "selected duct system report": ("selection", "HVAC"),
            "check selected duct systems": ("selection", "HVAC"),
            "selected electrical system report": ("selection", "Electrical"),
            "check selected electrical systems": ("selection", "Electrical"),
            "selected circuit report": ("selection", "Electrical"),
            "active view mep system assignment report": ("active_view", None),
            "check active view mep systems": ("active_view", None),
            "active view system qa report": ("active_view", None),
            "report active view elements by system": ("active_view", None),
            "count systems in active view": ("active_view", None),
            "active view pipe system report": ("active_view", "Piping"),
            "check pipe systems in active view": ("active_view", "Piping"),
            "active view duct system report": ("active_view", "HVAC"),
            "check duct systems in active view": ("active_view", "HVAC"),
            "active view electrical system report": ("active_view", "Electrical"),
            "check electrical systems in active view": ("active_view", "Electrical"),
            "active view circuit report": ("active_view", "Electrical"),
        }
        if normalized in exact:
            return exact[normalized]
        if "system" not in normalized and "circuit" not in normalized:
            return None
        scope = None
        if "selected" in normalized or "selection" in normalized:
            scope = "selection"
        elif "active view" in normalized:
            scope = "active_view"
        if not scope:
            return None
        discipline = None
        if "pipe" in normalized or "piping" in normalized:
            discipline = "Piping"
        elif "duct" in normalized or "hvac" in normalized:
            discipline = "HVAC"
        elif "electrical" in normalized or "circuit" in normalized:
            discipline = "Electrical"
        return (scope, discipline)

    def answer_system_assignment_report_question(self, prompt):
        route = self._system_assignment_report_route(prompt)
        if not route:
            return None
        scope, discipline = route
        if scope == "selection":
            return selected_mep_system_assignment_report(doc, uidoc, discipline_filter=discipline)
        return active_view_mep_system_assignment_report(doc, uidoc, discipline_filter=discipline)

    def _discipline_qa_report_route(self, prompt):
        normalized = self._normalize_context_prompt(prompt)
        exact = {
            "selected discipline qa report": ("selection", None),
            "selected mep qa rules report": ("selection", None),
            "check selected mep qa rules": ("selection", None),
            "selected piping qa report": ("selection", "Piping"),
            "selected pipe qa report": ("selection", "Piping"),
            "selected hvac qa report": ("selection", "HVAC"),
            "selected duct qa report": ("selection", "HVAC"),
            "selected electrical qa report": ("selection", "Electrical"),
            "selected electrical circuit qa report": ("selection", "Electrical"),
            "active view discipline qa report": ("active_view", None),
            "active view mep qa rules report": ("active_view", None),
            "check active view mep qa rules": ("active_view", None),
            "active view piping qa report": ("active_view", "Piping"),
            "active view pipe qa report": ("active_view", "Piping"),
            "active view hvac qa report": ("active_view", "HVAC"),
            "active view duct qa report": ("active_view", "HVAC"),
            "active view electrical qa report": ("active_view", "Electrical"),
            "active view electrical circuit qa report": ("active_view", "Electrical"),
        }
        if normalized in exact:
            return exact[normalized]
        if "qa" not in normalized and "rules" not in normalized:
            return None
        scope = None
        if "selected" in normalized or "selection" in normalized:
            scope = "selection"
        elif "active view" in normalized:
            scope = "active_view"
        if not scope:
            return None
        discipline = None
        if "pipe" in normalized or "piping" in normalized:
            discipline = "Piping"
        elif "duct" in normalized or "hvac" in normalized:
            discipline = "HVAC"
        elif "electrical" in normalized or "circuit" in normalized:
            discipline = "Electrical"
        return (scope, discipline)

    def answer_discipline_qa_report_question(self, prompt):
        route = self._discipline_qa_report_route(prompt)
        if not route:
            return None
        scope, discipline = route
        if scope == "selection":
            return selected_discipline_qa_report(doc, uidoc, discipline_filter=discipline)
        return active_view_discipline_qa_report(doc, uidoc, discipline_filter=discipline)

    def _selection_report_kind(self, prompt):
        normalized = self._normalize_context_prompt(prompt)
        exact = {
            "report selected elements by category": "category",
            "selection category report": "category",
            "selected elements by category": "category",
            "report selection by category": "category",
            "report selected elements by type": "type",
            "selection type report": "type",
            "selected elements by type": "type",
            "report selection by type": "type",
            "count selected elements": "count",
            "count selection": "count",
            "bim qa count selected elements": "count",
            "health check selected elements": "health",
            "health check selection": "health",
            "selection health check": "health",
            "report missing parameters from selection": "missing_parameters",
            "report missing parameters from selected elements": "missing_parameters",
            "missing parameters from selection": "missing_parameters",
            "missing selected parameters": "missing_parameters",
        }
        if normalized in exact:
            return exact[normalized]
        if "selected elements" in normalized and "category" in normalized:
            return "category"
        if "selection" in normalized and "category" in normalized and "report" in normalized:
            return "category"
        if "selected elements" in normalized and "type" in normalized:
            return "type"
        if "selection" in normalized and "type" in normalized and "report" in normalized:
            return "type"
        if ("count" in normalized and "selected elements" in normalized) or normalized == "count selection":
            return "count"
        if ("health check" in normalized and "selected" in normalized) or normalized == "selection health check" or normalized == "health check selection":
            return "health"
        if "missing" in normalized and "parameter" in normalized and ("selection" in normalized or "selected" in normalized):
            return "missing_parameters"
        return None

    def answer_selection_report_question(self, prompt):
        kind = self._selection_report_kind(prompt)
        if not kind:
            return None
        if kind == "category":
            return report_selected_elements_by_category(doc, uidoc)
        if kind == "type":
            return report_selected_elements_by_type(doc, uidoc)
        if kind == "count":
            return count_selected_elements(doc, uidoc)
        if kind == "health":
            return health_check_selected_elements(doc, uidoc)
        if kind == "missing_parameters":
            return report_missing_parameters_from_selection(doc, uidoc)
        return None

    def deterministic_project_context_answer(self, prompt):
        kind = self._context_query_kind(prompt)
        if not kind:
            return None
        if kind == "scan":
            result = self.run_project_context_scan("standard")
            return result.get("summary", "")
        context = self.get_latest_project_context(require_standard=False)
        context_depth = (context.get("scan_depth") or self.latest_project_context_scan_depth or "").lower()
        standard_required = kind in ("onboarding", "guided_startup_plan", "link_coordinate_health", "level_grid_health")
        if standard_required and context_depth != "standard":
            return "\n".join(
                [
                    "[PROJECT CONTEXT]",
                    "",
                    "Run Scan Project first for full project-context diagnostics.",
                    "Current context depth: {0}".format(context_depth or "not scanned"),
                ]
            )
        if kind == "summary":
            return _summarize_project_context(context)
        if kind == "onboarding":
            return self._format_project_onboarding_checklist(context)
        if kind == "guided_startup_plan":
            return format_guided_project_startup_plan(build_guided_project_startup_plan(context))
        if kind == "levels":
            return self._format_context_levels(context)
        if kind == "views":
            return self._format_context_views(context)
        if kind == "sheets":
            return self._format_context_sheets(context)
        if kind == "links":
            return self._format_context_links(context)
        if kind == "link_coordinate_health":
            return self._format_link_coordinate_health(context)
        if kind == "level_grid_health":
            return self._format_level_grid_health(context, self._level_grid_query_focus(prompt))
        if kind == "imports":
            return self._format_context_imports(context)
        if kind == "categories":
            return self._format_context_categories(context)
        if kind == "schedules":
            return self._format_context_schedules(context)
        if kind == "empty_schedules":
            return self._format_context_schedules(context, "empty")
        if kind == "template_schedules":
            return self._format_context_schedules(context, "template")
        if kind == "warnings":
            return self._format_context_warnings(context)
        if kind == "issues":
            return self._format_context_issues(context)
        if kind == "first_checks":
            return self._format_context_first_checks(context)
        return None

    def _sanitize_ollama_context_error(self, reply):
        text = safe_str(reply)
        lowered = text.lower()
        if "httpconnectionpool" in lowered or "read timed out" in lowered or "timed out" in lowered or "could not connect to ollama" in lowered:
            return (
                "Ollama did not respond within the configured timeout. The cached Revit project context is still available. "
                "Try one of the deterministic context commands such as 'show schedules', 'show links', 'show warnings', or 'ask agent for plan'."
            )
        return reply

    def _context_for_ollama(self):
        context = self.get_latest_project_context(require_standard=False)
        try:
            payload = json.dumps(context, indent=2, sort_keys=True)
        except:
            payload = safe_str(context)
        if len(payload) > 12000:
            payload = payload[:12000] + "\n... truncated for local model context ..."
        return payload

    def answer_project_context_question(self, prompt):
        deterministic = self.deterministic_project_context_answer(prompt)
        if deterministic is not None:
            return deterministic
        self.get_latest_project_context(require_standard=False)
        grounding = (
            "You are a BIM assistant answering from a Revit API project-context snapshot. "
            "Use only the provided context. If data is missing, say it was not scanned. "
            "Do not claim model state that is not in the context.\n\n"
            "Project context JSON:\n{0}\n\n"
            "User question: {1}".format(self._context_for_ollama(), prompt)
        )
        reply = send_ollama_chat(self.model, grounding)
        return self._sanitize_ollama_context_error(reply)

    def _recommended_project_actions_from_context(self, context):
        issues = set([issue.get("id") for issue in context.get("detected_issues", [])])
        schedules = context.get("schedules", {})
        categories = context.get("category_counts", [])
        selection = context.get("selection", {})
        recommendations = []

        def add(action_id, reason):
            entry = self.catalog.get_entry_by_id(action_id)
            if entry:
                item = dict(entry)
                item["recommendation_reason"] = reason
                recommendations.append(item)

        onboarding = build_project_onboarding_checklist(context)
        if onboarding:
            add("project-onboarding-checklist", "Start with the deterministic project onboarding checklist. Overall status {0}; automation readiness {1}.".format(onboarding.get("overall_status", "UNKNOWN"), onboarding.get("automation_readiness_status", "UNKNOWN")))
        if any(flag in issues for flag in ["host_duplicate_level_names", "linked_duplicate_level_names", "linked_level_elevation_mismatch", "linked_level_missing_in_host", "host_level_missing_in_link", "linked_level_possible_name_mismatch", "host_duplicate_grid_names", "linked_duplicate_grid_names", "linked_grid_missing_in_host", "host_grid_missing_in_link", "linked_grid_geometry_mismatch", "linked_grid_geometry_unknown", "level_grid_scan_partial"]):
            level_grid = _get_host_link_level_grid_health(context)
            add("check-bim-basis-level-grid-health", "BIM Basis / Levels & Grids review. status {0}; level findings {1}; grid findings {2}; links compared {3}.".format(level_grid.get("overall_status", "unknown"), level_grid.get("level_findings_count", 0), level_grid.get("grid_findings_count", 0), level_grid.get("links_compared_count", 0)))
        if any(flag in issues for flag in ["unloaded_or_unavailable_link", "non_identity_link_transform", "link_z_offset_detected", "rotated_link_detected", "duplicate_link_instances", "unknown_coordinate_state"]):
            add("check-linked-model-coordinate-health", "Review linked model coordinate health. {0}".format(self._coordinate_health_reason_summary(_get_linked_model_coordinate_health(context))))
        if "unloaded_or_unavailable_revit_links" in issues:
            add("scan-current-project", "Unloaded or unavailable Revit links were observed; use the context scan before coordination decisions.")
        if "cad_imports_present" in issues or "view_specific_imports_present" in issues:
            add("health-check-active-view-selection", "CAD/import or active-view coordination issues were observed; run a read-only active-view health check.")
        if context.get("warnings_summary", {}).get("total_warning_count", 0):
            add("scan-current-project", "Revit warnings were observed; review warning groups before production schedule/template actions.")
        if "empty_or_suspicious_schedules" in issues:
            add("scan-current-project", "Suspicious or empty schedule candidates were observed; keep inspection read-only before using schedule templates.")
        if "aco_bunge_templates_present" in issues:
            add("create-aco-pipe-fitting-summary-from-template", "ACO/Bunge template-like schedules were observed; schedule/template actions should come after read-only diagnostics.")
        for item in categories:
            if item.get("category") == "Pipes" and isinstance(item.get("total_count"), int) and item.get("total_count") > 0:
                add("create-pipe-schedule-by-level", "Pipes were found in the project context.")
                break
        for item in categories:
            if item.get("category") == "Pipe Fittings" and isinstance(item.get("total_count"), int) and item.get("total_count") > 0:
                add("create-pipe-fitting-schedule-by-level", "Pipe fittings were found in the project context.")
                break
        if selection.get("selected_element_count", 0):
            add("report-selected-elements-by-category", "An active-document selection is present.")
        else:
            add("health-check-active-view-selection", "No active-document selection is present; start with active-view read-only health checks.")
        if not recommendations:
            add("health-check-active-view-selection", "Use a read-only active-view health check as a first reviewed inspection.")
        deduped = []
        seen = set()
        for item in recommendations:
            if item.get("id") in seen:
                continue
            seen.add(item.get("id"))
            deduped.append(item)
        return deduped[:5]

    def build_project_agent_plan(self):
        context = self.get_latest_project_context(require_standard=True)
        guided_plan = build_guided_project_startup_plan(context)
        recommendations = []
        seen_action_keys = set()
        for step in guided_plan.get("steps", []):
            action_key = step.get("action_key")
            if not action_key or action_key in seen_action_keys:
                continue
            entry = self.catalog.get_entry_by_id(action_key)
            if not entry:
                continue
            item = dict(entry)
            item["recommendation_reason"] = step.get("reason", "")
            item["guided_phase"] = step.get("phase", "")
            item["guided_action_type"] = step.get("action_type", "")
            recommendations.append(item)
            seen_action_keys.add(action_key)
        self.agent_session.refresh_catalog(self.catalog.get_agent_commands())
        self.agent_session.reset()
        for entry in recommendations:
            self.agent_session._add_step(
                entry.get("id"),
                requested_prompt="project-context recommended plan",
                step_overrides={"recommendation_reason": entry.get("recommendation_reason", "")},
            )
        requires_modification = any(step.get("command_role") == "modify" for step in self.agent_session.plan)
        self.agent_session.plan_object = {
            "matched_action": "project-context-plan",
            "confidence": 0.7,
            "requires_modification": requires_modification,
            "destructive": False,
            "summary": "Project-context plan generated from the latest read-only scan. No actions have been executed.",
            "execution_ready": bool(self.agent_session.plan),
            "context_depth": context.get("scan_depth"),
            "context_timestamp": context.get("timestamp_utc"),
            "context_document_title": (context.get("document") or {}).get("title"),
            "guided_startup_plan": guided_plan,
        }
        self.agent_session.status = "ready_to_execute" if self.agent_session.plan else "failed"
        self.agent_session.message = "Ready to execute" if self.agent_session.plan else "No reviewed plan"
        return self.agent_session.plan_object

    def append_project_agent_plan(self):
        plan_object = self.build_project_agent_plan()
        context = self.get_latest_project_context(require_standard=True)
        doc_info = context.get("document", {})
        self.AgentHistory.AppendText("Project-context planner request\n")
        self.AgentHistory.AppendText("{0}\n".format(plan_object.get("summary")))
        self.AgentHistory.AppendText(
            "Context metadata: depth {0} | timestamp {1} | document {2}\n".format(
                context.get("scan_depth", "unknown"),
                context.get("timestamp_utc", "unknown"),
                doc_info.get("title", "(unknown document)"),
            )
        )
        guided_plan = plan_object.get("guided_startup_plan") or build_guided_project_startup_plan(context)
        formatted_guided_plan = format_guided_project_startup_plan(guided_plan)
        self.AgentHistory.AppendText("{0}\n".format(formatted_guided_plan))
        self.remember_latest_deterministic_report("Ask Agent for Plan", formatted_guided_plan)
        self.AgentHistory.AppendText("\nObserved context\n{0}\n".format(self.get_latest_project_context_summary(require_standard=True)))
        plan = self.agent_session.get_visible_steps()
        if plan:
            self.AgentHistory.AppendText("Recommended reviewed actions\n")
            for index, step in enumerate(plan):
                reason = step.get("recommendation_reason") or "Recommended from scanned project context."
                self.AgentHistory.AppendText(
                    "  {0}. {1} | risk: {2} | approval required: {3}\n     Reason: {4}\n".format(
                        index + 1,
                        step.get("title"),
                        step.get("risk", step.get("risk_level", "low")),
                        "yes" if step.get("command_role") == "modify" or step.get("requires_confirmation") else "no",
                        reason,
                    )
                )
            self.AgentHistory.AppendText("No actions have been executed. Execute Plan runs only enabled reviewed steps after approval.\n\n")
        else:
            self.AgentHistory.AppendText("No reviewed actions were recommended from the current scan.\n\n")
        self.populate_agent_command_selector()
        self.set_agent_status(self.agent_session.status)
        self.refresh_action_button_states()
        return plan_object

    def on_project_agent_plan(self, sender, args):
        try:
            self.append_project_agent_plan()
            try:
                self.MainTabs.SelectedIndex = 1
            except:
                pass
        except Exception as exc:
            self.ChatHistory.AppendText("Project-context agent plan failed: {0}\n\n".format(str(exc)))

    def build_codex_task_brief(self, observed_issue=None):
        context = self.get_latest_project_context(require_standard=True)
        doc_info = context.get("document", {})
        active_view_info = doc_info.get("active_view", {})
        schedules = context.get("schedules", {})
        schedule_samples = schedules.get("schedules", []) or []
        populated = len([item for item in schedule_samples if isinstance(item.get("body_row_count"), int) and item.get("body_row_count") > 0])
        empty = len([item for item in schedule_samples if item.get("body_row_count") == 0])
        unknown = len([item for item in schedule_samples if not isinstance(item.get("body_row_count"), int)])
        warnings = context.get("warnings_summary", {})
        warning_count = warnings.get("total_warning_count", "not scanned")
        coord_health = _get_linked_model_coordinate_health(context)
        level_grid_health = _get_host_link_level_grid_health(context)
        onboarding = build_project_onboarding_checklist(context)
        guided_plan = build_guided_project_startup_plan(context)
        doc_title = doc_info.get("title") or self.latest_project_context_doc_title or _document_title(doc)
        active_view = "(unknown view)"
        try:
            active_view = active_view_info.get("name") or _active_view_title(doc, uidoc)
        except:
            pass
        issue_text = observed_issue or "Runtime validation/export tooling for ModelMind actions."
        coord_counts = self._coordinate_health_counts(coord_health)
        flagged_links = [item for item in (coord_health.get("links", []) if isinstance(coord_health, dict) else []) if item.get("status_label") != "OK"]
        brief_lines = [
                "Codex Task Brief",
                "",
                "Current document: {0}".format(doc_title),
                "Active view: {0}".format(active_view),
                "Context depth: {0}".format(context.get("scan_depth", "unknown")),
                "Scan timestamp: {0}".format(context.get("timestamp_utc", "unknown")),
                "Schedule status: sampled {0}, populated {1}, empty {2}, unknown {3}".format(
                    schedules.get("sampled_count", len(schedule_samples)),
                    populated,
                    empty,
                    unknown,
                ),
                "Warning count: {0}".format(warning_count),
                "Linked model coordinate health:",
                "- Links scanned: {0}".format(coord_health.get("total_links", "not scanned")),
                "- OK: {0}".format(coord_counts.get("OK", 0)),
                "- Review: {0}".format(coord_counts.get("REVIEW", 0)),
                "- Unloaded/unavailable: {0}".format(coord_health.get("unavailable_count", "not scanned")),
                "- Unknown: {0}".format(coord_counts.get("UNKNOWN", 0)),
                "- Main flags: {0}".format(", ".join(self._coordinate_health_main_flags(coord_health))),
                "BIM Basis / Levels & Grids:",
                "- Status: {0}".format(level_grid_health.get("overall_status", "not scanned")),
                "- Links compared: {0}".format(level_grid_health.get("links_compared_count", "not scanned")),
                "- Level findings: {0}".format(level_grid_health.get("level_findings_count", "not scanned")),
                "- Grid findings: {0}".format(level_grid_health.get("grid_findings_count", "not scanned")),
                "- Main issue flags: {0}".format(", ".join(level_grid_health.get("issue_flags", []) or ["none"])),
                "Project onboarding:",
                "- Overall status: {0}".format(onboarding.get("overall_status", "UNKNOWN")),
                "- Automation readiness: {0}".format(onboarding.get("automation_readiness_status", "UNKNOWN")),
                "- Top recommended next actions: {0}".format(" | ".join(onboarding.get("recommended_next_actions", [])[:3]) or "none"),
                "- Blocking/review items: {0}".format(", ".join(onboarding.get("blocking_review_items", [])[:6]) or "none"),
                "Guided Agent startup plan:",
                "- Top phase 1 diagnostics: {0}".format(" | ".join([step.get("action_title") for step in (guided_plan.get("phase1") or [])[:3]]) or "none"),
                "- Blocked/review items: {0}".format(" | ".join([step.get("action_title") for step in (guided_plan.get("blocked") or [])[:5]]) or "none"),
                "- First reviewed automation candidate: {0}".format(next((step.get("action_title") for step in (guided_plan.get("phase3") or []) if step.get("action_key")), "none")),
                "- Execution-safe / plan-only: {0}".format("yes" if guided_plan.get("execution_safe") and guided_plan.get("plan_only") else "unknown"),
        ]
        if flagged_links:
            brief_lines.append("- Flagged links:")
            for item in flagged_links[:8]:
                brief_lines.append("  - {0}: {1}".format(item.get("display_name") or item.get("type_name") or item.get("instance_name"), "; ".join(self._coord_reason_list(item))))
        flagged_lg_links = [item for item in (level_grid_health.get("links_compared", []) if isinstance(level_grid_health, dict) else []) if item.get("status") != "OK"]
        if flagged_lg_links:
            brief_lines.append("- Top flagged level/grid links:")
            for item in flagged_lg_links[:8]:
                brief_lines.append("  - {0}: level findings {1}, grid findings {2}".format(item.get("link_name"), len(item.get("level_findings") or []), len(item.get("grid_findings") or [])))
        brief_lines.extend(
            [
                "",
                "Latest scan summary:",
                self.get_latest_project_context_summary(require_standard=True),
                "",
                "Observed issue:",
                issue_text,
                "",
                "Expected behavior:",
                "- Add read-only or validation/export tooling only.",
                "- Preserve reviewed ModelMind/catalog governance.",
                "- Do not execute generated free-form code.",
                "",
                "Forbidden changes:",
                "- window lifecycle",
                "- ExternalEvent / reviewed dispatch",
                "- modal/modeless behavior",
                "- create sheet / create 3D view / rename view paths",
                "- shared undo plumbing",
                "",
                "Likely files:",
                "- AI.extension/AI.tab/Dev.panel/AI_01.pushbutton/script.py",
                "- AI.extension/lib/prompt_catalog.json",
                "- AI.extension/lib/ai_prompt_registry.py",
                "",
                "Validation commands:",
                "- python -m tabnanny AI.extension/AI.tab/Dev.panel/AI_01.pushbutton/script.py",
                "- python -m py_compile AI.extension/lib/ai_prompt_registry.py",
                "- python -m py_compile AI.extension/lib/ai_agent_session.py",
                "- parse AI.extension/lib/prompt_catalog.json",
            ]
        )
        return "\n".join(brief_lines)

    def on_project_codex_brief(self, sender, args):
        brief = self.build_codex_task_brief()
        self.latest_codex_brief = brief
        self.populate_project_context_tree()
        self.append_chat_notice("CODEX TASK BRIEF", "```\n{0}\n```".format(brief))

    def on_project_context_quick_action(self, sender, args):
        prompt = ""
        try:
            prompt = safe_str(sender.Tag)
        except:
            prompt = ""
        if not prompt:
            try:
                prompt = safe_str(sender.Content)
            except:
                prompt = "summary"
        if prompt == "summary":
            prompt = "summarize current project"
        elif prompt == "onboarding":
            prompt = "project onboarding checklist"
        elif prompt == "empty schedules":
            prompt = "show empty schedules"
        elif prompt == "first checks":
            prompt = "what should I check first in this project"
        elif prompt == "levels grids":
            prompt = "check levels and grids"
        try:
            reply = self.answer_project_context_question(prompt)
            self.remember_latest_deterministic_report(prompt, reply)
            self.append_chat_turn(prompt, reply, "PROJECT CONTEXT")
        except Exception as exc:
            self.append_chat_notice("PROJECT CONTEXT QUERY FAILED", str(exc))

    def on_send_chat(self, sender, args):
        prompt = self.ChatInput.Text.strip()
        if not prompt:
            return

        self.update_window_status("executing", "Ollama Chat request")
        self._set_thinking("Thinking (Chat)...")
        self.show_busy()
        self._pump_ui()

        try:
            remember_report = False
            preserve_latest_report_state = False
            index_reply = self.answer_qa_export_index_question(prompt)
            if index_reply is not None:
                reply = index_reply
            else:
                split_verification_reply = self.answer_split_apply_verification_question(prompt)
                if split_verification_reply is not None:
                    reply = split_verification_reply
                    remember_report = True
                else:
                    reviewed_apply_reply = self.answer_split_reviewed_apply_question(prompt)
                    if reviewed_apply_reply is not None:
                        reply = reviewed_apply_reply
                        remember_report = True
                    else:
                        rollback_reply = self.answer_split_selected_pipes_rollback_test_question(prompt)
                        if rollback_reply is not None:
                            reply = rollback_reply
                            remember_report = True
                        else:
                            guard_reply = self.answer_reviewed_action_confirmation_guard_question(prompt)
                            if guard_reply is not None:
                                reply = guard_reply
                                remember_report = True
                            else:
                                split_dry_run_reply = self.answer_split_selected_pipes_dry_run_question(prompt)
                                if split_dry_run_reply is not None:
                                    reply = split_dry_run_reply
                                    remember_report = True
                                else:
                                    proposal_reply = self.answer_reviewed_action_proposal_question(prompt)
                                    if proposal_reply is not None:
                                        reply = proposal_reply
                                        remember_report = True
                                    else:
                                        if self._is_qa_export_request(prompt):
                                            reply = self.export_latest_qa_report(prompt)
                                            preserve_latest_report_state = True
                                        elif self._is_codex_brief_request(prompt):
                                            brief = self.build_codex_task_brief(prompt)
                                            self.latest_codex_brief = brief
                                            self.populate_project_context_tree()
                                            reply = "```\n{0}\n```".format(brief)
                                        elif self._is_scan_request(prompt):
                                            result = self.run_project_context_scan("standard")
                                            reply = result.get("summary", "")
                                            remember_report = True
                                        else:
                                            discipline_qa_reply = self.answer_discipline_qa_report_question(prompt)
                                            if discipline_qa_reply is not None:
                                                reply = discipline_qa_reply
                                                remember_report = True
                                            else:
                                                system_reply = self.answer_system_assignment_report_question(prompt)
                                                if system_reply is not None:
                                                    reply = system_reply
                                                    remember_report = True
                                                else:
                                                    active_view_reply = self.answer_active_view_report_question(prompt)
                                                    if active_view_reply is not None:
                                                        reply = active_view_reply
                                                        remember_report = True
                                                    else:
                                                        selection_reply = self.answer_selection_report_question(prompt)
                                                        if selection_reply is not None:
                                                            reply = selection_reply
                                                            remember_report = True
                                                        elif self._is_project_context_question(prompt):
                                                            reply = self.answer_project_context_question(prompt)
                                                            remember_report = True
                                                        elif "ask ai agent for a plan" in prompt.lower() or "agent plan" in prompt.lower():
                                                            self.append_project_agent_plan()
                                                            reply = "AI Agent project-context plan created. Review it in the AI Agent tab; no actions have been executed."
                                                            preserve_latest_report_state = True
                                                        else:
                                                            reply = send_ollama_chat(self.model, prompt)
                                                            reply = self._sanitize_ollama_context_error(reply)
                                                            self.latest_chat_output_is_deterministic_report = False
            if reply.startswith("Error:") and self.model != DEFAULT_MODEL:
                reply += " Runtime note: this may reflect local model/runtime instability rather than a broken feature. Switching back to phi3:mini is recommended."
            if remember_report:
                self.remember_latest_deterministic_report(prompt, reply)
            elif not preserve_latest_report_state:
                self.latest_chat_output_is_deterministic_report = False
            self.append_chat_turn(prompt, reply, "AI")
            self.ChatInput.Text = ""
        except Exception as e:
            self.append_chat_notice("CHAT ERROR", str(e))
        finally:
            self.hide_busy()
            self._set_thinking("Thinking...")
            self.update_window_status("idle")

    def on_modelmind_send(self, sender, args):
        prompt = self.ModelMindInput.Text.strip()
        if not prompt:
            return

        source_entry = self.resolve_prompt_entry(prompt)
        self.pending_ai_prompt = prompt
        self.pending_source_entry = source_entry
        self.remember_recent_prompt(prompt)
        self.populate_prompt_tree(self.get_catalog_filter_text())
        self.last_successful_reviewed_code = None
        self.last_reviewed_recipe_metadata = None
        self.reset_reviewed_code_state("draft", "Preparing request.")
        self.update_window_status("planning", "ModelMind request")
        self._set_thinking("Thinking (ModelMind)...")
        self.show_busy()
        self._pump_ui()

        try:
            self.ModelMindHistory.AppendText("You: {}\n".format(prompt))

            if source_entry and source_entry.get("source") == "approved_recipe":
                result = self.run_approved_recipe(source_entry)
                self.apply_undo_context_from_execution_result(result)
                self.ModelMindHistory.AppendText(
                    "Approved recipe executed: {0}\n{1}\n\n".format(
                        source_entry.get("title"), execution_result_message(result)
                    )
                )
                self.reset_reviewed_code_state("saved", "Approved recipe executed from reviewed store.")
                return

            if source_entry and source_entry.get("reviewed_steps"):
                preset_result = self.run_reviewed_preset(source_entry)
                self.ModelMindHistory.AppendText(
                    "Reviewed preset result\n{0}\n\n".format(preset_result)
                )
                self.reset_reviewed_code_state("draft", "Reviewed preset executed from shared registry.")
                self.update_window_status("ready_to_execute", "Reviewed preset complete")
                return

            if source_entry and source_entry.get("deterministic_handler"):
                if source_entry.get("deterministic_handler") == "create_sheet_reviewed_template":
                    reviewed_code = build_create_sheet_reviewed_code()
                    self.ModelMindHistory.AppendText(
                        "Reviewed code draft\nPrepared reviewed create-sheet action from the shared registry.\n"
                    )
                    validation = self.validate_and_prepare_reviewed_code(reviewed_code, prompt)
                    if validation.get("is_valid"):
                        self.ModelMindHistory.AppendText(
                            "Reviewed code validated\nUse Approve & Run Code to execute.\n"
                        )
                        self.update_window_status("ready_to_execute", "Reviewed create sheet action validated")
                    else:
                        self.ModelMindHistory.AppendText(
                            "Reviewed code blocked before approval.\n"
                        )
                        self.update_window_status("failed", "Reviewed create sheet action blocked")
                    return
                reviewed_result = execute_reviewed_action_handler(
                    source_entry.get("deterministic_handler"),
                    doc,
                    uidoc,
                    {
                        "requested_prompt": prompt,
                        "prompt_text": source_entry.get("canonical_prompt") or source_entry.get("prompt_text"),
                        "canonical_prompt": source_entry.get("canonical_prompt") or source_entry.get("prompt_text"),
                        "id": source_entry.get("id"),
                    },
                )
                if reviewed_result:
                    self.apply_undo_context_from_execution_result(reviewed_result)
                    self.ModelMindHistory.AppendText(
                        "Deterministic result\n{0}\n\n".format(
                            execution_result_message(reviewed_result)
                        )
                    )
                    self.reset_reviewed_code_state("draft", "Reviewed action returned from shared registry.")
                    self.update_window_status("ready_to_execute", "Deterministic result complete")
                    return

            public_cmd_result = handle_public_command(prompt, doc, uidoc)
            if public_cmd_result:
                self.ModelMindHistory.AppendText(
                    "Deterministic result\n{0}\n\n".format(public_cmd_result)
                )
                self.reset_reviewed_code_state("draft", "Deterministic ModelMind result returned.")
                self.update_window_status("ready_to_execute", "Deterministic result complete")
                return

            if "create sheet" in prompt.lower():
                reviewed_code = build_create_sheet_reviewed_code()
                self.ModelMindHistory.AppendText(
                    "Reviewed code draft\nPrepared pyRevit-safe create-sheet template.\n"
                )
                validation = self.validate_and_prepare_reviewed_code(reviewed_code, prompt)
                if validation.get("is_valid"):
                    self.ModelMindHistory.AppendText(
                        "Reviewed code validated\nUse Approve & Run Code to execute.\n"
                    )
                    self.update_window_status("ready_to_execute", "Reviewed create sheet template validated")
                else:
                    self.ModelMindHistory.AppendText(
                        "Reviewed code blocked before approval.\n"
                    )
                    self.update_window_status("failed", "Reviewed create sheet template blocked")
                return

            code_prompt = (
                "You are ModelMind, the primary BIM task surface for pyRevit users. "
                "Prefer deterministic, reviewable Revit API scripts. "
                "You are an expert Revit Python (IronPython) assistant. "
                "Given the following task, output ONLY a working Python script for Revit using the Revit API. "
                "Wrap your answer in triple backticks with 'python'. "
                "Assume 'doc' is the current Document, 'uidoc' is the ActiveUIDocument, and 'DB' is imported. "
                "Do not explain, only give code. "
                "Task: {}".format(prompt)
            )
            ai_reply = send_ollama_chat(self.model, code_prompt)
            code_blocks = extract_python_code(ai_reply)
            if code_blocks and len(code_blocks[0].strip()) > 0:
                self.ModelMindHistory.AppendText(
                    "Reviewed code draft\nA reviewed code draft was generated for this request.\n"
                )
                validation = self.validate_and_prepare_reviewed_code(code_blocks[0], prompt)
                if validation.get("is_valid"):
                    self.ModelMindHistory.AppendText(
                        "Reviewed code validated\nUse Approve & Run Code to execute.\n"
                    )
                    self.update_window_status("ready_to_execute", "Code review validated")
                else:
                    self.ModelMindHistory.AppendText(
                        "Approval remains disabled because the reviewed code is not pyRevit-compatible.\n"
                    )
                    self.update_window_status("failed", "Reviewed code blocked")
            else:
                self.reset_reviewed_code_state("draft", "No reviewed code available.")
                self.ModelMindHistory.AppendText(
                    "No code block detected in AI reply.\n"
                )
                self.update_window_status("failed", "No executable code returned")

        except Exception as e:
            self.ModelMindHistory.AppendText("Error: {}\n".format(str(e)))
            self.update_window_status("failed", str(e))
        finally:
            self.hide_busy()
            self._set_thinking("Thinking...")

    def on_approve_code(self, sender, args):
        if not self.pending_ai_code or len(self.pending_ai_code.strip()) == 0:
            self.ModelMindHistory.AppendText("No code to run.\n")
            return
        self.update_window_status("executing", "Running reviewed ModelMind code")
        self._set_thinking("Running code...")
        self.show_busy()
        self._pump_ui()
        try:
            validation = self.validate_and_prepare_reviewed_code(
                self.pending_ai_code, self.pending_ai_prompt
            )
            if not validation.get("is_valid"):
                self.ModelMindHistory.AppendText(
                    "Approval blocked before execution. Unsupported items: {0}\n".format(
                        ", ".join(validation.get("blocked_hits") or validation.get("errors"))
                    )
                )
                self.update_window_status("failed", "Reviewed code blocked before execution")
                return
            sanitized_code = validation.get("sanitized_code")
            result = run_code_in_revit(sanitized_code, doc, uidoc)
            self.apply_undo_context_from_execution_result(result)
            result_message = execution_result_message(result)
            self.ModelMindHistory.AppendText("Reviewed code executed\n{0}\n".format(result_message))
            result_text = result_message.lower()
            if result_text.startswith("code executed successfully") or result_text.startswith("created sheet:"):
                self.last_successful_reviewed_code = sanitized_code
                self.last_reviewed_recipe_metadata = self.get_default_recipe_metadata()
                self.SaveRecipeButton.IsEnabled = True
                self.update_reviewed_code_state("executed", "Reviewed code executed successfully.")
                self.update_window_status("ready_to_execute", "Reviewed code executed")
                self.refresh_action_button_states()
            else:
                self.last_successful_reviewed_code = None
                self.last_reviewed_recipe_metadata = None
                self.SaveRecipeButton.IsEnabled = False
                self.update_reviewed_code_state("blocked", "Execution failed; recipe save remains disabled.")
                self.update_window_status("failed", result_message)
                self.refresh_action_button_states()
        except Exception as e:
            self.ModelMindHistory.AppendText("AI code error: {}\n".format(str(e)))
            self.last_successful_reviewed_code = None
            self.last_reviewed_recipe_metadata = None
            self.SaveRecipeButton.IsEnabled = False
            self.update_reviewed_code_state("blocked", str(e))
            self.update_window_status("failed", str(e))
            self.refresh_action_button_states()
        finally:
            self.pending_ai_code = None
            self.pending_validated_code = None
            self.ApproveCodeButton.IsEnabled = False
            self.refresh_action_button_states()
            self.hide_busy()
            self._set_thinking("Thinking...")

    def on_upgrade_model(self, sender, args):
        self.ModelInfo.Text = "Upgrading model: {} ...".format(self.model)
        if pull_ollama_model(self.model):
            self.ModelInfo.Text = "Model {} upgraded.".format(self.model)
        else:
            self.ModelInfo.Text = "Failed to upgrade model: {}".format(self.model)

    def on_save_recipe(self, sender, args):
        if not self.last_successful_reviewed_code:
            self.ModelMindHistory.AppendText(
                "Save as Approved Recipe is available only after a successful reviewed-code run.\n"
            )
            return

        dialog = ApprovedRecipeMetadataDialog(
            self.last_reviewed_recipe_metadata or self.get_default_recipe_metadata()
        )
        if dialog.ShowDialog() != WinForms.DialogResult.OK or not dialog.metadata:
            self.ModelMindHistory.AppendText("Approved recipe save cancelled.\n")
            return

        try:
            recipe = self.catalog.save_approved_recipe(
                dialog.metadata,
                self.last_successful_reviewed_code,
                source_entry=self.pending_source_entry,
            )
            self.ModelMindHistory.AppendText(
                "Approved recipe saved\n{0} ({1})\n".format(
                    recipe.get("title"), recipe.get("id")
                )
            )
            self.populate_prompt_tree(self.get_catalog_filter_text())
            self.SaveRecipeButton.IsEnabled = False
            self.last_successful_reviewed_code = None
            self.last_reviewed_recipe_metadata = None
            self.update_reviewed_code_state("saved", "Approved recipe stored and tree reloaded.")
            self.refresh_action_button_states()
        except Exception as exc:
            self.ModelMindHistory.AppendText(
                "Failed to save approved recipe: {}\n".format(str(exc))
            )

    def resolve_prompt_entry(self, prompt):
        target = (prompt or "").strip().lower()
        if self.selected_prompt_entry:
            current_prompt = (
                self.selected_prompt_entry.get("prompt_text")
                or self.selected_prompt_entry.get("title")
                or ""
            ).strip().lower()
            if current_prompt == target:
                return self.selected_prompt_entry

        entry = self.catalog.get_entry_by_prompt(prompt)
        if entry:
            return entry

        for approved in self.catalog.get_approved_entries():
            prompt_text = (approved.get("prompt_text") or "").strip().lower()
            title = (approved.get("title") or "").strip().lower()
            if prompt_text == target or title == target:
                return approved
        return None

    def run_reviewed_preset(self, entry):
        preset_title = entry.get("title", "Reviewed preset")
        step_defs = list(entry.get("reviewed_steps") or [])
        if not step_defs:
            return "{0} has no reviewed steps.".format(preset_title)
        original_selection = _snapshot_selection_ids(uidoc)
        lines = [
            "{0}".format(preset_title),
            "Active document: {0}".format(_document_title(doc)),
            "Active view: {0}".format(_active_view_title(doc, uidoc)),
            "Reviewed steps: {0}".format(len(step_defs)),
        ]
        try:
            for index, step_def in enumerate(step_defs):
                if isinstance(step_def, dict):
                    step_id = step_def.get("action_id")
                    scope_behavior = step_def.get("scope_behavior", "use_active_view")
                else:
                    step_id = step_def
                    scope_behavior = "use_active_view"
                    step_def = {}
                step_entry = self.catalog.get_entry_by_id(step_id)
                if not step_entry:
                    lines.append("{0}. Missing reviewed step: {1}".format(index + 1, step_id))
                    continue
                handler_name = step_entry.get("deterministic_handler")
                if not handler_name:
                    lines.append(
                        "{0}. {1}\nNo deterministic handler is registered for this reviewed step.".format(
                            index + 1,
                            step_entry.get("title", step_id),
                        )
                    )
                    continue

                selection_before_step = _snapshot_selection_ids(uidoc)
                active_selection = _snapshot_selection_ids(uidoc)
                if step_def.get("skip_if_selection_empty") and not active_selection:
                    lines.append(
                        "{0}. {1}\n{2}".format(
                            index + 1,
                            step_entry.get("title", step_id),
                            step_def.get(
                                "skip_message",
                                "Skipped because there is no active-document selection.",
                            ),
                        )
                    )
                    continue

                scope_note = "Preset scope: active view"
                if scope_behavior == "use_current_selection":
                    scope_note = "Preset scope: current active-document selection"
                elif scope_behavior == "use_generated_selection":
                    generated_ids = _collect_scope_element_ids(doc, uidoc, step_def.get("generated_selection"))
                    _restore_selection_ids(uidoc, generated_ids)
                    scope_note = "Preset scope: generated working selection ({0} element(s))".format(len(generated_ids))
                elif scope_behavior == "use_active_view":
                    scope_note = "Preset scope: active view in active document"

                result = execute_reviewed_action_handler(
                    handler_name,
                    doc,
                    uidoc,
                    {
                        "requested_prompt": self.pending_ai_prompt or entry.get("canonical_prompt") or entry.get("prompt_text"),
                        "prompt_text": step_entry.get("canonical_prompt") or step_entry.get("prompt_text"),
                        "canonical_prompt": step_entry.get("canonical_prompt") or step_entry.get("prompt_text"),
                        "id": step_entry.get("id"),
                    },
                )
                self.apply_undo_context_from_execution_result(result)
                lines.append(
                    "{0}. {1}\n{2}\n{3}".format(
                        index + 1,
                        step_entry.get("title", step_id),
                        scope_note,
                        execution_result_message(result),
                    )
                )
                if step_def.get("restore_previous_selection_after_step", False):
                    _restore_selection_ids(uidoc, selection_before_step)
        finally:
            _restore_selection_ids(uidoc, original_selection)
        return "\n\n".join(lines)

    def run_approved_recipe(self, entry):
        code_text = entry.get("stored_code")
        if not code_text:
            return "Approved recipe has no stored code."
        validation = validate_reviewed_code(llm_output_safety_filter(sanitize_llm_code(code_text)))
        if not validation.get("is_valid"):
            return "Approved recipe blocked because it contains unsupported items: {0}".format(
                ", ".join(validation.get("blocked_hits") or validation.get("errors"))
            )
        reviewed_code = validation.get("sanitized_code") if "sanitized_code" in validation else llm_output_safety_filter(sanitize_llm_code(code_text))
        return run_code_in_revit(reviewed_code, doc, uidoc)

    def update_agent_warning(self):
        destructive_enabled = bool(self.AgentAllowDestructive.IsChecked)
        self.agent_session.set_allow_destructive(destructive_enabled)
        warning = (
            "Warning: destructive tools are enabled for this session and may modify or delete model data."
            if destructive_enabled
            else "Destructive tools are OFF by default. Review plans first; modifying commands remain blocked."
        )
        try:
            self.AgentWarningText.Text = warning
        except:
            pass
        self.refresh_agent_step_state()
        self.refresh_action_button_states()

    def on_agent_destructive_toggled(self, sender, args):
        self.update_agent_warning()

    def _describe_agent_step(self, step):
        role_label = "Read-only" if step.get("role") == "read_only" else "Modifying"
        enabled_label = "Enabled" if step.get("enabled", True) else "Disabled"
        return "[{0}] {1} | risk: {2} | {3}".format(
            role_label, step.get("title"), step.get("risk", step.get("risk_level", "low")), enabled_label
        )

    def refresh_agent_supported_actions_ui(self, matched_action_id=None):
        commands = self.catalog.get_agent_commands()
        summary = "Shared reviewed actions available: {0}".format(len(commands))
        if matched_action_id:
            matched = None
            for command in commands:
                if command.get("id") == matched_action_id:
                    matched = command
                    break
            if matched:
                summary = "{0} | Matched action: {1}".format(summary, matched.get("title"))
        try:
            self.AgentCommandLegend.Text = summary
        except:
            pass

    def get_selected_agent_step(self):
        selected = self.AgentCommandSelector.SelectedItem
        if selected is None or not hasattr(selected, "Tag"):
            return None
        tag = selected.Tag
        if not isinstance(tag, dict) or tag.get("kind") != "plan_step":
            return None
        return tag.get("payload")

    def _step_requires_destructive(self, step):
        return bool(step and step.get("role") == "modifying")

    def _get_execute_plan_status(self):
        if not self.agent_session.has_plan():
            return (False, "No current reviewed plan. Plan Request creates the current plan.")
        if not self.agent_session.has_enabled_steps():
            return (False, "All current plan steps are disabled.")
        if not self.agent_session.has_runnable_steps():
            return (False, "Current enabled steps are blocked by destructive-tools gating.")
        return (True, "Execute Plan is available for the current enabled reviewed steps.")

    def refresh_agent_step_state(self):
        step = self.get_selected_agent_step()
        execute_available, execute_reason = self._get_execute_plan_status()
        if not step:
            message = "No current reviewed plan. Plan Request creates the current reviewed plan steps."
            if self.agent_session.has_plan():
                message = execute_reason
            try:
                self.AgentStepStateText.Text = message
                self.AgentStepStateText.ToolTip = message
            except:
                pass
            return

        role_label = "Read-only" if step.get("role") == "read_only" else "Modifying"
        enabled_label = "Enabled" if step.get("enabled", True) else "Disabled"
        blocked_reason = step.get("blocked_reason") or "None"
        if self._step_requires_destructive(step) and not bool(self.AgentAllowDestructive.IsChecked):
            blocked_reason = "Blocked by destructive-tools gate."
        execute_label = "available" if execute_available else "not available"
        message = (
            "Selected plan step: {0} | {1} | risk: {2} | {3} | executed: {4} | blocked: {5} | Execute Plan: {6}".format(
                step.get("title", "(untitled step)"),
                role_label,
                step.get("risk", step.get("risk_level", "low")),
                enabled_label,
                "yes" if step.get("executed") else "no",
                blocked_reason,
                execute_label,
            )
        )
        try:
            self.AgentStepStateText.Text = message
            self.AgentStepStateText.ToolTip = execute_reason
        except:
            pass

    def refresh_agent_undo_status(self):
        undo_context = self.agent_session.get_undo_context()
        if undo_context and undo_context.get("undo_available"):
            message = "Undo available: {0}".format(
                undo_context.get("action_title", "Reversible action")
            )
        else:
            message = "Undo unavailable: no reversible action in current session."
        try:
            self.AgentUndoStatusText.Text = message
            self.AgentUndoStatusText.ToolTip = message
        except:
            pass

    def apply_undo_context_from_execution_result(self, execution_result):
        if isinstance(execution_result, dict):
            undo_context = execution_result.get("undo_context")
            if undo_context:
                self.agent_session.set_undo_context(undo_context)
                self.refresh_agent_undo_status()
                self.refresh_action_button_states()
                return True
        return False

    def on_agent_step_selected(self, sender, args):
        self.refresh_agent_step_state()
        self.refresh_agent_undo_status()
        self.refresh_action_button_states()

    def populate_agent_command_selector(self):
        from System.Windows.Controls import ComboBoxItem

        self.AgentCommandSelector.Items.Clear()
        steps = self.agent_session.get_visible_steps()
        if steps:
            for step in steps:
                item = ComboBoxItem()
                item.Content = self._describe_agent_step(step)
                item.Tag = {"kind": "plan_step", "payload": step}
                self.AgentCommandSelector.Items.Add(item)
        else:
            item = ComboBoxItem()
            item.Content = "(No current reviewed plan steps)"
            item.Tag = {"kind": "placeholder", "payload": None}
            self.AgentCommandSelector.Items.Add(item)
        if self.AgentCommandSelector.Items.Count > 0:
            self.AgentCommandSelector.SelectedIndex = 0
        self.refresh_agent_supported_actions_ui(self.current_matched_action_id)
        self.refresh_agent_step_state()
        self.refresh_agent_undo_status()
        self.refresh_action_button_states()

    def on_agent_run(self, sender, args):
        goal = self.AgentGoalInput.Text.strip()
        if not goal:
            return

        if "project" in goal.lower() and ("plan" in goal.lower() or "check first" in goal.lower()):
            self.update_window_status("planning", "Project-context planner request")
            self.append_project_agent_plan()
            self.update_window_status("ready_to_execute", "Project-context plan ready")
            return

        if self._is_cloud_self_test_request(goal):
            self.AgentHistory.AppendText("Planner request\n{0}\n".format(goal))
            self.run_cloud_planner_self_test()
            self.AgentExecuteButton.IsEnabled = False
            self.populate_agent_command_selector()
            self.refresh_action_button_states()
            return

        self.agent_session.refresh_catalog(self.catalog.get_agent_commands())
        self.set_agent_status("planning")
        self.update_window_status("planning", "Planner request")
        self.AgentHistory.AppendText("Planner request\n{0}\n".format(goal))
        mode = self.get_selected_planner_mode()
        plan_object = None

        if mode == "cloud" and self.cloud_provider_state.get("state") == "provider_ready":
            cloud_result = self._build_cloud_plan(goal)
            if cloud_result.get("provider_state") != "provider_ready":
                self.cloud_provider_state = {
                    "available": False,
                    "state": cloud_result.get("provider_state", "request_failed"),
                    "key_state": "key_present",
                    "key_present": True,
                    "provider_reachable": bool(cloud_result.get("provider_reachable", False)),
                    "last_error_category": cloud_result.get("last_error_category", cloud_result.get("provider_state", "request_failed")),
                    "message": cloud_result.get("message", cloud_result.get("error", "Cloud planner request failed.")),
                    "detail": cloud_result.get("detail", cloud_result.get("error", "")),
                }
                self.update_planner_provider_ui(
                    self.cloud_provider_state.get("state"),
                    self.cloud_provider_state.get("message"),
                )
                self.AgentHistory.AppendText(
                    "{0}\nFalling back to local deterministic planning.\n\n".format(
                        self.cloud_provider_state.get("message", "Cloud planner request failed.")
                    )
                )
                plan_object = self._build_local_plan(goal)
            else:
                self.cloud_provider_state = dict(get_openai_provider_state())
                self.update_planner_provider_ui("provider_ready", self.cloud_provider_state.get("message"))
                plan_object = cloud_result.get("plan")
        else:
            if mode == "cloud" and self.cloud_provider_state.get("state") != "provider_ready":
                self.AgentHistory.AppendText(
                    "{0}\nUsing local deterministic planning instead.\n\n".format(
                        self.cloud_provider_state.get("message", "Cloud planner unavailable.")
                    )
                )
            self.update_planner_provider_ui(
                self.cloud_provider_state.get("state", "local")
                if mode == "cloud"
                else "local",
                self.cloud_provider_state.get("message"),
            )
            plan_object = self._build_local_plan(goal)

        plan = self.agent_session.get_visible_steps()
        self.current_matched_action_id = ""
        if plan_object:
            self.current_matched_action_id = plan_object.get("matched_action", "") or ""
        self._append_plan_object(plan_object)
        if not plan_object or not bool(plan_object.get("execution_ready", False)) or not plan:
            self.AgentHistory.AppendText(
                "No reviewed executable plan is available.\n{0}\n\n".format(
                    plan_object.get("summary", self.agent_session.guidance) if plan_object else self.agent_session.guidance
                )
            )
            self.AgentExecuteButton.IsEnabled = False
            self.populate_agent_command_selector()
            self.set_agent_status(self.agent_session.status)
            self.refresh_action_button_states()
            return

        self.AgentHistory.AppendText("Plan ready for review\n")
        for index, step in enumerate(plan):
            self.AgentHistory.AppendText(
                "  {0}. {1}\n".format(index + 1, self._describe_agent_step(step))
            )
        self.AgentHistory.AppendText(
            "Execute Plan runs only the reviewed enabled steps.\n\n"
        )
        self.AgentExecuteButton.IsEnabled = bool(plan_object.get("execution_ready", False))
        self.populate_agent_command_selector()
        self.set_agent_status(self.agent_session.status)
        self.refresh_action_button_states()

    def _execute_agent_step(self, step):
        selection_before_step = _snapshot_selection_ids(uidoc)
        if step.get("skip_if_selection_empty") and not selection_before_step:
            return step.get(
                "skip_message",
                "Skipped because there is no active-document selection.",
            )
        if step.get("scope_behavior") == "use_generated_selection":
            generated_ids = _collect_scope_element_ids(doc, uidoc, step.get("generated_selection"))
            _restore_selection_ids(uidoc, generated_ids)
        try:
            if step.get("deterministic_handler") == "create_sheet_reviewed_template" or step.get("id") == "create-sheet-reviewed-template":
                validation = self.validate_and_prepare_reviewed_code(
                    build_create_sheet_reviewed_code(),
                    "create sheet",
                )
                if not validation.get("is_valid"):
                    return "Reviewed create-sheet template is blocked."
                return run_code_in_revit(validation.get("sanitized_code"), doc, uidoc)
            if step.get("deterministic_handler") == "create_3d_view_from_selection" or step.get("id") == "create-3d-view-from-selection":
                return execute_create_3d_view_with_undo(doc, uidoc)
            if step.get("deterministic_handler"):
                result = execute_reviewed_action_handler(step.get("deterministic_handler"), doc, uidoc, step)
                if result is not None:
                    return result
            result = handle_public_command(step.get("prompt_text", ""), doc, uidoc)
            if result is None:
                return "No deterministic executor is available for this step."
            return result
        finally:
            if step.get("restore_previous_selection_after_step", False):
                _restore_selection_ids(uidoc, selection_before_step)

    def _planner_supported_actions(self):
        return self.agent_session.get_supported_actions()

    def _build_local_plan(self, goal):
        self.agent_session.plan_goal(goal)
        return self.agent_session.plan_object

    def _build_cloud_plan(self, goal):
        response = normalize_intent_to_supported_action(
            goal,
            self._planner_supported_actions(),
            model_name=self.planner_model,
        )
        if not response.get("ok"):
            return {
                "provider_state": response.get("state", "request_failed"),
                "message": response.get("message", response.get("error", "Cloud planner request failed.")),
                "error": response.get("error", "Cloud planner request failed."),
                "provider_reachable": bool(response.get("provider_reachable", False)),
                "last_error_category": response.get("last_error_category", "request_failed"),
                "detail": response.get("detail", ""),
            }

        result = response.get("result") or {}
        action_id = result.get("matched_action", "")
        if not action_id or result.get("rejected"):
            self.agent_session.reset()
            self.agent_session.plan_object = {
                "matched_action": "",
                "confidence": float(result.get("confidence", 0.0) or 0.0),
                "requires_modification": False,
                "destructive": False,
                "summary": result.get("summary", self.agent_session._build_unsupported_summary(goal)),
                "execution_ready": False,
            }
            self.agent_session.status = "failed"
            self.agent_session.message = "Unsupported request"
            self.agent_session.guidance = self.agent_session.plan_object["summary"]
            return {"provider_state": "provider_ready", "plan": self.agent_session.plan_object}

        plan = self.agent_session.build_plan_from_action(
            action_id,
            result.get("confidence", 0.0),
            result.get("summary", "OpenAI planner matched a supported action."),
            requested_prompt=goal,
        )
        return {"provider_state": "provider_ready", "plan": plan}

    def _append_plan_object(self, plan_object):
        if not plan_object:
            return
        self.AgentHistory.AppendText(
            "Plan object\nmatched_action: {0}\nconfidence: {1:.2f}\nrequires_modification: {2}\ndestructive: {3}\nsummary: {4}\nexecution_ready: {5}\n".format(
                plan_object.get("matched_action", ""),
                float(plan_object.get("confidence", 0.0) or 0.0),
                bool(plan_object.get("requires_modification", False)),
                bool(plan_object.get("destructive", False)),
                plan_object.get("summary", ""),
                bool(plan_object.get("execution_ready", False)),
            )
        )

    def on_agent_execute(self, sender, args):
        if not self.agent_session.get_visible_steps():
            return

        self.update_window_status("executing", "AI Agent execution")
        self.set_agent_status("executing")
        results = self.agent_session.execute(self._execute_agent_step)
        for result in results:
            step = result.get("step", {})
            self.AgentHistory.AppendText(
                "{0}: {1}\n{2}\n\n".format(
                    result.get("status", "unknown").upper(),
                    step.get("title", step.get("prompt_text", "(unknown step)")),
                    result.get("message", ""),
                )
            )
        self.AgentExecuteButton.IsEnabled = False
        self.populate_agent_command_selector()
        self.set_agent_status(self.agent_session.status)
        self.refresh_agent_undo_status()
        self.refresh_action_button_states()

    def on_agent_toggle_command(self, sender, args):
        selected = self.AgentCommandSelector.SelectedItem
        if selected is None or not hasattr(selected, "Tag"):
            return
        tag = selected.Tag
        if not isinstance(tag, dict) or tag.get("kind") != "plan_step":
            return
        step = tag.get("payload") or {}
        updated = self.agent_session.toggle_command(step.get("id"))
        if updated:
            self.AgentHistory.AppendText(
                "Toggled command: {}\n\n".format(self._describe_agent_step(updated))
            )
            self.populate_agent_command_selector()
            self.refresh_action_button_states()

    def on_agent_reset_commands(self, sender, args):
        self.agent_session.reset()
        self.current_matched_action_id = ""
        self.AgentExecuteButton.IsEnabled = False
        self.populate_agent_command_selector()
        self.AgentHistory.AppendText("Planner state cleared.\n\n")
        self.set_agent_status(self.agent_session.status)
        self.refresh_agent_undo_status()
        self.refresh_action_button_states()

    def _run_shared_undo(self):
        undo_context = self.agent_session.get_undo_context()
        if not undo_context:
            return {"ok": False, "message": "Undo unavailable: no undo context recorded."}

        action_id = undo_context.get("action_id")
        if action_id == "create-3d-view-from-selection":
            result = undo_create_3d_view_action(doc, undo_context)
        elif action_id == "create-sheet-reviewed-template":
            result = undo_create_sheet_action(doc, undo_context)
        elif action_id == "rename-active-view":
            result = undo_rename_active_view_action(doc, undo_context)
        else:
            result = {
                "ok": False,
                "message": "Undo unavailable: last action is not a supported reversible reviewed action.",
            }
        if result.get("ok"):
            self.agent_session.clear_undo_context()
            self.update_window_status("idle", "Undo completed")
        else:
            self.update_window_status("failed", "Undo failed")
        return result

    def on_agent_undo(self, sender, args):
        result = self._run_shared_undo()
        self.AgentHistory.AppendText("{0}\n\n".format(result.get("message", "")))
        self.refresh_agent_undo_status()
        self.populate_agent_command_selector()
        self.refresh_action_button_states()

    def on_modelmind_undo(self, sender, args):
        result = self._run_shared_undo()
        self.ModelMindHistory.AppendText("Undo result\n{0}\n\n".format(result.get("message", "")))
        self.refresh_agent_undo_status()
        self.populate_agent_command_selector()
        self.refresh_action_button_states()

    def _on_header_drag(self, sender, e):
        import System.Windows.Input as wpfInput

        src = e.OriginalSource
        try:
            tname = src.GetType().Name if hasattr(src, "GetType") else ""
        except:
            tname = ""
        if tname in (
            "Button",
            "TextBox",
            "ComboBox",
            "ListBox",
            "ListBoxItem",
            "RichTextBox",
        ):
            return

        if e.LeftButton == wpfInput.MouseButtonState.Pressed:
            try:
                self.DragMove()
                e.Handled = True
            except:
                pass

    def _pump_ui(self):
        self.Dispatcher.Invoke(
            System.Windows.Threading.DispatcherPriority.Background, Action(do_nothing)
        )

    def on_close(self, sender, args):
        self.Close()


# print("About to run main() in", __file__)
# === Script Entry Point ===


def main():
    # 1. Check Ollama presence
    if not ollama_is_installed():
        forms.alert(
            "Ollama is not installed. Please install Ollama first:\nhttps://ollama.com/download",
            exitscript=True,
        )

    # 2. Check if model installed, if not, offer to pull
    if not ollama_model_installed(DEFAULT_MODEL):
        if not pull_ollama_model(DEFAULT_MODEL):
            forms.alert(
                "Unable to install {}. Exiting.".format(DEFAULT_MODEL), exitscript=True
            )

    # 3. Show chat UI
    win = OllamaAIChat(XAML_PATH)
    win.ShowDialog()


if __name__ == "__main__":
    main()

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


def _detect_project_issues(context):
    issues = []
    levels = context.get("levels", {})
    if levels.get("ambiguous_aliases"):
        issues.append({"id": "ambiguous_level_names", "severity": "medium", "message": "Ambiguous level aliases detected."})
    links = context.get("links", {})
    if links.get("unavailable_links"):
        issues.append({"id": "unloaded_or_unavailable_revit_links", "severity": "medium", "message": "One or more Revit links appear unloaded or unavailable."})
    imports = context.get("imports_cad", {})
    coord_health = context.get("linked_model_coordinate_health", {})
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
    coord_health = context.get("linked_model_coordinate_health", {})
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


def _summarize_project_context(context):
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
    summary = result.get("summary", "")
    return "\n".join(
        [
            "Project-context planning input prepared.",
            summary,
            "Use AI Agent Ask Agent for Plan to create a reviewed plan from this context.",
            "No actions have been executed.",
        ]
    )


def check_linked_model_coordinate_health(doc, uidoc, context=None):
    result = collect_project_context(doc, uidoc, "standard")
    health = result.get("context", {}).get("linked_model_coordinate_health", {})
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


def report_selected_elements_by_category(doc, uidoc):
    elems = _selected_elements(doc, uidoc)
    lines = [
        "Selected elements by category",
        "Selection scope: active document only",
        "Active document: {0}".format(_document_title(doc)),
        "Active view: {0}".format(_active_view_title(doc, uidoc)),
        "Current selection count: {0}".format(len(elems)),
        "Total selected elements: {0}".format(len(elems)),
    ]
    grouped = {}
    try:
        for elem in elems:
            if not elem:
                continue
            category_name = _category_name(elem)
            grouped.setdefault(category_name, []).append(elem)
    except Exception as err:
        lines.append("Unable to group selected elements by category.")
        lines.append("Diagnostic: {0}".format(safe_str(err)))
        return "\n".join(lines)
    if not grouped:
        lines.append("No selected elements found in the active Revit document.")
        lines.append("Selections in other open Revit projects are not included.")
        return "\n".join(lines)
    ordered = sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0]))
    for index, (category_name, items) in enumerate(ordered[:20]):
        lines.append(
            "{0}: {1} | sample ids: {2}".format(
                category_name,
                len(items),
                _sample_id_text(items),
            )
        )
    if len(ordered) > 20:
        lines.append("...showing first 20 of {0} category groups".format(len(ordered)))
    return "\n".join(lines)


def report_selected_elements_by_type(doc, uidoc):
    elems = _selected_elements(doc, uidoc)
    grouped = {}
    for elem in elems:
        if not elem:
            continue
        type_name = _family_and_type_text(doc, elem) or "(no type)"
        grouped.setdefault(type_name, []).append(elem)
    lines = [
        "Selected elements by type",
        "Selection scope: active document only",
        "Active document: {0}".format(_document_title(doc)),
        "Active view: {0}".format(_active_view_title(doc, uidoc)),
        "Current selection count: {0}".format(len(elems)),
        "Total selected elements: {0}".format(len(elems)),
    ]
    if not grouped:
        lines.append("No selected elements found in the active Revit document.")
        lines.append("Selections in other open Revit projects are not included.")
        return "\n".join(lines)
    ordered = sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0]))
    for type_name, items in ordered[:20]:
        lines.append(
            "{0}: {1} | sample ids: {2}".format(
                type_name,
                len(items),
                _sample_id_text(items),
            )
        )
    if len(ordered) > 20:
        lines.append("...showing first 20 of {0} type groups".format(len(ordered)))
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
        "Missing key parameters from selection",
        "Selection scope: active document only",
        "Active document: {0}".format(_document_title(doc)),
        "Active view: {0}".format(_active_view_title(doc, uidoc)),
        "Current selection count: {0}".format(len(elems)),
        "Total selected elements: {0}".format(len(elems)),
    ]
    if not elems:
        lines.append("No selected elements found in the active Revit document.")
        lines.append("Selections in other open Revit projects are not included.")
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

    findings = []
    for label, resolver in checks:
        applicable = []
        missing = []
        for elem in elems:
            try:
                value = resolver(elem)
            except:
                value = "__not_applicable__"
            if value == "__not_applicable__":
                continue
            applicable.append(elem)
            if not value:
                missing.append(elem)
        if not applicable:
            continue
        findings.append((label, applicable, missing))

    if not findings:
        lines.append("No reviewed parameter checks were applicable to the current selection.")
        return "\n".join(lines)

    missing_any = False
    for label, applicable, missing in findings:
        lines.append(
            "{0}: missing on {1} of {2} applicable element(s)".format(
                label, len(missing), len(applicable)
            )
        )
        if missing:
            missing_any = True
            lines.append("  sample ids: {0}".format(_sample_id_text(missing)))
    if not missing_any:
        lines.append("Nothing is missing from the reviewed baseline parameter set for the current selection.")
    return "\n".join(lines)


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
    "report_selected_elements_by_category": report_selected_elements_by_category,
    "report_selected_elements_by_type": report_selected_elements_by_type,
    "health_check_for_active_view_selection": health_check_for_active_view_selection,
    "report_missing_parameters_from_selection": report_missing_parameters_from_selection,
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
    "ask_agent_for_project_plan_action": ask_agent_for_project_plan_action,
    "create_codex_task_brief_action": create_codex_task_brief_action,
}


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
        return count_pipes_in_active_view(doc, uidoc)
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
    if "report selected elements by category" in p:
        return report_selected_elements_by_category(doc, uidoc)
    if "report selected elements by type" in p:
        return report_selected_elements_by_type(doc, uidoc)
    if "health check for active view selection" in p or "selection health check" in p:
        return health_check_for_active_view_selection(doc, uidoc)
    if "report missing parameters from selection" in p or "missing parameters from selection" in p:
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
    if "count all ducts in active view" in p:
        return count_ducts_in_active_view(doc, uidoc)
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
            "ProjectQuickLevelsButton",
            "ProjectQuickViewsButton",
            "ProjectQuickSheetsButton",
            "ProjectQuickLinksButton",
            "ProjectQuickLinkCoordinatesButton",
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
            "ProjectQuickLevelsButton",
            "ProjectQuickViewsButton",
            "ProjectQuickSheetsButton",
            "ProjectQuickLinksButton",
            "ProjectQuickLinkCoordinatesButton",
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
        coord_health = context.get("linked_model_coordinate_health", {})
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
            flags = item.get("coordinate_flags") or []
            origin = item.get("origin_internal_feet") or {}
            origin_display = item.get("origin_display") or {}
            coord_children.append(
                self._context_tree_item(
                    "{0} [{1}]".format(item.get("display_name") or item.get("type_name") or item.get("instance_name"), item.get("status_label", "UNKNOWN")),
                    [
                        "instance: {0} (id {1})".format(item.get("instance_name"), item.get("instance_id")),
                        "type: {0} (id {1})".format(item.get("type_name"), item.get("type_id")),
                        "linked document: {0}".format(item.get("linked_document_title") or "not loaded/not scanned"),
                        "pinned: {0}".format(item.get("pinned")),
                        "path: {0}".format(item.get("path_info") or "<path not available>"),
                        "origin internal ft: X={0}, Y={1}, Z={2}".format(origin.get("x"), origin.get("y"), origin.get("z")),
                        "origin display: X={0}, Y={1}, Z={2}".format(origin_display.get("x"), origin_display.get("y"), origin_display.get("z")),
                        "rotation Z: {0} deg".format(item.get("rotation_z_degrees")),
                        "transform: {0}".format("identity" if item.get("close_to_identity") is True else "non-identity/unknown"),
                        "basis orthonormal: {0}".format(item.get("basis_orthonormal")),
                        "coordinate flags: {0}".format(", ".join(flags) if flags else "none"),
                    ],
                )
            )
        self.ProjectContextTree.Items.Add(
            self._context_tree_item("Links / Coordinate Health", coord_children)
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
            self.append_chat_notice("PROJECT SCAN COMPLETE", result.get("summary", ""))
            self.update_window_status("ready_to_execute", "Standard project scan complete")
        except Exception as exc:
            self.append_chat_notice("PROJECT SCAN FAILED", str(exc))
            self._set_project_context_status("Scan failed partially")
            self.update_window_status("failed", "Project scan failed")
        finally:
            self.hide_busy()
            self._set_thinking("Thinking...")

    def _is_scan_request(self, prompt):
        text = (prompt or "").lower()
        return text in ("scan current project", "refresh project context", "project context") or "scan current project" in text or "refresh project context" in text

    def _is_project_context_question(self, prompt):
        text = (prompt or "").lower()
        triggers = [
            "summarize current project",
            "what is in this revit model",
            "what links are loaded",
            "what linked models are loaded",
            "list revit links",
            "show revit links",
            "check linked model coordinates",
            "linked model coordinate health",
            "are the links aligned",
            "do the linked models match coordinates",
            "check revit link transforms",
            "are any links unloaded",
            "coordinate health check",
            "project coordinates check",
            "what cad imports",
            "list cad imports",
            "show imports",
            "what schedules",
            "list schedules",
            "show schedules",
            "empty schedules",
            "suspicious schedules",
            "template schedules",
            "list levels",
            "show levels",
            "list views",
            "show views",
            "list sheets",
            "show sheets",
            "what categories",
            "show category counts",
            "show warnings",
            "main bim issues",
            "what should i check first",
            "what should i test first",
            "what should i inspect first",
            "where should i start",
            "first checks",
            "recommended checks",
            "project first checks",
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
        text = (prompt or "").strip().lower()
        normalized = re.sub(r"\s+", " ", text)
        exact = {
            "scan current project": "scan",
            "refresh project context": "scan",
            "summarize current project": "summary",
            "project context": "summary",
            "list levels": "levels",
            "show levels": "levels",
            "list views": "views",
            "show views": "views",
            "list sheets": "sheets",
            "show sheets": "sheets",
            "what links are loaded": "links",
            "what linked models are loaded": "link_coordinate_health",
            "are any links unloaded": "link_coordinate_health",
            "list revit links": "links",
            "show revit links": "links",
            "check linked model coordinates": "link_coordinate_health",
            "linked model coordinate health": "link_coordinate_health",
            "are the links aligned": "link_coordinate_health",
            "do the linked models match coordinates": "link_coordinate_health",
            "check revit link transforms": "link_coordinate_health",
            "coordinate health check": "link_coordinate_health",
            "project coordinates check": "link_coordinate_health",
            "what cad imports are in this model": "imports",
            "list cad imports": "imports",
            "show imports": "imports",
            "what schedules exist": "schedules",
            "list schedules": "schedules",
            "show schedules": "schedules",
            "show empty schedules": "empty_schedules",
            "show suspicious schedules": "empty_schedules",
            "show template schedules": "template_schedules",
            "what categories are in this model": "categories",
            "show category counts": "categories",
            "what are the main bim issues": "issues",
            "show warnings": "warnings",
            "what should i check first in this project": "first_checks",
            "what should i test first in this project": "first_checks",
            "what should i inspect first": "first_checks",
            "where should i start": "first_checks",
            "first checks": "first_checks",
            "recommended checks": "first_checks",
            "project first checks": "first_checks",
            "summary": "summary",
            "levels": "levels",
            "views": "views",
            "sheets": "sheets",
            "links": "links",
            "imports": "imports",
            "categories": "categories",
            "schedules": "schedules",
            "warnings": "warnings",
            "issues": "issues",
            "first checks": "first_checks",
            "empty schedules": "empty_schedules",
        }
        if normalized in exact:
            return exact[normalized]
        if "scan current project" in normalized or "refresh project context" in normalized:
            return "scan"
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
        health = context.get("linked_model_coordinate_health", {}) or {}
        lines = [
            "[LINKED MODEL COORDINATE HEALTH]",
            "Total links: {0}".format(health.get("total_links", 0)),
            "Loaded/readable: {0}".format(health.get("loaded_readable_count", 0)),
            "Unavailable: {0}".format(health.get("unavailable_count", 0)),
            "Non-identity transforms: {0}".format(health.get("non_identity_transform_count", 0)),
            "Rotated links: {0}".format(health.get("rotated_link_count", 0)),
            "Z-offset links: {0}".format(health.get("z_offset_link_count", 0)),
            "Duplicate link instances: {0}".format(health.get("duplicate_link_instance_count", 0)),
            "Overall status: {0}".format(health.get("overall_status", "not scanned")),
            "",
        ]
        links = health.get("links", []) or []
        suspicious = [item for item in links if item.get("status_label") != "OK"]
        if suspicious:
            lines.append("[LINKS TO REVIEW]")
            for item in suspicious[:10]:
                flags = item.get("coordinate_flags") or []
                origin = item.get("origin_display") or {}
                lines.append("- {0} [{1}] | flags: {2}".format(item.get("display_name") or item.get("type_name") or item.get("instance_name"), item.get("status_label", "UNKNOWN"), ", ".join(flags) if flags else "none"))
                lines.append("  origin: X={0}, Y={1}, Z={2} | rotation Z: {3} deg".format(origin.get("x"), origin.get("y"), origin.get("z"), item.get("rotation_z_degrees")))
                lines.append("  document: {0} | path: {1}".format(item.get("linked_document_title") or "not loaded/not scanned", item.get("path_info") or "<path not available>"))
            if len(suspicious) > 10:
                lines.append("More suspicious links are available in Project Context > Links / Coordinate Health.")
        else:
            lines.append("No suspicious linked-model coordinate flags were found in the sampled context.")
            lines.append("Full per-link transform details are available in Project Context > Links / Coordinate Health.")
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

    def deterministic_project_context_answer(self, prompt):
        kind = self._context_query_kind(prompt)
        if not kind:
            return None
        if kind == "scan":
            result = self.run_project_context_scan("standard")
            return result.get("summary", "")
        context = self.get_latest_project_context(require_standard=True)
        if kind == "summary":
            return self.get_latest_project_context_summary(require_standard=True)
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

        if "aco_bunge_templates_present" in issues:
            add("create-aco-pipe-fitting-summary-from-template", "ACO/Bunge template-like schedules were observed; this reviewed summary action has runtime evidence.")
        if "empty_or_suspicious_schedules" in issues:
            add("scan-current-project", "Suspicious or empty schedule candidates were observed; keep inspection read-only before using schedule templates.")
        if "unloaded_or_unavailable_revit_links" in issues:
            add("scan-current-project", "Unloaded or unavailable Revit links were observed; use the context scan before coordination decisions.")
        if any(flag in issues for flag in ["unloaded_or_unavailable_link", "non_identity_link_transform", "link_z_offset_detected", "rotated_link_detected", "duplicate_link_instances", "unknown_coordinate_state"]):
            add("check-linked-model-coordinate-health", "Linked model coordinate health flags were observed; run the read-only coordinate health check before coordination work.")
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
        if "cad_imports_present" in issues or "view_specific_imports_present" in issues:
            add("health-check-active-view-selection", "CAD/import or active-view coordination issues were observed; run a read-only active-view health check.")
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
        recommendations = self._recommended_project_actions_from_context(context)
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
        self.AgentHistory.AppendText("Observed context\n{0}\n".format(self.get_latest_project_context_summary(require_standard=True)))
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
        coord_health = context.get("linked_model_coordinate_health", {})
        doc_title = doc_info.get("title") or self.latest_project_context_doc_title or _document_title(doc)
        active_view = "(unknown view)"
        try:
            active_view = active_view_info.get("name") or _active_view_title(doc, uidoc)
        except:
            pass
        issue_text = observed_issue or "Runtime validation/export tooling for ModelMind actions."
        return "\n".join(
            [
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
                "Linked coordinate health: links {0}, loaded {1}, unavailable {2}, non-identity {3}, rotated {4}, z-offset {5}, duplicate types {6}, status {7}".format(
                    coord_health.get("total_links", "not scanned"),
                    coord_health.get("loaded_readable_count", "not scanned"),
                    coord_health.get("unavailable_count", "not scanned"),
                    coord_health.get("non_identity_transform_count", "not scanned"),
                    coord_health.get("rotated_link_count", "not scanned"),
                    coord_health.get("z_offset_link_count", "not scanned"),
                    coord_health.get("duplicate_link_instance_count", "not scanned"),
                    coord_health.get("overall_status", "not scanned"),
                ),
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
        elif prompt == "empty schedules":
            prompt = "show empty schedules"
        elif prompt == "first checks":
            prompt = "what should I check first in this project"
        try:
            reply = self.answer_project_context_question(prompt)
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
            if self._is_codex_brief_request(prompt):
                brief = self.build_codex_task_brief(prompt)
                self.latest_codex_brief = brief
                self.populate_project_context_tree()
                reply = "```\n{0}\n```".format(brief)
            elif self._is_scan_request(prompt):
                result = self.run_project_context_scan("standard")
                reply = result.get("summary", "")
            elif self._is_project_context_question(prompt):
                reply = self.answer_project_context_question(prompt)
            elif "ask ai agent for a plan" in prompt.lower() or "agent plan" in prompt.lower():
                self.append_project_agent_plan()
                reply = "AI Agent project-context plan created. Review it in the AI Agent tab; no actions have been executed."
            else:
                reply = send_ollama_chat(self.model, prompt)
                reply = self._sanitize_ollama_context_error(reply)
            if reply.startswith("Error:") and self.model != DEFAULT_MODEL:
                reply += " Runtime note: this may reflect local model/runtime instability rather than a broken feature. Switching back to phi3:mini is recommended."
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

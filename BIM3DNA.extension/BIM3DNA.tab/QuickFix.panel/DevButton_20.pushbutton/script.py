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
        DB.BuiltInCategory.OST_DataDevices,
        DB.BuiltInCategory.OST_FireAlarmDevices,
        DB.BuiltInCategory.OST_CommunicationDevices,
        DB.BuiltInCategory.OST_SecurityDevices,
        DB.BuiltInCategory.OST_NurseCallDevices,
    ]


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
    "report_devices_without_circuit_info": report_devices_without_circuit_info,
    "list_fixtures_by_type_in_active_view": list_fixtures_by_type_in_active_view,
    "report_selected_elements_by_category": report_selected_elements_by_category,
    "report_selected_elements_by_type": report_selected_elements_by_type,
    "health_check_for_active_view_selection": health_check_for_active_view_selection,
    "report_missing_parameters_from_selection": report_missing_parameters_from_selection,
    "create_3d_view_from_selection": create_3d_view_from_selection,
}


def execute_reviewed_action_handler(handler_name, doc, uidoc):
    handler = REVIEWED_ACTION_HANDLERS.get(handler_name)
    if handler is None:
        return None
    return handler(doc, uidoc)


def handle_public_command(prompt, doc, uidoc):
    p = prompt.lower()
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

        self.populate_model_selector()
        self.ModelSelector.SelectionChanged += self.on_model_selected
        self.refresh_model_info()

        # Ollama Chat: low-risk conversational help and prompt experimentation.
        self.SendButton.Click += self.on_send_chat

        # ModelMind: primary end-user workflow for deterministic and semi-generative tasks.
        self.ModelMindSendButton.Click += self.on_modelmind_send
        self.ApproveCodeButton.Click += self.on_approve_code
        self.SaveRecipeButton.Click += self.on_save_recipe
        self.ToggleReviewedCodeButton.Click += self.on_toggle_reviewed_code
        self.ModelMindInput.KeyDown += self.on_modelmindinput_keydown
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
            "available_to_agent": bool(source_entry.get("available_to_agent", bool(source_entry.get("deterministic_handler")))),
            "deterministic_handler": source_entry.get("deterministic_handler", ""),
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
        self._apply_control_style("ToggleReviewedCodeButton", "#475569", "#ffffff", "#475569")
        self._apply_control_style("ReviewedCodeStateLabel", None, palette["accent"])
        self._apply_control_style("PromptDetailsGroup", palette["panel_bg"], palette["text"], palette["border"])
        self._apply_control_style("PromptDetailTitle", None, palette["text"])
        self._apply_control_style("PromptDetailMeta", None, palette["muted"])
        self._apply_control_style("PromptDetailBody", palette["panel_alt"], palette["text"], palette["border"])
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
        meta = "Role, risk, and validation status will appear here."
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
            lines = [
                "Canonical prompt: {0}".format(
                    entry.get("canonical_prompt", entry.get("prompt_text", ""))
                ),
                "Discipline: {0}".format(entry.get("discipline", "General")),
                "Scope: {0}".format(entry.get("scope_type", "project")),
                "Group: {0}".format(entry.get("group", "Report")),
            ]
            if aliases:
                lines.append("Aliases: {0}".format(", ".join(aliases[:6])))
            if examples:
                lines.append("Examples: {0}".format(", ".join(examples[:4])))
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

    def on_modelmind_input_changed(self, sender, args):
        typed = self.ModelMindInput.Text or ""
        self.filter_prompt_tree(typed)
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

    def on_send_chat(self, sender, args):
        prompt = self.ChatInput.Text.strip()
        if not prompt:
            return

        self.update_window_status("executing", "Ollama Chat request")
        self._set_thinking("Thinking (Chat)...")
        self.show_busy()
        self._pump_ui()

        try:
            self.ChatHistory.AppendText("You: {}\n".format(prompt))
            # Simulate long call
            # time.sleep(5)  # For demo, remove in production
            reply = send_ollama_chat(self.model, prompt)
            if reply.startswith("Error:") and self.model != DEFAULT_MODEL:
                reply += " Runtime note: this may reflect local model/runtime instability rather than a broken feature. Switching back to phi3:mini is recommended."
            self.ChatHistory.AppendText("AI: {}\n\n".format(reply))
            self.ChatInput.Text = ""
        except Exception as e:
            self.ChatHistory.AppendText("Error: {}\n".format(str(e)))
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
        self.populate_prompt_tree(self.ModelMindInput.Text or "")
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
            self.populate_prompt_tree(self.ModelMindInput.Text or "")
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
            result = execute_reviewed_action_handler(step.get("deterministic_handler"), doc, uidoc)
            if result is not None:
                return result
        result = handle_public_command(step.get("prompt_text", ""), doc, uidoc)
        if result is None:
            return "No deterministic executor is available for this step."
        return result

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

    def on_agent_undo(self, sender, args):
        undo_context = self.agent_session.get_undo_context()
        if not undo_context:
            self.AgentHistory.AppendText(
                "Undo unavailable: no undo context recorded.\n\n"
            )
            self.refresh_agent_undo_status()
            self.refresh_action_button_states()
            return

        action_id = undo_context.get("action_id")
        if action_id == "create-3d-view-from-selection":
            result = undo_create_3d_view_action(doc, undo_context)
        elif action_id == "create-sheet-reviewed-template":
            result = undo_create_sheet_action(doc, undo_context)
        else:
            result = {
                "ok": False,
                "message": "Undo unavailable: last action is not a supported reversible reviewed action.",
            }
        self.AgentHistory.AppendText("{0}\n\n".format(result.get("message", "")))
        if result.get("ok"):
            self.agent_session.clear_undo_context()
            self.update_window_status("idle", "Undo completed")
        else:
            self.update_window_status("failed", "Undo failed")
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

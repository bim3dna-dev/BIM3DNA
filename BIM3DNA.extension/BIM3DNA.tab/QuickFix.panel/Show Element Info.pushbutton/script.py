# -*- coding: utf-8 -*-
__title__ = "Show \n Element Info"
__doc__ = "Shows detailed info for selected elements, including ALL parameters, with color-coded console output and warnings for missing values."

from pyrevit import revit, DB, forms, script
from pyrevit import script as pyscript
import System.Windows.Forms as WinForms
from Autodesk.Revit.DB import (
    BuiltInParameter,
    StorageType,
    WorksetTable,
    WorksetId,
    ElementId,
    FamilyInstance,
    MEPCurve,
)
from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons, TaskDialogIcon

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

out = script.get_output()
sel = revit.get_selection()
if not sel:
    forms.alert("No selection! Please select elements first.", exitscript=True)

# ---------------- helpers ----------------


def as_readable(p):
    """Prefer Revit pretty strings (units/names)."""
    try:
        if not p:
            return None
        v = p.AsValueString()
        if v not in (None, ""):
            return v
        st = p.StorageType
        if st == StorageType.String:
            return p.AsString()
        if st == StorageType.Double:
            return str(p.AsDouble())
        if st == StorageType.Integer:
            return str(p.AsInteger())
        if st == StorageType.ElementId:
            eid = p.AsElementId()
            if eid and eid.IntegerValue >= 0:
                el = p.Element.Document.GetElement(eid)
                if el and hasattr(el, "Name"):
                    return el.Name
            return str(eid.IntegerValue)
    except:
        pass
    return None


def workset_name_and_id(el):
    # Try WorksetId API first
    try:
        wid = getattr(el, "WorksetId", None)
        if wid and wid.IntegerValue != -1:
            ws = WorksetTable.GetWorkset(el.Document, wid)
            if ws:
                return ws.Name, wid.IntegerValue
    except:
        pass
    # Fallback through parameter
    try:
        p = el.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM)
        if p:
            nm = p.AsValueString()
            eid = p.AsElementId()
            if nm and eid:
                return nm, eid.IntegerValue
    except:
        pass
    return None, None


def family_and_type(el):
    fn = tn = None
    try:
        te = el.Document.GetElement(el.GetTypeId())
        if te:
            tn = getattr(te, "Name", None)
            if hasattr(te, "Family") and te.Family:
                fn = te.Family.Name
    except:
        pass
    return fn, tn


def level_name(el):
    try:
        lid = getattr(el, "LevelId", None)
        if lid and lid.IntegerValue > -1:
            lv = el.Document.GetElement(lid)
            if lv:
                return lv.Name
    except:
        pass
    for bip in (
        BuiltInParameter.LEVEL_PARAM,
        BuiltInParameter.SCHEDULE_LEVEL_PARAM,
        BuiltInParameter.WALL_BASE_CONSTRAINT,
    ):
        try:
            v = as_readable(el.get_Parameter(bip))
            if v:
                return v
        except:
            pass
    return None


def owner_view_name(el):
    try:
        if getattr(el, "ViewSpecific", False):
            v = el.Document.GetElement(el.OwnerViewId)
            return getattr(v, "Name", None)
    except:
        pass
    return None


def host_info(el):
    try:
        if isinstance(el, FamilyInstance):
            h = el.Host
            if h:
                nm = getattr(h, "Name", None)
                cat = h.Category.Name if h.Category else "Host"
                return "{}{}".format(cat + ": " if cat else "", nm or h.Id.IntegerValue)
    except:
        pass
    return None


def mep_system_name(el):
    try:
        if isinstance(el, MEPCurve) and el.MEPSystem:
            return getattr(el.MEPSystem, "Name", None) or getattr(
                el.MEPSystem, "Abbreviation", None
            )
        if isinstance(el, FamilyInstance):
            mep = getattr(el, "MEPModel", None)
            if mep and mep.MEPSystem:
                return getattr(mep.MEPSystem, "Name", None) or getattr(
                    mep.MEPSystem, "Abbreviation", None
                )
    except:
        pass
    return None


def dedup_params(param_iter):
    """
    Build a dict of name -> value, keeping the first non-empty value.
    This removes duplicates like 'Design Option' (id + name).
    """
    out = {}
    for p in param_iter:
        try:
            nm = p.Definition.Name
            val = as_readable(p)
            if nm not in out:
                out[nm] = val
            else:
                if (out[nm] in (None, "")) and val not in (None, ""):
                    out[nm] = val
        except:
            pass
    return out


def html_line(label, value, ok=True):
    color = "blue" if ok else "orange"
    v = value if ok else "⚠️ " + (value or "<None>")
    return '<span style="color:{}">{}</span>{}'.format(color, label, v)


# --------------- main ---------------------

for el in sel:
    blocks = []

    # Top summary
    fn, tn = family_and_type(el)
    lvl = level_name(el)
    ws_name, ws_id = workset_name_and_id(el)

    top = []
    top.append(html_line("Element ID: ", str(el.Id)))
    try:
        top.append(html_line("Category: ", el.Category.Name))
    except:
        top.append(html_line("Category: ", "<None>", ok=False))
    top.append(html_line("Family: ", fn or "<None>", ok=bool(fn)))
    top.append(html_line("Type: ", tn or "<None>", ok=bool(tn)))
    top.append(html_line("Level: ", lvl or "<None>", ok=bool(lvl)))
    if ws_name:
        top.append(html_line("Workset: ", "{} (Id: {})".format(ws_name, ws_id)))
    else:
        top.append(html_line("Workset: ", "<None>", ok=False))
    try:
        do = as_readable(el.get_Parameter(BuiltInParameter.DESIGN_OPTION_PARAM))
        if do:
            top.append(html_line("Design Option: ", do))
    except:
        pass
    for lbl, bip in (
        ("Phase Created", BuiltInParameter.PHASE_CREATED),
        ("Phase Demolished", BuiltInParameter.PHASE_DEMOLISHED),
    ):
        try:
            v = as_readable(el.get_Parameter(bip))
            if v:
                top.append(html_line(lbl + ": ", v))
        except:
            pass
    try:
        top.append(html_line("Pinned: ", "Yes" if el.Pinned else "No"))
    except:
        pass

    ov = owner_view_name(el)
    if ov:
        top.append(html_line("Owner View: ", ov))

    host = host_info(el)
    if host:
        top.append(html_line("Host: ", host))

    msys = mep_system_name(el)
    if msys:
        top.append(html_line("MEP System: ", msys))

    # Location
    try:
        loc = el.Location
        if hasattr(loc, "Point") and loc.Point:
            p = loc.Point
            top.append(
                html_line(
                    "Location: ", "({:.2f}, {:.2f}, {:.2f})".format(p.X, p.Y, p.Z)
                )
            )
        elif hasattr(loc, "Curve") and loc.Curve:
            top.append(html_line("Location: ", "Curve"))
        else:
            top.append(html_line("Location: ", "<None>", ok=False))
    except:
        top.append(html_line("Location: ", "<None>", ok=False))

    blocks.append("<br/>".join(top))

    # Instance parameters (deduped)
    inst_map = dedup_params(el.Parameters)
    inst_lines = ['<span style="color:magenta">All Instance Parameters:</span>']
    for nm, val in sorted(inst_map.items()):
        if val in (None, ""):
            inst_lines.append(
                '    <span style="color:green">{}</span>: <span style="color:orange">⚠️ &lt;Empty&gt;</span>'.format(
                    nm
                )
            )
        else:
            inst_lines.append(
                '    <span style="color:green">{}</span>: <span style="color:green">{}</span>'.format(
                    nm, val
                )
            )
    blocks.append("<br/>".join(inst_lines))

    # Type parameters (handy and requested often)
    try:
        te = el.Document.GetElement(el.GetTypeId())
        if te:
            type_map = dedup_params(te.Parameters)
            type_lines = ['<span style="color:magenta">All Type Parameters:</span>']
            for nm, val in sorted(type_map.items()):
                if val in (None, ""):
                    type_lines.append(
                        '    <span style="color:green">{}</span>: <span style="color:orange">⚠️ &lt;Empty&gt;</span>'.format(
                            nm
                        )
                    )
                else:
                    type_lines.append(
                        '    <span style="color:green">{}</span>: <span style="color:green">{}</span>'.format(
                            nm, val
                        )
                    )
            blocks.append("<br/>".join(type_lines))
    except:
        pass

    # print with fallback
    html = "<div style='font-family:Consolas,monospace'>{}</div>".format(
        "<br/><br/>".join(blocks)
    )
    try:
        out.print_html(html)
    except Exception as e:
        print("HTML render failed ({}). Falling back to text:\n".format(e))
        from System.Text import StringBuilder

        sb = StringBuilder()
        for chunk in blocks:
            txt = chunk.replace("<br/>", "\n").replace("<br>", "\n")
            for tag in ("span", "div"):
                txt = txt.replace("<{}>".format(tag), "").replace(
                    "</{}>".format(tag), ""
                )
            sb.AppendLine(txt + "\n")
        print(sb.ToString())

forms.alert("Element info printed to pyRevit console.", title="Element Info Printed")

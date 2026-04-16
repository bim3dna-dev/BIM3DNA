# -*- coding: utf-8 -*-
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.Exceptions import OperationCanceledException
from pyrevit import revit, DB

class _MEPSelectionFilter(ISelectionFilter):
    def AllowElement(self, element):
        try:
            if not element or not element.Category:
                return False
            bic = element.Category.Id.IntegerValue
            allowed = set([
                int(DB.BuiltInCategory.OST_PipeCurves),
                int(DB.BuiltInCategory.OST_PipeFitting),
                int(DB.BuiltInCategory.OST_PipeAccessory),
                int(DB.BuiltInCategory.OST_DuctCurves),
                int(DB.BuiltInCategory.OST_DuctFitting),
                int(DB.BuiltInCategory.OST_DuctAccessory),
                int(DB.BuiltInCategory.OST_MechanicalEquipment),
                int(DB.BuiltInCategory.OST_PlumbingFixtures),
            ])
            return bic in allowed
        except Exception:
            return False

    def AllowReference(self, reference, point):
        return True

def pick_point_on_element(prompt="Pick point on element"):
    uidoc = revit.uidoc
    try:
        return uidoc.Selection.PickObject(
            ObjectType.PointOnElement,
            _MEPSelectionFilter(),
            prompt
        )
    except OperationCanceledException:
        return None
    except Exception:
        return None

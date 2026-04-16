# -*- coding: utf-8 -*-
__title__ = "Fix \n Worksets"
__doc__ = """This manually assigns correct worksets to all existing elements in the project."""

import os
from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    Transaction,
    FilteredElementCollector,
)
from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons, TaskDialogResult
from System import DateTime
from pyrevit import revit
from pyrevit import script as pyscript
import System.Windows.Forms as WinForms
from pyrevit import forms


import clr

uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document

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

# Workset mapping based on Assembly Code
txt_mapping = {
    # Ground Facilites (Bodemvoorzieningen)
    "1": 9353,  # FUNDERINGEN
    "11": 9353,  # Bodemvoorzieningen	2	-2001340
    "11.00": 9353,  # bodemvoorzieningen; algemeen 	3	-2001340
    "11.10": 9353,  # bodemvoorzieningen - grond, algemeen (verzamelniveau)	4	-2001340
    "11.11": 9353,  # bodemvoorzieningen - grond, ontgravingen	4	-2001340
    "11.12": 9353,  # bodemvoorzieningen - grond, aanvullingen	4	-2001340
    "11.13": 9353,  # bodemvoorzieningen - grond, sloop- en rooiwerkzaamheden	4	-2001340
    "11.15": 9353,  # bodemvoorzieningen, grond, damwanden	4	-2001340
    "11.20": 9353,  # bodemvoorzieningen - water, algemeen (verzamelniveau)	4	-2001340
    "11.24": 9353,  # bodemvoorzieningen - water, bemalingen	4	-2001340
    "11.25": 9353,  # bodemvoorzieningen - water, damwanden	4	-2001340
    # Foundation Floors (Vloeren op grondlag)
    "13": 9354,  # Vloeren op grondslag	2	-2000032
    "13.10": 9354,  # vloeren op grondslag - niet constructief, algemeen (verzamelniveau)	4	-2000032
    "13.00": 9354,  # vloeren op grondslag; algemeen	3	-2000032
    "13.11": 9354,  # vloeren op grondslag - niet constructief, bodemafsluitingen	4	-2000032
    "13.12": 9354,  # vloeren op grondslag - niet constructief, vloeren als gebouwonderdeel	4	-2000032
    "13.13": 9354,  # vloeren op grondslag - niet constructief, vloeren als bestrating	4	-2000032
    "13.20": 9354,  # vloeren op grondslag - constructief, algemeen (verzamelniveau)	4	-2000032
    "13.21": 9354,  # vloeren op grondslag - constructief, bodemafsluitingen	4	-2000032
    "13.22": 9354,  # vloeren op grondslag - constructief, vloeren als gebouwonderdeel	4	-2000032
    "13.25": 9354,  # vloeren op grondslag - constructief, grondverbeteringen	4	-2000032
    # Foundation Structures (Funderingsconstructies)
    "16": 9355,  # Funderingsconstructies	2	-2001300
    "16.00": 9355,  # funderingsconstructies; algemeen	3	-2001300
    "16.10": 9355,  # funderingsconstructies - voeten en balken, algemeen (verzamelniveau)	4	-2001300
    "16.11": 9355,  # funderingsconstructies - voeten en balken, fundatie voeten	4	-2001300
    "16.12": 9355,  # funderingsconstructies - voeten en balken, fundatie balken	4	-2001300
    "16.13": 9355,  # funderingsconstructies - voeten en balken, fundatie poeren	4	-2001300
    "16.14": 9355,  # funderingsconstructies - voeten en balken, gevelwanden (-200)	4	-2001300
    "16.15": 9355,  # funderingsconstructies - voeten en balken, grondverbeteringen	4	-2001300
    "16.20": 9355,  # funderingsconstructies - keerwanden, algemeen (verzamelniveau)	4	-2001300
    "16.21": 9355,  # funderingsconstructies - keerwanden, grondkerende wanden	4	-2001300
    "16.22": 9355,  # funderingsconstructies - keerwanden, waterkerende wanden	4	-2001300
    "16.23": 9355,  # funderingsconstructies - keerwanden, gevelwanden (-200)	4	-2001300
    "16.25": 9355,  # funderingsconstructies - keerwanden, grondverbeteringen	4	-2001300
    # Foundation Piles (PaalFundering)
    "17": 9356,  # Paalfunderingen	2	-2001300
    "17.00": 9356,  # paalfunderingen; algemeen	3	-2001300
    "17.10": 9356,  # paalfunderingen - niet geheid, algemeen (verzamelniveau)	4	-2001300
    "17.11": 9356,  # paalfunderingen - niet geheid, dragend palen - geboord	4	-2001300
    "17.12": 9356,  # paalfunderingen - niet geheid, dragende palen - geschroefd	4	-2001300
    "17.13": 9356,  # paalfunderingen - niet geheid, trekverankeringen	4	-2001300
    "17.14": 9356,  # paalfunderingen - niet geheid, pijler-putring funderingen	4	-2001300
    "17.15": 9356,  # paalfunderingen - niet geheid, bodeminjecties	4	-2001300
    "17.20": 9356,  # paalfunderingen - geheid, algemeen (verzamelniveau)	4	-2001300
    "17.21": 9356,  # paalfunderingen - geheid, dragende palen	4	-2001300
    "17.22": 9356,  # paalfunderingen - geheid, palen - ingeheide bekisting	4	-2001300
    "17.23": 9356,  # paalfunderingen - geheid, trekverankeringen	4	-2001300
    "17.25": 9356,  # paalfunderingen - geheid, damwanden funderingen	4	-2001300
    # Walls Exterior (Buitenwanden)
    "2": 9357,  # RUWBOUW
    "21": 9357,  # Buitenwanden	2	-2000011
    "21.00": 9357,  # buitenwanden; algemeen 	3	-2000011
    "21.10": 9357,  # buitenwanden - niet constructief, algemeen (verzamelniveau)	4	-2000011
    "21.11": 9357,  # buitenwanden - niet constructief, massieve wanden	4	-2000011
    "21.12": 9357,  # buitenwanden - niet constructief, spouwwanden	4	-2000011
    "21.13": 9357,  # buitenwanden - niet constructief, systeemwanden	4	-2000011
    "21.14": 9357,  # buitenwanden - niet constructief, vlieswanden	4	-2000011
    "21.15": 9357,  # buitenwanden - niet constructief, borstweringen	4	-2000011
    "21.16": 9357,  # buitenwanden - niet constructief, boeiboorden	4	-2000011
    "21.20": 9357,  # buitenwanden - constructief, algemeen (verzamelniveau)	4	-2000011
    "21.21": 9357,  # buitenwanden - constructief, massieve wanden	4	-2000011
    "21.22": 9357,  # buitenwanden - constructief, spouwwanden	4	-2000011
    "21.23": 9357,  # buitenwanden - constructief, systeemwanden	4	-2000011
    "21.25": 9357,  # buitenwanden - constructief, borstweringen	4	-2000011
    # Walls Interior (Binnenwanden)
    "22": 9358,  # Binnenwanden 2    -2000011
    "22.00": 9358,  # binnenwanden; algemeen 	3	-2000011
    "22.10": 9358,  # binnenwanden - niet constructief, algemeen (verzamelniveau)	4	-2000011
    "22.11": 9358,  # binnenwanden - niet constructief, massieve wanden	4	-2000011
    "22.12": 9358,  # binnenwanden - niet constructief, spouwwanden	4	-2000011
    "22.13": 9358,  # binnenwanden - niet constructief, systeemwanden - vast	4	-2000011
    "22.14": 9358,  # binnenwanden - niet constructief, systeemwanden - verplaatsbaar	4	-2000011
    "22.20": 9358,  # binnenwanden - constructief, algemeen (verzamelniveau)	4	-2000011
    "22.21": 9358,  # binnenwanden - constructief, massieve wanden	4	-2000011
    "22.22": 9358,  # binnenwanden - constructief, spouwwanden	4	-2000011
    "22.23": 9358,  # binnenwanden - constructief, systeemwanden - vast	4	-2000011
    # Floors (Vloeren)
    "23": 9359,  # Vloeren 2	    -2000032
    "23.00": 9359,  # vloeren; algemeen 	3	-2000032
    "23.10": 9359,  # vloeren - niet constructief 	4	-2000032
    # "23.10": 9359,  # vloeren - niet constructief, algemeen (verzamelniveau)	4	-2000032
    "23.11": 9359,  # vloeren - niet constructief, vrijdragende Vloeren	4	-2000032
    "23.12": 9359,  # vloeren - niet constructief, balkons	4	-2000032
    "23.13": 9359,  # vloeren - niet constructief, galerijen	4	-2000032
    "23.14": 9359,  # vloeren - niet constructief, bordessen	4	-2000032
    "23.15": 9359,  # vloeren - niet constructief, vloeren t.b.v. technische voorziengen	4	-2000032
    "23.20": 9359,  # vloeren - constructief, algemeen (verzamelniveau)	4	-2000032
    "23.21": 9359,  # vloeren - constructief, vrijdragende vloeren	4	-2000032
    "23.22": 9359,  # vloeren - constructief, balkons	4	-2000032
    "23.23": 9359,  # vloeren - constructief, galerijen	4	-2000032
    "23.24": 9359,  # vloeren - constructief, bordessen	4	-2000032
    "23.25": 9359,  # vloeren - constructief, vloeren t.b.v. technische voorziengen	4	-2000032
    "24": 9360,  # Trappen en hellingesn	2	-2000120
    "24.00": 9360,  # trappen en hellingen; algemeen 	3	-2000120
    "24.10": 9360,  # trappen en hellingen - trappen, algemeen (verzamelniveau)	4	-2000120
    "24.11": 9360,  # trappen en hellingen - trappen, rechte steektrappen	4	-2000120
    "24.12": 9360,  # trappen en hellingen - trappen, niet rechte steektrappen	4	-2000120
    "24.13": 9360,  # trappen en hellingen - trappen, spiltrappen	4	-2000120
    "24.15": 9360,  # trappen en hellingen - trappen, bordessen	4	-2000120
    "24.20": 9360,  # trappen en hellingen - hellingen, algemeen (verzamelniveau)	4	-2000180
    "24.21": 9360,  # trappen en hellingen - hellingen, beloopbare hellingen	4	-2000180
    "24.22": 9360,  # trappen en hellingen - hellingen, berijdbare hellingen	4	-2000180
    "24.25": 9360,  # trappen en hellingen - hellingen, bordessen	4	-2000180
    "24.30": 9360,  # trappen en hellingen - ladders en klimijzers, algemeen (verzamelniveau)	4	-2000120
    "24.31": 9360,  # trappen en hellingen - ladders en klimijzers, ladders	4	-2000120
    "24.32": 9360,  # trappen en hellingen - ladders en klimijzers, klimijzers	4	-2001340
    "24.35": 9360,  # trappen en hellingen - ladders en klimijzers, bordessen	4	-2000120
    # Roof (Dakbeschot)(Dakliggers)
    "27": 9361,  # Daken	2	-2000035
    "27.00": 9361,  # daken; algemeen	3	-2000035
    "27.10": 9361,  # daken - niet constructief, algemeen (verzamelniveau)	4	-2000035
    "27.11": 9361,  # daken - niet constructief, vlakke daken	4	-2000035
    "27.12": 9361,  # daken - niet constructief, hellende daken	4	-2000035
    "27.13": 9361,  # daken - niet constructief, luifels	4	-2000035
    "27.14": 9361,  # daken - niet constructief, overkappingen	4	-2000035
    "27.16": 9361,  # daken - niet constructief, gootconstructies	4	-2000035
    "27.20": 9361,  # daken - constructief, algemeen (verzamelniveau)	4	-2000035
    "27.21": 9361,  # daken - constructief, vlakke daken	4	-2000035
    "27.22": 9361,  # daken - constructief, hellende daken	4	-2000035
    "27.23": 9361,  # daken - constructief, luifels	4	-2000035
    "27.24": 9361,  # daken - constructief, overkappingen	4	-2000035
    "27.26": 9361,  # daken - constructief, gootconstructies	4	-2000035
    # Primary Load-Bearing Structures (Hoofddraagconstructies)
    "28": 9362,  # Hoofddraagconstructies	2	-2001320
    "28.00": 9362,  # hoofddraagconstructies; algemeen	3	-2001320
    "28.10": 9362,  # hoofddraagconstructies - kolommen en liggers, algemeen (verzamelniveau)	4	-2001320
    "28.11": 9362,  # hoofddraagconstructies - kolommen en liggers, kolom-/liggerconstructies	4	-2001320
    "28.12": 9362,  # hoofddraagconstructies - kolommen en liggers, spanten	4	-2001320
    "28.20": 9362,  # hoofddraagconstructies - wanden en vloeren, algemeen (verzamelniveau)	4	-2000011
    "28.21": 9362,  # hoofddraagconstructies - wanden en vloeren, wand-/vloerconstructies	4	-2000011
    "28.30": 9362,  # hoofddraagconstructies - ruimte-eenheden, algemeen (verzamelniveau)	4	-2001320
    "28.31": 9362,  # hoofddraagconstructies - ruimte-eenheden, doosconstructies	4	-2001320
    # Finishing (AFBOUW)
    "3": 9363,  # AFBOUW	1	-2000151
    # Exterior Wall Openings (Buitenwanopeningen)
    "31": 9363,  # Buitenwandopeningen	2	-2000011
    "31.00": 9363,  # buitenwandopeningen; algemeen 	3	-2000011
    "31.10": 9363,  # buitenwandopeningen - niet gevuld, algemeen (verzamelniveau)	4	-2000011
    "31.11": 9363,  # buitenwandopeningen - niet gevuld, daglichtopeningen	4	-2000011
    "31.12": 9363,  # buitenwandopeningen - niet gevuld, buitenluchtopeningen	4	-2000011
    "31.20": 9363,  # buitenwandopeningen - gevuld met ramen, algemeen (verzamelniveau)	4	-2000014
    "31.21": 9363,  # buitenwandopeningen - gevuld met ramen, gesloten ramen	4	-2000014
    "31.22": 9363,  # buitenwandopeningen - gevuld met ramen, ramen draaiend aan een kant	4	-2000014
    "31.23": 9363,  # buitenwandopeningen - gevuld met ramen, schuiframen	4	-2000014
    "31.24": 9363,  # buitenwandopeningen - gevuld met ramen, ramen draaiend op verticale of horizontale as	4	-2000014
    "31.25": 9363,  # buitenwandopeningen - gevuld met ramen, combinatieramen	4	-2000014
    "31.30": 9363,  # buitenwandopeningen - gevuld met deuren, algemeen (verzamelniveau)	4	-2000023
    "31.31": 9363,  # buitenwandopeningen - gevuld met deuren, draaideuren	4	-2000023
    "31.32": 9363,  # buitenwandopeningen - gevuld met deuren, schuifdeuren	4	-2000023
    "31.33": 9363,  # buitenwandopeningen - gevuld met deuren, tuimeldeuren	4	-2000023
    "31.34": 9363,  # buitenwandopeningen - gevuld met deuren, tourniqets	4	-2000023
    "31.40": 9363,  # buitenwandopeningen - gevuld met puien, algemeen (verzamelniveau)	4	-2000014
    "31.41": 9363,  # buitenwandopeningen - gevuld met puien, gesloten puien	4	-2000014
    # Interior Wall Openings (Binnenwandopeningen)
    "32": 9364,  # 	Binnenwandopeningen	2	-2000011
    "32.00": 9364,  # binnenwandopeningen; algemeen 	3	-2000011
    "32.10": 9364,  # binnenwandopeningen - niet gevuld, algemeen (verzamelniveau)	4	-2000011
    "32.11": 9364,  # binnenwandopeningen - niet gevuld, openingen als doorgang	4	-2000011
    "32.12": 9364,  # binnenwandopeningen - niet gevuld, openingen als doorzicht	4	-2000011
    "32.20": 9364,  # binnenwandopeningen - gevuld met ramen, algemeen (verzamelniveau)	4	-2000014
    "32.21": 9364,  # binnenwandopeningen - gevuld met ramen, gesloten ramen	4	-2000014
    "32.22": 9364,  # binnenwandopeningen - gevuld met ramen, ramen draaiend aan een kant	4	-2000014
    "32.23": 9364,  # binnenwandopeningen - gevuld met ramen, schuiframen	4	-2000014
    "32.24": 9364,  # binnenwandopeningen - gevuld met ramen, ramen draaiend op verticale of horizontale as	4	-2000014
    "32.25": 9364,  # binnenwandopeningen - gevuld met ramen, combinatieramen	4	-2000014
    "32.30": 9364,  # binnenwandopeningen - gevuld met deuren, algemeen (verzamelniveau)	4	-2000023
    "32.31": 9364,  # binnenwandopeningen - gevuld met deuren, draaideuren	4	-2000023
    "32.32": 9364,  # binnenwandopeningen - gevuld met deuren, schuifdeuren	4	-2000023
    "32.33": 9364,  # binnenwandopeningen - gevuld met deuren, tuimeldeuren	4	-2000023
    "32.34": 9364,  # binnenwandopeningen - gevuld met deuren, tourniqets	4	-2000023
    "32.40": 9364,  # binnenwandopeningen - gevuld met puien, algemeen (verzamelniveau)	4	-2000011
    "32.41": 9364,  # binnenwandopeningen - gevuld met puien, gesloten puien	4	-2000011
    # Floor Openings (Vloeropeningen)
    "33": 9359,  # Vloeropeningen	2	-2000032
    "33.00": 9359,  # vloeropeningen; algemeen 	3	-2000032
    "33.10": 9359,  # vloeropeningen - niet gevuld, algemeen (verzamelniveau)	4	-2000032
    "33.11": 9359,  # vloeropeningen - niet gevuld, openingen als doorgang	4	-2000032
    "33.12": 9359,  # vloeropeningen - niet gevuld, openingen als doorzicht	4	-2000032
    "33.20": 9359,  # vloeropeningen - gevuld, algemeen (verzamelniveau)	4	-2000032
    "33.21": 9359,  # vloeropeningen - gevuld, beloopbare vullingen	4	-2000032
    "33.22": 9359,  # vloeropeningen - gevuld, niet-beloopbare vullingen	4	-2000032
    # Balustrades and Railings (Balustrades en Leuningen)
    "34": 9366,  # Balustrades en leuningen	2	-2000126
    "34.00": 9366,  # balustrades en leuningen; algemeen 	3	-2000126
    "34.10": 9366,  # balustrades en leuningen - balustrades, algemeen (verzamelniveau)	4	-2000126
    "34.11": 9366,  # balustrades en leuningen - balustrades, binnenbalustrades	4	-2000126
    "34.12": 9366,  # balustrades en leuningen - balustrades, buitenbalustrades	4	-2000126
    "34.20": 9366,  # balustrades en leuningen - leuningen, algemeen (verzamelniveau)	4	-2000126
    "34.21": 9366,  # balustrades en leuningen - leuningen, binnenleuningen	4	-2000126
    "34.22": 9366,  # balustrades en leuningen - leuningen, buitenleuningen	4	-2000126
    # Roof Openings (Dak Openingen)
    "37": 9367,  # 	Dakopeningen	2	-2000035
    "37.00": 9367,  # dakopeningen; algemeen 	3	-2000035
    "37.10": 9367,  # dakopeningen - niet gevuld, algemeen (verzamelniveau)	4	-2000035
    "37.11": 9367,  # dakopeningen - niet gevuld, daglichtopeningen	4	-2000035
    "37.12": 9367,  # dakopeningen - niet gevuld, buitenluchtopeningen	4	-2000035
    "37.20": 9367,  # dakopeningen - gevuld, algemeen (verzamelniveau)	4	-2000014
    "37.21": 9367,  # dakopeningen - gevuld, gesloten ramen	4	-2000014
    "37.22": 9367,  # dakopeningen - gevuld, ramen draaiend aan één kant	4	-2000014
    "37.23": 9367,  # dakopeningen - gevuld, schuiframen	4	-2000014
    "37.24": 9367,  # dakopeningen - gevuld, ramen draaiend op een as	4	-2000014
    "37.25": 9367,  # dakopeningen - gevuld, combinatieramen	4	-2000014
    # Built_in Packages (Inbouwpakketten)
    "38": 9368,  # Inbouwpakketten	2	-2000151
    "38.00": 9368,  # inbouwpakketten; algemeen	3	-2000151
    "38.10": 9368,  # inbouwpakketten - algemeen (verzamelniveau)	4	-2000151
    "38.11": 9368,  # inbouwpakketten - inbouwpakketten met te openen delen	4	-2000151
    "38.12": 9368,  # inbouwpakketten - inbouwpakketten met gesloten delen	4	-2000151
    # Exterior Wall Finishes (Buitenwandafwerkingen)
    "4": 9357,  # -	AFWERKINGEN	1	-2000151
    "41": 9357,  # 	Buitenwandafwerkingen	2	-2000011
    "41.00": 9357,  # buitenwandafwerkingen; algemeen 	3	-2000011
    "41.10": 9357,  # buitenwandafwerkingen - algemeen (verzamelniveau)	4	-2000011
    "41.11": 9357,  # buitenwandafwerkingen - afwerklagen 	4	-2000011
    "41.12": 9357,  # buitenwandafwerkingen - bekledingen 	4	-2000011
    "41.13": 9357,  # buitenwandafwerkingen - voorzetwanden	4	-2000011
    # Interior Wall Finishes (Binnenwandafwerkingen)
    "42": 9358,  # 	Binnenwandafwerkingen	2	-2000011
    "42.00": 9358,  # binnenwandafwerkingen; algemeen	3	-2000011
    "42.10": 9358,  # binnenwandafwerkingen - algemeen (verzamelniveau)	4	-2000011
    "42.11": 9358,  # binnenwandafwerkingen - afwerklagen	4	-2000011
    "42.12": 9358,  # binnenwandafwerkingen - bekledingen	4	-2000011
    # Floor Finishes (Vloerafwerkingen)
    "43": 9359,  # 	Vloerafwerkingen	2	-2000032
    "43.00": 9359,  # vloerafwerkingen; algemeen 	3	-2000032
    "43.10": 9359,  # vloerafwerkingen - verhoogd, algemeen (verzamelniveau) 	4	-2000032
    "43.11": 9359,  # vloerafwerkingen - verhoogd, podiums	4	-2000032
    "43.12": 9359,  # vloerafwerkingen - verhoogd, installatievloeren	4	-2000032
    "43.20": 9359,  # vloerafwerkingen - niet verhoogd, algemeen (verzamelniveau)	4	-2000032
    "43.21": 9359,  # vloerafwerkingen - niet verhoogd, afwerklagen	4	-2000032
    "43.22": 9359,  # vloerafwerkingen - niet verhoogd, bekledingen	4	-2000032
    "43.23": 9359,  # vloerafwerkingen - niet verhoogd, systeemvloerafwerkingen	4	-2000032
    # Stair and Ramp Finishes (Trap- en Hellingafwekingen)
    "44": 9360,  # Trap- en hellingafwerkingen	2	-2000120
    "44.00": 9360,  # trap- en hellingafwerkingen; algemeen 	3	-2000120
    "44.10": 9360,  # trap- en hellingafwerkingen - trapafwerkingen, algemeen (verzamelniveau)	4	-2000120
    "44.11": 9360,  # trap- en hellingafwerkingen - trapafwerkingen, afwerklagen	4	-2000120
    "44.12": 9360,  # trap- en hellingafwerkingen - trapafwerkingen, bekledingen	4	-2000120
    "44.13": 9360,  # trap- en hellingafwerkingen - trapafwerkingen, systeemafwerkingen	4	-2000120
    "44.20": 9360,  # trap- en hellingafwerkingen - hellingafwerkingen, algemeen (verzamelniveau)	4	-2000120
    "44.21": 9360,  # trap- en hellingafwerkingen - hellingafwerkingen, afwerklagen	4	-2000120
    "44.22": 9360,  # trap- en hellingafwerkingen - hellingafwerkingen, bekledingen	4	-2000120
    "44.23": 9360,  # trap- en hellingafwerkingen - hellingafwerkingen, systeemafwerkingen	4	-2000120
    # Ceiling Finishes (Plafondafwerkingen)
    "45": 9373,  # Plafondafwerkingen	2	-2000038
    "45.00": 9373,  # plafondafwerkingen; algemeen 	3	-2000038
    "45.10": 9373,  # plafondafwerkingen - verlaagd, algemeen (verzamelniveau)	4	-2000038
    "45.11": 9373,  # plafondafwerkingen - verlaagd, verlaagde plafonds	4	-2000038
    "45.12": 9373,  # plafondafwerkingen - verlaagd, systeem plafonds	4	-2000038
    "45.14": 9373,  # plafondafwerkingen - verlaagd, koofconstructies	4	-2000038
    "45.15": 9373,  # plafondafwerkingen - verlaagd, gordijnplanken	4	-2000038
    "45.20": 9373,  # plafondafwerkingen - niet verlaagd, algemeen (verzamelniveau)	4	-2000038
    "45.21": 9373,  # plafondafwerkingen - niet verlaagd, afwerkingen	4	-2000038
    "45.22": 9373,  # plafondafwerkingen - niet verlaagd, bekledingen	4	-2000038
    "45.23": 9373,  # plafondafwerkingen - niet verlaagd, systeemafwerkingen	4	-2000038
    "45.24": 9373,  # plafondafwerkingen - niet verlaagd, koofconstructies	4	-2000038
    "45.25": 9373,  # plafondafwerkingen - niet verlaagd, gordijnplanken	4	-2000038
    # Roof Finishes (Dakafwerkingen)
    "47": 9361,  # 	Dakafwerkingen	2	-2000035
    "47.00": 9361,  # dakafwerkingen; algemeen 	3	-2000035
    "47.10": 9361,  # dakafwerkingen - afwerkingen, algemeen (verzamelniveau)	4	-2000035
    "47.11": 9361,  # dakafwerkingen - afwerkingen, vlakke dakafwerkingen	4	-2000035
    "47.12": 9361,  # dakafwerkingen - afwerkingen, hellende dakafwerkingen	4	-2000035
    "47.13": 9361,  # dakafwerkingen - afwerkingen, luifel afwerkingen	4	-2000035
    "47.14": 9361,  # dakafwerkingen - afwerkingen, overkappings afwerkingen	4	-2000035
    "47.15": 9361,  # dakafwerkingen - afwerkingen, beloopbare dakafwerkingen	4	-2000035
    "47.16": 9361,  # dakafwerkingen - afwerkingen, berijdbare dakafwerkingen	4	-2000035
    "47.20": 9361,  # dakafwerkingen - bekledingen, algemeen (verzamelniveau)	4	-2000035
    "47.21": 9361,  # dakafwerkingen - bekledingen, vlakke bekledingen	4	-2000035
    "47.22": 9361,  # dakafwerkingen - bekledingen, hellende bekledingen	4	-2000035
    "47.23": 9361,  # dakafwerkingen - bekledingen, luifel bekledingen	4	-2000035
    "47.24": 9361,  # dakafwerkingen - bekledingen, overkappings bekledingen	4	-2000035
    "47.25": 9361,  # dakafwerkingen - bekledingen, beloopbare bekledingen	4	-2000035
    "47.26": 9361,  # dakafwerkingen - bekledingen, berijdbare bekledingen	4	-2000035
    # Finishing Packages (Afwerkingspakketten)
    "48": 9368,  # Afwerkingspakketten	2	-2000035
    "48.00": 9368,  # afwerkingspakketten; algemeen 	3	-2000035
    "48.10": 9368,  # afwerkingspakketten - algemeen (verzamelniveau)	4	-2000035
    "48.11": 9368,  # afwerkingspakketten - naadloze afwerkingen	4	-2000035
    "48.12": 9368,  # afwerkingspakketten - overige afwerkingen	4	-2000035
    # Mechanical Installations (Werktuigbouwkundig)
    "5": 9376,  # -	INSTALLATIES WERKTUIGBOUWKUNDIG	1	-2000151
    "51": 9376,  # Warmteopwekking	2	-2001140
    "51.00": 9376,  # warmteopwekking; algemeen 	3	-2001140
    "51.10": 9376,  # warmteopwekking - lokaal, algemeen (verzamelniveau)	4	-2001140
    "51.11": 9376,  # warmteopwekking - lokaal, gasvormige brandstoffen	4	-2001140
    "51.12": 9376,  # warmteopwekking - lokaal, vloeibare brandstoffen	4	-2001140
    "51.13": 9376,  # warmteopwekking - lokaal, vaste brandstoffen	4	-2001140
    "51.14": 9376,  # warmteopwekking - lokaal, schoorstenen/kanalen (niet bouwkundig)	4	-2001140
    "51.16": 9376,  # warmteopwekking - lokaal, gecombineerde tapwater verwarming	4	-2001140
    "51.19": 9376,  # warmteopwekking - lokaal, brandstoffenopslag	4	-2001140
    "51.20": 9376,  # warmteopwekking - centraal, algemeen (verzamelniveau)	4	-2001140
    "51.21": 9376,  # warmteopwekking - centraal, gasvormige brandstoffen	4	-2001140
    "51.22": 9376,  # warmteopwekking - centraal, vloeibare brandstoffen	4	-2001140
    "51.23": 9376,  # warmteopwekking - centraal, vaste brandstoffen	4	-2001140
    "51.24": 9376,  # warmteopwekking - centraal, schoorstenen/kanalen (niet bouwkundig)	4	-2001140
    "51.26": 9376,  # warmteopwekking - centraal, gecombineerde tapwater verwarming	4	-2001140
    "51.29": 9376,  # warmteopwekking - centraal, brandstoffenopslag	4	-2001140
    "51.30": 9376,  # warmteopwekking - toegeleverde warmte, algemeen (verzamelniveau)	4	-2001140
    "51.31": 9376,  # warmteopwekking - toegeleverde warmte, water tot 140° C.	4	-2001140
    "51.32": 9376,  # warmteopwekking - toegeleverde warmte, water boven 140° C.	4	-2001140
    "51.33": 9376,  # warmteopwekking - toegeleverde warmte, stoom	4	-2001140
    "51.36": 9376,  # warmteopwekking - toegeleverde warmte, gecombineerde tapwaterverwarming	4	-2001140
    "51.40": 9376,  # warmteopwekking - warmte-krachtkoppeling, algemeen (verzamelniveau)	4	-2001140
    "51.41": 9376,  # warmteopwekking - warmte-krachtkoppeling, total-energy	4	-2001140
    "51.44": 9376,  # warmteopwekking - warmte-krachtkoppeling, schoorstenen/kanalen (niet bouwkundig)	4	-2001140
    "51.46": 9376,  # warmteopwekking - warmte-krachtkoppeling, gecombineerde tapwater verwarming	4	-2001140
    "51.49": 9376,  # warmteopwekking - warmte-krachtkoppeling, brandstoffenopslag	4	-2001140
    "51.50": 9376,  # warmteopwekking - bijzonder, algemeen (verzamelniveau)	4	-2001140
    "51.51": 9376,  # warmteopwekking - bijzonder, warmtepomp	4	-2001140
    "51.52": 9376,  # warmteopwekking - bijzonder, zonnecollectoren	4	-2001140
    "51.53": 9376,  # warmteopwekking - bijzonder, accumulatie	4	-2001140
    "51.54": 9376,  # warmteopwekking - bijzonder, aardwarmte	4	-2001140
    "51.55": 9376,  # warmteopwekking - bijzonder, kernenergie	4	-2001160
    # Mechanical Installations
    "52": 9377,  # Drainage (Afvoeren)	2	-2001160
    "52.00": 9377,  # afvoeren; algemeen	3	-2001160
    "52.10": 9377,  # afvoeren - regenwater, algemeen (verzamelniveau)	4	-2001160
    "52.11": 9377,  # afvoeren - regenwater, afvoerinstallatie - in het gebouw	4	-2001160
    "52.12": 9377,  # afvoeren - regenwater, afvoerinstallatie - buiten het gebouw	4	-2001160
    "52.16": 9377,  # afvoeren - regenwater, pompsysteem	4	-2001160
    "52.20": 9377,  # afvoeren - faecaliën, algemeen (verzamelniveau)	4	-2001160
    "52.21": 9377,  # afvoeren - faecaliën, standaard systeem /	4	-2001160
    "52.22": 9377,  # afvoeren - faecaliën, vacuümsysteem	4	-2001160
    "52.23": 9377,  # afvoeren - faecaliën, overdruksysteem	4	-2001160
    "52.26": 9377,  # afvoeren - faecaliën, pompsysteem	4	-2001160
    "52.30": 9377,  # afvoeren - afvalwater, algemeen (verzamelniveau)	4	-2001160
    "52.31": 9377,  # afvoeren - afvalwater, huishoudelijk afval	4	-2001160
    "52.32": 9377,  # afvoeren - afvalwater, bedrijfsafval	4	-2001160
    "52.36": 9377,  # afvoeren - afvalwater, pompsysteem	4	-2001160
    "52.40": 9377,  # afvoeren - gecombineerd, algemeen (verzamelniveau) 	4	-2001160
    "52.41": 9377,  # afvoeren - gecombineerd, geïntegreerd systeem 	4	-2001160
    "52.46": 9377,  # afvoeren - gecombineerd, pompsysteem	4	-2001160
    "52.50": 9377,  # afvoeren - speciaal, algemeen (verzamelniveau) 	4	-2001160
    "52.51": 9377,  # afvoeren - speciaal, chemisch verontreinigd afvalwater 	4	-2001160
    "52.52": 9377,  # afvoeren - speciaal, biologisch besmet afvalwater 	4	-2001160
    "52.53": 9377,  # afvoeren - speciaal, radio-actief besmet afvalwater 	4	-2001160
    "52.56": 9377,  # afvoeren - speciaal, pompsysteem	4	-2001160
    "52.60": 9377,  # afvoeren - vast vuil, algemeen (verzamelniveau)	4	-2001160
    "52.61": 9377,  # afvoeren - vast vuil, stortkokers 	4	-2001160
    "52.62": 9377,  # afvoeren - vast vuil, vacuümsysteem 	4	-2001160
    "52.63": 9377,  # afvoeren - vast vuil, persluchtsysteem 	4	-2001160
    "52.64": 9377,  # afvoeren - vast vuil, verdichtingssysteem 	4	-2001160
    "52.65": 9377,  # afvoeren - vast vuil, verbrandingssysteem	4	-2001160
    # Water
    "53": 9378,  # Water	2	-2001160
    "53.00": 9378,  # water; algemeen	3	-2001160
    "53.10": 9378,  # water - drinkwater, algemeen (verzamelniveau) 	4	-2001160
    "53.11": 9378,  # water - drinkwater, netaansluiting 	4	-2001160
    "53.12": 9378,  # water - drinkwater, bronaansluiting 	4	-2001160
    "53.13": 9378,  # water - drinkwater, reinwaterkelderaansluiting 	4	-2001160
    "53.14": 9378,  # water - drinkwater, drukverhoging 	4	-2001160
    "53.19": 9378,  # water - drinkwater, opslagtanks	4	-2001160
    "53.20": 9378,  # water - verwarmd tapwater, algemeen (verzamelniveau) 	4	-2001160
    "53.21": 9378,  # water - verwarmd tapwater, direct verwarmd met voorraad 	4	-2001160
    "53.22": 9378,  # water - verwarmd tapwater, indirect verwarmd met voorraad 	4	-2001160
    "53.23": 9378,  # water - verwarmd tapwater, doorstroom - direct verwarmd 	4	-2001160
    "53.24": 9378,  # water - verwarmd tapwater, doorstroom - indirect verwarmd	4	-2001160
    "53.30": 9378,  # water - bedrijfswater, algemeen (verzamelniveau) 	4	-2001160
    "53.31": 9378,  # water - bedrijfswater, onthard-watersysteem 	4	-2001160
    "53.32": 9378,  # water - bedrijfswater, demi-watersysteem 	4	-2001160
    "53.33": 9378,  # water - bedrijfswater, gedistileerd-watersysteem 	4	-2001160
    "53.34": 9378,  # water - bedrijfswater, zwembad-watersysteem	4	-2001160
    "53.40": 9378,  # water - gebruiksstoom en condens, algemeen (verzamelniveau) 	4	-2001160
    "53.41": 9378,  # water - gebruiksstoom en condens, lage-druk stoomsysteem 	4	-2001160
    "53.42": 9378,  # water - gebruiksstoom en condens, hoge -ruk stoomsysteem 	4	-2001160
    "53.44": 9378,  # water - gebruiksstoom en condens, condensverzamelsysteem	4	-2001160
    "53.50": 9378,  # water - waterbehandeling, algemeen (verzamelniveau) 	4	-2001160
    "53.51": 9378,  # water - waterbehandeling, filtratiesysteem 	4	-2001160
    "53.52": 9378,  # water - waterbehandeling, absorptiesysteem 	4	-2001160
    "53.53": 9378,  # water - waterbehandeling, ontgassingssysteem 	4	-2001160
    "53.54": 9378,  # water - waterbehandeling, destillatiesysteem	4	-2001160
    # Gas System
    "54": 9379,  # Gassen	2	-2001160
    "54.00": 9379,  # gassen; algemeen	3	-2001160
    "54.10": 9379,  # gassen - brandstof, algemeen (verzamelniveau) 	4	-2001160
    "54.11": 9379,  # gassen - brandstof, aardgasvoorziening 	4	-2001160
    "54.12": 9379,  # gassen - brandstof, butaanvoorziening 	4	-2001160
    "54.13": 9379,  # gassen - brandstof, propaanvoorziening 	4	-2001160
    "54.14": 9379,  # gassen - brandstof, LPG-voorziening	4	-2001160
    "54.20": 9379,  # gassen - perslucht en vacuüm, algemeen (verzamelniveau) 	4	-2001160
    "54.21": 9379,  # gassen - perslucht en vacuüm, persluchtvoorziening 	4	-2001160
    "54.22": 9379,  # gassen - perslucht en vacuüm, vacuümvoorziening	4	-2001160
    "54.30": 9379,  # gassen - medisch, algemeen (verzamelniveau) 	4	-2001160
    "54.31": 9379,  # gassen - medisch, zuurstofvoorziening 	4	-2001160
    "54.32": 9379,  # gassen - medisch, carbogeenvoorziening 	4	-2001160
    "54.33": 9379,  # gassen - medisch, lachgasvoorziening 	4	-2001160
    "54.34": 9379,  # gassen - medisch, koolzuurvoorziening 	4	-2001160
    "54.35": 9379,  # gassen - medisch, medische luchtvoorziening	4	-2001160
    "54.40": 9379,  # gassen - technisch, algemeen (verzamelniveau) 	4	-2001160
    "54.41": 9379,  # gassen - technisch, stikstofvoorziening 	4	-2001160
    "54.42": 9379,  # gassen - technisch, waterstofvoorziening 	4	-2001160
    "54.43": 9379,  # gassen - technisch, argonvoorziening 	4	-2001160
    "54.44": 9379,  # gassen - technisch, heliumvoorziening 	4	-2001160
    "54.45": 9379,  # gassen - technisch, acyteleenvoorziening 	4	-2001160
    "54.46": 9379,  # gassen - technisch, propaanvoorziening 	4	-2001160
    "54.47": 9379,  # gassen - technisch, koolzuurvoorziening	4	-2001160
    "54.50": 9379,  # gassen - bijzonder, algemeen (verzamelniveau) 	4	-2001160
    "54.51": 9379,  # gassen - bijzonder, voorziening - zuivere gassen 	4	-2001160
    "54.52": 9379,  # gassen - bijzonder, voorziening - menggassen	4	-2001140
    # Cooling Generator and Distribution (Koude-opwekking en distributie)
    "55": 9380,  # Koude-opwekking en distributie	2	-2001140
    "55.00": 9380,  # koude-opwekking; algemeen	3	-2001140
    "55.10": 9380,  # koude-opwekking - lokaal, algemeen (verzamelniveau) 	4	-2001140
    "55.11": 9380,  # koude-opwekking - lokaal, raamkoelers 	4	-2001140
    "55.12": 9380,  # koude-opwekking - lokaal, splitsystemen 	4	-2001140
    "55.13": 9380,  # koude-opwekking - lokaal, compactsystemen	4	-2001140
    "55.20": 9380,  # koude-opwekking - centraal, algemeen (verzamelniveau) 	4	-2001140
    "55.21": 9380,  # koude-opwekking - centraal, compressorensystemen 	4	-2001140
    "55.22": 9380,  # koude-opwekking - centraal, absorptiesystemen 	4	-2001140
    "55.23": 9380,  # koude-opwekking - centraal, grondwatersystemen 	4	-2001140
    "55.24": 9380,  # koude-opwekking - centraal, oppervlaktewatersystemen	4	-2001140
    "55.30": 9380,  # koude-opwekking - distributie, algemeen (verzamelniveau) 	4	-2001140
    "55.31": 9380,  # koude-opwekking - distributie, distributie-systemen	4	-2001140
    # Heat Distribution (Warmtedistributie)
    "56": 9381,  # 	Warmtedistributie	2	-2001140
    "56.00": 9381,  #  #warmtedistributie; algemeen 	3	-2001140
    "56.10": 9381,  #  #warmtedistributie - water, algemeen (verzamelniveau) 	4	-2001140
    "56.11": 9381,  #  #warmtedistributie - water, radiatorsystemen 	4	-2001140
    "56.12": 9381,  #  #warmtedistributie - water, convectorsystemen 	4	-2001140
    "56.13": 9381,  #  #warmtedistributie - water, vloerverwarmingssysteem	4	-2001140
    "56.20": 9381,  #  #warmtedistributie - stoom, algemeen (verzamelniveau) 	4	-2001140
    "56.21": 9381,  #  #warmtedistributie - stoom, radiatorsystemen 	4	-2001140
    "56.22": 9381,  #  #warmtedistributie - stoom, convectorsystemen 	4	-2001140
    "56.24": 9381,  #  #warmtedistributie - stoom, stralingspanelen	4	-2001140
    "56.30": 9381,  #  #warmtedistributie - lucht, algemeen (verzamelniveau) 	4	-2001140
    "56.31": 9381,  #  #warmtedistributie - lucht, directe distributiesysteem 	4	-2001140
    "56.32": 9381,  #  #warmtedistributie - lucht, systeem met stralingsoverdracht	4	-2001140
    "56.40": 9381,  #  #warmtedistributie - bijzonder, algemeen (verzamelniveau) 	4	-2001140
    "56.41": 9381,  #  #warmtedistributie - bijzonder, zonnewarmtesysteem 	4	-2001140
    "56.42": 9381,  #  #warmtedistributie - bijzonder, aardwarmtesysteem 	4	-2001140
    "56.43": 9381,  #  #warmtedistributie - bijzonder, centraal warmtepompsysteem	4	-2001140
    # Air Treatment (Luchtbehandeling)
    "57": 9382,  #   Luchtbehandeling	2	-2001140
    "57.00": 9382,  # luchtbehandeling; algemeen	3	-2001140
    "57.10": 9382,  # luchtbehandeling - natuurlijke ventilatie, algemeen (verzamelniveau) 	4	-2001140
    "57.11": 9382,  # luchtbehandeling - natuurlijke ventilatie, voorzieningen - regelbaar 	4	-2001140
    "57.12": 9382,  # luchtbehandeling - natuurlijke ventilatie, voorzieningen - niet regelbaar	4	-2001140
    "57.20": 9382,  # luchtbehandeling - lokale mechanische afzuiging, algemeen (verzamelniveau) 	4	-2001140
    "57.21": 9382,  # luchtbehandeling - lokale mechanische afzuiging, afzuiginstallatie	4	-2001140
    "57.30": 9382,  # luchtbehandeling - centrale mechanische afzuiging, algemeen (verzamelniveau) 	4	-2001140
    "57.31": 9382,  # luchtbehandeling - centrale mechanische afzuiging, afzuiginstallatie	4	-2001140
    "57.40": 9382,  # luchtbehandeling - lokale mechanische ventilatie, algemeen (verzamelniveau) 	4	-2001140
    "57.41": 9382,  # luchtbehandeling - lokale mechanische ventilatie, ventilatie-installatie	4	-2001140
    "57.50": 9382,  # luchtbehandeling - centrale mechanische ventilatie, algemeen (verzamelniveau)	4	-2001140
    "57.51": 9382,  # luchtbehandeling - centrale mechanische ventilatie, ventilatie-installatie 	4	-2001140
    "57.52": 9382,  # luchtbehandeling - centrale mechanische ventilatie, ventilatie-installatie met warmte-terugwinning	4	-2001140
    "57.60": 9382,  # luchtbehandeling - lokaal, algemeen (verzamelniveau) 	4	-2001140
    "57.61": 9382,  # luchtbehandeling - lokaal, luchtbehandelingsinstallatie	4	-2001140
    "57.70": 9382,  # luchtbehandeling - centraal, algemeen (verzamelniveau) 	4	-2001140
    "57.71": 9382,  # luchtbehandeling - centraal, luchtbehandelingsinstallatie	4	-2001140
    # Climate and Sanitation Control (Regeling Klimaat en Sanitair)
    "58": 9383,  #   Regeling klimaat en sanitair	2	-2001140
    "58.00": 9383,  #   regeling klimaat en sanitair; algemeen	3	-2001140
    "58.10": 9383,  #   regeling klimaat en sanitair - specifieke regelingen, algemeen (verzamelniveau) 	4	-2001140
    "58.11": 9383,  #   regeling klimaat en sanitair - specifieke regelingen, specifieke regeling 	4	-2001140
    "58.12": 9383,  #   regeling klimaat en sanitair - specifieke regelingen, gecombineerde regeling	4	-2001140
    "58.20": 9383,  #   regeling klimaat en sanitair - centrale melding, meting en sturing, algemeen (verzamelniveau)	4	-2001140
    "58.21": 9383,  #   regeling klimaat en sanitair - centrale melding, meting en sturing, specifieke regeling	4	-2001140
    "58.22": 9383,  #   regeling klimaat en sanitair - centrale melding, meting en sturing, gecombineerde regeling	4	-2001040
    # Electrical Installations (Installaties Elektrotechnisch)
    "6": 9384,  # -	INSTALLATIES ELEKTROTECHNISCH	1	-2000151
    "61": 9384,  #   Centrale electrotechnische voorzieningen	2	-2001040
    "61.00": 9384,  #   centrale elektrotechnische voorzieningen; algemeen	3	-2001040
    "61.10": 9384,  #   centrale elektrotechnische voorzieningen - energie, noodstroom, algemeen (verzamelniveau) 	4	-2001040
    "61.11": 9384,  #   centrale elektrotechnische voorzieningen - energie, noodstroom, eigen energieopwekking	4	-2001040
    "61.20": 9384,  #   centrale elektrotechnische voorzieningen - aarding, algemeen (verzamelniveau)	4	-2001040
    "61.21": 9384,  #   centrale elektrotechnische voorzieningen - aarding, veiligheidsaarding	4	-2001040
    "61.22": 9384,  #   centrale elektrotechnische voorzieningen - aarding, medische aarding	4	-2001040
    "61.23": 9384,  #   centrale elektrotechnische voorzieningen - aarding, speciale aarding	4	-2001040
    "61.24": 9384,  #   centrale elektrotechnische voorzieningen - aarding, statische elektriciteit	4	-2001040
    "61.25": 9384,  #   centrale elektrotechnische voorzieningen - aarding, bliksemafleiding	4	-2001040
    "61.26": 9384,  #   centrale elektrotechnische voorzieningen - aarding, potentiaalvereffening	4	-2001040
    "61.30": 9384,  #   centrale elektrotechnische voorzieningen - kanalisatie, algemeen (verzamelniveau) 	4	-2001040
    "61.31": 9384,  #   centrale elektrotechnische voorzieningen - kanalisatie, t.b.v. installaties voor hoge spanning 	4	-2001040
    "61.32": 9384,  #   centrale elektrotechnische voorzieningen - kanalisatie, t.b.v. installaties voor lage spanning	4	-2001040
    "61.33": 9384,  #   centrale elektrotechnische voorzieningen - kanalisatie, t.b.v. installaties voor communicatie of beveiliging	4	-2001040
    "61.40": 9384,  #   centrale elektrotechnische voorzieningen - energie, hoge spanning, algemeen 	4	-2001040
    "61.41": 9384,  #   centrale elektrotechnische voorzieningen - energie, hoge spanning, 1 Kv en hoger	4	-2001040
    "61.50": 9384,  #   centrale elektrotechnische voorzieningen - energie, lage spanning, algemeen 	4	-2001040
    "61.51": 9384,  #   centrale elektrotechnische voorzieningen - energie, lage spanning, lager dan 1 Kv en hoger dan 100 V	4	-2001040
    "61.60": 9384,  #   centrale elektrotechnische voorzieningen - energie, zeer lage spanning, algemeen 	4	-2001040
    "61.61": 9384,  # 	centrale elektrotechnische voorzieningen - energie, zeer lage spanning, lager dan 100 V	4	-2001040
    "61.70": 9384,  # 	centrale elektrotechnische voorzieningen - bliksemafleiding, algemeen 	4	-2001040
    "61.71": 9384,  # 	centrale elektrotechnische voorzieningen - bliksemafleiding, volgens NEN 1014	4	-2001040
    # Power Current (Krachtstroom)
    "62": 9385,  #   Krachtstroom	2	-2001040
    "62.00": 9385,  # krachtstroom; algemeen	3	-2001040
    "62.10": 9385,  # krachtstroom - hoogspanning, algemeen (verzamelniveau) 	4	-2001040
    "62.11": 9385,  # krachtstroom - hoogspanning, 1 t/m 3 Kv 	4	-2001040
    "62.12": 9385,  # krachtstroom - hoogspanning, boven 3 Kv	4	-2001040
    "62.20": 9385,  # krachtstroom - laagspanning, onbewaakt, algemeen (verzamelniveau) 	4	-2001040
    "62.21": 9385,  # krachtstroom - laagspanning, onbewaakt, 220/230 V - 380 V 	4	-2001040
    "62.22": 9385,  # krachtstroom - laagspanning, onbewaakt, 380 V - 660 V 	4	-2001040
    "62.23": 9385,  # krachtstroom - laagspanning, onbewaakt, 660 V - 1 Kv	4	-2001040
    "62.30": 9385,  # krachtstroom - laagspanning, bewaakt, algemeen (verzamelniveau) 	4	-2001040
    "62.31": 9385,  # krachtstroom - laagspanning, bewaakt, 220/230 V - 380 V 	4	-2001040
    "62.32": 9385,  # krachtstroom - laagspanning, bewaakt, 380 V - 660 V 	4	-2001040
    "62.33": 9385,  # krachtstroom - laagspanning, bewaakt, 660 V - l kV	4	-2001040
    "62.40": 9385,  # krachtstroom - laagspanning, gestabiliseerd, algemeen (verzamelniveau) 	4	-2001040
    "62.41": 9385,  # krachtstroom - laagspanning, gestabiliseerd, 220/230 V - 380 V 	4	-2001040
    "62.42": 9385,  # krachtstroom - laagspanning, gestabiliseerd, 380 V - 660 V 	4	-2001040
    "62.43": 9385,  # krachtstroom - laagspanning, gestabiliseerd, 660 V - 1 Kv	4	-2001040
    "62.50": 9385,  # krachtstroom - laagspanning, gecompenseerd, algemeen (verzamelniveau) 	4	-2001040
    "62.51": 9385,  # krachtstroom - laagspanning, gecompenseerd, 220/230 V - 380 V 	4	-2001040
    "62.52": 9385,  # krachtstroom - laagspanning, gecompenseerd, 380 V - 660 V 	4	-2001120
    "62.53": 9385,  # krachtstroom - laagspanning, gecompenseerd, 660 V - 1 Kv	4	-2001120
    # Lighting (Verlichting)
    "63": 9386,  #   Verlichting	2	-2001120
    "63.00": 9386,  #   verlichting; algemeen	3	-2001120
    "63.10": 9386,  #   verlichting - standaard, onbewaakt, algemeen (verzamelniveau) 	4	-2001120
    "63.11": 9386,  #   verlichting - standaard, onbewaakt, 220/230 V 	4	-2001120
    "63.12": 9386,  #   verlichting - standaard, onbewaakt, 115 V 	4	-2001120
    "63.13": 9386,  #   verlichting - standaard, onbewaakt, 42 V 	4	-2001120
    "63.14": 9386,  #   verlichting - standaard, onbewaakt, 24 V	4	-2001120
    "63.20": 9386,  #   verlichting - calamiteiten, decentraal gevoed, algemeen (verzamelniveau)	4	-2001120
    "63.23": 9386,  #   verlichting - calamiteiten, decentraal gevoed, 42 V	4	-2001120
    "63.24": 9386,  #   verlichting - calamiteiten, decentraal gevoed, 24 V	4	-2001120
    "63.30": 9386,  #   verlichting - bijzonder, onbewaakt, algemeen (verzamelniveau) 	4	-2001120
    "63.31": 9386,  #   verlichting - bijzonder, onbewaakt, 220/230 V 	4	-2001120
    "63.32": 9386,  #   verlichting - bijzonder, onbewaakt, 115 V 	4	-2001120
    "63.33": 9386,  #   verlichting - bijzonder, onbewaakt, 42 V 	4	-2001120
    "63.34": 9386,  #   verlichting - bijzonder, onbewaakt, 24 V	4	-2001120
    "63.40": 9386,  #   verlichting - standaard, bewaakt, algemeen (verzamelniveau) 	4	-2001120
    "63.41": 9386,  #   verlichting - standaard, bewaakt, 220/230 V 	4	-2001120
    "63.42": 9386,  #   verlichting - standaard, bewaakt, 115 V 	4	-2001120
    "63.43": 9386,  #   verlichting - standaard, bewaakt, 42 V 	4	-2001120
    "63.44": 9386,  #   verlichting - standaard, bewaakt, 24 V	4	-2001120
    "63.50": 9386,  #   verlichting - calamiteiten, centraal gevoed, algemeen (verzamelniveau) 	4	-2001120
    "63.51": 9386,  #   verlichting - calamiteiten, centraal gevoed, 220/230 V 	4	-2001120
    "63.52": 9386,  #   verlichting - calamiteiten, centraal gevoed, 115 V 	4	-2001120
    "63.53": 9386,  #   verlichting - calamiteiten, centraal gevoed, 42 V 	4	-2001120
    "63.54": 9386,  #   verlichting - calamiteiten, centraal gevoed, 24 V	4	-2001120
    "63.60": 9386,  #   verlichting - bijzonder, bewaakt, algemeen (verzamelniveau) 	4	-2001120
    "63.61": 9386,  #   verlichting - bijzonder, bewaakt, 220/230 V 	4	-2001120
    "63.62": 9386,  #   verlichting - bijzonder, bewaakt, 115 V 	4	-2001120
    "63.63": 9386,  # 	verlichting - bijzonder, bewaakt, 42 V 	4	-2001120
    "63.64": 9386,  # 	verlichting - bijzonder, bewaakt, 24 V	4	-2001120
    "63.70": 9386,  # 	verlichting - reclame, algemeen (verzamelniveau)	4	-2001120
    "63.71": 9386,  # 	verlichting - reclame, 220/230 V	4	-2001120
    "63.72": 9386,  # 	verlichting - reclame, 115 V	4	-2001120
    "63.73": 9386,  # 	verlichting - reclame, 42 V	4	-2001120
    "63.74": 9386,  # 	verlichting - reclame, 24 V	4	-2001060
    "63.75": 9386,  # 	verlichting - reclame, 1Kv en hoger	4	-2001060
    # Communication (Communicatie)
    "64": 9387,  # 	Communicatie	2	-2001060
    "64.00": 9387,  # communicatie; algemeen	3	-2001060
    "64.10": 9387,  # communicatie - overdracht van signalen, algemeen (verzamelniveau) 	4	-2001060
    "64.11": 9387,  # communicatie - overdracht van signalen, algemene signaleringen 	4	-2001060
    "64.12": 9387,  # communicatie - overdracht van signalen, algemene personenoproep 	4	-2001060
    "64.13": 9387,  # communicatie - overdracht van signalen, tijdsignalering 	4	-2001060
    "64.14": 9387,  # communicatie - overdracht van signalen, aanwezigheid-/beletsignalering	4	-2001060
    "64.20": 9387,  # communicatie - overdracht van geluid/spraak, algemeen (verzamelniveau) 	4	-2001060
    "64.21": 9387,  # communicatie - overdracht van geluid/spraak, telefoon 	4	-2001060
    "64.22": 9387,  # communicatie - overdracht van geluid/spraak, intercom 	4	-2001060
    "64.23": 9387,  # communicatie - overdracht van geluid/spraak, radio/mobilofoon 	4	-2001060
    "64.24": 9387,  # communicatie - overdracht van geluid/spraak, geluiddistributie 	4	-2001060
    "64.25": 9387,  # communicatie - overdracht van geluid/spraak, vertaalsystemen 	4	-2001060
    "64.26": 9387,  # communicatie - overdracht van geluid/spraak, conferentiesystemen	4	-2001060
    "64.30": 9387,  # communicatie - overdracht van beelden, algemeen (verzamelniveau) 	4	-2001060
    "64.31": 9387,  # communicatie - overdracht van beelden, gesloten televisiecircuits 	4	-2001060
    "64.32": 9387,  # communicatie - overdracht van beelden, beeldreproductie 	4	-2001060
    "64.33": 9387,  # communicatie - overdracht van beelden, film/dia/overhead	4	-2001060
    "64.40": 9387,  # communicatie - overdracht van data, algemeen (verzamelniveau) 	4	-2001060
    "64.41": 9387,  # communicatie - overdracht van data, gesloten datanet 	4	-2001060
    "64.42": 9387,  # communicatie - overdracht van data, openbaar datanet	4	-2001060
    "64.50": 9387,  # communicatie - geïntegreerde systemen, algemeen (verzamelniveau) 	4	-2001060
    "64.51": 9387,  # communicatie - geïntegreerde systemen, gesloten netwerken 	4	-2001060
    "64.52": 9387,  # communicatie - geïntegreerde systemen, openbare netwerken	4	-2001060
    "64.60": 9387,  # communicatie - antenne-inrichtingen, algemeen	4	-2001060
    # Security (Beveiliging)
    "65": 9388,  # 	Beveiliging	2	-2001060
    "65.00": 9388,  # beveiliging; algemeen	3	-2001060
    "65.10": 9388,  # beveiliging - brand, algemeen (verzamelniveau) 	4	-2001060
    "65.11": 9388,  # beveiliging - brand, detectie en alarmering 	4	-2001060
    "65.12": 9388,  # beveiliging - brand, deurgrendelingen en -ontgrendelingen 	4	-2001060
    "65.13": 9388,  # beveiliging - brand, brandbestrijding	4	-2001060
    "65.20": 9388,  # beveiliging - braak, algemeen (verzamelniveau) 	4	-2001060
    "65.21": 9388,  # beveiliging - braak, detectie en alarmering 	4	-2001060
    "65.22": 9388,  # beveiliging - braak, toegangscontrole	4	-2001060
    "65.30": 9388,  # beveiliging - overlast, detectie en alarmering, algemeen (verzamelniveau) 	4	-2001060
    "65.31": 9388,  # beveiliging - overlast, detectie en alarmering, zonweringsinstallatie 	4	-2001060
    "65.32": 9388,  # beveiliging - overlast, detectie en alarmering, elektromagnetische voorzieningen	4	-2001060
    "65.33": 9388,  # beveiliging - overlast, detectie en alarmering, elektromagnetische voorzieningen 	4	-2001060
    "65.34": 9388,  # beveiliging - overlast, detectie en alarmering, overspanningsbeveiliging 	4	-2001060
    "65.35": 9388,  # beveiliging - overlast, detectie en alarmering, gassenbeveiliging 	4	-2001060
    "65.36": 9388,  # beveiliging - overlast, detectie en alarmering, vloeistofbeveiliging 	4	-2001060
    "65.37": 9388,  # beveiliging - overlast, detectie en alarmering, stralingsbeveiliging 	4	-2001060
    "65.39": 9388,  # beveiliging - overlast, detectie en alarmering, overige beveiligingen	4	-2001060
    "65.40": 9388,  # beveiliging - sociale alarmering, algemeen (verzamelniveau) 	4	-2001060
    "65.41": 9388,  # beveiliging - sociale alarmering, nooddetectie - gesloten systemen 	4	-2001060
    "65.42": 9388,  # beveiliging - sociale alarmering, nooddetectie - open systemen	4	-2001350
    "65.50": 9388,  # beveiliging - milieu-overlast, detectie en alarmering, algemeen (verzamelniveau) 	4	-2001350
    # Transport
    "66": 9389,  #   Transport	2	-2001350
    "66.00": 9389,  #   transport; algemeen	3	-2001350
    "66.10": 9389,  #   transport - liften en heftableau's, algemeen (verzamelniveau) 	4	-2001350
    "66.11": 9389,  #   transport - liften en heftableau's, elektrische liften 	4	-2001350
    "66.12": 9389,  #   transport - liften en heftableau's, hydraulische liften 	4	-2001350
    "66.13": 9389,  #   transport - liften en heftableau's, trapliften 	4	-2001350
    "66.14": 9389,  #   transport - liften en heftableau's, heftableau's	4	-2001350
    "66.20": 9389,  #   transport - roltrappen en rolpaden, algemeen (verzamelniveau) 	4	-2001350
    "66.21": 9389,  #   transport - roltrappen en rolpaden, roltrappen 	4	-2001350
    "66.22": 9389,  #   transport - roltrappen en rolpaden, rolpaden	4	-2001350
    "66.30": 9389,  #   transport - goederen, algemeen (verzamelniveau) 	4	-2001350
    "66.31": 9389,  #   transport - goederen, goederenliften 	4	-2001350
    "66.32": 9389,  #   transport - goederen, goederenheffers 	4	-2001350
    "66.33": 9389,  #   transport - goederen, baantransportmiddelen 	4	-2001350
    "66.34": 9389,  #   transport - goederen, bandtransportmiddelen 	4	-2001350
    "66.35": 9389,  #   transport - goederen, baktransportmiddelen 	4	-2001350
    "66.36": 9389,  #   transport - goederen, hijswerktuigen 	4	-2001350
    "66.37": 9389,  #   transport - goederen, vrije-baan-transportvoertuigen	4	-2001350
    "66.40": 9389,  #   transport - documenten, algemeen (verzamelniveau) 	4	-2001350
    "66.41": 9389,  #   transport - documenten, buizenpost 	4	-2001350
    "66.42": 9389,  #   transport - documenten, railcontainer banen 	4	-2001060
    "66.44": 9389,  #   transport - documenten, bandtransportmiddelen	4	-2001060
    # Building Management Provisions (Gebouwbeheervoorzieningen)
    "67": 9390,  #   Gebouwbeheervoorzieningen	2	-2001060
    "67.00": 9390,  #   gebouwbeheervoorzieningen; algemeen	3	-2001060
    "67.10": 9390,  #   gebouwbeheervoorzieningen - bediening en signalering, algemeen (verzamelniveau) 	4	-2001060
    "67.11": 9390,  #   gebouwbeheervoorzieningen - bediening en signalering, elektrotechnische systemen 	4	-2001060
    "67.12": 9390,  #   gebouwbeheervoorzieningen - bediening en signalering, optische systemen 	4	-2001060
    "67.13": 9390,  #   gebouwbeheervoorzieningen - bediening en signalering, pneumatische systemen 	4	-2001060
    "67.14": 9390,  #   gebouwbeheervoorzieningen - bediening en signalering, geïntegreerde systemen	4	-2001060
    "67.20": 9390,  #   gebouwbeheervoorzieningen - gebouwautomatisering, algemeen (verzamelniveau) 	4	-2001060
    "67.21": 9390,  #   gebouwbeheervoorzieningen - gebouwautomatisering, elektrotechnische systemen 	4	-2001060
    "67.22": 9390,  #   gebouwbeheervoorzieningen - gebouwautomatisering, optische systemen 	4	-2001060
    "67.23": 9390,  #   gebouwbeheervoorzieningen - gebouwautomatisering, pneumatische systemen 	4	-2001060
    "67.24": 9390,  #   gebouwbeheervoorzieningen - gebouwautomatisering, geïntegreerde systemen	4	-2001060
    "67.30": 9390,  #   gebouwbeheervoorzieningen - regelinstallaties klimaat en sanitair (op afstand), algemeen (verzamelniveau)	4	-2001060
    "67.31": 9390,  #   gebouwbeheervoorzieningen - regelinstallaties klimaat en sanitair (op afstand), elektrotechnische systemen	4	-2001060
    "67.32": 9390,  #   gebouwbeheervoorzieningen - regelinstallaties klimaat en sanitair (op afstand), optische systemen	4	-2001060
    "67.33": 9390,  #   gebouwbeheervoorzieningen - regelinstallaties klimaat en sanitair (op afstand), pneumatische systemen	4	-2001350
    "67.34": 9390,  #   gebouwbeheervoorzieningen - regelinstallaties klimaat en sanitair (op afstand), geïntegreerde systemen	4	-2001350
    # Fixed Provisions (Vaste Voorzieningen)
    "7": 9368,  # -	VASTE VOORZIENINGEN	1	-2000151
    "71": 9368,  #   Vaste verkeersvoorzieningen	2	-2001350
    "71.00": 9368,  #   vaste verkeersvoorzieningen; algemeen	3	-2001350
    "71.10": 9368,  #   vaste verkeersvoorzieningen - standaard, algemeen (verzamelniveau) 	4	-2001350
    "71.11": 9368,  #   vaste verkeersvoorzieningen - standaard, meubileringen 	4	-2001350
    "71.12": 9368,  #   vaste verkeersvoorzieningen - standaard, bewegwijzeringen 	4	-2001350
    "71.13": 9368,  #   vaste verkeersvoorzieningen - standaard, kunstwerken 	4	-2001350
    "71.14": 9368,  #   vaste verkeersvoorzieningen - standaard, decoraties e.d.	4	-2001350
    "71.20": 9368,  #   vaste verkeersvoorzieningen - bijzonder, algemeen (verzamelniveau)	4	-2001350
    "71.21": 9368,  #   vaste verkeersvoorzieningen - bijzonder, meubileringen	4	-2001350
    "71.22": 9368,  #   vaste verkeersvoorzieningen - bijzonder, bewegwijzeringen	4	-2001350
    "71.23": 9368,  #   vaste verkeersvoorzieningen - bijzonder, specifieke voorz. (o.a. loopleuningen)	4	-2001350
    # Fixed User Provisions (Vaste gebruikersvoorzieningen)
    "72": 9392,  #   Vaste gebruikersvoorzieningen	2	-2001350
    "72.00": 9392,  #   vaste gebruikersvoorzieningen; algemeen	3	-2001350
    "72.10": 9392,  #   vaste gebruikersvoorzieningen - standaard, algemeen (verzamelniveau) 	4	-2001350
    "72.11": 9392,  #   vaste gebruikersvoorzieningen - standaard, meubilering 	4	-2001350
    "72.12": 9392,  #   vaste gebruikersvoorzieningen - standaard, lichtweringen 	4	-2001350
    "72.13": 9392,  #   vaste gebruikersvoorzieningen - standaard, gordijnvoorzieningen	4	-2001350
    "72.14": 9392,  #   vaste gebruikersvoorzieningen - standaard, beschermende voorzieningen	4	-2001350
    "72.20": 9392,  #   vaste gebruikersvoorzieningen - bijzonder, algemeen (verzamelniveau)	4	-2001350
    "72.21": 9392,  #   vaste gebruikersvoorzieningen - bijzonder, meubilering voor specifieke functie-doeleinden	4	-2001000
    "72.22": 9392,  #   vaste gebruikersvoorzieningen - bijzonder, instrumenten/apparatuur	4	-2001000
    # Fixed Kitchen Provisions (Vaste Keukenvoorzieningen)
    "73": 9393,  # 	Vaste keukenvoorzieningen	2	-2001000
    "73.00": 9393,  #   vaste keukenvoorzieningen; algemeen	3	-2001000
    "73.10": 9393,  #   vaste keukenvoorzieningen - standaard, algemeen (verzamelniveau) 	4	-2001000
    "73.11": 9393,  #   vaste keukenvoorzieningen - standaard, keukenmeubilering 	4	-2001000
    "73.12": 9393,  #   vaste keukenvoorzieningen - standaard, keukenapparatuur	4	-2001000
    "73.20": 9393,  #   vaste keukenvoorzieningen - bijzonder, algemeen (verzamelniveau) 	4	-2001000
    "73.21": 9393,  #   vaste keukenvoorzieningen - bijzonder, keukenmeubilering 	4	-2000160
    "73.22": 9393,  #   vaste keukenvoorzieningen - bijzonder, keukenapparatuur	4	-2000160
    # Fixed Sanitary Provisions (Vaste Sanitarie Voorzieningen)
    "74": 9378,  #   Vaste sanitaire voorzieningen	2	-2000160
    "74.00": 9378,  #   vaste sanitaire voorzieningen; algemeen	3	-2000160
    "74.10": 9378,  #   vaste sanitaire voorzieningen - standaard, algemeen (verzamelniveau) 	4	-2000160
    "74.11": 9378,  #   vaste sanitaire voorzieningen - standaard, sanitaire toestellen - normaal 	4	-2000160
    "74.12": 9378,  #   vaste sanitaire voorzieningen - standaard, sanitaire toestellen - aangepast 	4	-2000160
    "74.13": 9378,  #   vaste sanitaire voorzieningen - standaard, accessoires	4	-2000160
    "74.20": 9378,  #   vaste sanitaire voorzieningen - bijzonder, algemeen (verzamelniveau)	4	-2000160
    "74.21": 9378,  #   vaste sanitaire voorzieningen - bijzonder, sanitaire toestellen voor bijzondere toepassing	4	-2001350
    "74.22": 9378,  #   vaste sanitaire voorzieningen - bijzonder, ingebouwde sanitaire voorzieningen	4	-2001350
    # Fixed Maintenance Provisions (Vaste Onderhoudsvoorzieningen)
    "75": 9368,  #   Vaste onderhoudsvoorzieningen	2	-2001350
    "75.00": 9368,  #   vaste onderhoudsvoorzieningen; algemeen 	3	-2001350
    "75.10": 9368,  #   vaste onderhoudsvoorzieningen - standaard, algemeen (verzamelniveau) 	4	-2001350
    "75.11": 9368,  #   vaste onderhoudsvoorzieningen - standaard, gebouwonderhoudsvoorzieningen	4	-2001350
    "75.12": 9368,  #   vaste onderhoudsvoorzieningen - standaard, interieur onderhoudsvoorzieningen	4	-2001350
    "75.13": 9368,  #   vaste onderhoudsvoorzieningen - standaard, gevelonderhoudsvoorzieningen	4	-2001350
    "75.20": 9368,  #   vaste onderhoudsvoorzieningen - bijzonder, algemeen (verzamelniveau)	4	-2001350
    "75.21": 9368,  #   vaste onderhoudsvoorzieningen - bijzonder, gebouwonderhoudsvoorzieningen	4	-2001350
    "75.22": 9368,  #   vaste onderhoudsvoorzieningen - bijzonder, interieur onderhoudsvoorzieningen	4	-2001350
    "75.23": 9368,  #   vaste onderhoudsvoorzieningen - bijzonder, gemechaniseerde gevelonderhoudsvoorzieningen	4	-2001350
    # Fixed Storage Provisions (Vaste Opslagvoorzieningen)
    "76": 9368,  #   Vaste opslagvoorzieningen	2	-2001350
    "76.00": 9368,  #   vaste opslagvoorzieningen; algemeen	3	-2001350
    "76.10": 9368,  #   vaste opslagvoorzieningen - standaard, algemeen (verzamelniveau) 	4	-2001350
    "76.11": 9368,  #   vaste opslagvoorzieningen - standaard, meubileringen	4	-2001350
    "76.20": 9368,  #   vaste opslagvoorzieningen - bijzonder, algemeen (verzamelniveau) 	4	-2001350
    "76.21": 9368,  #   vaste opslagvoorzieningen - bijzonder, gemechaniseerde voorzieningen	4	-2001350
    "76.22": 9368,  #   vaste opslagvoorzieningen - bijzonder, specifieke voorzieningen	4	-2001350
    # Loose Inventory (Loose Inventaris)
    "8": 9368,  # -	LOSSE INVENTARIS	1	-2000151
    "81": 9368,  #   Losse verkeersinventaris	2	-2001350
    "81.00": 9368,  #   losse verkeersinventaris; algemeen	3	-2001350
    "81.10": 9368,  #   losse verkeersinventaris - standaard, algemeen (verzamelniveau) 	4	-2001350
    "81.11": 9368,  #   losse verkeersinventaris - standaard, meubilering 	4	-2001350
    "81.12": 9368,  #   losse verkeersinventaris - standaard, bewegwijzering 	4	-2001350
    "81.13": 9368,  #   losse verkeersinventaris - standaard, kunstwerken 	4	-2001350
    "81.14": 9368,  #   losse verkeersinventaris - standaard, decoraties e.d.	4	-2001350
    "81.20": 9368,  #   losse verkeersinventaris - bijzonder, algemeen (verzamelniveau)	4	-2001350
    "81.21": 9368,  #   losse verkeersinventaris - bijzonder, meubilering	4	-2001350
    "81.22": 9368,  #   losse verkeersinventaris - bijzonder, bewegwijzering	4	-2001350
    "81.23": 9368,  #   losse verkeersinventaris - bijzonder, specifieke voorzieningen (o.a. avalbakken)	4	-2001350
    # Loose User Inventory (Loose gebruikersinventaris)
    "82": 9392,  #   Losse gebruikersinventaris	2	-2001350
    "82.00": 9392,  #   losse gebruikersinventaris; algemeen	3	-2001350
    "82.10": 9392,  #   losse gebruikersinventaris - standaard, algemeen (verzamelniveau) 	4	-2001350
    "82.11": 9392,  #   losse gebruikersinventaris - standaard, meubilering 	4	-2001350
    "82.12": 9392,  #   losse gebruikersinventaris - standaard, lichtweringen/verduisteringen 	4	-2001350
    "82.13": 9392,  #   losse gebruikersinventaris - standaard, stofferingen	4	-2001350
    "82.20": 9392,  #   losse gebruikersinventaris - bijzonder, algemeen (verzamelniveau)	4	-2001350
    "82.21": 9392,  #   losse gebruikersinventaris - bijzonder, meubilering voor specifieke functie-doeleinden	4	-2001000
    "82.22": 9392,  #   losse gebruikersinventaris - bij zonder, instrumenten/apparatuur	4	-2001000
    # Loose Kitchen Inventory (Loose Keukeninventaris)
    "83": 9368,  #   Losse keukeninventaris	2	-2001000
    "83.00": 9368,  #   losse keukeninventaris; algemeen	3	-2001000
    "83.10": 9368,  #   losse keukeninventaris - standaard, algemeen (verzamelniveau) 	4	-2001000
    "83.11": 9368,  #   losse keukeninventaris - standaard, keukenmeubilering 	4	-2001000
    "83.12": 9368,  #   losse keukeninventaris - standaard, keukenapparatuur 	4	-2001000
    "83.13": 9368,  #   losse keukeninventaris - standaard, kleine keukeninventaris	4	-2001000
    "83.20": 9368,  #   losse keukeninventaris - bijzonder, algemeen (verzamelniveau) 	4	-2001000
    "83.21": 9368,  #   losse keukeninventaris - bijzonder, keukeninrichting 	4	-2001000
    "83.22": 9368,  #   losse keukeninventaris - bijzonder, keukenapparatuur 	4	-2001000
    "83.23": 9368,  #   losse keukeninventaris - bijzonder, kleine keukeninventaris 	4	-2001160
    "83.24": 9368,  #   losse keukeninventaris - bijzonder, transportmiddelen	4	-2001160
    # Loose Sanitary Inventory (Losse sanitarie inventaris)
    "84": 9378,  #   Losse sanitaire inventaris	2	-2001160
    "84.00": 9378,  #   losse sanitaire inventaris; algemeen	3	-2001160
    "84.10": 9378,  #   losse sanitaire inventaris - standaard, algemeen (verzamelniveau) 	4	-2001160
    "84.11": 9378,  #   losse sanitaire inventaris - standaard, afvalvoorzieningen	4	-2001160
    "84.12": 9378,  #   losse sanitaire inventaris - standaard, voorzieningen t.b.v. hygiëne 	4	-2001160
    "84.13": 9378,  #   losse sanitaire inventaris - standaard, accessoires	4	-2001160
    "84.20": 9378,  #   losse sanitaire inventaris - bijzonder, algemeen (verzamelniveau) 	4	-2001350
    "84.21": 9378,  #   losse sanitaire inventaris - bijzonder, sanitaire toestellen voor bijzondere toepassing	4	-2001350
    # Loose Cleaning Inventory (Loose schoonmaakinventaris)
    "85": 9368,  #   Losse schoonmaakinventaris	2	-2001350
    "85.00": 9368,  #   losse schoonmaakinventaris; algemeen	3	-2001350
    "85.10": 9368,  #   losse schoonmaakinventaris -standaard, algemeen (verzamelniveau) 	4	-2001350
    "85.11": 9368,  #   losse schoonmaakinventaris - standaard, schoonmaakapparatuur 	4	-2001350
    "85.12": 9368,  #   losse schoonmaakinventaris - standaard, vuilopslag 	4	-2001350
    "85.13": 9368,  #   losse schoonmaakinventaris - standaard, vuiltransport	4	-2001350
    "85.20": 9368,  #   losse schoonmaakinventaris - bijzonder, algemeen (verzamelniveau) 	4	-2001350
    "85.21": 9368,  #   losse schoonmaakinventaris - bijzonder, schoonmaakapparatuur 	4	-2001350
    "85.22": 9368,  #   losse schoonmaakinventaris - bijzonder, vuilopslag 	4	-2001350
    "85.23": 9368,  #   losse schoonmaakinventaris - bijzonder, vuiltransport	4	-2001350
    # Loose Storage Inventory (Loose opslaginventaris)
    "86": 9368,  #   Losse opslaginventaris	2	-2001350
    "86.00": 9368,  #   losse opslaginventaris; algemeen	3	-2001350
    "86.10": 9368,  #   losse opslaginventaris - standaard, algemeen (verzamelniveau) 	4	-2001350
    "86.11": 9368,  #   losse opslaginventaris - standaard, meubileringen	4	-2001350
    "86.20": 9368,  #   losse opslaginventaris - bijzonder, algemeen (verzamelniveau) 	4	-2001350
    "86.21": 9368,  #   losse opslaginventaris - bijzonder, gemechaniseerde voorzieningen	4	-2001260
    "86.22": 9368,  #   losse opslaginventaris - bijzonder, specifieke voorzieningen	4	-2001260
    # Terrain (Terrein)
    "9": 9353,  # -	TERREIN	1	-2000151
    "90": 9353,  #   Terrein	2	-2001260
    "90.00": 9353,  #   terrein; algemeen	3	-2001260
    "90.10": 9353,  #   terrein - grondvoorzieningen, algemeen (verzamelniveau) 	4	-2001260
    "90.11": 9353,  #   terrein - grondvoorzieningen, verwijderen opstakels 	4	-2001260
    "90.12": 9353,  #   terrein - grondvoorzieningen, grondwaterverlagingen 	4	-2001260
    "90.13": 9353,  #   terrein - grondvoorzieningen, drainagevoorz.	4	-2001260
    "90.20": 9353,  #   terrein - opstallen, algemeen (verzamelniveau) 	4	-2001260
    "90.21": 9353,  #   terrein - opstallen, gebouwtjes met speciale functie 	4	-2001260
    "90.22": 9353,  #   terrein - opstallen, overkappingen	4	-2001260
    "90.30": 9353,  #   terrein - omheiningen, algemeen (verzamelniveau) 	4	-2001260
    "90.31": 9353,  #   terrein - omheiningen, muren	4	-2001260
    "90.32": 9353,  #   terrein - omheiningen, hekwerken 	4	-2001260
    "90.33": 9353,  #   terrein - omheiningen, overige afscheidingen 	4	-2001260
    "90.34": 9353,  #   terrein - omheiningen, toegangen	4	-2001260
    "90.40": 9353,  #   terrein - terreinafwerkingen, algemeen (verzamelniveau) 	4	-2001260
    "90.41": 9353,  #   terrein - terreinafwerkingen, verhardingen 	4	-2001260
    "90.42": 9353,  #   terrein - terreinafwerkingen, beplantingen 	4	-2001260
    "90.43": 9353,  #   terrein - terreinafwerkingen, waterpartijen 	4	-2001260
    "90.44": 9353,  #   terrein - terreinafwerkingen, keerwanden/balustrades 	4	-2001260
    "90.45": 9353,  #   terrein - terreinafwerkingen, pergola's	4	-2001260
    "90.50": 9353,  #   terrein - terreinvoorzieningen - werktuigbouwkundig, algemeen (verzamelniveau) 	4	-2001260
    "90.51": 9353,  #   terrein - terreinvoorzieningen - werktuigbouwkundig, verwarmingsvoorzieningen	4	-2001260
    "90.52": 9353,  #   terrein - terreinvoorzieningen - werktuigbouwkundig, afvoervoorzieningen	4	-2001260
    "90.53": 9353,  #   terrein - terreinvoorzieningen - werktuigbouwkundig, watervoorzieningen	4	-2001260
    "90.54": 9353,  #   terrein - terreinvoorzieningen - werktuigbouwkundig, gasvoorzieningen	4	-2001260
    "90.55": 9353,  #   terrein - terreinvoorzieningen - werktuigbouwkundig, koudeopwekkingsvoorzieningen	4	-2001260
    "90.56": 9353,  #   terrein - terreinvoorzieningen - werktuigbouwkundig, warmtedistributievoorzieningen	4	-2001260
    "90.57": 9353,  #   terrein - terreinvoorzieningen - werktuigbouwkundig, luchtbehandelingsvoorzieningen	4	-2001260
    "90.58": 9353,  #   terrein - terreinvoorzieningen - werktuigbouwkundig, regelingvoorzieningen	4	-2001260
    "90.60": 9353,  #   terrein - terreinvoorzieningen - elektrotechnisch, algemeen (verzamelniveau) 	4	-2001260
    "90.61": 9353,  #   terrein - terreinvoorzieningen - elektrotechnisch, elektrotechnische en aardingsvoorzieningen	4	-2001260
    "90.62": 9353,  #   terrein - terreinvoorzieningen - elektrotechnisch, krachtvoorzieningen	4	-2001260
    "90.63": 9353,  #   terrein - terreinvoorzieningen - elektrotechnisch, lichtvoorzieningen	4	-2001260
    "90.64": 9353,  #   terrein - terreinvoorzieningen - elektrotechnisch, communicatievoorzieningen	4	-2001260
    "90.65": 9353,  #   terrein - terreinvoorzieningen - elektrotechnisch, beveiligingsvoorzieningen	4	-2001260
    "90.66": 9353,  #   terrein - terreinvoorzieningen - elektrotechnisch, transportvoorzieningen	4	-2001260
    "90.67": 9353,  #   terrein - terreinvoorzieningen - elektrotechnisch, beheervoorzieningen	4	-2001260
    "90.70": 9353,  #   terrein - terreininrichtingen - standaard, algemeen (verzamelniveau) 	4	-2001260
    "90.71": 9353,  #   terrein - terreininrichtingen - standaard, terreinmeubilering 	4	-2001260
    "90.72": 9353,  #   terrein - terreininrichtingen - standaard, bewegwijzering 	4	-2001260
    "90.73": 9353,  #   terrein - terreininrichtingen - standaard, kunstwerken 	4	-2001260
    "90.74": 9353,  #   terrein - terreininrichtingen - standaard, decoraties e.d.	4	-2001260
    "90.80": 9353,  #   terrein - terreininrichtingen - bijzonder, algemeen (verzamelniveau) 	4	-2001260
    "90.81": 9353,  #   terrein - terreininrichtingen - bijzonder, terreinmeubilering 	4	-2001260
    "90.82": 9353,  #   terrein - terreininrichtingen - bijzonder, specifieke voorzieningen	4	-2001350
    "90.83": 9353,  #   terrein - terreininrichtingen - bijzonder, bijzondere verhardingen	4	-2001350
    # Indirect Project Provisions (Indirecte Projectvoorzieningen)
    "0": 9368,  # -	INDIRECTE PROJECTVOORZIENINGEN	1	-2000151
    "0-.00": 9368,  #   indirecte projectvoorzieningen	3	-2001350
    "0-.10": 9368,  #   indirecte projectvoorzieningen - werkterreininrichting, algemeen (verzamelniveau)	4	-2001350
    "0-.11": 9368,  #   indirecte projectvoorzieningen - werkterreininrichting, bijkomende werken	4	-2001350
    "0-.12": 9368,  #   indirecte projectvoorzieningen - werkterreininrichting, personen/materiaalvoorzieningen	4	-2001350
    "0-.13": 9368,  #   indirecte projectvoorzieningen - werkterreininrichting, energievoorzieningen	4	-2001350
    "0-.14": 9368,  #   indirecte projectvoorzieningen - werkterreininrichting, beveiligingsvoorzieningen	4	-2001350
    "0-.15": 9368,  #   indirecte projectvoorzieningen - werkterreininrichting, doorwerkvoorzieningen	4	-2001350
    "0-.16": 9368,  #   indirecte projectvoorzieningen - werkterreininrichting, voorzieningen belendende percelen	4	-2001350
    "0-.17": 9368,  #   indirecte projectvoorzieningen - werkterreininrichting, onderhoudsvoorzieningen	4	-2001350
    "0-.20": 9368,  #   indirecte projectvoorzieningen - materieelvoorzieningen, algemeen (verzamelniveau)	4	-2001350
    "0-,21": 9368,  #   indirecte projectvoorzieningen - materieelvoorzieningen, transport	4	-2001350
    "0-.22": 9368,  #   indirecte projectvoorzieningen - materieelvoorzieningen, gereedschappen	4	-2001350
    "0-.30": 9368,  #   indirecte projectvoorzieningen - risicodekking, algemeen (verzamelniveau) 	4	-2001350
    "0-.31": 9368,  #   indirecte projectvoorzieningen - risicodekking, verzekeringen	4	-2001350
    "0-.32": 9368,  #   indirecte projectvoorzieningen - risicodekking, waarborgen	4	-2001350
    "0-.33": 9368,  #   indirecte projectvoorzieningen - risicodekking, prijsstijgingen	4	-2001350
    "0-.40": 9368,  #   indirecte projectvoorzieningen - projectorganisatie, algemeen (verzamelniveau)	4	-2001350
    "0-.41": 9368,  #   indirecte projectvoorzieningen - projectorganisatie, administratie	4	-2001350
    "0-.42": 9368,  #   indirecte projectvoorzieningen - projectorganisatie, uitvoering	4	-2001350
    "0-.43": 9368,  #   indirecte projectvoorzieningen - projectorganisatie, documentatie	4	-2001350
    "0-.50": 9368,  #   indirecte projectvoorzieningen - bedrijfsorganisatie, algemeen (verzamelniveau)	4	-2001350
    "0-.51": 9368,  #   indirecte projectvoorzieningen - bedrijfsorganisatie, bestuur en directie	4	-2001350
    "0-.52": 9368,  #   indirecte projectvoorzieningen - bedrijfsorganisatie, winstregelingen	4	-2001350
}

# Logging Setup
# timestamp = DateTime.Now.ToString("yyyyMMdd_HHmmss")
# desktop = os.path.expanduser("~/Desktop")
# log_file = os.path.join(desktop, "workset_log_" + timestamp + ".txt")
# missing_file = os.path.join(desktop, "missing_prefixes_" + timestamp + ".txt")

missing_prefixes = {}


def get_workset_id_by_element_type(name):
    for prefix in txt_mapping:
        if name.startswith(prefix):
            return txt_mapping[prefix]
    return None


def assign_workset(element, workset_id):
    try:
        t = Transaction(doc, "Assign Workset")
        t.Start()
        param = element.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM)
        if param and not param.IsReadOnly:
            param.Set(workset_id)
            print(
                "Assigned element "
                + str(element.Id)
                + " to WorksetId "
                + str(workset_id)
            )
        else:
            print("Workset param missing or readonly for element " + str(element.Id))
        t.Commit()
    except Exception as e:
        print(
            "Failed assigning WorksetId to element " + str(element.Id) + ": " + str(e)
        )


def process_elements():
    categories = [
        BuiltInCategory.OST_Ceilings,
        BuiltInCategory.OST_Columns,
        BuiltInCategory.OST_Casework,
        BuiltInCategory.OST_Doors,
        BuiltInCategory.OST_ElectricalEquipment,
        BuiltInCategory.OST_ElectricalFixtures,
        BuiltInCategory.OST_Floors,
        BuiltInCategory.OST_Furniture,
        BuiltInCategory.OST_GenericModel,
        BuiltInCategory.OST_LightingFixtures,
        BuiltInCategory.OST_MechanicalEquipment,
        BuiltInCategory.OST_PipeAccessory,
        BuiltInCategory.OST_PipeFitting,
        BuiltInCategory.OST_PlumbingFixtures,
        BuiltInCategory.OST_SecurityDevices,
        BuiltInCategory.OST_Stairs,
        BuiltInCategory.OST_ShaftOpening,
        BuiltInCategory.OST_StructuralFoundation,
        BuiltInCategory.OST_StructuralColumns,
        BuiltInCategory.OST_StructuralFraming,
        BuiltInCategory.OST_Topography,
        BuiltInCategory.OST_Ramps,
        BuiltInCategory.OST_Railings,
        BuiltInCategory.OST_Roofs,
        BuiltInCategory.OST_Windows,
        BuiltInCategory.OST_Walls,
    ]

    for category in categories:
        elements = (
            FilteredElementCollector(doc)
            .OfCategory(category)
            .WhereElementIsNotElementType()
        )
        for element in elements:
            type_element = doc.GetElement(element.GetTypeId())
            if type_element is None:
                continue
            name_param = type_element.get_Parameter(
                BuiltInParameter.ALL_MODEL_TYPE_NAME
            )
            if name_param is None:
                continue
            name = name_param.AsString()
            print("Processing element ID " + str(element.Id) + ": Type = " + name)
            workset_id = get_workset_id_by_element_type(name)
            if workset_id:
                assign_workset(element, workset_id)
            else:
                print("No match found for element ID " + str(element.Id))
                prefix = name.split(".")[0]
                if prefix not in txt_mapping:
                    if prefix not in missing_prefixes:
                        missing_prefixes[prefix] = 0
                    missing_prefixes[prefix] += 1


# === Run Script ===
print("Started assigning worksets...\n")
process_elements()
print("\nCompleted assigning worksets.")

# === Save Logs ===
# with open(log_file, "w") as f:
#     for line in log_lines:
#         f.write(line + "\n")

# if missing_prefixes:
#     with open(missing_file, "w") as f:
#         f.write("MISSING PREFIXES:\n")
#         for prefix in missing_prefixes:
#             f.write(
#                 prefix + " = ???  # " + str(missing_prefixes[prefix]) + " elements\n"
#             )

# TaskDialog.Show("Finished", "Logs saved to Desktop:\n" + log_file + "\n" + missing_file)
TaskDialog.Show(
    "Finished", "All workset assigned. See Revit Python console for messages."
)

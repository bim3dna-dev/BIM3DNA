# -*- coding: utf-8 -*-
import re


BLOCKED_PATTERNS = [
    ("Autodesk.DesignScript", "Autodesk.DesignScript namespace is not supported in pyRevit."),
    ("DesignScript", "DesignScript APIs are not supported in pyRevit."),
    ("RevitServices", "RevitServices is a Dynamo runtime dependency and is not supported in pyRevit."),
    ("RevitNodes", "RevitNodes is a Dynamo runtime dependency and is not supported in pyRevit."),
    ("ProtoGeometry", "ProtoGeometry is a Dynamo geometry dependency and is not supported in pyRevit."),
    ("from Revit import", "Dynamo Revit wrapper imports are not supported in pyRevit."),
    ("clr.AddReference('ProtoGeometry')", "ProtoGeometry reference loading is not supported in pyRevit."),
    ('clr.AddReference("ProtoGeometry")', "ProtoGeometry reference loading is not supported in pyRevit."),
    ("DB.RevitServices", "DB.RevitServices is not part of the supported pyRevit runtime."),
    ("DocumentManager.Instance", "Dynamo document context APIs are not supported in pyRevit."),
    ("TransactionManager.Instance", "Dynamo transaction manager APIs are not supported in pyRevit."),
    ("UnwrapElement", "Dynamo element unwrapping helpers are not supported in pyRevit."),
    ("ToDSType", "Dynamo type conversion helpers are not supported in pyRevit."),
    ("ToProtoType", "Dynamo geometry conversion helpers are not supported in pyRevit."),
    ("IN[", "Dynamo graph input variables are not supported in pyRevit."),
    ("OUT =", "Dynamo graph output variables are not supported in pyRevit."),
]

ALLOWED_IMPORT_PREFIXES = [
    "Autodesk.Revit",
    "pyrevit",
    "System",
    "clr",
    "math",
    "re",
    "json",
    "time",
    "collections",
    "itertools",
]

ALLOWED_LIB_MODULES = [
    "ai_local_store",
    "ai_prompt_registry",
    "ai_agent_session",
    "ai_reviewed_code",
]


def extract_import_targets(code_text):
    targets = []
    for line in (code_text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("import "):
            raw = stripped[len("import ") :]
            parts = [item.strip() for item in raw.split(",")]
            for part in parts:
                targets.append(part.split(" as ")[0].strip())
        elif stripped.startswith("from "):
            remainder = stripped[len("from ") :]
            module = remainder.split(" import ")[0].strip()
            targets.append(module)
    return targets


def _is_allowed_import(module_name):
    for prefix in ALLOWED_IMPORT_PREFIXES:
        if module_name == prefix or module_name.startswith(prefix + "."):
            return True
    for prefix in ALLOWED_LIB_MODULES:
        if module_name == prefix or module_name.startswith(prefix + "."):
            return True
    return False


def validate_reviewed_code(code_text):
    code_text = code_text or ""
    errors = []
    blocked_hits = []

    for pattern, message in BLOCKED_PATTERNS:
        if pattern in code_text:
            blocked_hits.append(pattern)
            errors.append(message)

    unsupported_imports = []
    for module_name in extract_import_targets(code_text):
        if not _is_allowed_import(module_name):
            unsupported_imports.append(module_name)

    if unsupported_imports:
        errors.append(
            "Unsupported import/module references found: {0}".format(
                ", ".join(sorted(set(unsupported_imports)))
            )
        )

    has_revit_context = False
    if re.search(r"\bdoc\b", code_text):
        has_revit_context = True
    elif re.search(r"\buidoc\b", code_text):
        has_revit_context = True
    elif "Autodesk.Revit.DB" in code_text or "DB." in code_text:
        has_revit_context = True

    if not has_revit_context:
        errors.append(
            "Reviewed code must target the pyRevit/Revit API runtime and reference doc, uidoc, or Autodesk.Revit.DB."
        )

    is_valid = len(errors) == 0
    return {
        "is_valid": is_valid,
        "errors": errors,
        "blocked_hits": sorted(set(blocked_hits + unsupported_imports)),
    }

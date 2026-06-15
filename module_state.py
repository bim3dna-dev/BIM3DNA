# -*- coding: utf-8 -*-
"""
Persistent storage for region_scanner payloads.

Writes versioned JSON under:
 - <rvt_dir>\\_PLEGTVOS_ModuleEngine\\state\\ (when doc is saved)
 - %APPDATA%\\pyRevit\\PLEGTVOS_ModuleEngine\\state\\<doc.Title>\\ (fallback)

Creates both timestamped file and latest.json on each save.
"""

import os
import json
import getpass
from datetime import datetime

try:
    from Autodesk.Revit.DB import ModelPathUtils, WorksharingUtils
except Exception:
    ModelPathUtils = None
    WorksharingUtils = None
    WorksharingUtils = None

SCHEMA_VERSION = 1


def _utc_now_iso():
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_central_path(doc):
    if ModelPathUtils and hasattr(doc, "GetWorksharingCentralModelPath"):
        try:
            mp = doc.GetWorksharingCentralModelPath()
            if mp:
                return ModelPathUtils.ConvertModelPathToUserVisiblePath(mp)
        except Exception:
            return None
    return None


def _candidate_dirs(doc):
    dirs = []
    try:
        if getattr(doc, "PathName", None):
            rvt_dir = os.path.dirname(doc.PathName)
            if rvt_dir and os.path.isdir(rvt_dir):
                dirs.append(os.path.join(rvt_dir, "_PLEGTVOS_ModuleEngine", "state"))
    except Exception:
        pass

    try:
        cpath = _safe_central_path(doc)
        if cpath:
            cdir = os.path.dirname(cpath)
            if cdir and os.path.isdir(cdir):
                candidate = os.path.join(cdir, "_PLEGTVOS_ModuleEngine", "state")
                if candidate not in dirs:
                    dirs.append(candidate)
    except Exception:
        pass

    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    fallback = os.path.join(
        appdata,
        "pyRevit",
        "PLEGTVOS_ModuleEngine",
        "state",
        (doc.Title or "unsaved_doc"),
    )
    if fallback not in dirs:
        dirs.append(fallback)
    return dirs


def _is_writable(path):
    try:
        if not os.path.isdir(path):
            os.makedirs(path)
        test_path = os.path.join(path, ".write_test.tmp")
        with open(test_path, "w") as fp:
            fp.write("ok")
        os.remove(test_path)
        return True
    except Exception:
        print("module_state: path not writable -> {}".format(path))
        return False


def get_state_dir(doc):
    """
    Pick a state dir with priority:
    1) project/central folder
    2) fallback local (AppData/pyRevit/PLEGTVOS_ModuleEngine/state/<title>)
    Returns (path, label).
    """
    errors = []
    for label, path in [("project", d) for d in _candidate_dirs(doc)]:
        try:
            if _is_writable(path):
                print("module_state: using state dir ({0}): {1}".format(label, path))
                return path, label
            else:
                errors.append("not writable: {0}".format(path))
        except Exception as ex:
            errors.append("{0} -> {1}".format(path, ex))
            continue
    # fallback to last candidate even if not writable
    fallback = _candidate_dirs(doc)[-1]
    print("module_state: falling back to {0}".format(fallback))
    return fallback, "fallback"


def _primary_state_dir(doc):
    """Try to derive a project directory near the RVT or its central path."""
    try:
        if getattr(doc, "PathName", None):
            rvt_dir = os.path.dirname(doc.PathName)
            if rvt_dir and os.path.isdir(rvt_dir):
                return os.path.join(rvt_dir, "_PLEGTVOS_ModuleEngine", "state")
    except Exception:
        pass

    try:
        cpath = _safe_central_path(doc)
        if cpath:
            cdir = os.path.dirname(cpath)
            if cdir and os.path.isdir(cdir):
                return os.path.join(cdir, "_PLEGTVOS_ModuleEngine", "state")
    except Exception:
        pass
    return None


def _fallback_state_dir(doc):
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    return os.path.join(
        appdata,
        "pyRevit",
        "PLEGTVOS_ModuleEngine",
        "state",
        (doc.Title or "unsaved_doc"),
    )


def state_dirs(doc):
    """Ordered list: primary (project/central) then fallback appdata."""
    dirs = []
    primary = _primary_state_dir(doc)
    if primary:
        dirs.append(primary)
    fallback = _fallback_state_dir(doc)
    if fallback not in dirs:
        dirs.append(fallback)
    return dirs


def _base_state_dir(doc):
    """Backwards-compatible; returns the first in state_dirs."""
    dirs = _candidate_dirs(doc)
    return dirs[0] if dirs else None


def _ensure_dir(path):
    if not os.path.isdir(path):
        os.makedirs(path)


def to_payload(doc, core_payload):
    """Wrap core payload with metadata and schema info."""
    central_guid = None
    try:
        if WorksharingUtils and hasattr(doc, "IsWorkshared") and doc.IsWorkshared:
            central_guid = str(WorksharingUtils.GetCentralGUID(doc))
    except Exception:
        central_guid = None

    return {
        "schema_version": SCHEMA_VERSION,
        "created_utc": _utc_now_iso(),
        "user": getpass.getuser(),
        "doc": {
            "title": doc.Title,
            "path": getattr(doc, "PathName", None),
            "central_path": _safe_central_path(doc),
            "central_guid": central_guid,
            "is_workshared": getattr(doc, "IsWorkshared", False),
        },
        "revit": {
            "build": getattr(doc.Application, "VersionBuild", None),
            "version_name": getattr(doc.Application, "VersionName", None),
        },
        "payload": core_payload,
    }


def _write_json(path, data):
    with open(path, "w") as fp:
        json.dump(data, fp, indent=2, sort_keys=True)


def save_payload(doc, payload, name=None):
    """Save payload, return full path of timestamped file."""
    state_dir, label = get_state_dir(doc)
    _ensure_dir(state_dir)
    stamp = datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S")
    fname = name or "module_state_{}.json".format(stamp)
    full_path = os.path.join(state_dir, fname)
    _write_json(full_path, payload)
    _write_json(os.path.join(state_dir, "latest.json"), payload)
    return full_path


def list_payloads(doc):
    """List available payload files (full paths)."""
    for state_dir in _candidate_dirs(doc):
        if not os.path.isdir(state_dir):
            continue
        files = [
            os.path.join(state_dir, f)
            for f in os.listdir(state_dir)
            if f.lower().endswith(".json")
        ]
        if files:
            return sorted(files)
    return []


def load_latest_payload(doc):
    """Load latest payload json; returns dict or None."""
    for state_dir in _candidate_dirs(doc):
        latest_path = os.path.join(state_dir, "latest.json")
        if os.path.isfile(latest_path):
            with open(latest_path, "r") as fp:
                return json.load(fp)
    return None

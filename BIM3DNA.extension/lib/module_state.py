# -*- coding: utf-8 -*-
"""File-based state helpers for Scope Modifier commands."""

import json
import os
import re
import tempfile
from datetime import datetime

try:
    from Autodesk.Revit.DB import ElementId, ElementType
except Exception:
    ElementId = None
    ElementType = None


try:
    _string_types = (basestring,)
except NameError:
    _string_types = (str,)

try:
    _integer_types = (int, long)
except NameError:
    _integer_types = (int,)


def _safe_doc_name(doc):
    try:
        value = doc.Title or "Untitled"
    except Exception:
        value = "Untitled"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "Untitled"


def normalize_doc_title(value):
    if value is None:
        return ""
    text = str(value).strip().lower()
    text = text.replace("_", " ")
    text = text.replace("-", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def doc_title_matches(payload_title, current_title):
    return normalize_doc_title(payload_title) == normalize_doc_title(current_title)


def get_doc_identity(doc):
    """Return primitive document identity data without retaining the Document."""
    try:
        title = doc.Title
    except Exception:
        title = None
    try:
        path_name = doc.PathName
    except Exception:
        path_name = None
    return {
        "title": title,
        "title_normalized": normalize_doc_title(title),
        "path_name": path_name,
    }


def payload_matches_current_doc(payload, doc):
    """Match current and legacy payloads by path, then normalized title."""
    current = get_doc_identity(doc)
    payload_doc = None
    if isinstance(payload, dict):
        payload_doc = (
            payload.get("doc_identity")
            or payload.get("doc")
            or payload.get("document")
        )

    payload_title = None
    payload_path = None
    if isinstance(payload_doc, dict):
        payload_title = (
            payload_doc.get("title")
            or payload_doc.get("doc_title")
            or payload_doc.get("name")
        )
        payload_path = (
            payload_doc.get("path_name")
            or payload_doc.get("path")
            or payload_doc.get("model_path")
        )
    elif isinstance(payload, dict):
        payload_title = (
            payload.get("doc_title")
            or payload.get("document_title")
            or payload.get("title")
        )
        payload_path = payload.get("doc_path") or payload.get("path_name")

    current_path = current.get("path_name")
    if payload_path and current_path:
        if os.path.normcase(str(payload_path)) == os.path.normcase(str(current_path)):
            return True
    return doc_title_matches(payload_title, current.get("title"))


def _append_unique(items, path):
    if path:
        normalized = os.path.normpath(path)
        if normalized not in items:
            items.append(normalized)


def _ensure_dir(path):
    if not os.path.isdir(path):
        os.makedirs(path)
    return path


def _get_local_appdata():
    value = os.environ.get("LOCALAPPDATA")
    if value:
        return value
    value = os.environ.get("APPDATA")
    if value:
        return value
    return tempfile.gettempdir()


def get_scope_modifier_data_dir():
    root = os.path.join(_get_local_appdata(), "BIM3DNA", "ScopeModifier")
    return _ensure_dir(root)


def get_region_payload_dir():
    root = os.path.join(get_scope_modifier_data_dir(), "Regions")
    return _ensure_dir(root)


def get_latest_region_pointer_path():
    return os.path.join(
        get_scope_modifier_data_dir(), "latest_region_payload.txt"
    )


def set_latest_region_payload(path):
    pointer_path = get_latest_region_pointer_path()
    _ensure_dir(os.path.dirname(pointer_path))
    with open(pointer_path, "w") as stream:
        stream.write(str(path))
    print(
        "MODULE_STATE -> latest region payload pointer: {}".format(
            pointer_path
        )
    )
    return pointer_path


def get_latest_region_payload_path():
    pointer_path = get_latest_region_pointer_path()
    if not os.path.isfile(pointer_path):
        return None
    try:
        with open(pointer_path, "r") as stream:
            path = stream.read().strip()
        if path and os.path.isfile(path):
            return path
    except Exception as ex:
        print(
            "MODULE_STATE -> failed to read latest region pointer: {}".format(
                ex
            )
        )
    return None


def _payload_pointer_path(name):
    return os.path.join(get_scope_modifier_data_dir(), name)


def _set_payload_pointer(pointer_name, path):
    pointer_path = _payload_pointer_path(pointer_name)
    _ensure_dir(os.path.dirname(pointer_path))
    with open(pointer_path, "w") as stream:
        stream.write(str(path))
    return pointer_path


def _get_payload_pointer(pointer_name):
    pointer_path = _payload_pointer_path(pointer_name)
    if not os.path.isfile(pointer_path):
        return None
    try:
        with open(pointer_path, "r") as stream:
            path = stream.read().strip()
        if path and os.path.isfile(path):
            return path
    except Exception:
        pass
    return None


def set_latest_copyable_payload(path):
    pointer = _set_payload_pointer("latest_copyable_payload.txt", path)
    print("MODULE_STATE -> latest copyable payload pointer: {}".format(pointer))
    return pointer


def get_latest_copyable_payload_path():
    return _get_payload_pointer("latest_copyable_payload.txt")


def save_named_payload(doc, payload, filename):
    state_dir, _state_label = get_state_dir(doc)
    path = os.path.join(state_dir, _safe_filename(filename))
    safe_payload = _serialize_value(payload)
    with open(path, "w") as stream:
        json.dump(safe_payload, stream, indent=2, sort_keys=True)
    print("MODULE_STATE -> saved payload: {}".format(path))
    return os.path.abspath(path)


def get_element_category_name(element):
    try:
        if element and element.Category:
            return str(element.Category.Name or "")
    except Exception:
        pass
    return ""


def get_element_category_parent_name(element):
    try:
        category = element.Category
        parent = category.Parent if category else None
        if parent:
            return str(parent.Name or "")
    except Exception:
        pass
    return ""


def get_element_class_name(element):
    try:
        return str(element.GetType().Name)
    except Exception:
        try:
            return str(type(element).__name__)
        except Exception:
            return ""


_COPYABLE_CATEGORY_NAMES = set(
    [
        "pipes",
        "pipe fittings",
        "pipe accessories",
        "plumbing fixtures",
        "plumbing equipment",
        "ducts",
        "duct fittings",
        "duct accessories",
        "air terminals",
        "mechanical equipment",
        "flex pipes",
        "flex ducts",
    ]
)

_NON_COPYABLE_CATEGORY_NAMES = set(
    [
        "piping systems",
        "duct systems",
        "scope boxes",
        "cameras",
        "center line",
        "lines",
        "constraints",
        "dimensions",
        "views",
        "levels",
        "grids",
    ]
)

_NON_COPYABLE_CLASS_NAMES = set(
    ["view", "view3d", "level", "grid", "elementtype", "scopebox", "camera"]
)


def is_scope_modifier_copyable_element(element):
    """Return (is_copyable, reason) for destructive Scope Modifier workflows."""
    if element is None:
        return False, "invalid element"
    if ElementType is not None:
        try:
            if isinstance(element, ElementType):
                return False, "element type"
        except Exception:
            pass

    category = get_element_category_name(element)
    parent = get_element_category_parent_name(element)
    class_name = get_element_class_name(element)
    category_key = category.strip().lower()
    parent_key = parent.strip().lower()
    class_key = class_name.strip().lower()

    if class_key in _NON_COPYABLE_CLASS_NAMES or class_key.endswith("elementtype"):
        return False, "non-copyable class"
    if not category:
        return False, "no category"
    if (
        category_key in _NON_COPYABLE_CATEGORY_NAMES
        or parent_key in _NON_COPYABLE_CATEGORY_NAMES
    ):
        return False, "non-copyable category"
    if category_key in _COPYABLE_CATEGORY_NAMES:
        return True, "copyable category"
    return False, "not in copyable category whitelist"


def classify_copyable_elements(doc, ids):
    copyable_ids = []
    skipped = []
    skipped_breakdown = {}
    for value in ids or []:
        try:
            element_id = value
            if hasattr(value, "IntegerValue"):
                integer_id = int(value.IntegerValue)
            else:
                integer_id = int(value)
                element_id = ElementId(integer_id) if ElementId else integer_id
            element = doc.GetElement(element_id)
        except Exception:
            integer_id = _safe_int_element_id(value)
            element = None

        category = get_element_category_name(element)
        parent = get_element_category_parent_name(element)
        class_name = get_element_class_name(element)
        allowed, reason = is_scope_modifier_copyable_element(element)
        if allowed:
            copyable_ids.append(integer_id)
            continue
        record = {
            "id": integer_id,
            "category": category or "-",
            "parent_category": parent or "-",
            "class": class_name or "-",
            "reason": reason,
        }
        skipped.append(record)
        key = "{} :: {}".format(reason, category or "-")
        skipped_breakdown[key] = skipped_breakdown.get(key, 0) + 1

    return {
        "copyable_ids": copyable_ids,
        "skipped": skipped,
        "counts": {
            "copyable": len(copyable_ids),
            "skipped": len(skipped),
        },
        "skipped_by_reason_category": skipped_breakdown,
    }


def _safe_filename(name):
    invalid = '<>:"/\\|?*'
    cleaned = []
    for character in str(name):
        if character in invalid:
            cleaned.append("_")
        else:
            cleaned.append(character)
    return "".join(cleaned)


def _candidate_dirs(doc):
    """Return deterministic state locations without retaining Revit objects."""
    paths = []
    try:
        _append_unique(paths, get_region_payload_dir())
    except Exception:
        pass
    _append_unique(
        paths,
        os.path.join(
            tempfile.gettempdir(), "BIM3DNA", "ScopeModifier", "Regions"
        ),
    )
    return paths


def get_state_dir(doc=None):
    """Return the preferred writable state directory and a diagnostic label."""
    try:
        return get_region_payload_dir(), "local appdata"
    except Exception:
        fallback = os.path.join(
            tempfile.gettempdir(), "BIM3DNA", "ScopeModifier", "Regions"
        )
        return _ensure_dir(fallback), "temporary state"


def _safe_int_element_id(value):
    try:
        return int(value.IntegerValue)
    except Exception:
        return None


def _is_xyz(value):
    return (
        hasattr(value, "X")
        and hasattr(value, "Y")
        and hasattr(value, "Z")
    )


def _xyz_payload(value):
    return {
        "__type__": "XYZ",
        "x": float(value.X),
        "y": float(value.Y),
        "z": float(value.Z),
    }


def _serialize_value(value):
    """Convert runtime values to JSON-safe primitives without retaining Revit objects."""
    if value is None or isinstance(value, (bool, float) + _integer_types + _string_types):
        return value

    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            result[str(key)] = _serialize_value(item)
        return result

    if isinstance(value, (list, tuple, set)):
        return [_serialize_value(item) for item in value]

    element_id = _safe_int_element_id(value)
    if element_id is not None:
        return {
            "__type__": "ElementId",
            "id": element_id,
        }

    if _is_xyz(value):
        return _xyz_payload(value)

    try:
        element_id = _safe_int_element_id(value.Id)
    except Exception:
        element_id = None
    if element_id is not None:
        category_name = None
        try:
            if value.Category:
                category_name = str(value.Category.Name)
        except Exception:
            category_name = None
        return {
            "__type__": "Element",
            "id": element_id,
            "category": category_name,
        }

    return str(value)


def to_payload(doc, value):
    """Wrap Scope Modifier state in a stable, JSON-serializable payload."""
    doc_identity = get_doc_identity(doc)
    return {
        "schema_version": 1,
        "created_utc": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "doc": {
            "title": doc_identity.get("title"),
            "title_normalized": doc_identity.get("title_normalized"),
            "path": doc_identity.get("path_name"),
        },
        "doc_identity": doc_identity,
        "payload": _serialize_value(value),
    }


def save_payload(doc, payload, filename=None, output_dir=None):
    """Save a timestamped state file and the latest.json consumed by other tools."""
    if output_dir is None:
        state_dir, _state_label = get_state_dir(doc)
    else:
        state_dir = _ensure_dir(output_dir)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
    if filename is None:
        filename = "region_{}.json".format(timestamp)
    if os.path.isabs(filename):
        history_path = filename
    else:
        history_path = os.path.join(state_dir, _safe_filename(filename))
    latest_path = os.path.join(state_dir, "latest.json")

    safe_payload = _serialize_value(payload)
    try:
        parent = os.path.dirname(history_path)
        if parent:
            _ensure_dir(parent)
        with open(history_path, "w") as stream:
            json.dump(safe_payload, stream, indent=2, sort_keys=True)
        with open(latest_path, "w") as stream:
            json.dump(safe_payload, stream, indent=2, sort_keys=True)
        final_path = os.path.abspath(history_path)
        print("MODULE_STATE -> saved payload: {}".format(final_path))
        return final_path
    except Exception as ex:
        fallback_dir = get_region_payload_dir()
        fallback_name = _safe_filename(os.path.basename(history_path))
        fallback_path = os.path.join(fallback_dir, fallback_name)
        fallback_latest_path = os.path.join(fallback_dir, "latest.json")
        print("MODULE_STATE -> primary payload save failed: {}".format(history_path))
        print("MODULE_STATE -> retrying writable fallback: {}".format(fallback_path))
        print("MODULE_STATE -> save error: {}".format(ex))
        with open(fallback_path, "w") as stream:
            json.dump(safe_payload, stream, indent=2, sort_keys=True)
        with open(fallback_latest_path, "w") as stream:
            json.dump(safe_payload, stream, indent=2, sort_keys=True)
        final_path = os.path.abspath(fallback_path)
        print("MODULE_STATE -> saved payload: {}".format(final_path))
        return final_path


def load_latest_payload(doc):
    """Load the first valid latest.json from the known state locations."""
    pointer_path = get_latest_region_payload_path()
    if pointer_path:
        try:
            with open(pointer_path, "r") as stream:
                return json.load(stream)
        except Exception as ex:
            print(
                "MODULE STATE -> failed to read pointed payload {}: {}".format(
                    pointer_path, ex
                )
            )
    for directory in _candidate_dirs(doc):
        path = os.path.join(directory, "latest.json")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r") as stream:
                return json.load(stream)
        except Exception as ex:
            print("MODULE STATE -> failed to read {}: {}".format(path, ex))
    return None

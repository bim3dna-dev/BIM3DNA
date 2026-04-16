# -*- coding: utf-8 -*-
import json
import os
import re
import subprocess
import sys

try:
    import inflect
except Exception:
    inflect = None


_BRIDGE_SCRIPT = os.path.join(os.path.dirname(__file__), "openai_provider_bridge.py")
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _is_ironpython():
    return sys.platform == "cli" or "IronPython" in sys.version


def _safe_json_loads(text):
    try:
        return json.loads(text)
    except Exception:
        return {}


def _candidate_runtime_commands():
    commands = []
    seen = set()

    def add(command):
        key = tuple(command)
        if key not in seen:
            seen.add(key)
            commands.append(command)

    repo_venv = os.path.join(_REPO_ROOT, ".venv", "Scripts", "python.exe")
    if os.path.exists(repo_venv):
        add([repo_venv])

    env_python = os.environ.get("PYREVIT_CPYTHON_EXE") or os.environ.get("PYTHON_EXE")
    if env_python and os.path.exists(env_python):
        add([env_python])

    if sys.executable and os.path.exists(sys.executable) and not _is_ironpython():
        add([sys.executable])

    add(["py", "-3"])
    add(["python"])

    return commands


def _run_bridge(action, payload=None, timeout=30):
    request = dict(payload or {})
    request["action"] = action
    last_failure = None
    input_bytes = json.dumps(request).encode("utf-8")

    for command in _candidate_runtime_commands():
        bridge_command = list(command) + [_BRIDGE_SCRIPT]
        try:
            process = subprocess.Popen(
                bridge_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout_data, stderr_data = process.communicate(
                input_bytes, timeout=timeout
            )
        except TypeError:
            try:
                process = subprocess.Popen(
                    bridge_command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                stdout_data, stderr_data = process.communicate(input_bytes)
            except Exception as exc:
                last_failure = {
                    "failure_category": "runtime_launch_failed",
                    "failure_message_safe": str(exc),
                    "runtime_command": " ".join(bridge_command),
                }
                continue
        except Exception as exc:
            last_failure = {
                "failure_category": "runtime_launch_failed",
                "failure_message_safe": str(exc),
                "runtime_command": " ".join(bridge_command),
            }
            continue

        if isinstance(stdout_data, bytes):
            stdout_text = stdout_data.decode("utf-8", "replace")
        else:
            stdout_text = stdout_data or ""

        if isinstance(stderr_data, bytes):
            stderr_text = stderr_data.decode("utf-8", "replace")
        else:
            stderr_text = stderr_data or ""

        if process.returncode != 0:
            last_failure = {
                "failure_category": "runtime_bridge_failed",
                "failure_message_safe": stderr_text.strip()
                or stdout_text.strip()
                or "Bridge exited with code {0}".format(process.returncode),
                "runtime_command": " ".join(bridge_command),
            }
            continue

        result = _safe_json_loads(stdout_text.strip())
        if result:
            if not result.get("runtime_command"):
                result["runtime_command"] = " ".join(bridge_command)
            return result

        last_failure = {
            "failure_category": "runtime_bridge_failed",
            "failure_message_safe": "Bridge returned invalid JSON output.",
            "runtime_command": " ".join(bridge_command),
        }

    return {
        "ok": False,
        "state": "client_init_failed",
        "message": "No usable CPython runtime was found for the OpenAI planner bridge.",
        "failure_category": (last_failure or {}).get(
            "failure_category", "runtime_unavailable"
        ),
        "failure_message_safe": (last_failure or {}).get(
            "failure_message_safe",
            "No usable CPython runtime was found for the OpenAI planner bridge.",
        ),
        "runtime_command": (last_failure or {}).get("runtime_command", ""),
        "provider_reachable": False,
        "last_error_category": (last_failure or {}).get(
            "failure_category", "runtime_unavailable"
        ),
    }


def _singularize(text):
    if inflect is None:
        return text
    return inflect.engine().singular_noun(text) or text


def normalize(text):
    """Convert plural nouns to singular (for example, windows -> window)."""
    return _singularize(text)


def filter_elements(question, model_data):
    """
    Filter model data based on keywords found in the question.
    Matches are normalized to singular form for robustness.
    """
    q = (question or "").lower()
    words_in_question = re.findall(r"\w+", q)
    normalized_words = [normalize(word) for word in words_in_question]

    category_keywords = {
        "wall": ["wall", "partition"],
        "door": ["door", "entry", "exit"],
        "window": ["window", "fenestration", "opening"],
        "floor": ["floor", "level", "story"],
        "roof": ["roof", "covering"],
        "column": ["column", "pillar"],
        "beam": ["beam", "girder"],
        "stair": ["stair", "step", "staircase"],
        "furniture": ["furniture", "chair", "table", "sofa", "desk"],
        "fixture": ["fixture", "lighting", "lamp"],
        "plumbing": ["pipe", "plumbing", "water"],
        "electrical": ["electrical", "conduit", "cable", "wiring"],
        "hvac": ["hvac", "air", "vent", "duct"],
        "structural": ["structural", "steel", "concrete", "reinforcement"],
        "exterior": ["exterior", "facade", "cladding"],
        "interior": ["interior"],
        "landscape": ["landscape", "garden", "tree", "plant"],
    }

    matched_categories = []
    for category, keywords in category_keywords.items():
        for word in normalized_words:
            if word in keywords:
                matched_categories.append(category)
                break

    matched_categories = list(set(matched_categories))

    if not matched_categories:
        return model_data

    filtered_elements = []
    seen_ids = set()
    for element in model_data:
        cat_str = element.get("Category", "").lower()
        for category in matched_categories:
            if category in cat_str:
                element_id = element.get("Id")
                if element_id not in seen_ids:
                    seen_ids.add(element_id)
                    filtered_elements.append(element)
                break
    return filtered_elements


def get_openai_provider_state():
    result = _run_bridge("provider_state", timeout=20)
    return {
        "state": result.get("state", "client_init_failed"),
        "message": result.get(
            "message", "OpenAI planner bridge did not return a valid provider state."
        ),
        "key_present": bool(result.get("key_present", False)),
        "provider_reachable": bool(result.get("provider_reachable", False)),
        "last_error_category": result.get(
            "last_error_category", result.get("failure_category", "")
        ),
        "runtime_command": result.get("runtime_command", ""),
    }


def get_openai_provider_self_test():
    result = _run_bridge("self_test", timeout=30)
    return {
        "env_key_present": bool(result.get("env_key_present", False)),
        "openai_module_importable": bool(result.get("openai_module_importable", False)),
        "client_init_ok": bool(result.get("client_init_ok", False)),
        "test_request_ok": bool(result.get("test_request_ok", False)),
        "failure_category": result.get(
            "failure_category", result.get("state", "request_failed")
        ),
        "failure_message_safe": result.get("failure_message_safe", ""),
        "runtime_executable": result.get("runtime_executable", ""),
        "runtime_version": result.get("runtime_version", ""),
        "runtime_command": result.get("runtime_command", ""),
    }


def normalize_intent_to_supported_action(goal, supported_actions, model_name=None):
    result = _run_bridge(
        "normalize_intent",
        {
            "goal": goal,
            "supported_actions": supported_actions,
            "model_name": model_name,
        },
        timeout=45,
    )
    if result.get("ok"):
        return {
            "ok": True,
            "state": "provider_ready",
            "result": result.get("result", {}),
            "provider_reachable": bool(result.get("provider_reachable", False)),
            "last_error_category": result.get("last_error_category", "provider_ready"),
            "message": result.get("message", "OpenAI planner completed successfully."),
            "detail": result.get("detail", ""),
        }

    return {
        "ok": False,
        "state": result.get("state", "request_failed"),
        "error": result.get("failure_message_safe", result.get("message", "")),
        "message": result.get("message", "Cloud planner request failed."),
        "detail": result.get("detail", ""),
        "provider_reachable": bool(result.get("provider_reachable", False)),
        "last_error_category": result.get(
            "last_error_category", result.get("failure_category", "request_failed")
        ),
    }


if __name__ == "__main__":
    input_data = sys.stdin.read()
    try:
        data = json.loads(input_data)
        question = data.get("question", "")
        model_data = data.get("model_data", [])

        matching_elements = filter_elements(question, model_data)
        count = len(matching_elements)

        response = "Received question: {0}. Number of matching elements: {1}.".format(
            question, count
        )
        print(response)
    except Exception as exc:
        print("Error: " + str(exc))

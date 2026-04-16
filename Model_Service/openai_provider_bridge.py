# -*- coding: utf-8 -*-
import json
import os
import sys


def _result_base():
    return {
        "runtime_executable": sys.executable or "",
        "runtime_version": sys.version.replace("\n", " "),
        "runtime_command": "",
        "provider_reachable": False,
        "last_error_category": "",
    }


def _message_for_state(state):
    messages = {
        "provider_ready": "OpenAI planner bridge is ready.",
        "missing_key": "OPENAI_API_KEY is not set for the current process.",
        "missing_openai_module": "The configured CPython runtime cannot import the openai package.",
        "client_init_failed": "The configured CPython runtime could not initialize the OpenAI client.",
        "auth_failed": "OpenAI rejected the request due to authentication or permission issues.",
        "network_failed": "OpenAI could not be reached from the configured CPython runtime.",
        "request_failed": "The OpenAI planner request failed.",
    }
    return messages.get(state, "The OpenAI planner bridge failed.")


def _state_payload(state, message=None, **kwargs):
    payload = _result_base()
    payload.update(kwargs)
    payload["state"] = state
    payload["message"] = message or _message_for_state(state)
    payload["last_error_category"] = kwargs.get("last_error_category", state)
    return payload


def _load_request():
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    return json.loads(raw)


def _try_import_openai():
    try:
        from openai import OpenAI

        return True, OpenAI, ""
    except Exception as exc:
        return False, None, str(exc)


def _init_client():
    key_present = bool(os.environ.get("OPENAI_API_KEY"))
    importable, openai_cls, import_error = _try_import_openai()
    if not key_present:
        return {
            "env_key_present": False,
            "openai_module_importable": importable,
            "client_init_ok": False,
            "state": "missing_key",
            "failure_category": "missing_key",
            "failure_message_safe": _message_for_state("missing_key"),
            "client": None,
        }
    if not importable:
        return {
            "env_key_present": True,
            "openai_module_importable": False,
            "client_init_ok": False,
            "state": "missing_openai_module",
            "failure_category": "missing_openai_module",
            "failure_message_safe": import_error or _message_for_state("missing_openai_module"),
            "client": None,
        }
    try:
        client = openai_cls()
        return {
            "env_key_present": True,
            "openai_module_importable": True,
            "client_init_ok": True,
            "state": "provider_ready",
            "failure_category": "provider_ready",
            "failure_message_safe": "",
            "client": client,
        }
    except Exception as exc:
        return {
            "env_key_present": True,
            "openai_module_importable": True,
            "client_init_ok": False,
            "state": "client_init_failed",
            "failure_category": "client_init_failed",
            "failure_message_safe": str(exc),
            "client": None,
        }


def _normalize_result_payload(result):
    return {
        "matched_action": result.get("matched_action", ""),
        "confidence": float(result.get("confidence", 0.0) or 0.0),
        "summary": result.get("summary", ""),
        "rejected": bool(result.get("rejected", False)),
    }


def _normalize_intent(request):
    supported_actions = request.get("supported_actions") or []
    model_name = request.get("model_name") or "gpt-4o-mini"
    goal = (request.get("goal") or "").strip()
    client_state = _init_client()
    base = _result_base()
    base.update(client_state)

    if client_state["state"] != "provider_ready":
        return _state_payload(
            client_state["state"],
            message=_message_for_state(client_state["state"]),
            env_key_present=client_state["env_key_present"],
            openai_module_importable=client_state["openai_module_importable"],
            client_init_ok=client_state["client_init_ok"],
            test_request_ok=False,
            failure_category=client_state["failure_category"],
            failure_message_safe=client_state["failure_message_safe"],
        )

    prompt = (
        "You map a user goal to one supported Revit action.\n"
        "Return JSON only with keys matched_action, confidence, summary, rejected.\n"
        "If no supported action fits, return matched_action as an empty string and rejected as true.\n"
        "Supported actions:\n{0}\n"
        "User goal:\n{1}"
    ).format(json.dumps(supported_actions, indent=2), goal)

    try:
        response = client_state["client"].chat.completions.create(
            model=model_name,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": "Map the request to one supported action and respond with strict JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
        )
        content = response.choices[0].message.content or "{}"
        parsed = json.loads(content)
        return {
            "ok": True,
            "state": "provider_ready",
            "message": _message_for_state("provider_ready"),
            "result": _normalize_result_payload(parsed),
            "provider_reachable": True,
            "last_error_category": "provider_ready",
            "detail": "",
            "env_key_present": True,
            "openai_module_importable": True,
            "client_init_ok": True,
            "test_request_ok": True,
            "failure_category": "provider_ready",
            "failure_message_safe": "",
        }
    except Exception as exc:
        error_text = str(exc)
        state = "request_failed"
        if "401" in error_text or "403" in error_text:
            state = "auth_failed"
        elif "timeout" in error_text.lower() or "connection" in error_text.lower():
            state = "network_failed"
        return _state_payload(
            state,
            env_key_present=True,
            openai_module_importable=True,
            client_init_ok=True,
            test_request_ok=False,
            failure_category=state,
            failure_message_safe=error_text,
            provider_reachable=(state != "network_failed"),
            detail=error_text,
        )


def _provider_state():
    client_state = _init_client()
    state = client_state["state"]
    return _state_payload(
        state,
        env_key_present=client_state["env_key_present"],
        openai_module_importable=client_state["openai_module_importable"],
        client_init_ok=client_state["client_init_ok"],
        test_request_ok=(state == "provider_ready"),
        failure_category=client_state["failure_category"],
        failure_message_safe=client_state["failure_message_safe"],
        key_present=client_state["env_key_present"],
        provider_reachable=(state == "provider_ready"),
    )


def _self_test():
    client_state = _init_client()
    state = client_state["state"]
    return _state_payload(
        state if state != "provider_ready" else "provider_ready",
        env_key_present=client_state["env_key_present"],
        openai_module_importable=client_state["openai_module_importable"],
        client_init_ok=client_state["client_init_ok"],
        test_request_ok=(state == "provider_ready"),
        failure_category=client_state["failure_category"],
        failure_message_safe=client_state["failure_message_safe"],
        key_present=client_state["env_key_present"],
        provider_reachable=(state == "provider_ready"),
    )


def main():
    request = _load_request()
    action = request.get("action")
    if action == "provider_state":
        result = _provider_state()
    elif action == "self_test":
        result = _self_test()
    elif action == "normalize_intent":
        result = _normalize_intent(request)
    else:
        result = _state_payload(
            "request_failed",
            message="Unknown bridge action: {0}".format(action),
            failure_category="request_failed",
            failure_message_safe="Unknown bridge action.",
        )
    print(json.dumps(result))


if __name__ == "__main__":
    main()

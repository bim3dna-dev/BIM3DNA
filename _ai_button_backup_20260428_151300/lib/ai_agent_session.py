# -*- coding: utf-8 -*-


class AgentSession(object):
    def __init__(self, commands=None):
        self.allow_destructive = False
        self.refresh_catalog(commands or [])
        self.reset()

    def refresh_catalog(self, commands):
        self.catalog = {}
        for command in commands:
            self.catalog[command.get("id")] = dict(command)
        supported_titles = []
        for command in commands:
            title = command.get("title")
            if title:
                supported_titles.append(title)
        self.supported_message = "Supported reviewed actions: {0}.".format(
            "; ".join(supported_titles)
        ) if supported_titles else "No shared reviewed actions are currently registered."

    def reset(self):
        self.goal = ""
        self.plan = []
        self.plan_object = None
        self.status = "idle"
        self.message = "Idle"
        self.last_undo_context = None
        self.guidance = getattr(self, "supported_message", "No shared reviewed actions are currently registered.")

    def set_allow_destructive(self, enabled):
        self.allow_destructive = bool(enabled)

    def _add_step(self, command_id):
        command = self.catalog.get(command_id)
        if not command:
            return
        step = dict(command)
        original_role = step.get("role", "read")
        step["command_role"] = original_role
        step["role"] = "modifying" if original_role == "modify" else "read_only"
        step["risk"] = step.get("risk_level", "low")
        step["enabled"] = True
        step["executed"] = False
        step["blocked_reason"] = ""
        step["undo_available"] = False
        self.plan.append(step)

    def has_plan(self):
        return bool(self.plan)

    def has_enabled_steps(self):
        for step in self.plan:
            if step.get("enabled", True):
                return True
        return False

    def has_runnable_steps(self):
        for step in self.plan:
            if not step.get("enabled", True):
                continue
            if step.get("role") == "modifying" and not self.allow_destructive:
                continue
            return True
        return False

    def has_undo_context(self):
        return bool(self.last_undo_context and self.last_undo_context.get("undo_available"))

    def get_undo_context(self):
        return dict(self.last_undo_context) if self.last_undo_context else None

    def set_undo_context(self, context):
        self.clear_undo_context()
        if not context:
            return
        self.last_undo_context = dict(context)
        target_action_id = self.last_undo_context.get("action_id")
        for step in self.plan:
            step["undo_available"] = bool(step.get("id") == target_action_id)

    def clear_undo_context(self):
        self.last_undo_context = None
        for step in self.plan:
            step["undo_available"] = False

    def get_supported_actions(self):
        actions = []
        for command in self.catalog.values():
            actions.append(
                {
                    "id": command.get("id"),
                    "title": command.get("title"),
                    "prompt_text": command.get("canonical_prompt", command.get("prompt_text")),
                    "planner_aliases": list(command.get("aliases") or command.get("planner_aliases") or []),
                    "canonical_prompt": command.get("canonical_prompt", command.get("prompt_text")),
                    "validation_state": command.get("validation_state", "structural_only"),
                    "deterministic_handler": command.get("deterministic_handler", ""),
                    "requires_confirmation": bool(command.get("requires_confirmation", False)),
                    "requires_modification": command.get("role") == "modify",
                    "destructive": command.get("risk_level") == "high",
                }
            )
        return actions

    def _match_goal(self, goal_text):
        goal = (goal_text or "").lower()
        matches = []
        for command_id, command in self.catalog.items():
            phrases = []
            phrases.extend(command.get("aliases") or command.get("planner_aliases") or [])
            phrases.extend(command.get("example_prompts") or [])
            phrases.append(command.get("canonical_prompt", command.get("prompt_text", "")))
            phrases.append(command.get("prompt_text", ""))
            phrases.append(command.get("title", ""))
            best_score = 0
            for phrase in phrases:
                phrase = (phrase or "").strip().lower()
                if not phrase:
                    continue
                if goal == phrase:
                    best_score = max(best_score, 100 + len(phrase))
                elif phrase in goal:
                    best_score = max(best_score, 50 + len(phrase))
                elif all(token in goal for token in phrase.split() if len(token) > 2):
                    best_score = max(best_score, 10 + len(phrase))
            if best_score:
                matches.append((best_score, command_id))
        matches.sort(reverse=True)
        return matches

    def _build_unsupported_summary(self, goal_text):
        goal = (goal_text or "").lower()
        if "schedule" in goal or "quantity" in goal:
            return (
                "Schedule creation or quantity schedule generation is not yet implemented as "
                "a reviewed deterministic action. Closest supported reviewed actions include "
                "count selected ducts; count ducts in active view; list ducts in active view; "
                "report total selected duct volume in cubic meters; create sheet."
            )
        if "volume" in goal and "duct" in goal:
            return (
                "No reviewed duct-volume action matched this request exactly. "
                "Closest supported reviewed actions include report total selected duct volume in cubic meters, "
                "count selected ducts, and list ducts in active view."
            )
        return (
            "This request is outside the current reviewed deterministic action set. "
            + self.supported_message
        )

    def plan_goal(self, goal_text):
        self.reset()
        self.goal = goal_text or ""
        self.status = "planning"
        self.message = "Planning"
        matches = self._match_goal(goal_text)
        if matches:
            if len(matches) > 1 and matches[0][0] == matches[1][0]:
                first = self.catalog.get(matches[0][1], {})
                second = self.catalog.get(matches[1][1], {})
                self.plan_object = {
                    "matched_action": "",
                    "confidence": 0.0,
                    "requires_modification": False,
                    "destructive": False,
                    "summary": "Ambiguous request. Did you mean '{0}' or '{1}'?".format(
                        first.get("title", matches[0][1]),
                        second.get("title", matches[1][1]),
                    ),
                    "execution_ready": False,
                }
                self.status = "failed"
                self.message = "Needs clarification"
                self.guidance = self.plan_object["summary"]
                return list(self.plan)
            self.build_plan_from_action(
                matches[0][1],
                confidence=0.75,
                summary="Planner matched a shared reviewed action.",
            )
        else:
            self.plan_object = {
                "matched_action": "",
                "confidence": 0.0,
                "requires_modification": False,
                "destructive": False,
                "summary": self._build_unsupported_summary(goal_text),
                "execution_ready": False,
            }
            self.status = "failed"
            self.message = "Unsupported request"
            self.guidance = self.plan_object["summary"]
        return list(self.plan)

    def build_plan_from_action(self, action_id, confidence, summary):
        self.reset()
        command = self.catalog.get(action_id)
        if not command:
            self.plan_object = {
                "matched_action": "",
                "confidence": 0.0,
                "requires_modification": False,
                "destructive": False,
                "summary": self._build_unsupported_summary(action_id),
                "execution_ready": False,
            }
            self.status = "failed"
            self.message = "Unsupported request"
            self.guidance = self.plan_object["summary"]
            return None

        self._add_step(action_id)
        self.plan_object = {
            "matched_action": action_id,
            "confidence": float(confidence),
            "requires_modification": command.get("role") == "modify",
            "destructive": command.get("risk_level") == "high",
            "summary": summary,
            "execution_ready": True,
        }
        self.status = "ready_to_execute"
        self.message = "Ready to execute"
        self.guidance = "Plan ready for review."
        return dict(self.plan_object)

    def toggle_command(self, command_id):
        for step in self.plan:
            if step.get("id") == command_id:
                step["enabled"] = not step.get("enabled", True)
                step["blocked_reason"] = ""
                return step
        return None

    def get_visible_steps(self):
        return list(self.plan)

    def execute(self, executor):
        if not self.plan:
            self.status = "failed"
            self.message = "Failed"
            return []

        self.status = "executing"
        self.message = "Executing"
        results = []
        self.clear_undo_context()
        for step in self.plan:
            step["executed"] = False
            step["blocked_reason"] = ""
            step["undo_available"] = False
            result = {"step": step, "status": "skipped", "message": "Disabled"}
            if step.get("enabled", True):
                if step.get("role") == "modifying" and not self.allow_destructive:
                    step["blocked_reason"] = "Blocked by destructive-tools gate."
                    result = {
                        "step": step,
                        "status": "blocked",
                        "message": "Modifying command blocked because destructive tools are disabled.",
                    }
                else:
                    try:
                        exec_result = executor(step)
                        if isinstance(exec_result, dict):
                            message = exec_result.get("message", "")
                            undo_context = exec_result.get("undo_context")
                        else:
                            message = exec_result
                            undo_context = None
                        step["executed"] = True
                        if step.get("role") == "modifying" and undo_context:
                            self.set_undo_context(undo_context)
                        result = {"step": step, "status": "executed", "message": message}
                    except Exception as exc:
                        self.status = "failed"
                        self.message = "Failed"
                        step["blocked_reason"] = str(exc)
                        result = {"step": step, "status": "failed", "message": str(exc)}
            else:
                step["blocked_reason"] = "Disabled in current plan."
            results.append(result)

        if self.status != "failed":
            self.status = "idle"
            self.message = "Idle"
        return results

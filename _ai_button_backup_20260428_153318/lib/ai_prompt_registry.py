# -*- coding: utf-8 -*-
import json
import os
import time
import re


class PromptCatalog(object):
    MODELMIND_CATEGORY_ORDER = [
        "QA Presets",
        "Project Intelligence",
        "Schedules",
        "HVAC",
        "Piping",
        "Electrical",
        "QA / BIM",
        "Selection / Categories",
        "Coordination / Spaces",
        "Cleanup / Repair",
        "Views / Sheets / Tags",
        "Quantities",
    ]
    MODELMIND_GROUP_ORDER = [
        "HVAC",
        "Piping",
        "Electrical",
        "Coordination / BIM",
        "Project Context",
        "Links / Imports",
        "AI Agent Planning",
        "Developer Briefs",
        "Pipes",
        "Ducts",
        "Electrical",
        "Bundles",
        "Template-Based",
        "Template-Based / ACO / Bunge",
        "Template-Based / ACO / Bunge / Pipe Product Families",
        "Template-Based / ACO / Bunge / Pipe Fittings",
        "Select",
        "Count",
        "List",
        "Report",
        "Cleanup / Split",
        "Cleanup / Repair",
        "Create",
    ]
    def __init__(self, catalog_path, approved_path):
        self.catalog_path = catalog_path
        self.approved_path = approved_path

    def _read_list(self, path):
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r") as stream:
                loaded = json.load(stream)
            if isinstance(loaded, list):
                return loaded
        except Exception:
            pass
        return []

    def _write_list(self, path, items):
        with open(path, "w") as stream:
            json.dump(items, stream, indent=2, sort_keys=True)

    def get_base_entries(self):
        return self._read_list(self.catalog_path)

    def get_approved_entries(self):
        return self._read_list(self.approved_path)

    def get_enabled_entries(self):
        items = []
        for entry in self.get_base_entries():
            if entry.get("enabled", True):
                items.append(self._normalize_entry(entry))
        return items

    def get_entry_by_id(self, entry_id):
        target = (entry_id or "").strip().lower()
        if not target:
            return None
        for entry in self.get_enabled_entries():
            if (entry.get("id") or "").strip().lower() == target:
                return entry
        return None

    def _normalize_match_text(self, text):
        value = (text or "").strip().lower()
        if not value:
            return ""
        value = value.replace("\\", "/")
        value = re.sub(r"\s+", " ", value)
        value = re.sub(r"\s*,\s*", ",", value)
        value = re.sub(r"\s*:\s*", ":", value)
        value = re.sub(r"\s*/\s*", " / ", value)
        value = re.sub(r"\s+", " ", value).strip()
        return value

    def _normalize_entry(self, entry):
        normalized = dict(entry or {})
        normalized["discipline"] = normalized.get("discipline", "General")
        normalized["scope_type"] = normalized.get("scope_type", "project")
        normalized["planner_aliases"] = list(normalized.get("planner_aliases") or [])
        normalized["aliases"] = list(normalized.get("aliases") or normalized.get("planner_aliases") or [])
        normalized["canonical_prompt"] = normalized.get("canonical_prompt", normalized.get("prompt_text", ""))
        normalized["example_prompts"] = list(normalized.get("example_prompts") or normalized.get("aliases") or normalized.get("planner_aliases") or [])
        normalized["deterministic_handler"] = normalized.get("deterministic_handler", "")
        normalized["reviewed_steps"] = list(normalized.get("reviewed_steps") or [])
        normalized["requires_confirmation"] = bool(normalized.get("requires_confirmation", False))
        normalized["available_to_agent"] = bool(
            normalized.get(
                "available_to_agent",
                bool(normalized["deterministic_handler"] or normalized["reviewed_steps"]),
            )
        )
        normalized["visible_in_modelmind"] = bool(
            normalized.get(
                "visible_in_modelmind",
                bool(normalized["deterministic_handler"] or normalized["reviewed_steps"]),
            )
        )
        normalized["validation_state"] = normalized.get("validation_state", self._default_validation_state(normalized))
        existing_category = normalized.get("category", "")
        if normalized["deterministic_handler"] or normalized["reviewed_steps"]:
            normalized["category"] = (
                existing_category
                if existing_category in self.MODELMIND_CATEGORY_ORDER
                else self._derive_modelmind_category(normalized)
            )
        else:
            normalized["category"] = existing_category or self._derive_modelmind_category(normalized)
        normalized["group"] = normalized.get("group", self._derive_modelmind_group(normalized))
        return normalized

    def _default_validation_state(self, entry):
        live_validated = set(
            [
                "select-all-ducts",
                "count-selected-ducts",
                "count-ducts-active-view",
                "list-ducts-active-view",
                "create-sheet-reviewed-template",
                "report-selected-elements-by-category",
                "report-selected-elements-by-type",
                "report-missing-parameters-from-selection",
                "health-check-active-view-selection",
                "create-pipe-schedule-by-level",
                "create-pipe-fitting-schedule-by-level",
                "create-duct-schedule-by-level",
                "create-duct-fitting-schedule-by-level",
                "create-electrical-fixture-equipment-schedule-by-level",
                "create-schedule-bundle-by-level",
            ]
        )
        if entry.get("id") in live_validated:
            return "live_validated"
        if entry.get("deterministic_handler") or entry.get("reviewed_steps"):
            return "structural_only"
        return entry.get("validation_state", "legacy")

    def _derive_modelmind_category(self, entry):
        title = (entry.get("title") or "").lower()
        handler = (entry.get("deterministic_handler") or "").lower()
        discipline = (entry.get("discipline") or "").lower()
        if "preset" in title:
            return "QA Presets"
        if "room" in title or "space" in title:
            return "Coordination / Spaces"
        if "duplicate" in title:
            return "Cleanup / Repair"
        if "split" in title:
            return "Piping"
        if "schedule" in title:
            return "Schedules"
        if "category" in title:
            return "Selection / Categories"
        if "tag" in title:
            return "Views / Sheets / Tags"
        if "rename active view" in title:
            return "Views / Sheets / Tags"
        if "total length" in title and "selected" in title:
            return "Quantities"
        if handler in ("create_sheet_reviewed_template", "create_3d_view_from_selection"):
            return "Views / Sheets / Tags"
        if "sheet" in title or "3d view" in title or "view from" in title or "view" in title:
            return "Views / Sheets / Tags"
        if "hvac" in discipline or "duct" in discipline:
            return "HVAC"
        if "piping" in discipline or "pipe" in discipline:
            return "Piping"
        if "electrical" in discipline:
            return "Electrical"
        if "qa / bim" in discipline:
            return "QA / BIM"
        return "QA / BIM"

    def _derive_modelmind_group(self, entry):
        category = (entry.get("category") or "").lower()
        title = (entry.get("title") or "").lower()
        role = (entry.get("role") or "").lower()
        if category == "qa presets":
            return entry.get("group", entry.get("discipline", "Coordination / BIM"))
        if role == "modify" or "create " in title:
            return "Create"
        if "split" in title:
            return "Cleanup / Split"
        if "duplicate" in title:
            return "Cleanup / Repair"
        if category in ("select all", "select"):
            return "Select"
        if category == "count":
            return "Count"
        if category == "lists":
            return "List"
        if category in ("reports", "totals", "clash check"):
            return "Report"
        if title.startswith("select "):
            return "Select"
        if title.startswith("count "):
            return "Count"
        if title.startswith("list "):
            return "List"
        return "Report"

    def is_shared_reviewed_action(self, entry):
        entry = self._normalize_entry(entry)
        return bool(
            entry.get("enabled", True)
            and (entry.get("deterministic_handler") or entry.get("reviewed_steps"))
        )

    def get_entry_by_prompt(self, prompt_text):
        target = self._normalize_match_text(prompt_text)
        for entry in self.get_enabled_entries():
            prompt = self._normalize_match_text(entry.get("canonical_prompt") or entry.get("prompt_text") or "")
            title = self._normalize_match_text(entry.get("title") or "")
            aliases = [
                self._normalize_match_text(alias)
                for alias in (entry.get("aliases") or entry.get("planner_aliases") or [])
            ]
            examples = [self._normalize_match_text(example) for example in entry.get("example_prompts") or []]
            if prompt == target or title == target or target in aliases or target in examples:
                return entry
        if "hvac qa preset" in target:
            return self.get_entry_by_id("hvac-qa-preset")
        if "piping qa preset" in target:
            return self.get_entry_by_id("piping-qa-preset")
        if "electrical qa preset" in target:
            return self.get_entry_by_id("electrical-qa-preset")
        if "coordination qa preset" in target or "bim qa preset" in target:
            return self.get_entry_by_id("coordination-bim-qa-preset")
        if "scan current project" in target or target == "project context" or "scan this revit model" in target:
            return self.get_entry_by_id("scan-current-project")
        if "summarize current project" in target or "what is in this revit model" in target or "what is in this model" in target:
            return self.get_entry_by_id("summarize-current-project")
        if "ask ai agent for a plan" in target or "ask agent for project plan" in target or "what should i check first in this project" in target:
            return self.get_entry_by_id("ask-agent-for-project-plan")
        if "create codex task brief" in target or "generate developer task" in target or "prepare codex instruction" in target or "make implementation brief" in target:
            return self.get_entry_by_id("create-codex-task-brief")
        if "split" in target and "pipe" in target:
            return self.get_entry_by_id("split-selected-pipes")
        if "duplicate" in target:
            if "remove" in target or "delete" in target or "clean" in target:
                return self.get_entry_by_id("remove-duplicates")
            return self.get_entry_by_id("report-duplicates")
        if "room to space" in target or "space vs room" in target or "room space check" in target or "rooms vs spaces" in target:
            return self.get_entry_by_id("report-room-space-mismatches")
        if "rooms without spaces" in target:
            return self.get_entry_by_id("report-rooms-without-matching-spaces")
        if "spaces without rooms" in target:
            return self.get_entry_by_id("report-spaces-without-matching-rooms")
        if "aco" in target and "template" in target:
            if "1.4301" in target and "single socket" in target:
                if "summary" in target:
                    return self.get_entry_by_id("create-aco-14301-single-socket-pipe-summary-from-template")
                return self.get_entry_by_id("create-aco-14301-single-socket-pipe-schedule-from-template")
            if "1.4404" in target and "single socket" in target:
                if "summary" in target:
                    return self.get_entry_by_id("create-aco-14404-single-socket-pipe-summary-from-template")
                return self.get_entry_by_id("create-aco-14404-single-socket-pipe-schedule-from-template")
            if "1.4404" in target and "double socket" in target:
                if "summary" in target:
                    return self.get_entry_by_id("create-aco-14404-double-socket-pipe-summary-from-template")
                return self.get_entry_by_id("create-aco-14404-double-socket-pipe-schedule-from-template")
            if "prefab" in target:
                return self.get_entry_by_id("create-aco-prefab-schedule-bundle-from-template")
            if "pipe fitting" in target and "summary" in target:
                return self.get_entry_by_id("create-aco-pipe-fitting-summary-from-template")
            if "pipe fitting" in target:
                return self.get_entry_by_id("create-aco-pipe-fitting-schedule-from-template")
            if "pipe" in target and "summary" in target:
                return self.get_entry_by_id("create-aco-pipe-summary-from-template")
            if "pipe" in target:
                return self.get_entry_by_id("create-aco-pipe-schedule-from-template")
        if "schedule bundle" in target and ("level" in target or "reference level" in target):
            return self.get_entry_by_id("create-schedule-bundle-by-level")
        if "pipe fitting" in target and "schedule" in target and ("level" in target or "reference level" in target):
            return self.get_entry_by_id("create-pipe-fitting-schedule-by-level")
        if "duct fitting" in target and "schedule" in target and ("level" in target or "reference level" in target):
            return self.get_entry_by_id("create-duct-fitting-schedule-by-level")
        if "pipe" in target and "schedule" in target and ("level" in target or "reference level" in target):
            return self.get_entry_by_id("create-pipe-schedule-by-level")
        if "duct" in target and "schedule" in target and ("level" in target or "reference level" in target):
            return self.get_entry_by_id("create-duct-schedule-by-level")
        if "conduit" in target and "schedule" in target and ("level" in target or "reference level" in target):
            return self.get_entry_by_id("create-conduit-schedule-by-level")
        if "electrical" in target and "schedule" in target and ("fixture" in target or "equipment" in target):
            return self.get_entry_by_id("create-electrical-fixture-equipment-schedule-by-level")
        if "categories list" in target or "category ids" in target:
            return self.get_entry_by_id("categories-list-and-id")
        if "rename active view" in target:
            return self.get_entry_by_id("rename-active-view")
        if "align" in target and "tag" in target:
            return self.get_entry_by_id("align-selected-tags")
        if "total length" in target and ("linear" in target or "mep" in target):
            if "active view" in target:
                return self.get_entry_by_id("report-total-length-active-view-linear-mep")
            return self.get_entry_by_id("report-total-length-selected-linear-mep")
        if target.startswith("select all ") or "select all elements of category" in target:
            return self.get_entry_by_id("select-all-elements-of-category")
        if target.startswith("count all ") or "count all elements of category" in target:
            return self.get_entry_by_id("count-all-elements-of-category")
        if target.startswith("list all ") or "list all elements of category" in target:
            return self.get_entry_by_id("list-all-elements-of-category")
        return None

    def _matches_filter(self, entry, filter_text):
        if not filter_text:
            return True
        haystacks = [
            entry.get("title", ""),
            entry.get("category", ""),
            entry.get("prompt_text", ""),
            entry.get("canonical_prompt", ""),
            entry.get("role", ""),
            entry.get("mode", ""),
            entry.get("discipline", ""),
            entry.get("scope_type", ""),
            entry.get("validation_state", ""),
            " ".join(entry.get("aliases") or []),
            " ".join(entry.get("planner_aliases") or []),
            " ".join(entry.get("example_prompts") or []),
        ]
        ft = filter_text.lower()
        for text in haystacks:
            if ft in str(text).lower():
                return True
        return False

    def _group_entries(self, entries):
        grouped = {}
        for entry in entries:
            category = entry.get("category", "QA / BIM")
            group = entry.get("group", "Report")
            grouped.setdefault(category, {})
            grouped[category].setdefault(group, [])
            grouped[category][group].append(entry)
        return grouped

    def _build_group_nodes(self, grouped, branch_kind):
        sections = []
        ordered_categories = []
        for category in self.MODELMIND_CATEGORY_ORDER:
            if category in grouped:
                ordered_categories.append(category)
        for category in sorted(grouped.keys()):
            if category not in ordered_categories:
                ordered_categories.append(category)

        for category in ordered_categories:
            child_groups = []
            current_groups = grouped.get(category, {})
            ordered_groups = []
            for group_name in self.MODELMIND_GROUP_ORDER:
                if group_name in current_groups:
                    ordered_groups.append(group_name)
            for group_name in sorted(current_groups.keys()):
                if group_name not in ordered_groups:
                    ordered_groups.append(group_name)
            for group_name in ordered_groups:
                items = sorted(
                    current_groups.get(group_name, []),
                    key=lambda item: (item.get("title") or item.get("canonical_prompt") or ""),
                )
                child_groups.append(
                    {"header": group_name, "items": items, "kind": branch_kind}
                )
            sections.append({"header": category, "groups": child_groups, "kind": branch_kind})
        return sections

    def _derive_recipe_category(self, entry):
        category = (entry.get("category") or "").strip()
        prompt_text = (entry.get("prompt_text") or "").lower()
        title = (entry.get("title") or "").lower()
        if category in self.MODELMIND_CATEGORY_ORDER:
            return category
        if "schedule" in prompt_text or "schedule" in title:
            return "Schedules"
        if "pipe" in prompt_text or "pipe" in title:
            return "Piping"
        if "electrical" in prompt_text or "fixture" in prompt_text or "device" in prompt_text or "circuit" in prompt_text:
            return "Electrical"
        if "sheet" in prompt_text or "view" in prompt_text or "3d" in prompt_text:
            return "Views / Sheets"
        if "duct" in prompt_text or "hvac" in prompt_text:
            return "HVAC"
        return "QA / BIM"

    def get_tree_sections(self, filter_text=None, recent_prompts=None):
        filter_text = filter_text or ""
        reviewed_entries = []
        for entry in self.get_reviewed_actions():
            if entry.get("visible_in_modelmind") and self._matches_filter(entry, filter_text):
                reviewed_entries.append(entry)

        sections = self._build_group_nodes(self._group_entries(reviewed_entries), "catalog")

        recent = []
        for entry in recent_prompts or []:
            normalized = self._normalize_entry(entry)
            if self._matches_filter(normalized, filter_text):
                recent.append(normalized)
        if recent:
            sections.append(
                {
                    "header": "Recent Prompts",
                    "groups": [{"header": "Recent", "items": recent, "kind": "recent"}],
                    "kind": "recent",
                }
            )

        approved = []
        for entry in self.get_approved_entries():
            normalized = self._normalize_entry(entry)
            normalized["category"] = self._derive_recipe_category(normalized)
            normalized["group"] = "Approved"
            if self._matches_filter(normalized, filter_text):
                approved.append(normalized)
        if approved:
            approved_groups = []
            grouped_approved = {}
            for entry in approved:
                grouped_approved.setdefault(entry.get("category", "QA / BIM"), []).append(entry)
            for category in self.MODELMIND_CATEGORY_ORDER:
                if category in grouped_approved:
                    approved_groups.append(
                        {
                            "header": category,
                            "items": sorted(
                                grouped_approved.pop(category),
                                key=lambda item: (item.get("title") or ""),
                            ),
                            "kind": "approved",
                        }
                    )
            for category in sorted(grouped_approved.keys()):
                approved_groups.append(
                    {
                        "header": category,
                        "items": sorted(
                            grouped_approved.get(category, []),
                            key=lambda item: (item.get("title") or ""),
                        ),
                        "kind": "approved",
                    }
                )
            sections.append(
                {
                    "header": "Approved Recipes",
                    "groups": approved_groups,
                    "kind": "approved",
                }
            )
        return sections

    def get_reviewed_actions(self):
        actions = []
        for entry in self.get_enabled_entries():
            if self.is_shared_reviewed_action(entry):
                actions.append(self._normalize_entry(entry))
        return actions

    def get_agent_commands(self):
        commands = []
        for entry in self.get_reviewed_actions():
            commands.append(dict(entry))
        return commands

    def save_approved_recipe(self, metadata, code_text, source_entry=None):
        source_entry = source_entry or {}
        metadata = metadata or {}
        items = self.get_approved_entries()
        recipe = {
            "id": metadata.get("id") or "approved-{0}".format(int(time.time())),
            "title": metadata.get("title") or source_entry.get("title") or "Approved recipe",
            "category": metadata.get("category") or source_entry.get("category", "Approved Recipes"),
            "group": metadata.get("group") or source_entry.get("group", "Approved"),
            "role": metadata.get("role") or source_entry.get("role", "modify"),
            "risk_level": metadata.get("risk_level") or source_entry.get("risk_level", "medium"),
            "mode": source_entry.get("mode", "deterministic"),
            "canonical_prompt": source_entry.get("canonical_prompt") or metadata.get("source_prompt") or metadata.get("prompt_text") or "",
            "prompt_text": metadata.get("source_prompt") or metadata.get("prompt_text") or "",
            "source_prompt": metadata.get("source_prompt") or metadata.get("prompt_text") or "",
            "aliases": list(source_entry.get("aliases") or source_entry.get("planner_aliases") or []),
            "example_prompts": list(source_entry.get("example_prompts") or []),
            "validation_state": "approved_recipe",
            "visible_in_modelmind": False,
            "available_to_agent": False,
            "enabled": bool(metadata.get("enabled", True)),
            "source": "approved_recipe",
            "stored_code": code_text,
            "approved_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        items.append(recipe)
        self._write_list(self.approved_path, items)
        return recipe

#!/usr/bin/env python3
"""Fail-closed validation for parallel-feature-workflow artifacts and runtime state."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from fnmatch import fnmatchcase
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Any, Callable, Iterable


OID_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
AC_ID_RE = re.compile(r"AC-[A-Za-z0-9][A-Za-z0-9._-]*\Z")
AVAILABILITY = {
    "available",
    "absent",
    "unloadable",
    "execution_blocked",
    "disabled_by_user",
}
ENGINE_RULES = {
    "planning_engine": "solution-planner",
    "review_engine": "local-pr-review",
}
PLANNING_MODES = {"new_plan", "existing_plan_review", "fallback"}
DEPENDENCY_TYPES = {
    "contract",
    "artifact",
    "code",
    "build",
    "test",
    "integration",
}
UNBLOCK_CONDITIONS = {
    "contract_approved",
    "artifact_available",
    "reviewed_head",
    "build_passed",
    "integration_checkpoint",
    "tests_passed",
}
DEPENDENCY_PAIRS = {
    "contract": "contract_approved",
    "artifact": "artifact_available",
    "code": "reviewed_head",
    "build": "build_passed",
    "test": "tests_passed",
    "integration": "integration_checkpoint",
}
TASK_STATUSES = {
    "planned",
    "ready",
    "running",
    "blocked",
    "stale",
    "failed",
    "implemented",
    "review_incomplete",
    "review_blocked",
    "reviewed",
    "integrated",
    "cancelled",
}
REVIEW_STATUSES = {"pending", "passed", "blocked", "incomplete"}
INTEGRATION_STATUSES = {
    "planned",
    "running",
    "blocked",
    "failed",
    "integrated",
    "review_incomplete",
    "review_blocked",
    "reviewed",
}
SCOPE_SOURCE_KINDS = {"plan", "issue", "user", "pr"}
SCOPE_CONTRACT_FIELDS = {
    "version",
    "status",
    "confirmation_basis",
    "sources",
    "objective",
    "in_scope",
    "out_of_scope",
    "acceptance_criteria",
    "integration_constraints",
    "open_questions",
}
WORKFLOW_HAPPY_PATH = (
    "planning",
    "plan_approved",
    "orchestration_draft",
    "orchestration_approved",
    "docs_ready",
    "plan_baselined",
    "implementing",
    "branch_reviewing",
    "branches_ready",
    "integrating",
    "integration_reviewing",
    "merge_ready",
)
WORKFLOW_STATUSES = set(WORKFLOW_HAPPY_PATH) | {
    "plan_blocked",
    "orchestration_blocked",
    "not_ready",
    "cancelled",
}
WORKFLOW_TRANSITIONS = set(zip(WORKFLOW_HAPPY_PATH, WORKFLOW_HAPPY_PATH[1:])) | {
    ("planning", "plan_blocked"),
    ("plan_blocked", "planning"),
    ("orchestration_draft", "orchestration_blocked"),
    ("orchestration_blocked", "orchestration_draft"),
}
WORKFLOW_TRANSITIONS |= {
    (status, terminal)
    for status in WORKFLOW_STATUSES - {"not_ready", "cancelled", "merge_ready"}
    for terminal in {"not_ready", "cancelled"}
}
SEVERITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
BLOCKING_SCOPE_RELATIONS = {"in_scope", "required_integration", "scope_drift"}
BLOCKING_CHANGE_RELATIONS = {
    "introduced_by_change",
    "amplified_by_change",
    "unmet_plan_requirement",
}
SCOPE_RELATIONS = BLOCKING_SCOPE_RELATIONS | {"out_of_scope", "uncertain"}
CHANGE_RELATIONS = BLOCKING_CHANGE_RELATIONS | {
    "pre_existing_unchanged",
    "not_attributable",
    "uncertain",
}
REQUIRED_REVIEW_VIEWS = {
    "correctness",
    "error-boundaries",
    "state-lifecycle",
    "api-integration",
    "tests-regression",
}
REVIEW_FINDING_FIELDS = {
    "title",
    "severity",
    "category",
    "file",
    "line_start",
    "line_end",
    "summary",
    "evidence",
    "trigger",
    "suggested_fix",
    "suggested_test",
    "confidence",
    "scope_relation",
    "change_relation",
    "scope_basis",
    "attribution_evidence",
    "source_lanes",
    "source_candidate_ids",
}
CONDITION_GATE_RANK = {
    "dispatch": 0,
    "branch_review": 1,
    "integration": 2,
}
CONDITION_PROOF_KINDS = {
    "test_result",
    "artifact_result",
    "external_evidence",
    "user_confirmation",
}


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class GitEvidenceError(RuntimeError):
    pass


def diagnostic(code: str, message: str) -> Diagnostic:
    return Diagnostic(code=code, message=message)


def is_str(value: Any) -> bool:
    return isinstance(value, str)


def is_resolved_string(value: Any) -> bool:
    return (
        is_str(value)
        and bool(value.strip())
        and "{{" not in value
        and "}}" not in value
    )


def is_oid(value: Any) -> bool:
    return is_str(value) and OID_RE.fullmatch(value) is not None


def is_sha256(value: Any) -> bool:
    return is_str(value) and SHA256_RE.fullmatch(value) is not None


def is_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(is_resolved_string(item) for item in value)


def list_or_empty(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def is_safe_relative_path(value: Any) -> bool:
    if not is_resolved_string(value):
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts


def canonical_scope_id(contract: dict[str, Any]) -> str:
    canonical = json.dumps(
        contract,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonical_json_sha256(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _plan_acceptance_criteria_map(
    manifest: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    plan = manifest.get("plan")
    if not isinstance(plan, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in list_or_empty(plan.get("acceptance_criteria")):
        if not isinstance(item, dict):
            continue
        criterion_id = item.get("id")
        if (
            is_str(criterion_id)
            and AC_ID_RE.fullmatch(criterion_id)
            and criterion_id not in result
        ):
            result[criterion_id] = item
    return result


def _review_scope_acceptance_criteria(
    envelope: Any,
) -> list[str]:
    if not isinstance(envelope, dict):
        return []
    contract = envelope.get("contract")
    if not isinstance(contract, dict):
        return []
    criteria = contract.get("acceptance_criteria")
    return criteria if is_str_list(criteria) else []


def select_engine(
    capability: str,
    availability: str,
    *,
    user_selected_fallback: bool = False,
) -> dict[str, Any]:
    if capability == "planner":
        primary = "solution-planner"
    elif capability == "review":
        primary = "local-pr-review"
    else:
        raise ValueError(f"unknown capability: {capability}")

    if availability == "available":
        return {"engine": primary, "fallback": False, "blocked": False}
    if availability in {"absent", "unloadable"}:
        return {"engine": "fallback", "fallback": True, "blocked": False}
    if availability == "disabled_by_user":
        if user_selected_fallback:
            return {"engine": "fallback", "fallback": True, "blocked": False}
        return {"engine": primary, "fallback": False, "blocked": True}
    if availability == "execution_blocked":
        return {"engine": primary, "fallback": False, "blocked": True}
    raise ValueError(f"unknown availability: {availability}")


def _require_object(
    value: Any,
    keys: Iterable[str],
    location: str,
    errors: list[Diagnostic],
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        errors.append(diagnostic("E_TYPE", f"{location} must be an object"))
        return None
    missing = sorted(key for key in keys if key not in value)
    if missing:
        errors.append(
            diagnostic(
                "E_REQUIRED",
                f"{location} missing fields: {', '.join(missing)}",
            )
        )
        return None
    return value


def _validate_confirmation(
    value: Any,
    *,
    location: str,
    subject_type: str,
    subject_id: Any,
    revision: Any,
    confirmation_basis: Any,
    errors: list[Diagnostic],
) -> None:
    record = _require_object(
        value,
        (
            "subject_type",
            "subject_id",
            "revision",
            "confirmation_basis",
            "confirmed_by",
            "confirmed_at",
            "scoped_skip",
        ),
        location,
        errors,
    )
    if record is None:
        return
    expected = {
        "subject_type": subject_type,
        "subject_id": subject_id,
        "revision": revision,
        "confirmation_basis": confirmation_basis,
    }
    for field, expected_value in expected.items():
        if record[field] != expected_value:
            errors.append(
                diagnostic(
                    "E_CONFIRMATION_IDENTITY",
                    f"{location}.{field} is not bound to the confirmed subject",
                )
            )
    if not is_resolved_string(record["confirmed_by"]):
        errors.append(
            diagnostic(
                "E_CONFIRMATION_ACTOR",
                f"{location}.confirmed_by is required",
            )
        )
    if record["confirmed_at"] is not None and not is_resolved_string(
        record["confirmed_at"]
    ):
        errors.append(
            diagnostic(
                "E_CONFIRMATION_TIME",
                f"{location}.confirmed_at must be null or a recorded timestamp",
            )
        )
    if record["scoped_skip"] is not False:
        errors.append(
            diagnostic(
                "E_CONFIRMATION_SKIP",
                f"{location}.scoped_skip must be false for a full workflow confirmation",
            )
        )


def _validate_workflow_control(
    manifest: dict[str, Any],
    runtime: dict[str, Any],
    phase: str,
    errors: list[Diagnostic],
) -> None:
    status = runtime.get("workflow_status")
    history = runtime.get("transition_history")
    if not is_str(status) or status not in WORKFLOW_STATUSES:
        errors.append(
            diagnostic(
                "E_WORKFLOW_STATUS",
                "runtime.workflow_status is invalid",
            )
        )
    if not isinstance(history, list):
        errors.append(
            diagnostic(
                "E_TRANSITION_HISTORY",
                "runtime.transition_history must be a list",
            )
        )
        return

    plan_revision = dict_or_empty(manifest.get("plan")).get("revision")
    orchestration_revision = dict_or_empty(manifest.get("orchestration")).get(
        "revision"
    )
    previous_to: str | None = None
    previous_plan_revision: Any = None
    previous_orchestration_revision: Any = None
    for index, item in enumerate(history):
        record = _require_object(
            item,
            (
                "event",
                "from_status",
                "to_status",
                "plan_revision",
                "orchestration_revision",
                "evidence_refs",
            ),
            f"runtime.transition_history[{index}]",
            errors,
        )
        if record is None:
            continue
        event = record["event"]
        from_status = record["from_status"]
        to_status = record["to_status"]
        if not is_str(event) or event not in {"advance", "rebaseline"}:
            errors.append(
                diagnostic(
                    "E_TRANSITION_EVENT",
                    f"runtime.transition_history[{index}].event is invalid",
                )
            )
        elif event == "advance":
            if (
                not is_str(from_status)
                or not is_str(to_status)
                or (from_status, to_status) not in WORKFLOW_TRANSITIONS
            ):
                errors.append(
                    diagnostic(
                        "E_TRANSITION_ILLEGAL",
                        f"runtime.transition_history[{index}] is not a legal transition",
                    )
                )
            if previous_plan_revision is not None and (
                record["plan_revision"] != previous_plan_revision
                or record["orchestration_revision"]
                != previous_orchestration_revision
            ):
                errors.append(
                    diagnostic(
                        "E_TRANSITION_REVISION_CHANGE",
                        f"runtime.transition_history[{index}] changes revision without a rebaseline event",
                    )
                )
        else:
            rebaseline = _require_object(
                record,
                (
                    "reason",
                    "previous_plan_revision",
                    "previous_orchestration_revision",
                    "previous_plan_sha",
                    "new_plan_sha",
                    "invalidated_task_ids",
                ),
                f"runtime.transition_history[{index}]",
                errors,
            )
            rebaseline_sources = set(WORKFLOW_HAPPY_PATH[5:]) | {"not_ready"}
            if (
                not is_str(from_status)
                or from_status not in rebaseline_sources
                or to_status != "plan_baselined"
            ):
                errors.append(
                    diagnostic(
                        "E_REBASELINE_TRANSITION",
                        f"runtime.transition_history[{index}] must return a baselined workflow to plan_baselined",
                    )
                )
            if rebaseline is not None:
                if (
                    rebaseline["previous_plan_revision"]
                    != previous_plan_revision
                    or rebaseline["previous_orchestration_revision"]
                    != previous_orchestration_revision
                ):
                    errors.append(
                        diagnostic(
                            "E_REBASELINE_PREVIOUS_IDENTITY",
                            f"runtime.transition_history[{index}] does not preserve prior revisions",
                        )
                    )
                plan_changed = (
                    record["plan_revision"]
                    != rebaseline["previous_plan_revision"]
                )
                orchestration_changed = (
                    record["orchestration_revision"]
                    != rebaseline["previous_orchestration_revision"]
                )
                if not orchestration_changed or (
                    plan_changed and not orchestration_changed
                ):
                    errors.append(
                        diagnostic(
                            "E_REBASELINE_REVISION",
                            f"runtime.transition_history[{index}] must advance orchestration revision",
                        )
                    )
                if (
                    not is_oid(rebaseline["previous_plan_sha"])
                    or not is_oid(rebaseline["new_plan_sha"])
                    or rebaseline["previous_plan_sha"]
                    == rebaseline["new_plan_sha"]
                ):
                    errors.append(
                        diagnostic(
                            "E_REBASELINE_PLAN_SHA",
                            f"runtime.transition_history[{index}] must bind distinct old/new PLAN_SHA values",
                        )
                    )
                if (
                    not is_str_list(rebaseline["invalidated_task_ids"])
                    or not rebaseline["invalidated_task_ids"]
                    or not is_resolved_string(rebaseline["reason"])
                ):
                    errors.append(
                        diagnostic(
                            "E_REBASELINE_INVALIDATION",
                            f"runtime.transition_history[{index}] must record affected tasks and reason",
                        )
                    )
                if (
                    record["plan_revision"] == plan_revision
                    and record["orchestration_revision"]
                    == orchestration_revision
                    and rebaseline["new_plan_sha"]
                    != dict_or_empty(runtime.get("git")).get("plan_sha")
                ):
                    errors.append(
                        diagnostic(
                            "E_REBASELINE_PLAN_SHA",
                            f"runtime.transition_history[{index}] new PLAN_SHA is stale",
                        )
                    )
        if previous_to is not None and record["from_status"] != previous_to:
            errors.append(
                diagnostic(
                    "E_TRANSITION_DISCONTINUITY",
                    f"runtime.transition_history[{index}] does not continue the prior state",
                )
            )
        previous_to = to_status if is_str(to_status) else None
        if not is_resolved_string(record["plan_revision"]) or not is_resolved_string(
            record["orchestration_revision"]
        ):
            errors.append(
                diagnostic(
                    "E_TRANSITION_REVISION",
                    f"runtime.transition_history[{index}] revisions are invalid",
                )
            )
        previous_plan_revision = record["plan_revision"]
        previous_orchestration_revision = record["orchestration_revision"]
        if not is_str_list(record["evidence_refs"]) or not record["evidence_refs"]:
            errors.append(
                diagnostic(
                    "E_TRANSITION_EVIDENCE",
                    f"runtime.transition_history[{index}].evidence_refs must be non-empty",
                )
            )

    if history:
        first = history[0] if isinstance(history[0], dict) else {}
        last = history[-1] if isinstance(history[-1], dict) else {}
        if first.get("from_status") != "planning":
            errors.append(
                diagnostic(
                    "E_TRANSITION_ORIGIN",
                    "transition history must start from planning",
                )
            )
        if last.get("to_status") != status:
            errors.append(
                diagnostic(
                    "E_TRANSITION_STATUS_STALE",
                    "workflow_status must equal the last recorded transition",
                )
            )
        if (
            last.get("plan_revision") != plan_revision
            or last.get("orchestration_revision") != orchestration_revision
        ):
            errors.append(
                diagnostic(
                    "E_TRANSITION_CURRENT_REVISION",
                    "last transition must bind the current plan and orchestration revisions",
                )
            )
    elif status != "planning":
        errors.append(
            diagnostic(
                "E_TRANSITION_HISTORY",
                "a non-planning workflow status requires transition history",
            )
        )

    if phase == "dispatch":
        allowed = set(WORKFLOW_HAPPY_PATH[WORKFLOW_HAPPY_PATH.index("implementing") :])
        if not is_str(status) or status not in allowed:
            errors.append(
                diagnostic(
                    "E_WORKFLOW_PHASE_STATUS",
                    "dispatch requires implementing or a later successful state",
                )
            )
    elif phase == "branch-ready":
        allowed = set(
            WORKFLOW_HAPPY_PATH[WORKFLOW_HAPPY_PATH.index("branch_reviewing") :]
        )
        if not is_str(status) or status not in allowed:
            errors.append(
                diagnostic(
                    "E_WORKFLOW_PHASE_STATUS",
                    "branch-ready requires branch_reviewing or a later successful state",
                )
            )
    elif phase == "integration-ready":
        allowed = set(
            WORKFLOW_HAPPY_PATH[WORKFLOW_HAPPY_PATH.index("branches_ready") :]
        )
        if not is_str(status) or status not in allowed:
            errors.append(
                diagnostic(
                    "E_WORKFLOW_PHASE_STATUS",
                    "integration-ready requires branches_ready or a later successful state",
                )
            )
    elif phase == "merge-ready" and status != "merge_ready":
        errors.append(
            diagnostic(
                "E_WORKFLOW_PHASE_STATUS",
                "merge-ready requires workflow_status=merge_ready",
            )
        )


def _validate_scope_contract(
    contract: Any,
    location: str,
    errors: list[Diagnostic],
) -> dict[str, Any] | None:
    if not isinstance(contract, dict):
        errors.append(diagnostic("E_REVIEW_SCOPE_TYPE", f"{location} must be an object"))
        return None
    missing = sorted(SCOPE_CONTRACT_FIELDS - set(contract))
    extra = sorted(set(contract) - SCOPE_CONTRACT_FIELDS)
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unknown {', '.join(extra)}")
        errors.append(
            diagnostic(
                "E_REVIEW_SCOPE_SCHEMA",
                f"{location} has invalid fields: {'; '.join(details)}",
            )
        )
        return None
    if type(contract["version"]) is not int or contract["version"] != 1:
        errors.append(
            diagnostic("E_REVIEW_SCOPE_VERSION", f"{location}.version must be 1")
        )
    if contract["status"] != "confirmed":
        errors.append(
            diagnostic(
                "E_REVIEW_SCOPE_STATUS",
                f"{location}.status must be confirmed",
            )
        )
    for field in ("confirmation_basis", "objective"):
        if not is_resolved_string(contract[field]):
            errors.append(
                diagnostic(
                    "E_REVIEW_SCOPE_CONTENT",
                    f"{location}.{field} must be a non-empty string",
                )
            )

    list_requirements = {
        "in_scope": 1,
        "out_of_scope": 0,
        "acceptance_criteria": 1,
        "integration_constraints": 0,
        "open_questions": 0,
    }
    for field, minimum in list_requirements.items():
        value = contract[field]
        if (
            not isinstance(value, list)
            or len(value) < minimum
            or any(not is_resolved_string(item) for item in value)
        ):
            errors.append(
                diagnostic(
                    "E_REVIEW_SCOPE_CONTENT",
                    f"{location}.{field} is invalid",
                )
            )
    if isinstance(contract["open_questions"], list) and contract["open_questions"]:
        errors.append(
            diagnostic(
                "E_REVIEW_SCOPE_OPEN",
                f"{location} has unresolved open questions",
            )
        )
    if isinstance(contract["in_scope"], list) and isinstance(
        contract["out_of_scope"], list
    ):
        in_scope = {
            item.strip().casefold()
            for item in contract["in_scope"]
            if is_resolved_string(item)
        }
        out_of_scope = {
            item.strip().casefold()
            for item in contract["out_of_scope"]
            if is_resolved_string(item)
        }
        if in_scope & out_of_scope:
            errors.append(
                diagnostic(
                    "E_REVIEW_SCOPE_OVERLAP",
                    f"{location} has overlapping in_scope and out_of_scope",
                )
            )

    sources = contract["sources"]
    if not isinstance(sources, list) or not sources:
        errors.append(
            diagnostic(
                "E_REVIEW_SCOPE_SOURCES",
                f"{location}.sources must be a non-empty list",
            )
        )
    else:
        for index, source in enumerate(sources):
            source_location = f"{location}.sources[{index}]"
            if not isinstance(source, dict):
                errors.append(
                    diagnostic(
                        "E_REVIEW_SCOPE_SOURCE",
                        f"{source_location} must be an object",
                    )
                )
                continue
            if (
                set(source) - {"kind", "reference", "revision"}
                or not {"kind", "reference"}.issubset(source)
            ):
                errors.append(
                    diagnostic(
                        "E_REVIEW_SCOPE_SOURCE",
                        f"{source_location} has invalid fields",
                    )
                )
                continue
            if (
                not is_str(source["kind"])
                or source["kind"] not in SCOPE_SOURCE_KINDS
            ):
                errors.append(
                    diagnostic(
                        "E_REVIEW_SCOPE_SOURCE",
                        f"{source_location}.kind is invalid",
                    )
                )
            if not is_resolved_string(source["reference"]):
                errors.append(
                    diagnostic(
                        "E_REVIEW_SCOPE_SOURCE",
                        f"{source_location}.reference is invalid",
                    )
                )
            if "revision" in source and not is_resolved_string(source["revision"]):
                errors.append(
                    diagnostic(
                        "E_REVIEW_SCOPE_SOURCE",
                        f"{source_location}.revision is invalid",
                    )
                )
    return contract


def _validate_review_scope_definition(
    value: Any,
    location: str,
    errors: list[Diagnostic],
    expected_plan_revision: Any = None,
) -> dict[str, Any] | None:
    envelope = _require_object(
        value,
        ("scope_revision", "scope_id", "fail_on", "contract"),
        location,
        errors,
    )
    if envelope is None:
        return None
    if not is_resolved_string(envelope["scope_revision"]):
        errors.append(
            diagnostic(
                "E_REVIEW_SCOPE_REVISION",
                f"{location}.scope_revision is invalid",
            )
        )
    if not is_str(envelope["fail_on"]) or envelope["fail_on"] not in {
        "P0",
        "P1",
        "P2",
        "P3",
    }:
        errors.append(
            diagnostic(
                "E_REVIEW_FAIL_ON",
                f"{location}.fail_on must be P0, P1, P2 or P3",
            )
        )
    contract = _validate_scope_contract(
        envelope["contract"],
        f"{location}.contract",
        errors,
    )
    if contract is None:
        return envelope
    if is_resolved_string(expected_plan_revision):
        has_current_plan = any(
            isinstance(source, dict)
            and source.get("kind") == "plan"
            and source.get("revision") == expected_plan_revision
            for source in list_or_empty(contract.get("sources"))
        )
        if not has_current_plan:
            errors.append(
                diagnostic(
                    "E_REVIEW_SCOPE_PLAN_SOURCE",
                    f"{location} must reference current plan revision {expected_plan_revision!r}",
                )
            )
    calculated = canonical_scope_id(contract)
    if envelope["scope_id"] != calculated:
        errors.append(
            diagnostic(
                "E_REVIEW_SCOPE_HASH",
                f"{location}.scope_id does not match canonical contract SHA-256",
            )
        )
    return envelope


def _validate_engine(
    manifest: dict[str, Any],
    field: str,
    errors: list[Diagnostic],
) -> None:
    record = _require_object(
        manifest.get(field),
        (
            "capability",
            "primary_skill",
            "selected_engine",
            "availability",
            "selection_basis",
            "fallback_reason",
            "coverage_gap",
            "remaining_risk",
        ),
        field,
        errors,
    )
    if record is None:
        return
    availability = record["availability"]
    if not is_str(availability) or availability not in AVAILABILITY:
        errors.append(
            diagnostic(
                "E_ENGINE_AVAILABILITY",
                f"{field}.availability is invalid",
            )
        )
        return

    expected_capability = "planner" if field == "planning_engine" else "review"
    if record["capability"] != expected_capability:
        errors.append(
            diagnostic(
                "E_ENGINE_CAPABILITY",
                f"{field}.capability must be {expected_capability!r}",
            )
        )
    if record["primary_skill"] != ENGINE_RULES[field]:
        errors.append(
            diagnostic(
                "E_ENGINE_PRIMARY",
                f"{field}.primary_skill must be {ENGINE_RULES[field]!r}",
            )
        )
    basis = _require_object(
        record["selection_basis"],
        ("kind", "reference"),
        f"{field}.selection_basis",
        errors,
    )
    basis_kind = basis.get("kind") if basis is not None else None
    basis_valid = basis is not None
    if basis is not None and (
        not is_str(basis_kind)
        or basis_kind
        not in {"catalog", "capability_resolution", "user_confirmation"}
    ):
        errors.append(
            diagnostic(
                "E_ENGINE_SELECTION_BASIS",
                f"{field}.selection_basis is invalid",
            )
        )
        basis_valid = False
    expected_basis_kind = {
        "available": "catalog",
        "absent": "capability_resolution",
        "unloadable": "capability_resolution",
        "execution_blocked": "capability_resolution",
        "disabled_by_user": "user_confirmation",
    }[availability]
    if basis is not None and basis_kind != expected_basis_kind:
        errors.append(
            diagnostic(
                "E_ENGINE_SELECTION_BASIS_KIND",
                f"{field}.selection_basis.kind must be {expected_basis_kind!r} for {availability!r}",
            )
        )
        basis_valid = False
    reference = (
        _require_object(
            basis["reference"],
            (
                "schema_version",
                "subject_id",
                "revision",
                "capability",
                "primary_skill",
                "availability",
                "decision",
                "actor",
                "evidence",
                "evidence_sha256",
            ),
            f"{field}.selection_basis.reference",
            errors,
        )
        if basis is not None
        else None
    )
    expected_decision = (
        "use_primary"
        if availability == "available"
        else "use_fallback"
        if availability in {"absent", "unloadable"}
        else "use_fallback"
        if availability == "disabled_by_user"
        and record["selected_engine"] == "fallback"
        else "stop"
    )
    expected_actor = "user" if availability == "disabled_by_user" else "orchestrator"
    if reference is None:
        basis_valid = False
    else:
        evidence = reference["evidence"]
        expected_reference = {
            "schema_version": 1,
            "subject_id": f"{manifest.get('workflow_id')}:{field}",
            "capability": expected_capability,
            "primary_skill": ENGINE_RULES[field],
            "availability": availability,
            "decision": expected_decision,
            "actor": expected_actor,
        }
        for reference_field, expected_value in expected_reference.items():
            actual = reference[reference_field]
            if (
                reference_field == "schema_version"
                and (type(actual) is not int or actual != expected_value)
            ) or (
                reference_field != "schema_version"
                and actual != expected_value
            ):
                errors.append(
                    diagnostic(
                        "E_ENGINE_SELECTION_REFERENCE",
                        f"{field}.selection_basis.reference.{reference_field} is stale",
                    )
                )
                basis_valid = False
        if not is_resolved_string(reference["revision"]):
            errors.append(
                diagnostic(
                    "E_ENGINE_SELECTION_REFERENCE",
                    f"{field}.selection_basis.reference.revision is required",
                )
            )
            basis_valid = False
        if (
            not is_resolved_string(evidence)
            or not is_sha256(reference["evidence_sha256"])
            or (
                is_str(evidence)
                and hashlib.sha256(evidence.encode("utf-8")).hexdigest()
                != reference["evidence_sha256"]
            )
        ):
            errors.append(
                diagnostic(
                    "E_ENGINE_SELECTION_REFERENCE",
                    f"{field}.selection_basis.reference evidence is invalid",
                )
            )
            basis_valid = False
    user_selected = (
        availability == "disabled_by_user"
        and record["selected_engine"] == "fallback"
        and basis_kind == "user_confirmation"
        and basis_valid
        and reference is not None
        and reference["decision"] == "use_fallback"
        and reference["actor"] == "user"
    )
    selection = select_engine(
        expected_capability,
        availability,
        user_selected_fallback=user_selected,
    )
    if selection["blocked"]:
        errors.append(
            diagnostic(
                "E_ENGINE_BLOCKED",
                f"{ENGINE_RULES[field]} is {availability}; fallback is not authorized",
            )
        )
    if record["selected_engine"] != selection["engine"]:
        errors.append(
            diagnostic(
                "E_ENGINE_SELECTION",
                f"{field}.selected_engine must be {selection['engine']!r} for {availability!r}",
            )
        )

    if selection["fallback"]:
        if not is_resolved_string(record["fallback_reason"]):
            errors.append(
                diagnostic(
                    "E_FALLBACK_REASON",
                    f"{field}.fallback_reason is required",
                )
            )
        if not is_str_list(record["coverage_gap"]) or not record["coverage_gap"]:
            errors.append(
                diagnostic(
                    "E_FALLBACK_COVERAGE",
                    f"{field}.coverage_gap must disclose limitations",
                )
            )
        if not is_str_list(record["remaining_risk"]) or not record["remaining_risk"]:
            errors.append(
                diagnostic(
                    "E_FALLBACK_RISK",
                    f"{field}.remaining_risk must disclose fallback risks",
                )
            )
    else:
        if record["fallback_reason"] not in (None, ""):
            errors.append(
                diagnostic(
                    "E_FALLBACK_FORBIDDEN",
                    f"{field}.fallback_reason must be empty for the primary engine",
                )
            )
        if not isinstance(record["coverage_gap"], list):
            errors.append(
                diagnostic(
                    "E_ENGINE_COVERAGE_TYPE",
                    f"{field}.coverage_gap must be a list",
                )
            )
        if not isinstance(record["remaining_risk"], list):
            errors.append(
                diagnostic(
                    "E_ENGINE_RISK_TYPE",
                    f"{field}.remaining_risk must be a list",
                )
            )


def _validate_condition_head_source(
    value: Any,
    *,
    task_ids: set[str],
    location: str,
    errors: list[Diagnostic],
) -> dict[str, Any] | None:
    source = _require_object(value, ("kind",), location, errors)
    if source is None:
        return None
    kind = source["kind"]
    if not is_str(kind) or kind not in {"plan_sha", "task_head"}:
        errors.append(
            diagnostic(
                "E_CONDITION_PROOF_REQUIREMENT",
                f"{location}.kind is invalid",
            )
        )
        return None
    if kind == "task_head":
        if (
            not is_resolved_string(source.get("task_id"))
            or source["task_id"] not in task_ids
        ):
            errors.append(
                diagnostic(
                    "E_CONDITION_PROOF_REQUIREMENT",
                    f"{location}.task_id is unknown",
                )
            )
            return None
    return source


def _resolve_condition_head(
    source: Any,
    runtime: dict[str, Any],
) -> Any:
    if not isinstance(source, dict):
        return None
    kind = source.get("kind")
    if kind == "plan_sha":
        return dict_or_empty(runtime.get("git")).get("plan_sha")
    if kind == "task_head":
        return dict_or_empty(
            dict_or_empty(runtime.get("tasks")).get(source.get("task_id"))
        ).get("head_sha")
    return None


def _validate_condition_proof_requirement(
    value: Any,
    *,
    task_ids: set[str],
    location: str,
    errors: list[Diagnostic],
) -> dict[str, Any] | None:
    requirement = _require_object(value, ("kind",), location, errors)
    if requirement is None:
        return None
    kind = requirement["kind"]
    if not is_str(kind) or kind not in CONDITION_PROOF_KINDS:
        errors.append(
            diagnostic(
                "E_CONDITION_PROOF_REQUIREMENT",
                f"{location}.kind is invalid",
            )
        )
        return None
    if kind == "test_result":
        record = _require_object(
            requirement,
            ("head_source", "tests"),
            location,
            errors,
        )
        if record is None:
            return None
        _validate_condition_head_source(
            record["head_source"],
            task_ids=task_ids,
            location=f"{location}.head_source",
            errors=errors,
        )
        tests = record["tests"]
        seen_ids: set[str] = set()
        if not isinstance(tests, list) or not tests:
            errors.append(
                diagnostic(
                    "E_CONDITION_PROOF_REQUIREMENT",
                    f"{location}.tests must be non-empty",
                )
            )
        else:
            for index, item in enumerate(tests):
                test = _require_object(
                    item,
                    ("test_id", "command"),
                    f"{location}.tests[{index}]",
                    errors,
                )
                if test is None:
                    continue
                if (
                    not is_resolved_string(test["test_id"])
                    or test["test_id"] in seen_ids
                    or not is_resolved_string(test["command"])
                ):
                    errors.append(
                        diagnostic(
                            "E_CONDITION_PROOF_REQUIREMENT",
                            f"{location}.tests[{index}] is invalid or duplicate",
                        )
                    )
                else:
                    seen_ids.add(test["test_id"])
    elif kind == "artifact_result":
        record = _require_object(
            requirement,
            ("head_source", "paths"),
            location,
            errors,
        )
        if record is None:
            return None
        _validate_condition_head_source(
            record["head_source"],
            task_ids=task_ids,
            location=f"{location}.head_source",
            errors=errors,
        )
        paths = record["paths"]
        if (
            not is_str_list(paths)
            or not paths
            or len(paths) != len(set(paths))
            or any(not is_safe_relative_path(path) for path in paths)
        ):
            errors.append(
                diagnostic(
                    "E_CONDITION_PROOF_REQUIREMENT",
                    f"{location}.paths must be non-empty unique safe paths",
                )
            )
    elif kind == "external_evidence":
        record = _require_object(
            requirement,
            ("evidence_id", "revision", "result_sha256"),
            location,
            errors,
        )
        if record is None:
            return None
        if (
            not is_resolved_string(record["evidence_id"])
            or not is_resolved_string(record["revision"])
            or not is_sha256(record["result_sha256"])
        ):
            errors.append(
                diagnostic(
                    "E_CONDITION_PROOF_REQUIREMENT",
                    f"{location} external expectation is invalid",
                )
            )
    elif kind == "user_confirmation":
        record = _require_object(
            requirement,
            ("confirmation_id", "revision", "confirmed_by"),
            location,
            errors,
        )
        if record is None:
            return None
        if (
            not is_resolved_string(record["confirmation_id"])
            or not is_resolved_string(record["revision"])
            or not is_resolved_string(record["confirmed_by"])
        ):
            errors.append(
                diagnostic(
                    "E_CONDITION_PROOF_REQUIREMENT",
                    f"{location} confirmation expectation is invalid",
                )
            )
    return requirement


def _validate_condition_proof(
    proof: dict[str, Any],
    *,
    condition_id: str,
    close_condition: Any,
    requirement: dict[str, Any],
    manifest: dict[str, Any],
    runtime: dict[str, Any],
    location: str,
    errors: list[Diagnostic],
    evidence_reader: Callable[[str], bytes] | None,
    read_object: Callable[[str, str], bytes] | None,
) -> str | None:
    payload = _read_evidence_json(
        proof.get("reference"),
        f"{location}.reference",
        errors,
        evidence_reader,
    )
    if payload is None:
        return None
    kind = proof.get("kind")
    common = _require_object(
        payload,
        (
            "schema_version",
            "workflow_id",
            "plan_revision",
            "orchestration_revision",
            "subject_id",
        ),
        f"{location}.payload",
        errors,
    )
    if common is None:
        return None
    expected_common = {
        "schema_version": 1,
        "workflow_id": manifest.get("workflow_id"),
        "plan_revision": dict_or_empty(manifest.get("plan")).get("revision"),
        "orchestration_revision": dict_or_empty(
            manifest.get("orchestration")
        ).get("revision"),
        "subject_id": f"condition:{condition_id}",
    }
    valid = True
    for field, expected in expected_common.items():
        actual = common.get(field)
        if (
            field == "schema_version"
            and (type(actual) is not int or actual != expected)
        ) or (field != "schema_version" and actual != expected):
            errors.append(
                diagnostic(
                    "E_CONDITION_PROOF_STALE",
                    f"{location}.payload.{field} does not match the current condition",
                )
            )
            valid = False

    identity: str | None = None
    if kind == "test_result":
        record = _require_object(
            payload,
            ("status", "tested_head_sha", "commands"),
            f"{location}.payload",
            errors,
        )
        if record is None:
            return None
        commands = record["commands"]
        normalized_commands: list[dict[str, str]] = []
        seen_test_ids: set[str] = set()
        expected_head = _resolve_condition_head(
            requirement.get("head_source"),
            runtime,
        )
        if (
            record["status"] != "passed"
            or not is_oid(record["tested_head_sha"])
            or not is_oid(expected_head)
            or record["tested_head_sha"] != expected_head
        ):
            errors.append(
                diagnostic(
                    "E_CONDITION_PROOF_CONTENT",
                    f"{location} test_result is not passed on its declared HEAD source",
                )
            )
            valid = False
        if not isinstance(commands, list) or not commands:
            errors.append(
                diagnostic(
                    "E_CONDITION_PROOF_CONTENT",
                    f"{location} test_result commands must be non-empty",
                )
            )
            valid = False
        else:
            for index, item in enumerate(commands):
                command = _require_object(
                    item,
                    ("test_id", "command", "exit_code", "status", "result"),
                    f"{location}.payload.commands[{index}]",
                    errors,
                )
                if command is None:
                    valid = False
                    continue
                test_id = command["test_id"]
                if (
                    not is_resolved_string(test_id)
                    or test_id in seen_test_ids
                    or not is_resolved_string(command["command"])
                    or type(command["exit_code"]) is not int
                    or command["exit_code"] != 0
                    or command["status"] != "passed"
                    or not is_resolved_string(command["result"])
                ):
                    errors.append(
                        diagnostic(
                            "E_CONDITION_PROOF_CONTENT",
                            f"{location}.payload.commands[{index}] is invalid",
                        )
                    )
                    valid = False
                    continue
                seen_test_ids.add(test_id)
                normalized_commands.append(
                    {"test_id": test_id, "command": command["command"]}
                )
        expected_commands = [
            {
                "test_id": item.get("test_id"),
                "command": item.get("command"),
            }
            for item in list_or_empty(requirement.get("tests"))
            if isinstance(item, dict)
            and is_resolved_string(item.get("test_id"))
            and is_resolved_string(item.get("command"))
        ]
        if sorted(
            normalized_commands,
            key=lambda item: (item["test_id"], item["command"]),
        ) != sorted(
            expected_commands,
            key=lambda item: (item["test_id"], item["command"]),
        ):
            errors.append(
                diagnostic(
                    "E_CONDITION_PROOF_CONTENT",
                    f"{location} test_result does not match declared tests",
                )
            )
            valid = False
        if is_oid(record["tested_head_sha"]) and normalized_commands:
            command_digest = canonical_json_sha256(
                sorted(normalized_commands, key=lambda item: item["test_id"])
            )
            identity = (
                f"test_result:{common['subject_id']}@"
                f"{record['tested_head_sha']}#{command_digest}"
            )
    elif kind == "artifact_result":
        record = _require_object(
            payload,
            ("status", "source_sha", "artifacts"),
            f"{location}.payload",
            errors,
        )
        if record is None:
            return None
        artifacts = record["artifacts"]
        normalized_artifacts: list[dict[str, str]] = []
        seen_paths: set[str] = set()
        expected_head = _resolve_condition_head(
            requirement.get("head_source"),
            runtime,
        )
        if (
            record["status"] != "available"
            or not is_oid(record["source_sha"])
            or not is_oid(expected_head)
            or record["source_sha"] != expected_head
        ):
            errors.append(
                diagnostic(
                    "E_CONDITION_PROOF_CONTENT",
                    f"{location} artifact_result does not use its declared source anchor",
                )
            )
            valid = False
        if not isinstance(artifacts, list) or not artifacts:
            errors.append(
                diagnostic(
                    "E_CONDITION_PROOF_CONTENT",
                    f"{location} artifact_result artifacts must be non-empty",
                )
            )
            valid = False
        else:
            for index, item in enumerate(artifacts):
                artifact = _require_object(
                    item,
                    ("path", "sha256"),
                    f"{location}.payload.artifacts[{index}]",
                    errors,
                )
                if artifact is None:
                    valid = False
                    continue
                path = artifact["path"]
                digest = artifact["sha256"]
                if (
                    not is_safe_relative_path(path)
                    or path in seen_paths
                    or not is_sha256(digest)
                ):
                    errors.append(
                        diagnostic(
                            "E_CONDITION_PROOF_CONTENT",
                            f"{location}.payload.artifacts[{index}] is invalid",
                        )
                    )
                    valid = False
                    continue
                seen_paths.add(path)
                normalized_artifacts.append({"path": path, "sha256": digest})
                if read_object is None or not is_oid(record["source_sha"]):
                    errors.append(
                        diagnostic(
                            "E_CONDITION_PROOF_CONTENT",
                            f"{location} cannot verify artifact Git blob content",
                        )
                    )
                    valid = False
                    continue
                try:
                    raw = read_object(record["source_sha"], path)
                except Exception as exc:
                    errors.append(
                        diagnostic(
                            "E_CONDITION_PROOF_CONTENT",
                            f"{location} artifact Git blob read failed: {exc}",
                        )
                    )
                    valid = False
                    continue
                if (
                    not isinstance(raw, bytes)
                    or hashlib.sha256(raw).hexdigest() != digest
                ):
                    errors.append(
                        diagnostic(
                            "E_CONDITION_PROOF_CONTENT",
                            f"{location} artifact digest does not match source_sha",
                        )
                    )
                    valid = False
        actual_paths = {
            item["path"] for item in normalized_artifacts
        }
        expected_paths = set(
            requirement["paths"]
            if is_str_list(requirement.get("paths"))
            else []
        )
        if actual_paths != expected_paths:
            errors.append(
                diagnostic(
                    "E_CONDITION_PROOF_CONTENT",
                    f"{location} artifact paths do not match the declared requirement",
                )
            )
            valid = False
        if is_oid(record["source_sha"]) and normalized_artifacts:
            artifact_digest = canonical_json_sha256(
                sorted(normalized_artifacts, key=lambda item: item["path"])
            )
            identity = (
                f"artifact_result:{common['subject_id']}@"
                f"{record['source_sha']}#{artifact_digest}"
            )
    elif kind == "external_evidence":
        record = _require_object(
            payload,
            ("status", "evidence_id", "revision", "result"),
            f"{location}.payload",
            errors,
        )
        if record is None:
            return None
        if (
            record["status"] != "verified"
            or not is_resolved_string(record["evidence_id"])
            or not is_resolved_string(record["revision"])
            or not is_resolved_string(record["result"])
            or record["evidence_id"] != requirement.get("evidence_id")
            or record["revision"] != requirement.get("revision")
            or hashlib.sha256(record["result"].encode("utf-8")).hexdigest()
            != requirement.get("result_sha256")
        ):
            errors.append(
                diagnostic(
                    "E_CONDITION_PROOF_CONTENT",
                    f"{location} external_evidence is invalid",
                )
            )
            valid = False
        else:
            result_digest = canonical_json_sha256(
                {
                    "subject_id": common["subject_id"],
                    "result": record["result"],
                }
            )
            identity = (
                f"external_evidence:{record['evidence_id']}@"
                f"{record['revision']}#{result_digest}"
            )
    elif kind == "user_confirmation":
        record = _require_object(
            payload,
            (
                "status",
                "condition_id",
                "close_condition",
                "confirmation_id",
                "revision",
                "confirmation_basis",
                "confirmed_by",
                "confirmed_at",
            ),
            f"{location}.payload",
            errors,
        )
        if record is None:
            return None
        if (
            record["status"] != "confirmed"
            or record["condition_id"] != condition_id
            or record["close_condition"] != close_condition
            or not is_resolved_string(record["confirmation_id"])
            or not is_resolved_string(record["revision"])
            or not is_resolved_string(record["confirmation_basis"])
            or not is_resolved_string(record["confirmed_by"])
            or not is_resolved_string(record["confirmed_at"])
            or record["confirmation_id"] != requirement.get("confirmation_id")
            or record["revision"] != requirement.get("revision")
            or record["confirmed_by"] != requirement.get("confirmed_by")
        ):
            errors.append(
                diagnostic(
                    "E_CONDITION_PROOF_CONTENT",
                    f"{location} user_confirmation is invalid or stale",
                )
            )
            valid = False
        else:
            identity = (
                f"user_confirmation:{common['subject_id']}:"
                f"{record['confirmation_id']}@"
                f"{record['revision']}"
            )
    else:
        return None

    if identity is None or proof.get("identity") != identity:
        errors.append(
            diagnostic(
                "E_CONDITION_PROOF_IDENTITY",
                f"{location}.identity does not match referenced proof content",
            )
        )
        valid = False
    return identity if valid else None


def _validate_conditions(
    manifest: dict[str, Any],
    runtime: dict[str, Any],
    phase: str,
    selected_task: str | None,
    errors: list[Diagnostic],
    evidence_reader: Callable[[str], bytes] | None,
    read_object: Callable[[str, str], bytes] | None,
) -> None:
    plan = manifest.get("plan")
    if not isinstance(plan, dict):
        return
    definitions = plan.get("conditions")
    if not isinstance(definitions, list):
        errors.append(diagnostic("E_PLAN_CONDITIONS", "plan.conditions must be a list"))
        return

    definition_ids: set[str] = set()
    definition_map: dict[str, dict[str, Any]] = {}
    task_ids = {
        item["id"]
        for item in list_or_empty(manifest.get("tasks"))
        if isinstance(item, dict) and is_resolved_string(item.get("id"))
    }
    for index, item in enumerate(definitions):
        record = _require_object(
            item,
            (
                "id",
                "owner",
                "close_condition",
                "must_close_before",
                "applies_to",
                "required_proof_kinds",
                "proof_requirements",
            ),
            f"plan.conditions[{index}]",
            errors,
        )
        if record is None:
            continue
        condition_id = record["id"]
        if not is_resolved_string(condition_id):
            errors.append(
                diagnostic("E_CONDITION_ID", f"plan.conditions[{index}].id is invalid")
            )
            continue
        if condition_id in definition_ids:
            errors.append(
                diagnostic("E_CONDITION_DUPLICATE", f"duplicate condition {condition_id}")
            )
        definition_ids.add(condition_id)
        definition_map[condition_id] = record
        if not is_resolved_string(record["owner"]):
            errors.append(
                diagnostic("E_CONDITION_OWNER", f"{condition_id}.owner is required")
            )
        if not is_resolved_string(record["close_condition"]):
            errors.append(
                diagnostic(
                    "E_CONDITION_CLOSE",
                    f"{condition_id}.close_condition is required",
                )
            )
        if (
            not is_str(record["must_close_before"])
            or record["must_close_before"] not in CONDITION_GATE_RANK
        ):
            errors.append(
                diagnostic(
                    "E_CONDITION_GATE",
                    f"{condition_id}.must_close_before is invalid",
                )
            )
        if (
            not is_str_list(record["applies_to"])
            or not record["applies_to"]
            or len(record["applies_to"]) != len(set(record["applies_to"]))
        ):
            errors.append(
                diagnostic(
                    "E_CONDITION_SCOPE",
                    f"{condition_id}.applies_to must be a non-empty unique string list",
                )
            )
        elif any(
            target not in task_ids | {"workflow", "integration"}
            for target in record["applies_to"]
        ):
            errors.append(
                diagnostic(
                    "E_CONDITION_SCOPE",
                    f"{condition_id}.applies_to contains an unknown target",
                )
            )
        required_proof_kinds = record["required_proof_kinds"]
        if (
            not is_str_list(required_proof_kinds)
            or not required_proof_kinds
            or len(required_proof_kinds) != len(set(required_proof_kinds))
            or any(
                kind not in CONDITION_PROOF_KINDS for kind in required_proof_kinds
            )
        ):
            errors.append(
                diagnostic(
                    "E_CONDITION_PROOF_KINDS",
                    f"{condition_id}.required_proof_kinds is invalid",
                )
            )
        proof_requirements = record["proof_requirements"]
        requirement_kinds: set[str] = set()
        if not isinstance(proof_requirements, list) or not proof_requirements:
            errors.append(
                diagnostic(
                    "E_CONDITION_PROOF_REQUIREMENTS",
                    f"{condition_id}.proof_requirements must be non-empty",
                )
            )
        else:
            for requirement_index, requirement_value in enumerate(
                proof_requirements
            ):
                requirement = _validate_condition_proof_requirement(
                    requirement_value,
                    task_ids=task_ids,
                    location=(
                        f"plan.conditions[{index}].proof_requirements"
                        f"[{requirement_index}]"
                    ),
                    errors=errors,
                )
                if requirement is None:
                    continue
                kind = requirement.get("kind")
                if is_str(kind):
                    if kind in requirement_kinds:
                        errors.append(
                            diagnostic(
                                "E_CONDITION_PROOF_REQUIREMENTS",
                                f"{condition_id} has duplicate proof requirement {kind}",
                            )
                        )
                    requirement_kinds.add(kind)
        if is_str_list(required_proof_kinds) and requirement_kinds != set(
            required_proof_kinds
        ):
            errors.append(
                diagnostic(
                    "E_CONDITION_PROOF_REQUIREMENTS",
                    f"{condition_id}.proof_requirements must exactly match required_proof_kinds",
                )
            )

    if plan.get("quality_status") == "有条件通过" and not definition_ids:
        errors.append(
            diagnostic(
                "E_CONDITIONAL_PLAN_EMPTY",
                "有条件通过 requires at least one structured condition",
            )
        )

    runtime_conditions = runtime.get("conditions")
    if not isinstance(runtime_conditions, list):
        errors.append(
            diagnostic("E_RUNTIME_CONDITIONS", "runtime conditions must be a list")
        )
        return
    runtime_map: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(runtime_conditions):
        record = _require_object(
            item,
            ("id", "status", "evidence_ref"),
            f"runtime.conditions[{index}]",
            errors,
        )
        if record is None or not is_resolved_string(record["id"]):
            continue
        if record["id"] in runtime_map:
            errors.append(
                diagnostic(
                    "E_RUNTIME_CONDITION_DUPLICATE",
                    f"duplicate runtime condition {record['id']}",
                )
            )
        runtime_map[record["id"]] = record
        if not is_str(record["status"]) or record["status"] not in {"open", "closed"}:
            errors.append(
                diagnostic(
                    "E_CONDITION_STATUS",
                    f"{record['id']}.status must be open or closed",
                )
            )
    if set(runtime_map) != definition_ids:
        errors.append(
            diagnostic(
                "E_CONDITION_MISMATCH",
                "runtime condition IDs must exactly match plan conditions",
            )
        )
    if phase != "planning":
        def targets_for(definition: dict[str, Any]) -> set[str]:
            return {
                item
                for item in list_or_empty(definition.get("applies_to"))
                if is_resolved_string(item)
            }

        phase_gate = {
            "dispatch": "dispatch",
            "branch-ready": "branch_review",
            "integration-ready": "integration",
            "merge-ready": "integration",
        }.get(phase)

        def gate_is_due(definition: dict[str, Any]) -> bool:
            condition_gate = definition.get("must_close_before")
            return (
                is_str(condition_gate)
                and condition_gate in CONDITION_GATE_RANK
                and is_str(phase_gate)
                and CONDITION_GATE_RANK[condition_gate]
                <= CONDITION_GATE_RANK[phase_gate]
            )

        if phase == "merge-ready":
            relevant_ids = set(definition_ids)
        elif phase == "integration-ready":
            relevant_ids = {
                condition_id
                for condition_id, definition in definition_map.items()
                if gate_is_due(definition)
            }
        elif phase == "branch-ready" and is_resolved_string(selected_task):
            relevant_ids = {
                condition_id
                for condition_id, definition in definition_map.items()
                if gate_is_due(definition)
                and (
                    selected_task in targets_for(definition)
                    or "workflow" in targets_for(definition)
                )
            }
        elif phase == "dispatch":
            active_statuses = {
                "ready",
                "running",
                "implemented",
                "review_incomplete",
                "review_blocked",
                "reviewed",
                "integrated",
            }
            runtime_tasks = dict_or_empty(runtime.get("tasks"))
            active_tasks = {
                task_id
                for task_id, state in runtime_tasks.items()
                if isinstance(state, dict)
                and is_str(state.get("status"))
                and state["status"] in active_statuses
            }
            relevant_ids = {
                condition_id
                for condition_id, definition in definition_map.items()
                if gate_is_due(definition)
                and (
                    active_tasks & targets_for(definition)
                    or "workflow" in targets_for(definition)
                )
            }
        else:
            relevant_ids = {
                condition_id
                for condition_id, definition in definition_map.items()
                if gate_is_due(definition)
                and "workflow" in targets_for(definition)
            }
        for condition_id in sorted(relevant_ids):
            record = runtime_map.get(condition_id)
            if record is None or record.get("status") != "closed":
                errors.append(
                    diagnostic(
                        "E_CONDITION_OPEN",
                        f"{condition_id} is not closed with evidence",
                    )
                )
                continue
            payload = _read_evidence_json(
                record.get("evidence_ref"),
                f"runtime.conditions.{condition_id}.evidence_ref",
                errors,
                evidence_reader,
            )
            expected = {
                "schema_version": 1,
                "workflow_id": manifest.get("workflow_id"),
                "plan_revision": dict_or_empty(manifest.get("plan")).get("revision"),
                "orchestration_revision": dict_or_empty(
                    manifest.get("orchestration")
                ).get("revision"),
                "condition_id": condition_id,
                "status": "closed",
            }
            if payload is not None:
                for field, value in expected.items():
                    actual = payload.get(field)
                    if (
                        field == "schema_version"
                        and (type(actual) is not int or actual != value)
                    ) or (field != "schema_version" and actual != value):
                        errors.append(
                            diagnostic(
                                "E_CONDITION_EVIDENCE_STALE",
                                f"{condition_id} evidence field {field} is stale",
                            )
                        )
                closure = _require_object(
                    payload.get("closure"),
                    ("close_condition", "verified_by", "proofs"),
                    f"runtime.conditions.{condition_id}.closure",
                    errors,
                )
                if closure is None:
                    continue
                if closure["close_condition"] != definition_map[
                    condition_id
                ].get("close_condition"):
                    errors.append(
                        diagnostic(
                            "E_CONDITION_EVIDENCE_STALE",
                            f"{condition_id} closure criterion is stale",
                        )
                    )
                if not is_resolved_string(closure["verified_by"]):
                    errors.append(
                        diagnostic(
                            "E_CONDITION_EVIDENCE_CONTENT",
                            f"{condition_id} closure verifier is missing",
                        )
                    )
                proofs = closure["proofs"]
                if not isinstance(proofs, list) or not proofs:
                    errors.append(
                        diagnostic(
                            "E_CONDITION_EVIDENCE_CONTENT",
                            f"{condition_id} closure requires structured proofs",
                        )
                    )
                    continue
                valid_proofs: set[tuple[str, str]] = set()
                actual_proof_kinds: set[str] = set()
                requirement_map = {
                    item["kind"]: item
                    for item in list_or_empty(
                        definition_map[condition_id].get("proof_requirements")
                    )
                    if isinstance(item, dict)
                    and is_str(item.get("kind"))
                    and item["kind"] in CONDITION_PROOF_KINDS
                }
                for proof_index, item in enumerate(proofs):
                    proof = _require_object(
                        item,
                        ("kind", "reference", "identity"),
                        (
                            f"runtime.conditions.{condition_id}.closure."
                            f"proofs[{proof_index}]"
                        ),
                        errors,
                    )
                    if proof is None:
                        continue
                    if (
                        not is_str(proof["kind"])
                        or proof["kind"] not in CONDITION_PROOF_KINDS
                        or not is_resolved_string(proof["identity"])
                    ):
                        errors.append(
                            diagnostic(
                                "E_CONDITION_EVIDENCE_CONTENT",
                                f"{condition_id} proof #{proof_index} is invalid",
                            )
                        )
                        continue
                    requirement = requirement_map.get(proof["kind"])
                    if requirement is None:
                        errors.append(
                            diagnostic(
                                "E_CONDITION_PROOF_UNEXPECTED",
                                f"{condition_id} has no requirement for {proof['kind']}",
                            )
                        )
                        continue
                    identity = _validate_condition_proof(
                        proof,
                        condition_id=condition_id,
                        close_condition=definition_map[condition_id].get(
                            "close_condition"
                        ),
                        requirement=requirement,
                        manifest=manifest,
                        runtime=runtime,
                        location=(
                            f"runtime.conditions.{condition_id}.closure."
                            f"proofs[{proof_index}]"
                        ),
                        errors=errors,
                        evidence_reader=evidence_reader,
                        read_object=read_object,
                    )
                    if identity is None:
                        continue
                    proof_key = (proof["kind"], identity)
                    if proof_key in valid_proofs:
                        errors.append(
                            diagnostic(
                                "E_CONDITION_PROOF_DUPLICATE",
                                f"{condition_id} contains duplicate proof {identity}",
                            )
                        )
                        continue
                    valid_proofs.add(proof_key)
                    actual_proof_kinds.add(proof["kind"])
                required_proof_kinds = definition_map[condition_id].get(
                    "required_proof_kinds"
                )
                if is_str_list(required_proof_kinds) and actual_proof_kinds != set(
                    required_proof_kinds
                ):
                    errors.append(
                        diagnostic(
                            "E_CONDITION_PROOF_COVERAGE",
                            f"{condition_id} proofs do not exactly cover required_proof_kinds",
                        )
                    )


def _validate_plan(
    manifest: dict[str, Any],
    errors: list[Diagnostic],
) -> None:
    plan = _require_object(
        manifest.get("plan"),
        (
            "revision",
            "quality_subject",
            "contract_status",
            "quality_status",
            "source",
            "planning_mode",
            "effective_decision_status",
            "effective_confirmation_basis",
            "confirmation",
            "acceptance_criteria",
            "conditions",
        ),
        "plan",
        errors,
    )
    if plan is None:
        return
    source = _require_object(
        plan["source"],
        (
            "task_type",
            "decision_status_field",
            "decision_status",
            "confirmation_basis_field",
            "confirmation_basis",
        ),
        "plan.source",
        errors,
    )
    if not is_resolved_string(plan["revision"]):
        errors.append(diagnostic("E_PLAN_REVISION", "plan.revision is required"))
    if plan["quality_subject"] != plan["revision"]:
        errors.append(
            diagnostic(
                "E_PLAN_QUALITY_SUBJECT",
                "plan.quality_subject must equal plan.revision",
            )
        )
    if plan["contract_status"] != "就绪":
        errors.append(diagnostic("E_PLAN_CONTRACT", "contract_status must be 就绪"))
    if not is_str(plan["quality_status"]) or plan["quality_status"] not in {
        "通过",
        "有条件通过",
    }:
        errors.append(
            diagnostic(
                "E_PLAN_QUALITY",
                "quality_status must be 通过 or 有条件通过",
            )
        )
    if not is_str(plan["planning_mode"]) or plan["planning_mode"] not in PLANNING_MODES:
        errors.append(
            diagnostic("E_PLANNING_MODE", "plan.planning_mode is invalid")
        )
    elif source is not None:
        selected_planner = dict_or_empty(
            manifest.get("planning_engine")
        ).get("selected_engine")
        if (plan["planning_mode"] == "fallback") != (
            selected_planner == "fallback"
        ):
            errors.append(
                diagnostic(
                    "E_PLANNING_ENGINE_MODE",
                    "plan.planning_mode must match planning_engine.selected_engine",
                )
            )
        expected_fields = (
            ("source_decision_status", "source_confirmation_basis")
            if plan["planning_mode"] == "existing_plan_review"
            else ("decision_status", "confirmation_basis")
        )
        if not is_resolved_string(source["task_type"]):
            errors.append(
                diagnostic("E_PLAN_SOURCE_TASK", "plan.source.task_type is required")
            )
        if source["decision_status_field"] != expected_fields[0]:
            errors.append(
                diagnostic(
                    "E_PLAN_SOURCE_MAPPING",
                    "plan source decision field does not match planning_mode",
                )
            )
        if source["confirmation_basis_field"] != expected_fields[1]:
            errors.append(
                diagnostic(
                    "E_PLAN_SOURCE_MAPPING",
                    "plan source confirmation field does not match planning_mode",
                )
            )
        if source["decision_status"] != plan["effective_decision_status"]:
            errors.append(
                diagnostic(
                    "E_PLAN_SOURCE_STATUS",
                    "effective decision status does not match the preserved source",
                )
            )
        if source["confirmation_basis"] != plan["effective_confirmation_basis"]:
            errors.append(
                diagnostic(
                    "E_PLAN_SOURCE_CONFIRMATION",
                    "effective confirmation basis does not match the preserved source",
                )
            )
    if plan["effective_decision_status"] != "已确认":
        errors.append(
            diagnostic(
                "E_PLAN_DECISION",
                "effective_decision_status must be 已确认",
            )
        )
    if not is_resolved_string(plan["effective_confirmation_basis"]):
        errors.append(
            diagnostic(
                "E_PLAN_CONFIRMATION",
                "effective_confirmation_basis is required",
            )
        )
    _validate_confirmation(
        plan["confirmation"],
        location="plan.confirmation",
        subject_type="plan",
        subject_id=manifest.get("workflow_id"),
        revision=plan["revision"],
        confirmation_basis=plan["effective_confirmation_basis"],
        errors=errors,
    )
    criteria = plan["acceptance_criteria"]
    if not isinstance(criteria, list) or not criteria:
        errors.append(
            diagnostic(
                "E_ACCEPTANCE_CRITERIA",
                "plan.acceptance_criteria must be a non-empty list",
            )
        )
        return
    seen_criteria: set[str] = set()
    for index, item in enumerate(criteria):
        criterion = _require_object(
            item,
            ("id", "statement", "source"),
            f"plan.acceptance_criteria[{index}]",
            errors,
        )
        if criterion is None:
            continue
        criterion_id = criterion["id"]
        if (
            not is_str(criterion_id)
            or AC_ID_RE.fullmatch(criterion_id) is None
            or criterion_id in seen_criteria
        ):
            errors.append(
                diagnostic(
                    "E_ACCEPTANCE_CRITERION_ID",
                    f"plan.acceptance_criteria[{index}].id is invalid or duplicate",
                )
            )
        else:
            seen_criteria.add(criterion_id)
        if not is_resolved_string(criterion["statement"]):
            errors.append(
                diagnostic(
                    "E_ACCEPTANCE_CRITERION_STATEMENT",
                    f"plan.acceptance_criteria[{index}].statement is required",
                )
            )
        source_record = _require_object(
            criterion["source"],
            ("kind", "reference", "revision"),
            f"plan.acceptance_criteria[{index}].source",
            errors,
        )
        if source_record is None:
            continue
        if (
            not is_str(source_record["kind"])
            or source_record["kind"] not in SCOPE_SOURCE_KINDS
            or not is_resolved_string(source_record["reference"])
            or not is_resolved_string(source_record["revision"])
        ):
            errors.append(
                diagnostic(
                    "E_ACCEPTANCE_CRITERION_SOURCE",
                    f"plan.acceptance_criteria[{index}].source is invalid",
                )
            )
        elif (
            source_record["kind"] == "plan"
            and source_record["revision"] != plan["revision"]
        ):
            errors.append(
                diagnostic(
                    "E_ACCEPTANCE_CRITERION_SOURCE",
                    f"plan.acceptance_criteria[{index}] uses a stale plan revision",
                )
            )


def _validate_orchestration(
    manifest: dict[str, Any],
    errors: list[Diagnostic],
) -> None:
    orchestration = _require_object(
        manifest.get("orchestration"),
        (
            "revision",
            "based_on_plan_revision",
            "status",
            "confirmation_basis",
            "confirmation",
            "sources",
            "supersedes",
        ),
        "orchestration",
        errors,
    )
    if orchestration is None:
        return
    plan = manifest.get("plan")
    if (
        isinstance(plan, dict)
        and orchestration["based_on_plan_revision"] != plan.get("revision")
    ):
        errors.append(
            diagnostic(
                "E_PLAN_REVISION_STALE",
                "orchestration is based on a stale plan revision",
            )
        )
    if not is_resolved_string(orchestration["revision"]):
        errors.append(
            diagnostic(
                "E_ORCHESTRATION_REVISION",
                "orchestration.revision is required",
            )
        )
    if orchestration["status"] != "confirmed":
        errors.append(
            diagnostic(
                "E_ORCHESTRATION_STATUS",
                "orchestration.status must be confirmed",
            )
        )
    if not is_resolved_string(orchestration["confirmation_basis"]):
        errors.append(
            diagnostic(
                "E_ORCHESTRATION_CONFIRMATION",
                "orchestration.confirmation_basis is required",
            )
        )
    _validate_confirmation(
        orchestration["confirmation"],
        location="orchestration.confirmation",
        subject_type="orchestration",
        subject_id=manifest.get("workflow_id"),
        revision=orchestration["revision"],
        confirmation_basis=orchestration["confirmation_basis"],
        errors=errors,
    )
    if not is_str_list(orchestration["sources"]) or not orchestration["sources"]:
        errors.append(
            diagnostic(
                "E_ORCHESTRATION_SOURCES",
                "orchestration.sources must be a non-empty string list",
            )
        )
    if orchestration["supersedes"] is not None and not is_resolved_string(
        orchestration["supersedes"]
    ):
        errors.append(
            diagnostic(
                "E_ORCHESTRATION_SUPERSEDES",
                "orchestration.supersedes is invalid",
            )
        )


def _read_artifact(
    ai_root: Path | None,
    relative: Any,
    location: str,
    errors: list[Diagnostic],
    artifact_reader: Callable[[str], bytes] | None = None,
) -> str | None:
    if not is_safe_relative_path(relative):
        errors.append(diagnostic("E_PATH", f"{location} is not a safe relative path"))
        return None
    try:
        if artifact_reader is not None:
            raw = artifact_reader(relative)
            if not isinstance(raw, bytes):
                raise TypeError("artifact reader must return bytes")
            text = raw.decode("utf-8")
        elif ai_root is not None:
            text = (ai_root / relative).read_text(encoding="utf-8")
        else:
            return None
    except (OSError, UnicodeError) as exc:
        errors.append(diagnostic("E_ARTIFACT_MISSING", f"{location}: {exc}"))
        return None
    except Exception as exc:
        errors.append(diagnostic("E_ARTIFACT_MISSING", f"{location}: {exc}"))
        return None
    if "{{" in text or "}}" in text:
        errors.append(
            diagnostic("E_TEMPLATE_UNRESOLVED", f"{location} contains placeholders")
        )
    if len(text.strip()) < 80:
        errors.append(diagnostic("E_ARTIFACT_EMPTY", f"{location} is an empty shell"))
    return text


def _read_evidence_ref(
    value: Any,
    location: str,
    errors: list[Diagnostic],
    evidence_reader: Callable[[str], bytes] | None,
) -> bytes | None:
    record = _require_object(value, ("path", "sha256"), location, errors)
    if record is None:
        return None
    path = record["path"]
    digest = record["sha256"]
    if not is_safe_relative_path(path):
        errors.append(
            diagnostic("E_EVIDENCE_PATH", f"{location}.path is not safe")
        )
        return None
    if not is_sha256(digest):
        errors.append(
            diagnostic("E_EVIDENCE_DIGEST", f"{location}.sha256 is invalid")
        )
        return None
    if evidence_reader is None:
        errors.append(
            diagnostic(
                "E_EVIDENCE_PROOF_REQUIRED",
                f"{location} cannot be verified without an evidence reader",
            )
        )
        return None
    try:
        raw = evidence_reader(path)
    except Exception as exc:
        errors.append(diagnostic("E_EVIDENCE_MISSING", f"{location}: {exc}"))
        return None
    if not isinstance(raw, bytes) or not raw:
        errors.append(
            diagnostic("E_EVIDENCE_EMPTY", f"{location} is empty or unreadable")
        )
        return None
    actual = hashlib.sha256(raw).hexdigest()
    if actual != digest:
        errors.append(
            diagnostic(
                "E_EVIDENCE_DIGEST",
                f"{location} content does not match recorded SHA-256",
            )
        )
        return None
    return raw


def _read_evidence_json(
    value: Any,
    location: str,
    errors: list[Diagnostic],
    evidence_reader: Callable[[str], bytes] | None,
) -> dict[str, Any] | None:
    raw = _read_evidence_ref(value, location, errors, evidence_reader)
    if raw is None:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        errors.append(diagnostic("E_EVIDENCE_FORMAT", f"{location}: {exc}"))
        return None
    if not isinstance(payload, dict):
        errors.append(
            diagnostic("E_EVIDENCE_FORMAT", f"{location} root must be an object")
        )
        return None
    return payload


def _markdown_metadata(text: str, key: str) -> str | None:
    pattern = re.compile(
        rf"^-\s+{re.escape(key)}:\s*`?(.+?)`?\s*$",
        re.MULTILINE,
    )
    match = pattern.search(text)
    return match.group(1).strip() if match else None


def _markdown_section(text: str, heading: str) -> str | None:
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*$\n(.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    return match.group(1).strip() if match else None


def _validate_task_report(
    manifest: dict[str, Any],
    task_id: str,
    definition: dict[str, Any],
    state: dict[str, Any],
    errors: list[Diagnostic],
    read_object: Callable[[str, str], bytes] | None,
) -> None:
    head_sha = state.get("head_sha")
    report_path = f".ai/{definition['report_path']}"
    expected_ref = f"git:{head_sha}:{report_path}"
    if state.get("report_ref") != expected_ref:
        errors.append(
            diagnostic(
                "E_TASK_REPORT_REF",
                f"{task_id}.report_ref must bind the exact task HEAD and path",
            )
        )
    if read_object is None or not is_oid(head_sha):
        errors.append(
            diagnostic(
                "E_TASK_REPORT_PROOF_REQUIRED",
                f"{task_id} report requires fixed Git blob proof",
            )
        )
        return
    try:
        raw = read_object(head_sha, report_path)
        if not isinstance(raw, bytes):
            raise TypeError("Git object reader must return bytes")
        text = raw.decode("utf-8")
    except Exception as exc:
        errors.append(
            diagnostic("E_TASK_REPORT_MISSING", f"{task_id} report: {exc}")
        )
        return
    if "{{" in text or "}}" in text:
        errors.append(
            diagnostic(
                "E_TASK_REPORT_TEMPLATE",
                f"{task_id} report contains unresolved placeholders",
            )
        )
    expected_metadata = {
        "report_schema_version": "1",
        "report_status": "completed",
        "verification_status": "passed",
        "workflow_id": str(manifest.get("workflow_id")),
        "task_id": task_id,
        "plan_revision": str(dict_or_empty(manifest.get("plan")).get("revision")),
        "orchestration_revision": str(
            dict_or_empty(manifest.get("orchestration")).get("revision")
        ),
        "branch": str(definition.get("branch")),
        "worktree": str(definition.get("worktree")),
        "start_sha": str(state.get("start_sha")),
    }
    for key, expected in expected_metadata.items():
        if _markdown_metadata(text, key) != expected:
            errors.append(
                diagnostic(
                    "E_TASK_REPORT_STALE",
                    f"{task_id} report metadata {key} is stale",
                )
            )
    revisions = _markdown_metadata(text, "contract_revisions")
    try:
        parsed_revisions = json.loads(revisions) if revisions is not None else None
    except json.JSONDecodeError:
        parsed_revisions = None
    if (
        not is_str_list(parsed_revisions)
        or sorted(parsed_revisions)
        != sorted(list_or_empty(definition.get("contracts")))
    ):
        errors.append(
            diagnostic(
                "E_TASK_REPORT_CONTRACTS",
                f"{task_id} report contract revisions are stale",
            )
        )
    risk_ids = _markdown_metadata(text, "risk_ids")
    try:
        parsed_risk_ids = json.loads(risk_ids) if risk_ids is not None else None
    except json.JSONDecodeError:
        parsed_risk_ids = None
    expected_risk_ids = sorted(
        risk["id"]
        for risk in list_or_empty(state.get("risks"))
        if isinstance(risk, dict) and is_resolved_string(risk.get("id"))
    )
    if (
        not is_str_list(parsed_risk_ids)
        or sorted(parsed_risk_ids) != expected_risk_ids
    ):
        errors.append(
            diagnostic(
                "E_TASK_REPORT_RISKS",
                f"{task_id} report risk_ids do not match runtime risk ledger",
            )
        )
    for requirement in _validate_test_requirements(
        definition.get("required_tests"),
        f"{task_id}.required_tests",
        errors,
    ):
        if requirement["id"] not in text:
            errors.append(
                diagnostic(
                    "E_TASK_REPORT_TEST_COVERAGE",
                    f"{task_id} report does not mention planned test {requirement['id']}",
                )
            )
    for heading in (
        "Changes",
        "Contracts Used",
        "Verification",
        "Not Run",
        "Known Risks, TODOs and Scope Drift",
    ):
        content = _markdown_section(text, heading)
        if (
            content is None
            or not content.strip()
            or content.strip().casefold() == "pending"
        ):
            errors.append(
                diagnostic(
                    "E_TASK_REPORT_INCOMPLETE",
                    f"{task_id} report section {heading!r} is incomplete",
                )
            )


def _validate_task_risks(
    value: Any,
    *,
    task_id: str,
    plan_revision: Any,
    orchestration_revision: Any,
    head_sha: Any,
    errors: list[Diagnostic],
    evidence_reader: Callable[[str], bytes] | None,
) -> set[str]:
    if not isinstance(value, list):
        errors.append(
            diagnostic("E_TASK_RISKS", f"{task_id}.risks must be a list")
        )
        return set()
    accepted_ids: set[str] = set()
    seen: set[str] = set()
    for index, item in enumerate(value):
        risk = _require_object(
            item,
            ("id", "severity", "status", "description", "disposition"),
            f"{task_id}.risks[{index}]",
            errors,
        )
        if risk is None:
            continue
        risk_id = risk["id"]
        if (
            not is_resolved_string(risk_id)
            or risk_id in seen
            or not is_str(risk["severity"])
            or risk["severity"] not in SEVERITY_RANK
            or not is_resolved_string(risk["description"])
        ):
            errors.append(
                diagnostic(
                    "E_TASK_RISKS",
                    f"{task_id}.risks[{index}] has invalid identity or content",
                )
            )
            continue
        seen.add(risk_id)
        if risk["status"] == "closed":
            disposition = _require_object(
                risk["disposition"],
                ("kind", "evidence_ref"),
                f"{task_id}.risks[{index}].disposition",
                errors,
            )
            if disposition is not None:
                if disposition["kind"] != "closed":
                    errors.append(
                        diagnostic(
                            "E_TASK_RISK_DISPOSITION",
                            f"{task_id}.{risk_id} closed disposition is invalid",
                        )
                    )
                _read_evidence_ref(
                    disposition["evidence_ref"],
                    f"{task_id}.risks[{index}].disposition.evidence_ref",
                    errors,
                    evidence_reader,
                )
        elif risk["status"] == "accepted":
            disposition = _require_object(
                risk["disposition"],
                (
                    "kind",
                    "reason",
                    "impact",
                    "authorization_basis",
                    "authorized_by",
                    "review_condition",
                    "follow_up_owner",
                ),
                f"{task_id}.risks[{index}].disposition",
                errors,
            )
            if (
                disposition is None
                or disposition["kind"] != "accepted"
                or risk["severity"] == "P0"
                or any(
                    not is_resolved_string(disposition[field])
                    for field in (
                        "reason",
                        "impact",
                        "authorization_basis",
                        "authorized_by",
                        "review_condition",
                        "follow_up_owner",
                    )
                )
            ):
                errors.append(
                    diagnostic(
                        "E_TASK_RISK_DISPOSITION",
                        f"{task_id}.{risk_id} accepted disposition is invalid",
                    )
                )
            else:
                accepted_ids.add(risk_id)
        else:
            errors.append(
                diagnostic(
                    "E_TASK_RISK_OPEN",
                    f"{task_id}.{risk_id} is not closed or accepted",
                )
            )
        if (
            not is_resolved_string(plan_revision)
            or not is_resolved_string(orchestration_revision)
            or not is_oid(head_sha)
        ):
            errors.append(
                diagnostic(
                    "E_TASK_RISK_IDENTITY",
                    f"{task_id}.{risk_id} cannot bind current revisions and HEAD",
                )
            )
    return accepted_ids


def _validate_test_evidence(
    value: Any,
    *,
    location: str,
    expected_workflow: str,
    expected_plan: str,
    expected_orchestration: str,
    expected_subject: str,
    expected_head: str,
    expected_tests: list[dict[str, Any]],
    errors: list[Diagnostic],
    evidence_reader: Callable[[str], bytes] | None,
) -> None:
    payload = _read_evidence_json(value, location, errors, evidence_reader)
    if payload is None:
        return
    expected = {
        "schema_version": 1,
        "workflow_id": expected_workflow,
        "plan_revision": expected_plan,
        "orchestration_revision": expected_orchestration,
        "subject_id": expected_subject,
        "status": "passed",
        "tested_head_sha": expected_head,
    }
    for field, expected_value in expected.items():
        actual = payload.get(field)
        if (
            field == "schema_version"
            and (type(actual) is not int or actual != expected_value)
        ) or (field != "schema_version" and actual != expected_value):
            errors.append(
                diagnostic(
                    "E_TEST_EVIDENCE_STALE",
                    f"{location}.{field} does not match the fixed test subject",
                )
            )
    commands = payload.get("commands")
    if not isinstance(commands, list) or not commands:
        errors.append(
            diagnostic(
                "E_TEST_EVIDENCE_COMMANDS",
                f"{location}.commands must be non-empty",
            )
        )
        return
    for index, item in enumerate(commands):
        record = _require_object(
            item,
            ("test_id", "command", "exit_code", "status", "result"),
            f"{location}.commands[{index}]",
            errors,
        )
        if record is None:
            continue
        if (
            not is_resolved_string(record["test_id"])
            or not is_resolved_string(record["command"])
            or type(record["exit_code"]) is not int
            or record["exit_code"] != 0
            or record["status"] != "passed"
            or not is_resolved_string(record["result"])
        ):
            errors.append(
                diagnostic(
                    "E_TEST_EVIDENCE_COMMAND",
                    f"{location}.commands[{index}] is not a passed command",
                )
            )
    if isinstance(commands, list):
        actual_commands = {
            item.get("test_id"): item.get("command")
            for item in commands
            if isinstance(item, dict) and is_resolved_string(item.get("test_id"))
        }
        expected_commands = {
            item["id"]: item["command"]
            for item in expected_tests
            if isinstance(item, dict)
            and is_resolved_string(item.get("id"))
            and is_resolved_string(item.get("command"))
        }
        if actual_commands != expected_commands or len(actual_commands) != len(
            commands
        ):
            errors.append(
                diagnostic(
                    "E_TEST_EVIDENCE_COVERAGE",
                    f"{location} does not exactly cover the planned test IDs and commands",
                )
            )


def _validate_test_requirements(
    value: Any,
    location: str,
    errors: list[Diagnostic],
    *,
    allowed_acceptance_criteria: set[str] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        errors.append(
            diagnostic(
                "E_TEST_REQUIREMENTS",
                f"{location} must be a non-empty list",
            )
        )
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        record = _require_object(
            item,
            ("id", "command", "acceptance_criteria"),
            f"{location}[{index}]",
            errors,
        )
        if record is None:
            continue
        test_id = record["id"]
        command = record["command"]
        if (
            not is_resolved_string(test_id)
            or not is_resolved_string(command)
            or test_id in seen
        ):
            errors.append(
                diagnostic(
                    "E_TEST_REQUIREMENTS",
                    f"{location}[{index}] has invalid or duplicate identity",
                )
            )
            continue
        criteria = record["acceptance_criteria"]
        if (
            not is_str_list(criteria)
            or not criteria
            or len(criteria) != len(set(criteria))
            or (
                allowed_acceptance_criteria is not None
                and not set(criteria).issubset(allowed_acceptance_criteria)
            )
        ):
            errors.append(
                diagnostic(
                    "E_TEST_ACCEPTANCE_CRITERIA",
                    f"{location}[{index}].acceptance_criteria is invalid or out of scope",
                )
            )
            continue
        seen.add(test_id)
        result.append(
            {
                "id": test_id,
                "command": command,
                "acceptance_criteria": list(criteria),
            }
        )
    return result


def _validate_artifact_evidence(
    value: Any,
    *,
    location: str,
    expected_workflow: str,
    expected_plan: str,
    expected_orchestration: str,
    expected_subject: str,
    expected_head: str,
    expected_paths: list[str],
    errors: list[Diagnostic],
    evidence_reader: Callable[[str], bytes] | None,
    read_object: Callable[[str, str], bytes] | None,
) -> None:
    payload = _read_evidence_json(value, location, errors, evidence_reader)
    if payload is None:
        return
    expected = {
        "schema_version": 1,
        "workflow_id": expected_workflow,
        "plan_revision": expected_plan,
        "orchestration_revision": expected_orchestration,
        "subject_id": expected_subject,
        "status": "available",
        "source_sha": expected_head,
    }
    for field, expected_value in expected.items():
        actual = payload.get(field)
        if (
            field == "schema_version"
            and (type(actual) is not int or actual != expected_value)
        ) or (field != "schema_version" and actual != expected_value):
            errors.append(
                diagnostic(
                    "E_ARTIFACT_EVIDENCE_STALE",
                    f"{location}.{field} does not match the fixed artifact subject",
                )
            )
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        errors.append(
            diagnostic(
                "E_ARTIFACT_EVIDENCE_CONTENT",
                f"{location}.artifacts must be a list",
            )
        )
        return
    paths: list[str] = []
    for index, item in enumerate(artifacts):
        record = _require_object(
            item,
            ("path", "sha256"),
            f"{location}.artifacts[{index}]",
            errors,
        )
        if record is None:
            continue
        if not is_safe_relative_path(record["path"]) or not is_sha256(
            record["sha256"]
        ):
            errors.append(
                diagnostic(
                    "E_ARTIFACT_EVIDENCE_CONTENT",
                    f"{location}.artifacts[{index}] is invalid",
                )
            )
        else:
            paths.append(record["path"])
            if read_object is None or not is_oid(expected_head):
                errors.append(
                    diagnostic(
                        "E_ARTIFACT_HASH_PROOF",
                        f"{location}.artifacts[{index}] cannot verify Git blob content",
                    )
                )
            else:
                try:
                    raw = read_object(expected_head, record["path"])
                except Exception as exc:
                    errors.append(
                        diagnostic(
                            "E_ARTIFACT_HASH_PROOF",
                            f"{location}.artifacts[{index}] Git blob read failed: {exc}",
                        )
                    )
                else:
                    if (
                        not isinstance(raw, bytes)
                        or hashlib.sha256(raw).hexdigest() != record["sha256"]
                    ):
                        errors.append(
                            diagnostic(
                                "E_ARTIFACT_HASH_STALE",
                                f"{location}.artifacts[{index}] digest does not match source_sha",
                            )
                        )
    if sorted(paths) != sorted(expected_paths):
        errors.append(
            diagnostic(
                "E_ARTIFACT_EVIDENCE_CONTENT",
                f"{location} artifact paths do not match the dependency contract",
            )
        )


def _validate_integration_execution(
    value: Any,
    manifest: dict[str, Any],
    state: dict[str, Any],
    merged_map: dict[str, dict[str, Any]],
    errors: list[Diagnostic],
    evidence_reader: Callable[[str], bytes] | None,
) -> None:
    raw = _read_evidence_ref(
        value,
        "integration.execution_log_ref",
        errors,
        evidence_reader,
    )
    if raw is None:
        return
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        errors.append(
            diagnostic("E_INTEGRATION_LOG_FORMAT", f"integration log: {exc}")
        )
        return
    if "{{" in text or "}}" in text:
        errors.append(
            diagnostic(
                "E_INTEGRATION_LOG_TEMPLATE",
                "integration execution log contains unresolved placeholders",
            )
        )
    expected_metadata = {
        "execution_schema_version": "1",
        "execution_status": "completed",
        "workflow_id": str(manifest.get("workflow_id")),
        "plan_revision": str(dict_or_empty(manifest.get("plan")).get("revision")),
        "orchestration_revision": str(
            dict_or_empty(manifest.get("orchestration")).get("revision")
        ),
        "integration_start_sha": str(state.get("start_sha")),
        "integration_head_sha": str(state.get("head_sha")),
        "clean": "true",
        "unresolved_risk_count": "0",
        "write_authorization_basis": str(
            state.get("write_authorization_basis")
        ),
        "tests evidence": str(
            dict_or_empty(
                dict_or_empty(state.get("tests")).get("evidence_ref")
            ).get("path")
        ),
        "final Review result": str(
            dict_or_empty(
                dict_or_empty(state.get("final_review")).get("report_ref")
            ).get("path")
        ),
    }
    for key, expected in expected_metadata.items():
        actual = _markdown_metadata(text, key)
        if actual is None or actual.casefold() != expected.casefold():
            errors.append(
                diagnostic(
                    "E_INTEGRATION_LOG_STALE",
                    f"integration execution metadata {key} is stale",
                )
            )
    for task_id, record in merged_map.items():
        for token in (
            task_id,
            str(record.get("reviewed_head_sha")),
            str(record.get("integration_head_after")),
        ):
            if token not in text:
                errors.append(
                    diagnostic(
                        "E_INTEGRATION_LOG_TASK",
                        f"integration execution log is missing {task_id} evidence",
                    )
                )
                break
    logged_task_ids = {
        match.group(1)
        for match in re.finditer(
            r"^\|\s*(TASK-[^|\s]+)\s*\|",
            text,
            re.MULTILINE,
        )
    }
    if logged_task_ids != set(merged_map):
        errors.append(
            diagnostic(
                "E_INTEGRATION_LOG_TASK_SET",
                "integration execution task rows do not exactly match runtime",
            )
        )


def _require_tokens(
    text: str | None,
    tokens: Iterable[str],
    location: str,
    errors: list[Diagnostic],
) -> None:
    if text is None:
        return
    for token in tokens:
        if not is_str(token):
            errors.append(
                diagnostic(
                    "E_ARTIFACT_CONTENT",
                    f"{location} contains an invalid required token",
                )
            )
            continue
        if token not in text:
            errors.append(
                diagnostic(
                    "E_ARTIFACT_CONTENT",
                    f"{location} does not contain {token!r}",
                )
            )


def _validate_dependencies(
    task_map: dict[str, dict[str, Any]],
    plan_acceptance_ids: set[str],
    errors: list[Diagnostic],
) -> None:
    graph = {task_id: set() for task_id in task_map}
    dependency_ids: set[str] = set()
    for task_id, task in task_map.items():
        dependencies = task.get("dependencies")
        if not isinstance(dependencies, list):
            errors.append(
                diagnostic(
                    "E_DEPENDENCY_TYPE",
                    f"{task_id}.dependencies must be a list",
                )
            )
            continue
        for index, item in enumerate(dependencies):
            dependency = _require_object(
                item,
                ("id", "task_id", "type", "unblocks_on", "expected_evidence"),
                f"{task_id}.dependencies[{index}]",
                errors,
            )
            if dependency is None:
                continue
            dependency_id = dependency["id"]
            if not is_resolved_string(dependency_id):
                errors.append(
                    diagnostic(
                        "E_DEPENDENCY_ID",
                        f"{task_id}.dependencies[{index}].id is invalid",
                    )
                )
            elif dependency_id in dependency_ids:
                errors.append(
                    diagnostic(
                        "E_DEPENDENCY_ID",
                        f"duplicate dependency id {dependency_id}",
                    )
                )
            else:
                dependency_ids.add(dependency_id)
            upstream = dependency["task_id"]
            if not is_resolved_string(upstream) or upstream not in task_map:
                errors.append(
                    diagnostic(
                        "E_DEPENDENCY_UNKNOWN",
                        f"{task_id} references unknown task {upstream!r}",
                    )
                )
            elif upstream == task_id:
                errors.append(
                    diagnostic("E_DEPENDENCY_SELF", f"{task_id} depends on itself")
                )
            else:
                graph[task_id].add(upstream)
            if (
                not is_str(dependency["type"])
                or dependency["type"] not in DEPENDENCY_TYPES
            ):
                errors.append(
                    diagnostic(
                        "E_DEPENDENCY_KIND",
                        f"{task_id}.dependencies[{index}].type is invalid",
                    )
                )
            if (
                not is_str(dependency["unblocks_on"])
                or dependency["unblocks_on"] not in UNBLOCK_CONDITIONS
            ):
                errors.append(
                    diagnostic(
                        "E_DEPENDENCY_UNBLOCK",
                        f"{task_id}.dependencies[{index}].unblocks_on is invalid",
                    )
                )
            if (
                is_str(dependency["type"])
                and dependency["type"] in DEPENDENCY_PAIRS
                and dependency["unblocks_on"]
                != DEPENDENCY_PAIRS[dependency["type"]]
            ):
                errors.append(
                    diagnostic(
                        "E_DEPENDENCY_PAIR",
                        f"{task_id}.dependencies[{index}] has an invalid type/unblocks_on pair",
                    )
                )
            expected = dependency["expected_evidence"]
            if not isinstance(expected, dict):
                errors.append(
                    diagnostic(
                        "E_DEPENDENCY_EXPECTATION",
                        f"{task_id}.dependencies[{index}].expected_evidence must be an object",
                    )
                )
            elif dependency["type"] == "contract":
                revisions = expected.get("contract_revisions")
                if not is_str_list(revisions) or not revisions:
                    errors.append(
                        diagnostic(
                            "E_DEPENDENCY_EXPECTATION",
                            f"{dependency_id}.contract_revisions must be non-empty",
                        )
                    )
            elif dependency["type"] == "artifact":
                paths = expected.get("artifact_paths")
                if (
                    not isinstance(paths, list)
                    or not paths
                    or any(not is_safe_relative_path(path) for path in paths)
                ):
                    errors.append(
                        diagnostic(
                            "E_DEPENDENCY_EXPECTATION",
                            f"{dependency_id}.artifact_paths must be non-empty safe paths",
                        )
                    )
            elif is_str(dependency["type"]) and dependency["type"] in {
                "build",
                "test",
            }:
                if not is_resolved_string(expected.get("evidence_subject")):
                    errors.append(
                        diagnostic(
                            "E_DEPENDENCY_EXPECTATION",
                            f"{dependency_id}.evidence_subject is required",
                        )
                    )
                _validate_test_requirements(
                    expected.get("required_tests"),
                    f"{dependency_id}.required_tests",
                    errors,
                    allowed_acceptance_criteria=plan_acceptance_ids,
                )
            elif (
                is_str(dependency["type"])
                and dependency["type"] in {"code", "integration"}
                and expected
            ):
                errors.append(
                    diagnostic(
                        "E_DEPENDENCY_EXPECTATION",
                        f"{dependency_id}.expected_evidence must be empty for {dependency['type']}",
                    )
                )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> bool:
        if task_id in visiting:
            return True
        if task_id in visited:
            return False
        visiting.add(task_id)
        for upstream in graph[task_id]:
            if visit(upstream):
                return True
        visiting.remove(task_id)
        visited.add(task_id)
        return False

    if any(visit(task_id) for task_id in graph if task_id not in visited):
        errors.append(diagnostic("E_DEPENDENCY_CYCLE", "task DAG contains a cycle"))


def _validate_contracts_and_tasks(
    manifest: dict[str, Any],
    ai_root: Path | None,
    errors: list[Diagnostic],
    artifact_reader: Callable[[str], bytes] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    tasks = manifest.get("tasks")
    contracts = manifest.get("contracts")
    if not isinstance(tasks, list) or not tasks:
        errors.append(diagnostic("E_TASKS", "tasks must be a non-empty list"))
        tasks = []
    if not isinstance(contracts, list):
        errors.append(diagnostic("E_CONTRACTS", "contracts must be a list"))
        contracts = []

    plan_revision = (
        dict_or_empty(manifest.get("plan")).get("revision")
        if isinstance(manifest.get("plan"), dict)
        else None
    )
    orchestration_revision = (
        dict_or_empty(manifest.get("orchestration")).get("revision")
        if isinstance(manifest.get("orchestration"), dict)
        else None
    )
    workflow_id = manifest.get("workflow_id")
    plan_acceptance_criteria = _plan_acceptance_criteria_map(manifest)
    plan_acceptance_ids = set(plan_acceptance_criteria)

    task_map: dict[str, dict[str, Any]] = {}
    branches: set[str] = set()
    worktrees: set[str] = set()
    for index, item in enumerate(tasks):
        task = _require_object(
            item,
            (
                "id",
                "path",
                "report_path",
                "owner",
                "branch",
                "worktree",
                "dependencies",
                "contracts",
                "acceptance_criteria",
                "required_tests",
                "allowed_paths",
                "sources",
                "supersedes",
                "review_scope",
            ),
            f"tasks[{index}]",
            errors,
        )
        if task is None:
            continue
        task_id = task["id"]
        if not is_resolved_string(task_id):
            errors.append(diagnostic("E_TASK_ID", f"tasks[{index}].id is invalid"))
            continue
        if task_id in task_map:
            errors.append(diagnostic("E_TASK_DUPLICATE", f"duplicate task {task_id}"))
            continue
        task_map[task_id] = task
        for field, seen in (("branch", branches), ("worktree", worktrees)):
            value = task[field]
            if not is_resolved_string(value):
                errors.append(
                    diagnostic("E_TASK_HANDOFF", f"{task_id}.{field} is invalid")
                )
            elif value in seen:
                errors.append(
                    diagnostic(
                        "E_TASK_HANDOFF_DUPLICATE",
                        f"duplicate {field}: {value}",
                    )
                )
            else:
                seen.add(value)
        if not is_resolved_string(task["owner"]):
            errors.append(diagnostic("E_TASK_OWNER", f"{task_id}.owner is invalid"))
        if not is_str_list(task["contracts"]):
            errors.append(
                diagnostic(
                    "E_TASK_CONTRACTS",
                    f"{task_id}.contracts must be a string list",
                )
            )
        task_acceptance_criteria = task["acceptance_criteria"]
        if (
            not is_str_list(task_acceptance_criteria)
            or not task_acceptance_criteria
            or len(task_acceptance_criteria) != len(set(task_acceptance_criteria))
            or not set(task_acceptance_criteria).issubset(plan_acceptance_ids)
        ):
            errors.append(
                diagnostic(
                    "E_TASK_ACCEPTANCE_CRITERIA",
                    f"{task_id}.acceptance_criteria is invalid or references unknown ACs",
                )
            )
            task_acceptance_ids: set[str] = set()
        else:
            task_acceptance_ids = set(task_acceptance_criteria)
        planned_tests = _validate_test_requirements(
            task["required_tests"],
            f"{task_id}.required_tests",
            errors,
            allowed_acceptance_criteria=task_acceptance_ids,
        )
        if (
            not is_str_list(task["allowed_paths"])
            or not task["allowed_paths"]
            or any(not is_safe_relative_path(path) for path in task["allowed_paths"])
        ):
            errors.append(
                diagnostic(
                    "E_TASK_ALLOWED_PATHS",
                    f"{task_id}.allowed_paths must be a non-empty safe path/pattern list",
                )
            )
        if not is_str_list(task["sources"]) or not task["sources"]:
            errors.append(
                diagnostic("E_TASK_SOURCES", f"{task_id}.sources must be non-empty")
            )
        if task["supersedes"] is not None and not is_resolved_string(
            task["supersedes"]
        ):
            errors.append(
                diagnostic("E_TASK_SUPERSEDES", f"{task_id}.supersedes is invalid")
            )
        review_scope = _validate_review_scope_definition(
            task["review_scope"],
            f"{task_id}.review_scope",
            errors,
            plan_revision,
        )
        if review_scope is not None and (
            len(_review_scope_acceptance_criteria(review_scope))
            != len(task_acceptance_ids)
            or set(_review_scope_acceptance_criteria(review_scope))
            != task_acceptance_ids
        ):
            errors.append(
                diagnostic(
                    "E_TASK_REVIEW_AC_COVERAGE",
                    f"{task_id}.review_scope must exactly cover task acceptance criteria",
                )
            )
        task_text = _read_artifact(
            ai_root,
            task["path"],
            f"{task_id}.path",
            errors,
            artifact_reader,
        )
        _require_tokens(
            task_text,
            (
                str(workflow_id),
                str(plan_revision),
                str(orchestration_revision),
                task_id,
                task["report_path"],
                "sources",
                "supersedes",
                "## Scope",
                "## Dependencies",
                "## Contracts",
                "## Build and Tests",
                *sorted(task_acceptance_ids),
                *sorted(
                    item["id"]
                    for item in planned_tests
                    if is_resolved_string(item.get("id"))
                ),
            ),
            f"{task_id}.path",
            errors,
        )
        report_text = _read_artifact(
            ai_root,
            task["report_path"],
            f"{task_id}.report_path",
            errors,
            artifact_reader,
        )
        _require_tokens(
            report_text,
            (
                str(workflow_id),
                str(plan_revision),
                str(orchestration_revision),
                task_id,
                "sources",
                "supersedes",
                "## Verification",
                "## Not Run",
                "## Known Risks",
            ),
            f"{task_id}.report_path",
            errors,
        )

    _validate_dependencies(task_map, plan_acceptance_ids, errors)

    contract_map: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(contracts):
        contract = _require_object(
            item,
            (
                "id",
                "revision",
                "status",
                "based_on_plan_revision",
                "supersedes",
                "sources",
                "path",
                "producer",
                "consumers",
            ),
            f"contracts[{index}]",
            errors,
        )
        if contract is None:
            continue
        contract_id = contract["id"]
        revision = contract["revision"]
        if not is_resolved_string(contract_id) or not is_resolved_string(revision):
            errors.append(
                diagnostic(
                    "E_CONTRACT_ID",
                    f"contracts[{index}] id/revision is invalid",
                )
            )
            continue
        key = f"{contract_id}@{revision}"
        if key in contract_map:
            errors.append(
                diagnostic("E_CONTRACT_DUPLICATE", f"duplicate contract {key}")
            )
            continue
        contract_map[key] = contract
        if contract["status"] != "confirmed":
            errors.append(
                diagnostic("E_CONTRACT_STATUS", f"{key}.status must be confirmed")
            )
        if contract["based_on_plan_revision"] != plan_revision:
            errors.append(
                diagnostic(
                    "E_CONTRACT_PLAN_STALE",
                    f"{key} is based on a stale plan revision",
                )
            )
        if contract["supersedes"] is not None and not is_resolved_string(
            contract["supersedes"]
        ):
            errors.append(
                diagnostic("E_CONTRACT_SUPERSEDES", f"{key}.supersedes is invalid")
            )
        elif contract["supersedes"] is not None:
            superseded = contract["supersedes"]
            superseded_parts = superseded.split("@", 1)
            if (
                len(superseded_parts) != 2
                or superseded_parts[0] != contract_id
                or not is_resolved_string(superseded_parts[1])
                or superseded == key
            ):
                errors.append(
                    diagnostic(
                        "E_CONTRACT_SUPERSEDES",
                        f"{key}.supersedes must reference an older revision of the same contract",
                    )
                )
        if not is_str_list(contract["sources"]) or not contract["sources"]:
            errors.append(
                diagnostic("E_CONTRACT_SOURCES", f"{key}.sources must be non-empty")
            )
        producer = contract["producer"]
        consumers = contract["consumers"]
        if not is_resolved_string(producer) or producer not in task_map:
            errors.append(
                diagnostic("E_CONTRACT_PRODUCER", f"{key} producer is unknown")
            )
        if not is_str_list(consumers) or any(
            consumer not in task_map for consumer in consumers
        ):
            errors.append(
                diagnostic("E_CONTRACT_CONSUMER", f"{key} consumers are invalid")
            )
            consumers = []
        contract_text = _read_artifact(
            ai_root,
            contract["path"],
            f"{key}.path",
            errors,
            artifact_reader,
        )
        _require_tokens(
            contract_text,
            (
                str(workflow_id),
                str(plan_revision),
                contract_id,
                revision,
                "sources",
                "supersedes",
                "## Inputs and Outputs",
                "## Invariants",
                "## Error Handling",
                "## Change Policy",
            ),
            f"{key}.path",
            errors,
        )

    for task_id, task in task_map.items():
        contracts_used = task.get("contracts")
        if not is_str_list(contracts_used):
            continue
        for key in contracts_used:
            contract = contract_map.get(key)
            if contract is None:
                errors.append(
                    diagnostic(
                        "E_TASK_CONTRACT_UNKNOWN",
                        f"{task_id} references unknown contract {key}",
                    )
                )
                continue
            consumers = contract["consumers"]
            participants: set[str] = set()
            if is_resolved_string(contract["producer"]):
                participants.add(contract["producer"])
            if isinstance(consumers, list):
                participants.update(
                    consumer for consumer in consumers if is_resolved_string(consumer)
                )
            if task_id not in participants:
                errors.append(
                    diagnostic(
                        "E_TASK_CONTRACT_ROLE",
                        f"{task_id} has no role in {key}",
                    )
                )
    for key, contract in contract_map.items():
        consumers = (
            [
                consumer
                for consumer in contract["consumers"]
                if is_resolved_string(consumer)
            ]
            if isinstance(contract["consumers"], list)
            else []
        )
        participants = set(consumers)
        if is_resolved_string(contract["producer"]):
            participants.add(contract["producer"])
        for participant in participants:
            task = task_map.get(participant)
            if task is not None and key not in list_or_empty(task.get("contracts")):
                errors.append(
                    diagnostic(
                        "E_CONTRACT_TASK_MISMATCH",
                        f"{participant} does not declare {key}",
                    )
                )
    return task_map, contract_map


def _validate_acceptance_traceability(
    manifest: dict[str, Any],
    errors: list[Diagnostic],
) -> None:
    plan_acceptance_ids = set(_plan_acceptance_criteria_map(manifest))
    if not plan_acceptance_ids:
        return
    task_acceptance: dict[str, set[str]] = {}
    task_test_coverage: dict[str, set[str]] = {}
    all_test_coverage: set[str] = set()
    test_locations: dict[str, str] = {}

    def record_tests(
        value: Any,
        *,
        location: str,
        task_id: str | None,
        allowed_ids: set[str],
    ) -> None:
        for index, item in enumerate(list_or_empty(value)):
            if not isinstance(item, dict):
                continue
            test_id = item.get("id")
            if is_resolved_string(test_id):
                previous = test_locations.get(test_id)
                if previous is not None:
                    errors.append(
                        diagnostic(
                            "E_TEST_ID_GLOBAL_DUPLICATE",
                            f"{test_id} is declared by both {previous} and {location}[{index}]",
                        )
                    )
                else:
                    test_locations[test_id] = f"{location}[{index}]"
            criteria = item.get("acceptance_criteria")
            if not is_str_list(criteria):
                continue
            valid_criteria = set(criteria) & allowed_ids
            all_test_coverage.update(valid_criteria)
            if task_id is not None:
                task_test_coverage.setdefault(task_id, set()).update(valid_criteria)

    for item in list_or_empty(manifest.get("tasks")):
        if not isinstance(item, dict) or not is_resolved_string(item.get("id")):
            continue
        task_id = item["id"]
        criteria = item.get("acceptance_criteria")
        assigned = (
            set(criteria) & plan_acceptance_ids if is_str_list(criteria) else set()
        )
        task_acceptance[task_id] = assigned
        task_test_coverage[task_id] = set()
        record_tests(
            item.get("required_tests"),
            location=f"{task_id}.required_tests",
            task_id=task_id,
            allowed_ids=assigned,
        )

    integration = dict_or_empty(manifest.get("integration"))
    checkpoint_tests = integration.get("checkpoint_tests")
    if isinstance(checkpoint_tests, dict):
        for task_id, requirements in checkpoint_tests.items():
            if not is_resolved_string(task_id):
                continue
            record_tests(
                requirements,
                location=f"integration.checkpoint_tests.{task_id}",
                task_id=task_id,
                allowed_ids=task_acceptance.get(task_id, set()),
            )

    integration_coverage: set[str] = set()
    for item in list_or_empty(integration.get("required_tests")):
        if isinstance(item, dict) and is_str_list(item.get("acceptance_criteria")):
            integration_coverage.update(
                set(item["acceptance_criteria"]) & plan_acceptance_ids
            )
    record_tests(
        integration.get("required_tests"),
        location="integration.required_tests",
        task_id=None,
        allowed_ids=plan_acceptance_ids,
    )
    for task_id, assigned in task_acceptance.items():
        task_test_coverage[task_id].update(assigned & integration_coverage)

    assigned_criteria = (
        set().union(*task_acceptance.values()) if task_acceptance else set()
    )
    missing_tasks = plan_acceptance_ids - assigned_criteria
    if missing_tasks:
        errors.append(
            diagnostic(
                "E_ACCEPTANCE_TASK_COVERAGE",
                "acceptance criteria without a task: "
                + ", ".join(sorted(missing_tasks)),
            )
        )
    missing_tests = plan_acceptance_ids - all_test_coverage
    if missing_tests:
        errors.append(
            diagnostic(
                "E_ACCEPTANCE_TEST_COVERAGE",
                "acceptance criteria without a planned test: "
                + ", ".join(sorted(missing_tests)),
            )
        )
    for task_id, assigned in task_acceptance.items():
        missing = assigned - task_test_coverage.get(task_id, set())
        if missing:
            errors.append(
                diagnostic(
                    "E_TASK_ACCEPTANCE_TEST_COVERAGE",
                    f"{task_id} acceptance criteria without task/checkpoint/integration test: "
                    + ", ".join(sorted(missing)),
                )
            )


def _validate_artifacts(
    manifest: dict[str, Any],
    ai_root: Path | None,
    errors: list[Diagnostic],
    artifact_reader: Callable[[str], bytes] | None = None,
) -> None:
    artifacts = _require_object(
        manifest.get("artifacts"),
        ("solution_record", "orchestration_record", "integration_plan"),
        "artifacts",
        errors,
    )
    if artifacts is None:
        return
    workflow_id = manifest.get("workflow_id")
    plan_revision = dict_or_empty(manifest.get("plan")).get("revision")
    orchestration_revision = dict_or_empty(manifest.get("orchestration")).get(
        "revision"
    )
    plan_acceptance_ids = tuple(
        sorted(_plan_acceptance_criteria_map(manifest))
    )
    specifications = (
        (
            "solution_record",
            (
                str(workflow_id),
                str(plan_revision),
                "sources",
                "supersedes",
                "## Objective",
                "## Scope",
                "## Acceptance Criteria",
                "## Verification",
                *plan_acceptance_ids,
            ),
        ),
        (
            "orchestration_record",
            (
                str(workflow_id),
                str(plan_revision),
                str(orchestration_revision),
                "sources",
                "supersedes",
                "## Task DAG",
                "## Contracts",
                "## File Ownership",
                "## Review and Integration",
            ),
        ),
        (
            "integration_plan",
            (
                str(workflow_id),
                str(plan_revision),
                str(orchestration_revision),
                "sources",
                "supersedes",
                "## Inputs",
                "## Merge Order",
                "## Final Verification",
                "## Recovery",
            ),
        ),
    )
    for field, tokens in specifications:
        text = _read_artifact(
            ai_root,
            artifacts[field],
            f"artifacts.{field}",
            errors,
            artifact_reader,
        )
        _require_tokens(text, tokens, f"artifacts.{field}", errors)


def _validate_delivery_definition(manifest: dict[str, Any], errors: list[Diagnostic]) -> None:
    """Validate the opt-in extension early; legacy v3 remains readable."""
    if "delivery" not in manifest:
        return
    spec = manifest["delivery"]
    if not isinstance(spec, dict) or type(spec.get("schema_version")) is not int or spec["schema_version"] != 1:
        errors.append(diagnostic("E_DELIVERY_SCHEMA", "delivery schema_version must be 1"))
        return
    if spec.get("excluded_paths") != [".ai/"]:
        errors.append(diagnostic("E_DELIVERY_EXCLUSIONS", "delivery v1 excludes only root .ai/"))
    source = spec.get("original_request_ref")
    if not isinstance(source, dict) or not is_safe_relative_path(source.get("path")) or not is_sha256(source.get("sha256")):
        errors.append(diagnostic("E_DELIVERY_SOURCE", "original request needs a safe path and SHA-256"))
    requirements = spec.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        errors.append(diagnostic("E_DELIVERY_REQUIREMENTS", "original requirement inventory is required"))
        return
    ids: set[str] = set()
    covered: set[str] = set()
    criteria = set(_plan_acceptance_criteria_map(manifest))
    for item in requirements:
        if not isinstance(item, dict):
            errors.append(diagnostic("E_DELIVERY_REQUIREMENTS", "requirement must be an object"))
            continue
        key, mapped = item.get("id"), item.get("acceptance_criteria")
        if not is_resolved_string(key) or re.fullmatch(r"REQ-[A-Za-z0-9._-]+", key) is None or key in ids:
            errors.append(diagnostic("E_DELIVERY_REQUIREMENTS", "requirement ID must be valid and unique"))
        else:
            ids.add(key)
        if not is_str_list(mapped) or not mapped or len(mapped) != len(set(mapped)) or not set(mapped) <= criteria:
            errors.append(diagnostic("E_DELIVERY_REQUIREMENTS", "requirement needs known unique AC references"))
        else:
            covered.update(mapped)
    if covered != criteria:
        errors.append(diagnostic("E_DELIVERY_REQUIREMENTS", "requirements must cover every plan AC"))


def _validate_runtime_identity(
    manifest: dict[str, Any],
    runtime: dict[str, Any],
    errors: list[Diagnostic],
) -> None:
    if type(manifest.get("schema_version")) is not int or manifest.get(
        "schema_version"
    ) != 3:
        errors.append(diagnostic("E_SCHEMA_VERSION", "manifest schema_version must be 3"))
    if type(runtime.get("schema_version")) is not int or runtime.get(
        "schema_version"
    ) != 3:
        errors.append(diagnostic("E_RUNTIME_SCHEMA", "runtime schema_version must be 3"))
    if not is_resolved_string(manifest.get("workflow_id")):
        errors.append(diagnostic("E_WORKFLOW_ID", "manifest workflow_id is invalid"))
    if not is_resolved_string(manifest.get("runtime_state_id")):
        errors.append(
            diagnostic("E_RUNTIME_STATE_ID", "manifest runtime_state_id is invalid")
        )
    if runtime.get("runtime_state_id") != manifest.get("runtime_state_id"):
        errors.append(
            diagnostic(
                "E_RUNTIME_STATE_ID",
                "runtime_state_id does not match manifest",
            )
        )
    if not is_str_list(manifest.get("sources")) or not manifest.get("sources"):
        errors.append(
            diagnostic("E_MANIFEST_SOURCES", "manifest.sources must be non-empty")
        )
    if manifest.get("supersedes") is not None and not is_resolved_string(
        manifest.get("supersedes")
    ):
        errors.append(
            diagnostic("E_MANIFEST_SUPERSEDES", "manifest.supersedes is invalid")
        )
    if runtime.get("workflow_id") != manifest.get("workflow_id"):
        errors.append(
            diagnostic("E_RUNTIME_WORKFLOW", "runtime workflow_id does not match manifest")
        )
    plan_revision = dict_or_empty(manifest.get("plan")).get("revision")
    orchestration_revision = dict_or_empty(manifest.get("orchestration")).get(
        "revision"
    )
    if runtime.get("plan_revision") != plan_revision:
        errors.append(
            diagnostic("E_RUNTIME_PLAN", "runtime plan_revision does not match manifest")
        )
    if runtime.get("orchestration_revision") != orchestration_revision:
        errors.append(
            diagnostic(
                "E_RUNTIME_ORCHESTRATION",
                "runtime orchestration_revision does not match manifest",
            )
        )


def _validate_git_record(
    runtime: dict[str, Any],
    phase: str,
    errors: list[Diagnostic],
) -> dict[str, Any] | None:
    git = _require_object(
        runtime.get("git"),
        (
            "target_branch",
            "workflow_base_sha",
            "plan_sha",
            "read_only_git_authorization_basis",
        ),
        "runtime.git",
        errors,
    )
    if git is None:
        return None
    if not is_resolved_string(git["target_branch"]):
        errors.append(
            diagnostic("E_TARGET_BRANCH", "runtime.git.target_branch is invalid")
        )
    if phase != "planning":
        if not is_oid(git["workflow_base_sha"]):
            errors.append(
                diagnostic("E_BASE_SHA", "workflow_base_sha must be a full OID")
            )
        if not is_oid(git["plan_sha"]):
            errors.append(diagnostic("E_PLAN_SHA", "plan_sha must be a full OID"))
        if not is_resolved_string(git["read_only_git_authorization_basis"]):
            errors.append(
                diagnostic(
                    "E_GIT_AUTHORIZATION",
                    "read-only Git authorization basis is required",
                )
            )
    return git


def _validate_review_finding(
    value: Any,
    location: str,
    errors: list[Diagnostic],
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        errors.append(
            diagnostic("E_REVIEW_FINDING_SCHEMA", f"{location} must be an object")
        )
        return None
    missing = sorted(REVIEW_FINDING_FIELDS - set(value))
    extra = sorted(set(value) - REVIEW_FINDING_FIELDS)
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unknown {', '.join(extra)}")
        errors.append(
            diagnostic(
                "E_REVIEW_FINDING_SCHEMA",
                f"{location} has invalid fields: {'; '.join(details)}",
            )
        )
        return None
    for field in (
        "title",
        "category",
        "summary",
        "evidence",
        "trigger",
        "suggested_fix",
        "suggested_test",
        "scope_basis",
        "attribution_evidence",
    ):
        if not is_resolved_string(value[field]):
            errors.append(
                diagnostic(
                    "E_REVIEW_FINDING_SCHEMA",
                    f"{location}.{field} is invalid",
                )
            )
    if not is_safe_relative_path(value["file"]):
        errors.append(
            diagnostic(
                "E_REVIEW_FINDING_SCHEMA",
                f"{location}.file must be a safe repository-relative path",
            )
        )
    if (
        type(value["line_start"]) is not int
        or type(value["line_end"]) is not int
        or value["line_start"] < 1
        or value["line_end"] < value["line_start"]
    ):
        errors.append(
            diagnostic(
                "E_REVIEW_FINDING_SCHEMA",
                f"{location} has an invalid line range",
            )
        )
    if not is_str(value["severity"]) or value["severity"] not in SEVERITY_RANK:
        errors.append(
            diagnostic(
                "E_REVIEW_FINDING_SCHEMA",
                f"{location}.severity is invalid",
            )
        )
    if not is_str(value["confidence"]) or value["confidence"] not in {
        "high",
        "medium",
        "low",
    }:
        errors.append(
            diagnostic(
                "E_REVIEW_FINDING_SCHEMA",
                f"{location}.confidence is invalid",
            )
        )
    if (
        not is_str(value["scope_relation"])
        or value["scope_relation"] not in SCOPE_RELATIONS
    ):
        errors.append(
            diagnostic(
                "E_REVIEW_FINDING_SCHEMA",
                f"{location}.scope_relation is invalid",
            )
        )
    if (
        not is_str(value["change_relation"])
        or value["change_relation"] not in CHANGE_RELATIONS
    ):
        errors.append(
            diagnostic(
                "E_REVIEW_FINDING_SCHEMA",
                f"{location}.change_relation is invalid",
            )
        )
    if not is_str_list(value["source_lanes"]) or not value["source_lanes"]:
        errors.append(
            diagnostic(
                "E_REVIEW_FINDING_SCHEMA",
                f"{location}.source_lanes must be non-empty",
            )
        )
    if not is_str_list(value["source_candidate_ids"]):
        errors.append(
            diagnostic(
                "E_REVIEW_FINDING_SCHEMA",
                f"{location}.source_candidate_ids must be a string list",
            )
        )
    return value


def _is_blocking_finding(finding: dict[str, Any], fail_on: Any) -> bool:
    return (
        is_str(finding.get("scope_relation"))
        and finding["scope_relation"] in BLOCKING_SCOPE_RELATIONS
        and is_str(finding.get("change_relation"))
        and finding["change_relation"] in BLOCKING_CHANGE_RELATIONS
        and is_str(finding.get("severity"))
        and finding["severity"] in SEVERITY_RANK
        and is_str(fail_on)
        and fail_on in SEVERITY_RANK
        and SEVERITY_RANK[finding["severity"]] <= SEVERITY_RANK[fail_on]
    )


def _is_current_change_finding(finding: dict[str, Any]) -> bool:
    return (
        is_str(finding.get("scope_relation"))
        and finding["scope_relation"] in BLOCKING_SCOPE_RELATIONS
        and is_str(finding.get("change_relation"))
        and finding["change_relation"] in BLOCKING_CHANGE_RELATIONS
    )


def _validate_agent_execution(
    record: dict, location: str, errors: list[Diagnostic]
) -> None:
    execution = record.get("agent_execution")
    if execution is None and "agent_execution" not in record:
        if record.get("actual_model") == "mixed" or record.get("actual_reasoning_effort") == "mixed":
            errors.append(diagnostic("E_REVIEW_AGENT_EXECUTION", f"{location} mixed configuration needs per-agent evidence"))
        return

    def reject(message: str) -> None:
        errors.append(diagnostic("E_REVIEW_AGENT_EXECUTION", f"{location}: {message}"))

    if not isinstance(execution, dict):
        reject("agent_execution must be an object for completed Review")
        return
    if type(execution.get("schema_version")) is not int or execution["schema_version"] != 1:
        reject("agent_execution schema_version must be 1")
    if not is_resolved_string(execution.get("config_revision")) or not re.fullmatch(
        r"[0-9a-f]{64}", str(execution.get("config_id", ""))
    ):
        reject("agent_execution requires config_revision and config_id")
    passes = execution.get("passes")
    agents = execution.get("agents")
    if type(passes) is not int or passes < 1 or not isinstance(agents, list) or not agents:
        reject("agent_execution requires positive passes and nonempty agents")
        return
    if record.get("mode") == "native":
        lane_views = {view: [view] for view in REQUIRED_REVIEW_VIEWS}
        evidence_kind = "native_tool"
    elif record.get("mode") == "script":
        lane_views = {
            "correctness": ["correctness"],
            "state-concurrency": ["state-lifecycle"],
            "security": [],
            "reliability-performance": ["error-boundaries"],
            "contracts-tests": ["api-integration", "tests-regression"],
        }
        evidence_kind = "cli_invocation"
    else:
        reject("agent_execution mode must be native or script")
        return
    ids = set()
    coverage = set()
    models = set()
    efforts = set()
    aggregators = 0
    for agent in agents:
        if not isinstance(agent, dict):
            reject("agent entry must be an object")
            continue
        identity = agent.get("agent_id")
        if not is_resolved_string(identity) or identity in ids:
            reject("agent IDs must be resolved and unique")
        else:
            ids.add(identity)
        for field, values in (("model", models), ("effort", efforts)):
            value = agent.get(field)
            if not is_resolved_string(value) or value == "mixed":
                reject(f"agent {field} must be a concrete invocation value")
            else:
                values.add(value)
        basis = agent.get("selection_basis")
        if (
            not isinstance(basis, dict)
            or set(basis) != {"model", "effort"}
            or not all(is_resolved_string(value) for value in basis.values())
        ):
            reject("agent needs per-field selection_basis")
        if agent.get("status") != "completed" or agent.get("configuration_evidence") != evidence_kind:
            reject("agent must have completed with invocation evidence")
        lane = agent.get("lane")
        number = agent.get("pass")
        views = agent.get("views")
        if agent.get("role") == "reviewer":
            if (
                type(number) is not int or not 1 <= number <= passes
                or not is_str(lane) or lane not in lane_views
            ):
                reject("reviewer pass/lane is invalid")
                continue
            if not is_str_list(views) or sorted(views) != sorted(lane_views[lane]):
                reject("reviewer views do not match actual lane coverage")
            key = (number, lane)
            if key in coverage:
                reject("reviewer pass/lane is duplicated")
            coverage.add(key)
        elif agent.get("role") == "aggregator":
            aggregators += 1
            if type(number) is not int or number != 0 or views != ["aggregation"] or lane != "aggregation":
                reject("aggregator must use pass=0 and aggregation lane/views")
        else:
            reject("Review agent role must be reviewer or aggregator")
    if len(coverage) != passes * len(lane_views) or aggregators != 1:
        reject("each pass needs all five views and one final aggregator")
    for field, values in (("actual_model", models), ("actual_reasoning_effort", efforts)):
        summary = next(iter(values)) if len(values) == 1 else "mixed"
        if record.get(field) != summary:
            reject(f"{field} does not summarize per-agent invocation values")


def _validate_review(
    review: Any,
    *,
    location: str,
    expected_workflow: str,
    expected_subject: str,
    expected_engine: str,
    expected_plan: str,
    expected_orchestration: str,
    expected_contracts: list[str],
    expected_scope: Any,
    expected_base: Any,
    expected_head: Any,
    errors: list[Diagnostic],
    merge_base: Callable[[str, str], str] | None,
    evidence_reader: Callable[[str], bytes] | None,
    expected_accepted_risk_ids: set[str] | None = None,
) -> None:
    record = _require_object(
        review,
        (
            "status",
            "engine",
            "mode",
            "actual_model",
            "actual_reasoning_effort",
            "model_selection_basis",
            "fail_on",
            "scope_id",
            "scope_revision",
            "plan_revision",
            "orchestration_revision",
            "contract_revisions",
            "review_base_sha",
            "merge_base_sha",
            "reviewed_head_sha",
            "snapshot_valid",
            "blocking_count",
            "completed_views",
            "failed_views",
            "aggregation_mode",
            "report_ref",
            "read_only_git_authorization_basis",
        ),
        location,
        errors,
    )
    if record is None:
        return
    _validate_agent_execution(record, location, errors)
    scope = _validate_review_scope_definition(
        expected_scope,
        f"{location}.expected_scope",
        errors,
        expected_plan,
    )
    for field in (
        "mode",
        "actual_model",
        "actual_reasoning_effort",
        "model_selection_basis",
    ):
        if not is_resolved_string(record[field]):
            errors.append(
                diagnostic(
                    "E_REVIEW_EXECUTION_IDENTITY",
                    f"{location}.{field} is required",
                )
            )
    if record["engine"] != expected_engine:
        errors.append(
            diagnostic(
                "E_REVIEW_ENGINE_STALE",
                f"{location}.engine does not match the selected Review engine",
            )
        )
    if record["status"] != "passed":
        errors.append(diagnostic("E_REVIEW_NOT_PASSED", f"{location} is not passed"))
    if not is_resolved_string(record["scope_id"]) or not is_resolved_string(
        record["scope_revision"]
    ):
        errors.append(
            diagnostic("E_REVIEW_SCOPE", f"{location} scope identity is invalid")
        )
    if scope is not None:
        if record["fail_on"] != scope.get("fail_on"):
            errors.append(
                diagnostic(
                    "E_REVIEW_POLICY_STALE",
                    f"{location} fail_on does not match the frozen Review policy",
                )
            )
        if record["scope_id"] != scope.get("scope_id"):
            errors.append(
                diagnostic(
                    "E_REVIEW_SCOPE_STALE",
                    f"{location} scope_id does not match the confirmed contract",
                )
            )
        if record["scope_revision"] != scope.get("scope_revision"):
            errors.append(
                diagnostic(
                    "E_REVIEW_SCOPE_STALE",
                    f"{location} scope_revision does not match the confirmed contract",
                )
            )
    if record["plan_revision"] != expected_plan:
        errors.append(
            diagnostic("E_REVIEW_PLAN_STALE", f"{location} plan revision is stale")
        )
    if record["orchestration_revision"] != expected_orchestration:
        errors.append(
            diagnostic(
                "E_REVIEW_ORCHESTRATION_STALE",
                f"{location} orchestration revision is stale",
            )
        )
    if not is_str_list(record["contract_revisions"]) or sorted(
        record["contract_revisions"]
    ) != sorted(expected_contracts):
        errors.append(
            diagnostic(
                "E_REVIEW_CONTRACT_STALE",
                f"{location} contract revisions do not match",
            )
        )
    if record["review_base_sha"] != expected_base:
        errors.append(
            diagnostic("E_REVIEW_BASE_STALE", f"{location} review base is stale")
        )
    if record["reviewed_head_sha"] != expected_head:
        errors.append(
            diagnostic("E_REVIEW_STALE", f"{location} reviewed head is stale")
        )
    if not is_oid(record["merge_base_sha"]):
        errors.append(
            diagnostic("E_REVIEW_MERGE_BASE", f"{location} merge_base_sha is invalid")
        )
    elif merge_base is not None and is_oid(expected_base) and is_oid(expected_head):
        try:
            actual_merge_base = merge_base(expected_base, expected_head)
        except Exception as exc:
            errors.append(
                diagnostic(
                    "E_REVIEW_MERGE_BASE_PROOF",
                    f"{location} merge-base proof failed: {exc}",
                )
            )
        else:
            if record["merge_base_sha"] != actual_merge_base:
                errors.append(
                    diagnostic(
                        "E_REVIEW_MERGE_BASE_STALE",
                        f"{location} merge_base_sha does not match Git",
                    )
                )
    if record["snapshot_valid"] is not True:
        errors.append(
            diagnostic("E_REVIEW_SNAPSHOT", f"{location} snapshot is invalid")
        )
    if type(record["blocking_count"]) is not int or record["blocking_count"] != 0:
        errors.append(
            diagnostic("E_REVIEW_BLOCKERS", f"{location} has unresolved blockers")
        )
    if (
        not is_str_list(record["completed_views"])
        or len(record["completed_views"]) != len(set(record["completed_views"]))
        or set(record["completed_views"]) != REQUIRED_REVIEW_VIEWS
    ):
        errors.append(
            diagnostic(
                "E_REVIEW_VIEWS",
                f"{location} does not prove all required Review views",
            )
        )
    if not isinstance(record["failed_views"], list) or record["failed_views"]:
        errors.append(
            diagnostic("E_REVIEW_VIEWS", f"{location} has failed Review views")
        )
    if not is_str(record["aggregation_mode"]) or record[
        "aggregation_mode"
    ] not in {"quick", "deep"}:
        errors.append(
            diagnostic(
                "E_REVIEW_AGGREGATION",
                f"{location}.aggregation_mode must be quick or deep",
            )
        )
    payload = _read_evidence_json(
        record["report_ref"],
        f"{location}.report_ref",
        errors,
        evidence_reader,
    )
    if payload is not None:
        if type(payload.get("schema_version")) is not int or payload.get(
            "schema_version"
        ) != 1:
            errors.append(
                diagnostic(
                    "E_REVIEW_EVIDENCE_SCHEMA",
                    f"{location} evidence schema_version must be 1",
                )
            )
        if payload.get("workflow_id") != expected_workflow:
            errors.append(
                diagnostic(
                    "E_REVIEW_EVIDENCE_WORKFLOW",
                    f"{location} evidence workflow is stale",
                )
            )
        if payload.get("subject_id") != expected_subject:
            errors.append(
                diagnostic(
                    "E_REVIEW_EVIDENCE_SUBJECT",
                    f"{location} evidence subject is stale",
                )
            )
        evidence_fields = (
            "status",
            "engine",
            "mode",
            "actual_model",
            "actual_reasoning_effort",
            "model_selection_basis",
            "fail_on",
            "scope_id",
            "scope_revision",
            "plan_revision",
            "orchestration_revision",
            "contract_revisions",
            "review_base_sha",
            "merge_base_sha",
            "reviewed_head_sha",
            "snapshot_valid",
            "blocking_count",
            "completed_views",
            "failed_views",
            "aggregation_mode",
            "read_only_git_authorization_basis",
        )
        for field in evidence_fields:
            if payload.get(field) != record[field]:
                errors.append(
                    diagnostic(
                        "E_REVIEW_EVIDENCE_STALE",
                        f"{location} evidence field {field} does not match runtime",
                    )
                )
        if "agent_execution" in record or "agent_execution" in payload:
            if (
                payload.get("agent_execution") != record.get("agent_execution")
                or ("agent_execution" in payload) != ("agent_execution" in record)
            ):
                errors.append(
                    diagnostic("E_REVIEW_AGENT_EXECUTION_STALE", f"{location} per-agent evidence does not match runtime")
                )
        finding_groups = (
            "blockers",
            "change_responsibility_non_blocking",
            "advisories",
        )
        for field in (
            *finding_groups,
            "remaining_risks",
            "accepted_risks",
        ):
            if not isinstance(payload.get(field), list):
                errors.append(
                    diagnostic(
                        "E_REVIEW_EVIDENCE_CONTENT",
                        f"{location} evidence field {field} must be a list",
                    )
                )
        derived_blockers = 0
        seen_findings: set[str] = set()
        for group in finding_groups:
            findings = payload.get(group)
            if not isinstance(findings, list):
                continue
            for index, item in enumerate(findings):
                finding = _validate_review_finding(
                    item,
                    f"{location}.{group}[{index}]",
                    errors,
                )
                if finding is None:
                    continue
                identity = json.dumps(
                    finding,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                if identity in seen_findings:
                    errors.append(
                        diagnostic(
                            "E_REVIEW_FINDING_DUPLICATE",
                            f"{location} contains a duplicated finding",
                        )
                    )
                seen_findings.add(identity)
                blocking = _is_blocking_finding(finding, record["fail_on"])
                current_change = _is_current_change_finding(finding)
                if blocking:
                    derived_blockers += 1
                    expected_group = "blockers"
                elif current_change:
                    expected_group = "change_responsibility_non_blocking"
                else:
                    expected_group = "advisories"
                if group != expected_group:
                    errors.append(
                        diagnostic(
                            "E_REVIEW_FINDING_CLASSIFICATION",
                            f"{location}.{group}[{index}] belongs in {expected_group}",
                        )
                    )
        if record["blocking_count"] != derived_blockers:
            errors.append(
                diagnostic(
                    "E_REVIEW_BLOCKING_DERIVATION",
                    f"{location}.blocking_count does not match derived blockers",
                )
            )
        if isinstance(payload.get("remaining_risks"), list) and payload[
            "remaining_risks"
        ]:
            errors.append(
                diagnostic(
                    "E_REVIEW_REMAINING_RISKS",
                    f"{location} has unresolved remaining risks",
                )
            )
        accepted_risks = payload.get("accepted_risks")
        if isinstance(accepted_risks, list):
            accepted_ids: set[str] = set()
            for index, item in enumerate(accepted_risks):
                risk = _require_object(
                    item,
                    (
                        "id",
                        "severity",
                        "reason",
                        "impact",
                        "authorization_basis",
                        "authorized_by",
                        "plan_revision",
                        "orchestration_revision",
                        "reviewed_head_sha",
                        "review_condition",
                        "follow_up_owner",
                    ),
                    f"{location}.accepted_risks[{index}]",
                    errors,
                )
                if risk is None:
                    continue
                if is_resolved_string(risk["id"]):
                    accepted_ids.add(risk["id"])
                if (
                    not is_resolved_string(risk["id"])
                    or not is_str(risk["severity"])
                    or risk["severity"] not in {"P1", "P2", "P3"}
                    or not is_resolved_string(risk["reason"])
                    or not is_resolved_string(risk["impact"])
                    or not is_resolved_string(risk["authorization_basis"])
                    or not is_resolved_string(risk["authorized_by"])
                    or risk["plan_revision"] != expected_plan
                    or risk["orchestration_revision"] != expected_orchestration
                    or risk["reviewed_head_sha"] != expected_head
                    or not is_resolved_string(risk["review_condition"])
                    or not is_resolved_string(risk["follow_up_owner"])
                ):
                    errors.append(
                        diagnostic(
                            "E_REVIEW_ACCEPTED_RISK",
                            f"{location}.accepted_risks[{index}] is invalid or stale",
                        )
                    )
            if (
                expected_accepted_risk_ids is not None
                and not expected_accepted_risk_ids.issubset(accepted_ids)
            ):
                errors.append(
                    diagnostic(
                        "E_TASK_RISK_DISPOSITION",
                        f"{location} omits accepted task risks",
                    )
                )
    if not is_resolved_string(record["read_only_git_authorization_basis"]):
        errors.append(
            diagnostic(
                "E_REVIEW_GIT_AUTHORIZATION",
                f"{location} lacks read-only Git authorization basis",
            )
        )


def _validate_commit_authorizations(
    subject_id: str,
    identity_field: str,
    definition: dict[str, Any],
    state: dict[str, Any],
    orchestration_revision: Any,
    errors: list[Diagnostic],
    list_commits: Callable[[str, str], list[str]] | None,
    changed_paths: Callable[[str], list[str]] | None,
) -> None:
    authorizations = state.get("commit_authorizations")
    commits = state.get("commits")
    if not isinstance(authorizations, list):
        errors.append(
            diagnostic(
                "E_COMMIT_AUTHORIZATIONS",
                f"{subject_id}.commit_authorizations must be a list",
            )
        )
        authorizations = []

    authorized_commits: list[str] = []
    for index, item in enumerate(authorizations):
        record = _require_object(
            item,
            (
                "commit_sha",
                identity_field,
                "orchestration_revision",
                "worktree",
                "diff_scope",
                "authorization_basis",
            ),
            f"{subject_id}.commit_authorizations[{index}]",
            errors,
        )
        if record is None:
            continue
        commit_sha = record["commit_sha"]
        if not is_oid(commit_sha):
            errors.append(
                diagnostic(
                    "E_COMMIT_AUTHORIZATION_SHA",
                    f"{subject_id}.commit_authorizations[{index}].commit_sha is invalid",
                )
            )
        else:
            authorized_commits.append(commit_sha)
        if record[identity_field] != subject_id:
            errors.append(
                diagnostic(
                    "E_COMMIT_AUTHORIZATION_TASK",
                    f"{subject_id}.commit_authorizations[{index}] is bound to another subject",
                )
            )
        if record["orchestration_revision"] != orchestration_revision:
            errors.append(
                diagnostic(
                    "E_COMMIT_AUTHORIZATION_REVISION",
                    f"{subject_id}.commit_authorizations[{index}] uses a stale orchestration revision",
                )
            )
        if record["worktree"] != definition.get("worktree"):
            errors.append(
                diagnostic(
                    "E_COMMIT_AUTHORIZATION_WORKTREE",
                    f"{subject_id}.commit_authorizations[{index}] is bound to another worktree",
                )
            )
        scope_valid = (
            is_str_list(record["diff_scope"])
            and bool(record["diff_scope"])
            and all(is_safe_relative_path(pattern) for pattern in record["diff_scope"])
        )
        if not scope_valid:
            errors.append(
                diagnostic(
                    "E_COMMIT_AUTHORIZATION_SCOPE",
                    f"{subject_id}.commit_authorizations[{index}].diff_scope must be non-empty",
                )
            )
        allowed_paths = definition.get("allowed_paths")
        if (
            scope_valid
            and (
                not is_str_list(allowed_paths)
                or any(pattern not in allowed_paths for pattern in record["diff_scope"])
            )
        ):
            errors.append(
                diagnostic(
                    "E_COMMIT_AUTHORIZATION_PLAN_SCOPE",
                    f"{subject_id}.commit_authorizations[{index}] exceeds immutable allowed_paths",
                )
            )
        if not is_resolved_string(record["authorization_basis"]):
            errors.append(
                diagnostic(
                    "E_COMMIT_AUTHORIZATION_BASIS",
                    f"{subject_id}.commit_authorizations[{index}] lacks authorization basis",
                )
            )
        if (
            changed_paths is not None
            and is_oid(commit_sha)
            and scope_valid
        ):
            try:
                actual_paths = changed_paths(commit_sha)
            except Exception as exc:
                errors.append(
                    diagnostic(
                        "E_COMMIT_PATH_PROOF",
                        f"{subject_id} commit {commit_sha} path proof failed: {exc}",
                    )
                )
            else:
                if not isinstance(actual_paths, list) or any(
                    not is_safe_relative_path(path) for path in actual_paths
                ):
                    errors.append(
                        diagnostic(
                            "E_COMMIT_PATH_PROOF",
                            f"{subject_id} commit {commit_sha} returned invalid paths",
                        )
                    )
                else:
                    uncovered = [
                        path
                        for path in actual_paths
                        if not any(
                            path == pattern
                            or (
                                pattern.endswith("/")
                                and path.startswith(pattern)
                            )
                            or (
                                any(character in pattern for character in "*?[")
                                and fnmatchcase(path, pattern)
                            )
                            for pattern in record["diff_scope"]
                        )
                    ]
                    if uncovered:
                        errors.append(
                            diagnostic(
                                "E_COMMIT_AUTHORIZATION_PATHS",
                                f"{subject_id} commit {commit_sha} has unauthorized paths: {', '.join(uncovered)}",
                            )
                        )

    if (
        isinstance(commits, list)
        and commits
        and all(is_oid(commit) for commit in commits)
        and authorized_commits != commits
    ):
        errors.append(
            diagnostic(
                "E_COMMIT_AUTHORIZATION_COVERAGE",
                f"{subject_id}.commit_authorizations must cover every declared commit in order",
            )
        )
    start_sha = state.get("start_sha")
    head_sha = state.get("head_sha")
    if list_commits is not None and is_oid(start_sha) and is_oid(head_sha):
        try:
            actual_commits = list_commits(start_sha, head_sha)
        except Exception as exc:
            errors.append(
                diagnostic(
                    "E_COMMIT_HISTORY_PROOF",
                    f"{subject_id} commit history proof failed: {exc}",
                )
            )
        else:
            if (
                not isinstance(actual_commits, list)
                or any(not is_oid(commit) for commit in actual_commits)
                or actual_commits != commits
            ):
                errors.append(
                    diagnostic(
                        "E_COMMIT_HISTORY_STALE",
                        f"{subject_id}.commits does not match the real first-parent history",
                    )
                )


def _validate_runtime_tasks(
    manifest: dict[str, Any],
    runtime: dict[str, Any],
    phase: str,
    selected_task: str | None,
    errors: list[Diagnostic],
    is_ancestor: Callable[[str, str], bool] | None,
    merge_base: Callable[[str, str], str] | None,
    object_exists: Callable[[str, str], bool] | None,
    read_object: Callable[[str, str], bytes] | None,
    evidence_reader: Callable[[str], bytes] | None,
    list_commits: Callable[[str, str], list[str]] | None,
    changed_paths: Callable[[str], list[str]] | None,
) -> dict[str, dict[str, Any]]:
    manifest_tasks = {
        item["id"]: item
        for item in list_or_empty(manifest.get("tasks"))
        if isinstance(item, dict) and is_resolved_string(item.get("id"))
    }
    runtime_tasks = runtime.get("tasks")
    if not isinstance(runtime_tasks, dict):
        errors.append(diagnostic("E_RUNTIME_TASKS", "runtime.tasks must be an object"))
        return {}
    if set(runtime_tasks) != set(manifest_tasks):
        errors.append(
            diagnostic(
                "E_RUNTIME_TASK_MISMATCH",
                "runtime task IDs must exactly match manifest task IDs",
            )
        )

    plan_revision = dict_or_empty(manifest.get("plan")).get("revision")
    orchestration_revision = dict_or_empty(manifest.get("orchestration")).get(
        "revision"
    )
    validated: dict[str, dict[str, Any]] = {}
    for task_id, definition in manifest_tasks.items():
        state = _require_object(
            runtime_tasks.get(task_id),
            (
                "status",
                "start_sha",
                "start_basis",
                "dependency_evidence",
                "head_sha",
                "clean",
                "commits",
                "implementation_write_authorization_basis",
                "commit_authorizations",
                "tests",
                "risks",
                "report_ref",
                "review",
            ),
            f"runtime.tasks.{task_id}",
            errors,
        )
        if state is None:
            continue
        validated[task_id] = state
        if not is_str(state["status"]) or state["status"] not in TASK_STATUSES:
            errors.append(
                diagnostic("E_TASK_STATUS", f"{task_id}.status is invalid")
            )
        if not isinstance(state["commit_authorizations"], list):
            errors.append(
                diagnostic(
                    "E_COMMIT_AUTHORIZATIONS",
                    f"{task_id}.commit_authorizations must be a list",
                )
            )

        if (
            phase == "dispatch"
            and is_str(state["status"])
            and state["status"] in {"ready", "running"}
        ):
            if not is_oid(state["start_sha"]):
                errors.append(
                    diagnostic("E_TASK_START_SHA", f"{task_id}.start_sha is invalid")
                )
            if not is_resolved_string(state["implementation_write_authorization_basis"]):
                errors.append(
                    diagnostic(
                        "E_IMPLEMENTATION_AUTHORIZATION",
                        f"{task_id} lacks implementation write authorization",
                    )
                )

        require_ready = (
            phase == "branch-ready" and task_id == selected_task
        ) or phase in {"integration-ready", "merge-ready"}
        if not require_ready:
            continue
        if phase == "merge-ready":
            allowed_statuses = {"integrated"}
        else:
            allowed_statuses = {"reviewed", "integrated"}
        if not is_str(state["status"]) or state["status"] not in allowed_statuses:
            errors.append(
                diagnostic(
                    "E_TASK_PHASE_STATUS",
                    f"{task_id}.status must be one of {sorted(allowed_statuses)}",
                )
            )
        if not is_oid(state["start_sha"]):
            errors.append(
                diagnostic("E_TASK_START_SHA", f"{task_id}.start_sha is invalid")
            )
        if not is_oid(state["head_sha"]):
            errors.append(
                diagnostic("E_TASK_HEAD_SHA", f"{task_id}.head_sha is invalid")
            )
        if state["clean"] is not True:
            errors.append(diagnostic("E_TASK_DIRTY", f"{task_id} is not clean"))
        if (
            not isinstance(state["commits"], list)
            or not state["commits"]
            or any(not is_oid(commit) for commit in state["commits"])
            or state["commits"][-1] != state["head_sha"]
        ):
            errors.append(
                diagnostic(
                    "E_TASK_COMMITS",
                    f"{task_id}.commits must end at task head",
                )
            )
        if not is_resolved_string(state["implementation_write_authorization_basis"]):
            errors.append(
                diagnostic(
                    "E_IMPLEMENTATION_AUTHORIZATION",
                    f"{task_id} lacks implementation write authorization",
                )
            )
        _validate_commit_authorizations(
            task_id,
            "task_id",
            definition,
            state,
            orchestration_revision,
            errors,
            list_commits,
            changed_paths,
        )
        if is_ancestor is not None and is_oid(state["start_sha"]) and is_oid(
            state["head_sha"]
        ):
            if not is_ancestor(state["start_sha"], state["head_sha"]):
                errors.append(
                    diagnostic(
                        "E_TASK_ANCESTRY",
                        f"{task_id} start is not an ancestor of head",
                    )
                )
        _validate_task_report(
            manifest,
            task_id,
            definition,
            state,
            errors,
            read_object,
        )
        accepted_risk_ids = _validate_task_risks(
            state["risks"],
            task_id=task_id,
            plan_revision=plan_revision,
            orchestration_revision=orchestration_revision,
            head_sha=state["head_sha"],
            errors=errors,
            evidence_reader=evidence_reader,
        )
        tests = _require_object(
            state["tests"],
            ("status", "tested_head_sha", "evidence_ref"),
            f"{task_id}.tests",
            errors,
        )
        if tests is not None:
            if tests["status"] != "passed":
                errors.append(
                    diagnostic(
                        "E_TASK_TESTS",
                        f"{task_id}.tests is not passed",
                    )
                )
            if tests["tested_head_sha"] != state["head_sha"]:
                errors.append(
                    diagnostic(
                        "E_TASK_TESTS_STALE",
                        f"{task_id}.tests is not bound to the current task HEAD",
                    )
                )
            _validate_test_evidence(
                tests["evidence_ref"],
                location=f"{task_id}.tests.evidence_ref",
                expected_workflow=manifest.get("workflow_id"),
                expected_plan=plan_revision,
                expected_orchestration=orchestration_revision,
                expected_subject=f"task:{task_id}",
                expected_head=state["head_sha"],
                expected_tests=_validate_test_requirements(
                    definition.get("required_tests"),
                    f"{task_id}.required_tests",
                    errors,
                ),
                errors=errors,
                evidence_reader=evidence_reader,
            )
        _validate_review(
            state["review"],
            location=f"{task_id}.review",
            expected_workflow=manifest.get("workflow_id"),
            expected_subject=task_id,
            expected_engine=dict_or_empty(
                manifest.get("review_engine")
            ).get("selected_engine"),
            expected_plan=plan_revision,
            expected_orchestration=orchestration_revision,
            expected_contracts=list_or_empty(definition.get("contracts")),
            expected_scope=definition["review_scope"],
            expected_base=state["start_sha"],
            expected_head=state["head_sha"],
            errors=errors,
            merge_base=merge_base,
            evidence_reader=evidence_reader,
            expected_accepted_risk_ids=accepted_risk_ids,
        )
    return validated


def _task_validation_targets(
    manifest_tasks: dict[str, dict[str, Any]],
    task_states: dict[str, dict[str, Any]],
    phase: str,
    selected_task: str | None,
) -> set[str]:
    if phase in {"integration-ready", "merge-ready"}:
        return set(manifest_tasks)
    if phase == "dispatch":
        active = {
            "ready",
            "running",
            "implemented",
            "review_incomplete",
            "review_blocked",
            "reviewed",
            "integrated",
        }
        return {
            task_id
            for task_id, state in task_states.items()
            if is_str(state.get("status")) and state["status"] in active
        }
    if phase != "branch-ready" or selected_task not in manifest_tasks:
        return set()

    targets: set[str] = set()
    pending = [selected_task]
    while pending:
        task_id = pending.pop()
        if task_id in targets:
            continue
        targets.add(task_id)
        dependencies = manifest_tasks[task_id].get("dependencies")
        if isinstance(dependencies, list):
            pending.extend(
                item["task_id"]
                for item in dependencies
                if isinstance(item, dict)
                and is_resolved_string(item.get("task_id"))
                and item["task_id"] in manifest_tasks
            )
    return targets


def _validate_task_starts_and_dependencies(
    manifest: dict[str, Any],
    runtime: dict[str, Any],
    task_states: dict[str, dict[str, Any]],
    phase: str,
    selected_task: str | None,
    errors: list[Diagnostic],
    is_ancestor: Callable[[str, str], bool] | None,
    merge_base: Callable[[str, str], str] | None,
    object_exists: Callable[[str, str], bool] | None,
    read_object: Callable[[str, str], bytes] | None,
    evidence_reader: Callable[[str], bytes] | None,
) -> None:
    if phase == "planning":
        return
    manifest_tasks = {
        item["id"]: item
        for item in list_or_empty(manifest.get("tasks"))
        if isinstance(item, dict) and is_resolved_string(item.get("id"))
    }
    targets = _task_validation_targets(
        manifest_tasks,
        task_states,
        phase,
        selected_task,
    )
    plan_revision = dict_or_empty(manifest.get("plan")).get("revision")
    orchestration_revision = dict_or_empty(manifest.get("orchestration")).get(
        "revision"
    )
    plan_sha = dict_or_empty(runtime.get("git")).get("plan_sha")
    contracts = {
        f"{item['id']}@{item['revision']}": item
        for item in list_or_empty(manifest.get("contracts"))
        if isinstance(item, dict)
        and is_resolved_string(item.get("id"))
        and is_resolved_string(item.get("revision"))
    }
    integration = runtime.get("integration")
    checkpoints = []
    if isinstance(integration, dict) and isinstance(
        integration.get("merged_tasks"), list
    ):
        checkpoints = [
            item for item in integration["merged_tasks"] if isinstance(item, dict)
        ]

    for task_id in sorted(targets):
        definition = manifest_tasks.get(task_id)
        state = task_states.get(task_id)
        if definition is None or state is None:
            continue
        basis = _require_object(
            state.get("start_basis"),
            (
                "kind",
                "plan_revision",
                "orchestration_revision",
                "contract_revisions",
                "source_task_ids",
            ),
            f"{task_id}.start_basis",
            errors,
        )
        evidence_map = state.get("dependency_evidence")
        if not isinstance(evidence_map, dict):
            errors.append(
                diagnostic(
                    "E_DEPENDENCY_EVIDENCE_TYPE",
                    f"{task_id}.dependency_evidence must be an object",
                )
            )
            evidence_map = {}
        dependencies = definition.get("dependencies")
        if not isinstance(dependencies, list):
            continue
        expected_evidence_ids = {
            item["id"]
            for item in dependencies
            if isinstance(item, dict) and is_resolved_string(item.get("id"))
        }
        if set(evidence_map) != expected_evidence_ids:
            errors.append(
                diagnostic(
                    "E_DEPENDENCY_EVIDENCE_SET",
                    f"{task_id}.dependency_evidence must exactly match dependency IDs",
                )
            )
        if basis is not None:
            if basis["plan_revision"] != plan_revision:
                errors.append(
                    diagnostic(
                        "E_TASK_START_BASIS_PLAN_STALE",
                        f"{task_id}.start_basis uses a stale plan revision",
                    )
                )
            if basis["orchestration_revision"] != orchestration_revision:
                errors.append(
                    diagnostic(
                        "E_TASK_START_BASIS_ORCHESTRATION_STALE",
                        f"{task_id}.start_basis uses a stale orchestration revision",
                    )
                )
            if (
                not is_str_list(basis["contract_revisions"])
                or sorted(basis["contract_revisions"])
                != sorted(list_or_empty(definition.get("contracts")))
            ):
                errors.append(
                    diagnostic(
                        "E_TASK_START_BASIS_CONTRACT_STALE",
                        f"{task_id}.start_basis contract revisions are stale",
                    )
                )
            if not is_str_list(basis["source_task_ids"]):
                errors.append(
                    diagnostic(
                        "E_TASK_START_BASIS_SOURCES",
                        f"{task_id}.start_basis.source_task_ids is invalid",
                    )
                )

        start_sha = state.get("start_sha")
        if (
            is_ancestor is not None
            and is_oid(plan_sha)
            and is_oid(start_sha)
            and not is_ancestor(plan_sha, start_sha)
        ):
            errors.append(
                diagnostic(
                    "E_TASK_START_PLAN_ANCESTRY",
                    f"PLAN_SHA is not an ancestor of {task_id}.start_sha",
                )
            )

        material_sources: dict[str, str] = {}
        has_integration_dependency = False
        for index, dependency in enumerate(dependencies):
            if not isinstance(dependency, dict):
                continue
            dependency_id = dependency.get("id")
            upstream_id = dependency.get("task_id")
            kind = dependency.get("type")
            if not is_resolved_string(dependency_id) or not is_resolved_string(
                upstream_id
            ):
                continue
            record = _require_object(
                evidence_map.get(dependency_id),
                ("status", "source_sha", "evidence_ref"),
                f"{task_id}.dependency_evidence.{dependency_id}",
                errors,
            )
            if record is None:
                continue
            if record["status"] != "satisfied":
                errors.append(
                    diagnostic(
                        "E_DEPENDENCY_EVIDENCE_STALE",
                        f"{dependency_id} is not satisfied",
                    )
                )
            upstream = task_states.get(upstream_id)
            upstream_definition = manifest_tasks.get(upstream_id)
            expected = dependency.get("expected_evidence")
            if not isinstance(expected, dict):
                expected = {}

            if kind == "contract":
                if record["source_sha"] != plan_sha or record["evidence_ref"] is not None:
                    errors.append(
                        diagnostic(
                            "E_DEPENDENCY_EVIDENCE_STALE",
                            f"{dependency_id} must be bound directly to PLAN_SHA",
                        )
                    )
                revisions = expected.get("contract_revisions")
                if is_str_list(revisions):
                    for revision in revisions:
                        contract = contracts.get(revision)
                        consumers = (
                            contract.get("consumers", [])
                            if isinstance(contract, dict)
                            else []
                        )
                        if (
                            contract is None
                            or contract.get("producer") != upstream_id
                            or task_id not in consumers
                            or contract.get("status") != "confirmed"
                        ):
                            errors.append(
                                diagnostic(
                                    "E_DEPENDENCY_CONTRACT_STALE",
                                    f"{dependency_id} does not resolve to a confirmed producer/consumer contract",
                                )
                            )
            elif is_str(kind) and kind in {"code", "artifact", "build", "test"}:
                upstream_head = upstream.get("head_sha") if upstream else None
                upstream_review = upstream.get("review") if upstream else None
                if (
                    upstream is None
                    or upstream_definition is None
                    or not is_str(upstream.get("status"))
                    or upstream.get("status") not in {"reviewed", "integrated"}
                    or not isinstance(upstream_review, dict)
                    or upstream_review.get("status") != "passed"
                    or upstream_review.get("reviewed_head_sha") != upstream_head
                    or upstream_review.get("plan_revision") != plan_revision
                    or upstream_review.get("orchestration_revision")
                    != orchestration_revision
                    or upstream_review.get("scope_id")
                    != dict_or_empty(upstream_definition.get("review_scope")).get(
                        "scope_id"
                    )
                ):
                    errors.append(
                        diagnostic(
                            "E_UPSTREAM_REVIEW_STALE",
                            f"{dependency_id} upstream Review is not current",
                        )
                    )
                if (
                    upstream is not None
                    and upstream_definition is not None
                    and isinstance(upstream_review, dict)
                ):
                    _validate_review(
                        upstream_review,
                        location=f"{task_id}.{dependency_id}.upstream_review",
                        expected_workflow=manifest.get("workflow_id"),
                        expected_subject=upstream_id,
                        expected_engine=dict_or_empty(
                            manifest.get("review_engine")
                        ).get("selected_engine"),
                        expected_plan=plan_revision,
                        expected_orchestration=orchestration_revision,
                        expected_contracts=list_or_empty(
                            upstream_definition.get("contracts")
                        ),
                        expected_scope=upstream_definition.get("review_scope"),
                        expected_base=upstream.get("start_sha"),
                        expected_head=upstream_head,
                        errors=errors,
                        merge_base=merge_base,
                        evidence_reader=evidence_reader,
                    )
                if record["source_sha"] != upstream_head:
                    errors.append(
                        diagnostic(
                            "E_DEPENDENCY_EVIDENCE_STALE",
                            f"{dependency_id}.source_sha does not match upstream reviewed HEAD",
                        )
                    )
                if kind in {"code", "artifact"} and is_oid(upstream_head):
                    material_sources[upstream_id] = upstream_head
                if kind == "code" and record["evidence_ref"] is not None:
                    errors.append(
                        diagnostic(
                            "E_DEPENDENCY_EVIDENCE_STALE",
                            f"{dependency_id}.evidence_ref must be null for code dependency",
                        )
                    )
                if kind == "artifact":
                    raw_paths = expected.get("artifact_paths")
                    paths = (
                        [
                            path
                            for path in raw_paths
                            if is_safe_relative_path(path)
                        ]
                        if isinstance(raw_paths, list)
                        else []
                    )
                    if (
                        object_exists is not None
                        and is_oid(upstream_head)
                    ):
                        for path in paths:
                            if is_safe_relative_path(path) and not object_exists(
                                upstream_head, path
                            ):
                                errors.append(
                                    diagnostic(
                                        "E_DEPENDENCY_ARTIFACT_MISSING",
                                        f"{dependency_id} artifact {path} is missing",
                                    )
                                )
                    _validate_artifact_evidence(
                        record["evidence_ref"],
                        location=(
                            f"{task_id}.dependency_evidence.{dependency_id}.evidence_ref"
                        ),
                        expected_workflow=manifest.get("workflow_id"),
                        expected_plan=plan_revision,
                        expected_orchestration=orchestration_revision,
                        expected_subject=dependency_id,
                        expected_head=upstream_head,
                        expected_paths=paths,
                        errors=errors,
                        evidence_reader=evidence_reader,
                        read_object=read_object,
                    )
                if kind in {"build", "test"}:
                    _validate_test_evidence(
                        record["evidence_ref"],
                        location=(
                            f"{task_id}.dependency_evidence.{dependency_id}.evidence_ref"
                        ),
                        expected_workflow=manifest.get("workflow_id"),
                        expected_plan=plan_revision,
                        expected_orchestration=orchestration_revision,
                        expected_subject=expected.get("evidence_subject"),
                        expected_head=upstream_head,
                        expected_tests=_validate_test_requirements(
                            expected.get("required_tests"),
                            f"{dependency_id}.required_tests",
                            errors,
                        ),
                        errors=errors,
                        evidence_reader=evidence_reader,
                    )
                if kind in {"artifact", "build", "test"} and record[
                    "evidence_ref"
                ] is None:
                    errors.append(
                        diagnostic(
                            "E_DEPENDENCY_EVIDENCE_MISSING",
                            f"{dependency_id}.evidence_ref is required",
                        )
                    )
            elif kind == "integration":
                has_integration_dependency = True
                checkpoint_sha = record["source_sha"]
                upstream_head = upstream.get("head_sha") if upstream else None
                upstream_review = upstream.get("review") if upstream else None
                if (
                    upstream is None
                    or upstream_definition is None
                    or upstream.get("status") not in {"reviewed", "integrated"}
                    or not isinstance(upstream_review, dict)
                    or upstream_review.get("status") != "passed"
                    or upstream_review.get("reviewed_head_sha") != upstream_head
                    or upstream_review.get("plan_revision") != plan_revision
                    or upstream_review.get("orchestration_revision")
                    != orchestration_revision
                ):
                    errors.append(
                        diagnostic(
                            "E_UPSTREAM_REVIEW_STALE",
                            f"{dependency_id} upstream Review is not current",
                        )
                    )
                elif upstream_definition is not None:
                    _validate_review(
                        upstream_review,
                        location=f"{task_id}.{dependency_id}.upstream_review",
                        expected_workflow=manifest.get("workflow_id"),
                        expected_subject=upstream_id,
                        expected_engine=dict_or_empty(
                            manifest.get("review_engine")
                        ).get("selected_engine"),
                        expected_plan=plan_revision,
                        expected_orchestration=orchestration_revision,
                        expected_contracts=list_or_empty(
                            upstream_definition.get("contracts")
                        ),
                        expected_scope=upstream_definition.get("review_scope"),
                        expected_base=upstream.get("start_sha"),
                        expected_head=upstream_head,
                        errors=errors,
                        merge_base=merge_base,
                        evidence_reader=evidence_reader,
                    )
                matches = [
                    item
                    for item in checkpoints
                    if item.get("integration_head_after") == checkpoint_sha
                ]
                matched = matches[0] if len(matches) == 1 else None
                if len(matches) != 1:
                    errors.append(
                        diagnostic(
                            "E_CHECKPOINT_IDENTITY",
                            f"{dependency_id} must resolve to exactly one integration checkpoint",
                        )
                    )
                elif matched.get("task_id") != upstream_id:
                    errors.append(
                        diagnostic(
                            "E_CHECKPOINT_SUBJECT",
                            f"{dependency_id} checkpoint belongs to {matched.get('task_id')!r}, not {upstream_id!r}",
                        )
                    )
                if (
                    not isinstance(matched, dict)
                    or matched.get("reviewed_head_sha") != upstream_head
                ):
                    errors.append(
                        diagnostic(
                            "E_CHECKPOINT_SOURCE_STALE",
                            f"{dependency_id} checkpoint is not bound to the current reviewed upstream HEAD",
                        )
                    )
                if (
                    is_ancestor is not None
                    and is_oid(upstream_head)
                    and is_oid(checkpoint_sha)
                    and not is_ancestor(upstream_head, checkpoint_sha)
                ):
                    errors.append(
                        diagnostic(
                            "E_CHECKPOINT_ANCESTRY",
                            f"{dependency_id} checkpoint does not contain {upstream_id}",
                        )
                    )
                verification = (
                    matched.get("verification") if isinstance(matched, dict) else None
                )
                if (
                    not isinstance(verification, dict)
                    or verification.get("status") != "passed"
                    or verification.get("tested_head_sha") != checkpoint_sha
                ):
                    errors.append(
                        diagnostic(
                            "E_CHECKPOINT_UNVERIFIED",
                            f"{dependency_id} does not reference a verified integration checkpoint",
                        )
                    )
                elif isinstance(verification, dict):
                    _validate_test_evidence(
                        verification.get("evidence_ref"),
                        location=f"{task_id}.{dependency_id}.checkpoint_evidence",
                        expected_workflow=manifest.get("workflow_id"),
                        expected_plan=plan_revision,
                        expected_orchestration=orchestration_revision,
                        expected_subject=f"checkpoint:{upstream_id}",
                        expected_head=checkpoint_sha,
                        expected_tests=_validate_test_requirements(
                            dict_or_empty(
                                dict_or_empty(
                                    manifest.get("integration")
                                ).get("checkpoint_tests")
                            ).get(upstream_id),
                            (
                                "integration.checkpoint_tests."
                                f"{upstream_id}"
                            ),
                            errors,
                        ),
                        errors=errors,
                        evidence_reader=evidence_reader,
                    )
                if is_oid(checkpoint_sha):
                    material_sources[upstream_id] = checkpoint_sha

        source_ids = sorted(material_sources)
        if basis is None:
            continue
        basis_source_ids = (
            basis["source_task_ids"]
            if is_str_list(basis["source_task_ids"])
            else []
        )
        if sorted(basis_source_ids) != source_ids:
            errors.append(
                diagnostic(
                    "E_TASK_START_BASIS_SOURCES",
                    f"{task_id}.start_basis source tasks do not match material dependencies",
                )
            )
        if not source_ids:
            expected_kind = "plan_sha"
            expected_start = plan_sha
        elif len(source_ids) == 1 and not has_integration_dependency:
            expected_kind = "reviewed_head"
            expected_start = material_sources[source_ids[0]]
        else:
            expected_kind = "integration_checkpoint"
            expected_start = start_sha
            checkpoint = next(
                (
                    item
                    for item in checkpoints
                    if item.get("integration_head_after") == start_sha
                ),
                None,
            )
            verification = (
                checkpoint.get("verification")
                if isinstance(checkpoint, dict)
                else None
            )
            if (
                not isinstance(verification, dict)
                or verification.get("status") != "passed"
                or verification.get("tested_head_sha") != start_sha
            ):
                errors.append(
                    diagnostic(
                        "E_CHECKPOINT_UNVERIFIED",
                        f"{task_id}.start_sha is not a verified integration checkpoint",
                    )
                )
            if is_ancestor is not None and is_oid(start_sha):
                for source_id, source_sha in material_sources.items():
                    if is_oid(source_sha) and not is_ancestor(source_sha, start_sha):
                        errors.append(
                            diagnostic(
                                "E_CHECKPOINT_ANCESTRY",
                                f"{task_id} checkpoint does not contain {source_id}",
                            )
                        )
        if basis["kind"] != expected_kind or start_sha != expected_start:
            errors.append(
                diagnostic(
                    "E_TASK_START_ANCHOR",
                    f"{task_id}.start_sha is not anchored to {expected_kind}",
                )
            )


def _validate_integration(
    manifest: dict[str, Any],
    runtime: dict[str, Any],
    task_states: dict[str, dict[str, Any]],
    phase: str,
    errors: list[Diagnostic],
    is_ancestor: Callable[[str, str], bool] | None,
    merge_base: Callable[[str, str], str] | None,
    evidence_reader: Callable[[str], bytes] | None,
    list_commits: Callable[[str, str], list[str]] | None,
    changed_paths: Callable[[str], list[str]] | None,
) -> None:
    definition = _require_object(
        manifest.get("integration"),
        (
            "branch",
            "worktree",
            "sources",
            "supersedes",
            "required_tests",
            "checkpoint_tests",
            "allowed_paths",
            "merge_order",
            "review_scope",
        ),
        "integration",
        errors,
    )
    state = _require_object(
        runtime.get("integration"),
        (
            "status",
            "start_sha",
            "head_sha",
            "clean",
            "write_authorization_basis",
            "commits",
            "commit_authorizations",
            "merged_tasks",
            "tests",
            "final_review",
            "execution_log_ref",
        ),
        "runtime.integration",
        errors,
    )
    integration_required_tests: list[dict[str, Any]] = []
    checkpoint_requirements: dict[str, list[dict[str, Any]]] = {}
    plan_acceptance_ids = set(_plan_acceptance_criteria_map(manifest))
    task_definitions = {
        item["id"]: item
        for item in list_or_empty(manifest.get("tasks"))
        if isinstance(item, dict) and is_resolved_string(item.get("id"))
    }
    if definition is not None:
        if not is_resolved_string(definition["branch"]):
            errors.append(
                diagnostic("E_INTEGRATION_BRANCH", "integration.branch is invalid")
            )
        if not is_resolved_string(definition["worktree"]):
            errors.append(
                diagnostic("E_INTEGRATION_WORKTREE", "integration.worktree is invalid")
            )
        if not is_str_list(definition["sources"]) or not definition["sources"]:
            errors.append(
                diagnostic(
                    "E_INTEGRATION_SOURCES",
                    "integration.sources must be non-empty",
                )
            )
        if definition["supersedes"] is not None and not is_resolved_string(
            definition["supersedes"]
        ):
            errors.append(
                diagnostic(
                    "E_INTEGRATION_SUPERSEDES",
                    "integration.supersedes is invalid",
                )
            )
        integration_review_scope = _validate_review_scope_definition(
            definition["review_scope"],
            "integration.review_scope",
            errors,
            dict_or_empty(manifest.get("plan")).get("revision"),
        )
        if integration_review_scope is not None and (
            len(_review_scope_acceptance_criteria(integration_review_scope))
            != len(plan_acceptance_ids)
            or set(_review_scope_acceptance_criteria(integration_review_scope))
            != plan_acceptance_ids
        ):
            errors.append(
                diagnostic(
                    "E_INTEGRATION_REVIEW_AC_COVERAGE",
                    "integration.review_scope must exactly cover all plan acceptance criteria",
                )
            )
        integration_required_tests = _validate_test_requirements(
            definition["required_tests"],
            "integration.required_tests",
            errors,
            allowed_acceptance_criteria=plan_acceptance_ids,
        )
        if (
            not is_str_list(definition["allowed_paths"])
            or not definition["allowed_paths"]
            or any(
                not is_safe_relative_path(path)
                for path in list_or_empty(definition["allowed_paths"])
            )
        ):
            errors.append(
                diagnostic(
                    "E_INTEGRATION_ALLOWED_PATHS",
                    "integration.allowed_paths must be a non-empty safe path/pattern list",
                )
            )
        checkpoint_tests = definition["checkpoint_tests"]
        manifest_task_ids = {
            item["id"]
            for item in list_or_empty(manifest.get("tasks"))
            if isinstance(item, dict) and is_resolved_string(item.get("id"))
        }
        merge_order = definition["merge_order"]
        if (
            not is_str_list(merge_order)
            or len(merge_order) != len(set(merge_order))
            or set(merge_order) != manifest_task_ids
        ):
            errors.append(
                diagnostic(
                    "E_INTEGRATION_MERGE_ORDER",
                    "integration.merge_order must exactly order every task once",
                )
            )
        else:
            positions = {
                task_id: index for index, task_id in enumerate(merge_order)
            }
            for task in list_or_empty(manifest.get("tasks")):
                if not isinstance(task, dict):
                    continue
                task_id = task.get("id")
                for dependency in list_or_empty(task.get("dependencies")):
                    if (
                        isinstance(dependency, dict)
                        and dependency.get("task_id") in positions
                        and task_id in positions
                        and positions[dependency["task_id"]] >= positions[task_id]
                    ):
                        errors.append(
                            diagnostic(
                                "E_INTEGRATION_MERGE_ORDER",
                                f"integration.merge_order places {task_id} before its dependency",
                            )
                        )
        if not isinstance(checkpoint_tests, dict):
            errors.append(
                diagnostic(
                    "E_CHECKPOINT_TEST_REQUIREMENTS",
                    "integration.checkpoint_tests must be an object",
                )
            )
        else:
            if set(checkpoint_tests) != manifest_task_ids:
                errors.append(
                    diagnostic(
                        "E_CHECKPOINT_TEST_REQUIREMENTS",
                        "integration.checkpoint_tests must exactly cover task IDs",
                    )
                )
            for checkpoint_task_id, requirements in checkpoint_tests.items():
                if is_resolved_string(checkpoint_task_id):
                    task_acceptance_value = dict_or_empty(
                        task_definitions.get(checkpoint_task_id)
                    ).get("acceptance_criteria")
                    task_acceptance_ids = (
                        set(task_acceptance_value)
                        if is_str_list(task_acceptance_value)
                        else set()
                    )
                    checkpoint_requirements[checkpoint_task_id] = (
                        _validate_test_requirements(
                            requirements,
                            f"integration.checkpoint_tests.{checkpoint_task_id}",
                            errors,
                            allowed_acceptance_criteria=task_acceptance_ids,
                        )
                    )
    if state is None:
        return
    if not is_str(state["status"]) or state["status"] not in INTEGRATION_STATUSES:
        errors.append(
            diagnostic("E_INTEGRATION_STATUS", "integration status is invalid")
        )
    if phase != "merge-ready":
        return

    git = dict_or_empty(runtime.get("git"))
    plan_sha = git.get("plan_sha")
    workflow_base = git.get("workflow_base_sha")
    if state["status"] != "reviewed":
        errors.append(
            diagnostic(
                "E_INTEGRATION_PHASE_STATUS",
                "integration.status must be reviewed",
            )
        )
    if not is_oid(state["start_sha"]) or state["start_sha"] != plan_sha:
        errors.append(
            diagnostic(
                "E_INTEGRATION_START_SHA",
                "integration.start_sha must equal PLAN_SHA",
            )
        )
    if not is_oid(state["head_sha"]):
        errors.append(
            diagnostic("E_INTEGRATION_HEAD_SHA", "integration.head_sha is invalid")
        )
    if state["clean"] is not True:
        errors.append(
            diagnostic("E_INTEGRATION_DIRTY", "integration worktree is not clean")
        )
    if not is_resolved_string(state["write_authorization_basis"]):
        errors.append(
            diagnostic(
                "E_INTEGRATION_WRITE_AUTHORIZATION",
                "integration write authorization basis is required",
            )
        )
    if (
        not isinstance(state["commits"], list)
        or not state["commits"]
        or any(not is_oid(commit) for commit in state["commits"])
        or state["commits"][-1] != state["head_sha"]
    ):
        errors.append(
            diagnostic(
                "E_INTEGRATION_COMMITS",
                "integration.commits must be real first-parent commits ending at head",
            )
        )
    _validate_commit_authorizations(
        "integration",
        "integration_id",
        definition if definition is not None else {},
        state,
        dict_or_empty(manifest.get("orchestration")).get("revision"),
        errors,
        list_commits,
        changed_paths,
    )
    merged = state["merged_tasks"]
    merged_map: dict[str, dict[str, Any]] = {}
    if not isinstance(merged, list):
        errors.append(
            diagnostic("E_INTEGRATED_TASKS", "integration.merged_tasks must be a list")
        )
        merged = []
    for index, item in enumerate(merged):
        record = _require_object(
            item,
            (
                "task_id",
                "reviewed_head_sha",
                "integration_head_after",
                "verification",
            ),
            f"integration.merged_tasks[{index}]",
            errors,
        )
        if record is None or not is_resolved_string(record["task_id"]):
            continue
        if record["task_id"] in merged_map:
            errors.append(
                diagnostic(
                    "E_INTEGRATED_TASK_DUPLICATE",
                    f"task {record['task_id']} is recorded more than once",
                )
            )
        merged_map[record["task_id"]] = record
        if not is_oid(record["reviewed_head_sha"]) or not is_oid(
            record["integration_head_after"]
        ):
            errors.append(
                diagnostic(
                    "E_INTEGRATED_TASK_SHA",
                    f"merged task {record['task_id']} has invalid SHA evidence",
                )
            )
        verification = _require_object(
            record["verification"],
            ("status", "tested_head_sha", "evidence_ref"),
            f"integration.merged_tasks[{index}].verification",
            errors,
        )
        if verification is not None:
            if verification["status"] != "passed":
                errors.append(
                    diagnostic(
                        "E_INTEGRATION_STEP_TESTS",
                        f"merged task {record['task_id']} checkpoint is not verified",
                    )
                )
            if verification["tested_head_sha"] != record["integration_head_after"]:
                errors.append(
                    diagnostic(
                        "E_INTEGRATION_STEP_TESTS_STALE",
                        f"merged task {record['task_id']} checkpoint verification is stale",
                    )
                )
            _validate_test_evidence(
                verification["evidence_ref"],
                location=(
                    f"integration.merged_tasks[{index}].verification.evidence_ref"
                ),
                expected_workflow=manifest.get("workflow_id"),
                expected_plan=dict_or_empty(manifest.get("plan")).get("revision"),
                expected_orchestration=dict_or_empty(
                    manifest.get("orchestration")
                ).get("revision"),
                expected_subject=f"checkpoint:{record['task_id']}",
                expected_head=record["integration_head_after"],
                expected_tests=checkpoint_requirements.get(record["task_id"], []),
                errors=errors,
                evidence_reader=evidence_reader,
            )
    if set(merged_map) != set(task_states):
        errors.append(
            diagnostic(
                "E_INTEGRATED_TASKS",
                "merged task IDs must exactly match all workflow tasks",
            )
        )
    if definition is not None and [
        record.get("task_id")
        for record in merged
        if isinstance(record, dict)
    ] != definition.get("merge_order"):
        errors.append(
            diagnostic(
                "E_INTEGRATION_MERGE_ORDER",
                "runtime merged task order differs from immutable merge_order",
            )
        )
    for task_id, task_state in task_states.items():
        record = merged_map.get(task_id)
        if record is None:
            continue
        if record["reviewed_head_sha"] != task_state.get("head_sha"):
            errors.append(
                diagnostic(
                    "E_INTEGRATED_TASK_STALE",
                    f"{task_id} merged head differs from reviewed task head",
                )
            )
        if (
            is_ancestor is not None
            and is_oid(task_state.get("head_sha"))
            and is_oid(state["head_sha"])
            and not is_ancestor(task_state["head_sha"], state["head_sha"])
        ):
            errors.append(
                diagnostic(
                    "E_INTEGRATED_TASK_ANCESTRY",
                    f"{task_id} reviewed head is not in integration head",
                )
            )
        if (
            is_ancestor is not None
            and is_oid(record["integration_head_after"])
            and is_oid(state["head_sha"])
            and not is_ancestor(record["integration_head_after"], state["head_sha"])
        ):
            errors.append(
                diagnostic(
                    "E_INTEGRATION_STEP_ANCESTRY",
                    f"{task_id} integration checkpoint is not in final head",
                )
            )
    if (
        is_ancestor is not None
        and is_oid(state["start_sha"])
        and is_oid(state["head_sha"])
        and not is_ancestor(state["start_sha"], state["head_sha"])
    ):
        errors.append(
            diagnostic(
                "E_INTEGRATION_ANCESTRY",
                "integration start is not an ancestor of final head",
            )
        )

    tests = _require_object(
        state["tests"],
        ("status", "tested_head_sha", "evidence_ref"),
        "integration.tests",
        errors,
    )
    if tests is not None:
        if tests["status"] != "passed":
            errors.append(
                diagnostic("E_INTEGRATION_TESTS", "integration tests are not passed")
            )
        if tests["tested_head_sha"] != state["head_sha"]:
            errors.append(
                diagnostic(
                    "E_INTEGRATION_TESTS_STALE",
                    "integration tests are stale",
                )
            )
        _validate_test_evidence(
            tests["evidence_ref"],
            location="integration.tests.evidence_ref",
            expected_workflow=manifest.get("workflow_id"),
            expected_plan=dict_or_empty(manifest.get("plan")).get("revision"),
            expected_orchestration=dict_or_empty(
                manifest.get("orchestration")
            ).get("revision"),
            expected_subject="integration",
            expected_head=state["head_sha"],
            expected_tests=integration_required_tests,
            errors=errors,
            evidence_reader=evidence_reader,
        )

    contract_revisions = sorted(
        f"{item['id']}@{item['revision']}"
        for item in list_or_empty(manifest.get("contracts"))
        if isinstance(item, dict)
        and is_resolved_string(item.get("id"))
        and is_resolved_string(item.get("revision"))
    )
    _validate_review(
        state["final_review"],
        location="integration.final_review",
        expected_workflow=manifest.get("workflow_id"),
        expected_subject="integration",
        expected_engine=dict_or_empty(
            manifest.get("review_engine")
        ).get("selected_engine"),
        expected_plan=dict_or_empty(manifest.get("plan")).get("revision"),
        expected_orchestration=dict_or_empty(manifest.get("orchestration")).get(
            "revision"
        ),
        expected_contracts=contract_revisions,
        expected_scope=definition.get("review_scope") if definition else None,
        expected_base=workflow_base,
        expected_head=state["head_sha"],
        errors=errors,
        merge_base=merge_base,
        evidence_reader=evidence_reader,
        expected_accepted_risk_ids={
            risk["id"]
            for task_state in task_states.values()
            for risk in list_or_empty(task_state.get("risks"))
            if isinstance(risk, dict)
            and risk.get("status") == "accepted"
            and is_resolved_string(risk.get("id"))
        },
    )
    _validate_integration_execution(
        state["execution_log_ref"],
        manifest,
        state,
        merged_map,
        errors,
        evidence_reader,
    )


def _prepare_plan_artifact_reader(
    manifest: dict[str, Any],
    runtime: dict[str, Any],
    phase: str,
    errors: list[Diagnostic],
    read_object: Callable[[str, str], bytes] | None,
) -> Callable[[str], bytes] | None:
    if phase == "planning":
        return None
    plan_sha = dict_or_empty(runtime.get("git")).get("plan_sha")
    if read_object is None or not is_oid(plan_sha):
        errors.append(
            diagnostic(
                "E_PLAN_SNAPSHOT_PROOF_REQUIRED",
                "formal validation requires a reader for PLAN_SHA artifacts",
            )
        )
        return None
    try:
        raw_manifest = read_object(plan_sha, ".ai/workflow-manifest.json")
        if not isinstance(raw_manifest, bytes):
            raise TypeError("Git object reader must return bytes")
        committed_manifest = json.loads(raw_manifest.decode("utf-8"))
    except Exception as exc:
        errors.append(
            diagnostic(
                "E_PLAN_MANIFEST_MISSING",
                f"cannot read PLAN_SHA manifest: {exc}",
            )
        )
        return None
    if committed_manifest != manifest:
        errors.append(
            diagnostic(
                "E_PLAN_MANIFEST_DRIFT",
                "current manifest does not match PLAN_SHA manifest content",
            )
        )

    def reader(relative: str) -> bytes:
        return read_object(plan_sha, f".ai/{relative}")

    return reader


def _immutable_plan_paths(manifest: dict[str, Any]) -> list[str]:
    paths = [".ai/workflow-manifest.json"]
    artifacts = manifest.get("artifacts")
    if isinstance(artifacts, dict):
        paths.extend(
            f".ai/{value}"
            for value in artifacts.values()
            if is_safe_relative_path(value)
        )
    for item in list_or_empty(manifest.get("tasks")):
        if isinstance(item, dict) and is_safe_relative_path(item.get("path")):
            paths.append(f".ai/{item['path']}")
    for item in list_or_empty(manifest.get("contracts")):
        if isinstance(item, dict) and is_safe_relative_path(item.get("path")):
            paths.append(f".ai/{item['path']}")
    return sorted(set(paths))


def _validate_immutable_plan_at_heads(
    manifest: dict[str, Any],
    runtime: dict[str, Any],
    phase: str,
    errors: list[Diagnostic],
    read_object: Callable[[str, str], bytes] | None,
) -> None:
    if phase == "planning" or read_object is None:
        return
    plan_sha = dict_or_empty(runtime.get("git")).get("plan_sha")
    if not is_oid(plan_sha):
        return
    heads: dict[str, str] = {}
    tasks = runtime.get("tasks")
    if isinstance(tasks, dict):
        for task_id, state in tasks.items():
            if isinstance(state, dict) and is_oid(state.get("head_sha")):
                heads[f"task {task_id}"] = state["head_sha"]
    integration = runtime.get("integration")
    if (
        phase == "merge-ready"
        and isinstance(integration, dict)
        and is_oid(integration.get("head_sha"))
    ):
        heads["integration"] = integration["head_sha"]
    for path in _immutable_plan_paths(manifest):
        try:
            planned = read_object(plan_sha, path)
        except Exception as exc:
            errors.append(
                diagnostic(
                    "E_PLAN_ARTIFACT_MISSING",
                    f"{path} is missing from PLAN_SHA: {exc}",
                )
            )
            continue
        for label, head_sha in heads.items():
            try:
                current = read_object(head_sha, path)
            except Exception as exc:
                errors.append(
                    diagnostic(
                        "E_PLAN_ARTIFACT_MISSING",
                        f"{path} is missing from {label} HEAD: {exc}",
                    )
                )
                continue
            if current != planned:
                errors.append(
                    diagnostic(
                        "E_PLAN_ARTIFACT_DRIFT",
                        f"{path} changed between PLAN_SHA and {label} HEAD",
                    )
                )


def validate_state(
    manifest: dict[str, Any],
    runtime: dict[str, Any],
    *,
    phase: str = "planning",
    ai_root: Path | None = None,
    task_id: str | None = None,
    is_ancestor: Callable[[str, str], bool] | None = None,
    merge_base: Callable[[str, str], str] | None = None,
    object_exists: Callable[[str, str], bool] | None = None,
    read_object: Callable[[str, str], bytes] | None = None,
    evidence_reader: Callable[[str], bytes] | None = None,
    list_commits: Callable[[str, str], list[str]] | None = None,
    changed_paths: Callable[[str], list[str]] | None = None,
) -> list[Diagnostic]:
    errors: list[Diagnostic] = []
    if phase not in {
        "planning",
        "dispatch",
        "branch-ready",
        "integration-ready",
        "merge-ready",
    }:
        return [diagnostic("E_PHASE", f"unknown phase: {phase}")]
    if not isinstance(manifest, dict) or not isinstance(runtime, dict):
        return [diagnostic("E_ROOT", "manifest and runtime roots must be objects")]

    _validate_runtime_identity(manifest, runtime, errors)
    _validate_workflow_control(manifest, runtime, phase, errors)
    for field in ENGINE_RULES:
        _validate_engine(manifest, field, errors)
    _validate_plan(manifest, errors)
    _validate_delivery_definition(manifest, errors)
    _validate_orchestration(manifest, errors)
    _validate_conditions(
        manifest,
        runtime,
        phase,
        task_id,
        errors,
        evidence_reader,
        read_object,
    )
    git = _validate_git_record(runtime, phase, errors)
    plan_artifact_reader = _prepare_plan_artifact_reader(
        manifest,
        runtime,
        phase,
        errors,
        read_object,
    )
    _validate_artifacts(manifest, ai_root, errors, plan_artifact_reader)
    _validate_contracts_and_tasks(
        manifest,
        ai_root,
        errors,
        plan_artifact_reader,
    )
    _validate_acceptance_traceability(manifest, errors)

    if phase != "planning" and (
        is_ancestor is None
        or merge_base is None
        or object_exists is None
        or read_object is None
        or list_commits is None
        or changed_paths is None
    ):
        errors.append(
            diagnostic(
                "E_GIT_PROOF_REQUIRED",
                f"{phase} validation requires Git ancestry, merge-base and object content proof",
            )
        )
    if phase != "planning" and evidence_reader is None:
        errors.append(
            diagnostic(
                "E_EVIDENCE_PROOF_REQUIRED",
                f"{phase} validation requires external evidence proof",
            )
        )

    task_states = _validate_runtime_tasks(
        manifest,
        runtime,
        phase,
        task_id,
        errors,
        is_ancestor,
        merge_base,
        object_exists,
        read_object,
        evidence_reader,
        list_commits,
        changed_paths,
    )
    if phase == "branch-ready":
        if not is_resolved_string(task_id) or task_id not in task_states:
            errors.append(
                diagnostic(
                    "E_TASK_SELECTION",
                    "--task-id must select a known task",
                )
            )
    _validate_task_starts_and_dependencies(
        manifest,
        runtime,
        task_states,
        phase,
        task_id,
        errors,
        is_ancestor,
        merge_base,
        object_exists,
        read_object,
        evidence_reader,
    )
    _validate_integration(
        manifest,
        runtime,
        task_states,
        phase,
        errors,
        is_ancestor,
        merge_base,
        evidence_reader,
        list_commits,
        changed_paths,
    )
    _validate_immutable_plan_at_heads(
        manifest,
        runtime,
        phase,
        errors,
        read_object,
    )

    if phase != "planning" and git is not None and is_ancestor is not None:
        base_sha = git["workflow_base_sha"]
        plan_sha = git["plan_sha"]
        if is_oid(base_sha) and is_oid(plan_sha) and not is_ancestor(
            base_sha, plan_sha
        ):
            errors.append(
                diagnostic(
                    "E_PLAN_ANCESTRY",
                    "WORKFLOW_BASE_SHA is not an ancestor of PLAN_SHA",
                )
            )
    if phase != "planning" and git is not None and object_exists is not None:
        plan_sha = git["plan_sha"]
        if is_oid(plan_sha):
            planned_paths: list[str] = [".ai/workflow-manifest.json"]
            artifacts = manifest.get("artifacts")
            if isinstance(artifacts, dict):
                planned_paths.extend(
                    f".ai/{value}"
                    for value in artifacts.values()
                    if is_safe_relative_path(value)
                )
            for item in list_or_empty(manifest.get("tasks")):
                if isinstance(item, dict):
                    for field in ("path", "report_path"):
                        if is_safe_relative_path(item.get(field)):
                            planned_paths.append(f".ai/{item[field]}")
            for item in list_or_empty(manifest.get("contracts")):
                if isinstance(item, dict) and is_safe_relative_path(item.get("path")):
                    planned_paths.append(f".ai/{item['path']}")
            for path in sorted(set(planned_paths)):
                if not object_exists(plan_sha, path):
                    errors.append(
                        diagnostic(
                            "E_PLAN_ARTIFACT_MISSING",
                            f"{path} is missing from PLAN_SHA",
                        )
                    )
    return errors


def derive_merge_readiness(
    manifest: dict[str, Any],
    runtime: dict[str, Any],
    *,
    ai_root: Path | None = None,
    is_ancestor: Callable[[str, str], bool] | None = None,
    merge_base: Callable[[str, str], str] | None = None,
    object_exists: Callable[[str, str], bool] | None = None,
    read_object: Callable[[str, str], bytes] | None = None,
    evidence_reader: Callable[[str], bytes] | None = None,
    list_commits: Callable[[str, str], list[str]] | None = None,
    changed_paths: Callable[[str], list[str]] | None = None,
) -> str:
    errors = validate_state(
        manifest,
        runtime,
        phase="merge-ready",
        ai_root=ai_root,
        is_ancestor=is_ancestor,
        merge_base=merge_base,
        object_exists=object_exists,
        read_object=read_object,
        evidence_reader=evidence_reader,
        list_commits=list_commits,
        changed_paths=changed_paths,
    )
    # Legacy phase names remain readable; final PR readiness belongs to delivery.
    return "DEVELOPMENT_READY" if not errors else "NOT_READY"


def authorization_allows(
    runtime: dict[str, Any],
    operation: str,
    head_sha: str,
) -> bool:
    authorizations = runtime.get("authorizations")
    if not isinstance(authorizations, dict):
        return False
    record = authorizations.get(operation)
    return (
        isinstance(record, dict)
        and record.get("granted") is True
        and record.get("head_sha") == head_sha
    )


class GitEvidence:
    def __init__(self, repo: Path):
        self.repo = repo
        self.environment = os.environ.copy()
        for key in tuple(self.environment):
            if key in {
                "GIT_DIR",
                "GIT_WORK_TREE",
                "GIT_COMMON_DIR",
                "GIT_OBJECT_DIRECTORY",
                "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                "GIT_INDEX_FILE",
            } or key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
                self.environment.pop(key, None)
        self.environment.pop("GIT_CONFIG_COUNT", None)
        self.environment["GIT_NO_REPLACE_OBJECTS"] = "1"

    def _run(self, *args: str, allow_false: bool = False) -> subprocess.CompletedProcess:
        result = subprocess.run(
            ("git", "--no-replace-objects", "-C", str(self.repo), *args),
            check=False,
            capture_output=True,
            text=True,
            env=self.environment,
        )
        if result.returncode == 0:
            return result
        if allow_false and result.returncode == 1:
            return result
        message = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise GitEvidenceError(message)

    def is_ancestor(self, base: str, head: str) -> bool:
        return (
            self._run(
                "merge-base",
                "--is-ancestor",
                base,
                head,
                allow_false=True,
            ).returncode
            == 0
        )

    def merge_base(self, base: str, head: str) -> str:
        return self._run("merge-base", base, head).stdout.strip()

    def object_exists(self, commit: str, path: str) -> bool:
        return (
            self._run(
                "cat-file",
                "-e",
                f"{commit}:{path}",
                allow_false=True,
            ).returncode
            == 0
        )

    def read_object(self, commit: str, path: str) -> bytes:
        result = subprocess.run(
            (
                "git",
                "--no-replace-objects",
                "-C",
                str(self.repo),
                "cat-file",
                "blob",
                f"{commit}:{path}",
            ),
            check=False,
            capture_output=True,
            env=self.environment,
        )
        if result.returncode != 0:
            message = (
                result.stderr.decode("utf-8", errors="replace").strip()
                or result.stdout.decode("utf-8", errors="replace").strip()
                or "git object read failed"
            )
            raise GitEvidenceError(message)
        return result.stdout

    def list_commits(self, start: str, head: str) -> list[str]:
        output = self._run(
            "rev-list",
            "--first-parent",
            head,
        ).stdout
        lineage = [line for line in output.splitlines() if line]
        try:
            start_index = lineage.index(start)
        except ValueError as exc:
            raise GitEvidenceError(
                f"{start} is not on the first-parent chain of {head}"
            ) from exc
        return list(reversed(lineage[:start_index]))

    def changed_paths(self, commit: str) -> list[str]:
        lineage = self._run("rev-list", "--parents", "-n", "1", commit).stdout.split()
        if len(lineage) >= 2:
            output = self._run(
                "diff",
                "--name-only",
                "-z",
                "--no-renames",
                lineage[1],
                commit,
            ).stdout
        else:
            output = self._run(
                "diff-tree",
                "--root",
                "--no-commit-id",
                "--name-only",
                "-z",
                "-r",
                commit,
            ).stdout
        return sorted({path for path in output.split("\0") if path})


class EvidenceStore:
    def __init__(self, state_path: Path):
        self.root = state_path.parent.resolve()

    def read(self, relative: str) -> bytes:
        if not is_safe_relative_path(relative):
            raise ValueError("unsafe evidence path")
        path = (self.root / relative).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("evidence path escapes runtime directory") from exc
        if not path.is_file():
            raise FileNotFoundError(path)
        return path.read_bytes()


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} root must be an object")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate immutable workflow artifacts and external runtime state."
    )
    parser.add_argument(
        "--root",
        required=True,
        type=Path,
        help="Repository root containing .ai/workflow-manifest.json.",
    )
    parser.add_argument(
        "--state",
        required=True,
        type=Path,
        help="External single-writer runtime-state.json.",
    )
    parser.add_argument(
        "--phase",
        choices=(
            "planning",
            "dispatch",
            "branch-ready",
            "integration-ready",
            "merge-ready",
        ),
        default="planning",
    )
    parser.add_argument("--task-id")
    parser.add_argument(
        "--check-git",
        action="store_true",
        help="Use read-only Git commands for ancestry and object proof.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    ai_root = root / ".ai"
    state_path = args.state.resolve()
    try:
        state_path.relative_to(root)
    except ValueError:
        pass
    else:
        print(
            "E_STATE_LOCATION: runtime-state.json must be outside the target repository",
            file=sys.stderr,
        )
        return 2
    try:
        manifest = load_json(ai_root / "workflow-manifest.json")
        runtime = load_json(state_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"E_INPUT: {exc}", file=sys.stderr)
        return 2

    if args.phase != "planning" and not args.check_git:
        print(
            f"E_GIT_PROOF_REQUIRED: --phase {args.phase} requires --check-git",
            file=sys.stderr,
        )
        return 2

    evidence = GitEvidence(root) if args.check_git else None
    evidence_store = EvidenceStore(state_path) if args.phase != "planning" else None
    try:
        errors = validate_state(
            manifest,
            runtime,
            phase=args.phase,
            ai_root=ai_root,
            task_id=args.task_id,
            is_ancestor=evidence.is_ancestor if evidence else None,
            merge_base=evidence.merge_base if evidence else None,
            object_exists=evidence.object_exists if evidence else None,
            read_object=evidence.read_object if evidence else None,
            evidence_reader=evidence_store.read if evidence_store else None,
            list_commits=evidence.list_commits if evidence else None,
            changed_paths=evidence.changed_paths if evidence else None,
        )
    except (GitEvidenceError, OSError) as exc:
        print(f"E_GIT_EVIDENCE: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(
            f"E_VALIDATOR_INTERNAL: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2

    if errors:
        for item in errors:
            print(item, file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "valid",
                "phase": args.phase,
                "workflow_id": manifest["workflow_id"],
                "readiness": "DEVELOPMENT_READY" if args.phase == "merge-ready" else None,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

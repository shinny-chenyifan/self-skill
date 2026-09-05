#!/usr/bin/env python3
"""Read-only checks for the single execution-state block in an active plan."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys


STATUSES = {"NOT_STARTED", "IN_PROGRESS", "IMPLEMENTED", "VALIDATED", "VERIFIED", "BLOCKED"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def catalog(records, prefix):
    require(isinstance(records, list) and records, f"{prefix}: empty inventory")
    result = {}
    for record in records:
        require(isinstance(record, dict), f"{prefix}: expected object")
        key = record.get("id")
        require(isinstance(key, str) and re.fullmatch(prefix + r"[A-Za-z0-9._-]+", key), f"{prefix}: invalid ID")
        require(key not in result, f"duplicate ID: {key}")
        result[key] = record
    return result


def references(value, allowed, label, allow_empty=False):
    require(isinstance(value, list), f"{label}: expected list")
    require(all(isinstance(item, str) for item in value), f"{label}: invalid reference")
    require(len(value) == len(set(value)), f"{label}: duplicate references")
    require(allow_empty or bool(value), f"{label}: empty references")
    require(set(value) <= set(allowed), f"{label}: unknown reference")
    return set(value)


def read_ref(root, ref):
    require(isinstance(ref, dict), "missing evidence reference")
    name, digest = ref.get("path"), ref.get("sha256")
    require(nonempty(name), "missing evidence path")
    relative = Path(name)
    require(not relative.is_absolute() and ".." not in relative.parts, "unsafe evidence path")
    path = (root / relative).resolve()
    require(path.is_relative_to(root.resolve()) and path.is_file(), f"unreadable evidence: {name}")
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == digest, f"stale evidence: {name}")
    return raw


def snapshot(root, records):
    require(isinstance(records, list) and records, "missing implementation snapshot")
    result = {}
    for ref in records:
        require(isinstance(ref, dict), "invalid snapshot entry")
        name = ref.get("path")
        require(nonempty(name) and name not in result, "duplicate/invalid snapshot path")
        # Absence is an explicit assertion for a deleted file.
        if ref.get("deleted") is True:
            path = Path(name)
            require(not path.is_absolute() and ".." not in path.parts, "unsafe deleted path")
            target = root / path
            require(target.resolve().is_relative_to(root.resolve()), "deleted path escapes root")
            require(not target.exists() and not target.is_symlink(), f"deleted file still exists: {name}")
            result[name] = None
        else:
            read_ref(root, ref)
            mode = ref.get("mode")
            if mode is not None:
                require(isinstance(mode, str) and re.fullmatch(r"0[0-7]{3}", mode), "invalid snapshot mode")
                actual_mode = (root / name).stat().st_mode & 0o777
                require(actual_mode == int(mode, 8), f"stale file mode: {name}")
            result[name] = (ref["sha256"], mode)
    return result


def validate(plan_text, root, complete=False):
    """Raise ValueError on uncertainty; a pass checks evidence, not its semantics."""
    blocks = re.findall(r"^```execution-state\s*\n(.*?)^```\s*$", plan_text, re.M | re.S)
    require(len(blocks) == 1, "expected one execution-state block")
    state = json.loads(blocks[0])
    require(isinstance(state, dict) and type(state.get("schema_version")) is int and state["schema_version"] == 1, "unsupported execution schema")
    require(nonempty(state.get("plan_revision")), "missing plan revision")
    require(nonempty(state.get("objective")), "missing objective")
    source = read_ref(root, state.get("original_request"))
    require(bool(source.strip()), "empty original request")
    original = catalog(state.get("requirements"), "REQ-")
    criteria = catalog(state.get("acceptance_criteria"), "AC-")
    steps = catalog(state.get("steps"), "STEP-")
    tests = catalog(state.get("tests"), "TEST-")
    # Compare the durable source inventory independently from the plan mapping.
    original_ids = re.findall(r"^\s*- (REQ-[A-Za-z0-9._-]+):\s*\S", source.decode("utf-8"), re.M)
    require(len(original_ids) == len(set(original_ids)) and set(original_ids) == set(original), "original requirement inventory does not match plan")
    covered_ac = set()
    for item in original.values():
        covered_ac |= references(item.get("acceptance_criteria"), criteria, item["id"])
    require(covered_ac == set(criteria), "acceptance criteria lack requirement source")
    covered_steps = set()
    for item in criteria.values():
        require(nonempty(item.get("statement")), "missing acceptance statement")
        covered_steps |= references(item.get("steps"), steps, item["id"])
    require(covered_steps == set(steps), "steps lack acceptance mapping")
    current = state.get("current")
    require(isinstance(current, dict), "missing current state")
    for key in ("phase", "milestone", "status", "last_completed", "next_action"):
        require(nonempty(current.get(key)), f"current.{key} is required")
    require(current["status"] in STATUSES | {"COMPLETE"}, "invalid current status")
    references(current.get("remaining"), steps, "remaining", allow_empty=True)
    require(isinstance(current.get("blockers"), list), "missing blockers")
    require(current["milestone"] in steps or current["milestone"] == "final", "unknown current milestone")
    if current["status"] == "BLOCKED":
        require(bool(current["blockers"]), "blocked state needs a reason")
    snapshots = {}
    used_tests = set()
    for key, step in steps.items():
        status = step.get("status")
        require(isinstance(status, str) and status in STATUSES, f"{key}: invalid status")
        required_tests = references(step.get("tests"), tests, key)
        used_tests |= required_tests
        if status == "BLOCKED":
            require(nonempty(step.get("blocker")), f"{key}: missing blocker")
            require(nonempty(step.get("recovery")), f"{key}: missing recovery condition")
        if status in {"IMPLEMENTED", "VALIDATED", "VERIFIED"}:
            snapshots[key] = snapshot(root, step.get("implementation"))
        if status in {"VALIDATED", "VERIFIED"}:
            for test_id in required_tests:
                test = tests[test_id]
                require(test.get("status") == "passed", f"{key}: {test_id} is unverified")
                require(test.get("plan_revision") == state["plan_revision"], f"{test_id}: stale plan revision")
                tested = snapshot(root, test.get("snapshot"))
                require(all(name in tested and tested[name] == digest for name, digest in snapshots[key].items()), f"{test_id}: wrong implementation snapshot")
                require(bool(read_ref(root, test.get("evidence")).strip()), f"{test_id}: empty evidence")
    require(used_tests == set(tests), "unassigned tests")
    for test in tests.values():
        require(nonempty(test.get("command")) and nonempty(test.get("expected")), "test needs command and expected result")
        require(test.get("status") in ("pending", "passed", "failed", "blocked"), "invalid test status")
    remaining = {key for key, step in steps.items() if step["status"] != "VERIFIED"}
    require(set(current["remaining"]) == remaining, "remaining inventory disagrees with step states")
    decisions = state.get("decisions", [])
    require(isinstance(decisions, list), "invalid decision ledger")
    if decisions:
        latest_approved_revision = None
        for decision in catalog(decisions, "DEC-").values():
            for key in ("reason", "impact", "plan_revision"):
                require(nonempty(decision.get(key)), f"decision lacks {key}")
            require(type(decision.get("scope_change")) is bool, "decision needs scope_change")
            if decision["scope_change"]:
                affected = references(decision.get("affected_steps"), steps, "scope affected_steps")
                require(decision.get("status") in ("pending", "approved"), "scope decision needs status")
                require(decision["status"] == "approved" or bool(current["blockers"]), "unapproved deviation needs blocker")
                if decision["status"] == "approved":
                    require(nonempty(decision.get("confirmation_basis")), "scope change lacks confirmation")
                    latest_approved_revision = decision["plan_revision"]
                else:
                    require(all(steps[key]["status"] == "BLOCKED" for key in affected), "pending deviation must block affected steps")
                    affected_ac = {key for key, item in criteria.items() if set(item["steps"]) & affected}
                    final = state.get("final_verification")
                    if isinstance(final, dict) and final.get("status") == "passed":
                        verified = references(final.get("acceptance_criteria"), criteria, "final verification")
                        require(not affected_ac & verified, "pending deviation invalidates affected final acceptance")
        require(latest_approved_revision is None or latest_approved_revision == state["plan_revision"], "latest approved scope revision does not match active plan")
    finish = complete or current["status"] == "COMPLETE"
    if finish or any(step["status"] == "VERIFIED" for step in steps.values()):
        final = state.get("final_verification")
        require(isinstance(final, dict) and final.get("plan_revision") == state["plan_revision"], "missing/current final verification required")
        verified_ac = references(final.get("acceptance_criteria"), criteria, "final verification")
        require(final.get("status") == "passed", "final verification not passed")
        require(bool(read_ref(root, final.get("evidence")).strip()), "empty final evidence")
        verified_snapshot = snapshot(root, final.get("snapshot"))
        for key, step in steps.items():
            if step["status"] == "VERIFIED":
                required_ac = {ac for ac, item in criteria.items() if key in item["steps"]}
                require(required_ac <= verified_ac, f"{key}: final AC coverage missing")
                require(all(name in verified_snapshot and verified_snapshot[name] == digest for name, digest in snapshots[key].items()), "final snapshot mismatch")
        if finish:
            require(not remaining and not current["blockers"], "unfinished or blocked work cannot be COMPLETE")
            require(verified_ac == set(criteria), "final verification omits acceptance criteria")
            require(current["phase"] == "complete", "completion phase required")
            require(not any(d.get("scope_change") and d.get("status") != "approved" for d in decisions), "pending scope decision")
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--root", type=Path, required=True, help="Root for implementation and evidence paths")
    parser.add_argument("--complete", action="store_true")
    args = parser.parse_args()
    try:
        state = validate(args.plan.read_text(encoding="utf-8"), args.root, args.complete)
    except (ValueError, OSError, UnicodeError, TypeError, KeyError) as exc:
        print(f"NOT_READY: {exc}", file=sys.stderr)
        return 2
    print(f"VALID: {state['plan_revision']} (structural/evidence checks only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

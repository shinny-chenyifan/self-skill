#!/usr/bin/env python3
"""Validate clean PR delivery against the existing development evidence ledger."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

import validate_workflow as workflow


def require(condition, message):
    if not condition:
        raise ValueError(message)


def ai_path(path):
    return path == ".ai" or path.startswith(".ai/")


class DeliveryGit(workflow.GitEvidence):
    def __init__(self, repo):
        super().__init__(repo)
        self.environment["GIT_OPTIONAL_LOCKS"] = "0"

    def verify_checkout(self, head):
        actual = self._run("rev-parse", "--verify", "HEAD").stdout.strip()
        require(actual == head, "delivery checkout HEAD differs from recorded SHA")
        status = self._run("status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
        require(not status, "delivery worktree is dirty")

    def tree(self, sha):
        output = self._run("ls-tree", "-r", "-z", "--full-tree", sha).stdout
        result = {}
        for entry in output.split("\0"):
            if entry:
                metadata, path = entry.split("\t", 1)
                mode, kind, oid = metadata.split()
                result[path] = (mode, kind, oid)
        return result

    def introduced_commits(self, base, head):
        # All parents matter: first-parent alone can hide an .ai commit in a merge.
        return self._run("rev-list", f"{base}..{head}").stdout.splitlines()

    def commit_parents(self, sha):
        return self._run("rev-list", "--parents", "-n", "1", sha).stdout.split()[1:]


def verify_content(base, source, head, git):
    require(all(workflow.is_oid(sha) for sha in (base, source, head)), "fixed full SHAs are required")
    require(git.is_ancestor(base, source) and git.is_ancestor(base, head), "delivery/source must descend from target base")
    base_tree, source_tree, head_tree = (git.tree(sha) for sha in (base, source, head))
    require(not any(ai_path(path) for path in base_tree), "target base already contains .ai; resolve explicitly")
    expected = {path: entry for path, entry in source_tree.items() if not ai_path(path)}
    require(head_tree == expected, "delivery tree differs from source excluding .ai (content, deletion or mode)")
    commits = git.introduced_commits(base, head)
    require(bool(commits), "delivery has no introduced commits")
    for commit in commits:
        require(workflow.is_oid(commit), "invalid introduced commit")
        tree = git.tree(commit)
        require(not any(ai_path(path) for path in tree), f".ai exists in PR commit {commit}")
        require(not any(ai_path(path) for path in git.changed_paths(commit)), f".ai changed in PR commit {commit}")
        # Inspect every parent, including the target history, to catch deletions.
        for parent in git.commit_parents(commit):
            require(not any(ai_path(path) for path in git.tree(parent)), f".ai removed by PR commit {commit}")
    return commits


def checkpoint_valid(runtime):
    checkpoint = runtime.get("checkpoint")
    require(isinstance(checkpoint, dict) and type(checkpoint.get("schema_version")) is int and checkpoint["schema_version"] == 1, "missing/versioned checkpoint")
    for field in ("plan_revision", "orchestration_revision"):
        require(checkpoint.get(field) == runtime.get(field), f"checkpoint {field} is stale")
    for field in ("writer", "phase", "last_completed", "next_action"):
        require(workflow.is_resolved_string(checkpoint.get(field)), f"checkpoint lacks {field}")
    require(isinstance(checkpoint.get("blockers"), list), "checkpoint lacks blockers")
    remaining = checkpoint.get("remaining_task_ids")
    require(workflow.is_str_list(remaining) and len(remaining) == len(set(remaining)), "invalid remaining task inventory")
    tasks = runtime.get("tasks")
    require(isinstance(tasks, dict) and set(remaining) <= set(tasks), "checkpoint references unknown task")
    unfinished = {key for key, value in tasks.items() if not isinstance(value, dict) or value.get("status") not in {"integrated", "cancelled"}}
    require(set(remaining) == unfinished, "checkpoint does not enumerate unfinished tasks")
    return checkpoint


def recover(locator_path):
    locator = workflow.load_json(locator_path)
    require(type(locator.get("schema_version")) is int and locator["schema_version"] == 1, "unsupported locator version")
    location = locator.get("state_path")
    require(workflow.is_resolved_string(location), "missing state location")
    state_path = Path(location)
    require(state_path.is_absolute(), "locator state_path must be absolute")
    runtime = workflow.load_json(state_path)
    for key in ("workflow_id", "runtime_state_id"):
        require(workflow.is_resolved_string(locator.get(key)) and locator[key] == runtime.get(key), f"locator {key} mismatch")
    checkpoint_valid(runtime)
    # Locating a ledger is not proof that its code or evidence is still current.
    return state_path, runtime


def validate_delivery(manifest, runtime, git, reader, ai_root=None):
    spec = manifest.get("delivery")
    record = runtime.get("delivery")
    require(isinstance(spec, dict) and type(spec.get("schema_version")) is int and spec["schema_version"] == 1, "missing delivery specification; legacy state is not PR ready")
    require(isinstance(record, dict) and type(record.get("schema_version")) is int and record["schema_version"] == 1, "missing delivery state")
    require(spec.get("excluded_paths") == [".ai/"], "only root .ai/ may be excluded in delivery v1")
    errors = workflow.validate_state(
        manifest, runtime, phase="merge-ready", ai_root=ai_root,
        is_ancestor=git.is_ancestor, merge_base=git.merge_base,
        object_exists=git.object_exists, read_object=git.read_object,
        evidence_reader=reader, list_commits=git.list_commits,
        changed_paths=git.changed_paths,
    )
    require(not errors, "development evidence is not ready: " + "; ".join(str(item) for item in errors))
    checkpoint = checkpoint_valid(runtime)
    require(not checkpoint["blockers"] and not checkpoint["remaining_task_ids"], "unfinished checkpoint")
    base = runtime["git"]["workflow_base_sha"]
    source = runtime["integration"]["head_sha"]
    head = record.get("head_sha")
    require(workflow.is_oid(head), "delivery needs a fixed full SHA")
    git.verify_checkout(head)
    require(record.get("base_sha") == base and record.get("source_head_sha") == source, "delivery source/base is stale")
    require(record.get("plan_revision") == runtime["plan_revision"] and record.get("orchestration_revision") == runtime["orchestration_revision"], "delivery revisions are stale")
    require(record.get("status") == "verified", "delivery is not verified")
    require(workflow.is_resolved_string(record.get("write_authorization_basis")), "missing delivery write authorization")
    commits = verify_content(base, source, head, git)
    require(record.get("commits") == commits, "delivery commit inventory mismatch (use rev-list order)")
    authorizations = record.get("commit_authorizations")
    require(isinstance(authorizations, list) and len(authorizations) == len(commits), "missing per-commit delivery authorization")
    by_commit = {}
    for item in authorizations:
        require(isinstance(item, dict) and workflow.is_oid(item.get("commit_sha")), "invalid delivery authorization")
        require(item["commit_sha"] not in by_commit, "duplicate delivery authorization")
        by_commit[item["commit_sha"]] = item
    require(set(by_commit) == set(commits), "authorization commit mismatch")
    for commit in commits:
        item = by_commit[commit]
        require(item.get("plan_revision") == runtime["plan_revision"] and item.get("orchestration_revision") == runtime["orchestration_revision"], "stale commit authorization")
        require(workflow.is_resolved_string(item.get("authorization_basis")), "missing commit authorization basis")
        paths = item.get("diff_scope")
        require(workflow.is_str_list(paths) and len(paths) == len(set(paths)) and set(paths) == set(git.changed_paths(commit)), "delivery authorization must cover exact changed paths")
    scope = manifest["integration"]["review_scope"]
    workflow._validate_test_evidence(
        record.get("tests_ref"), location="delivery.tests",
        expected_workflow=manifest["workflow_id"], expected_plan=runtime["plan_revision"],
        expected_orchestration=runtime["orchestration_revision"], expected_subject="delivery",
        expected_head=head, expected_tests=manifest["integration"]["required_tests"],
        errors=errors, evidence_reader=reader,
    )
    accepted = {
        risk["id"] for task in runtime["tasks"].values()
        for risk in task.get("risks", []) if risk.get("status") == "accepted"
    }
    workflow._validate_review(
        record.get("review"), location="delivery.review",
        expected_workflow=manifest["workflow_id"], expected_subject="delivery",
        expected_engine=manifest["review_engine"]["selected_engine"],
        expected_plan=runtime["plan_revision"], expected_orchestration=runtime["orchestration_revision"],
        expected_contracts=[f"{item['id']}@{item['revision']}" for item in manifest["contracts"]],
        expected_scope=scope, expected_base=base, expected_head=head,
        errors=errors, merge_base=git.merge_base, evidence_reader=reader,
        expected_accepted_risk_ids=accepted,
    )
    source_bytes = workflow._read_evidence_ref(spec.get("original_request_ref"), "delivery.original_request", errors, reader)
    require(isinstance(source_bytes, bytes) and source_bytes.strip(), "original request is unavailable")
    original_ids = re.findall(r"^\s*- (REQ-[A-Za-z0-9._-]+):\s*\S", source_bytes.decode("utf-8"), re.M)
    require(original_ids and len(original_ids) == len(set(original_ids)), "missing/duplicate original requirement inventory")
    mappings = spec.get("requirements")
    require(isinstance(mappings, list) and len(mappings) == len(original_ids), "original requirements omitted")
    mapped_ids, mapped_ac = set(), set()
    all_ac = set(workflow._plan_acceptance_criteria_map(manifest))
    for item in mappings:
        require(isinstance(item, dict) and item.get("id") in original_ids and item["id"] not in mapped_ids, "invalid requirement mapping")
        mapped_ids.add(item["id"])
        criteria = item.get("acceptance_criteria")
        require(workflow.is_str_list(criteria) and criteria and len(criteria) == len(set(criteria)) and set(criteria) <= all_ac, "invalid requirement AC mapping")
        mapped_ac.update(criteria)
    require(mapped_ac == all_ac, "AC lack original requirement coverage")
    final = workflow._read_evidence_json(record.get("final_verification_ref"), "delivery.final_verification", errors, reader)
    require(isinstance(final, dict), "missing independent final verification")
    for key, expected in {
        "schema_version": 1, "workflow_id": manifest["workflow_id"], "subject_id": "delivery",
        "plan_revision": runtime["plan_revision"], "orchestration_revision": runtime["orchestration_revision"],
        "head_sha": head, "scope_id": scope["scope_id"], "status": "passed",
    }.items():
        require(final.get(key) == expected and (key != "schema_version" or type(final[key]) is int), f"final verification {key} mismatch")
    for key, expected in (("requirements", mapped_ids), ("acceptance_criteria", all_ac)):
        values = final.get(key)
        require(workflow.is_str_list(values) and len(values) == len(set(values)) and set(values) == expected, f"final verification omits {key}")
    require(workflow.is_resolved_string(final.get("result")), "final verification lacks reverse-check result")
    require(not errors, "; ".join(str(item) for item in errors))
    git.verify_checkout(head)
    return "MERGE_READY"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--locator", type=Path, help="Locate durable state; alone performs no Git operations")
    parser.add_argument("--check-git", action="store_true", help="Run authorized read-only delivery checks")
    args = parser.parse_args()
    try:
        if args.locator:
            state_path, runtime = recover(args.locator)
            if not args.check_git:
                print(json.dumps({"status": "LOCATED_NOT_VERIFIED", "state_path": str(state_path), "checkpoint": runtime["checkpoint"]}, ensure_ascii=False))
                return 0
            require(args.state is None or args.state.resolve() == state_path.resolve(), "state and locator disagree")
        else:
            require(args.state is not None, "--state or --locator is required")
            state_path = args.state
            runtime = workflow.load_json(state_path)
        require(args.check_git and args.root is not None, "delivery verification requires --root and authorized --check-git")
        require(not state_path.resolve().is_relative_to(args.root.resolve()), "runtime state must be outside target repository")
        git = DeliveryGit(args.root)
        plan_sha = runtime.get("git", {}).get("plan_sha")
        require(workflow.is_oid(plan_sha), "invalid PLAN_SHA")
        manifest = json.loads(git.read_object(plan_sha, ".ai/workflow-manifest.json"))
        result = validate_delivery(manifest, runtime, git, workflow.EvidenceStore(state_path).read)
        print(json.dumps({
            "status": result,
            "workflow_id": manifest["workflow_id"],
            "base_sha": runtime["delivery"]["base_sha"],
            "head_sha": runtime["delivery"]["head_sha"],
            "scope_id": manifest["integration"]["review_scope"]["scope_id"],
        }, ensure_ascii=False))
        return 0
    except (ValueError, OSError, UnicodeError, TypeError, KeyError, workflow.GitEvidenceError) as exc:
        print(f"NOT_READY: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

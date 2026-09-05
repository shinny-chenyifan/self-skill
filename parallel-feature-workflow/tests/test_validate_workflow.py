import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "validate_workflow.py"
)
SPEC = importlib.util.spec_from_file_location("validate_workflow", SCRIPT)
validator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validator
SPEC.loader.exec_module(validator)

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
SHA_D = "d" * 40
SHA_E = "e" * 40


def scope_envelope(revision, objective):
    contract = {
        "version": 1,
        "status": "confirmed",
        "confirmation_basis": f"用户确认 {revision}。",
        "sources": [
            {
                "kind": "plan",
                "reference": ".ai/solution-record.md",
                "revision": "plan-v1",
            }
        ],
        "objective": objective,
        "in_scope": [f"{objective} 范围"],
        "out_of_scope": ["与当前目标无关的既有问题"],
        "acceptance_criteria": ["AC-1"],
        "integration_constraints": ["保持已确认契约"],
        "open_questions": [],
    }
    return {
        "scope_revision": revision,
        "scope_id": validator.canonical_scope_id(contract),
        "fail_on": "P2",
        "contract": contract,
    }


def json_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def evidence_ref(path, raw):
    return {"path": path, "sha256": hashlib.sha256(raw).hexdigest()}


def engine_selection_basis(
    *,
    kind,
    capability,
    primary_skill,
    availability,
    decision,
    actor,
    evidence,
):
    engine_field = (
        "planning_engine" if capability == "planner" else "review_engine"
    )
    return {
        "kind": kind,
        "reference": {
            "schema_version": 1,
            "subject_id": f"workflow-test:{engine_field}",
            "revision": "selection-r1",
            "capability": capability,
            "primary_skill": primary_skill,
            "availability": availability,
            "decision": decision,
            "actor": actor,
            "evidence": evidence,
            "evidence_sha256": hashlib.sha256(
                evidence.encode("utf-8")
            ).hexdigest(),
        },
    }


def review_payload(subject_id, base_sha, head_sha, contracts, scope):
    payload = {
        "schema_version": 1,
        "workflow_id": "workflow-test",
        "subject_id": subject_id,
        "status": "passed",
        "engine": "local-pr-review",
        "mode": "native",
        "actual_model": "inherited-test-model",
        "actual_reasoning_effort": "inherited-test-effort",
        "model_selection_basis": "inherited from caller",
        "fail_on": "P2",
        "scope_id": scope["scope_id"],
        "scope_revision": scope["scope_revision"],
        "plan_revision": "plan-v1",
        "orchestration_revision": "orch-v1",
        "contract_revisions": list(contracts),
        "review_base_sha": base_sha,
        "merge_base_sha": base_sha,
        "reviewed_head_sha": head_sha,
        "snapshot_valid": True,
        "blocking_count": 0,
        "completed_views": [
            "correctness",
            "error-boundaries",
            "state-lifecycle",
            "api-integration",
            "tests-regression",
        ],
        "failed_views": [],
        "aggregation_mode": "quick",
        "read_only_git_authorization_basis": "用户授权只读 Git Review。",
        "blockers": [],
        "change_responsibility_non_blocking": [],
        "advisories": [],
        "remaining_risks": [],
        "accepted_risks": [],
    }
    return payload


def review_record(subject_id, path, base_sha, head_sha, contracts, scope):
    payload = review_payload(subject_id, base_sha, head_sha, contracts, scope)
    record = {
        key: copy.deepcopy(value)
        for key, value in payload.items()
        if key
        not in {
            "schema_version",
            "workflow_id",
            "subject_id",
            "blockers",
            "change_responsibility_non_blocking",
            "advisories",
            "remaining_risks",
            "accepted_risks",
        }
    }
    raw = json_bytes(payload)
    record["report_ref"] = evidence_ref(path, raw)
    return record


def review_finding(
    *,
    severity="P0",
    scope_relation="in_scope",
    change_relation="introduced_by_change",
):
    return {
        "title": "示例问题",
        "severity": severity,
        "category": "correctness",
        "file": "src/task-1.py",
        "line_start": 1,
        "line_end": 1,
        "summary": "存在可复现的问题。",
        "evidence": "固定差异中的证据。",
        "trigger": "执行目标路径。",
        "suggested_fix": "修复根因。",
        "suggested_test": "增加回归测试。",
        "confidence": "high",
        "scope_relation": scope_relation,
        "change_relation": change_relation,
        "scope_basis": "plan-v1 / AC-1",
        "attribution_evidence": "问题由当前差异引入。",
        "source_lanes": ["correctness"],
        "source_candidate_ids": ["correctness:1"],
    }


def test_payload(subject_id, head_sha):
    if subject_id.startswith("task:"):
        test_id = f"TEST-{subject_id.removeprefix('task:')}"
    elif subject_id.startswith("condition:"):
        test_id = f"TEST-{subject_id.removeprefix('condition:')}"
    else:
        test_id = {
            "integration": "TEST-INTEGRATION",
            "checkpoint:TASK-1": "TEST-CHECKPOINT-TASK-1",
        }.get(subject_id, f"TEST-{subject_id}")
    return {
        "schema_version": 1,
        "workflow_id": "workflow-test",
        "plan_revision": "plan-v1",
        "orchestration_revision": "orch-v1",
        "subject_id": subject_id,
        "status": "passed",
        "tested_head_sha": head_sha,
        "commands": [
            {
                "test_id": test_id,
                "command": f"verify {subject_id}",
                "exit_code": 0,
                "status": "passed",
                "result": "all checks passed",
            }
        ],
    }


def integration_execution_text():
    return (
        "# Integration Execution\n\n"
        "- execution_schema_version: `1`\n"
        "- execution_status: `completed`\n"
        "- workflow_id: `workflow-test`\n"
        "- plan_revision: `plan-v1`\n"
        "- orchestration_revision: `orch-v1`\n"
        f"- integration_start_sha: `{SHA_B}`\n"
        f"- integration_head_sha: `{SHA_D}`\n"
        "- clean: `true`\n"
        "- unresolved_risk_count: `0`\n"
        "- write_authorization_basis: `用户授权集成与冲突文件写入。`\n\n"
        "## Integrated Tasks\n\n"
        "| task_id | reviewed task HEAD | integration HEAD after merge | conflicts | manual changes | verification |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        f"| TASK-1 | {SHA_C} | {SHA_D} | none | none | tests/checkpoint-TASK-1.json |\n\n"
        "## Final Snapshot\n\n"
        "- tests evidence: `tests/integration.json`\n"
        "- final Review result: `reviews/integration.json`\n\n"
        "## Remaining Risks and Authorization\n\nNone.\n"
    )


def valid_evidence_files():
    task_scope = scope_envelope("TASK-1-scope-r1", "验证 TASK-1 实现")
    integration_scope = scope_envelope("integration-scope-r1", "验证最终集成")
    return {
        "reviews/TASK-1.json": json_bytes(
            review_payload(
                "TASK-1",
                SHA_B,
                SHA_C,
                ["CONTRACT-1@r1"],
                task_scope,
            )
        ),
        "reviews/integration.json": json_bytes(
            review_payload(
                "integration",
                SHA_A,
                SHA_D,
                ["CONTRACT-1@r1"],
                integration_scope,
            )
        ),
        "tests/TASK-1.json": json_bytes(test_payload("task:TASK-1", SHA_C)),
        "tests/checkpoint-TASK-1.json": json_bytes(
            test_payload("checkpoint:TASK-1", SHA_D)
        ),
        "tests/integration.json": json_bytes(test_payload("integration", SHA_D)),
        "integration-execution.md": integration_execution_text().encode("utf-8"),
    }


def read_valid_evidence(path):
    try:
        return valid_evidence_files()[path]
    except KeyError as exc:
        raise FileNotFoundError(path) from exc


def condition_test_requirement():
    return {
        "kind": "test_result",
        "head_source": {"kind": "plan_sha"},
        "tests": [
            {
                "test_id": "TEST-COND-1",
                "command": "verify condition:COND-1",
            }
        ],
    }


def closed_test_condition_case():
    manifest = valid_manifest()
    manifest["plan"]["quality_status"] = "有条件通过"
    manifest["plan"]["conditions"] = [
        {
            "id": "COND-1",
            "owner": "Owner",
            "close_condition": "Evidence exists",
            "must_close_before": "dispatch",
            "applies_to": ["TASK-1"],
            "required_proof_kinds": ["test_result"],
            "proof_requirements": [condition_test_requirement()],
        }
    ]
    runtime = valid_runtime()
    proof_payload = test_payload("condition:COND-1", SHA_B)
    proof_raw = json_bytes(proof_payload)
    proof_commands = [
        {
            "test_id": item["test_id"],
            "command": item["command"],
        }
        for item in proof_payload["commands"]
    ]
    proof_identity = (
        f"test_result:condition:COND-1@{SHA_B}#"
        f"{validator.canonical_json_sha256(proof_commands)}"
    )
    condition_payload = {
        "schema_version": 1,
        "workflow_id": "workflow-test",
        "plan_revision": "plan-v1",
        "orchestration_revision": "orch-v1",
        "condition_id": "COND-1",
        "status": "closed",
        "closure": {
            "close_condition": "Evidence exists",
            "verified_by": "Verifier",
            "proofs": [
                {
                    "kind": "test_result",
                    "reference": evidence_ref(
                        "conditions/proofs/COND-1-test.json",
                        proof_raw,
                    ),
                    "identity": proof_identity,
                }
            ],
        },
    }
    condition_raw = json_bytes(condition_payload)
    runtime["conditions"] = [
        {
            "id": "COND-1",
            "status": "closed",
            "evidence_ref": evidence_ref(
                "conditions/COND-1.json",
                condition_raw,
            ),
        }
    ]
    runtime["tasks"]["TASK-1"]["status"] = "ready"
    files = valid_evidence_files()
    files["conditions/proofs/COND-1-test.json"] = proof_raw
    files["conditions/COND-1.json"] = condition_raw
    return manifest, runtime, files, condition_payload


def replace_condition_proof(
    manifest,
    runtime,
    files,
    condition_payload,
    *,
    kind,
    payload,
    identity,
    path,
    requirement,
):
    raw = json_bytes(payload)
    condition_payload["closure"]["proofs"] = [
        {
            "kind": kind,
            "reference": evidence_ref(path, raw),
            "identity": identity,
        }
    ]
    manifest["plan"]["conditions"][0]["required_proof_kinds"] = [kind]
    manifest["plan"]["conditions"][0]["proof_requirements"] = [requirement]
    condition_raw = json_bytes(condition_payload)
    runtime["conditions"][0]["evidence_ref"] = evidence_ref(
        "conditions/COND-1.json",
        condition_raw,
    )
    files[path] = raw
    files["conditions/COND-1.json"] = condition_raw


def valid_manifest():
    return {
        "schema_version": 3,
        "workflow_id": "workflow-test",
        "runtime_state_id": "runtime-workflow-test",
        "sources": ["user:confirmed-request"],
        "supersedes": None,
        "planning_engine": {
            "capability": "planner",
            "primary_skill": "solution-planner",
            "selected_engine": "solution-planner",
            "availability": "available",
            "selection_basis": engine_selection_basis(
                kind="catalog",
                capability="planner",
                primary_skill="solution-planner",
                availability="available",
                decision="use_primary",
                actor="orchestrator",
                evidence="当前会话 Skills catalog 包含 solution-planner。",
            ),
            "fallback_reason": None,
            "coverage_gap": [],
            "remaining_risk": [],
        },
        "review_engine": {
            "capability": "review",
            "primary_skill": "local-pr-review",
            "selected_engine": "local-pr-review",
            "availability": "available",
            "selection_basis": engine_selection_basis(
                kind="catalog",
                capability="review",
                primary_skill="local-pr-review",
                availability="available",
                decision="use_primary",
                actor="orchestrator",
                evidence="当前会话 Skills catalog 包含 local-pr-review。",
            ),
            "fallback_reason": None,
            "coverage_gap": [],
            "remaining_risk": [],
        },
        "plan": {
            "revision": "plan-v1",
            "quality_subject": "plan-v1",
            "contract_status": "就绪",
            "quality_status": "通过",
            "source": {
                "task_type": "新方案制定",
                "decision_status_field": "decision_status",
                "decision_status": "已确认",
                "confirmation_basis_field": "confirmation_basis",
                "confirmation_basis": "用户确认 plan-v1。",
            },
            "planning_mode": "new_plan",
            "effective_decision_status": "已确认",
            "effective_confirmation_basis": "用户确认 plan-v1。",
            "confirmation": {
                "subject_type": "plan",
                "subject_id": "workflow-test",
                "revision": "plan-v1",
                "confirmation_basis": "用户确认 plan-v1。",
                "confirmed_by": "user",
                "confirmed_at": None,
                "scoped_skip": False,
            },
            "acceptance_criteria": [
                {
                    "id": "AC-1",
                    "statement": "TASK-1 在最终集成中满足已确认行为。",
                    "source": {
                        "kind": "plan",
                        "reference": ".ai/solution-record.md",
                        "revision": "plan-v1",
                    },
                }
            ],
            "conditions": [],
        },
        "orchestration": {
            "revision": "orch-v1",
            "based_on_plan_revision": "plan-v1",
            "status": "confirmed",
            "confirmation_basis": "用户确认 orch-v1。",
            "confirmation": {
                "subject_type": "orchestration",
                "subject_id": "workflow-test",
                "revision": "orch-v1",
                "confirmation_basis": "用户确认 orch-v1。",
                "confirmed_by": "user",
                "confirmed_at": None,
                "scoped_skip": False,
            },
            "sources": ["plan:plan-v1"],
            "supersedes": None,
        },
        "artifacts": {
            "solution_record": "solution-record.md",
            "orchestration_record": "orchestration-record.md",
            "integration_plan": "merge/integration-plan.md",
        },
        "contracts": [
            {
                "id": "CONTRACT-1",
                "revision": "r1",
                "status": "confirmed",
                "based_on_plan_revision": "plan-v1",
                "supersedes": None,
                "sources": ["plan:plan-v1"],
                "path": "contracts/CONTRACT-1.md",
                "producer": "TASK-1",
                "consumers": [],
            }
        ],
        "tasks": [
            {
                "id": "TASK-1",
                "path": "tasks/TASK-1.md",
                "report_path": "reports/TASK-1-summary.md",
                "owner": "Coder-1",
                "branch": "feature/task-1",
                "worktree": "../feature-task-1",
                "dependencies": [],
                "contracts": ["CONTRACT-1@r1"],
                "acceptance_criteria": ["AC-1"],
                "required_tests": [
                    {
                        "id": "TEST-TASK-1",
                        "command": "verify task:TASK-1",
                        "acceptance_criteria": ["AC-1"],
                    }
                ],
                "allowed_paths": [
                    "src/task-1.py",
                    ".ai/reports/TASK-1-summary.md",
                ],
                "sources": ["plan:plan-v1", "orchestration:orch-v1"],
                "supersedes": None,
                "review_scope": scope_envelope(
                    "TASK-1-scope-r1",
                    "验证 TASK-1 实现",
                ),
            }
        ],
        "integration": {
            "branch": "feature/integration",
            "worktree": "../feature-integration",
            "sources": ["plan:plan-v1", "orchestration:orch-v1"],
            "supersedes": None,
            "required_tests": [
                {
                    "id": "TEST-INTEGRATION",
                    "command": "verify integration",
                    "acceptance_criteria": ["AC-1"],
                }
            ],
            "checkpoint_tests": {
                "TASK-1": [
                    {
                        "id": "TEST-CHECKPOINT-TASK-1",
                        "command": "verify checkpoint:TASK-1",
                        "acceptance_criteria": ["AC-1"],
                    }
                ]
            },
            "allowed_paths": ["src/task-1.py"],
            "merge_order": ["TASK-1"],
            "review_scope": scope_envelope(
                "integration-scope-r1",
                "验证最终集成",
            ),
        },
    }


def completed_workflow_history():
    statuses = (
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
    evidence = (
        "manifest:plan.confirmation",
        "manifest:planning-handoff",
        "manifest:orchestration.confirmation",
        "artifact:planning-docs",
        "runtime:git.plan_sha",
        "runtime:task-starts",
        "git:task-heads",
        "runtime:task-reviews",
        "runtime:integration.authorization",
        "runtime:integration.tests",
        "runtime:integration.final_review",
    )
    return [
        {
            "event": "advance",
            "from_status": from_status,
            "to_status": to_status,
            "plan_revision": "plan-v1",
            "orchestration_revision": "orch-v1",
            "evidence_refs": [evidence[index]],
        }
        for index, (from_status, to_status) in enumerate(
            zip(statuses, statuses[1:])
        )
    ]


def valid_runtime():
    return {
        "schema_version": 3,
        "workflow_id": "workflow-test",
        "runtime_state_id": "runtime-workflow-test",
        "plan_revision": "plan-v1",
        "orchestration_revision": "orch-v1",
        "workflow_status": "merge_ready",
        "transition_history": completed_workflow_history(),
        "git": {
            "target_branch": "main",
            "workflow_base_sha": SHA_A,
            "plan_sha": SHA_B,
            "read_only_git_authorization_basis": "用户授权只读 Git 校验。",
        },
        "conditions": [],
        "tasks": {
            "TASK-1": {
                "status": "integrated",
                "start_sha": SHA_B,
                "start_basis": {
                    "kind": "plan_sha",
                    "plan_revision": "plan-v1",
                    "orchestration_revision": "orch-v1",
                    "contract_revisions": ["CONTRACT-1@r1"],
                    "source_task_ids": [],
                },
                "dependency_evidence": {},
                "head_sha": SHA_C,
                "clean": True,
                "commits": [SHA_C],
                "implementation_write_authorization_basis": (
                    "用户授权 TASK-1 修改允许路径。"
                ),
                "commit_authorizations": [
                    {
                        "commit_sha": SHA_C,
                        "task_id": "TASK-1",
                        "orchestration_revision": "orch-v1",
                        "worktree": "../feature-task-1",
                        "diff_scope": ["src/task-1.py", ".ai/reports/TASK-1-summary.md"],
                        "authorization_basis": "用户授权本次 staging 和 commit。",
                    }
                ],
                "tests": {
                    "status": "passed",
                    "tested_head_sha": SHA_C,
                    "evidence_ref": evidence_ref(
                        "tests/TASK-1.json",
                        json_bytes(test_payload("task:TASK-1", SHA_C)),
                    ),
                },
                "risks": [],
                "report_ref": f"git:{SHA_C}:.ai/reports/TASK-1-summary.md",
                "review": review_record(
                    "TASK-1",
                    "reviews/TASK-1.json",
                    SHA_B,
                    SHA_C,
                    ["CONTRACT-1@r1"],
                    scope_envelope("TASK-1-scope-r1", "验证 TASK-1 实现"),
                ),
            }
        },
        "integration": {
            "status": "reviewed",
            "start_sha": SHA_B,
            "head_sha": SHA_D,
            "clean": True,
            "write_authorization_basis": "用户授权集成与冲突文件写入。",
            "commits": [SHA_D],
            "commit_authorizations": [
                {
                    "commit_sha": SHA_D,
                    "integration_id": "integration",
                    "orchestration_revision": "orch-v1",
                    "worktree": "../feature-integration",
                    "diff_scope": ["src/task-1.py"],
                    "authorization_basis": "用户授权 integration commit。",
                }
            ],
            "merged_tasks": [
                {
                    "task_id": "TASK-1",
                    "reviewed_head_sha": SHA_C,
                    "integration_head_after": SHA_D,
                    "verification": {
                        "status": "passed",
                        "tested_head_sha": SHA_D,
                        "evidence_ref": evidence_ref(
                            "tests/checkpoint-TASK-1.json",
                            json_bytes(test_payload("checkpoint:TASK-1", SHA_D)),
                        ),
                    },
                }
            ],
            "tests": {
                "status": "passed",
                "tested_head_sha": SHA_D,
                "evidence_ref": evidence_ref(
                    "tests/integration.json",
                    json_bytes(test_payload("integration", SHA_D)),
                ),
            },
            "final_review": review_record(
                "integration",
                "reviews/integration.json",
                SHA_A,
                SHA_D,
                ["CONTRACT-1@r1"],
                scope_envelope("integration-scope-r1", "验证最终集成"),
            ),
            "execution_log_ref": evidence_ref(
                "integration-execution.md",
                integration_execution_text().encode("utf-8"),
            ),
        },
        "authorizations": {
            "merge": None,
            "push": None,
            "cleanup": None,
        },
    }


def planning_contents(manifest):
    workflow = manifest["workflow_id"]
    plan = manifest["plan"]["revision"]
    orchestration = manifest["orchestration"]["revision"]
    return {
        ".ai/workflow-manifest.json": json.dumps(
            manifest,
            ensure_ascii=False,
        ).encode("utf-8"),
        ".ai/solution-record.md": (
            f"# Solution\n{workflow}\n{plan}\nsources: plan\nsupersedes: none\n"
            "## Objective\nvalue\n## Scope\nvalue\n"
            "## Acceptance Criteria\nAC-1: value\n"
            "## Verification\nTEST-TASK-1 verifies AC-1\n"
        ).encode("utf-8"),
        ".ai/orchestration-record.md": (
            f"# Orchestration\n{workflow}\n{plan}\n{orchestration}\n"
            "sources: plan\nsupersedes: none\n"
            "## Task DAG\nvalue\n## Contracts\nvalue\n## File Ownership\nvalue\n"
            "## Review and Integration\nvalue\n"
        ).encode("utf-8"),
        ".ai/merge/integration-plan.md": (
            f"# Integration\n{workflow}\n{plan}\n{orchestration}\n"
            "sources: plan\nsupersedes: none\n"
            "## Inputs\nvalue\n## Merge Order\nvalue\n"
            "## Final Verification\nvalue\n## Recovery\nvalue\n"
        ).encode("utf-8"),
        ".ai/tasks/TASK-1.md": (
            f"# TASK-1\n{workflow}\n{plan}\n{orchestration}\n"
            "reports/TASK-1-summary.md\nsources: plan\nsupersedes: none\n"
            "## Scope\nAC-1\n## Dependencies\nvalue\n"
            "## Contracts\nvalue\n## Build and Tests\nTEST-TASK-1\n"
        ).encode("utf-8"),
        ".ai/reports/TASK-1-summary.md": (
            "# Task Handoff — TASK-1\n\n"
            "- report_schema_version: `1`\n"
            "- report_status: `pending`\n"
            "- verification_status: `pending`\n"
            f"- workflow_id: `{workflow}`\n"
            "- task_id: `TASK-1`\n"
            f"- plan_revision: `{plan}`\n"
            f"- orchestration_revision: `{orchestration}`\n"
            "- branch: `feature/task-1`\n"
            "- worktree: `../feature-task-1`\n"
            f"- start_sha: `{SHA_B}`\n"
            '- contract_revisions: `["CONTRACT-1@r1"]`\n'
            "- risk_ids: `[]`\n"
            "- sources: `plan-v1`\n"
            "- supersedes: `none`\n\n"
            "## Changes\n\npending\n\n"
            "## Contracts Used\n\npending\n\n"
            "## Verification\n\npending\n\n"
            "## Not Run\n\npending\n\n"
            "## Known Risks, TODOs and Scope Drift\n\npending\n"
        ).encode("utf-8"),
        ".ai/contracts/CONTRACT-1.md": (
            f"# CONTRACT-1@r1\n{workflow}\n{plan}\n"
            "sources: plan\nsupersedes: none\n"
            "## Inputs and Outputs\nvalue\n## Invariants\nvalue\n"
            "## Error Handling\nvalue\n## Change Policy\nvalue\n"
        ).encode("utf-8"),
    }


def completed_task_report():
    return (
        "# Task Handoff — TASK-1\n\n"
        "- report_schema_version: `1`\n"
        "- report_status: `completed`\n"
        "- verification_status: `passed`\n"
        "- workflow_id: `workflow-test`\n"
        "- task_id: `TASK-1`\n"
        "- plan_revision: `plan-v1`\n"
        "- orchestration_revision: `orch-v1`\n"
        "- branch: `feature/task-1`\n"
        "- worktree: `../feature-task-1`\n"
        f"- start_sha: `{SHA_B}`\n"
        '- contract_revisions: `["CONTRACT-1@r1"]`\n'
        "- risk_ids: `[]`\n"
        "- sources: `plan-v1`\n"
        "- supersedes: `none`\n\n"
        "## Changes\n\n- modified: src/task-1.py\n\n"
        "## Contracts Used\n\nCONTRACT-1@r1\n\n"
        "## Verification\n\nTEST-TASK-1 passed with evidence.\n\n"
        "## Not Run\n\nNone.\n\n"
        "## Review and Integration Notes\n\nReady for Review.\n\n"
        "## Known Risks, TODOs and Scope Drift\n\nNone.\n"
    ).encode("utf-8")


class GitGraph:
    def __init__(self):
        self.parents = {
            SHA_A: set(),
            SHA_B: {SHA_A},
            SHA_C: {SHA_B},
            SHA_D: {SHA_B, SHA_C},
            SHA_E: set(),
        }
        planned = planning_contents(valid_manifest())
        immutable = {
            path: content
            for path, content in planned.items()
            if path != ".ai/reports/TASK-1-summary.md"
        }
        self.objects = {
            **{(SHA_B, path): content for path, content in planned.items()},
            **{(SHA_C, path): content for path, content in immutable.items()},
            **{(SHA_D, path): content for path, content in immutable.items()},
            (SHA_C, ".ai/reports/TASK-1-summary.md"): completed_task_report(),
            (SHA_D, ".ai/reports/TASK-1-summary.md"): completed_task_report(),
        }
        self.commit_paths = {
            SHA_C: ["src/task-1.py", ".ai/reports/TASK-1-summary.md"],
            SHA_D: ["src/task-1.py"],
            SHA_E: ["src/disconnected.py"],
        }

    def is_ancestor(self, base, head):
        if base == head:
            return True
        seen = set()
        pending = [head]
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            parents = self.parents.get(current, set())
            if base in parents:
                return True
            pending.extend(parents)
        return False

    def merge_base(self, base, head):
        if self.is_ancestor(base, head):
            return base
        raise AssertionError(f"no merge base for {base} and {head}")

    def object_exists(self, commit, path):
        return (commit, path) in self.objects

    def read_object(self, commit, path):
        try:
            return self.objects[(commit, path)]
        except KeyError as exc:
            raise FileNotFoundError(f"{commit}:{path}") from exc

    def list_commits(self, start, head):
        if start == head:
            return []
        if (start, head) in {
            (SHA_B, SHA_C),
            (SHA_B, SHA_D),
            (SHA_C, SHA_D),
        }:
            return [head]
        if head == SHA_E:
            return [SHA_E]
        raise ValueError(f"unknown first-parent range {start}..{head}")

    def changed_paths(self, commit):
        try:
            return list(self.commit_paths[commit])
        except KeyError as exc:
            raise ValueError(f"unknown commit {commit}") from exc


def validation_errors(
    manifest=None,
    runtime=None,
    phase="planning",
    task_id=None,
    graph=None,
    evidence_files=None,
):
    manifest = valid_manifest() if manifest is None else manifest
    runtime = valid_runtime() if runtime is None else runtime
    graph = GitGraph() if graph is None else graph
    evidence_files = (
        valid_evidence_files() if evidence_files is None else evidence_files
    )

    def read_evidence(path):
        try:
            return evidence_files[path]
        except KeyError as exc:
            raise FileNotFoundError(path) from exc

    kwargs = {}
    if phase != "planning":
        kwargs = {
            "is_ancestor": graph.is_ancestor,
            "merge_base": graph.merge_base,
            "object_exists": graph.object_exists,
            "read_object": graph.read_object,
            "evidence_reader": read_evidence,
            "list_commits": graph.list_commits,
            "changed_paths": graph.changed_paths,
        }
    return validator.validate_state(
        manifest,
        runtime,
        phase=phase,
        task_id=task_id,
        **kwargs,
    )


def error_codes(*args, **kwargs):
    return {item.code for item in validation_errors(*args, **kwargs)}


def write_planning_artifacts(root, manifest):
    ai_root = root / ".ai"
    for relative in ("tasks", "contracts", "reports", "merge"):
        (ai_root / relative).mkdir(parents=True, exist_ok=True)
    for path, content in planning_contents(manifest).items():
        relative = path.removeprefix(".ai/")
        (ai_root / relative).write_bytes(content)
    return ai_root


class EngineSelectionTests(unittest.TestCase):
    def test_available_specialists_are_mandatory(self):
        self.assertEqual(
            validator.select_engine("planner", "available")["engine"],
            "solution-planner",
        )
        self.assertEqual(
            validator.select_engine("review", "available")["engine"],
            "local-pr-review",
        )

    def test_fallback_only_for_absent_unloadable_or_explicit_disable(self):
        for availability in ("absent", "unloadable"):
            with self.subTest(availability=availability):
                self.assertTrue(
                    validator.select_engine("planner", availability)["fallback"]
                )
        disabled = validator.select_engine(
            "planner",
            "disabled_by_user",
            user_selected_fallback=True,
        )
        self.assertTrue(disabled["fallback"])

    def test_execution_blocked_and_unselected_disable_do_not_fallback(self):
        for availability in ("execution_blocked", "disabled_by_user"):
            with self.subTest(availability=availability):
                result = validator.select_engine("review", availability)
                self.assertFalse(result["fallback"])
                self.assertTrue(result["blocked"])

    def test_fallback_requires_reason_and_coverage(self):
        manifest = valid_manifest()
        manifest["planning_engine"] = {
            "capability": "planner",
            "primary_skill": "solution-planner",
            "selected_engine": "fallback",
            "availability": "absent",
            "selection_basis": engine_selection_basis(
                kind="capability_resolution",
                capability="planner",
                primary_skill="solution-planner",
                availability="absent",
                decision="use_fallback",
                actor="orchestrator",
                evidence="当前 catalog 中不存在 solution-planner。",
            ),
            "fallback_reason": "",
            "coverage_gap": [],
            "remaining_risk": [],
        }
        codes = error_codes(manifest=manifest)
        self.assertIn("E_FALLBACK_REASON", codes)
        self.assertIn("E_FALLBACK_COVERAGE", codes)
        self.assertIn("E_FALLBACK_RISK", codes)

    def test_disabled_specialist_requires_explicit_user_fallback_selection(self):
        manifest = valid_manifest()
        engine = manifest["review_engine"]
        engine.update(
            {
                "selected_engine": "fallback",
                "availability": "disabled_by_user",
                "selection_basis": engine_selection_basis(
                    kind="capability_resolution",
                    capability="review",
                    primary_skill="local-pr-review",
                    availability="disabled_by_user",
                    decision="use_fallback",
                    actor="user",
                    evidence="仅记录用户禁用，没有选择 fallback。",
                ),
                "fallback_reason": "用户禁用了 local-pr-review。",
                "coverage_gap": ["专职脚本能力不可用"],
                "remaining_risk": ["覆盖度降低"],
            }
        )
        self.assertIn("E_ENGINE_BLOCKED", error_codes(manifest=manifest))

    def test_disabled_specialist_accepts_bound_user_fallback_selection(self):
        manifest = valid_manifest()
        engine = manifest["review_engine"]
        engine.update(
            {
                "selected_engine": "fallback",
                "availability": "disabled_by_user",
                "selection_basis": engine_selection_basis(
                    kind="user_confirmation",
                    capability="review",
                    primary_skill="local-pr-review",
                    availability="disabled_by_user",
                    decision="use_fallback",
                    actor="user",
                    evidence="用户明确选择 review fallback。",
                ),
                "fallback_reason": "用户明确选择 review fallback。",
                "coverage_gap": ["专职脚本能力不可用"],
                "remaining_risk": ["覆盖度降低"],
            }
        )
        codes = error_codes(manifest=manifest)
        self.assertNotIn("E_ENGINE_BLOCKED", codes)
        self.assertNotIn("E_ENGINE_SELECTION", codes)
        self.assertNotIn("E_ENGINE_SELECTION_REFERENCE", codes)

    def test_disabled_fallback_rejects_unstructured_confirmation_string(self):
        manifest = valid_manifest()
        engine = manifest["review_engine"]
        engine.update(
            {
                "selected_engine": "fallback",
                "availability": "disabled_by_user",
                "selection_basis": {
                    "kind": "user_confirmation",
                    "reference": "任意非空字符串",
                },
                "fallback_reason": "用户禁用。",
                "coverage_gap": ["专职能力缺失"],
                "remaining_risk": ["覆盖度降低"],
            }
        )
        codes = error_codes(manifest=manifest)
        self.assertIn("E_TYPE", codes)
        self.assertIn("E_ENGINE_BLOCKED", codes)

    def test_planning_mode_must_match_selected_engine(self):
        manifest = valid_manifest()
        manifest["plan"]["planning_mode"] = "fallback"
        self.assertIn(
            "E_PLANNING_ENGINE_MODE",
            error_codes(manifest=manifest),
        )

    def test_engine_selection_basis_kind_matches_availability(self):
        cases = (
            ("available", "user_confirmation"),
            ("absent", "catalog"),
            ("unloadable", "catalog"),
        )
        for availability, basis_kind in cases:
            with self.subTest(availability=availability):
                manifest = valid_manifest()
                engine = manifest["planning_engine"]
                engine["availability"] = availability
                engine["selection_basis"]["kind"] = basis_kind
                if availability != "available":
                    engine["selected_engine"] = "fallback"
                    engine["fallback_reason"] = "专职 Skill 不可用。"
                    engine["coverage_gap"] = ["专职能力缺失"]
                    engine["remaining_risk"] = ["覆盖度降低"]
                    manifest["plan"]["planning_mode"] = "fallback"
                self.assertIn(
                    "E_ENGINE_SELECTION_BASIS_KIND",
                    error_codes(manifest=manifest),
                )


class PlanningAndArtifactTests(unittest.TestCase):
    def test_scope_hash_matches_local_review_canonical_algorithm(self):
        contract = scope_envelope("范围-r1", "验证中文范围")["contract"]
        canonical = json.dumps(
            contract,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.assertEqual(
            validator.canonical_scope_id(contract),
            hashlib.sha256(canonical).hexdigest(),
        )
        reordered = dict(reversed(list(contract.items())))
        self.assertEqual(
            validator.canonical_scope_id(contract),
            validator.canonical_scope_id(reordered),
        )
        changed = copy.deepcopy(contract)
        changed["in_scope"].append("新增范围")
        self.assertNotEqual(
            validator.canonical_scope_id(contract),
            validator.canonical_scope_id(changed),
        )

    def test_scope_content_drift_requires_a_new_scope_id(self):
        manifest = valid_manifest()
        manifest["tasks"][0]["review_scope"]["contract"]["objective"] = "已改变目标"
        self.assertIn(
            "E_REVIEW_SCOPE_HASH",
            error_codes(manifest=manifest),
        )

    def test_review_scope_must_reference_current_plan_revision(self):
        manifest = valid_manifest()
        scope = manifest["tasks"][0]["review_scope"]
        scope["contract"]["sources"][0]["revision"] = "plan-old"
        scope["scope_id"] = validator.canonical_scope_id(scope["contract"])
        self.assertIn(
            "E_REVIEW_SCOPE_PLAN_SOURCE",
            error_codes(manifest=manifest),
        )

    def test_planning_source_mapping_is_auditable(self):
        manifest = valid_manifest()
        manifest["plan"]["planning_mode"] = "existing_plan_review"
        self.assertIn(
            "E_PLAN_SOURCE_MAPPING",
            error_codes(manifest=manifest),
        )

    def test_effective_confirmed_plan_is_required(self):
        cases = (
            ("contract_status", "待澄清", "E_PLAN_CONTRACT"),
            ("quality_status", "阻塞", "E_PLAN_QUALITY"),
            ("effective_decision_status", "待确认", "E_PLAN_DECISION"),
            ("effective_confirmation_basis", "", "E_PLAN_CONFIRMATION"),
        )
        for field, value, expected in cases:
            with self.subTest(field=field):
                manifest = valid_manifest()
                manifest["plan"][field] = value
                self.assertIn(expected, error_codes(manifest=manifest))

    def test_plan_revision_mismatch_is_rejected(self):
        runtime = valid_runtime()
        runtime["plan_revision"] = "plan-old"
        self.assertIn("E_RUNTIME_PLAN", error_codes(runtime=runtime))

    def test_conditional_plan_requires_closed_conditions_before_dispatch(self):
        manifest = valid_manifest()
        manifest["plan"]["quality_status"] = "有条件通过"
        manifest["plan"]["conditions"] = [
            {
                "id": "COND-1",
                "owner": "Owner",
                "close_condition": "Evidence exists",
                "must_close_before": "dispatch",
                "applies_to": ["TASK-1"],
                "required_proof_kinds": ["test_result"],
                "proof_requirements": [condition_test_requirement()],
            }
        ]
        runtime = valid_runtime()
        runtime["conditions"] = [
            {"id": "COND-1", "status": "open", "evidence_ref": None}
        ]
        runtime["tasks"]["TASK-1"]["status"] = "ready"
        self.assertIn(
            "E_CONDITION_OPEN",
            error_codes(manifest=manifest, runtime=runtime, phase="dispatch"),
        )

    def test_closed_condition_requires_structured_bound_proof(self):
        manifest, runtime, files, _ = closed_test_condition_case()
        codes = error_codes(
            manifest=manifest,
            runtime=runtime,
            phase="dispatch",
            evidence_files=files,
        )
        self.assertFalse(
            {code for code in codes if code.startswith("E_CONDITION")},
            codes,
        )

    def test_condition_proof_identity_is_derived_from_payload(self):
        manifest, runtime, files, payload = closed_test_condition_case()
        payload["closure"]["proofs"][0]["identity"] = "tampered"
        raw = json_bytes(payload)
        files["conditions/COND-1.json"] = raw
        runtime["conditions"][0]["evidence_ref"] = evidence_ref(
            "conditions/COND-1.json",
            raw,
        )
        self.assertIn(
            "E_CONDITION_PROOF_IDENTITY",
            error_codes(
                manifest=manifest,
                runtime=runtime,
                phase="dispatch",
                evidence_files=files,
            ),
        )

    def test_condition_test_proof_must_use_predeclared_head_and_command(self):
        manifest, runtime, files, condition = closed_test_condition_case()
        proof_payload = test_payload("condition:COND-1", SHA_E)
        proof_payload["commands"][0]["command"] = "unplanned command"
        normalized = [
            {
                "test_id": proof_payload["commands"][0]["test_id"],
                "command": proof_payload["commands"][0]["command"],
            }
        ]
        identity = (
            f"test_result:condition:COND-1@{SHA_E}#"
            f"{validator.canonical_json_sha256(normalized)}"
        )
        replace_condition_proof(
            manifest,
            runtime,
            files,
            condition,
            kind="test_result",
            payload=proof_payload,
            identity=identity,
            path="conditions/proofs/COND-1-unplanned-test.json",
            requirement=condition_test_requirement(),
        )
        self.assertIn(
            "E_CONDITION_PROOF_CONTENT",
            error_codes(
                manifest=manifest,
                runtime=runtime,
                phase="dispatch",
                evidence_files=files,
            ),
        )

    def test_condition_proof_cannot_bind_untrusted_integration_head(self):
        manifest, _, _, _ = closed_test_condition_case()
        manifest["plan"]["conditions"][0]["proof_requirements"][0][
            "head_source"
        ] = {"kind": "integration_head"}
        self.assertIn(
            "E_CONDITION_PROOF_REQUIREMENT",
            error_codes(manifest=manifest),
        )

    def test_condition_proof_kinds_must_match_plan(self):
        manifest, runtime, files, _ = closed_test_condition_case()
        manifest["plan"]["conditions"][0]["required_proof_kinds"] = [
            "artifact_result"
        ]
        self.assertIn(
            "E_CONDITION_PROOF_COVERAGE",
            error_codes(
                manifest=manifest,
                runtime=runtime,
                phase="dispatch",
                evidence_files=files,
            ),
        )

    def test_condition_scope_is_nonempty_and_unique(self):
        for applies_to in ([], ["TASK-1", "TASK-1"]):
            with self.subTest(applies_to=applies_to):
                manifest, runtime, files, _ = closed_test_condition_case()
                manifest["plan"]["conditions"][0]["applies_to"] = applies_to
                self.assertIn(
                    "E_CONDITION_SCOPE",
                    error_codes(
                        manifest=manifest,
                        runtime=runtime,
                        phase="dispatch",
                        evidence_files=files,
                    ),
                )

    def test_integration_ready_checks_task_scoped_due_conditions(self):
        manifest = valid_manifest()
        manifest["plan"]["quality_status"] = "有条件通过"
        manifest["plan"]["conditions"] = [
            {
                "id": "COND-1",
                "owner": "Owner",
                "close_condition": "Evidence exists",
                "must_close_before": "integration",
                "applies_to": ["TASK-1"],
                "required_proof_kinds": ["test_result"],
                "proof_requirements": [condition_test_requirement()],
            }
        ]
        runtime = valid_runtime()
        runtime["workflow_status"] = "branches_ready"
        runtime["transition_history"] = completed_workflow_history()[:8]
        runtime["conditions"] = [
            {"id": "COND-1", "status": "open", "evidence_ref": None}
        ]
        self.assertIn(
            "E_CONDITION_OPEN",
            error_codes(
                manifest=manifest,
                runtime=runtime,
                phase="integration-ready",
            ),
        )

    def test_runtime_condition_ids_are_unique(self):
        manifest, runtime, files, _ = closed_test_condition_case()
        runtime["conditions"].append(copy.deepcopy(runtime["conditions"][0]))
        self.assertIn(
            "E_RUNTIME_CONDITION_DUPLICATE",
            error_codes(
                manifest=manifest,
                runtime=runtime,
                phase="dispatch",
                evidence_files=files,
            ),
        )

    def test_condition_external_and_confirmation_proofs_are_bound(self):
        cases = []
        external = {
            "schema_version": 1,
            "workflow_id": "workflow-test",
            "plan_revision": "plan-v1",
            "orchestration_revision": "orch-v1",
            "subject_id": "condition:COND-1",
            "status": "verified",
            "evidence_id": "EVIDENCE-1",
            "revision": "r1",
            "result": "外部约束已满足。",
        }
        external_digest = validator.canonical_json_sha256(
            {
                "subject_id": "condition:COND-1",
                "result": external["result"],
            }
        )
        cases.append(
            (
                "external_evidence",
                external,
                f"external_evidence:EVIDENCE-1@r1#{external_digest}",
                "conditions/proofs/COND-1-external.json",
                {
                    "kind": "external_evidence",
                    "evidence_id": "EVIDENCE-1",
                    "revision": "r1",
                    "result_sha256": hashlib.sha256(
                        external["result"].encode("utf-8")
                    ).hexdigest(),
                },
            )
        )
        confirmation = {
            "schema_version": 1,
            "workflow_id": "workflow-test",
            "plan_revision": "plan-v1",
            "orchestration_revision": "orch-v1",
            "subject_id": "condition:COND-1",
            "status": "confirmed",
            "condition_id": "COND-1",
            "close_condition": "Evidence exists",
            "confirmation_id": "CONFIRM-1",
            "revision": "r1",
            "confirmation_basis": "用户确认该条件已满足。",
            "confirmed_by": "user",
            "confirmed_at": "2026-07-30T12:00:00+08:00",
        }
        cases.append(
            (
                "user_confirmation",
                confirmation,
                "user_confirmation:condition:COND-1:CONFIRM-1@r1",
                "conditions/proofs/COND-1-confirmation.json",
                {
                    "kind": "user_confirmation",
                    "confirmation_id": "CONFIRM-1",
                    "revision": "r1",
                    "confirmed_by": "user",
                },
            )
        )
        for kind, proof_payload, identity, path, requirement in cases:
            with self.subTest(kind=kind):
                manifest, runtime, files, condition = closed_test_condition_case()
                replace_condition_proof(
                    manifest,
                    runtime,
                    files,
                    condition,
                    kind=kind,
                    payload=proof_payload,
                    identity=identity,
                    path=path,
                    requirement=requirement,
                )
                codes = error_codes(
                    manifest=manifest,
                    runtime=runtime,
                    phase="dispatch",
                    evidence_files=files,
                )
                self.assertFalse(
                    {code for code in codes if code.startswith("E_CONDITION")},
                    codes,
                )

    def test_condition_artifact_proof_reads_fixed_git_blob(self):
        manifest, runtime, files, condition = closed_test_condition_case()
        artifact_raw = b"condition artifact"
        artifact = {
            "schema_version": 1,
            "workflow_id": "workflow-test",
            "plan_revision": "plan-v1",
            "orchestration_revision": "orch-v1",
            "subject_id": "condition:COND-1",
            "status": "available",
            "source_sha": SHA_B,
            "artifacts": [
                {
                    "path": "artifacts/condition.txt",
                    "sha256": hashlib.sha256(artifact_raw).hexdigest(),
                }
            ],
        }
        artifact_digest = validator.canonical_json_sha256(artifact["artifacts"])
        identity = (
            f"artifact_result:condition:COND-1@{SHA_B}#{artifact_digest}"
        )
        replace_condition_proof(
            manifest,
            runtime,
            files,
            condition,
            kind="artifact_result",
            payload=artifact,
            identity=identity,
            path="conditions/proofs/COND-1-artifact.json",
            requirement={
                "kind": "artifact_result",
                "head_source": {"kind": "plan_sha"},
                "paths": ["artifacts/condition.txt"],
            },
        )
        graph = GitGraph()
        graph.objects[(SHA_B, "artifacts/condition.txt")] = artifact_raw
        codes = error_codes(
            manifest=manifest,
            runtime=runtime,
            phase="dispatch",
            graph=graph,
            evidence_files=files,
        )
        self.assertFalse(
            {code for code in codes if code.startswith("E_CONDITION")},
            codes,
        )

    def test_acceptance_criteria_require_task_and_test_coverage(self):
        manifest = valid_manifest()
        manifest["plan"]["acceptance_criteria"].append(
            {
                "id": "AC-2",
                "statement": "第二项可观察行为。",
                "source": {
                    "kind": "plan",
                    "reference": ".ai/solution-record.md",
                    "revision": "plan-v1",
                },
            }
        )
        codes = error_codes(manifest=manifest)
        self.assertIn("E_ACCEPTANCE_TASK_COVERAGE", codes)
        self.assertIn("E_ACCEPTANCE_TEST_COVERAGE", codes)

    def test_task_test_cannot_claim_an_unassigned_acceptance_criterion(self):
        manifest = valid_manifest()
        manifest["tasks"][0]["required_tests"][0]["acceptance_criteria"] = [
            "AC-UNKNOWN"
        ]
        self.assertIn(
            "E_TEST_ACCEPTANCE_CRITERIA",
            error_codes(manifest=manifest),
        )

    def test_review_scope_cannot_narrow_task_acceptance_criteria(self):
        manifest = valid_manifest()
        scope = manifest["tasks"][0]["review_scope"]
        scope["contract"]["acceptance_criteria"] = []
        scope["scope_id"] = validator.canonical_scope_id(scope["contract"])
        self.assertIn(
            "E_TASK_REVIEW_AC_COVERAGE",
            error_codes(manifest=manifest),
        )

    def test_planned_test_ids_are_globally_unique(self):
        manifest = valid_manifest()
        manifest["integration"]["required_tests"][0]["id"] = "TEST-TASK-1"
        self.assertIn(
            "E_TEST_ID_GLOBAL_DUPLICATE",
            error_codes(manifest=manifest),
        )

    def test_transition_history_preserves_old_revisions_across_rebaseline(self):
        runtime = valid_runtime()
        old_statuses = (
            "planning",
            "plan_approved",
            "orchestration_draft",
            "orchestration_approved",
            "docs_ready",
            "plan_baselined",
            "implementing",
            "branch_reviewing",
        )
        history = [
            {
                "event": "advance",
                "from_status": from_status,
                "to_status": to_status,
                "plan_revision": "plan-v0",
                "orchestration_revision": "orch-v0",
                "evidence_refs": [f"old:{index}"],
            }
            for index, (from_status, to_status) in enumerate(
                zip(old_statuses, old_statuses[1:])
            )
        ]
        history.append(
            {
                "event": "rebaseline",
                "from_status": "branch_reviewing",
                "to_status": "plan_baselined",
                "plan_revision": "plan-v1",
                "orchestration_revision": "orch-v1",
                "evidence_refs": ["rebaseline:confirmed"],
                "reason": "Review Scope Contract changed",
                "previous_plan_revision": "plan-v0",
                "previous_orchestration_revision": "orch-v0",
                "previous_plan_sha": SHA_A,
                "new_plan_sha": SHA_B,
                "invalidated_task_ids": ["TASK-1"],
            }
        )
        new_statuses = (
            "plan_baselined",
            "implementing",
            "branch_reviewing",
            "branches_ready",
            "integrating",
            "integration_reviewing",
            "merge_ready",
        )
        history.extend(
            {
                "event": "advance",
                "from_status": from_status,
                "to_status": to_status,
                "plan_revision": "plan-v1",
                "orchestration_revision": "orch-v1",
                "evidence_refs": [f"new:{index}"],
            }
            for index, (from_status, to_status) in enumerate(
                zip(new_statuses, new_statuses[1:])
            )
        )
        runtime["transition_history"] = history
        errors = []
        validator._validate_workflow_control(
            valid_manifest(),
            runtime,
            "planning",
            errors,
        )
        self.assertFalse(errors, [str(item) for item in errors])

    def test_unresolved_template_and_empty_shell_are_rejected(self):
        manifest = valid_manifest()
        runtime = valid_runtime()
        with tempfile.TemporaryDirectory() as directory:
            ai_root = write_planning_artifacts(Path(directory), manifest)
            (ai_root / "solution-record.md").write_text(
                "{{OBJECTIVE}}",
                encoding="utf-8",
            )
            codes = {
                item.code
                for item in validator.validate_state(
                    manifest,
                    runtime,
                    phase="planning",
                    ai_root=ai_root,
                )
            }
        self.assertIn("E_TEMPLATE_UNRESOLVED", codes)
        self.assertIn("E_ARTIFACT_EMPTY", codes)

    def test_planning_cli_validates_manifest_and_external_state(self):
        manifest = valid_manifest()
        runtime = valid_runtime()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            runtime_root = Path(directory) / "runtime"
            runtime_root.mkdir()
            write_planning_artifacts(root, manifest)
            state_path = runtime_root / "runtime-state.json"
            state_path.write_text(
                json.dumps(runtime, ensure_ascii=False),
                encoding="utf-8",
            )
            result = subprocess.run(
                (
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    str(root),
                    "--state",
                    str(state_path),
                    "--phase",
                    "planning",
                ),
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"status": "valid"', result.stdout)


class DependencyAndTypeTests(unittest.TestCase):
    def test_dependency_type_and_unblock_pair_is_strict(self):
        manifest = valid_manifest()
        manifest["tasks"][0]["dependencies"] = [
            {
                "id": "DEP-PAIR",
                "task_id": "TASK-1",
                "type": "code",
                "unblocks_on": "tests_passed",
                "expected_evidence": {},
            }
        ]
        self.assertIn(
            "E_DEPENDENCY_PAIR",
            error_codes(manifest=manifest),
        )

    def test_dependency_cycle_is_rejected(self):
        manifest = valid_manifest()
        second = copy.deepcopy(manifest["tasks"][0])
        second.update(
            {
                "id": "TASK-2",
                "path": "tasks/TASK-2.md",
                "report_path": "reports/TASK-2-summary.md",
                "owner": "Coder-2",
                "branch": "feature/task-2",
                "worktree": "../feature-task-2",
                "contracts": [],
            }
        )
        manifest["tasks"][0]["dependencies"] = [
            {
                "id": "DEP-2-1-CODE",
                "task_id": "TASK-2",
                "type": "code",
                "unblocks_on": "reviewed_head",
                "expected_evidence": {},
            }
        ]
        second["dependencies"] = [
            {
                "id": "DEP-1-2-CODE",
                "task_id": "TASK-1",
                "type": "code",
                "unblocks_on": "reviewed_head",
                "expected_evidence": {},
            }
        ]
        manifest["tasks"].append(second)
        runtime = valid_runtime()
        runtime["tasks"]["TASK-2"] = copy.deepcopy(runtime["tasks"]["TASK-1"])
        self.assertIn(
            "E_DEPENDENCY_CYCLE",
            error_codes(manifest=manifest, runtime=runtime),
        )

    def test_unknown_contract_is_rejected(self):
        manifest = valid_manifest()
        manifest["tasks"][0]["contracts"] = ["CONTRACT-X@r1"]
        self.assertIn(
            "E_TASK_CONTRACT_UNKNOWN",
            error_codes(manifest=manifest),
        )

    def test_malformed_json_types_return_diagnostics_not_traceback(self):
        manifest = valid_manifest()
        runtime = valid_runtime()
        manifest["schema_version"] = True
        manifest["review_engine"]["availability"] = []
        manifest["plan"]["quality_status"] = []
        manifest["plan"]["planning_mode"] = []
        manifest["plan"]["conditions"] = [
            {
                "id": "COND-1",
                "owner": "Owner",
                "close_condition": "Evidence exists",
                "must_close_before": "dispatch",
                "applies_to": ["TASK-1"],
                "required_proof_kinds": ["test_result"],
                "proof_requirements": [condition_test_requirement()],
            }
        ]
        runtime["conditions"] = [
            {"id": "COND-1", "status": [], "evidence_ref": None}
        ]
        manifest["tasks"][0]["dependencies"] = [
            {
                "id": "DEP-MALFORMED",
                "task_id": "TASK-1",
                "type": [],
                "unblocks_on": {},
                "expected_evidence": {},
            }
        ]
        runtime["tasks"]["TASK-1"]["status"] = []
        manifest["contracts"][0]["consumers"] = None
        runtime["integration"]["final_review"]["blocking_count"] = False
        diagnostics = validation_errors(manifest, runtime, phase="merge-ready")
        codes = {item.code for item in diagnostics}
        self.assertIn("E_SCHEMA_VERSION", codes)
        self.assertIn("E_ENGINE_AVAILABILITY", codes)
        self.assertIn("E_PLAN_QUALITY", codes)
        self.assertIn("E_PLANNING_MODE", codes)
        self.assertIn("E_CONDITION_STATUS", codes)
        self.assertIn("E_DEPENDENCY_KIND", codes)
        self.assertIn("E_DEPENDENCY_UNBLOCK", codes)
        self.assertIn("E_TASK_STATUS", codes)
        self.assertIn("E_CONTRACT_CONSUMER", codes)
        self.assertIn("E_REVIEW_BLOCKERS", codes)

    def test_nested_type_mutations_fail_closed_without_traceback(self):
        cases = (
            lambda manifest, runtime: manifest["planning_engine"][
                "selection_basis"
            ].update({"kind": []}),
            lambda manifest, runtime: manifest["tasks"][0].update(
                {"report_path": []}
            ),
            lambda manifest, runtime: manifest["tasks"][0].update(
                {"contracts": None}
            ),
            lambda manifest, runtime: manifest["tasks"][0].update(
                {"acceptance_criteria": [{}]}
            ),
            lambda manifest, runtime: runtime.update({"workflow_status": []}),
            lambda manifest, runtime: runtime["transition_history"][0].update(
                {"from_status": []}
            ),
            lambda manifest, runtime: runtime.update({"git": []}),
        )
        for mutate in cases:
            with self.subTest(mutate=repr(mutate)):
                manifest = valid_manifest()
                runtime = valid_runtime()
                mutate(manifest, runtime)
                diagnostics = validation_errors(
                    manifest,
                    runtime,
                    phase="merge-ready",
                )
                self.assertTrue(diagnostics)


class ReadinessTests(unittest.TestCase):
    def test_single_code_dependency_starts_from_current_reviewed_head(self):
        manifest = valid_manifest()
        task2_scope = scope_envelope("TASK-2-scope-r1", "验证 TASK-2 实现")
        task2 = {
            "id": "TASK-2",
            "path": "tasks/TASK-2.md",
            "report_path": "reports/TASK-2-summary.md",
            "owner": "Coder-2",
            "branch": "feature/task-2",
            "worktree": "../feature-task-2",
            "dependencies": [
                {
                    "id": "DEP-TASK-1-TASK-2-CODE",
                    "task_id": "TASK-1",
                    "type": "code",
                    "unblocks_on": "reviewed_head",
                    "expected_evidence": {},
                }
            ],
            "contracts": [],
            "acceptance_criteria": ["AC-1"],
            "required_tests": [
                {
                    "id": "TEST-TASK-2",
                    "command": "verify task:TASK-2",
                    "acceptance_criteria": ["AC-1"],
                }
            ],
            "allowed_paths": [
                "src/task-2.py",
                ".ai/reports/TASK-2-summary.md",
            ],
            "sources": ["plan:plan-v1", "orchestration:orch-v1"],
            "supersedes": None,
            "review_scope": task2_scope,
        }
        manifest["tasks"].append(task2)
        runtime = valid_runtime()
        runtime["tasks"]["TASK-2"] = {
            "status": "reviewed",
            "start_sha": SHA_C,
            "start_basis": {
                "kind": "reviewed_head",
                "plan_revision": "plan-v1",
                "orchestration_revision": "orch-v1",
                "contract_revisions": [],
                "source_task_ids": ["TASK-1"],
            },
            "dependency_evidence": {
                "DEP-TASK-1-TASK-2-CODE": {
                    "status": "satisfied",
                    "source_sha": SHA_C,
                    "evidence_ref": None,
                }
            },
            "head_sha": SHA_D,
            "clean": True,
            "commits": [SHA_D],
            "implementation_write_authorization_basis": "用户授权 TASK-2 写入。",
            "commit_authorizations": [
                {
                    "commit_sha": SHA_D,
                    "task_id": "TASK-2",
                    "orchestration_revision": "orch-v1",
                    "worktree": "../feature-task-2",
                    "diff_scope": [
                        "src/task-2.py",
                        ".ai/reports/TASK-2-summary.md",
                    ],
                    "authorization_basis": "用户授权 TASK-2 commit。",
                }
            ],
            "tests": {
                "status": "passed",
                "tested_head_sha": SHA_D,
                "evidence_ref": evidence_ref(
                    "tests/TASK-2.json",
                    json_bytes(test_payload("task:TASK-2", SHA_D)),
                ),
            },
            "risks": [],
            "report_ref": f"git:{SHA_D}:.ai/reports/TASK-2-summary.md",
            "review": review_record(
                "TASK-2",
                "reviews/TASK-2.json",
                SHA_C,
                SHA_D,
                [],
                task2_scope,
            ),
        }
        graph = GitGraph()
        manifest_raw = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
        task_doc = (
            "# TASK-2\nworkflow-test\nplan-v1\norch-v1\n"
            "reports/TASK-2-summary.md\nsources: plan\nsupersedes: none\n"
            "## Scope\nAC-1\n## Dependencies\nvalue\n"
            "## Contracts\nvalue\n## Build and Tests\nTEST-TASK-2\n"
        ).encode("utf-8")
        report_skeleton = (
            "# Task Handoff — TASK-2\n"
            "workflow-test\nplan-v1\norch-v1\nsources\nsupersedes\n"
            "## Verification\npending\n## Not Run\npending\n"
            "## Known Risks\npending\n"
        ).encode("utf-8")
        report_complete = (
            "# Task Handoff — TASK-2\n\n"
            "- report_schema_version: `1`\n"
            "- report_status: `completed`\n"
            "- verification_status: `passed`\n"
            "- workflow_id: `workflow-test`\n"
            "- task_id: `TASK-2`\n"
            "- plan_revision: `plan-v1`\n"
            "- orchestration_revision: `orch-v1`\n"
            "- branch: `feature/task-2`\n"
            "- worktree: `../feature-task-2`\n"
            f"- start_sha: `{SHA_C}`\n"
            "- contract_revisions: `[]`\n"
            "- risk_ids: `[]`\n"
            "- sources: `plan-v1`\n"
            "- supersedes: `none`\n\n"
            "## Changes\n\n- modified: src/task-2.py\n\n"
            "## Contracts Used\n\nNone.\n\n"
            "## Verification\n\nTEST-TASK-2 passed.\n\n"
            "## Not Run\n\nNone.\n\n"
            "## Known Risks, TODOs and Scope Drift\n\nNone.\n"
        ).encode("utf-8")
        for head in (SHA_B, SHA_C, SHA_D):
            graph.objects[(head, ".ai/workflow-manifest.json")] = manifest_raw
            graph.objects[(head, ".ai/tasks/TASK-2.md")] = task_doc
        graph.objects[(SHA_B, ".ai/reports/TASK-2-summary.md")] = report_skeleton
        graph.objects[(SHA_D, ".ai/reports/TASK-2-summary.md")] = report_complete
        graph.commit_paths[SHA_D] = [
            "src/task-2.py",
            ".ai/reports/TASK-2-summary.md",
        ]
        files = valid_evidence_files()
        files["reviews/TASK-2.json"] = json_bytes(
            review_payload("TASK-2", SHA_C, SHA_D, [], task2_scope)
        )
        files["tests/TASK-2.json"] = json_bytes(
            test_payload("task:TASK-2", SHA_D)
        )
        codes = error_codes(
            manifest=manifest,
            runtime=runtime,
            phase="branch-ready",
            task_id="TASK-2",
            graph=graph,
            evidence_files=files,
        )
        self.assertNotIn("E_TASK_START_ANCHOR", codes)
        self.assertNotIn("E_UPSTREAM_REVIEW_STALE", codes)

        dependency = manifest["tasks"][1]["dependencies"][0]
        dependency["type"] = "integration"
        dependency["unblocks_on"] = "integration_checkpoint"
        task2_state = runtime["tasks"]["TASK-2"]
        task2_state["status"] = "ready"
        task2_state["start_sha"] = SHA_D
        task2_state["start_basis"]["kind"] = "integration_checkpoint"
        task2_state["dependency_evidence"][
            "DEP-TASK-1-TASK-2-CODE"
        ]["source_sha"] = SHA_D
        runtime["tasks"]["TASK-1"]["status"] = "planned"
        runtime["tasks"]["TASK-1"]["review"]["status"] = "pending"
        runtime["tasks"]["TASK-1"]["review"]["reviewed_head_sha"] = None
        stale_codes = error_codes(
            manifest=manifest,
            runtime=runtime,
            phase="dispatch",
            graph=graph,
            evidence_files=files,
        )
        self.assertIn("E_UPSTREAM_REVIEW_STALE", stale_codes)

    def test_task_start_must_be_anchored_to_plan_sha(self):
        runtime = valid_runtime()
        runtime["tasks"]["TASK-1"]["start_sha"] = SHA_C
        runtime["tasks"]["TASK-1"]["review"]["review_base_sha"] = SHA_C
        runtime["tasks"]["TASK-1"]["review"]["merge_base_sha"] = SHA_C
        self.assertIn(
            "E_TASK_START_ANCHOR",
            error_codes(runtime=runtime, phase="merge-ready"),
        )

    def test_start_basis_revisions_are_current(self):
        runtime = valid_runtime()
        runtime["tasks"]["TASK-1"]["start_basis"]["plan_revision"] = "plan-old"
        self.assertIn(
            "E_TASK_START_BASIS_PLAN_STALE",
            error_codes(runtime=runtime, phase="merge-ready"),
        )

    def test_every_commit_requires_bound_authorization(self):
        runtime = valid_runtime()
        runtime["tasks"]["TASK-1"]["commit_authorizations"] = []
        self.assertIn(
            "E_COMMIT_AUTHORIZATION_COVERAGE",
            error_codes(runtime=runtime, phase="merge-ready"),
        )

    def test_declared_commits_must_match_real_first_parent_history(self):
        graph = GitGraph()
        graph.list_commits = lambda start, head: [SHA_E, head]
        self.assertIn(
            "E_COMMIT_HISTORY_STALE",
            error_codes(phase="merge-ready", graph=graph),
        )

    def test_path_authorization_is_repository_root_anchored(self):
        manifest = valid_manifest()
        runtime = valid_runtime()
        manifest["tasks"][0]["allowed_paths"] = ["task-1.py"]
        authorization = runtime["tasks"]["TASK-1"]["commit_authorizations"][0]
        authorization["diff_scope"] = ["task-1.py"]
        graph = GitGraph()
        graph.commit_paths[SHA_C] = ["secret/task-1.py"]
        self.assertIn(
            "E_COMMIT_AUTHORIZATION_PATHS",
            error_codes(
                manifest=manifest,
                runtime=runtime,
                phase="merge-ready",
                graph=graph,
            ),
        )

    def test_integration_commit_authorizations_must_be_a_list(self):
        runtime = valid_runtime()
        runtime["integration"]["commit_authorizations"] = None
        self.assertIn(
            "E_COMMIT_AUTHORIZATIONS",
            error_codes(runtime=runtime, phase="merge-ready"),
        )

    def test_commit_authorization_cannot_exceed_planned_allowed_paths(self):
        runtime = valid_runtime()
        runtime["tasks"]["TASK-1"]["commit_authorizations"][0][
            "diff_scope"
        ].append("secret.txt")
        self.assertIn(
            "E_COMMIT_AUTHORIZATION_PLAN_SCOPE",
            error_codes(runtime=runtime, phase="merge-ready"),
        )

    def test_runtime_review_scope_cannot_self_report_another_identity(self):
        runtime = valid_runtime()
        runtime["tasks"]["TASK-1"]["review"]["scope_id"] = "f" * 64
        self.assertIn(
            "E_REVIEW_SCOPE_STALE",
            error_codes(runtime=runtime, phase="merge-ready"),
        )

    def test_plan_manifest_content_is_bound_to_plan_sha(self):
        manifest = valid_manifest()
        manifest["tasks"][0]["owner"] = "Changed Owner"
        self.assertIn(
            "E_PLAN_MANIFEST_DRIFT",
            error_codes(manifest=manifest, phase="merge-ready"),
        )

    def test_immutable_contract_cannot_change_at_task_head(self):
        graph = GitGraph()
        graph.objects[(SHA_C, ".ai/contracts/CONTRACT-1.md")] = (
            b"mutated contract content"
        )
        self.assertIn(
            "E_PLAN_ARTIFACT_DRIFT",
            error_codes(phase="merge-ready", graph=graph),
        )

    def test_pending_task_report_cannot_enter_review(self):
        graph = GitGraph()
        graph.objects[(SHA_C, ".ai/reports/TASK-1-summary.md")] = graph.objects[
            (SHA_B, ".ai/reports/TASK-1-summary.md")
        ]
        codes = error_codes(phase="merge-ready", graph=graph)
        self.assertIn("E_TASK_REPORT_STALE", codes)
        self.assertIn("E_TASK_REPORT_INCOMPLETE", codes)

    def test_task_report_ref_must_bind_exact_head_and_path(self):
        runtime = valid_runtime()
        runtime["tasks"]["TASK-1"]["report_ref"] = (
            f"git:{SHA_B}:.ai/reports/TASK-1-summary.md"
        )
        self.assertIn(
            "E_TASK_REPORT_REF",
            error_codes(runtime=runtime, phase="merge-ready"),
        )

    def test_missing_review_evidence_fails_closed(self):
        files = valid_evidence_files()
        files.pop("reviews/TASK-1.json")
        self.assertIn(
            "E_EVIDENCE_MISSING",
            error_codes(phase="merge-ready", evidence_files=files),
        )

    def test_task_test_evidence_must_cover_exact_planned_commands(self):
        runtime = valid_runtime()
        files = valid_evidence_files()
        payload = test_payload("task:TASK-1", SHA_C)
        payload["commands"][0]["command"] = "verify something-else"
        raw = json_bytes(payload)
        files["tests/TASK-1-wrong-command.json"] = raw
        runtime["tasks"]["TASK-1"]["tests"]["evidence_ref"] = evidence_ref(
            "tests/TASK-1-wrong-command.json",
            raw,
        )
        self.assertIn(
            "E_TEST_EVIDENCE_COVERAGE",
            error_codes(
                runtime=runtime,
                phase="merge-ready",
                evidence_files=files,
            ),
        )

    def test_review_blockers_are_derived_from_scope_attribution_and_threshold(self):
        runtime = valid_runtime()
        files = valid_evidence_files()
        scope = valid_manifest()["tasks"][0]["review_scope"]
        payload = review_payload(
            "TASK-1",
            SHA_B,
            SHA_C,
            ["CONTRACT-1@r1"],
            scope,
        )
        payload["change_responsibility_non_blocking"] = [review_finding()]
        raw = json_bytes(payload)
        files["reviews/TASK-1-misclassified.json"] = raw
        runtime["tasks"]["TASK-1"]["review"]["report_ref"] = evidence_ref(
            "reviews/TASK-1-misclassified.json",
            raw,
        )
        codes = error_codes(
            runtime=runtime,
            phase="merge-ready",
            evidence_files=files,
        )
        self.assertIn("E_REVIEW_FINDING_CLASSIFICATION", codes)
        self.assertIn("E_REVIEW_BLOCKING_DERIVATION", codes)

    def test_review_engine_must_match_manifest_selection(self):
        runtime = valid_runtime()
        runtime["tasks"]["TASK-1"]["review"]["engine"] = "fallback"
        self.assertIn(
            "E_REVIEW_ENGINE_STALE",
            error_codes(runtime=runtime, phase="merge-ready"),
        )

    def test_remaining_risk_fails_closed(self):
        runtime = valid_runtime()
        files = valid_evidence_files()
        scope = valid_manifest()["tasks"][0]["review_scope"]
        payload = review_payload(
            "TASK-1",
            SHA_B,
            SHA_C,
            ["CONTRACT-1@r1"],
            scope,
        )
        payload["remaining_risks"] = ["尚未处置的风险"]
        raw = json_bytes(payload)
        files["reviews/TASK-1-risk.json"] = raw
        runtime["tasks"]["TASK-1"]["review"]["report_ref"] = evidence_ref(
            "reviews/TASK-1-risk.json",
            raw,
        )
        self.assertIn(
            "E_REVIEW_REMAINING_RISKS",
            error_codes(
                runtime=runtime,
                phase="merge-ready",
                evidence_files=files,
            ),
        )

    def test_p0_cannot_be_accepted_as_ordinary_risk(self):
        runtime = valid_runtime()
        files = valid_evidence_files()
        scope = valid_manifest()["tasks"][0]["review_scope"]
        payload = review_payload(
            "TASK-1",
            SHA_B,
            SHA_C,
            ["CONTRACT-1@r1"],
            scope,
        )
        payload["accepted_risks"] = [
            {
                "id": "RISK-1",
                "severity": "P0",
                "reason": "暂不修复",
                "impact": "可能影响关键数据",
                "authorization_basis": "明确治理决策",
                "authorized_by": "risk-owner",
                "plan_revision": "plan-v1",
                "orchestration_revision": "orch-v1",
                "reviewed_head_sha": SHA_C,
                "review_condition": "发布前复核",
                "follow_up_owner": "Owner",
            }
        ]
        raw = json_bytes(payload)
        files["reviews/TASK-1-p0-risk.json"] = raw
        runtime["tasks"]["TASK-1"]["review"]["report_ref"] = evidence_ref(
            "reviews/TASK-1-p0-risk.json",
            raw,
        )
        self.assertIn(
            "E_REVIEW_ACCEPTED_RISK",
            error_codes(
                runtime=runtime,
                phase="merge-ready",
                evidence_files=files,
            ),
        )

    def test_stale_test_evidence_fails_closed(self):
        runtime = valid_runtime()
        files = valid_evidence_files()
        stale = test_payload("integration", SHA_C)
        raw = json_bytes(stale)
        files["tests/stale-integration.json"] = raw
        runtime["integration"]["tests"]["evidence_ref"] = evidence_ref(
            "tests/stale-integration.json",
            raw,
        )
        self.assertIn(
            "E_TEST_EVIDENCE_STALE",
            error_codes(
                runtime=runtime,
                phase="merge-ready",
                evidence_files=files,
            ),
        )

    def test_external_evidence_path_cannot_escape_runtime_directory(self):
        runtime = valid_runtime()
        runtime["integration"]["tests"]["evidence_ref"] = {
            "path": "../outside.json",
            "sha256": "0" * 64,
        }
        self.assertIn(
            "E_EVIDENCE_PATH",
            error_codes(runtime=runtime, phase="merge-ready"),
        )

    def test_missing_integration_execution_fails_closed(self):
        files = valid_evidence_files()
        files.pop("integration-execution.md")
        self.assertIn(
            "E_EVIDENCE_MISSING",
            error_codes(phase="merge-ready", evidence_files=files),
        )

    def test_task_must_be_integrated_for_merge_ready(self):
        runtime = valid_runtime()
        runtime["tasks"]["TASK-1"]["status"] = "stale"
        self.assertIn(
            "E_TASK_PHASE_STATUS",
            error_codes(runtime=runtime, phase="merge-ready"),
        )

    def test_fix_invalidates_previous_review(self):
        runtime = valid_runtime()
        runtime["tasks"]["TASK-1"]["status"] = "reviewed"
        runtime["tasks"]["TASK-1"]["head_sha"] = SHA_D
        runtime["tasks"]["TASK-1"]["commits"].append(SHA_D)
        self.assertIn(
            "E_REVIEW_STALE",
            error_codes(runtime=runtime, phase="branch-ready", task_id="TASK-1"),
        )

    def test_review_is_bound_to_plan_scope_contracts_and_task_base(self):
        runtime = valid_runtime()
        review = runtime["tasks"]["TASK-1"]["review"]
        review["plan_revision"] = "plan-old"
        review["contract_revisions"] = []
        review["review_base_sha"] = SHA_A
        codes = error_codes(runtime=runtime, phase="merge-ready")
        self.assertIn("E_REVIEW_PLAN_STALE", codes)
        self.assertIn("E_REVIEW_CONTRACT_STALE", codes)
        self.assertIn("E_REVIEW_BASE_STALE", codes)

    def test_task_report_must_exist_in_reviewed_head(self):
        graph = GitGraph()
        graph.objects.pop((SHA_C, ".ai/reports/TASK-1-summary.md"))
        errors = validator.validate_state(
            valid_manifest(),
            valid_runtime(),
            phase="merge-ready",
            is_ancestor=graph.is_ancestor,
            merge_base=graph.merge_base,
            object_exists=graph.object_exists,
            read_object=graph.read_object,
            evidence_reader=read_valid_evidence,
            list_commits=graph.list_commits,
            changed_paths=graph.changed_paths,
        )
        self.assertIn("E_TASK_REPORT_MISSING", {item.code for item in errors})

    def test_integration_must_contain_exact_reviewed_task_heads(self):
        runtime = valid_runtime()
        runtime["integration"]["merged_tasks"][0]["reviewed_head_sha"] = SHA_E
        self.assertIn(
            "E_INTEGRATED_TASK_STALE",
            error_codes(runtime=runtime, phase="merge-ready"),
        )

    def test_disconnected_integration_commit_graph_is_rejected(self):
        runtime = valid_runtime()
        runtime["integration"]["head_sha"] = SHA_E
        runtime["integration"]["tests"]["tested_head_sha"] = SHA_E
        runtime["integration"]["final_review"]["reviewed_head_sha"] = SHA_E
        runtime["integration"]["merged_tasks"][0]["integration_head_after"] = SHA_E
        codes = error_codes(runtime=runtime, phase="merge-ready")
        self.assertIn("E_INTEGRATED_TASK_ANCESTRY", codes)
        self.assertIn("E_INTEGRATION_ANCESTRY", codes)

    def test_integration_head_change_invalidates_tests_and_review(self):
        runtime = valid_runtime()
        runtime["integration"]["head_sha"] = SHA_E
        codes = error_codes(runtime=runtime, phase="merge-ready")
        self.assertIn("E_INTEGRATION_TESTS_STALE", codes)
        self.assertIn("E_REVIEW_STALE", codes)

    def test_git_proof_is_required_for_formal_phases(self):
        errors = validator.validate_state(
            valid_manifest(),
            valid_runtime(),
            phase="merge-ready",
        )
        self.assertIn("E_GIT_PROOF_REQUIRED", {item.code for item in errors})
        self.assertEqual(
            validator.derive_merge_readiness(
                valid_manifest(),
                valid_runtime(),
            ),
            "NOT_READY",
        )

    def test_valid_development_evidence_does_not_claim_final_pr_readiness(self):
        graph = GitGraph()
        manifest = valid_manifest()
        runtime = valid_runtime()
        self.assertEqual(
            validator.derive_merge_readiness(
                manifest,
                runtime,
                is_ancestor=graph.is_ancestor,
                merge_base=graph.merge_base,
                object_exists=graph.object_exists,
                read_object=graph.read_object,
                evidence_reader=read_valid_evidence,
                list_commits=graph.list_commits,
                changed_paths=graph.changed_paths,
            ),
            "DEVELOPMENT_READY",
        )
        self.assertFalse(
            validator.authorization_allows(runtime, "merge", SHA_D)
        )
        self.assertFalse(
            validator.authorization_allows(runtime, "push", SHA_D)
        )


if __name__ == "__main__":
    unittest.main()

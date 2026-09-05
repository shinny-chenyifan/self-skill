import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_execution.py"
SPEC = importlib.util.spec_from_file_location("execution_validator", SCRIPT)
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.code = self.write("code.txt", "implemented behavior")
        self.evidence = self.write("test.txt", "command: verify; observed: expected behavior")
        self.final_evidence = self.write("final.txt", "Re-read original request; each AC traced to code and test evidence.")
        request = "Original request: implement ten independent items.\n" + "".join(f"- REQ-{i}: implement item {i}\n" for i in range(1, 11))
        original = self.write("original.md", request)
        self.state = {
            "schema_version": 1, "plan_revision": "plan-v1", "objective": "ten items",
            "original_request": original,
            "requirements": [{"id": f"REQ-{i}", "acceptance_criteria": [f"AC-{i}"]} for i in range(1, 11)],
            "acceptance_criteria": [{"id": f"AC-{i}", "statement": f"item {i}", "steps": [f"STEP-{i}"]} for i in range(1, 11)],
            "steps": [{"id": f"STEP-{i}", "status": "VERIFIED", "tests": ["TEST-1"], "implementation": [self.code]} for i in range(1, 11)],
            "tests": [{"id": "TEST-1", "command": "verify", "expected": "ten behaviors", "status": "passed", "plan_revision": "plan-v1", "snapshot": [self.code], "evidence": self.evidence}],
            "current": {"phase": "complete", "milestone": "final", "status": "COMPLETE", "last_completed": "all verified", "remaining": [], "blockers": [], "next_action": "request authorized delivery"},
            "decisions": [],
            "final_verification": {"status": "passed", "plan_revision": "plan-v1", "acceptance_criteria": [f"AC-{i}" for i in range(1, 11)], "snapshot": [self.code], "evidence": self.final_evidence},
        }

    def write(self, name, content):
        raw = content.encode("utf-8")
        (self.root / name).write_bytes(raw)
        return {"path": name, "sha256": hashlib.sha256(raw).hexdigest()}

    def check(self, state=None, complete=True):
        text = "# Active plan\n```execution-state\n" + json.dumps(state or self.state) + "\n```\n"
        return validator.validate(text, self.root, complete)

    def test_complete_and_fresh_reader_can_reconstruct_state(self):
        recovered = self.check(json.loads(json.dumps(self.state)))
        self.assertEqual(len(recovered["requirements"]), 10)
        self.assertEqual(recovered["current"]["next_action"], "request authorized delivery")

    def test_ten_required_nine_implemented(self):
        self.state["steps"][-1]["status"] = "NOT_STARTED"
        with self.assertRaisesRegex(ValueError, "remaining inventory"):
            self.check()

    def test_requirement_omitted_during_initial_registration(self):
        for key in ("requirements", "acceptance_criteria", "steps"):
            self.state[key].pop()
        with self.assertRaisesRegex(ValueError, "original requirement inventory"):
            self.check()

    def test_some_tests_pass_but_required_check_unverified(self):
        self.state["tests"][0]["status"] = "pending"
        with self.assertRaisesRegex(ValueError, "unverified"):
            self.check()

    def test_implementation_is_not_final_verification(self):
        self.state["steps"][-1]["status"] = "IMPLEMENTED"
        self.state["current"]["remaining"] = ["STEP-10"]
        with self.assertRaisesRegex(ValueError, "unfinished"):
            self.check()

    def test_stale_code_and_test_log_block_recovery(self):
        for name in ("code.txt", "test.txt", "original.md", "final.txt"):
            with self.subTest(name=name):
                original = (self.root / name).read_bytes()
                (self.root / name).write_text("changed", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "stale evidence"):
                    self.check()
                (self.root / name).write_bytes(original)

    def test_same_bytes_but_different_snapshot_path_is_rejected(self):
        other = self.write("other.txt", "implemented behavior")
        self.state["tests"][0]["snapshot"] = [other]
        with self.assertRaisesRegex(ValueError, "wrong implementation snapshot"):
            self.check()

    def test_changed_plan_invalidates_old_verification(self):
        self.state["plan_revision"] = "plan-v2"
        with self.assertRaisesRegex(ValueError, "stale plan revision"):
            self.check()

    def test_pending_scope_drift_cannot_complete(self):
        self.state["decisions"] = [{"id": "DEC-1", "reason": "new requirement", "impact": "STEP-10", "affected_steps": ["STEP-10"], "plan_revision": "plan-v2", "scope_change": True, "status": "pending"}]
        with self.assertRaisesRegex(ValueError, "deviation needs blocker"):
            self.check()

    def test_approved_scope_change_requires_active_revision_update(self):
        self.state["decisions"] = [{"id": "DEC-1", "reason": "new behavior", "impact": "STEP-10 changed", "affected_steps": ["STEP-10"], "plan_revision": "plan-v2", "scope_change": True, "status": "approved", "confirmation_basis": "user confirmed v2"}]
        with self.assertRaisesRegex(ValueError, "latest approved scope revision"):
            self.check()

    def test_pending_scope_change_invalidates_affected_verified_step(self):
        self.state["current"].update(phase="implementation", status="IN_PROGRESS", blockers=["DEC-1 awaiting approval"])
        self.state["decisions"] = [{"id": "DEC-1", "reason": "new behavior", "impact": "STEP-10 changed", "affected_steps": ["STEP-10"], "plan_revision": "plan-v2", "scope_change": True, "status": "pending"}]
        with self.assertRaisesRegex(ValueError, "must block affected steps"):
            self.check(complete=False)

    def test_blocked_step_needs_recovery_condition(self):
        self.state["steps"][-1].update(status="BLOCKED", blocker="fixture missing")
        with self.assertRaisesRegex(ValueError, "recovery condition"):
            self.check(complete=False)

    @unittest.skipIf(os.name == "nt", "POSIX executable modes")
    def test_declared_mode_change_invalidates_snapshot(self):
        (self.root / "code.txt").chmod(0o644)
        self.code["mode"] = "0644"
        self.check()
        (self.root / "code.txt").chmod(0o755)
        with self.assertRaisesRegex(ValueError, "stale file mode"):
            self.check()

    def test_final_pass_cannot_omit_acceptance(self):
        self.state["final_verification"]["acceptance_criteria"].pop()
        with self.assertRaisesRegex(ValueError, "final AC coverage"):
            self.check()

    def test_missing_next_action_and_duplicate_state_block(self):
        self.state["current"]["next_action"] = ""
        with self.assertRaisesRegex(ValueError, "next_action"):
            self.check()
        with self.assertRaisesRegex(ValueError, "one execution-state"):
            validator.validate("```execution-state\n{}\n```\n" * 2, self.root)

    def test_mid_task_resume_keeps_all_remaining_items(self):
        for step in self.state["steps"]:
            step["status"] = "NOT_STARTED"
            step["implementation"] = []
        self.state["tests"][0]["status"] = "pending"
        self.state["final_verification"] = None
        self.state["current"].update(phase="implementation", milestone="STEP-1", status="IN_PROGRESS", remaining=[f"STEP-{i}" for i in range(1, 11)], next_action="implement item 1 and run verify")
        self.assertEqual(len(self.check(complete=False)["current"]["remaining"]), 10)

    def test_deleted_file_snapshot_checks_actual_absence(self):
        deleted = {"path": "removed.txt", "deleted": True}
        for step in self.state["steps"]:
            step["implementation"].append(deleted)
        self.state["tests"][0]["snapshot"].append(deleted)
        self.state["final_verification"]["snapshot"].append(deleted)
        self.check()
        self.write("removed.txt", "still here")
        with self.assertRaisesRegex(ValueError, "still exists"):
            self.check()

    def test_evidence_path_escape_rejected(self):
        self.state["tests"][0]["evidence"]["path"] = "../elsewhere"
        with self.assertRaisesRegex(ValueError, "unsafe evidence"):
            self.check()

    def test_unknown_or_duplicate_ids_rejected(self):
        altered = copy.deepcopy(self.state)
        altered["steps"][0]["tests"] = ["TEST-unknown"]
        with self.assertRaisesRegex(ValueError, "unknown reference"):
            self.check(altered)
        self.state["requirements"].append(copy.deepcopy(self.state["requirements"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate ID"):
            self.check()


if __name__ == "__main__":
    unittest.main()

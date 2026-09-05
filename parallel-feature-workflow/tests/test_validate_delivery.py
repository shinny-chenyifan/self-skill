import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_validate_workflow as fixtures


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_delivery.py"
SPEC = importlib.util.spec_from_file_location("delivery_validator", SCRIPT)
delivery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(delivery)

BASE, PLAN, TASK, SOURCE, HEAD = (fixtures.SHA_A, fixtures.SHA_B, fixtures.SHA_C, fixtures.SHA_D, fixtures.SHA_E)
MID = "f" * 40


class DeliveryGraph(fixtures.GitGraph):
    def __init__(self):
        super().__init__()
        self.trees = {
            BASE: {"src/task-1.py": ("100644", "blob", "1" * 40)},
            SOURCE: {"src/task-1.py": ("100755", "blob", "2" * 40), ".ai/plan.md": ("100644", "blob", "3" * 40)},
            HEAD: {"src/task-1.py": ("100755", "blob", "2" * 40)},
        }
        self.delivery_commits = [HEAD]
        self.delivery_parents = {HEAD: [BASE]}
        self.checkout_head = HEAD
        self.dirty = False

    def verify_checkout(self, head):
        delivery.require(self.checkout_head == head, "delivery checkout HEAD differs from recorded SHA")
        delivery.require(not self.dirty, "delivery worktree is dirty")

    def is_ancestor(self, base, head):
        if head == HEAD:
            return base in {BASE, HEAD}
        return super().is_ancestor(base, head)

    def merge_base(self, base, head):
        if head == HEAD:
            return BASE
        return super().merge_base(base, head)

    def tree(self, sha):
        return self.trees[sha]

    def introduced_commits(self, base, head):
        return self.delivery_commits

    def commit_parents(self, sha):
        return self.delivery_parents[sha]

    def changed_paths(self, sha):
        if sha in self.delivery_commits:
            return ["src/task-1.py"]
        return super().changed_paths(sha)


class ContentTests(unittest.TestCase):
    def setUp(self):
        self.git = DeliveryGraph()

    def test_clean_export_matches_full_source_tree(self):
        self.assertEqual(delivery.verify_content(BASE, SOURCE, HEAD, self.git), [HEAD])

    def test_content_mode_deletion_and_unexpected_file_are_detected(self):
        mutations = [
            {"src/task-1.py": ("100644", "blob", "2" * 40)},
            {"src/task-1.py": ("100755", "blob", "9" * 40)},
            {},
            dict(self.git.trees[HEAD], unexpected=("100644", "blob", "8" * 40)),
        ]
        for tree in mutations:
            with self.subTest(tree=tree):
                self.git.trees[HEAD] = tree
                with self.assertRaisesRegex(ValueError, "delivery tree differs"):
                    delivery.verify_content(BASE, SOURCE, HEAD, self.git)

    def test_ai_in_intermediate_or_side_parent_commit_cannot_be_hidden(self):
        self.git.delivery_commits = [HEAD, MID]
        self.git.trees[MID] = {".ai/secret": ("100644", "blob", "7" * 40)}
        self.git.delivery_parents[HEAD] = [BASE, MID]
        self.git.delivery_parents[MID] = [BASE]
        with self.assertRaisesRegex(ValueError, ".ai"):
            delivery.verify_content(BASE, SOURCE, HEAD, self.git)

    def test_ai_deletion_cannot_hide_dirty_base(self):
        self.git.trees[BASE][".ai/plan.md"] = ("100644", "blob", "3" * 40)
        with self.assertRaisesRegex(ValueError, "base already contains"):
            delivery.verify_content(BASE, SOURCE, HEAD, self.git)

    def test_nested_ai_is_not_silently_filtered(self):
        self.git.trees[SOURCE]["src/.ai/data"] = ("100644", "blob", "3" * 40)
        with self.assertRaisesRegex(ValueError, "delivery tree differs"):
            delivery.verify_content(BASE, SOURCE, HEAD, self.git)

    def test_git_adapter_uses_nul_paths_and_all_parent_history(self):
        git = delivery.DeliveryGit(Path("unused"))
        from subprocess import CompletedProcess

        def output(*args, **kwargs):
            if args[0] == "ls-tree":
                self.assertIn("-z", args)
                text = f"100755 blob {'2' * 40}\tsrc/name\nwith-tab\tfile\0"
            elif "--parents" in args:
                text = f"{HEAD} {BASE} {MID}\n"
            else:
                self.assertEqual(args, ("rev-list", f"{BASE}..{HEAD}"))
                text = f"{HEAD}\n{MID}\n"
            return CompletedProcess(args, 0, stdout=text, stderr="")

        with patch.object(git, "_run", side_effect=output):
            self.assertIn("src/name\nwith-tab\tfile", git.tree(HEAD))
            self.assertEqual(git.introduced_commits(BASE, HEAD), [HEAD, MID])
            self.assertEqual(git.commit_parents(HEAD), [BASE, MID])

    def test_git_checkout_adapter_checks_head_and_untracked_state(self):
        git = delivery.DeliveryGit(Path("unused"))
        from subprocess import CompletedProcess

        with patch.object(git, "_run", side_effect=[CompletedProcess([], 0, stdout=HEAD + "\n"), CompletedProcess([], 0, stdout="")]) as run:
            git.verify_checkout(HEAD)
            self.assertIn("--untracked-files=all", run.call_args.args)
        with patch.object(git, "_run", return_value=CompletedProcess([], 0, stdout=BASE + "\n")):
            with self.assertRaisesRegex(ValueError, "HEAD differs"):
                git.verify_checkout(HEAD)


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.manifest = fixtures.valid_manifest()
        self.runtime = fixtures.valid_runtime()
        self.files = fixtures.valid_evidence_files()
        original = b"Original user requirement: the confirmed behavior.\n- REQ-1: confirmed behavior\n"
        self.files["original.md"] = original
        self.manifest["delivery"] = {
            "schema_version": 1, "excluded_paths": [".ai/"],
            "original_request_ref": fixtures.evidence_ref("original.md", original),
            "requirements": [{"id": "REQ-1", "acceptance_criteria": ["AC-1"]}],
        }
        self.runtime["checkpoint"] = {
            "schema_version": 1, "plan_revision": "plan-v1", "orchestration_revision": "orch-v1",
            "writer": "orchestrator", "phase": "delivery", "last_completed": "delivery independently verified",
            "remaining_task_ids": [], "blockers": [], "next_action": "request authorization to push the delivery SHA",
        }
        scope = self.manifest["integration"]["review_scope"]
        contracts = [f"{item['id']}@{item['revision']}" for item in self.manifest["contracts"]]
        review = fixtures.review_record("delivery", "delivery-review.json", BASE, HEAD, contracts, scope)
        self.files["delivery-review.json"] = fixtures.json_bytes(fixtures.review_payload("delivery", BASE, HEAD, contracts, scope))
        test = {
            "schema_version": 1, "workflow_id": "workflow-test", "plan_revision": "plan-v1", "orchestration_revision": "orch-v1",
            "subject_id": "delivery", "status": "passed", "tested_head_sha": HEAD,
            "commands": [{"test_id": item["id"], "command": item["command"], "exit_code": 0, "status": "passed", "result": "observed expected behavior"} for item in self.manifest["integration"]["required_tests"]],
        }
        final = {
            "schema_version": 1, "workflow_id": "workflow-test", "subject_id": "delivery", "plan_revision": "plan-v1", "orchestration_revision": "orch-v1",
            "head_sha": HEAD, "scope_id": scope["scope_id"], "status": "passed", "requirements": ["REQ-1"], "acceptance_criteria": ["AC-1"],
            "result": "Original request, AC, source path, test evidence and final diff independently checked.",
        }
        self.runtime["delivery"] = {
            "schema_version": 1, "plan_revision": "plan-v1", "orchestration_revision": "orch-v1", "status": "verified",
            "base_sha": BASE, "source_head_sha": SOURCE, "head_sha": HEAD, "commits": [HEAD],
            "write_authorization_basis": "authorized export", "commit_authorizations": [{"commit_sha": HEAD, "plan_revision": "plan-v1", "orchestration_revision": "orch-v1", "diff_scope": ["src/task-1.py"], "authorization_basis": "authorized commit"}],
            "review": review,
            "tests_ref": self.put("delivery-test.json", test),
            "final_verification_ref": self.put("delivery-final.json", final),
        }
        self.git = DeliveryGraph()
        # The development reader must see the same frozen manifest, not a patched live copy.
        old_read = self.git.read_object
        manifest_bytes = fixtures.json_bytes(self.manifest)
        self.git.read_object = lambda sha, path: manifest_bytes if path == ".ai/workflow-manifest.json" else old_read(sha, path)

    def put(self, path, payload):
        raw = fixtures.json_bytes(payload)
        self.files[path] = raw
        return fixtures.evidence_ref(path, raw)

    def check(self):
        return delivery.validate_delivery(self.manifest, self.runtime, self.git, self.files.__getitem__)

    def test_real_development_validation_and_delivery_evidence_pass(self):
        self.assertEqual(self.check(), "MERGE_READY")

    def test_legacy_ledger_is_not_final_pr_ready(self):
        self.manifest.pop("delivery")
        with self.assertRaisesRegex(ValueError, "legacy state"):
            self.check()

    def test_planning_rejects_incomplete_delivery_definition(self):
        self.manifest["delivery"]["requirements"] = []
        codes = fixtures.error_codes(manifest=self.manifest, phase="planning")
        self.assertIn("E_DELIVERY_REQUIREMENTS", codes)

    def test_old_source_tests_cannot_prove_delivery(self):
        test = json.loads(self.files["delivery-test.json"])
        test["tested_head_sha"] = SOURCE
        self.runtime["delivery"]["tests_ref"] = self.put("delivery-test.json", test)
        with self.assertRaisesRegex(ValueError, "E_TEST_EVIDENCE_STALE"):
            self.check()

    def test_old_review_head_and_wrong_scope_are_rejected(self):
        for key, value in (("reviewed_head_sha", SOURCE), ("scope_id", "0" * 64)):
            with self.subTest(key=key):
                saved = self.runtime["delivery"]["review"][key]
                self.runtime["delivery"]["review"][key] = value
                with self.assertRaises(ValueError):
                    self.check()
                self.runtime["delivery"]["review"][key] = saved

    def test_final_verification_cannot_omit_ac(self):
        final = json.loads(self.files["delivery-final.json"])
        final["acceptance_criteria"] = []
        self.runtime["delivery"]["final_verification_ref"] = self.put("delivery-final.json", final)
        with self.assertRaisesRegex(ValueError, "omits acceptance_criteria"):
            self.check()

    def test_missing_original_requirement_detected(self):
        self.manifest["delivery"]["requirements"] = []
        # Content binding also rejects changing a frozen manifest.
        with self.assertRaises(ValueError):
            self.check()

    def test_changed_target_and_missing_commit_authorization(self):
        self.runtime["delivery"]["base_sha"] = PLAN
        with self.assertRaisesRegex(ValueError, "base is stale"):
            self.check()
        self.runtime["delivery"]["base_sha"] = BASE
        self.runtime["delivery"]["commit_authorizations"] = []
        with self.assertRaisesRegex(ValueError, "per-commit"):
            self.check()

    def test_partial_development_cannot_be_exported_as_complete(self):
        self.runtime["tasks"]["TASK-1"]["status"] = "implemented"
        with self.assertRaisesRegex(ValueError, "development evidence"):
            self.check()

    def test_wrong_checkout_and_dirty_delivery_are_not_ready(self):
        self.git.checkout_head = SOURCE
        with self.assertRaisesRegex(ValueError, "HEAD differs"):
            self.check()
        self.git.checkout_head = HEAD
        self.git.dirty = True
        with self.assertRaisesRegex(ValueError, "worktree is dirty"):
            self.check()

    def test_checkout_changes_during_verification_invalidate_result(self):
        with patch.object(self.git, "verify_checkout", side_effect=[None, ValueError("checkout changed")]):
            with self.assertRaisesRegex(ValueError, "checkout changed"):
                self.check()

    def test_fresh_agent_locator_and_missing_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path, locator = root / "runtime.json", root / "resume.json"
            state_path.write_text(json.dumps(self.runtime), encoding="utf-8")
            locator.write_text(json.dumps({"schema_version": 1, "workflow_id": "workflow-test", "runtime_state_id": "runtime-workflow-test", "state_path": str(state_path)}), encoding="utf-8")
            _, recovered = delivery.recover(locator)
            self.assertEqual(recovered["checkpoint"]["next_action"], self.runtime["checkpoint"]["next_action"])
            self.runtime["runtime_state_id"] = "another-task"
            state_path.write_text(json.dumps(self.runtime), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "mismatch"):
                delivery.recover(locator)
            state_path.unlink()
            with self.assertRaises(OSError):
                delivery.recover(locator)

    def test_checkpoint_cannot_hide_remaining_work_or_wrong_revision(self):
        self.runtime["checkpoint"]["plan_revision"] = "old"
        with self.assertRaisesRegex(ValueError, "stale"):
            delivery.checkpoint_valid(self.runtime)
        self.runtime["checkpoint"]["plan_revision"] = "plan-v1"
        self.runtime["tasks"]["TASK-1"]["status"] = "running"
        with self.assertRaisesRegex(ValueError, "unfinished tasks"):
            delivery.checkpoint_valid(self.runtime)


if __name__ == "__main__":
    unittest.main()

import contextlib
import copy
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from test_review import review, valid_scope_contract


def policy():
    return {"schema_version": 1, "revision": "agents-v1", "defaults": {}, "roles": {}, "agents": {}}


def capabilities():
    return {
        "source": "isolated test backend fixture",
        "models": [
            {"id": "test-astra", "aliases": ["astra"], "efforts": ["medium", "high", "xhigh", "ultra"]},
            {"id": "test-sol", "aliases": ["sol"], "efforts": ["medium", "high", "xhigh"]},
            {"id": "test-luna", "aliases": ["luna"], "efforts": ["medium", "high"]},
        ],
    }


class AgentConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.args = SimpleNamespace(
            model=None, effort=None, agent_config=None, capabilities_file=None,
            passes=1, jobs=3, deep=False, dry_run=False, timeout=10,
            repo=str(self.root), base="main", allow_dirty=False, fail_on="P2",
            output_dir=str(self.root / "run"),
            scope_file=str(self.write("scope.json", valid_scope_contract())),
        )

    def write(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def configure(self, config=None, catalog=None):
        self.args.agent_config = str(self.write("agents.json", config or policy()))
        self.args.capabilities_file = str(self.write("capabilities.json", catalog or capabilities()))

    def resolve(self):
        with mock.patch.object(review, "resolve_session_execution", return_value=("test-astra", "high")):
            return review.resolve_agent_roster(self.args)

    def test_default_inherits_every_agent_and_aggregator(self):
        roster = self.resolve()
        self.assertEqual(len(roster["agents"]), 6)
        for agent in roster["agents"].values():
            self.assertEqual((agent["model"], agent["effort"]), ("test-astra", "high"))
            self.assertEqual(agent["selection_basis"], {"model": "inherited", "effort": "inherited"})
            self.assertEqual(agent["status"], "planned")
            self.assertEqual(agent["configuration_evidence"], "not_started")

    def test_precedence_is_per_field_with_cli_at_task_default_level(self):
        config = policy()
        config["defaults"] = {"model": "luna", "effort": "medium"}
        config["roles"] = {"reviewer": {"model": "sol"}}
        config["agents"] = {"correctness-pass-1": {"effort": "xhight"}}
        self.configure(config)
        self.args.model, self.args.effort = "astra", "hight"
        roster = self.resolve()["agents"]
        self.assertEqual((roster["correctness-pass-1"]["model"], roster["correctness-pass-1"]["effort"]), ("test-sol", "xhigh"))
        self.assertEqual(roster["security-pass-1"]["effort"], "high")
        self.assertEqual(roster["aggregator"]["model"], "test-astra")
        self.assertEqual(roster["correctness-pass-1"]["selection_basis"]["effort"], "agent:correctness-pass-1")

    def test_aliases_are_resolved_to_backend_ids_and_canonical_effort(self):
        self.configure()
        self.args.model, self.args.effort = "aster", "xhight"
        roster = self.resolve()
        self.assertEqual(roster["agents"]["aggregator"]["model"], "test-astra")
        self.assertEqual(roster["agents"]["aggregator"]["effort"], "xhigh")
        for alias in ("mide", "mid", "med", "medium"):
            self.assertEqual(review.normalize_effort(alias), "medium")

    def test_model_only_override_keeps_inherited_effort(self):
        config = policy()
        config["agents"] = {"aggregator": {"model": "luna"}}
        self.configure(config)
        agent = self.resolve()["agents"]["aggregator"]
        self.assertEqual(agent["effort"], "high")
        self.assertEqual(agent["selection_basis"]["effort"], "inherited")

    def test_fully_explicit_profiles_do_not_read_session(self):
        config = policy()
        config["roles"] = {role: {"model": "sol", "effort": "high"} for role in ("reviewer", "aggregator")}
        self.configure(config)
        with mock.patch.object(review, "resolve_session_execution", side_effect=AssertionError("must not read")):
            review.resolve_agent_roster(self.args)

    def test_missing_inheritance_stops_instead_of_global_fallback(self):
        with mock.patch.object(review, "resolve_session_execution", side_effect=review.ReviewError("missing")):
            with self.assertRaises(review.ReviewError):
                review.resolve_agent_roster(self.args)

    def test_explicit_override_needs_capabilities_even_for_dry_run(self):
        self.args.effort, self.args.dry_run = "high", True
        with self.assertRaisesRegex(review.ReviewError, "capabilities-file"):
            self.resolve()

    def test_backend_rejects_unsupported_pair_not_silently_downgraded(self):
        self.configure()
        self.args.model, self.args.effort = "luna", "ultra"
        with self.assertRaisesRegex(review.ReviewError, "不支持"):
            self.resolve()

    def test_exact_new_model_works_without_hardcoded_product_catalog(self):
        catalog = capabilities()
        catalog["models"].append({"id": "future-model", "aliases": ["future"], "efforts": ["max"]})
        self.configure(catalog=catalog)
        self.args.model, self.args.effort = "future-model", "max"
        self.assertEqual(self.resolve()["agents"]["aggregator"]["model"], "future-model")

    def test_unknown_model_or_ambiguous_alias_fails(self):
        self.configure()
        self.args.model, self.args.effort = "not-available", "high"
        with self.assertRaises(review.ReviewError):
            self.resolve()
        catalog = capabilities()
        catalog["models"][1]["aliases"].append("astra")
        self.configure(catalog=catalog)
        with self.assertRaisesRegex(review.ReviewError, "不唯一"):
            self.resolve()

    def test_unknown_role_agent_and_profile_fields_fail(self):
        for field, value in (
            ("roles", {"reviewre": {"model": "sol"}}),
            ("agents", {"correctness-pass-2": {"model": "sol"}}),
            ("defaults", {"modle": "sol"}),
            ("defaults", {"model": None}),
            ("schema_version", True),
        ):
            with self.subTest(field=field, value=value):
                config = policy()
                config[field] = value
                self.configure(config)
                with self.assertRaises(review.ReviewError):
                    self.resolve()

    def test_multiple_passes_have_independent_ids(self):
        config = policy()
        config["agents"] = {"correctness-pass-2": {"model": "sol"}}
        self.configure(config)
        self.args.passes = 2
        agents = self.resolve()["agents"]
        self.assertEqual(len(agents), 11)
        self.assertEqual(agents["correctness-pass-2"]["model"], "test-sol")
        self.assertEqual(agents["correctness-pass-1"]["model"], "test-astra")

    def test_configuration_identity_is_stable_and_old_record_is_not_rewritten(self):
        self.configure()
        first = self.resolve()
        self.assertEqual(first["config_id"], self.resolve()["config_id"])
        saved = copy.deepcopy(first)
        self.args.effort = "medium"
        second = self.resolve()
        self.assertNotEqual(first["config_id"], second["config_id"])
        self.assertEqual(first, saved)

    def test_latest_session_supports_ultra_but_does_not_reuse_older_complete_turn(self):
        thread_id = "11111111-1111-1111-1111-111111111111"
        sessions = self.root / "sessions"
        sessions.mkdir()
        path = sessions / (thread_id + ".jsonl")
        events = [{"type": "turn_context", "payload": {"model": "test-astra", "effort": "ultra"}}]
        path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
        with mock.patch.dict(review.os.environ, {"CODEX_HOME": str(self.root), "CODEX_THREAD_ID": thread_id}):
            self.assertEqual(review.resolve_session_execution(), ("test-astra", "ultra"))
            events.append({"type": "turn_context", "payload": {"model": "test-sol"}})
            path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
            with self.assertRaises(review.ReviewError):
                review.resolve_session_execution()

    def execute_mocked(self, *, fail_lane=None, no_diff=False):
        self.configure()
        config = policy()
        config["roles"] = {"reviewer": {"model": "astra", "effort": "xhigh"}}
        config["agents"] = {"correctness-pass-1": {"model": "sol", "effort": "high"}, "aggregator": {"model": "luna", "effort": "medium"}}
        self.configure(config)
        scope = review.load_scope_contract(self.args.scope_file)
        snapshot = {
            "repo_root": str(self.root), "repo_name": "fixture", "base_ref": "main",
            "base_source": "explicit", "base_sha": "a" * 40, "head_sha": "b" * 40,
            "merge_base": "a" * 40, "worktree_status": "",
            "changed_files": [] if no_diff else ["example.py"],
        }
        calls = []
        stdout = io.StringIO()

        def invoke(command, **kwargs):
            calls.append(command)
            self.assertIn("你可以按角色或单个 Agent 修改", stdout.getvalue())
            output = Path(command[command.index("--output-last-message") + 1])
            if output.stem == fail_lane:
                return subprocess.CompletedProcess(command, 2, "", "fixture failure")
            if output.name == "verified-findings.json":
                self.assertEqual(len(calls), 6)
                payload = {"scope_id": scope["scope_id"], "findings": [], "rejected_candidates": [], "gap_search_summary": "快速模式未执行代码复核或 gap search"}
            else:
                payload = {"scope_id": scope["scope_id"], "lane": output.stem.rsplit("-pass-", 1)[0], "findings": []}
            output.write_text(json.dumps(payload), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")

        with contextlib.redirect_stdout(stdout), mock.patch.object(review, "require_command", side_effect=lambda name: name), mock.patch.object(review, "collect_snapshot", return_value=snapshot), mock.patch.object(review, "ensure_snapshot_unchanged"), mock.patch.object(review, "run_command", side_effect=invoke):
            if fail_lane:
                with self.assertRaises(review.ReviewError):
                    review.execute(self.args)
            else:
                self.assertEqual(review.execute(self.args), 0)
        return calls, stdout.getvalue(), scope["scope_id"]

    def test_real_dispatch_path_uses_each_profile_and_records_results(self):
        calls, output, scope_id = self.execute_mocked()
        self.assertEqual(len(calls), 6)
        for command in calls:
            path = Path(command[command.index("--output-last-message") + 1])
            expected = "test-luna" if path.name == "verified-findings.json" else ("test-sol" if path.stem == "correctness-pass-1" else "test-astra")
            self.assertEqual(command[command.index("--model") + 1], expected)
            self.assertIn(scope_id, command[-1])
        result = json.loads((self.root / "run/result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["snapshot"]["model"], "mixed")
        self.assertEqual(result["snapshot"]["scope_id"], scope_id)
        for agent in result["snapshot"]["agent_roster"]["agents"].values():
            self.assertEqual(agent["status"], "completed")
            self.assertEqual(agent["configuration_evidence"], "cli_invocation")
        self.assertIn("test-luna", (self.root / "run/report.md").read_text(encoding="utf-8"))

    def test_failure_preserves_status_and_does_not_start_aggregator(self):
        calls, _, _ = self.execute_mocked(fail_lane="correctness-pass-1")
        self.assertEqual(len(calls), 5)
        snapshot = json.loads((self.root / "run/snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["agent_roster"]["agents"]["correctness-pass-1"]["status"], "failed")
        self.assertEqual(snapshot["agent_roster"]["agents"]["aggregator"]["status"], "planned")
        self.assertFalse((self.root / "run/result.json").exists())

    def test_dry_run_displays_individual_commands_without_invoking(self):
        self.args.dry_run = True
        calls, output, _ = self.execute_mocked()
        self.assertEqual(calls, [])
        self.assertIn("--model test-sol", output)
        self.assertIn("--model test-luna", output)
        self.assertFalse((self.root / "run").exists())

    def test_no_diff_does_not_claim_agents_executed(self):
        calls, _, _ = self.execute_mocked(no_diff=True)
        self.assertEqual(calls, [])
        result = json.loads((self.root / "run/result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["summary"]["review_tasks"], 0)
        self.assertEqual({agent["status"] for agent in result["snapshot"]["agent_roster"]["agents"].values()}, {"not_run"})


if __name__ == "__main__":
    unittest.main()

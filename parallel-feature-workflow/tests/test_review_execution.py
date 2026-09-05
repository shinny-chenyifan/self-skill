import copy
import json
import unittest

from test_validate_workflow import (
    error_codes, evidence_ref, json_bytes, valid_evidence_files,
    valid_runtime, validator,
)


def execution(mode="native", passes=1):
    if mode == "native":
        mapping = {view: [view] for view in sorted(validator.REQUIRED_REVIEW_VIEWS)}
    else:
        mapping = {
            "correctness": ["correctness"],
            "state-concurrency": ["state-lifecycle"],
            "security": [],
            "reliability-performance": ["error-boundaries"],
            "contracts-tests": ["api-integration", "tests-regression"],
        }
    agents = [
        {
            "agent_id": f"{lane}-pass-{number}", "role": "reviewer",
            "views": views, "lane": lane, "pass": number,
            "model": "model-a", "effort": "high",
            "selection_basis": {"model": "inherited", "effort": "role:reviewer"},
            "status": "completed",
            "configuration_evidence": "native_tool" if mode == "native" else "cli_invocation",
        }
        for number in range(1, passes + 1)
        for lane, views in mapping.items()
    ]
    agents.append({
        "agent_id": "aggregator", "role": "aggregator", "views": ["aggregation"],
        "lane": "aggregation", "pass": 0, "model": "model-b", "effort": "medium",
        "selection_basis": {"model": "agent:aggregator", "effort": "agent:aggregator"},
        "status": "completed",
        "configuration_evidence": "native_tool" if mode == "native" else "cli_invocation",
    })
    return {"schema_version": 1, "config_revision": "agents-v1", "config_id": "a" * 64, "passes": passes, "agents": agents}


class ReviewExecutionTests(unittest.TestCase):
    def record(self, mode="native", passes=1):
        return {"mode": mode, "actual_model": "mixed", "actual_reasoning_effort": "mixed", "agent_execution": execution(mode, passes)}

    def errors(self, record):
        errors = []
        validator._validate_agent_execution(record, "review", errors)
        return errors

    def test_legacy_homogeneous_record_remains_compatible(self):
        self.assertEqual(self.errors({"actual_model": "old", "actual_reasoning_effort": "high"}), [])

    def test_native_and_script_support_multiple_passes_and_mixed_profiles(self):
        for mode in ("native", "script"):
            for passes in (1, 2):
                self.assertEqual(self.errors(self.record(mode, passes)), [])

    def test_mixed_without_per_agent_evidence_fails(self):
        self.assertTrue(self.errors({"actual_model": "mixed"}))

    def test_missing_or_duplicate_view_lane_and_aggregator_fail(self):
        mutations = (
            lambda agents: agents.pop(0),
            lambda agents: agents.pop(),
            lambda agents: agents.append(copy.deepcopy(agents[0])),
            lambda agents: agents[0].update(lane=agents[1]["lane"]),
            lambda agents: agents[0].update(views=agents[1]["views"]),
            lambda agents: agents[-1].update(lane="correctness"),
            lambda agents: agents[-1].update(role="reviewer"),
            lambda agents: agents[0].update(**{"pass": True}),
        )
        for mode in ("native", "script"):
            for mutate in mutations:
                record = self.record(mode)
                mutate(record["agent_execution"]["agents"])
                self.assertTrue(self.errors(record))

    def test_script_security_is_required_without_faking_another_view(self):
        record = self.record("script")
        agent = next(agent for agent in record["agent_execution"]["agents"] if agent["lane"] == "security")
        agent["views"] = ["api-integration"]
        self.assertTrue(self.errors(record))
        record = self.record("script")
        record["agent_execution"]["agents"] = [agent for agent in record["agent_execution"]["agents"] if agent["lane"] != "security"]
        self.assertTrue(self.errors(record))

    def test_failed_unknown_and_unproven_agent_records_fail(self):
        for fields in (
            {"status": "planned"}, {"status": "failed"}, {"model": None},
            {"effort": "mixed"}, {"configuration_evidence": "not_started"},
            {"configuration_evidence": "cli_invocation"},
            {"selection_basis": "inherited"}, {"selection_basis": {"model": "inherited"}},
        ):
            record = self.record()
            record["agent_execution"]["agents"][0].update(fields)
            self.assertTrue(self.errors(record))

    def test_bad_config_identity_and_scalar_summary_fail(self):
        for field, value in (("config_id", ""), ("config_revision", None), ("schema_version", True), ("passes", 0), ("agents", {})):
            record = self.record()
            record["agent_execution"][field] = value
            self.assertTrue(self.errors(record))
        record = self.record()
        record["actual_model"] = "model-a"
        self.assertTrue(self.errors(record))

    def setup_workflow(self):
        runtime = valid_runtime()
        files = valid_evidence_files()
        record = runtime["tasks"]["TASK-1"]["review"]
        record.update(self.record())
        payload = json.loads(files["reviews/TASK-1.json"])
        payload.update(self.record())
        raw = json_bytes(payload)
        files["reviews/TASK-1.json"] = raw
        record["report_ref"] = evidence_ref("reviews/TASK-1.json", raw)
        return runtime, files

    def test_full_workflow_gate_accepts_bound_per_agent_evidence(self):
        runtime, files = self.setup_workflow()
        self.assertEqual(error_codes(runtime=runtime, evidence_files=files, phase="merge-ready"), set())

    def test_recovery_rejects_relabeling_old_evidence_as_new_config(self):
        for mutate in (
            lambda record: record["agent_execution"].update(config_revision="agents-v2"),
            lambda record: record["agent_execution"]["agents"][0].update(model="changed-model"),
            lambda record: record.pop("agent_execution"),
        ):
            runtime, files = self.setup_workflow()
            mutate(runtime["tasks"]["TASK-1"]["review"])
            self.assertIn("E_REVIEW_AGENT_EXECUTION_STALE", error_codes(runtime=runtime, evidence_files=files, phase="merge-ready"))


if __name__ == "__main__":
    unittest.main()

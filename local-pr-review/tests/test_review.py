import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


REVIEW_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "review.py"
SCHEMA_DIR = REVIEW_SCRIPT.parent / "schemas"
SPEC = importlib.util.spec_from_file_location("local_pr_review", REVIEW_SCRIPT)
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)


def valid_scope_contract():
    return {
        "version": 1,
        "status": "confirmed",
        "confirmation_basis": "用户已确认本次 Review 的 plan 与边界。",
        "sources": [
            {
                "kind": "plan",
                "reference": "local-review-scope-boundary",
                "revision": "2026-07-30",
            }
        ],
        "objective": "让 Review 只阻断本次计划改动造成的问题。",
        "in_scope": [
            "范围确认门禁",
            "finding 的范围与变更归因",
            "阻断判定和报告分区",
        ],
        "out_of_scope": ["自动修复 Review 发现的问题"],
        "acceptance_criteria": [
            "既有或范围外问题会被告知，但不阻断",
            "所有 Review Agent 使用相同 scope_id",
        ],
        "integration_constraints": [
            "保留五个并行 Review Agent",
            "目标仓库保持只读",
        ],
        "open_questions": [],
    }


def finding(
    title,
    severity,
    scope_relation,
    change_relation,
):
    return {
        "title": title,
        "severity": severity,
        "category": "correctness",
        "file": "src/example.py",
        "line_start": 10,
        "line_end": 10,
        "summary": "可复现的问题。",
        "evidence": "固定 diff 中的代码证据。",
        "trigger": "运行对应输入。",
        "suggested_fix": "修复根因。",
        "suggested_test": "增加回归测试。",
        "confidence": "high",
        "scope_relation": scope_relation,
        "change_relation": change_relation,
        "scope_basis": "已确认范围契约中的验收条件。",
        "attribution_evidence": "基准版本与 HEAD 的对比证据。",
        "source_lanes": ["correctness"],
        "source_candidate_ids": ["correctness-pass-1:1"],
    }


def prompt_snapshot():
    return {
        "base_ref": "main",
        "base_sha": "a" * 40,
        "head_sha": "b" * 40,
        "merge_base": "a" * 40,
        "scope_id": "scope-test-123",
        "scope_contract": valid_scope_contract(),
    }


class ScopeContractTests(unittest.TestCase):
    def write_scope(self, directory, payload, *, sort_keys=False):
        path = Path(directory) / "scope.json"
        path.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=sort_keys,
            ),
            encoding="utf-8",
        )
        return path

    def test_load_scope_contract_returns_normalized_contract_and_stable_scope_id(self):
        contract = valid_scope_contract()
        reverse_order_contract = dict(reversed(list(contract.items())))
        with tempfile.TemporaryDirectory() as first_dir:
            first = review.load_scope_contract(
                self.write_scope(first_dir, contract)
            )
        with tempfile.TemporaryDirectory() as second_dir:
            second = review.load_scope_contract(
                self.write_scope(
                    second_dir,
                    reverse_order_contract,
                    sort_keys=True,
                )
            )

        self.assertEqual(first["contract"], second["contract"])
        self.assertEqual(first["scope_id"], second["scope_id"])
        self.assertIsInstance(first["scope_id"], str)
        self.assertTrue(first["scope_id"])

    def test_scope_schema_matches_python_contract_fields(self):
        schema = json.loads(
            (SCHEMA_DIR / "review-scope.json").read_text(encoding="utf-8")
        )

        self.assertEqual(set(schema["required"]), set(review.SCOPE_CONTRACT_FIELDS))
        source_kinds = schema["properties"]["sources"]["items"]["properties"][
            "kind"
        ]["enum"]
        self.assertEqual(set(source_kinds), review.SCOPE_SOURCE_KINDS)

    def test_load_scope_contract_rejects_each_missing_required_field(self):
        required_fields = (
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
        )
        for field in required_fields:
            with self.subTest(field=field):
                contract = valid_scope_contract()
                del contract[field]
                with tempfile.TemporaryDirectory() as directory:
                    path = self.write_scope(directory, contract)
                    with self.assertRaises(review.ReviewError):
                        review.load_scope_contract(path)

    def test_load_scope_contract_rejects_unconfirmed_status(self):
        contract = valid_scope_contract()
        contract["status"] = "draft"
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_scope(directory, contract)
            with self.assertRaises(review.ReviewError):
                review.load_scope_contract(path)

    def test_load_scope_contract_rejects_open_questions(self):
        contract = valid_scope_contract()
        contract["open_questions"] = ["兼容性范围是否包含旧客户端？"]
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_scope(directory, contract)
            with self.assertRaises(review.ReviewError):
                review.load_scope_contract(path)

    def test_load_scope_contract_rejects_scope_overlap(self):
        contract = valid_scope_contract()
        contract["out_of_scope"] = [" 范围确认门禁 "]
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_scope(directory, contract)
            with self.assertRaises(review.ReviewError):
                review.load_scope_contract(path)

    def test_load_scope_contract_rejects_null_revision(self):
        contract = valid_scope_contract()
        contract["sources"][0]["revision"] = None
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_scope(directory, contract)
            with self.assertRaises(review.ReviewError):
                review.load_scope_contract(path)

    def test_load_scope_contract_wraps_file_io_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(review.ReviewError):
                review.load_scope_contract(directory)

            invalid_utf8 = Path(directory) / "invalid.json"
            invalid_utf8.write_bytes(b"\xff")
            with self.assertRaises(review.ReviewError):
                review.load_scope_contract(invalid_utf8)

    def test_scope_prompt_contains_contract_id_and_classification_rules(self):
        snapshot = prompt_snapshot()
        contract = snapshot["scope_contract"]

        prompt = review.scope_prompt(snapshot)

        self.assertIn("scope-test-123", prompt)
        self.assertIn(contract["objective"], prompt)
        self.assertIn(contract["in_scope"][0], prompt)
        for value in (
            "in_scope",
            "required_integration",
            "scope_drift",
            "out_of_scope",
            "uncertain",
            "introduced_by_change",
            "amplified_by_change",
            "unmet_plan_requirement",
            "pre_existing_unchanged",
            "not_attributable",
        ):
            with self.subTest(value=value):
                self.assertIn(value, prompt)


class PayloadValidationTests(unittest.TestCase):
    def test_finding_schemas_match_python_boundary_enums(self):
        for filename in ("lane-review.json", "final-report.json"):
            with self.subTest(filename=filename):
                schema = json.loads(
                    (SCHEMA_DIR / filename).read_text(encoding="utf-8")
                )
                finding = schema["properties"]["findings"]["items"]
                properties = finding["properties"]

                self.assertEqual(
                    set(properties["scope_relation"]["enum"]),
                    review.SCOPE_RELATIONS,
                )
                self.assertEqual(
                    set(properties["change_relation"]["enum"]),
                    review.CHANGE_RELATIONS,
                )
                self.assertTrue(set(review.FINDING_FIELDS).issubset(finding["required"]))
                self.assertIn("scope_id", schema["required"])

    def test_lane_payload_rejects_mismatched_scope_id(self):
        payload = {
            "scope_id": "scope-other",
            "lane": "correctness",
            "findings": [],
        }

        with self.assertRaises(review.ReviewError):
            review.validate_lane_payload(
                payload,
                "correctness",
                "scope-expected",
                "correctness-pass-1",
            )

    def test_verified_payload_rejects_mismatched_scope_id(self):
        payload = {
            "scope_id": "scope-other",
            "findings": [],
            "rejected_candidates": [],
            "gap_search_summary": "未执行。",
        }

        with self.assertRaises(review.ReviewError):
            review.validate_verified_payload(payload, "scope-expected")

    def test_validate_finding_rejects_missing_review_axes(self):
        for field in ("scope_relation", "change_relation"):
            with self.subTest(field=field):
                candidate = finding(
                    "缺少分类",
                    "P2",
                    "in_scope",
                    "introduced_by_change",
                )
                del candidate[field]
                with self.assertRaises(review.ReviewError):
                    review.validate_finding(
                        candidate,
                        "test finding",
                        require_source_lanes=False,
                    )

    def test_validate_finding_rejects_invalid_review_axes(self):
        invalid_values = (
            ("scope_relation", "unknown_scope"),
            ("change_relation", "unknown_change"),
        )
        for field, value in invalid_values:
            with self.subTest(field=field):
                candidate = finding(
                    "非法分类",
                    "P2",
                    "in_scope",
                    "introduced_by_change",
                )
                candidate[field] = value
                with self.assertRaises(review.ReviewError):
                    review.validate_finding(
                        candidate,
                        "test finding",
                        require_source_lanes=False,
                    )

    def test_validate_finding_rejects_empty_human_readable_fields(self):
        for field in review.NON_EMPTY_FINDING_FIELDS:
            with self.subTest(field=field):
                candidate = finding(
                    "内容为空",
                    "P2",
                    "in_scope",
                    "introduced_by_change",
                )
                candidate[field] = " "
                with self.assertRaises(review.ReviewError):
                    review.validate_finding(
                        candidate,
                        "test finding",
                        require_source_lanes=False,
                    )

    def test_validate_finding_normalizes_contradictory_boundary_pairs(self):
        introduced = finding(
            "计划外新问题",
            "P2",
            "out_of_scope",
            "introduced_by_change",
        )
        review.validate_finding(
            introduced,
            "test finding",
            require_source_lanes=False,
        )
        self.assertEqual(introduced["scope_relation"], "scope_drift")
        self.assertTrue(review.is_blocking_eligible(introduced))

        pre_existing = finding(
            "计划外既有问题",
            "P1",
            "scope_drift",
            "pre_existing_unchanged",
        )
        review.validate_finding(
            pre_existing,
            "test finding",
            require_source_lanes=False,
        )
        self.assertEqual(pre_existing["scope_relation"], "uncertain")
        self.assertFalse(review.is_blocking_eligible(pre_existing))

        unmet = finding(
            "范围冲突",
            "P1",
            "out_of_scope",
            "unmet_plan_requirement",
        )
        review.validate_finding(
            unmet,
            "test finding",
            require_source_lanes=False,
        )
        self.assertEqual(unmet["scope_relation"], "uncertain")
        self.assertEqual(unmet["change_relation"], "uncertain")

    def test_verified_payload_requires_exact_candidate_coverage(self):
        lane_candidate = finding(
            "输入候选",
            "P2",
            "in_scope",
            "introduced_by_change",
        )
        lane_candidate["candidate_id"] = "correctness-pass-1:1"
        lane_results = [
            {
                "expected_lane": "correctness",
                "result": {"findings": [lane_candidate]},
            }
        ]

        retained = finding(
            "保留候选",
            "P2",
            "in_scope",
            "introduced_by_change",
        )
        payload = {
            "scope_id": "scope-expected",
            "findings": [retained],
            "rejected_candidates": [],
            "gap_search_summary": "快速模式未执行代码复核或 gap search",
        }
        review.validate_verified_payload(
            payload,
            "scope-expected",
            lane_results=lane_results,
            deep=False,
        )

        payload["findings"] = []
        with self.assertRaises(review.ReviewError):
            review.validate_verified_payload(
                payload,
                "scope-expected",
                lane_results=lane_results,
                deep=False,
            )

    def test_verified_payload_rejects_unknown_or_duplicate_candidate_ids(self):
        lane_candidate = finding(
            "输入候选",
            "P2",
            "in_scope",
            "introduced_by_change",
        )
        lane_candidate["candidate_id"] = "correctness-pass-1:1"
        lane_results = [
            {
                "expected_lane": "correctness",
                "result": {"findings": [lane_candidate]},
            }
        ]
        retained = finding(
            "保留候选",
            "P2",
            "in_scope",
            "introduced_by_change",
        )
        payload = {
            "scope_id": "scope-expected",
            "findings": [retained],
            "rejected_candidates": [
                {
                    "title": "拒绝候选",
                    "reason": "输入证据自相矛盾。",
                    "candidate_ids": ["correctness-pass-1:1"],
                }
            ],
            "gap_search_summary": "快速模式未执行代码复核或 gap search",
        }
        with self.assertRaises(review.ReviewError):
            review.validate_verified_payload(
                payload,
                "scope-expected",
                lane_results=lane_results,
                deep=False,
            )

        payload["rejected_candidates"] = []
        retained["source_candidate_ids"] = ["unknown:1"]
        with self.assertRaises(review.ReviewError):
            review.validate_verified_payload(
                payload,
                "scope-expected",
                lane_results=lane_results,
                deep=False,
            )

    def test_fast_verifier_cannot_discard_candidate_as_rejected(self):
        lane_candidate = finding(
            "输入阻断候选",
            "P1",
            "in_scope",
            "introduced_by_change",
        )
        lane_candidate["candidate_id"] = "correctness-pass-1:1"
        lane_results = [
            {
                "expected_lane": "correctness",
                "result": {"findings": [lane_candidate]},
            }
        ]
        payload = {
            "scope_id": "scope-expected",
            "findings": [],
            "rejected_candidates": [
                {
                    "title": "输入阻断候选",
                    "reason": "快速模式声称候选无效。",
                    "candidate_ids": ["correctness-pass-1:1"],
                }
            ],
            "gap_search_summary": "快速模式未执行代码复核或 gap search",
        }

        with self.assertRaises(review.ReviewError):
            review.validate_verified_payload(
                payload,
                "scope-expected",
                lane_results=lane_results,
                deep=False,
            )

    def test_fast_verifier_cannot_upgrade_attribution_or_severity(self):
        lane_candidate = finding(
            "既有输入候选",
            "P3",
            "in_scope",
            "pre_existing_unchanged",
        )
        lane_candidate["candidate_id"] = "correctness-pass-1:1"
        lane_results = [
            {
                "expected_lane": "correctness",
                "result": {"findings": [lane_candidate]},
            }
        ]
        retained = finding(
            "既有输入候选",
            "P3",
            "in_scope",
            "pre_existing_unchanged",
        )
        payload = {
            "scope_id": "scope-expected",
            "findings": [retained],
            "rejected_candidates": [],
            "gap_search_summary": "快速模式未执行代码复核或 gap search",
        }
        review.validate_verified_payload(
            payload,
            "scope-expected",
            lane_results=lane_results,
            deep=False,
        )

        retained["change_relation"] = "introduced_by_change"
        with self.assertRaises(review.ReviewError):
            review.validate_verified_payload(
                payload,
                "scope-expected",
                lane_results=lane_results,
                deep=False,
            )
        retained["change_relation"] = "pre_existing_unchanged"

        retained["severity"] = "P2"
        with self.assertRaises(review.ReviewError):
            review.validate_verified_payload(
                payload,
                "scope-expected",
                lane_results=lane_results,
                deep=False,
            )
        retained["severity"] = "P3"

        lane_candidate["severity"] = "P0"
        with self.assertRaises(review.ReviewError):
            review.validate_verified_payload(
                payload,
                "scope-expected",
                lane_results=lane_results,
                deep=False,
            )
        lane_candidate["severity"] = "P3"

        retained["source_lanes"] = ["security"]
        with self.assertRaises(review.ReviewError):
            review.validate_verified_payload(
                payload,
                "scope-expected",
                lane_results=lane_results,
                deep=False,
            )
        retained["source_lanes"] = ["correctness"]

        payload["gap_search_summary"] = "已完成代码复核"
        with self.assertRaises(review.ReviewError):
            review.validate_verified_payload(
                payload,
                "scope-expected",
                lane_results=lane_results,
                deep=False,
            )

    def test_fast_verifier_downgrades_merged_classification_conflicts(self):
        first = finding(
            "重复候选",
            "P2",
            "in_scope",
            "introduced_by_change",
        )
        first["candidate_id"] = "correctness-pass-1:1"
        second = finding(
            "重复候选",
            "P2",
            "required_integration",
            "pre_existing_unchanged",
        )
        second["candidate_id"] = "security-pass-1:1"
        lane_results = [
            {
                "expected_lane": "correctness",
                "result": {"findings": [first]},
            },
            {
                "expected_lane": "security",
                "result": {"findings": [second]},
            },
        ]
        retained = finding(
            "合并候选",
            "P2",
            "uncertain",
            "uncertain",
        )
        retained["source_lanes"] = ["correctness", "security"]
        retained["source_candidate_ids"] = [
            "correctness-pass-1:1",
            "security-pass-1:1",
        ]
        payload = {
            "scope_id": "scope-expected",
            "findings": [retained],
            "rejected_candidates": [],
            "gap_search_summary": "快速模式未执行代码复核或 gap search",
        }

        review.validate_verified_payload(
            payload,
            "scope-expected",
            lane_results=lane_results,
            deep=False,
        )
        retained["change_relation"] = "introduced_by_change"
        with self.assertRaises(review.ReviewError):
            review.validate_verified_payload(
                payload,
                "scope-expected",
                lane_results=lane_results,
                deep=False,
            )

    def test_deep_gap_search_may_have_no_source_candidate_id(self):
        gap_finding = finding(
            "Gap search 新发现",
            "P2",
            "in_scope",
            "introduced_by_change",
        )
        gap_finding["source_lanes"] = ["gap-search"]
        gap_finding["source_candidate_ids"] = []
        payload = {
            "scope_id": "scope-expected",
            "findings": [gap_finding],
            "rejected_candidates": [],
            "gap_search_summary": "深度模式完成 gap search。",
        }

        review.validate_verified_payload(
            payload,
            "scope-expected",
            lane_results=[],
            deep=True,
        )
        with self.assertRaises(review.ReviewError):
            review.validate_verified_payload(
                payload,
                "scope-expected",
                lane_results=[],
                deep=False,
            )


class PromptPropagationTests(unittest.TestCase):
    def test_lane_and_both_verifier_prompts_share_scope_id_and_contract(self):
        snapshot = prompt_snapshot()
        prompts = (
            review.lane_prompt(
                "correctness",
                "正确性与业务逻辑",
                snapshot,
                1,
            ),
            review.verifier_prompt(snapshot, False),
            review.verifier_prompt(snapshot, True),
        )

        for prompt in prompts:
            with self.subTest(prompt=prompt[:20]):
                self.assertIn(snapshot["scope_id"], prompt)
                self.assertIn(snapshot["scope_contract"]["objective"], prompt)

    def test_fast_verifier_downgrades_attribution_conflicts_to_uncertain(self):
        prompt = review.verifier_prompt(prompt_snapshot(), False)

        self.assertIn("冲突", prompt)
        self.assertIn("uncertain", prompt)
        self.assertIn("不得", prompt)
        self.assertIn("提升归因", prompt)

    def test_deep_verifier_classifies_gap_search_findings(self):
        prompt = review.verifier_prompt(prompt_snapshot(), True)

        self.assertIn("gap search", prompt)
        self.assertIn("新发现项", prompt)
        self.assertIn("scope_relation", prompt)
        self.assertIn("change_relation", prompt)
        self.assertIn("相同边界分类", prompt)


class BlockingPolicyTests(unittest.TestCase):
    def test_finding_fields_include_both_review_axes(self):
        self.assertIn("scope_relation", review.FINDING_FIELDS)
        self.assertIn("change_relation", review.FINDING_FIELDS)

    def test_blocking_eligibility_matrix(self):
        cases = (
            ("in_scope", "introduced_by_change", True),
            ("required_integration", "amplified_by_change", True),
            ("scope_drift", "introduced_by_change", True),
            ("in_scope", "unmet_plan_requirement", True),
            ("out_of_scope", "introduced_by_change", False),
            ("uncertain", "introduced_by_change", False),
            ("in_scope", "pre_existing_unchanged", False),
            ("in_scope", "not_attributable", False),
            ("in_scope", "uncertain", False),
        )
        for scope_relation, change_relation, expected in cases:
            with self.subTest(
                scope_relation=scope_relation,
                change_relation=change_relation,
            ):
                candidate = finding(
                    "边界矩阵",
                    "P1",
                    scope_relation,
                    change_relation,
                )
                self.assertEqual(
                    review.is_blocking_eligible(candidate),
                    expected,
                )

    def test_blocking_finding_applies_severity_threshold_after_eligibility(self):
        cases = (
            ("P0", "P0", True),
            ("P1", "P0", False),
            ("P1", "P1", True),
            ("P2", "P1", False),
            ("P2", "P2", True),
            ("P3", "P2", False),
            ("P3", "P3", True),
        )
        for severity, fail_on, expected in cases:
            with self.subTest(severity=severity, fail_on=fail_on):
                candidate = finding(
                    "严重度矩阵",
                    severity,
                    "in_scope",
                    "introduced_by_change",
                )
                self.assertEqual(
                    review.is_blocking_finding(candidate, fail_on),
                    expected,
                )

        advisory = finding(
            "既有高危问题",
            "P0",
            "in_scope",
            "pre_existing_unchanged",
        )
        self.assertFalse(review.is_blocking_finding(advisory, "P3"))

    def test_partition_findings_separates_blocking_current_and_advisory(self):
        blocking = finding(
            "本次引入的 P2",
            "P2",
            "in_scope",
            "introduced_by_change",
        )
        non_blocking_current = finding(
            "本次引入的 P3",
            "P3",
            "in_scope",
            "introduced_by_change",
        )
        advisory = finding(
            "基准版本已有的 P0",
            "P0",
            "in_scope",
            "pre_existing_unchanged",
        )

        partitions = review.partition_findings(
            [blocking, non_blocking_current, advisory],
            "P2",
        )

        self.assertEqual(partitions["blocking"], [blocking])
        self.assertEqual(
            partitions["non_blocking_current_change"],
            [non_blocking_current],
        )
        self.assertEqual(partitions["advisory"], [advisory])


class MarkdownTests(unittest.TestCase):
    def test_build_markdown_renders_each_finding_partition(self):
        blocking = finding(
            "本次引入的阻断问题",
            "P2",
            "in_scope",
            "introduced_by_change",
        )
        non_blocking_current = finding(
            "本次引入但未达阈值",
            "P3",
            "required_integration",
            "amplified_by_change",
        )
        advisory = finding(
            "既有问题仅告知",
            "P0",
            "in_scope",
            "pre_existing_unchanged",
        )
        result = {
            "snapshot": {
                "repo_root": "/tmp/example",
                "base_ref": "main",
                "base_source": "explicit",
                "base_sha": "a" * 40,
                "head_sha": "b" * 40,
                "merge_base": "a" * 40,
                "model": "test-model",
                "effort": "high",
                "changed_files": ["src/example.py"],
                "scope_id": "scope-test-123",
                "scope_contract": valid_scope_contract(),
            },
            "summary": {
                "review_tasks": 6,
                "severity_counts": {
                    "P0": 1,
                    "P1": 0,
                    "P2": 1,
                    "P3": 1,
                },
                "mode": "fast",
                "fail_on": "P2",
                "blocked": True,
            },
            "review": {
                "findings": [blocking, non_blocking_current, advisory],
                "rejected_candidates": [
                    {
                        "title": "重复候选",
                        "reason": "与已保留 finding 根因相同。",
                        "candidate_ids": ["correctness-pass-1:4"],
                    }
                ],
                "gap_search_summary": "快速模式未执行 gap search。",
            },
        }

        markdown = review.build_markdown(result, Path("/tmp/review-run"))

        self.assertIn("scope-test-123", markdown)
        self.assertIn("阻断问题", markdown)
        self.assertIn("本次变更但未达", markdown)
        self.assertIn("既有/范围外/不确定", markdown)
        self.assertIn(blocking["title"], markdown)
        self.assertIn(non_blocking_current["title"], markdown)
        self.assertIn(advisory["title"], markdown)
        self.assertIn("被拒绝的候选", markdown)


class ExecutionGateTests(unittest.TestCase):
    def test_unconfirmed_scope_never_checks_or_invokes_codex(self):
        contract = valid_scope_contract()
        contract["status"] = "draft"
        with tempfile.TemporaryDirectory() as directory:
            scope_file = Path(directory) / "scope.json"
            scope_file.write_text(
                json.dumps(contract, ensure_ascii=False),
                encoding="utf-8",
            )
            required_commands = []

            with mock.patch.object(
                review,
                "require_command",
                side_effect=required_commands.append,
            ):
                with self.assertRaises(review.ReviewError):
                    review.execute(SimpleNamespace(scope_file=str(scope_file)))

        self.assertNotIn("codex", required_commands)


if __name__ == "__main__":
    unittest.main()

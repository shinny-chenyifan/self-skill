from pathlib import Path
import re
import unittest


SKILL_ROOT = Path(__file__).resolve().parents[1]
SKILL_FILE = SKILL_ROOT / "SKILL.md"


class SkillContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.skill_text = SKILL_FILE.read_text(encoding="utf-8")
        cls.corpus = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                SKILL_FILE,
                SKILL_ROOT / "references" / "workflow-state-machine.md",
                SKILL_ROOT / "references" / "handoff-contracts.md",
                SKILL_ROOT / "references" / "git-worktree-lifecycle.md",
                SKILL_ROOT / "references" / "fallback-protocols.md",
                SKILL_ROOT / "references" / "workflow-code-session.md",
            )
        )

    def test_main_skill_stays_under_progressive_disclosure_limit(self):
        self.assertLessEqual(len(self.skill_text.splitlines()), 500)

    def test_frontmatter_triggers_specialist_composition(self):
        frontmatter = self.skill_text.split("---", 2)[1]
        self.assertIn("solution-planner", frontmatter)
        self.assertIn("local-pr-review", frontmatter)
        self.assertIn("worktree", frontmatter)
        self.assertIn("多 Agent", frontmatter)

    def test_specialist_skills_are_mandatory_when_available(self):
        self.assertRegex(
            self.skill_text,
            re.compile(
                r"available.{0,80}必须使用专职 Skill",
                re.DOTALL,
            ),
        )
        self.assertIn("本 Skill 不再创建第二套 Gate 1", self.skill_text)
        self.assertIn("本 Skill 不再创建额外 Reviewer Agent", self.skill_text)
        ui_prompt = (
            SKILL_ROOT / "agents" / "openai.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("可用时必须组合使用", ui_prompt)

    def test_status_labels_cannot_replace_complete_planning_handoff(self):
        for value in (
            "状态标签是必要条件，不是充分证据",
            "不得据此进入编排",
            "保持 `orchestration_blocked`",
            "不能新增方案未决定的格式",
        ):
            with self.subTest(value=value):
                self.assertIn(value, self.corpus)

    def test_fallback_cannot_bypass_blocked_results(self):
        for value in (
            "待澄清",
            "质量阻塞",
            "Review 阻断",
            "权限拒绝",
        ):
            with self.subTest(value=value):
                self.assertIn(value, self.corpus)
        self.assertRegex(
            self.corpus,
            re.compile(
                r"只有.{0,60}(absent|缺失).{0,40}(unloadable|无法加载)"
                r".{0,80}(fallback|降级)",
                re.DOTALL,
            ),
        )

    def test_review_uses_fixed_snapshots_and_re_review(self):
        for value in (
            "WORKFLOW_BASE_SHA",
            "TASK_START_SHA",
            "TASK_HEAD_SHA",
            "INTEGRATION_HEAD_SHA",
            "scope_id",
            "Re-review",
        ):
            with self.subTest(value=value):
                self.assertIn(value, self.corpus)
        self.assertNotIn("Current git diff", self.corpus)

    def test_scope_id_changes_only_with_scope_contract(self):
        self.assertIn(
            "Scope Contract 未变化时保留相同 `scope_id`",
            self.corpus,
        )

    def test_runtime_state_is_external_and_plan_commit_is_not_self_referential(self):
        self.assertIn("仓库外的单写者运行态账本", self.corpus)
        self.assertIn("规划 manifest 不包含 `PLAN_SHA`", self.corpus)
        manifest_template = (
            SKILL_ROOT / "assets" / "templates" / "workflow-manifest.json"
        ).read_text(encoding="utf-8")
        runtime_template = (
            SKILL_ROOT / "assets" / "templates" / "runtime-state.json"
        ).read_text(encoding="utf-8")
        self.assertNotIn('"plan_sha"', manifest_template)
        self.assertIn('"plan_sha"', runtime_template)

    def test_acceptance_and_condition_evidence_are_structured(self):
        for value in (
            "plan.acceptance_criteria",
            "AC→task→planned test→Review Scope",
            "required_proof_kinds",
            "subject_id=condition:<COND-ID>",
        ):
            with self.subTest(value=value):
                self.assertIn(value, self.corpus)

    def test_source_writes_and_internal_review_need_authorization(self):
        self.assertIn("implementation_write_authorization_basis", self.corpus)
        self.assertIn("read_only_git_authorization_basis", self.corpus)

    def test_merge_ready_is_not_git_authorization(self):
        self.assertRegex(
            self.corpus,
            re.compile(
                r"MERGE_READY.{0,100}(不代表|不等于).{0,80}"
                r"(merge|push)",
                re.IGNORECASE | re.DOTALL,
            ),
        )

    def test_workflow_code_session_has_an_explicit_separate_route(self):
        code_session = (
            SKILL_ROOT / "references" / "workflow-code-session.md"
        ).read_text(encoding="utf-8")
        for value in (
            "只有调用方明确指定 `workflow-code-session`",
            "不进入原完整流程的状态机、manifest、模板或 `validate_workflow.py` 校验器",
            "不得输出 `MERGE_READY`",
            "单个 Coder 串行实施",
        ):
            with self.subTest(value=value):
                self.assertIn(value, self.skill_text)
        for value in (
            "每个文件有唯一写入负责人",
            "所有 Coder 停写",
            "只有 code 主 Agent 操作 Git",
        ):
            with self.subTest(value=value):
                self.assertIn(value, code_session)

    def test_workflow_code_session_configuration_stays_outside_legacy_schema(self):
        dispatch = (
            SKILL_ROOT / "references" / "agent-dispatch.md"
        ).read_text(encoding="utf-8")
        self.assertRegex(
            dispatch,
            re.compile(
                r"用户对具体 Agent、角色或任务的配置优先.{0,80}"
                r"workflow 的明确配置.{0,100}"
                r"原完整流程的默认值或 `strict-plan-execution` 预设"
                r".{0,80}继承当前配置",
                re.DOTALL,
            ),
        )
        self.assertIn(
            "外层任务记录的单一写者保存，不写入旧 schema",
            dispatch,
        )

    def test_all_direct_resources_exist(self):
        expected = (
            "references/workflow-state-machine.md",
            "references/handoff-contracts.md",
            "references/git-worktree-lifecycle.md",
            "references/fallback-protocols.md",
            "references/workflow-code-session.md",
            "scripts/validate_workflow.py",
            "agents/openai.yaml",
            "assets/templates/workflow-manifest.json",
            "assets/templates/runtime-state.json",
            "assets/templates/solution-record.md",
            "assets/templates/orchestration-record.md",
            "assets/templates/task.md",
            "assets/templates/contract.md",
            "assets/templates/task-report.md",
            "assets/templates/integration-plan.md",
            "assets/templates/review-result.json",
            "assets/templates/review-scope.json",
            "assets/templates/test-result.json",
            "assets/templates/artifact-result.json",
            "assets/templates/condition-result.json",
            "assets/templates/condition-definition.json",
            "assets/templates/external-evidence.json",
            "assets/templates/user-confirmation.json",
            "assets/templates/integration-execution.md",
        )
        for relative in expected:
            with self.subTest(relative=relative):
                self.assertTrue((SKILL_ROOT / relative).is_file())


if __name__ == "__main__":
    unittest.main()

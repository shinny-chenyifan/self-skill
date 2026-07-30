from pathlib import Path
import re
import unittest


SKILL_FILE = Path(__file__).resolve().parents[1] / "SKILL.md"


class SkillContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.skill_text = SKILL_FILE.read_text(encoding="utf-8")

    def test_review_scope_must_be_confirmed_before_agents_start(self):
        self.assertRegex(
            self.skill_text,
            re.compile(
                r"(范围|Scope).{0,80}(确认|明确).{0,160}(不得|不能|禁止).{0,40}"
                r"(启动|运行).{0,40}Agent",
                re.IGNORECASE | re.DOTALL,
            ),
        )

    def test_native_review_keeps_five_agents(self):
        self.assertRegex(
            self.skill_text,
            re.compile(r"五个.{0,80}(审查|Review).{0,80}Agent", re.DOTALL),
        )
        for lane in (
            "正确性",
            "错误处理",
            "并发",
            "兼容",
            "测试",
        ):
            with self.subTest(lane=lane):
                self.assertIn(lane, self.skill_text)

    def test_scope_identity_is_shared_across_review(self):
        self.assertIn("scope_id", self.skill_text)
        self.assertRegex(
            self.skill_text,
            re.compile(r"(相同|同一).{0,80}scope_id", re.DOTALL),
        )

    def test_fast_summary_preserves_candidate_lineage(self):
        self.assertIn("candidate_id", self.skill_text)
        self.assertRegex(
            self.skill_text,
            re.compile(
                r"每个输入.{0,40}candidate_id.{0,80}必须且只能",
                re.DOTALL,
            ),
        )

    def test_script_fallback_requires_scope_file(self):
        self.assertIn("--scope-file", self.skill_text)
        self.assertRegex(
            self.skill_text,
            re.compile(r"--scope-file.{0,120}(必需|必须|不可省略)", re.DOTALL),
        )


if __name__ == "__main__":
    unittest.main()

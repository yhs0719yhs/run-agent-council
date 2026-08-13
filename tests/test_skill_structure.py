import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "run-agent-council"


class SkillStructureTests(unittest.TestCase):
    def test_required_files_exist(self):
        required = [
            SKILL / "SKILL.md",
            SKILL / "agents" / "openai.yaml",
            SKILL / "references" / "protocol.md",
            SKILL / "references" / "rubric.md",
            SKILL / "scripts" / "score_gate.py",
        ]
        for path in required:
            self.assertTrue(path.is_file(), str(path))

    def test_frontmatter_has_only_name_and_description(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        parts = text.split("---", 2)
        self.assertEqual(3, len(parts))
        fields = [
            line.split(":", 1)[0].strip()
            for line in parts[1].splitlines()
            if line.strip()
        ]
        self.assertEqual(["name", "description"], fields)
        self.assertIn("name: run-agent-council", parts[1])

    def test_skill_is_compact_and_has_no_placeholders(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertLess(len(text.splitlines()), 500)
        self.assertNotIn("TODO", text)

    def test_direct_reference_links_resolve(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        links = re.findall(r"\((references/[^)]+)\)", text)
        self.assertTrue(links)
        for link in links:
            self.assertTrue((SKILL / link).is_file(), link)

    def test_openai_metadata_mentions_explicit_skill(self):
        text = (SKILL / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn('display_name: "Agent Council"', text)
        self.assertIn("$run-agent-council", text)
        self.assertIn("exact 100/100 FINAL_PASS", text)

    def test_forward_test_regressions_are_guarded(self):
        skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        protocol_text = (SKILL / "references" / "protocol.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("dedicated Designer agent", skill_text)
        self.assertIn("append-only run register", skill_text)
        self.assertIn("Treat this event list as append-only", protocol_text)
        self.assertIn("return `BLOCKED` instead of claiming completion", protocol_text)

    def test_exact_100_policy_cannot_regress_to_fixed_limits(self):
        skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        protocol_text = (SKILL / "references" / "protocol.md").read_text(
            encoding="utf-8"
        )
        rubric_text = (SKILL / "references" / "rubric.md").read_text(
            encoding="utf-8"
        )
        combined = "\n".join((skill_text, protocol_text, rubric_text))
        self.assertIn("No preset repair-round or total agent-turn limit", skill_text)
        self.assertIn("FINAL_PASS", combined)
        self.assertIn("exact weighted score of 100/100", rubric_text)
        self.assertNotIn("max_repair_rounds:", combined)
        self.assertNotIn("at least 90/100", combined)
        self.assertNotIn("Twenty-four agent turns", combined)

    def test_no_repository_docs_are_inside_skill(self):
        forbidden = {"README.md", "INSTALLATION_GUIDE.md", "CHANGELOG.md"}
        found = {path.name for path in SKILL.rglob("*") if path.is_file()}
        self.assertFalse(forbidden & found)


if __name__ == "__main__":
    unittest.main()

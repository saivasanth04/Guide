import re
from pathlib import Path
from typing import Dict, Any
import yaml


class SkillManager:
    def __init__(self, skills_dir: str | Path | None = None):
        self.base_skills_dir = (
            Path(skills_dir) if skills_dir is not None
            else Path(__file__).resolve().parent / "Skills" / ".agents" / "skills"
        ).resolve()

    def load_skill(self, skill_name: str) -> Dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", skill_name):
            raise ValueError("Skill name must contain only letters, digits, underscores, or hyphens")
        skill_path = (self.base_skills_dir / skill_name / "SKILL.md").resolve()
        if not skill_path.is_relative_to(self.base_skills_dir):
            raise ValueError("Skill path must stay inside the skills directory")
        if not skill_path.exists():
            raise FileNotFoundError(f"SKILL.md not found at {skill_path.resolve()}")

        content = skill_path.read_text(encoding="utf-8")
        metadata = {}
        instructions = content

        frontmatter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
        if frontmatter_match:
            yaml_header, instructions = frontmatter_match.groups()
            metadata = yaml.safe_load(yaml_header) or {}

        if not isinstance(metadata, dict):
            raise ValueError(f"Skill metadata must be a mapping: {skill_path}")
        if not instructions.strip():
            raise ValueError(f"Skill instructions are empty: {skill_path}")
        if not isinstance(metadata.get("name", skill_name), str):
            raise ValueError(f"Skill name must be a string: {skill_path}")

        return {
            "name": metadata.get("name", skill_name),
            "instructions": instructions.strip(),
            "path": str(skill_path.resolve())
        }

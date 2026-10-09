#!/usr/bin/env python3
from __future__ import annotations

# BEGIN HUMAN READABLE UI PATCH
import json, sys
def _print_human_readable_ui(res):
    if not isinstance(res, dict):
        return
    if res.get('success'):
        data = res.get('data', {})
        if isinstance(data, dict):
            for k, v in data.items():
                print(f'>>> {k}: {v}', file=sys.stderr)
    else:
        err = res.get('error')
        if isinstance(err, dict):
            print(f'!!! Error: {err.get("message", err)}', file=sys.stderr)
        else:
            print(f'!!! Error: {err}', file=sys.stderr)
# END HUMAN READABLE UI PATCH

"""Manage, create, and load skills for the agent — llm-functions tool."""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List


def validate(action: str, name: str, description: str, content: str, skills_dir: str) -> None:
    """Validate inputs before execution."""
    valid_actions = {"create", "load", "list"}
    if action not in valid_actions:
        raise ValueError(f"Invalid action: {action}. Must be one of {valid_actions}")

    if action in {"create", "load"} and not name:
        raise ValueError(f"Name is required for action '{action}'")

    if name and not re.match(r"^[a-zA-Z0-9_-]+$", name):
        raise ValueError("Skill name can only contain alphanumeric characters, hyphens, and underscores")

    if action == "create":
        if not description:
            raise ValueError("Description is required when creating a skill")
        if not content:
            raise ValueError("Content is required when creating a skill")

    if not skills_dir:
        raise ValueError("skills_dir path cannot be empty")


def execute(action: str, name: str, description: str, content: str, skills_dir: str) -> Dict[str, Any]:
    """Execute the core logic of the skill manager."""
    target_dir = Path(skills_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    if action == "create":
        skill_dir = target_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"

        # Build YAML frontmatter and content
        full_content = f"---\nname: {name}\ndescription: {description}\n---\n\n{content}\n"

        try:
            skill_file.write_text(full_content, encoding="utf-8")
        except OSError as e:
            raise RuntimeError(f"Failed to write skill file: {e}")

        return {
            "success": True,
            "data": {
                "action": "create",
                "name": name,
                "path": str(skill_file.absolute()),
                "message": f"Skill '{name}' created successfully."
            },
            "warnings": [],
            "error": None
        }

    elif action == "load":
        skill_file = target_dir / name / "SKILL.md"
        if not skill_file.is_file():
            # Check if it's a direct markdown file
            alt_skill_file = target_dir / f"{name}.md"
            if alt_skill_file.is_file():
                skill_file = alt_skill_file
            else:
                return {
                    "success": False,
                    "data": {},
                    "warnings": [],
                    "error": f"Skill '{name}' not found at {skill_file} or {alt_skill_file}"
                }

        try:
            loaded_content = skill_file.read_text(encoding="utf-8")
        except OSError as e:
            raise RuntimeError(f"Failed to read skill file: {e}")

        return {
            "success": True,
            "data": {
                "action": "load",
                "name": name,
                "path": str(skill_file.absolute()),
                "content": loaded_content
            },
            "warnings": [],
            "error": None
        }

    elif action == "list":
        skills_found: List[Dict[str, str]] = []
        if target_dir.exists():
            for item in target_dir.iterdir():
                if item.is_dir():
                    skill_md = item / "SKILL.md"
                    if skill_md.is_file():
                        skills_found.append({"name": item.name, "path": str(skill_md.absolute())})
                elif item.is_file() and item.suffix == ".md":
                    skills_found.append({"name": item.stem, "path": str(item.absolute())})

        return {
            "success": True,
            "data": {
                "action": "list",
                "count": len(skills_found),
                "skills": sorted(skills_found, key=lambda x: x["name"])
            },
            "warnings": [],
            "error": None
        }

    return {"success": False, "error": "Unknown execution path"}


def run(
    action: str,
    name: str = "",
    description: str = "",
    content: str = "",
    skills_dir: str = "skills",
) -> Dict[str, Any]:
    """Create, load, or list agent skills.

    Args:
        action: The action to perform ('create', 'load', 'list').
        name: The name of the skill (required for create and load).
        description: Description of the skill (required for create).
        content: The Markdown instructions for the skill (required for create).
        skills_dir: Directory where skills are stored (defaults to 'skills').
    """
    try:
        validate(action, name, description, content, skills_dir)
        return execute(action, name, description, content, skills_dir)
    except Exception as e:
        return {
            "success": False,
            "data": {},
            "warnings": [],
            "error": str(e)
        }


def _cli() -> int:
    p = argparse.ArgumentParser(description="Manage, create, and load skills.")
    p.add_argument("--action", required=True, choices=["create", "load", "list"], help="Action to perform")
    p.add_argument("--name", default="", help="Name of the skill")
    p.add_argument("--description", default="", help="Description of the skill (for create)")
    p.add_argument("--content", default="", help="Markdown content of the skill (for create)")
    p.add_argument("--skills-dir", default="skills", help="Directory where skills are stored")

    args = p.parse_args()

    result = run(
        action=args.action,
        name=args.name,
        description=args.description,
        content=args.content,
        skills_dir=args.skills_dir,
    )

    # Write machine result to LLM_OUTPUT if available, else stdout
    output_target = os.environ.get("LLM_OUTPUT", "")
    if output_target and output_target != "/dev/stdout":
        try:
            with open(output_target, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
        except OSError as e:
            print(f"Failed to write to LLM_OUTPUT: {e}", file=sys.stderr)
            print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result, indent=2))

    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(_cli())
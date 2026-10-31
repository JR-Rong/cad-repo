"""Project metadata handling for cadkit projects.

A project lives at ``<cadRoot>/projects/<project_id>/`` and contains:

- ``project.yml``  : project metadata (JSON-compatible YAML written as JSON)
- ``build.py``     : parametric build script (uses cadkit.feature framework)
- ``design.md``    : design notes (bonus item + non-reverse-engineering evidence)
- ``build/``       : generated FCStd / reports / step / renders
"""
from __future__ import annotations

import json
import os
import re
import time

NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")

GRADES = {"C": (0, 49), "B": (50, 99), "A": (100, 199), "S": (200, None)}
GRADE_MIN_STEPS = {"C": 20, "B": 50, "A": 100, "S": 200}


def grade_for_steps(n: int) -> str:
    if n >= 200:
        return "S"
    if n >= 100:
        return "A"
    if n >= 50:
        return "B"
    return "C"


def check_safe_name(name: str) -> str:
    if not NAME_RE.fullmatch(name) or name != name.strip():
        raise ValueError("only [A-Za-z0-9_-] allowed in paths/filenames: %r" % name)
    return name


class Project:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.yml = os.path.join(self.root, "project.yml")
        if not os.path.exists(self.yml):
            raise FileNotFoundError("project.yml not found in " + self.root)
        with open(self.yml, "r", encoding="utf-8") as f:
            self.meta = json.load(f)

    # ---- metadata fields required by the purchase spec (2.3) ----
    REQUIRED = [
        "project_id", "project_name", "software", "author", "source_url",
        "license", "unit", "description", "target_grade",
    ]

    @property
    def id(self) -> str:
        # W1 (workflow audit 2026-10-30): IDs are validated on EVERY load,
        # not only on create — packager/probe join paths with this value and
        # package deletes+recreates the delivery folder keyed by it.
        return check_safe_name(self.meta["project_id"])

    def field(self, key, default=None):
        return self.meta.get(key, default)

    @property
    def build_dir(self) -> str:
        d = os.path.join(self.root, "build")
        os.makedirs(d, exist_ok=True)
        return d

    @property
    def fcstd_path(self) -> str:
        return os.path.join(self.build_dir, self.id + ".FCStd")

    @property
    def step_path(self) -> str:
        return os.path.join(self.build_dir, self.id + ".step")

    @property
    def renders_dir(self) -> str:
        d = os.path.join(self.build_dir, "renders")
        os.makedirs(d, exist_ok=True)
        return d

    def save_meta(self):
        with open(self.yml, "w", encoding="utf-8") as f:
            json.dump(self.meta, f, ensure_ascii=False, indent=2)

    @classmethod
    def create(cls, cad_root: str, meta: dict) -> "Project":
        pid = check_safe_name(meta["project_id"])
        root = os.path.join(os.path.abspath(cad_root), "projects", pid)
        os.makedirs(root, exist_ok=True)
        os.makedirs(os.path.join(root, "build"), exist_ok=True)
        meta.setdefault("project_name", pid)
        meta.setdefault("unit", "mm")
        meta.setdefault("type", "part")          # part | assembly
        meta.setdefault("target_grade", "A")
        meta.setdefault("software", "FreeCAD 1.1")
        meta.setdefault("author", "")
        meta.setdefault("source_url", "self-originated")
        meta.setdefault("license", "Author-granted commercial redistribution")
        meta.setdefault("description", "")
        meta.setdefault("series", "")
        meta.setdefault("created", time.strftime("%Y-%m-%d"))
        if meta["target_grade"] not in GRADES:
            raise ValueError("target_grade must be C/B/A/S")
        p = cls(root) if os.path.exists(os.path.join(root, "project.yml")) else None
        with open(os.path.join(root, "project.yml"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        return cls(root)

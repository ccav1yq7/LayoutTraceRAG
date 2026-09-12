"""Load only SHA-verified upstream definitions; keep task metadata out of actors."""

import ast
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

REVISION = "bc5018daaf45eea12330941bce68cebd293cfa85"


def digest(value):
    return hashlib.sha256(value).hexdigest()


def verify(root: Path):
    root = root.resolve()
    head = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    if head != REVISION:
        raise ValueError("ECOM_REVISION_MISMATCH")
    # Check all tracked Python, policies and data, not just HEAD (dirty files matter).
    paths = subprocess.check_output(
        ["git", "-C", str(root), "ls-tree", "-r", "--name-only", REVISION], text=True
    ).splitlines()
    hashes = {}
    for name in paths:
        if not (
            name.endswith((".py", ".json", ".md", ".yaml"))
            or name == "requirements.txt"
        ):
            continue
        pinned = subprocess.check_output(
            ["git", "-C", str(root), "show", f"{REVISION}:{name}"]
        )
        target = root / name
        if target.is_symlink() or target.read_bytes() != pinned:
            raise ValueError("ECOM_SOURCE_MODIFIED: " + name)
        hashes[name] = digest(pinned)
    return {"revision": head, "files": hashes}


def definitions(root, relative, wanted, namespace):
    tree = ast.parse((root / relative).read_text())
    nodes = [
        n
        for n in tree.body
        if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and n.name in wanted
    ]
    if {n.name for n in nodes} != set(wanted):
        raise ValueError("MISSING_UPSTREAM_DEFINITION")
    exec(  # noqa: S102 - verified pinned source definitions
        compile(
            ast.Module(body=[*nodes], type_ignores=[]), str(root / relative), "exec"
        ),
        namespace,
    )
    return namespace


def task_models(root):
    return definitions(
        root,
        "utils.py",
        ["ProductInfo", "Task", "Action", "Search", "Validation"],
        {
            "BaseModel": BaseModel,
            "List": list,
            "Dict": dict,
            "Optional": Optional,
            "Any": Any,
        },
    )


def tasks(root):
    ns = task_models(root)
    tree = ast.parse((root / "envs/story/tasks.py").read_text())
    assignment = next(
        n
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "ALL_TASKS" for t in n.targets)
    )
    exec(  # noqa: S102 - verified pinned source definitions
        compile(ast.Module(body=[assignment], type_ignores=[]), "pinned_tasks", "exec"),
        ns,
    )
    return ns["ALL_TASKS"]


def scorer_class(root, action_tools):
    import os
    import traceback

    from langchain_core.messages import AIMessage

    # Execute the exact upstream class, without import-time model constructors.
    ns = {
        "Env": object,
        "Dict": dict,
        "List": list,
        "Tuple": tuple,
        "Optional": Optional,
        "AIMessage": AIMessage,
        "ActionTools": action_tools,
        "os": os,
        "shutil": shutil,
        "hashlib": hashlib,
        "json": json,
        "traceback": traceback,
    }
    return definitions(root, "envs/story/env.py", ["MockStoryEnv"], ns)["MockStoryEnv"]


def data_hashes(root):
    return {p.name: digest(p.read_bytes()) for p in sorted(root.glob("*.json"))}

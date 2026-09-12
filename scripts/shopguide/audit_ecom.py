"""Static upstream inventory; does not import tasks or expose hidden goals to an agent."""

import argparse
import ast
import hashlib
import io
import json
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--check-images", action="store_true")
    args = parser.parse_args()
    root = args.root
    tasks_tree = ast.parse((root / "envs/story/tasks.py").read_text())
    tasks = [
        n
        for n in ast.walk(tasks_tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "Task"
    ]
    image_pattern = (
        r"https?://[^\s<>\"\x27]+?\.(?:jpg|jpeg|png|webp)(?:\?[^\s<>\"\x27]*)?"
    )
    urls = set()
    multimodal = 0
    for task in tasks:
        task_urls = set()
        for node in ast.walk(task):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                task_urls.update(re.findall(image_pattern, node.value, re.IGNORECASE))
        multimodal += bool(task_urls)
        urls.update(task_urls)
    server = ast.parse((root / "agent/servers/offline/server.py").read_text())
    tools = [
        {"name": n.name, "parameters": [a.arg for a in n.args.args]}
        for n in server.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any("mcp.tool" in ast.unparse(d) for d in n.decorator_list)
    ]
    datasets = {}
    for p in sorted((root / "envs/story/data").glob("*.json")):
        data = json.loads(p.read_text())
        datasets[p.name] = {
            "top_level_type": type(data).__name__,
            "top_level_count": len(data),
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        }
    report = {
        "source_revision": subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
        ).strip(),
        "task_count": len(tasks),
        "tasks_with_image_urls": multimodal,
        "unique_image_urls": len(urls),
        "tool_count": len(tools),
        "tools": tools,
        "data_files": datasets,
        "image_availability": "not_checked",
        "official_environment_smoke": "BLOCKED: upstream model/service configuration not integrated",
        "license": "Apache-2.0 code; third-party image redistribution requires separate review",
    }
    if args.check_images:
        checks = []
        for url in sorted(urls):
            result = {"url_sha256": hashlib.sha256(url.encode()).hexdigest()}
            try:
                with urllib.request.urlopen(url, timeout=12) as response:
                    content = response.read(20 * 1024 * 1024 + 1)
                if len(content) > 20 * 1024 * 1024:
                    raise ValueError("image too large")
                with Image.open(io.BytesIO(content)) as image:
                    image.verify()
                result.update(
                    status="available", sha256=hashlib.sha256(content).hexdigest()
                )
            except urllib.error.HTTPError as error:
                result.update(status="unavailable", http_status=error.code)
            except (OSError, ValueError):
                result.update(status="unavailable", reason="network_or_image_error")
            checks.append(result)
        report["image_checks"] = checks
        report["image_availability"] = {
            "available": sum(c["status"] == "available" for c in checks),
            "total": len(checks),
        }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "task_count",
                    "tasks_with_image_urls",
                    "unique_image_urls",
                    "tool_count",
                )
            }
        )
    )

    if args.check_images and report["image_availability"]["available"] != len(urls):
        raise SystemExit(2)


if __name__ == "__main__":
    main()

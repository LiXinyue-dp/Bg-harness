#!/usr/bin/env python3
"""
Workflow ablation harness for Boogu-Image.

Default mode is dry-run: commands and manifest rows are generated but inference
is not executed. Pass --execute on a GPU machine to run Boogu's inference scripts.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


WORKFLOWS = {
    "direct": [],
    "rewrite_default": [
        "--use_rewrite_text_instruction",
        "True",
        "--rewriter_system_prompt_type",
        "default",
    ],
    "rewrite_ppt": [
        "--use_rewrite_text_instruction",
        "True",
        "--rewriter_system_prompt_type",
        "ppt",
    ],
}

GENERATORS = {
    "turbo": {
        "model_dir": "models/Boogu-Image-0.1-Turbo",
        "script": "inference_turbo.py",
        "steps": "4",
        "cfg": "1.0",
        "width": "1024",
        "height": "1024",
    },
    "base": {
        "model_dir": "models/Boogu-Image-0.1-Base",
        "script": "inference.py",
        "steps": "50",
        "cfg": "4.0",
        "width": "1024",
        "height": "1024",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--boogu-root", required=True, help="Path to Boogu-Image repo root.")
    parser.add_argument("--cases", required=True, help="JSONL file with case_id/task_type/prompt.")
    parser.add_argument(
        "--generators",
        nargs="+",
        default=["turbo"],
        choices=sorted(GENERATORS),
        help="Generator variants to run.",
    )
    parser.add_argument(
        "--workflows",
        nargs="+",
        default=["direct", "rewrite_default", "rewrite_ppt"],
        choices=sorted(WORKFLOWS),
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--run-id", default=None)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True)
    mode.add_argument("--execute", action="store_true")
    return parser.parse_args()


def load_cases(path: Path) -> list[dict]:
    cases = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        for key in ("case_id", "task_type", "prompt"):
            if key not in record:
                raise ValueError(f"{path}:{line_no} missing required key: {key}")
        cases.append(record)
    return cases


def command_to_text(cmd: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(cmd)
    return " ".join(shlex.quote(part) for part in cmd)


def build_command(
    python_exe: str,
    boogu_root: Path,
    generator_name: str,
    workflow_name: str,
    case: dict,
    output_image: Path,
    seed: int,
    device: str,
) -> list[str]:
    generator = GENERATORS[generator_name]
    cmd = [
        python_exe,
        str(boogu_root / generator["script"]),
        "--seed",
        str(seed),
        "--pretrained_pipeline_name_or_path",
        str(boogu_root / generator["model_dir"]),
        "--num_inference_steps",
        generator["steps"],
        "--height",
        generator["height"],
        "--width",
        generator["width"],
        "--text_guidance_scale",
        generator["cfg"],
        "--instruction",
        case["prompt"],
        "--output_image_path",
        str(output_image),
        "--num_images_per_instruction",
        "1",
        "--device",
        device,
        "--rewriter_device",
        device,
    ]
    cmd.extend(WORKFLOWS[workflow_name])
    return cmd


def main() -> int:
    args = parse_args()
    boogu_root = Path(args.boogu_root).resolve()
    cases_path = Path(args.cases).resolve()
    execute = bool(args.execute)

    if not (boogu_root / "inference.py").exists():
        raise FileNotFoundError(f"Cannot find inference.py under {boogu_root}")

    run_id = args.run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = boogu_root / "outputs" / "harness_workflow_ablation" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "manifest.jsonl"

    cases = load_cases(cases_path)
    env = os.environ.copy()
    env["device"] = args.device
    env.setdefault("HF_MODULES_CACHE", str(boogu_root / ".hf_modules_cache"))

    planned = 0
    with manifest_path.open("w", encoding="utf-8") as manifest:
        for case in cases:
            for generator_name in args.generators:
                for workflow_name in args.workflows:
                    image_name = (
                        f"{case['case_id']}__{generator_name}__"
                        f"{workflow_name}__seed{args.seed}.png"
                    )
                    output_image = run_dir / "images" / image_name
                    output_image.parent.mkdir(parents=True, exist_ok=True)
                    cmd = build_command(
                        sys.executable,
                        boogu_root,
                        generator_name,
                        workflow_name,
                        case,
                        output_image,
                        args.seed,
                        args.device,
                    )
                    row = {
                        "run_id": run_id,
                        "case_id": case["case_id"],
                        "task_type": case["task_type"],
                        "prompt": case["prompt"],
                        "generator": generator_name,
                        "workflow": workflow_name,
                        "seed": args.seed,
                        "device": args.device,
                        "image_path": str(output_image),
                        "command": command_to_text(cmd),
                        "mode": "execute" if execute else "dry_run",
                    }
                    planned += 1
                    if execute:
                        started = time.time()
                        completed = subprocess.run(
                            cmd,
                            cwd=str(boogu_root),
                            env=env,
                            text=True,
                        )
                        row["latency_sec"] = round(time.time() - started, 3)
                        row["exit_code"] = completed.returncode
                    else:
                        row["latency_sec"] = None
                        row["exit_code"] = None
                    manifest.write(json.dumps(row, ensure_ascii=False) + "\n")
                    manifest.flush()

    print(f"planned_runs={planned}")
    print(f"run_dir={run_dir}")
    print(f"manifest={manifest_path}")
    print("mode=execute" if execute else "mode=dry_run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


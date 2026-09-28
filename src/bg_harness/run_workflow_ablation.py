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
import re
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
    "search_manual": [],
    "sketch_svg": [],
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


def slugify(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return value.strip("_") or "case"


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


def enriched_prompt_for_search(case: dict) -> str:
    facts = case.get("search_facts") or []
    if not facts:
        return case["prompt"]
    facts_text = "\n".join(f"- {fact}" for fact in facts)
    return (
        f"{case['prompt']}\n\n"
        "Use the following factual visual references while preserving the user's intent:\n"
        f"{facts_text}"
    )


def svg_for_sketch(case: dict, width: int = 1024, height: int = 1024) -> str:
    spec = case.get("sketch_spec") or {}
    sketch_type = spec.get("type", "three_shapes")

    if sketch_type == "three_shapes":
        return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="100%" height="100%" fill="#f6f4ef"/>
  <polygon points="560,345 675,405 675,555 560,615 445,555 445,405" fill="#2fad58" opacity="0.45"/>
  <text x="560" y="640" text-anchor="middle" font-family="Arial" font-size="30" fill="#333">green cone behind both</text>
  <rect x="210" y="430" width="220" height="220" fill="#d83b31"/>
  <text x="320" y="690" text-anchor="middle" font-family="Arial" font-size="30" fill="#333">red cube left</text>
  <circle cx="705" cy="540" r="120" fill="#276ef1"/>
  <text x="705" y="690" text-anchor="middle" font-family="Arial" font-size="30" fill="#333">blue sphere right</text>
</svg>"""

    if sketch_type == "three_column_slide":
        title = spec.get("title", "Agentic Image Generation")
        labels = spec.get("columns", ["DirectGen", "RewriteGen", "SearchGen"])
        return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="100%" height="100%" fill="#ffffff"/>
  <text x="80" y="115" font-family="Arial" font-size="54" font-weight="700" fill="#18212f">{title}</text>
  <line x1="80" y1="150" x2="944" y2="150" stroke="#d8dee8" stroke-width="3"/>
  <rect x="90" y="245" width="245" height="360" rx="16" fill="#edf4ff" stroke="#8eb6f2" stroke-width="3"/>
  <rect x="390" y="245" width="245" height="360" rx="16" fill="#f3f0ff" stroke="#a99bed" stroke-width="3"/>
  <rect x="690" y="245" width="245" height="360" rx="16" fill="#ecf8f1" stroke="#83c99a" stroke-width="3"/>
  <text x="212" y="355" text-anchor="middle" font-family="Arial" font-size="38" font-weight="700" fill="#18212f">{labels[0]}</text>
  <text x="512" y="355" text-anchor="middle" font-family="Arial" font-size="38" font-weight="700" fill="#18212f">{labels[1]}</text>
  <text x="812" y="355" text-anchor="middle" font-family="Arial" font-size="38" font-weight="700" fill="#18212f">{labels[2]}</text>
  <text x="212" y="465" text-anchor="middle" font-family="Arial" font-size="27" fill="#334155">Prompt</text>
  <text x="512" y="465" text-anchor="middle" font-family="Arial" font-size="27" fill="#334155">Prompt polish</text>
  <text x="812" y="465" text-anchor="middle" font-family="Arial" font-size="27" fill="#334155">External facts</text>
  <path d="M345 425 L375 425" stroke="#18212f" stroke-width="4" marker-end="url(#arrow)"/>
  <path d="M645 425 L675 425" stroke="#18212f" stroke-width="4" marker-end="url(#arrow)"/>
  <defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#18212f"/></marker></defs>
</svg>"""

    raise ValueError(f"Unsupported sketch_spec.type: {sketch_type}")


def write_svg_sketch(case: dict, run_dir: Path) -> Path:
    sketch_dir = run_dir / "sketches"
    sketch_dir.mkdir(parents=True, exist_ok=True)
    svg_path = sketch_dir / f"{slugify(case['case_id'])}.svg"
    svg_path.write_text(svg_for_sketch(case), encoding="utf-8")
    return svg_path


def workflow_is_applicable(case: dict, workflow_name: str) -> bool:
    if workflow_name == "search_manual":
        return bool(case.get("search_facts"))
    if workflow_name == "sketch_svg":
        return bool(case.get("sketch_spec"))
    return True


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
    run_dir: Path,
) -> list[str]:
    generator = GENERATORS[generator_name]
    instruction = case["prompt"]
    input_image_paths = []
    script = generator["script"]
    model_dir = generator["model_dir"]

    if workflow_name == "search_manual":
        instruction = enriched_prompt_for_search(case)

    if workflow_name == "sketch_svg":
        svg_path = write_svg_sketch(case, run_dir)
        instruction = (
            f"{case['prompt']}\n\n"
            "Use the provided layout sketch as a structural reference. Preserve the "
            "relative positions, counts, text regions, and overall composition, but "
            "render the final image in a polished style."
        )
        input_image_paths = [str(svg_path)]
        if generator_name == "turbo":
            script = "inference_turbo.py"
            model_dir = "models/Boogu-Image-0.1-Edit-Turbo"
        else:
            script = "inference.py"
            model_dir = "models/Boogu-Image-0.1-Edit"

    cmd = [
        python_exe,
        str(boogu_root / script),
        "--seed",
        str(seed),
        "--pretrained_pipeline_name_or_path",
        str(boogu_root / model_dir),
        "--num_inference_steps",
        generator["steps"],
        "--height",
        generator["height"],
        "--width",
        generator["width"],
        "--text_guidance_scale",
        generator["cfg"],
        "--instruction",
        instruction,
        "--output_image_path",
        str(output_image),
        "--num_images_per_instruction",
        "1",
        "--device",
        device,
        "--rewriter_device",
        device,
    ]
    if input_image_paths:
        cmd.extend(["--input_image_paths", *input_image_paths])
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
                    if not workflow_is_applicable(case, workflow_name):
                        continue
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
                        run_dir,
                    )
                    row = {
                        "run_id": run_id,
                        "case_id": case["case_id"],
                        "task_type": case["task_type"],
                        "prompt": case["prompt"],
                        "generator": generator_name,
                        "workflow": workflow_name,
                        "generation_prompt": enriched_prompt_for_search(case)
                        if workflow_name == "search_manual"
                        else case["prompt"],
                        "search_facts": case.get("search_facts", []),
                        "sketch_spec": case.get("sketch_spec"),
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

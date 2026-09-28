# Bg-harness

Lightweight experiment harness for Boogu-Image agentic image generation.

The first experiment is a workflow ablation:

- `direct`: prompt -> Boogu generator
- `rewrite_default`: prompt -> Boogu default instruction rewriter -> generator
- `rewrite_ppt`: prompt -> Boogu PPT/design rewriter -> generator
- `search_manual`: prompt + curated factual notes -> Boogu generator
- `sketch_svg`: prompt + generated SVG sketch -> Boogu Edit generator

This repo is intentionally GPU-optional. You can debug command generation and
manifest logging locally with `--dry-run`, then run the same config on a GPU
server with `--execute`.

## What You Need

This harness does not vendor Boogu-Image. Clone and install Boogu separately:

```bash
git clone https://github.com/boogu-project/Boogu-Image.git
cd Boogu-Image
pip install -r requirements/torch2.7-cu126.txt
pip install -e .
```

Download checkpoints on the machine that will execute inference:

```bash
huggingface-cli download Boogu/Boogu-Image-0.1-Turbo --local-dir models/Boogu-Image-0.1-Turbo
huggingface-cli download Boogu/Boogu-Image-0.1-Base --local-dir models/Boogu-Image-0.1-Base
```

Local machines without GPU can skip checkpoint download and use dry-run.

## Local Dry Run

From this harness repo:

```bash
python -m src.bg_harness.run_workflow_ablation \
  --boogu-root ../Boogu-Image \
  --cases data/cases_t2i_smoke.jsonl \
  --generators turbo \
  --dry-run
```

On Windows PowerShell:

```powershell
python -m src.bg_harness.run_workflow_ablation `
  --boogu-root ../Boogu-Image `
  --cases data/cases_t2i_smoke.jsonl `
  --generators turbo `
  --dry-run
```

The dry run writes a manifest under:

```text
../Boogu-Image/outputs/harness_workflow_ablation/<run_id>/manifest.jsonl
```

## GPU Execution

On the GPU server, after Boogu is installed and checkpoints are present:

```bash
python -m src.bg_harness.run_workflow_ablation \
  --boogu-root /path/to/Boogu-Image \
  --cases data/cases_t2i_smoke.jsonl \
  --generators turbo \
  --execute
```

Add Base when you want the slower quality comparison:

```bash
python -m src.bg_harness.run_workflow_ablation \
  --boogu-root /path/to/Boogu-Image \
  --cases data/cases_t2i_smoke.jsonl \
  --generators turbo base \
  --execute
```

## First Research Question

For each prompt category, compare whether rewrite helps or hurts:

- simple prompts: rewrite may over-complicate intent
- poster/PPT/text prompts: PPT rewrite may improve layout and text specificity
- world-knowledge prompts: rewrite alone may not fix missing knowledge
- spatial/multi-constraint prompts: rewrite may clarify constraints but not guarantee counting

This becomes the seed data for a GenRouter-style routing policy.

## Boundary Experiment

After the smoke test, use the boundary set to test when DirectGen is no longer
enough:

```bash
python -m src.bg_harness.run_workflow_ablation \
  --boogu-root /path/to/Boogu-Image \
  --cases data/cases_boundary_v1.jsonl \
  --generators turbo \
  --workflows direct search_manual sketch_svg \
  --execute
```

Interpretation:

- `search_manual` should help when the problem is missing world knowledge.
- `sketch_svg` should help when the problem is layout, counting, or spatial structure.
- If `direct` already wins, that case should stay on the cheap route.

Note: `sketch_svg` uses `models/Boogu-Image-0.1-Edit`, because it passes the
generated SVG sketch as a reference image. With `--generators turbo`, it uses
`models/Boogu-Image-0.1-Edit-Turbo`; with `--generators base`, it uses
`models/Boogu-Image-0.1-Edit`.

## Internal Boogu Dependencies

This starter does not require Boogu internal code, prompts, or datasets. It uses
public Boogu inference scripts and public checkpoints.

Internal resources are only needed if you want to reproduce official Boogu Arena
numbers, official prompt sets, official human preference logs, training data,
reward models, or unpublished multi-reference training/evaluation pipelines.

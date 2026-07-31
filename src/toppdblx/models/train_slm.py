"""Stage `models.train_slm`: LoRA fine-tune SmolLM2 for text-to-JSON parsing.

R1. MLX-LM rather than torch or transformers: this is an M1 Max, and MLX-LM is the proven path
from chem_sage and chatPDB, with native `--mask-prompt` and `--report-to wandb`.

Three hazards from previous training rounds on this machine are handled here rather than
rediscovered:

1. **Never disable `grad_checkpoint` to fit memory.** When the working set is exceeded with
   checkpointing off, output is silently corrupted rather than erroring: garbage learning rate,
   garbage tokens-per-second, garbage loss. Checkpointing is forced on and cannot be turned off
   through this stage.
2. **Resume restarts the iteration counter and the learning-rate schedule.** MLX-LM resumes
   weights only, so `--iters` must be the *remaining* budget, and the offset is recorded in the
   manifest so reported iteration numbers can be reconciled afterwards.
3. **`steps_per_eval` must be a multiple of `steps_per_report`**, so validation points land on
   training points and the two curves are directly comparable. They are deliberately *not* equal:
   mlx-lm only writes to W&B inside these two callbacks, so setting both to 200 produced six
   points across a 1,200-iteration run. Training loss is already computed every step and costs
   nothing to report; a validation pass costs a full sweep of the validation set (15 to 60
   seconds here). So report often, validate rarely, and keep the ratio a whole number.

   The eval cadence is set by *where the curve moves*, not by a round number. On round 02, 98%
   of the entire validation-loss collapse (0.991 to 0.020) happened between iteration 1 and
   iteration 200, so a cadence of 100 would put a single point inside the only interesting part
   of the run. At 50 there are three, for about 18% wall-clock overhead. That is worth paying
   because the early iterations are where a broken run declares itself, and noticing at
   iteration 50 rather than 200 saves far more time than the evaluations cost.

Run `bash scripts/preflight.sh` first. It is not optional: swap during training has been fatal
on this machine, and Spotlight has starved a training subprocess of CPU for hours at a time.

    bash scripts/preflight.sh
    ./run.sh models.train_slm
    ./run.sh models.train_slm --iters 50 --smoke      # prove the pipeline first
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from .. import config
from ..manifest import Manifest

STAGE = "models.train_slm"

# No precision suffix: `-bf16` and `-fp16` do not exist. Worth knowing that HuggingFace answers
# 401 for a repo that is not there, so a typo in a model id reports as an authentication failure.
BASE_MODEL = "mlx-community/SmolLM2-360M-Instruct"
DEFAULT_ITERS = 2000
DEFAULT_BATCH = 4
DEFAULT_LORA_LAYERS = 16
DEFAULT_LR = 1e-4

# The p99 training example is about 1,050 characters, so 1024 tokens leaves headroom. Set below
# the longest example and the answer is silently truncated, which teaches the model to emit
# invalid JSON: the one failure mode that makes the whole thing useless.
DEFAULT_MAX_SEQ = 1024

WANDB_PROJECT = "toppdblx"

# mlx-lm names the Weights & Biases run `os.path.basename(adapter_path)` and offers no flag to
# override it, so an adapter directory called `adapters` produces a run called "adapters". Rather
# than fight that, the adapter directory is *given* the run's name: one identity for the run, its
# checkpoints and its logs, and a checkpoint on disk can always be traced back to the run that
# produced it.
RUNS_DIR_NAME = "runs"
RUN_PREFIX = "r1-parse-residual"


def model_slug(model: str) -> str:
    """`mlx-community/SmolLM2-360M-Instruct` -> `smollm2-360m`."""
    tail = model.rsplit("/", 1)[-1].lower()
    tail = tail.replace("-instruct", "").replace("-bf16", "").replace("-fp16", "")
    return tail


def next_run_name(runs_dir: Path, model: str) -> str:
    """`r1-parse-residual-smollm2-360m-round03`, numbered from the runs already on disk.

    Round numbers come from the directory listing rather than from a counter file, so they stay
    correct if a run is deleted and cannot drift out of step with what actually exists.
    """
    stem = f"{RUN_PREFIX}-{model_slug(model)}-round"
    existing = [d.name for d in runs_dir.glob(f"{stem}*")] if runs_dir.exists() else []
    used = {int(m.group(1)) for name in existing
            if (m := re.search(rf"^{re.escape(stem)}(\d+)$", name))}
    return f"{stem}{max(used, default=0) + 1:02d}"


def check_dataless(paths: list[Path]) -> list[Path]:
    """macOS Optimize Mac Storage evicts files from iCloud-synced folders, leaving zero-length
    stubs that read as empty rather than failing. Training on those looks like a bad dataset."""
    return [p for p in paths if p.exists() and p.stat().st_size == 0]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, default=config.INTERIM_DIR / "slm")
    parser.add_argument("--adapter-dir", type=Path, default=None,
                        help="defaults to data/interim/slm/runs/<run-name>")
    parser.add_argument("--run-name", default=None,
                        help="names the W&B run AND its adapter directory; "
                             "defaults to the next round number")
    parser.add_argument("--model", default=BASE_MODEL)
    parser.add_argument("--iters", type=int, default=DEFAULT_ITERS,
                        help="REMAINING iterations; a resume restarts the counter at zero")
    parser.add_argument("--iter-offset", type=int, default=0,
                        help="iterations already completed, for honest reporting after a resume")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH)
    parser.add_argument("--num-layers", type=int, default=DEFAULT_LORA_LAYERS)
    parser.add_argument("--learning-rate", type=float, default=DEFAULT_LR)
    parser.add_argument("--max-seq-length", type=int, default=DEFAULT_MAX_SEQ)
    parser.add_argument("--steps-per-eval", type=int, default=50,
                        help="validation cadence; must be a multiple of --steps-per-report")
    parser.add_argument("--steps-per-report", type=int, default=10,
                        help="training-loss cadence; this is what sets W&B plot density")
    parser.add_argument("--resume", action="store_true",
                        help="continue from the existing adapter (weights only)")
    parser.add_argument("--lora-rank", type=int, default=8)
    parser.add_argument("--lora-dropout", type=float, default=0.05,
                        help="0.05 by default: fidelity to the rules kept climbing while "
                             "residual resolution plateaued, which is overfitting to the "
                             "teacher, and mild dropout targets exactly that")
    parser.add_argument("--warmup", type=int, default=50)
    parser.add_argument("--end-learning-rate", type=float, default=1e-5)
    parser.add_argument("--constant-lr", action="store_true",
                        help="disable the cosine schedule and hold the learning rate flat")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--smoke", action="store_true",
                        help="short run to prove the pipeline; skips wandb")
    parser.add_argument("--no-wandb", action="store_true")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    runs_dir = args.data_dir / RUNS_DIR_NAME
    if args.adapter_dir is None:
        args.run_name = args.run_name or next_run_name(runs_dir, args.model)
        args.adapter_dir = runs_dir / args.run_name
    else:
        # An explicit directory still determines the run name, because mlx-lm reads it from the
        # basename. Reporting it here keeps that link visible rather than surprising.
        args.run_name = args.adapter_dir.name

    # Enforced rather than merely documented: an eval cadence that is not a whole multiple of the
    # report cadence puts validation points between training points, and the two curves can no
    # longer be read against each other at any iteration.
    if args.steps_per_eval % args.steps_per_report:
        raise SystemExit(
            f"--steps-per-eval ({args.steps_per_eval}) must be a multiple of "
            f"--steps-per-report ({args.steps_per_report}), or the loss curves will not align")

    train = args.data_dir / "train.jsonl"
    valid = args.data_dir / "valid.jsonl"
    for path in (train, valid):
        if not path.exists():
            raise SystemExit(f"{path} missing. Run: ./run.sh models.build_slm_dataset")
    empty = check_dataless([train, valid])
    if empty:
        raise SystemExit(f"zero-length (iCloud-evicted?) data files: {empty}")

    if not shutil.which("mlx_lm.lora"):
        # The console script may not be on PATH; the module form always is.
        command = [sys.executable, "-m", "mlx_lm", "lora"]
    else:
        command = ["mlx_lm.lora"]

    args.adapter_dir.mkdir(parents=True, exist_ok=True)
    use_wandb = not (args.smoke or args.no_wandb)

    # `lr_schedule` and `lora_parameters` have no command-line flags in mlx-lm; they can only
    # arrive through a YAML config. mlx-lm merges the two by preferring anything given on the
    # command line, so the config carries only what the flags cannot express.
    #
    # The schedule exists because of a measured defect, not as routine hygiene. At a flat 1e-4
    # the round 01 sweep produced malformed JSON in a spike: `unparseable` counts ran
    # 12, 41, 3, 2 across checkpoints, which is what too high a learning rate looks like once a
    # model is near convergence. Warmup steadies the opening iterations, and decaying to 1e-5
    # keeps the late ones from thrashing.
    #
    # Decay is measured over the iterations that remain *after* warmup, so the schedule actually
    # reaches `end_learning_rate` on the final step rather than stopping part-way down.
    training_config: dict[str, object] = {
        "lora_parameters": {"rank": args.lora_rank, "dropout": args.lora_dropout,
                            "scale": 20.0},
    }
    if not args.constant_lr:
        training_config["lr_schedule"] = {
            "name": "cosine_decay",
            "arguments": [args.learning_rate,
                          max(1, args.iters - args.warmup),
                          args.end_learning_rate],
            "warmup": args.warmup,
            "warmup_init": args.learning_rate / 100,
        }
    config_path = args.adapter_dir / "training_config.yaml"
    config_path.write_text(yaml.safe_dump(training_config, sort_keys=False))

    command += [
        "--model", args.model,
        "--train",
        "--data", str(args.data_dir),
        "--adapter-path", str(args.adapter_dir),
        "--iters", str(args.iters),
        "--batch-size", str(args.batch_size),
        "--num-layers", str(args.num_layers),
        "--learning-rate", str(args.learning_rate),
        "--max-seq-length", str(args.max_seq_length),
        "--steps-per-eval", str(args.steps_per_eval),
        "--steps-per-report", str(args.steps_per_report),
        # Train on the completion only. Without this most of the loss is spent learning to
        # echo the condition string back.
        "--seed", str(args.seed),
        "--config", str(config_path),
        "--mask-prompt",
        # Never turned off. See the module docstring: disabling this silently corrupts output.
        "--grad-checkpoint",
    ]
    if args.resume:
        command += ["--resume-adapter-file", str(args.adapter_dir / "adapters.safetensors")]
    if use_wandb:
        # `--project-name`, not `--wandb`: mlx-lm 0.31 renamed it when swanlab was added.
        command += ["--report-to", "wandb", "--project-name", WANDB_PROJECT]

    params = {
        "run_name": args.run_name,
        "model": args.model, "iters": args.iters, "iter_offset": args.iter_offset,
        "effective_iter_range": [args.iter_offset, args.iter_offset + args.iters],
        "batch_size": args.batch_size, "num_layers": args.num_layers,
        "learning_rate": args.learning_rate, "max_seq_length": args.max_seq_length,
        "lora_rank": args.lora_rank, "lora_dropout": args.lora_dropout,
        "lr_schedule": training_config.get("lr_schedule"), "seed": args.seed,
        "mask_prompt": True, "grad_checkpoint": True, "resume": args.resume,
        "wandb": use_wandb, "smoke": args.smoke,
    }

    with Manifest(STAGE, params=params) as m:
        m.add_input(train).add_input(valid)
        print(f"  run: {args.run_name}   (W&B project {WANDB_PROJECT})")
        print(f"  adapters: {args.adapter_dir}\n")
        print("  " + " ".join(command) + "\n")
        started = datetime.now(timezone.utc)
        # HF_TOKEN in the environment is stale and shadows the valid cached login, so every
        # hub call 401s even for public repos. Dropped here rather than relying on the caller
        # remembering, since this subprocess always downloads a base model.
        environment = {k: v for k, v in os.environ.items() if k != "HF_TOKEN"}
        result = subprocess.run(command, text=True, env=environment)
        if result.returncode != 0:
            raise RuntimeError(f"mlx_lm.lora exited {result.returncode}")

        adapter = args.adapter_dir / "adapters.safetensors"
        m.add_output(args.adapter_dir).note(
            wall_seconds=round((datetime.now(timezone.utc) - started).total_seconds(), 1),
            adapter_exists=adapter.exists(),
            adapter_bytes=adapter.stat().st_size if adapter.exists() else 0,
            # Recorded because a resume restarts MLX-LM's counter at zero: the numbers in the
            # log are not the true iteration count without this offset.
            true_iters_completed=args.iter_offset + args.iters,
        )
        print(f"\n  adapter: {adapter}")
        if args.iter_offset:
            print(f"  true iteration count: {args.iter_offset + args.iters} "
                  f"(the log restarted at 0 on resume)")
        print(f"  next: ./run.sh models.eval_slm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Explicit local ACT training: preflight -> split -> official trainer -> reload."""
from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import numpy as np
from lerobot.datasets.lerobot_dataset import LeRobotDataset
from ai import dataset_contract as contract
from ai.train.validate_dataset import validate_dataset, format_report
from ai.train.pipeline_utils import write_json, fingerprint, split_episodes, train_stats, cache_estimate, environment
from ai.train.checkpoint import check_files, verify_checkpoint, assert_policy_contract, portable_contract

NORMALIZATION = '{"VISUAL":"IDENTITY","STATE":"MEAN_STD","ACTION":"QUANTILE10"}'
DEFAULTS = dict(seed=0, steps=100000, batch_size=64, num_workers=4, chunk_size=40, n_action_steps=10,
                save_freq=20000, log_freq=50, learning_rate=.0001, device="cuda", use_amp=True,
                ram_cache=False, dev_small=False, validation_fraction=.2, pilot_only=False, execution_prefix_weight=1.,
                joint_physical_balance=False)
# Device/workers/cache affect execution, not the trained architecture/optimizer.
STRICT_RESUME = ("seed", "batch_size", "chunk_size", "n_action_steps", "learning_rate", "dev_small",
                 "validation_fraction", "pilot_only", "use_amp", "save_freq", "log_freq", "execution_prefix_weight",
                 "joint_physical_balance")


def copy_state_baseline(dataset):
    """Physical hold: copy all six current joint positions and gripper index 6."""
    sums, n = np.zeros(7), 0
    numeric = dataset.hf_dataset.select_columns(["observation.state", "action"]).with_format("numpy")
    for batch in numeric.iter(batch_size=1024):
        target, state = np.asarray(batch["action"]), np.asarray(batch["observation.state"])
        if target.shape != state.shape or target.shape[-1] != 7:
            raise ValueError("Hold baseline requires the 7D contract")
        sums += np.abs(target - state).sum(0)
        n += len(target)
    return {"definition": "hold measured joint0..joint5 positions (rad) and current command gripper at index 6",
            "l1_mean": float((sums / n).mean()), "l1_per_dimension": (sums / n).tolist(), "sample_count": n}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", default=contract.SUCCESS_ROOT)
    parser.add_argument("--dataset-repo-id", default=contract.SUCCESS_REPO_ID)
    parser.add_argument("--output-dir", help="New run directory; existing paths are refused")
    parser.add_argument("--job-name", default="indy7_act")
    parser.add_argument("--resume", type=Path, help="Prior pipeline run, step, or pretrained_model directory")
    for name in ("seed", "steps", "batch_size", "num_workers", "chunk_size", "n_action_steps", "save_freq", "log_freq"):
        parser.add_argument("--" + name.replace("_", "-"), type=int, default=None)
    parser.add_argument("--learning-rate", "--lr", type=float, default=None)
    parser.add_argument("--validation-fraction", type=float, default=None)
    parser.add_argument("--device", default=None)
    for name in ("use_amp", "ram_cache", "dev_small", "pilot_only", "joint_physical_balance"):
        parser.add_argument("--" + name.replace("_", "-"), action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--init-checkpoint",type=Path,help="Weights-only initialization; new optimizer/scheduler, same verified dataset/architecture")
    parser.add_argument("--execution-prefix-weight",type=float,default=None,help="Optional supervised loss weight on the existing n_action_steps prefix; default1")
    parser.add_argument("--dry-run", action="store_true", help="Validate and write plan, never start optimization")
    return parser.parse_args(argv)


def resolve_settings(args, previous=None):
    settings = {**DEFAULTS, **(previous or {})}
    for key in DEFAULTS:
        explicit = getattr(args, key, None)
        if explicit is not None:
            if previous and key in STRICT_RESUME and explicit != previous.get(key,DEFAULTS[key]):
                raise ValueError(f"Resume setting mismatch: {key}")
            settings[key] = explicit
    if not math.isfinite(settings["execution_prefix_weight"]) or settings["execution_prefix_weight"]<1:raise ValueError("execution_prefix_weight must be finite >=1")
    for name in ("steps", "batch_size", "chunk_size", "n_action_steps", "save_freq", "log_freq"):
        if settings[name] < 1:
            raise ValueError(f"{name} must be positive")
    if settings["num_workers"] < 0 or settings["n_action_steps"] > settings["chunk_size"]:
        raise ValueError("Invalid workers or action chunk settings")
    if not math.isfinite(settings["learning_rate"]) or settings["learning_rate"] <= 0:
        raise ValueError("learning_rate must be finite and positive")
    if not 0 < settings["validation_fraction"] < 1:
        raise ValueError("Validation fraction must be between 0 and 1")
    if settings["device"] == "cpu":
        settings["use_amp"] = False
    return settings


def command(args, settings, split, training_root, resume_model=None):
    cmd = [sys.executable, "-m", "ai.train.lerobot_entry"]
    if resume_model:
        cmd += [f"--config_path={resume_model / 'train_config.json'}", "--resume", "true"]
    else:
        cmd += ["--policy.type", "act", "--policy.push_to_hub", "false", "--policy.use_vae", "true",
                "--policy.normalization_mapping", NORMALIZATION, "--policy.pretrained_backbone_weights", "null",
                "--policy.chunk_size", str(settings["chunk_size"]), "--policy.n_action_steps", str(settings["n_action_steps"]),
                "--use_policy_training_preset", "false", "--optimizer.type", "adamw",
                "--optimizer.lr", str(settings["learning_rate"]), "--optimizer.weight_decay", "0.0001", "--optimizer.grad_clip_norm", "10.0",
                "--scheduler.type", "cosine_decay_with_warmup", "--scheduler.num_warmup_steps", str(min(2000, settings["steps"] // 10)),
                "--scheduler.num_decay_steps", str(settings["steps"]), "--scheduler.peak_lr", str(settings["learning_rate"]),
                "--scheduler.decay_lr", str(settings["learning_rate"] * .01)]
        if settings["dev_small"]:
            cmd += ["--policy.dim_model", "64", "--policy.n_heads", "8", "--policy.dim_feedforward", "128",
                    "--policy.n_encoder_layers", "1", "--policy.n_vae_encoder_layers", "1"]
    if not resume_model and getattr(args,"init_checkpoint",None):cmd += ["--policy.pretrained_path",str(check_files(args.init_checkpoint))]
    cmd += ["--dataset.repo_id", args.dataset_repo_id, "--dataset.root", str(Path(args.dataset_dir).resolve()),
            "--dataset.episodes", json.dumps(split["train"]), "--dataset.use_imagenet_stats", "false", "--dataset.video_backend", "pyav",
            "--policy.device", settings["device"], "--policy.use_amp", str(settings["use_amp"]).lower(),
            "--output_dir", str(training_root), "--job_name", args.job_name, "--seed", str(settings["seed"]),
            "--batch_size", str(settings["batch_size"]), "--num_workers", str(settings["num_workers"]),
            "--steps", str(settings["steps"]), "--eval_freq", "0", "--log_freq", str(settings["log_freq"]),
            "--save_freq", str(settings["save_freq"])]
    return cmd


def resume_plan(path, dataset_fingerprint, args):
    # Standalone act train --resume must register ACT before decoding its config.
    from lerobot.policies.act.configuration_act import ACTConfig  # noqa: F401
    from lerobot.configs.policies import PreTrainedConfig
    from safetensors import safe_open
    model = check_files(path)
    manifest_path = next((p / "run.json" for p in model.parents if (p / "run.json").is_file()), None)
    if manifest_path is None:
        raise ValueError("Resume requires this pipeline's run.json provenance")
    previous = json.loads(manifest_path.read_text())
    if previous["dataset_fingerprint"] != dataset_fingerprint or previous["dataset_repo_id"] != args.dataset_repo_id:
        raise ValueError("Resume Dataset fingerprint or identity mismatch")
    settings = resolve_settings(args, previous["settings"])
    config = json.loads((model / "train_config.json").read_text())
    if previous.get("training_config_sha256") and hashlib.sha256((model / "train_config.json").read_bytes()).hexdigest() != previous["training_config_sha256"]:
        raise ValueError("Saved training config fingerprint mismatch")
    for name in ("batch_size", "seed"):
        if config[name] != settings[name]:
            raise ValueError(f"Saved training config mismatch: {name}")
    for name in ("chunk_size", "n_action_steps"):
        if config["policy"][name] != settings[name]:
            raise ValueError(f"Saved policy config mismatch: {name}")
    if config["optimizer"]["lr"] != settings["learning_rate"] or config["dataset"]["episodes"] != previous["split"]["train"]:
        raise ValueError("Saved optimizer or split mismatch")
    policy_config = PreTrainedConfig.from_pretrained(str(model))
    assert_policy_contract(policy_config)
    saved_policy = json.loads((model / "config.json").read_text())
    if any(config["policy"].get(key) != value for key, value in saved_policy.items() if key != "pretrained_path"):
        raise ValueError("Saved policy and training config mismatch")
    if saved_policy["normalization_mapping"] != json.loads(NORMALIZATION):
        raise ValueError("Normalization config mismatch")
    state = model.parent / "training_state"
    required = ("training_step.json", "optimizer_param_groups.json", "optimizer_state.safetensors",
                "scheduler_state.json", "rng_state.safetensors")
    for name in required:
        if not (state / name).is_file():
            raise ValueError(f"Cannot resume: missing training_state/{name}")
    step = json.loads((state / "training_step.json").read_text())["step"]
    if not isinstance(step, int) or not 0 < step < settings["steps"]:
        raise ValueError(f"Resume requires total --steps > saved step {step}")
    groups = json.loads((state / "optimizer_param_groups.json").read_text())
    scheduler = json.loads((state / "scheduler_state.json").read_text())
    with safe_open(state / "optimizer_state.safetensors", framework="pt") as tensors:
        if not tensors.keys() or not groups:
            raise ValueError("Empty optimizer state")
        optimizer_keys = len(tensors.keys())
        optimizer_steps = [float(tensors.get_tensor(key).item()) for key in tensors.keys() if key.endswith("/step")]
        if not optimizer_steps or any(not math.isfinite(value) or not 0 < value <= step for value in optimizer_steps):
            raise ValueError("Invalid optimizer update steps")
    with safe_open(state / "rng_state.safetensors", framework="pt") as tensors:
        if not tensors.keys():
            raise ValueError("Empty RNG state")
    if scheduler.get("last_epoch") != step:
        raise ValueError("Scheduler step differs from saved training step")
    return model, previous, settings, {"step": step, "optimizer_tensor_keys": optimizer_keys,
                                       "optimizer_update_step_range": [min(optimizer_steps), max(optimizer_steps)],
                                       "scheduler_last_epoch": scheduler["last_epoch"],
                                       "scheduler_horizon_preserved": config["scheduler"]}


def main(argv=None):
    args = parse_args() if argv is None else parse_args(argv)
    if args.resume and args.init_checkpoint:raise ValueError("Choose resume or weights-only init, not both")
    report = validate_dataset(args.dataset_dir, repo_id=args.dataset_repo_id)
    print(format_report(report), flush=True)
    if not report["trainable"]:
        raise SystemExit("Dataset validation blocked training; no Dataset loading or training started.")
    root = Path(args.dataset_dir).expanduser().resolve()
    args.dataset_dir = str(root)
    identity = fingerprint(root)
    resume_model = None
    resumed = None
    if args.resume:
        resume_model, previous, settings, resumed = resume_plan(args.resume, identity, args)
        split = previous["split"]
    else:
        settings = resolve_settings(args)
        split = split_episodes([ep["episode_index"] for ep in report["episode_manifest"]], settings["seed"],
                               settings["validation_fraction"], settings["pilot_only"])
    if args.init_checkpoint:
        source=check_files(args.init_checkpoint)
        source_provenance=json.loads((source/'training_provenance.json').read_text())
        config=json.loads((source/'config.json').read_text())
        from lerobot.policies.act.configuration_act import ACTConfig  # noqa: F401
        from lerobot.configs.policies import PreTrainedConfig
        assert_policy_contract(PreTrainedConfig.from_pretrained(str(source)))
        if (source_provenance['dataset_fingerprint']!=identity or
                source_provenance['dataset_repo_id']!=args.dataset_repo_id):
            raise ValueError('Initialization dataset differs')
        if (settings['dev_small'] or config['dim_model']!=512 or
                config['chunk_size']!=settings['chunk_size'] or
                config['n_action_steps']!=settings['n_action_steps'] or
                config['normalization_mapping']!=json.loads(NORMALIZATION) or
                config['use_vae'] is not True):
            raise ValueError('Weights-only init architecture mismatch')
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    output = Path(args.output_dir or f"outputs/act_{run_id}").expanduser().resolve()
    if output.exists() or output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError("Output must be a new directory outside the Dataset; existing runs are never overwritten")
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"run_id": run_id, "status": "planning", "dataset_root": str(root), "dataset_repo_id": args.dataset_repo_id,
                "dataset_fingerprint": identity, "settings": settings, "split": split, "environment": environment(),
                "initialized_from":str(check_files(args.init_checkpoint)) if args.init_checkpoint else None,
                "resumed_from": str(resume_model) if resume_model else None, "resume_state": resumed,
                "warnings": list(report["warnings"]), "robot_safety_verified": False}
    try:
        write_json(output / "dataset_validation.json", report)
        stats_root = output / "train_metadata"
        stats_root.mkdir()
        write_json(stats_root / "stats.json", train_stats(root, split["train"]))
        frames = sum(ep["length"] for ep in report["episode_manifest"] if ep["episode_index"] in split["train"])
        estimate = cache_estimate(frames)
        manifest["cache"] = {**estimate, "enabled": settings["ram_cache"]}
        if settings["ram_cache"]:
            manifest["warnings"].append("RAM cache explicitly enabled: RGB estimate excludes model, optimizer, workers and action tensors")
            available = os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
            if estimate["rgb_bytes"] > available // 2:
                raise ValueError("RGB cache alone exceeds half available host memory; use --no-ram-cache")
        if not split["validation"]:
            manifest["warnings"].append("Pilot only: no held-out validation; generalization is not evaluated")
        if resumed and settings["steps"] > resumed["scheduler_horizon_preserved"]["num_decay_steps"]:
            manifest["warnings"].append("Total steps extended beyond the original LR horizon; original scheduler definition/state is preserved without restarting warmup")
        training_root = output / "training"
        copied_model = None
        if resume_model:
            copied_step = training_root / "checkpoints" / resume_model.parent.name
            shutil.copytree(resume_model.parent, copied_step)
            copied_model = copied_step / "pretrained_model"
        cmd = command(args, settings, split, training_root, copied_model)
        manifest["command"] = cmd
        manifest["status"] = "planned" if args.dry_run else "training"
        write_json(output / "run.json", manifest)
        print(f"Run: {output}\nSplit: {split}\nRGB cache estimate: {estimate['rgb_gib']:.3f} GiB; enabled={settings['ram_cache']}", flush=True)
        if args.dry_run:
            return manifest
        import torch
        requested_device = torch.device(settings["device"])
        if requested_device.type == "cuda" and not torch.cuda.is_available():
            raise ValueError("CUDA requested but unavailable; choose --device cpu explicitly for a CPU run")
        env = dict(os.environ)
        env.update(PYTHONPATH=str(Path(__file__).resolve().parents[2]) + os.pathsep + env.get("PYTHONPATH", ""),
                   LEROBOT_RAM_CACHE="1" if settings["ram_cache"] else "0", LEROBOT_RAM_CACHE_WORKERS=str(settings["num_workers"]),
                   INDY7_ACT_TRAIN_STATS=str(stats_root), INDY7_ACT_EXECUTION_PREFIX_WEIGHT=str(settings["execution_prefix_weight"]),
                   INDY7_ACT_JOINT_PHYSICAL_BALANCE="1" if settings["joint_physical_balance"] else "0",
                   HF_HUB_OFFLINE="1", HF_DATASETS_OFFLINE="1")
        with (output / "training.log").open("w", encoding="utf-8") as log:
            with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
                                  cwd=Path(__file__).resolve().parents[2]) as process:
                for line in process.stdout:
                    log.write(line)
                    print(line, end="", flush=True)
                code = process.wait()
            if code:
                raise RuntimeError(f"Official trainer exited {code}; see {output / 'training.log'}")
        if fingerprint(root) != identity:
            raise ValueError("Dataset changed during training; checkpoint cannot be approved")
        manifest["phase"] = "checkpoint_validation"
        last = check_files(training_root / "checkpoints/last")
        bundle_contract = portable_contract()
        for step_dir in (training_root / "checkpoints").iterdir():
            if step_dir.is_symlink() or not step_dir.is_dir():
                continue
            model = step_dir / "pretrained_model"
            write_json(model / "indy7_contract.json", bundle_contract)
            write_json(model / "training_provenance.json", {"dataset_fingerprint": identity, "dataset_repo_id": args.dataset_repo_id,
                       "split": split, "settings": settings, "run_id": run_id})
        actual_step = json.loads((last.parent / "training_state/training_step.json").read_text())["step"]
        if actual_step != settings["steps"]:
            raise ValueError("Last checkpoint did not reach requested total step")
        try:
            verification = verify_checkpoint(last, device=settings["device"])
        except Exception as exc:
            write_json(output / "checkpoint_validation.json", {"valid": False, "error": str(exc)})
            raise
        write_json(output / "checkpoint_validation.json", verification)
        from ai.train.evaluate_overfit import evaluate_checkpoint
        manifest["phase"] = "offline_validation"
        evaluation = evaluate_checkpoint(last, root, args.dataset_repo_id, split["validation"],
                                         device=settings["device"], batch_size=min(settings["batch_size"], 8))
        write_json(output / "validation_metrics.json", evaluation)
        if fingerprint(root) != identity:
            raise ValueError("Dataset changed during evaluation; checkpoint cannot be approved")
        manifest.update(status="complete", checkpoint=str(last), completed_step=actual_step,
                        training_config_sha256=hashlib.sha256((last / "train_config.json").read_bytes()).hexdigest(),
                        checkpoint_validation=verification, offline_validation=evaluation,
                        eligible_for_inference=True, robot_safety_verified=False)
        write_json(output / "run.json", manifest)
        write_json(output / "result.json", manifest)
        print(f"ACT training complete. Verified checkpoint: {last}\nOffline action metrics are not robot task success rates.", flush=True)
        return manifest
    except BaseException as exc:
        manifest.update(status="failed", eligible_for_inference=False, error=f"{type(exc).__name__}: {exc}")
        write_json(output / "run.json", manifest)
        write_json(output / "result.json", manifest)
        raise


if __name__ == "__main__":
    main()

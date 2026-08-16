#!/usr/bin/env python3
"""Build each RMW variant and run the complete 16-way benchmark matrix.

The default configuration runs:

    4 variants x 9 sizes x 5 repeats x 4 communication paths = 720 cases

Each patch is applied independently to the configured baseline revision.  The
rmw_fastrtps source checkout is restored to its original branch and revision
when the script exits, including when a build or benchmark fails.
"""

import argparse
from pathlib import Path
import shlex
import subprocess
import sys


DEFAULT_SIZES = "64,1024,4096,16384,65536,262144,1048576,4194304,16777216"
DEFAULT_COMMUNICATIONS = "inter_process,intra_process_va"
DEFAULT_MODES = "cpu,memfd"

VARIANTS = {
    "baseline": None,
    "unique_ptr": "0001-fastrtps-reuse-unique-ptr-buffer.patch",
    "lazy": "0002-fastrtps-reuse-fastbuffer-lazy.patch",
    "reserve": "0003-fastrtps-reuse-fastbuffer-reserve.patch",
}


def git(repo, *args, capture=False):
    command = ["git", "-C", str(repo), *args]
    if capture:
        return subprocess.check_output(command, text=True).strip()
    subprocess.run(command, check=True)
    return ""


def shell_quote(path):
    return shlex.quote(str(path))


def source_command(setup_files, command):
    sources = "; ".join(f"source {shell_quote(path)}" for path in setup_files)
    return f"{sources}; {command}"


def run_shell(command, cwd, setup_files, dry_run=False):
    full_command = source_command(setup_files, command)
    print(f"$ (cd {shell_quote(cwd)} && {full_command})", file=sys.stderr, flush=True)
    if not dry_run:
        subprocess.run(["bash", "-lc", full_command], cwd=cwd, check=True)


def resolve_workspace_root(script_path):
    source_root = script_path.resolve().parents[4]
    if (source_root / "src/memfd_buffer_backend").is_dir():
        return source_root
    current_root = Path.cwd().resolve()
    if (current_root / "src/memfd_buffer_backend").is_dir():
        return current_root
    raise RuntimeError(
        "Could not infer the workspace root; pass --workspace-root explicitly"
    )


def parse_csv_values(value, kind):
    values = [item.strip() for item in value.split(",") if item.strip()]
    if not values:
        raise ValueError(f"--{kind} must not be empty")
    return values


def check_output_path(output_dir, variants, overwrite):
    output_dir.mkdir(parents=True, exist_ok=True)
    existing = [output_dir / f"{variant}.csv" for variant in variants]
    existing.extend(output_dir / "raw" / f"{variant}.csv" for variant in variants)
    existing = [path for path in existing if path.exists()]
    if existing and not overwrite:
        names = ", ".join(path.name for path in existing)
        raise RuntimeError(
            f"Output already exists: {names}. Use --overwrite or choose another "
            "--output-dir."
        )


def restore_source(repo, original_branch, original_head):
    # The initial clean-worktree check makes this reset recoverable for the
    # source changes made by this script.  It does not overwrite user changes.
    git(repo, "reset", "--hard", original_head)
    if original_branch == "HEAD":
        git(repo, "switch", "--detach", original_head)
    else:
        git(repo, "switch", original_branch)


def main():
    parser = argparse.ArgumentParser(
        description="Run all 720 default SHM/CPU pub-sub benchmark cases."
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        help="ROS 2 workspace containing src/memfd_buffer_backend",
    )
    parser.add_argument(
        "--ros2-root", type=Path, default=Path("~/ros2_lyrical").expanduser(),
        help="Lyrical source workspace (default: ~/ros2_lyrical)",
    )
    parser.add_argument(
        "--rmw-repo",
        type=Path,
        help="rmw_fastrtps repository; defaults to <ros2-root>/src/ros2/rmw_fastrtps",
    )
    parser.add_argument(
        "--baseline", default="origin/lyrical",
        help="baseline git revision (default: origin/lyrical)",
    )
    parser.add_argument(
        "--variants", default=",".join(VARIANTS),
        help="comma-separated variants (default: baseline,unique_ptr,lazy,reserve)",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        help="directory for one CSV per variant (default: benchmark-results-16way)",
    )
    parser.add_argument("--overwrite", action="store_true", help="overwrite CSV output")
    parser.add_argument(
        "--build-base", type=Path,
        help="build root (default: <ros2-root>/build_16way_<variant>)",
    )
    parser.add_argument(
        "--install-base", type=Path,
        help="install root (default: <ros2-root>/install_16way_<variant>)",
    )
    parser.add_argument("--sizes", default=DEFAULT_SIZES)
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--rate-hz", type=int, default=10)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260812)
    parser.add_argument("--publisher-affinity", default="8")
    parser.add_argument("--subscriber-affinity", default="9")
    parser.add_argument("--intra-affinity", default="8")
    parser.add_argument("--discovery-wait", type=float, default=3.0)
    parser.add_argument("--communications", default=DEFAULT_COMMUNICATIONS)
    parser.add_argument("--modes", default=DEFAULT_MODES)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print the 720-case plan without changing source or running commands",
    )
    args = parser.parse_args()

    script_path = Path(__file__).resolve()
    workspace_root = (
        args.workspace_root.expanduser().resolve()
        if args.workspace_root
        else resolve_workspace_root(script_path)
    )
    ros2_root = args.ros2_root.expanduser().resolve()
    rmw_repo = (
        args.rmw_repo.expanduser().resolve()
        if args.rmw_repo
        else ros2_root / "src/ros2/rmw_fastrtps"
    )
    benchmark_root = script_path.parents[2]
    runner = benchmark_root / "memfd_buffer_backend_benchmark/scripts/run_e2e_benchmark.py"
    patch_dir = benchmark_root / "patches"
    underlay_setup = ros2_root / "install/setup.bash"
    workspace_setup = workspace_root / "install/setup.bash"
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir
        else benchmark_root / "benchmark-results-16way"
    )

    variants = parse_csv_values(args.variants, "variants")
    unknown = [variant for variant in variants if variant not in VARIANTS]
    if unknown:
        parser.error(f"unknown variants: {', '.join(unknown)}")
    sizes = parse_csv_values(args.sizes, "sizes")
    communications = parse_csv_values(args.communications, "communications")
    modes = parse_csv_values(args.modes, "modes")
    if args.count <= args.warmup:
        parser.error("--count must be greater than --warmup")
    if args.rate_hz <= 0 or args.repeats <= 0 or args.warmup < 0:
        parser.error("rate, repeats, and warm-up values are invalid")

    cases_per_variant = len(sizes) * len(communications) * len(modes) * args.repeats
    total_cases = len(variants) * cases_per_variant
    print(
        f"plan: {len(variants)} variants x {len(sizes)} sizes x "
        f"{args.repeats} repeats x {len(communications)} communications x "
        f"{len(modes)} backends = {total_cases} cases",
        file=sys.stderr,
    )
    if args.dry_run:
        for variant in variants:
            patch = VARIANTS[variant]
            print(f"variant={variant} patch={patch or 'none'}", file=sys.stderr)
        return 0

    required = [underlay_setup, workspace_setup, rmw_repo, runner]
    missing = [path for path in required if not path.exists()]
    if missing:
        raise RuntimeError("Missing required path(s): " + ", ".join(map(str, missing)))
    check_output_path(output_dir, variants, args.overwrite)

    original_branch = git(rmw_repo, "rev-parse", "--abbrev-ref", "HEAD", capture=True)
    original_head = git(rmw_repo, "rev-parse", "HEAD", capture=True)
    if git(rmw_repo, "status", "--porcelain", capture=True):
        raise RuntimeError(
            f"{rmw_repo} has local changes. Commit or stash them before running."
        )

    baseline = git(rmw_repo, "rev-parse", args.baseline, capture=True)
    patch_paths = {
        variant: patch_dir / patch
        for variant, patch in VARIANTS.items()
        if patch is not None
    }
    missing_patches = [path for path in patch_paths.values() if not path.exists()]
    if missing_patches:
        raise RuntimeError("Missing patch file(s): " + ", ".join(map(str, missing_patches)))

    try:
        for variant in variants:
            print(f"=== {variant} ===", file=sys.stderr, flush=True)
            git(rmw_repo, "reset", "--hard", baseline)
            patch = VARIANTS[variant]
            if patch:
                git(rmw_repo, "apply", str(patch_paths[variant]))

            build_base = (
                args.build_base.expanduser().resolve() / variant
                if args.build_base
                else ros2_root / f"build_16way_{variant}"
            )
            install_base = (
                args.install_base.expanduser().resolve() / variant
                if args.install_base
                else ros2_root / f"install_16way_{variant}"
            )
            build_command = (
                "colcon build --packages-select rmw_fastrtps_cpp "
                "--allow-overriding rmw_fastrtps_cpp "
                f"--build-base {shell_quote(build_base)} "
                f"--install-base {shell_quote(install_base)} "
                "--cmake-args -DCMAKE_BUILD_TYPE=Release"
            )
            run_shell(build_command, ros2_root, [underlay_setup])

            output = output_dir / f"{variant}.csv"
            raw_output = output_dir / "raw" / f"{variant}.csv"
            runner_command = (
                f"python3 {shell_quote(runner)} "
                f"--output {shell_quote(output)} "
                f"--raw-output {shell_quote(raw_output)} "
                f"--variant {shell_quote(variant)} "
                f"--sizes {shell_quote(','.join(sizes))} "
                f"--count {args.count} --rate-hz {args.rate_hz} "
                f"--warmup {args.warmup} --repeats {args.repeats} "
                f"--seed {args.seed} "
                f"--publisher-affinity {shell_quote(args.publisher_affinity)} "
                f"--subscriber-affinity {shell_quote(args.subscriber_affinity)} "
                f"--intra-affinity {shell_quote(args.intra_affinity)} "
                f"--discovery-wait {args.discovery_wait} "
                f"--communications {shell_quote(','.join(communications))} "
                f"--modes {shell_quote(','.join(modes))}"
            )
            run_shell(
                runner_command,
                workspace_root,
                [underlay_setup, workspace_setup, install_base / "setup.bash"],
            )
    finally:
        print("restoring rmw_fastrtps source checkout", file=sys.stderr, flush=True)
        restore_source(rmw_repo, original_branch, original_head)

    print(f"completed: {total_cases} cases; results in {output_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)

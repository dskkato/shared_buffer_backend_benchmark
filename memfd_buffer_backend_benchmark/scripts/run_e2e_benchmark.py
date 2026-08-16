#!/usr/bin/env python3
"""Run old memfd backend CPU/memfd pub/sub latency comparisons."""

import argparse
import csv
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import time


RESULT_FIELDS = [
    "variant",
    "repeat",
    "communication",
    "backend",
    "size_bytes",
    "received",
    "measured",
    "va_matches",
    "p50_us",
    "p95_us",
    "p99_us",
]
BENCHMARK_FIELDS = RESULT_FIELDS[2:]
RAW_FIELDS = [
    "variant",
    "repeat",
    "communication",
    "backend",
    "size_bytes",
    "sample_index",
    "publish_timestamp_ns",
    "publish_duration_ns",
    "receive_timestamp_ns",
    "e2e_latency_ns",
]


def command(role, mode, size, count, rate, warmup, affinity, raw_output=None):
    command = [
        "ros2",
        "run",
        "memfd_buffer_backend_benchmark",
        "memfd_buffer_backend_e2e_node",
        "--role",
        role,
        "--mode",
        mode,
        "--size",
        str(size),
        "--count",
        str(count),
        "--rate-hz",
        str(rate),
        "--warmup",
        str(warmup),
    ]
    if affinity:
        command = ["taskset", "-c", affinity] + command
    if raw_output is not None:
        command.extend(["--raw-output", str(raw_output)])
    return command


def parse_result(output, communication, mode, size):
    lines = [line for line in output.splitlines() if line.startswith("RESULT,")]
    if not lines:
        raise RuntimeError(
            f"no result: {communication}, {mode}, {size}; process output: {output}"
        )

    fields = next(csv.reader([lines[-1]]))
    if len(fields) != len(BENCHMARK_FIELDS) + 2 or fields[0] != "RESULT" or fields[1] != "ok":
        raise RuntimeError(f"benchmark error: {lines[-1]}")

    result = dict(zip(BENCHMARK_FIELDS, fields[2:]))
    if (
        result["communication"] != communication
        or result["backend"] != mode
        or int(result["size_bytes"]) != size
    ):
        raise RuntimeError(f"unexpected result: {lines[-1]}")
    return result


def terminate_process(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def read_raw_case(raw_paths):
    publishes = {}
    receives = []
    for raw_path in raw_paths:
        if raw_path is None:
            continue
        raw_path = Path(raw_path)
        if not raw_path.exists():
            raise RuntimeError(f"missing raw timing file: {raw_path}")
        with raw_path.open(newline="", encoding="utf-8") as stream:
            for line_number, fields in enumerate(csv.reader(stream), start=1):
                if not fields:
                    continue
                if fields[0] != "RAW":
                    raise RuntimeError(
                        f"unexpected raw timing record in {raw_path}:{line_number}"
                    )
                if fields[1] == "publish" and len(fields) == 4:
                    timestamp_ns = int(fields[2])
                    if timestamp_ns in publishes:
                        raise RuntimeError(
                            f"duplicate publish timestamp in raw timing: {timestamp_ns}"
                        )
                    publishes[timestamp_ns] = int(fields[3])
                elif fields[1] == "receive" and len(fields) == 7:
                    receives.append(
                        {
                            "publish_timestamp_ns": int(fields[2]),
                            "receive_timestamp_ns": int(fields[3]),
                            "e2e_latency_ns": int(fields[4]),
                            "measured": int(fields[5]),
                            "sample_index": int(fields[6]),
                        }
                    )
                else:
                    raise RuntimeError(
                        f"malformed raw timing record in {raw_path}:{line_number}"
                    )

    rows = []
    for receive in receives:
        if receive["measured"] != 1:
            continue
        timestamp_ns = receive["publish_timestamp_ns"]
        if timestamp_ns not in publishes:
            raise RuntimeError(
                f"no matching publish timing for timestamp: {timestamp_ns}"
            )
        rows.append(
            {
                "sample_index": receive["sample_index"],
                "publish_timestamp_ns": timestamp_ns,
                "publish_duration_ns": publishes[timestamp_ns],
                "receive_timestamp_ns": receive["receive_timestamp_ns"],
                "e2e_latency_ns": receive["e2e_latency_ns"],
            }
        )
    rows.sort(key=lambda row: row["sample_index"])
    return rows


def run_inter_process_case(
    mode, size, count, rate, warmup, env, publisher_affinity, subscriber_affinity,
    discovery_wait, raw_dir=None
):
    communication = "inter_process"
    publisher_raw = Path(raw_dir) / "publisher.raw" if raw_dir else None
    subscriber_raw = Path(raw_dir) / "subscriber.raw" if raw_dir else None
    subscriber = None
    publisher = None
    output = ""
    try:
        subscriber = subprocess.Popen(
            command(
                "sub", mode, size, count, rate, warmup, subscriber_affinity, subscriber_raw
            ),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        time.sleep(discovery_wait)
        publisher = subprocess.Popen(
            command(
                "pub", mode, size, count, rate, warmup, publisher_affinity, publisher_raw
            ),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            output, _ = subscriber.communicate(timeout=(count / rate) + 30.0)
        except subprocess.TimeoutExpired:
            subscriber.kill()
            output, _ = subscriber.communicate()
            raise RuntimeError(
                f"timeout: {communication}, {mode}, {size}; subscriber output: {output}"
            )
    finally:
        terminate_process(publisher)
        terminate_process(subscriber)
    return parse_result(output, communication, mode, size), read_raw_case(
        (publisher_raw, subscriber_raw)
    )


def run_intra_process_case(mode, size, count, rate, warmup, env, affinity, raw_dir=None):
    communication = "intra_process_va"
    raw_output = Path(raw_dir) / "intra.raw" if raw_dir else None
    process = subprocess.Popen(
        command("intra", mode, size, count, rate, warmup, affinity, raw_output),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        output, _ = process.communicate(timeout=(count / rate) + 30.0)
    except subprocess.TimeoutExpired:
        process.kill()
        output, _ = process.communicate()
        raise RuntimeError(
            f"timeout: {communication}, {mode}, {size}; process output: {output}"
        )
    if process.returncode != 0:
        raise RuntimeError(
            f"process failed: {communication}, {mode}, {size}, returncode={process.returncode}; "
            f"output: {output}"
        )
    return parse_result(output, communication, mode, size), read_raw_case((raw_output,))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="memfd-old-pubsub-results.csv")
    parser.add_argument(
        "--sizes",
        default="64,1024,4096,16384,65536,262144,1048576,4194304,16777216",
    )
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--rate-hz", type=int, default=10)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260812)
    parser.add_argument("--variant", default="unspecified")
    parser.add_argument("--communications", default="inter_process,intra_process_va")
    parser.add_argument("--modes", default="cpu,memfd")
    parser.add_argument("--publisher-affinity")
    parser.add_argument("--subscriber-affinity")
    parser.add_argument("--intra-affinity")
    parser.add_argument("--discovery-wait", type=float, default=3.0)
    parser.add_argument("--timing-dir")
    parser.add_argument(
        "--raw-output",
        type=Path,
        help="write one row per measured sample with publish and end-to-end timing",
    )
    args = parser.parse_args()

    if args.count <= args.warmup:
        parser.error("--count must be greater than --warmup")
    if args.rate_hz <= 0:
        parser.error("--rate-hz must be positive")
    if args.warmup < 0 or args.repeats <= 0:
        parser.error("--warmup must be non-negative and --repeats must be positive")

    env = os.environ.copy()
    env["RMW_IMPLEMENTATION"] = "rmw_fastrtps_cpp"
    sizes = [int(value) for value in args.sizes.split(",") if value]
    communications = [value for value in args.communications.split(",") if value]
    modes = [value for value in args.modes.split(",") if value]
    raw_output = args.raw_output.expanduser().resolve() if args.raw_output else None
    if raw_output:
        raw_output.parent.mkdir(parents=True, exist_ok=True)
    cases = [
        (communication, mode, size)
        for size in sizes
        for mode in modes
        for communication in communications
    ]
    rows = []
    raw_rows = []
    raw_temp = tempfile.TemporaryDirectory(prefix="memfd-e2e-raw-") if raw_output else None
    try:
        raw_temp_dir = Path(raw_temp.name) if raw_temp else None
        for repeat in range(1, args.repeats + 1):
            repeat_cases = list(cases)
            random.Random(args.seed + repeat - 1).shuffle(repeat_cases)
            for communication, mode, size in repeat_cases:
                print(
                    f"running repeat={repeat} {communication} {mode} {size} B",
                    file=sys.stderr,
                    flush=True,
                )
                case_env = env.copy()
                if args.timing_dir:
                    os.makedirs(args.timing_dir, exist_ok=True)
                    timing_path = os.path.join(
                        args.timing_dir,
                        f"{args.variant}-repeat{repeat}-{communication}-{mode}-{size}.csv",
                    )
                    try:
                        os.remove(timing_path)
                    except FileNotFoundError:
                        pass
                    case_env["RMW_FASTRTPS_PUBLISH_TIMING_FILE"] = timing_path
                    case_env["RMW_FASTRTPS_PUBLISH_TIMING_VARIANT"] = args.variant

                case_raw_dir = None
                if raw_temp_dir:
                    case_raw_dir = raw_temp_dir / (
                        f"repeat{repeat}-{communication}-{mode}-{size}"
                    )
                    case_raw_dir.mkdir()
                if communication == "inter_process":
                    result, case_raw_rows = run_inter_process_case(
                        mode, size, args.count, args.rate_hz, args.warmup, case_env,
                        args.publisher_affinity, args.subscriber_affinity,
                        args.discovery_wait, case_raw_dir,
                    )
                else:
                    result, case_raw_rows = run_intra_process_case(
                        mode, size, args.count, args.rate_hz, args.warmup, case_env,
                        args.intra_affinity, case_raw_dir,
                    )
                if communication == "intra_process_va" and int(result["va_matches"]) != args.count:
                    raise RuntimeError(f"intra-process VA sharing failed: {result}")
                result["variant"] = args.variant
                result["repeat"] = repeat
                rows.append(result)

                if raw_output:
                    expected_samples = args.count - args.warmup
                    if len(case_raw_rows) != expected_samples:
                        raise RuntimeError(
                            f"expected {expected_samples} raw samples, found {len(case_raw_rows)}: "
                            f"{communication}, {mode}, {size}"
                        )
                    for raw_row in case_raw_rows:
                        raw_rows.append(
                            {
                                "variant": args.variant,
                                "repeat": repeat,
                                "communication": communication,
                                "backend": mode,
                                "size_bytes": size,
                                **raw_row,
                            }
                        )

        with open(args.output, "w", newline="", encoding="utf-8") as output_file:
            writer = csv.DictWriter(output_file, fieldnames=RESULT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        if raw_output:
            with raw_output.open("w", newline="", encoding="utf-8") as output_file:
                writer = csv.DictWriter(output_file, fieldnames=RAW_FIELDS)
                writer.writeheader()
                writer.writerows(raw_rows)
    finally:
        if raw_temp is not None:
            raw_temp.cleanup()

    if raw_output:
        print(f"raw sample timings written to {raw_output}", file=sys.stderr)


if __name__ == "__main__":
    main()

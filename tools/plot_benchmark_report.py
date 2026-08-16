#!/usr/bin/env python3
"""Generate figures for BENCHMARK_REPORT.md from the tracked CSV results."""

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


VARIANTS = ("baseline", "unique_ptr", "lazy", "reserve")
VARIANT_LABELS = {
    "baseline": "baseline",
    "unique_ptr": "unique_ptr",
    "lazy": "lazy",
    "reserve": "reserve",
}
VARIANT_COLORS = {
    "baseline": "#4C566A",
    "unique_ptr": "#5E81AC",
    "lazy": "#A3BE8C",
    "reserve": "#D08770",
}
PATHS = (
    ("inter_process", "cpu", "Inter CPU"),
    ("inter_process", "memfd", "Inter SHM"),
    ("intra_process_va", "cpu", "Intra CPU"),
    ("intra_process_va", "memfd", "Intra SHM"),
)
PATH_COLORS = {
    "Inter CPU": "#5E81AC",
    "Inter SHM": "#D08770",
    "Intra CPU": "#A3BE8C",
    "Intra SHM": "#B48EAD",
}
SIZES = (64, 1024, 4096, 16384, 65536, 262144, 1048576, 4194304, 16777216)
ONE_MIB = 1048576
PERCENTILES = (("p50_us", 0.50), ("p95_us", 0.95), ("p99_us", 0.99))


def size_label(size):
    for divisor, suffix in ((1024**2, "MiB"), (1024, "KiB")):
        if size >= divisor and size % divisor == 0:
            return f"{size // divisor} {suffix}"
    return f"{size} B"


def read_raw_results(data_dir):
    values = defaultdict(list)
    raw_dir = data_dir / "raw"
    for variant in VARIANTS:
        path = raw_dir / f"{variant}.csv"
        if not path.exists():
            raise RuntimeError(f"missing raw timing file: {path}")
        with path.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        if len(rows) != 3600:
            raise RuntimeError(f"expected 3600 rows in {path}, found {len(rows)}")
        for row in rows:
            if row["variant"] != variant:
                raise RuntimeError(
                    f"variant mismatch in {path}: {row['variant']} != {variant}"
                )
            key = (
                row["variant"],
                row["communication"],
                row["backend"],
                int(row["size_bytes"]),
            )
            values[key].append(
                {
                    "repeat": int(row["repeat"]),
                    "publish_duration_us": int(row["publish_duration_ns"]) / 1000.0,
                    "e2e_latency_us": int(row["e2e_latency_ns"]) / 1000.0,
                }
            )
    return dict(values)


def percentile(values, p):
    ordered = sorted(values)
    return ordered[int((len(ordered) - 1) * p)]


def aggregate_raw_results(raw_results):
    for key, rows in raw_results.items():
        if len(rows) != 100:
            raise RuntimeError(
                f"expected 100 raw samples for {key}, found {len(rows)}"
            )
    return {
        key: {
            metric: percentile(
                [row["e2e_latency_us"] for row in rows], p
            )
            for metric, p in PERCENTILES
        }
        for key, rows in raw_results.items()
    }


def distribution_x_limit(raw_results, metric, tick_step):
    values = [
        row[metric]
        for variant in VARIANTS
        for backend in ("cpu", "memfd")
        for row in raw_results[(variant, "inter_process", backend, ONE_MIB)]
    ]
    return max(tick_step, math.ceil(max(values) * 1.05 / tick_step) * tick_step)


def plot_one_mib_histogram(
    raw_results,
    output_dir,
    backend,
    backend_label,
    name,
    x_limit,
    metric,
    bin_width,
    tick_step,
    metric_label,
    figure_label,
):
    figure, axes = plt.subplots(
        nrows=len(VARIANTS),
        ncols=1,
        sharex=True,
        figsize=(10.5, 10.5),
    )
    bins = range(0, x_limit + bin_width, bin_width)
    for axis, variant in zip(axes, VARIANTS):
        rows = raw_results[(variant, "inter_process", backend, ONE_MIB)]
        color = VARIANT_COLORS[variant]
        values = [row[metric] for row in rows]
        axis.hist(
            values,
            bins=bins,
            color=color,
            alpha=0.55,
            edgecolor=color,
            linewidth=0.5,
        )
        axis.axvline(
            median(values),
            color=color,
            linestyle="--",
            linewidth=1.4,
            alpha=0.85,
        )
        axis.set_xlim(0, x_limit)
        axis.grid(True, axis="both", color="#D8DEE9", linewidth=0.7)
        axis.set_axisbelow(True)
        axis.set_ylabel("Frequency (samples)")
        axis.set_title(
            f"{VARIANT_LABELS[variant]} (n={len(values)}; dashed line = median)",
            loc="left",
            fontsize=10,
        )
    axes[-1].set_xticks(range(0, x_limit + 1, tick_step))
    axes[-1].set_xlabel(f"{metric_label}; shared x-axis across CPU and SHM plots")
    figure.suptitle(f"1 MiB inter-process {backend_label} {figure_label} frequency distribution")
    figure.text(
        0.01,
        0.01,
        f"Raw measured samples: five repeats × 20 samples per variant; common {bin_width} µs bins",
        ha="left",
        va="bottom",
        fontsize=8,
        color="#4C566A",
    )
    figure.tight_layout(rect=(0, 0.04, 1, 0.97))
    save_figure(figure, output_dir, name)


def plot_baseline_inter_histogram(raw_results, output_dir, x_limit):
    figure, axes = plt.subplots(
        nrows=2,
        ncols=1,
        sharex=True,
        figsize=(10.5, 6.8),
    )
    bins = range(0, x_limit + 250, 250)
    for axis, backend, backend_label in zip(
        axes, ("cpu", "memfd"), ("Inter CPU", "Inter SHM")
    ):
        rows = raw_results[("baseline", "inter_process", backend, ONE_MIB)]
        values = [row["e2e_latency_us"] for row in rows]
        axis.hist(
            values,
            bins=bins,
            color=PATH_COLORS[backend_label],
            alpha=0.55,
            edgecolor=PATH_COLORS[backend_label],
            linewidth=0.5,
        )
        axis.axvline(
            median(values),
            color=PATH_COLORS[backend_label],
            linestyle="--",
            linewidth=1.4,
            alpha=0.85,
        )
        axis.set_xlim(0, x_limit)
        axis.grid(True, axis="both", color="#D8DEE9", linewidth=0.7)
        axis.set_axisbelow(True)
        axis.set_ylabel("Frequency (samples)")
        axis.set_title(
            f"{backend_label} baseline (n={len(values)}; dashed line = median)",
            loc="left",
            fontsize=10,
        )
    axes[-1].set_xticks(range(0, x_limit + 1, 2000))
    axes[-1].set_xlabel(
        "End-to-end latency (µs); common x-axis and 250 µs bins"
    )
    figure.suptitle("1 MiB baseline inter-process latency frequency distribution")
    figure.tight_layout(rect=(0, 0, 1, 0.95))
    save_figure(figure, output_dir, "1m-baseline-inter-latency-distribution")


def series(results, variant, communication, backend, metric):
    return [
        results[(variant, communication, backend, size)][metric]
        for size in SIZES
    ]


def configure_axis(axis):
    axis.set_xscale("log", base=2)
    axis.set_xticks(SIZES)
    axis.set_xticklabels([size_label(size) for size in SIZES], rotation=35, ha="right")
    axis.grid(True, which="major", axis="both", color="#D8DEE9", linewidth=0.7)
    axis.set_axisbelow(True)


def save_figure(figure, output_dir, name):
    figure.savefig(output_dir / f"{name}.png", dpi=180, bbox_inches="tight")
    plt.close(figure)


def raw_average_series(raw_results, variant, communication, backend):
    return [
        mean(
            row["e2e_latency_us"]
            for row in raw_results[(variant, communication, backend, size)]
        )
        for size in SIZES
    ]


def plot_variant_paths(results, raw_results, output_dir, variant):
    figure, axis = plt.subplots(figsize=(11, 6.5))
    for communication, backend, title in PATHS:
        p50 = series(results, variant, communication, backend, "p50_us")
        average = raw_average_series(raw_results, variant, communication, backend)
        color = PATH_COLORS[title]
        axis.plot(
            SIZES, p50, marker="o", linewidth=2.2, color=color,
            label=title
        )
        axis.plot(
            SIZES,
            average,
            linestyle="--",
            linewidth=1.2,
            alpha=0.75,
            color=color,
        )
    configure_axis(axis)
    axis.set_yscale("log")
    axis.set_ylim(bottom=10)
    axis.set_xlabel("Payload size")
    axis.set_ylabel("Latency (µs)")
    axis.set_title(
        f"{VARIANT_LABELS[variant].capitalize()} p50 and average latency across communication and buffer paths"
    )
    axis.legend(title="p50 (solid); average (dashed)", frameon=True)
    figure.tight_layout()
    save_figure(figure, output_dir, f"{variant}-path-latency")


def plot_inter_variant_comparison(results, output_dir, backend, backend_label, name):
    figure, axis = plt.subplots(figsize=(11, 6.5))
    for variant in VARIANTS:
        p50 = series(results, variant, "inter_process", backend, "p50_us")
        p95 = series(results, variant, "inter_process", backend, "p95_us")
        color = VARIANT_COLORS[variant]
        axis.plot(
            SIZES, p50, marker="o", linewidth=2.2, color=color,
            label=VARIANT_LABELS[variant]
        )
        axis.plot(SIZES, p95, linestyle="--", linewidth=1.0, alpha=0.65, color=color)
    configure_axis(axis)
    axis.set_yscale("log")
    axis.set_ylim(bottom=500)
    axis.set_xlabel("Payload size")
    axis.set_ylabel("Latency (µs)")
    axis.set_title(f"Inter-process {backend_label} latency: patch comparison")
    axis.legend(title="p50 (solid); p95 (dashed)", ncol=2, frameon=True)
    figure.tight_layout()
    save_figure(figure, output_dir, name)


def main():
    package_dir = Path(__file__).resolve().parents[1]
    default_data_dir = package_dir / "benchmark-results-16way-rerun"
    default_output_dir = package_dir / "figures"
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=default_data_dir,
        help="benchmark CSV directory (default: benchmark-results-16way-rerun)",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=default_output_dir
    )
    args = parser.parse_args()

    args.data_dir = args.data_dir.expanduser().resolve()
    args.output_dir = args.output_dir.expanduser().resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    raw_results = read_raw_results(args.data_dir)
    results = aggregate_raw_results(raw_results)
    expected = len(VARIANTS) * len(PATHS) * len(SIZES)
    if len(results) != expected:
        raise RuntimeError(f"expected {expected} aggregated points, found {len(results)}")

    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.labelsize": 10,
        "figure.facecolor": "white",
        "axes.facecolor": "#FFFFFF",
    })
    for variant in VARIANTS:
        plot_variant_paths(results, raw_results, args.output_dir, variant)
    plot_inter_variant_comparison(
        results, args.output_dir, "memfd", "SHM", "inter-shm-variant-comparison"
    )
    plot_inter_variant_comparison(
        results, args.output_dir, "cpu", "CPU", "inter-cpu-variant-comparison"
    )
    e2e_x_limit = distribution_x_limit(raw_results, "e2e_latency_us", 2000)
    plot_one_mib_histogram(
        raw_results,
        args.output_dir,
        "cpu",
        "CPU",
        "1m-inter-cpu-latency-distribution",
        e2e_x_limit,
        "e2e_latency_us",
        250,
        2000,
        "End-to-end latency (µs)",
        "latency",
    )
    plot_one_mib_histogram(
        raw_results,
        args.output_dir,
        "memfd",
        "SHM",
        "1m-inter-shm-latency-distribution",
        e2e_x_limit,
        "e2e_latency_us",
        250,
        2000,
        "End-to-end latency (µs)",
        "latency",
    )
    plot_baseline_inter_histogram(raw_results, args.output_dir, e2e_x_limit)
    publish_x_limit = distribution_x_limit(raw_results, "publish_duration_us", 500)
    plot_one_mib_histogram(
        raw_results,
        args.output_dir,
        "cpu",
        "CPU",
        "1m-inter-cpu-publish-duration-distribution",
        publish_x_limit,
        "publish_duration_us",
        100,
        500,
        "publish() duration (µs)",
        "publish() duration",
    )
    plot_one_mib_histogram(
        raw_results,
        args.output_dir,
        "memfd",
        "SHM",
        "1m-inter-shm-publish-duration-distribution",
        publish_x_limit,
        "publish_duration_us",
        100,
        500,
        "publish() duration (µs)",
        "publish() duration",
    )
    print(f"aggregated {len(results)} points from {args.data_dir}")
    print(f"e2e distribution plots use shared x-limit of {e2e_x_limit} µs")
    print(f"publish distribution plots use shared x-limit of {publish_x_limit} µs")
    print(f"wrote figures to {args.output_dir}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Generate figures comparing the recorded fastrtps lazy and zenoh benchmarks."""

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from statistics import median

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


SIZES = (64, 1024, 4096, 16384, 65536, 262144, 1048576, 4194304, 16777216)
VARIANTS = ("lazy", "zenoh")
COLORS = {
    "lazy": "#A3BE8C",
    "zenoh": "#BF616A",
}
LABELS = {
    "lazy": "fastrtps lazy",
    "zenoh": "zenoh",
}
PATHS = (
    ("inter_process", "cpu", "Inter-process CPU"),
    ("inter_process", "memfd", "Inter-process memfd"),
    ("intra_process_va", "cpu", "Intra-process CPU"),
    ("intra_process_va", "memfd", "Intra-process memfd"),
)


def size_label(size):
    for divisor, suffix in ((1024**2, "MiB"), (1024, "KiB")):
        if size >= divisor and size % divisor == 0:
            return f"{size // divisor} {suffix}"
    return f"{size} B"


def percentile(values, p):
    ordered = sorted(values)
    return ordered[int((len(ordered) - 1) * p)]


def read_raw(path):
    values = defaultdict(lambda: {"e2e": [], "publish": []})
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            key = (row["communication"], row["backend"], int(row["size_bytes"]))
            values[key]["e2e"].append(int(row["e2e_latency_ns"]) / 1000.0)
            values[key]["publish"].append(int(row["publish_duration_ns"]) / 1000.0)
    return values


def summarize(raw):
    return {
        key: {
            metric: {
                "p50": percentile(samples, 0.50),
                "p95": percentile(samples, 0.95),
                "mean": sum(samples) / len(samples),
            }
            for metric, samples in metrics.items()
        }
        for key, metrics in raw.items()
    }


def setup_axis(axis):
    axis.set_xscale("log", base=2)
    axis.set_xticks(SIZES)
    axis.set_xticklabels([size_label(size) for size in SIZES], rotation=35, ha="right")
    axis.grid(True, which="both", color="#D8DEE9", linewidth=0.7)
    axis.set_axisbelow(True)


def plot_memfd_latency(data, output):
    figure, axis = plt.subplots(figsize=(10.5, 5.8))
    for variant in VARIANTS:
        values = [data[variant][("inter_process", "memfd", size)]["e2e"]["p50"] for size in SIZES]
        axis.plot(
            SIZES, values, marker="o", linewidth=2, markersize=4,
            color=COLORS[variant], label=LABELS[variant],
        )
    setup_axis(axis)
    axis.set_ylabel("End-to-end latency p50 (µs)")
    axis.set_title("Inter-process memfd latency: zenoh vs fastrtps lazy")
    axis.legend(ncol=2, frameon=False)
    figure.tight_layout()
    figure.savefig(output, dpi=180)
    plt.close(figure)


def plot_backend_latency(data, output):
    figure, axes = plt.subplots(1, 2, figsize=(12, 5.2), sharey=False)
    for axis, backend, title in zip(axes, ("cpu", "memfd"), ("CPU backend", "memfd backend")):
        for variant, linestyle in (("lazy", "-"), ("zenoh", "--")):
            for percentile_name, alpha in (("p50", 1.0), ("p95", 0.48)):
                values = [
                    data[variant][("inter_process", backend, size)]["e2e"][percentile_name]
                    for size in SIZES
                ]
                label = f"{LABELS[variant]} {percentile_name}" if percentile_name == "p50" else None
                axis.plot(
                    SIZES, values, marker="o", markersize=3.5, linewidth=2 if percentile_name == "p50" else 1.2,
                    linestyle=linestyle, color=COLORS[variant], alpha=alpha, label=label,
                )
        setup_axis(axis)
        axis.set_title(title)
        axis.set_xlabel("Payload")
        axis.set_ylabel("End-to-end latency (µs)")
        axis.legend(frameon=False, fontsize=8)
    figure.suptitle("Inter-process p50 (opaque) and p95 (faded): zenoh vs fastrtps lazy")
    figure.tight_layout()
    figure.savefig(output, dpi=180)
    plt.close(figure)


def plot_one_mib_distributions(raw, output):
    figure, axes = plt.subplots(1, 2, figsize=(12, 5.0))
    for axis, metric, title, x_limit, bin_width in (
        (axes[0], "e2e", "End-to-end latency", 4000, 100),
        (axes[1], "publish", "Publisher publish() duration", 1200, 50),
    ):
        for variant in VARIANTS:
            values = raw[variant][("inter_process", "memfd", 1048576)][metric]
            bins = range(0, x_limit + bin_width, bin_width)
            axis.hist(
                values, bins=bins, alpha=0.45, color=COLORS[variant],
                edgecolor=COLORS[variant], linewidth=0.7, label=LABELS[variant],
            )
            axis.axvline(median(values), color=COLORS[variant], linestyle="--", linewidth=1.5)
        axis.set_xlim(0, x_limit)
        axis.set_title(title)
        axis.set_xlabel("Time (µs); dashed lines = p50")
        axis.set_ylabel("Frequency (samples)")
        axis.grid(True, color="#D8DEE9", linewidth=0.7)
        axis.set_axisbelow(True)
        axis.legend(frameon=False)
    figure.suptitle("1 MiB inter-process memfd raw timing distributions (n=100 each)")
    figure.tight_layout()
    figure.savefig(output, dpi=180)
    plt.close(figure)


def plot_heatmap(data, output):
    matrix = []
    labels = []
    for communication, backend, label in PATHS:
        labels.append(label)
        matrix.append([
            100.0 * (
                data["zenoh"][(communication, backend, size)]["e2e"]["p50"] /
                data["lazy"][(communication, backend, size)]["e2e"]["p50"] - 1.0
            )
            for size in SIZES
        ])

    figure, axis = plt.subplots(figsize=(11.5, 3.8))
    image = axis.imshow(matrix, cmap="RdYlGn", aspect="auto", vmin=-80, vmax=80)
    axis.set_yticks(range(len(labels)), labels)
    axis.set_xticks(range(len(SIZES)), [size_label(size) for size in SIZES], rotation=35, ha="right")
    for row_index, row in enumerate(matrix):
        for column_index, value in enumerate(row):
            axis.text(column_index, row_index, f"{value:+.0f}%", ha="center", va="center", fontsize=8)
    axis.set_title("Zenoh p50 change vs fastrtps lazy (negative is faster)")
    figure.colorbar(image, ax=axis, label="Change in p50 (%)")
    figure.tight_layout()
    figure.savefig(output, dpi=180)
    plt.close(figure)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("benchmark-results-zenoh"))
    parser.add_argument("--fastrtps-dir", type=Path, default=Path("benchmark-results-16way-rerun"))
    parser.add_argument("--output-dir", type=Path, default=Path("figures/zenoh-comparison"))
    args = parser.parse_args()

    fastrtps_dir = args.fastrtps_dir
    zenoh_dir = args.data_dir
    raw = {"lazy": read_raw(fastrtps_dir / "raw" / "lazy.csv")}
    raw["zenoh"] = read_raw(zenoh_dir / "raw" / "zenoh.csv")
    data = {variant: summarize(values) for variant, values in raw.items()}

    args.output_dir.mkdir(parents=True, exist_ok=True)
    plot_memfd_latency(data, args.output_dir / "inter-process-memfd-latency.png")
    plot_backend_latency(data, args.output_dir / "inter-process-backend-latency.png")
    plot_one_mib_distributions(raw, args.output_dir / "1m-memfd-distributions.png")
    plot_heatmap(data, args.output_dir / "zenoh-vs-fastrtps-heatmap.png")


if __name__ == "__main__":
    main()

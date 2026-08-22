# memfd_buffer_backend_benchmark

Benchmark programs and measured results for
[`memfd_buffer_backend`](https://github.com/dskkato/memfd_buffer_backend).

This repository is intentionally separate from the published ROS packages.
The benchmark package is not part of the ROS 2 apt release.

## Layout

- `memfd_buffer_backend_benchmark/`: ROS 2 benchmark package.
- `BENCHMARK_REPORT.md`: benchmark methodology and analysis.
- `benchmark-results-16way-rerun/`: rerun summary and raw measurements.
- `figures/`: figures generated from the rerun results.
- `patches/`: `rmw_fastrtps` variants used by the 16-way benchmark.
- `tools/plot_benchmark_report.py`: report figure generator.

## Build

Clone this repository beside the main repository in a ROS 2 workspace:

```bash
cd ~/ros2_ws/src
git clone https://github.com/dskkato/memfd_buffer_backend.git
git clone https://github.com/dskkato/memfd_buffer_backend_benchmark.git
```

Build the benchmark package with the main packages:

```bash
cd ~/ros2_ws
source ~/ros2_lyrical/install/setup.bash
colcon build --packages-up-to memfd_buffer_backend_benchmark
source install/setup.bash
```

Run one end-to-end sweep:

```bash
ros2 run memfd_buffer_backend_benchmark run_e2e_benchmark.py \
  --output memfd-old-pubsub-results.csv \
  --raw-output memfd-old-pubsub-raw.csv
```

The runner accepts any installed RMW implementation. For a zenoh run, start
the local router first and pass `--rmw-implementation rmw_zenoh_cpp`:

```bash
source ~/ros2_lyrical/install/setup.bash
source install/setup.bash
ros2 run rmw_zenoh_cpp rmw_zenohd &
python3 src/memfd_buffer_backend_benchmark/memfd_buffer_backend_benchmark/scripts/run_e2e_benchmark.py \
  --rmw-implementation rmw_zenoh_cpp \
  --output benchmark-results-zenoh/zenoh.csv \
  --raw-output benchmark-results-zenoh/raw/zenoh.csv \
  --variant zenoh
```

No `rmw_fastrtps` patches are applied for the zenoh run. See
[`ZENOH_BENCHMARK_REPORT.md`](ZENOH_BENCHMARK_REPORT.md) for the recorded
lazy-only comparison and figures.

Run the complete 16-way matrix:

```bash
python3 src/memfd_buffer_backend_benchmark/memfd_buffer_backend_benchmark/scripts/run_16way_benchmark.py \
  --output-dir src/memfd_buffer_backend_benchmark/benchmark-results-16way-rerun \
  --overwrite
```

The orchestration script applies each `rmw_fastrtps` patch independently,
builds the variant, runs the end-to-end benchmark, and restores the original
`rmw_fastrtps` checkout when it exits.

Generate the report figures with:

```bash
python3 src/memfd_buffer_backend_benchmark/tools/plot_benchmark_report.py
```

See [`BENCHMARK_REPORT.md`](BENCHMARK_REPORT.md) for the recorded results.

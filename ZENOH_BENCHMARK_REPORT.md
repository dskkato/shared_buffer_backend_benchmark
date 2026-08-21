# Zenoh memfd rosidl buffer benchmark report

## Executive summary

This benchmark compares the unpatched `rmw_zenoh_cpp` path with the recorded
`rmw_fastrtps_cpp` 16-way rerun under the same benchmark protocol. The zenoh
run uses the memfd rosidl buffer backend and applies no fastrtps patches.

The main results are:

- Inter-process memfd p50 is essentially tied at 64 B (949.7 µs for zenoh vs
  951.4 µs for the fastrtps baseline) and is 66.5% lower at 16 MiB (768.0 µs
  vs 2,289.5 µs).
- Across all nine sizes, the geometric-mean inter-process memfd p50 for zenoh
  is 19.1% lower than the fastrtps baseline.
- Zenoh's inter-process CPU path is slower for small payloads (+34--40% p50
  through 16 KiB), but is 62.3% lower at 1 MiB and 59.2% lower at 4 MiB.
  At 16 MiB it is 12.4% higher than the fastrtps baseline, so the CPU result
  is not a uniformly monotonic win.
- At 1 MiB, zenoh reduces memfd publisher `publish()` p50 by 74.2% versus
  fastrtps (173.5 µs vs 672.6 µs), while the end-to-end memfd p50 decreases by
  17.2% (971.4 µs vs 1,172.9 µs).
- The fastrtps `unique_ptr`, `lazy`, and `reserve` variants remain faster than
  unpatched zenoh at 16 MiB memfd in this run (720.3--739.8 µs vs 768.0 µs),
  although zenoh is still much faster than the unpatched fastrtps baseline.

The practical conclusion is that zenoh is a strong transport choice for the
memfd backend at larger payloads, especially compared with unpatched
fastrtps. The patched fastrtps SHM path still has the lowest measured 16 MiB
memfd p50 in this dataset.

## Measurement design

The zenoh matrix contains one unpatched variant:

- RMW: `rmw_zenoh_cpp`
- Zenoh router: local `rmw_zenohd`, default configuration
- Payloads: 64 B, 1 KiB, 4 KiB, 16 KiB, 64 KiB, 256 KiB, 1 MiB, 4 MiB,
  and 16 MiB
- Communication: `inter_process` and `intra_process_va`
- Buffer modes: CPU and memfd
- Repeats: 5
- Messages per case: 30 at 10 Hz
- Warm-up: first 10 messages excluded; 20 measured samples remain
- Affinity: publisher CPU 8, subscriber CPU 9, intra-process CPU 8
- Discovery wait: 3 seconds before each inter-process publisher

This is the same protocol used by the fastrtps 16-way rerun:
4 variants × 9 sizes × 2 communication modes × 2 backends × 5 repeats.
The fastrtps comparison data are the existing `baseline`, `unique_ptr`,
`lazy`, and `reserve` raw CSVs. The zenoh run generated 180 summary rows and
3,600 measured raw samples. Every case received all 30 messages, collected 20
measured samples, and every intra-process case reported 30/30 virtual-address
matches.

The benchmark records two timings:

- `publish_duration_ns`: time spent inside the publisher's `publish()` call
  boundary. Payload allocation and initialization happen before this timing.
- `e2e_latency_ns`: publisher timestamp to subscriber-side payload access.

All reported percentiles use the benchmark's lower-rank order-statistic rule;
the p50 and p95 tables below are calculated over 100 raw samples (five
repeats × 20 measured messages) per case.

## Inter-process memfd results

Values are microseconds, shown as `p50 / p95`.

| Payload | fastrtps baseline | fastrtps unique_ptr | fastrtps lazy | fastrtps reserve | zenoh |
|---:|---:|---:|---:|---:|---:|
| 64 B | 951.4 / 1,091.2 | 933.4 / 1,069.6 | 944.0 / 1,073.8 | 966.0 / 1,067.9 | 949.7 / 1,077.0 |
| 1 KiB | 942.3 / 1,060.1 | 947.7 / 1,069.2 | 942.1 / 1,059.1 | 891.5 / 1,059.7 | 951.7 / 1,127.9 |
| 4 KiB | 927.2 / 1,045.0 | 973.4 / 1,066.2 | 956.8 / 1,071.7 | 908.5 / 1,046.0 | 953.6 / 1,070.5 |
| 16 KiB | 921.5 / 1,057.8 | 937.4 / 1,057.2 | 936.0 / 1,044.9 | 937.8 / 1,056.0 | 948.7 / 1,060.0 |
| 64 KiB | 958.8 / 1,107.0 | 918.4 / 1,040.9 | 925.1 / 1,091.9 | 895.9 / 1,053.1 | 950.7 / 1,132.9 |
| 256 KiB | 1,033.0 / 1,128.8 | 937.6 / 1,046.8 | 937.4 / 1,054.9 | 870.7 / 1,030.6 | 962.2 / 1,071.4 |
| 1 MiB | 1,172.9 / 1,298.2 | 943.2 / 1,057.1 | 935.6 / 1,050.5 | 923.7 / 1,056.9 | 971.4 / 1,102.7 |
| 4 MiB | 1,700.0 / 1,822.6 | 897.6 / 1,021.2 | 915.6 / 1,049.2 | 895.1 / 989.8 | 917.1 / 1,057.0 |
| 16 MiB | 2,289.5 / 2,718.5 | 739.8 / 816.6 | 727.6 / 805.7 | 720.3 / 806.3 | 768.0 / 860.5 |

The figure below makes the main result visible: the unpatched fastrtps curve
grows with payload size, while zenoh stays close to the patched fastrtps
curves at 4--16 MiB.

![Inter-process memfd p50 comparison](figures/zenoh-comparison/inter-process-memfd-latency.png)

## CPU and memfd path comparison

The paired figure shows p50 as opaque lines and p95 as faded lines for zenoh
and the fastrtps baseline.

![Inter-process CPU and memfd p50/p95 comparison](figures/zenoh-comparison/inter-process-backend-latency.png)

At selected payload sizes, the inter-process CPU and memfd results are:

| Payload / backend | fastrtps baseline p50 / p95 (µs) | zenoh p50 / p95 (µs) | Zenoh p50 change |
|---|---:|---:|---:|
| 64 B CPU | 636.3 / 726.9 | 891.4 / 1,072.3 | +40.1% |
| 64 B memfd | 951.4 / 1,091.2 | 949.7 / 1,077.0 | -0.2% |
| 1 MiB CPU | 11,700.0 / 13,774.4 | 4,412.8 / 4,877.5 | -62.3% |
| 1 MiB memfd | 1,172.9 / 1,298.2 | 971.4 / 1,102.7 | -17.2% |
| 4 MiB CPU | 15,407.8 / 16,025.3 | 6,280.9 / 10,524.6 | -59.2% |
| 4 MiB memfd | 1,700.0 / 1,822.6 | 917.1 / 1,057.0 | -46.1% |
| 16 MiB CPU | 14,879.5 / 16,019.0 | 16,721.3 / 22,369.4 | +12.4% |
| 16 MiB memfd | 2,289.5 / 2,718.5 | 768.0 / 860.5 | -66.5% |

The geometric-mean zenoh/fastrtps-baseline p50 ratios across all nine sizes are
1.079× for inter-process CPU (+7.9%), 0.809× for inter-process memfd
(-19.1%), 0.983× for intra-process CPU (-1.7%), and 0.910× for intra-process
memfd (-9.0%).

![Zenoh p50 change across all paths](figures/zenoh-comparison/zenoh-vs-fastrtps-heatmap.png)

## Publisher-side timing at 1 MiB

| Backend / metric | fastrtps baseline p50 / p95 (µs) | zenoh p50 / p95 (µs) | Zenoh p50 change |
|---|---:|---:|---:|
| CPU `publish()` | 1,589.3 / 1,801.1 | 3,094.9 / 3,414.9 | +94.7% |
| memfd `publish()` | 672.6 / 754.7 | 173.5 / 200.7 | -74.2% |
| CPU end-to-end | 11,700.0 / 13,774.4 | 4,412.8 / 4,877.5 | -62.3% |
| memfd end-to-end | 1,172.9 / 1,298.2 | 971.4 / 1,102.7 | -17.2% |

![1 MiB memfd raw distributions](figures/zenoh-comparison/1m-memfd-distributions.png)

The raw distributions show that zenoh's memfd publisher timing is shifted
substantially left of fastrtps. The end-to-end distributions are closer,
because they also include discovery-independent scheduling, queueing, and
subscriber-side work.

## Reproduction

Build the benchmark package after sourcing the Lyrical ROS 2 underlay:

```bash
cd ~/workspace/ros2_ws
source ~/ros2_lyrical/install/setup.bash
colcon build --packages-select memfd_buffer_backend_benchmark \
  --cmake-args -DCMAKE_BUILD_TYPE=Release
source install/setup.bash
```

Start a local router and run the same full zenoh matrix:

```bash
source ~/ros2_lyrical/install/setup.bash
source install/setup.bash
ros2 run rmw_zenoh_cpp rmw_zenohd > /tmp/memfd-zenoh-router.log 2>&1 &
python3 src/memfd_buffer_backend_benchmark/memfd_buffer_backend_benchmark/scripts/run_e2e_benchmark.py \
  --rmw-implementation rmw_zenoh_cpp \
  --variant zenoh \
  --sizes 64,1024,4096,16384,65536,262144,1048576,4194304,16777216 \
  --count 30 --rate-hz 10 --warmup 10 --repeats 5 --seed 20260812 \
  --publisher-affinity 8 --subscriber-affinity 9 --intra-affinity 8 \
  --discovery-wait 3 \
  --communications inter_process,intra_process_va --modes cpu,memfd \
  --output src/memfd_buffer_backend_benchmark/benchmark-results-zenoh/zenoh.csv \
  --raw-output src/memfd_buffer_backend_benchmark/benchmark-results-zenoh/raw/zenoh.csv
```

Generate the comparison figures with the workspace virtual environment:

```bash
source .venv/bin/activate
cd src/memfd_buffer_backend_benchmark
python3 tools/plot_zenoh_comparison.py \
  --data-dir benchmark-results-zenoh \
  --fastrtps-dir benchmark-results-16way-rerun \
  --output-dir figures/zenoh-comparison
```

## Limitations

The zenoh and fastrtps datasets were collected as separate benchmark runs,
not simultaneously. They use the same host, CPU affinities, benchmark binary
and matrix parameters, but normal run-to-run scheduling and system noise can
remain. The result therefore supports a measured comparison under controlled
conditions, not a hardware-independent transport ranking.

The local router is part of the zenoh inter-process path. Its presence and
default configuration are intentional and should be kept for reproduction.
The benchmark does not isolate router CPU time or memory use.

The 20 samples per repeat are sufficient for the aggregate comparison, but
p95 and p99 are adjacent order statistics under the benchmark's percentile
rule. Raw CSVs should be used for detailed tail analysis.

Finally, `memfd` here refers to the rosidl buffer backend path. It is distinct
from enabling Zenoh's own transport-level shared-memory optimization.

## Data and tooling

- [zenoh summary CSV](benchmark-results-zenoh/zenoh.csv)
- [zenoh raw CSV](benchmark-results-zenoh/raw/zenoh.csv)
- [fastrtps baseline raw CSV](benchmark-results-16way-rerun/raw/baseline.csv)
- [comparison plotting script](tools/plot_zenoh_comparison.py)

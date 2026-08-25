# Task 1 periodic scheduler A/B

Validated runs: 6. Every accepted run has contiguous periodic samples, a matching completion count, the requested ncnn/YOLO inference count (which may be zero for a periodic-only run), and no fatal marker.

| Scheduler | Runs | Median P99 | Median P99.9 | Median max | YOLO mean | YOLO P99 | Throughput |
|---|---:|---:|---:|---:|---:|---:|---:|
| rr | 3 | 0.621 ms | 7.816 ms | 9.122 ms | n/a | n/a | n/a |
| fp-rr | 3 | 0.278 ms | 0.748 ms | 1.134 ms | n/a | n/a | n/a |

Median per-run P99 changes from 0.621 ms to 0.278 ms (55.251% reduction).

The per-run and scheduler-median CSV files remain the source of truth; this report does not claim a native-RTOS bound or hide YOLO tail-latency/throughput trade-offs.

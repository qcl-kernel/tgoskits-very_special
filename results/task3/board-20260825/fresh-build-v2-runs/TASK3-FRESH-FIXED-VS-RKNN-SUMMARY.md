# Task 3 fixed perception vs RKNN/NPU

## Scope

This is the current physical-board Task 3 result. Both groups were built from
repository commit `9bef4f8560ec9abc29d64ebb734b818419e0ecce` plus the recorded
working-tree changes, then run three times each through RAM-only `fastboot
stage`. The archived FIT identities below bind these runs to their inputs.

Both groups use the frozen architecture:

```text
StarryOS vCPU0 (AI/RKNN/NPU submission) -> pCPU2
StarryOS vCPU1 (T2N1 communication)     -> pCPU1
Zephyr vCPU0 (RTOS control)             -> pCPU1
RK3588 NPU                              -> StarryOS-only passthrough
```

The scheduler is FP-RR in both groups. The Zephyr binary, T2N1 protocol,
12-event input manifest, CPU placement, priorities, and board DTB are held
constant. The A/B variable is the perception decision source: fixed perception
or YOLOv8 through RKNN on the RK3588 NPU.

## Three-run result

| Metric | Fixed perception | YOLOv8 RKNN/NPU |
|---|---:|---:|
| Valid runs | 3/3 | 3/3 |
| Complete CONTROL→STATUS chains | 36/36 | 36/36 |
| Correct scene decisions | 24/36 (66.7%) | 36/36 (100.0%) |
| Per-run correct decisions | 8/12, 8/12, 8/12 | 12/12, 12/12, 12/12 |
| Vehicle recall | N/A | 100% in every run |
| Hazard recall | 0% in every run | 100% in every run |
| Median per-run mean CONTROL→STATUS RTT | 35.6 ms | 57.8 ms |
| Per-run mean RTT range | 34.7–37.2 ms | 55.5–62.0 ms |
| Median per-run P95 RTT | 37 ms | 62 ms |
| Median per-run mean inference-start→STATUS | 35.6 ms | 219.9 ms |
| Median per-run mean RKNN inference | N/A | 50.1 ms |
| Panic / ESR / segfault / ACK timeout / protocol failure | 0 | 0 |

Fixed perception sent `SetOutput 500` for the hazard frames and therefore
missed all three hazards. RKNN detected the first hazard, sent `Stop`, kept the
stopped protocol state latched through the remaining hazard frames and the next
road frame, acknowledged an explicit `Reset`, and resumed `SetOutput` on the
final road frame.

The result demonstrates a correctness/latency trade-off. RKNN raises decision
accuracy from 66.7% to 100% and hazard recall from 0% to 100%, at the cost of
NPU inference plus publication, polling, scheduling, VirtIO/T2N1, and RTOS
execution latency. It does not support a claim that AI improves every latency
metric.

## Artifact identity

```text
Zephyr binary used by both arms:
0f947c3fe62cc4fb4f7031633c86ecc1cd630f871a4fd3f8d49478ff067a5d27

Fixed FP-RR FIT:
ca4deabe8a1b9958c3dcffd469844b00096da3be97f77f30ff4304ba66ac2fb8

RKNN FP-RR FIT:
7ce175c987197b866dc828c62beeb62af19bce265bd8a94efe180b830ae10bde
```

External BusyBox, RKNN runtime, model, labels, benchmark, DTB, and all input
images were bound by SHA-256 before the board was started. The hashes above
identify the common Zephyr binary and the two RAM-only FIT images.

## Evidence layout

- `fixed-fp-rr-run1.log` .. `fixed-fp-rr-run3.log`: fixed raw UART captures.
- `rknn-fp-rr-run1.log` .. `rknn-fp-rr-run3.log`: RKNN/NPU raw UART captures.
- `report-run1/` .. `report-run3/`: strict paired `metrics.json` and `REPORT.md`.
- `EVIDENCE-SHA256SUMS.txt`: hashes of all run logs and reports in this directory.

Every accepted log contains exactly one complete scene window, 12 CONTROL
records, 12 matching STATUS records, and the mode-specific successful end
marker. No fatal/error marker is present in the six accepted logs.

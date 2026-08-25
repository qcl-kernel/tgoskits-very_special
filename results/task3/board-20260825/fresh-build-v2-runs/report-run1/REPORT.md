# Task 3 final hybrid physical-board A/B

| Metric | Fixed perception | YOLOv8 RKNN/NPU |
|---|---:|---:|
| Complete CONTROL→STATUS chains | 12/12 | 12/12 |
| Correct scene decisions | 8/12 (66.7%) | 12/12 (100.0%) |
| Vehicle recall | N/A (no detector) | 100.0% |
| Hazard recall | 0.0% | 100.0% |
| Mean CONTROL→STATUS RTT | 37.2 ms | 57.8 ms |
| Mean inference-start→STATUS | 37.5 ms | 219.1 ms |
| Mean RKNN inference | N/A | 50.1 ms |

Fixed perception continued `SetOutput 500` through all hazard frames. RKNN detected the first
hazard and sent Stop, then kept Stop latched for two further hazard frames and a later safe road
frame. Zephyr state fell from 432 to 432 → 432 → 432 → 432.
The explicit Reset was acknowledged and the final road frame resumed SetOutput.

Both arms used the same FP-RR hybrid topology, Zephyr binary, T2N1 protocol and 12-event input
manifest. The only A/B factor was the perception decision source. All timing timestamps were
captured from StarryOS CLOCK_MONOTONIC; file publication/polling, guest scheduling and network
delivery are included in end-to-end latency.

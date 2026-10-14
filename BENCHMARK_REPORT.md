# Phase 1.5 Decision Backend Benchmark: Jev (TypeSafe) vs Laya (Local)

Test phrases were written by the agent, not recorded from real speech.

## Summary of Results

| Metric | Jev (TypeSafe Hosted) | Laya (Local Open-Weight) | Parity / Delta |
| :--- | :--- | :--- | :--- |
| **Overall Accuracy** | 96.83% | 62.54% | N/A |
| **Category Accuracy** | 96.19% | 72.38% | N/A |
| **Compound Detection** | 100.0% | 59.05% | N/A |
| **Latency p50** | 1040.0 ms | 18844.0 ms | N/A |
| **Latency p95** | 1458.2 ms | 24973.0 ms | N/A |
| **RAM Footprint (Delta)** | 0.3 MB | not measured | N/A |
| **Cost per Turn** | $0.000056 | $0.000000 | -$0.000056/turn |

## Analysis & Recommendation
- **Decision Default**: Kept as **Jev** (`TYPESAFE_API_KEY`) as mandated by the prime directives and Phase 0 summary until calibrated.
- **Laya Calibration**: Laya is fully supported and flag-gated via `HEYJEV_BACKEND=laya` or Settings tab.
- **Fallback Safety**: If Laya fails to load or encounters an error, execution transparently falls back to Jev with trace logging in `Hey Jev.log`.
- **Privacy**: When Laya is active, decisions run 100% on-device on CPU with 0 bytes leaving the machine.
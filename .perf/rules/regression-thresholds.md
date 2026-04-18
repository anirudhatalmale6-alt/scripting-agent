# Regression Thresholds

Business SLOs and regression classification rules.

## Latency Thresholds (p95)

| Domain       | Green    | Warn     | Fail     |
|--------------|----------|----------|----------|
| checkout     | < 800ms  | < 1200ms | >= 1200ms|
| auth/login   | < 500ms  | < 800ms  | >= 800ms |
| cart         | < 600ms  | < 1000ms | >= 1000ms|
| search       | < 700ms  | < 1100ms | >= 1100ms|
| product      | < 600ms  | < 1000ms | >= 1000ms|
| default      | < 2000ms | < 3000ms | >= 3000ms|

## Error Rate Thresholds

| Risk Level | Green   | Warn    | Fail    |
|------------|---------|---------|---------|
| high       | < 0.5%  | < 1%    | >= 1%   |
| medium     | < 1%    | < 2%    | >= 2%   |
| low        | < 2%    | < 5%    | >= 5%   |

## Throughput (RPS) — Minimum Floor

- checkout: >= 20 RPS
- search: >= 50 RPS
- default: >= 10 RPS

## Regression Classification

- **no_regression**: all metrics within green thresholds
- **minor_regression**: latency or error rate in warn zone (< 20% degradation vs baseline)
- **severe_regression**: latency or error rate in fail zone (>= 20% degradation vs baseline)
- **possible_noise**: result differs from baseline but within 2x standard deviation of last 10 runs

## Baseline Strategy

- Baseline window: last 10 green runs
- Update baseline: only after green merge-to-main run
- Baseline stored per: test_name + environment

# Architecture

[← Project overview](../README.md) · [Methodology](METHODOLOGY.md) · [Run and inspect](RUNNING.md)

HARPY-SIM is a local batch simulation. It contains no live worker fleet, model API integration, production control plane, or web service. The implementation separates the simulated world from the oversight inputs and from ground-truth scoring.

## Oversight flow

![Simulated mesh, mechanical checks, budget-selected mock audits, suspicion, and bounded response.](assets/oversight-flow.svg)

The diagram shows the oversight-enabled arms. `NONE` bypasses oversight entirely; `TIER0_ONLY` uses mechanical signals without sentinel audits. The mock receives recent messages for each selected agent and generates an audit outcome from assumed detector characteristics.

## Experiment boundary

```mermaid
flowchart TD
    world["Simulated world and planted fault"] --> observable["Messages and observable lineage"]
    observable --> oversight["Telemetry, sampling, suspicion, response"]
    world --> mock["MockSentinel: explicit ground-truth exception"]
    mock --> oversight
    world --> scoring["Ground-truth scoring"]
    oversight --> scoring
    scoring --> artifacts["Run JSON and comparative metrics"]
```

`Claim.is_corrupt` and `LineagePair.ground_truth` are guarded against access from oversight modules. `MockSentinel` is explicitly exempt because Phase 1 supplies detector performance as an input. The simulation uses ground truth to score outcomes, including missed detections and false isolations.

This guard catches accidental leakage in the experiment. It is not a security boundary against arbitrary Python code.

## A simulation tick

| Step | Behavior |
| --- | --- |
| Emit | Active simulated workers create messages; the world propagates faults and charges worker spend. |
| Observe | Tier 0 records mechanical signals, and the alert budget records active agent time. |
| Accumulate | Suspicion decays and incorporates Tier 0 scores. |
| Audit | The sampler accrues its allowance, selects affordable audits, and supplies outcomes to suspicion. |
| Gate | Suspicion uses the isolation threshold maintained by the alert budget. |
| Respond and score | Crossings trigger isolation; the controller flags descendants and attempts an alert. The world attributes true and false isolations and resets agent state as implemented. |

See [`run_simulation`](../src/harpy/simulation.py) and `_isolate` for the exact order and response sequence.

## Two independent budgets

| Budget | Mechanism | Evidence |
| --- | --- | --- |
| Dollars | Accrue a fraction of worker spend, subtract Tier 0 cost, and buy only affordable audits. | Audit counts and costs are recorded. |
| Human attention | Bound alerts per active agent-hour; optionally adapt the isolation threshold. | Suppressed alerts, effective threshold, and lost detections are scored. |

The cost ledger separates sentinel audits, Tier 0, discarded work, reruns, triage, and investigation. In the current fleet-scale model, four components scale with worker-fleet size while the two review components do not. That assumption makes a stated fleet scale essential to interpreting overhead.

## Five permitted controller actions

`isolate`, `halt_outbound`, `restart_clean`, `flag_downstream`, and `alert_human` are the controller's action surface. Not every action is invoked by the default simulation loop. A suppressed human alert is logged as `alert_suppressed`; it is the absence of an alert, not a sixth permitted action.

Downstream flagging traverses the observable lineage projection. Missing or spurious provenance can therefore change the response's reach.

## Sampling arms

| Arm | Audit policy |
| --- | --- |
| `NONE` | Unsupervised control: no oversight. |
| `TIER0_ONLY` | Mechanical checks and suspicion, with no sentinel audits. |
| `RANDOM` | Uniform random audits within the budget. |
| `DIRECTED` | Sampling weighted by Tier 0 scores; uniform fallback when all weights are zero. |
| `HYBRID` | A random reserve followed by directed sampling from the remaining allowance. |

Separate RNG streams keep a sampler change from reshuffling the simulated world used for comparison. Fleet-scale variants re-cost the same run because scale changes accounting rather than simulation dynamics.

## Outputs and next boundary

Each run records its inputs, assumed detector characteristics, pricing verification state, fault distribution, metrics, and cost ledger. Sweeps write per-run JSON and a Parquet summary; plotting derives figures from those records.

`RealSentinel` raises `NotImplementedError`. HARPY-REPLAY would measure real candidate detectors on recorded traces. The current mock's performance is assumed, and every result downstream inherits that assumption.

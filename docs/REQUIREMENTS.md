# Engineering requirements

[Project](../README.md) · [Architecture](ARCHITECTURE.md) · [V3 design](STUDY_V3_DESIGN.md) · [AI nutrition label](governance/AI_NUTRITION_LABEL.md)

Owner: Melinda Emerson · Covers: v2 (main) and the v3 draft · Status: living document

Each requirement says what HARPY must do and how that is checked. Most checks are named tests that run in CI on every push. A requirement without an automated check says so.

## 1. Purpose and scope

HARPY is a research testbed for one question: **can a fixed oversight budget find the agent that started a fault in a multi-agent system, before the fault spreads, without isolating the agents that only passed it along?**

It has two parts:
- **HARPY-SIM** is a seeded simulation of an agent mesh, oversight policies and their costs.
- **HARPY-REPLAY** is a separate harness that measures a detector on recorded, observable-only cases.

**Out of scope:**
- operating or enforcing on a production fleet;
- adversaries that adapt to the sampler;
- real-time or networked operation;
- any claim about a live model's accuracy before replay on real traces.

## 2. Terms

| Term | Meaning |
| --- | --- |
| Agent | A worker in the mesh. One per run is faulty from a sampled onset tick. |
| Claim | A typed value an agent asserts: subject, field, value. It may be derived from a claim the agent received. |
| Origin | The agent whose claim first differs from what it received, or that asserted it with no parent. |
| Relay | A healthy agent that carried a corrupt claim forward unchanged. |
| Tier 0 | Seven mechanical checks on typed message fields. Close to free. |
| Sentinel | The expensive detector. In HARPY-SIM it is `MockSentinel`, whose accuracy is an input. |
| Overhead | All oversight cost divided by worker-fleet cost, at a stated fleet scale and review policy. |
| Lineage fidelity | The probability that a true parent→child edge is recorded. |

## 3. Data model

![HARPY data model: agents emit messages that carry claims; lineage edges link claims; audits name evidence claims; attribution walks lineage to the origin and credits suspicion there; suspicion past the gate triggers isolation; a ledger charges every oversight cost.](assets/harpy-erd.svg)

`GT` fields are ground truth. Only scoring code and `MockSentinel` may read them (O-2).

## 4. Functional requirements

Status values:
- **Met:** an automated check passes.
- **Met, v3 draft:** met on the v3 branch (PR #8) but not yet on main.
- **Manual:** checked by inspection, with no automated test.

### World

| ID | Requirement | Verification | Status |
| --- | --- | --- | --- |
| W-1 | Each run shall generate a seeded mesh with exactly one faulty agent and a sampled onset tick. The world shall not depend on the oversight arm. | `test_determinism.py::test_different_seeds_give_different_worlds`, `::test_the_arm_does_not_perturb_the_world_it_is_measured_on` | Met |
| W-2 | Faults shall have three severities: OVERT (structural), MODERATE (typed conflicts) and SUBTLE (1–3% numeric perturbation). The preregistered fault distribution shall be echoed in every result. | `configs/default.yaml`; `test_instrumentation.py::test_every_required_metric_is_emitted` | Met |
| W-3 | Corruption shall propagate through claim derivation, and the true lineage shall be recorded for scoring only. | `test_lineage.py`; `test_isolation_guard.py::test_ground_truth_lineage_is_unreadable_from_every_harpy_module` | Met |

### Observation and isolation of ground truth

| ID | Requirement | Verification | Status |
| --- | --- | --- | --- |
| O-1 | Tier 0 shall compute exactly seven checks from typed fields, never from free text. | `test_telemetry_tiers.py::test_exactly_seven_checks_exist`, `::test_no_check_reads_free_text` | Met |
| O-2 | No HARPY-side module (telemetry, sentinel, sampler, suspicion, response, alert budget, attribution) shall read `Claim.is_corrupt` or the true lineage. `MockSentinel` is the only named exception. | `test_isolation_guard.py` (runtime guard plus a static source scan) | Met |

### Budgets and cost

| ID | Requirement | Verification | Status |
| --- | --- | --- | --- |
| B-1 | Audits shall be bought from an allowance of `budget_pct` × worker spend, minus Tier 0 cost. No arm shall spend more than the pool holds. | `test_sampler_arms.py::test_no_arm_spends_more_than_the_pool_holds`, `::test_audit_count_is_derived_from_the_budget`, `::test_tier0_spend_comes_off_the_same_allowance` | Met |
| B-2 | Follow-up audits shall come from the same pool and buy no extra audits. | `test_v3_followup.py::test_followups_buy_no_extra_audits` | Met, v3 draft |
| B-3 | Human alerts shall be capped per active agent-hour, independently of the dollar budget. | `test_alert_budget.py::test_the_cap_is_never_exceeded_at_any_prefix_of_the_run` | Met |
| B-4 | The ledger shall report six cost components separately: sentinel, Tier 0, discarded work, reruns, triage and investigation. Overhead shall be stated with its fleet scale and review policy. | `test_ledger.py`; `test_study.py::test_review_policy_is_recosting_and_cannot_change_detection` | Met |
| B-5 | No run or sweep shall proceed on unverified pricing, except under an explicit waiver that stamps every result. | `test_instrumentation.py::test_run_simulation_aborts_on_unverified_pricing`; CI step "sweep refuses placeholder pricing" | Met |

### Sampling

| ID | Requirement | Verification | Status |
| --- | --- | --- | --- |
| S-1 | The sampler shall provide six arms: NONE, TIER0_ONLY, RANDOM, DIRECTED, HYBRID and EXPOSURE. Default grids shall enumerate only the five v2 arms. | `test_sampler_arms.py::test_all_six_arms_are_present`; `test_v3_exposure.py::test_exposure_is_a_v3_arm_outside_the_historical_set` | Met, v3 draft |
| S-2 | EXPOSURE shall rank agents by (ticks since last audit) × (1 + observable fan-out), breaking ties at random. | `test_v3_exposure.py::test_score_is_gap_times_one_plus_fanout`, `::test_ties_are_broken_at_random_not_alphabetically` | Met, v3 draft |
| S-3 | No agent shall be audited twice in one tick. | `test_sampler_arms.py::test_no_agent_is_audited_twice_in_one_tick`; `test_v3_followup.py::test_no_agent_is_audited_twice_in_a_tick_with_followups_owed` | Met |

### Attribution (Tattletale's origin rule)

| ID | Requirement | Verification | Status |
| --- | --- | --- | --- |
| A-1 | When attribution is on, the walk shall pass blame upstream across unchanged carries and stop at the first agent whose claim differs from what it received. Schema version is not a change. | `test_v3_attribution.py::test_unchanged_carries_pass_blame_upstream`, `::test_an_agent_that_changes_a_claim_owns_it`, `::test_schema_restamping_is_not_a_change` | Met, v3 draft |
| A-2 | A missing parent or a cycle shall end the walk as `BROKEN_LINEAGE`, naming the last known agent and never defaulting to the submitter. | `test_v3_attribution.py::test_a_missing_parent_is_broken_lineage_naming_the_last_known_agent`, `::test_a_cycle_is_broken_lineage_not_an_infinite_walk` | Met, v3 draft |
| A-3 | A flagged audit's suspicion shall be credited to the traced origin, not the audited agent. | `test_v3_attribution.py::test_attribution_stops_isolating_relays` | Met, v3 draft |
| A-4 | Attribution shall read lineage only from the lossy provenance log, which applies fidelity and spurious edges. | `test_v3_attribution.py::test_full_fidelity_records_every_true_edge_and_zero_records_none`, `::test_spurious_edges_are_never_true_edges_and_the_log_is_deterministic` | Met, v3 draft |
| A-5 | With attribution off, the sentinel shall name no evidence and consume no extra random draws. | `test_v3_attribution.py::test_no_evidence_and_no_extra_draws_when_attribution_is_off` | Met, v3 draft |

### Response

| ID | Requirement | Verification | Status |
| --- | --- | --- | --- |
| R-1 | No single audit shall trigger isolation. `audit_weight < isolation_threshold` is enforced at construction. | `test_suspicion.py::test_isolation_never_fires_on_a_single_audit`, `::test_config_rejects_a_threshold_a_single_audit_could_clear` | Met |
| R-2 | The controller shall expose only five scoped actions: isolate, halt outbound, restart clean, flag downstream and alert a human. | `src/harpy/response.py` | Manual |
| R-3 | Every isolation shall be scored from the world's side as true or false. False isolations shall also be marked as relays where applicable. | `test_instrumentation.py::test_false_isolation_count_matches_the_isolation_log`; `test_v3_followup.py::test_relay_isolations_are_a_subset_of_false_isolations` | Met |

### Studies and evidence

| ID | Requirement | Verification | Status |
| --- | --- | --- | --- |
| E-1 | A study shall write its preregistration before collecting outcomes. A specification whose `status` is not `frozen` shall not run. | `test_study.py::test_small_study_persists_design_metrics_and_verifiable_evidence`; `test_v3_study.py::test_a_draft_specification_cannot_produce_evidence` | Met |
| E-2 | PASS shall require the lower bound of incremental detection over matched TIER0_ONLY to reach the floor and the upper bound of overhead to stay under the ceiling. FAIL, INCONCLUSIVE and INCOMPLETE are distinct. A missing baseline cannot pass. | `test_study.py::test_preregistered_decision_uses_uncertainty`, `::test_missing_baseline_cannot_produce_a_pass` | Met |
| E-3 | A pre-freeze check shall report `CRITERIA_CONFLICT` when one detection's triage alone reaches the ceiling. Pilot seeds shall be ≥ 100000 and disjoint from study seeds. | `test_feasibility.py`; CI step "v3 mechanisms and the pre-freeze check" | Met, v3 draft |
| E-4 | Every evidence directory shall carry a SHA-256 manifest. Verification shall detect changed, missing, unlisted and path-escaping files. | `test_provenance.py`; CI `harpy verify` on all three packets | Met |
| E-5 | Published v2 evidence shall still reproduce, metric for metric, with every v3 option off. | `test_v2_reproduction.py`; CI check of the v2 source revision | Met, v3 draft |

### Replay

| ID | Requirement | Verification | Status |
| --- | --- | --- | --- |
| P-1 | Labels shall stay separate from observations. Answer fields nested in observations shall be rejected before any transport. | `test_replay.py::test_nested_answer_fields_are_rejected_before_transport`, `::test_hash_mismatch_and_incomplete_labels_are_rejected` | Met |
| P-2 | A provider error, refusal or incomplete response shall never become a clear verdict. | `test_replay.py::test_error_is_not_a_clear_verdict_and_reserves_unknown_charge` | Met |
| P-3 | A paid call shall require verified exact-model pricing and a cost reservation made before the call. | `test_replay.py::test_exact_model_pricing_is_required_before_replay`, `::test_cost_preflight_prevents_a_paid_call` | Met |
| P-4 | No API key shall appear in a request record or in evidence. | `test_replay.py::test_structured_request_and_usage_include_no_key_or_tools` | Met |

## 5. Non-functional requirements

| ID | Requirement | Verification | Status |
| --- | --- | --- | --- |
| N-1 | Determinism: the same inputs and seed shall give byte-identical result JSON, with no clock or host fields. | `test_determinism.py::test_written_json_files_are_byte_identical`, `::test_results_json_carries_no_wall_clock_or_environment_fields` | Met |
| N-2 | Independent random streams: changing a sampler or review draw shall not reshuffle the world being compared. | `test_determinism.py::test_escalation_draws_do_not_depend_on_the_sampler_stream` | Met |
| N-3 | Portability: CI shall pass on Linux, macOS and Windows with Python 3.11. | `.github/workflows/ci.yml` | Met |
| N-4 | Offline: the simulation shall need no network access and no credentials. | CI smoke run and sweep | Met |
| N-5 | Speed: one 30-agent, 500-tick run shall finish in a few seconds on one core. Measured at 1–3 s during the v3 pilot. | Measured, not enforced | Manual |
| N-6 | Honest reporting: results shall carry fleet scale, review policy, detector assumptions and whether pricing is verified. Figures from unverified pricing shall be watermarked. | `test_instrumentation.py::test_every_required_metric_is_emitted`; `plot.py::_watermark_if_unverified` | Met |
| N-7 | Code shall pass `ruff check` with no findings. | CI | Met |

## 6. Constraints

- **License:** MIT. Runtime dependencies are pyyaml, matplotlib, pandas, pyarrow and numpy.
- **Sentinel:** `MockSentinel` is the only component allowed to read ground truth. Its accuracy is configured, never learned.
- **Reporting:** no live-model accuracy may be claimed until replay has run on real traces.
- **Artwork:** README artwork is generated by `docs/assets/build/build_art.py`, with text converted to paths. Fonts are SIL OFL and are not committed.

## 7. Acceptance criteria for the v3 study

The v3 study is accepted when all of the following hold:

1. The primary cell, seed range and decision criteria are committed with `status: frozen` before any study outcome exists (E-1).
2. `harpy feasibility` reports the frozen primary cell as `FREEZABLE` at or below the frozen seed count (E-3).
3. The study verdict follows E-2:
   - at least 10 percentage points of incremental detection over matched TIER0_ONLY;
   - overhead under 10%;
   - both shown by the interval bounds, not the point estimates.
4. The packet verifies against its manifest, and v2 still reproduces (E-4, E-5).

The pilot's candidate is listed in [the v3 design notes](STUDY_V3_DESIGN.md#the-candidate-primary-cell-not-frozen).

## 8. Open items

| Item | Why it matters |
| --- | --- |
| Measure evidence naming on real traces | A-3 assumes the sentinel names the bad claim. The pilot shows the result survives at 50% accuracy, but the real number is unknown. |
| Measure relay flagging | The mock flags a relay's restatement as readily as the original. A real detector may not. |
| Anthropic adapter for replay | Replay calls only OpenAI today, so the cross-family Haiku sentinel cannot be measured yet. |
| Low-fidelity lineage | At fidelity 0.5, blame accuracy drops to about one in three, and follow-up audits chase relays. |
| DBOM refresh | `docs/governance/DBOM.md` still describes v2. |

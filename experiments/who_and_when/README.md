# Blame tracing on real failure logs (Who&When)

HARPY's simulation blames the agent that first states a bad claim, not the agents that repeat it. This experiment checks that rule against real multi-agent failures that people have labeled by hand.

**Short answer:** it holds for wrong values and fails for wrong actions.
- **Code-and-calculation teams:** the agent that first stated the wrong answer was the labeled culprit about twice as often as the agent that delivered it.
- **Web-browsing teams:** the decisive mistake usually comes several steps before any wrong claim exists. A rule that traces claims can't see it.

## Data

[Who&When](https://github.com/ag2ai/Agents_Failure_Attribution) is from the ICML 2025 spotlight paper *Which Agent Causes Task Failures and When?* (Zhang et al.). It has 184 failed runs. Each is labeled with the agent and the step whose mistake decided the failure.

| Subset | Runs | Systems |
| --- | --- | --- |
| Algorithm-Generated | 126 | AutoGen CaptainAgent teams: experts writing and running code |
| Hand-Crafted | 58 | Magentic-One: an orchestrator directing a web surfer, a file surfer and a coder |

Pinned at commit `f4d2b6d`. The dataset is MIT-licensed, © 2025 Ming Yin. It is not copied here; `results.json` quotes short final answers from it.

## Method

The rule is Tattletale's, applied to text. No model is called, and the labels are read only after every prediction is fixed.

1. **Take the final answer.**
   - Hand-Crafted: the `FINAL ANSWER:` line the run prints. Magentic-One's orchestrator writes it, so it is credited to the Orchestrator.
   - Algorithm-Generated: these logs have no answer field. Answers were read from each log's last two agent messages and recorded with their step in [`ag_final_answers.json`](ag_final_answers.json). 60 of 126 logs have one; the rest end without an answer.
2. **Normalize** with Tattletale's rules: NFKC, straight quotes, no soft hyphens, rejoined line-broken words, collapsed whitespace. Matching is whole-token and case-insensitive. Numbers match with or without thousands separators.
3. **Walk forward** to the first agent message that contains the answer. That agent and step are the origin.
   - If the first appearance is code-execution output (`Computer_terminal`, `ComputerTerminal`), blame passes to the agent whose code block produced it. This is Tattletale's verbatim-carry rule.
   - If the answer never appears verbatim before the final statement (a list assembled there, say), the final statement owns it.

**Baselines,** each scored on the same logs:
- **Final speaker:** blame the agent and step that delivered the final answer. This is the blame Tattletale argues against.
- **First speaker:** blame the first agent message.
- **Random:** a uniform choice of agent and of step in each log, with seed 0.

**Scoring** is an exact match on the agent name and on the 0-based step index, the same indexing as the paper's code. Role suffixes are dropped, so `Orchestrator (thought)` is scored as `Orchestrator`. A log with no final answer counts as a miss for the origin walk and the final-speaker baseline.

## Results

### All 184 logs, against the dataset paper

Agent and step accuracy (%). The paper's rows are copied from its Table 1 (GPT-4o, without ground truth), not re-run.

| Method | Algorithm-Generated: agent | Algorithm-Generated: step | Hand-Crafted: agent | Hand-Crafted: step |
| --- | --- | --- | --- | --- |
| **Origin walk (no model)** | **27.0** | **17.5** | **34.5** | **15.5** |
| Final speaker | 14.3 | 3.2 | 31.0 | 12.1 |
| First speaker | 49.2 | 15.9 | 31.0 | 1.7 |
| Random, ours | 41.3 | 11.9 | 39.7 | 1.7 |
| Paper: Random | 29.10 | 19.06 | 12.00 | 4.16 |
| Paper: All-at-Once | 51.12 | 13.53 | 53.44 | 3.51 |
| Paper: Step-by-Step | 26.02 | 15.31 | 32.75 | 8.77 |
| Paper: Binary Search | 30.11 | 16.59 | 36.21 | 6.90 |

95% intervals for the origin walk (Wilson):
- Algorithm-Generated: agent 20.0–35.3, step 11.8–25.0.
- Hand-Crafted: agent 23.6–47.3, step 8.4–26.9.

### Logs that ended with a wrong answer

These are the logs where the rule has something to trace.

| Subset | Logs | Origin walk: agent | Origin walk: step | Final speaker: agent | Final speaker: step |
| --- | --- | --- | --- | --- | --- |
| Algorithm-Generated | 60 | **56.7** | **36.7** | 30.0 | 6.7 |
| Hand-Crafted | 56 | 35.7 | 16.1 | 32.1 | 12.5 |

Paired comparison with the final speaker (exact McNemar test):
- **Algorithm-Generated:** where the two methods disagreed, the origin was right 24 times and the final speaker 8 (p = 0.007). At the step level the split was 21 to 3 (p = 0.0003).
- **Hand-Crafted:** the splits were 8 to 6 and 5 to 3. Neither is significant.

## What it shows

1. **When the error is a wrong value, blame belongs upstream.** In 42 of the 60 Algorithm-Generated runs with an answer, the agent that first stated it was not the one that delivered it. The first one was the labeled culprit about twice as often. This is HARPY's relay premise, seen in real logs.
2. **At the step level, a string rule keeps up with GPT-4o prompting.** Across all logs, its step accuracy (17.5%, 15.5%) matches or beats the paper's best GPT-4o method (16.59%, 8.77%). The intervals are wide, and later work reports higher numbers than these 2025 baselines.
3. **At the agent level across all logs, it loses.** GPT-4o All-at-Once scores 51% and 53%; the origin walk scores 27% and 34%. There are two reasons:
   - 66 of 126 Algorithm-Generated runs end with no answer at all, so there is nothing to trace.
   - In web-browsing runs, the decisive mistakes are actions, not claims (next point).
4. **Web-browsing failures start as actions.** In the Magentic-One logs, the wrong answer first appears a median of 7 steps *after* the labeled mistake: after it in 29 of 45 traceable logs, before it in 9. The labeled mistakes are typically a bad search, a wrong click or a bad plan, taken before any wrong claim exists. The origin walk's agent accuracy there (35.7%) is no better than random (37.5%). Always guessing WebSurfer would score 57%.

## What it means for HARPY

**Supported:** tracing a flagged claim to the agent that first stated it beats blaming the agent that passed it on, for errors that are wrong values. That is the case HARPY's simulated faults model.

**Not modeled:** two kinds of real failure that HARPY's faults don't cover.
- **No answer.** The team gives up or stalls. That was 52% of the Algorithm-Generated runs.
- **Wrong actions before any wrong claim.** A sentinel that judges only claims would miss these, and tracing can't recover them.

Both belong on HARPY's list of assumptions to test on real traces.

## Caveats

- **Rules changed after a first look.** A prototype ran on all logs before these rules were final. After it, four things changed:
  - the harness's own log lines were excluded;
  - text matching became whole-token;
  - the code-author rule was added for tool output;
  - thousands separators were ignored.

  The `traceable` flag (answer of two characters or fewer, or already in the task text) was also added afterward. The headline numbers don't use it. On traceable logs only, the origin walk scores 55.6 / 37.8 on Algorithm-Generated and 40.0 / 15.6 on Hand-Crafted.
- **Algorithm-Generated answers were read, not parsed.** The answers were extracted by Claude, an AI assistant, not by a script. Labels were not consulted, but during an earlier look at the file format the labels of five logs were visible (1, 7, 20, 45, 90). Three of those have no final answer. Every extraction is in `ag_final_answers.json` to audit.
- **Many Hand-Crafted answers appear only at the end.** In 26 of the 56 Hand-Crafted logs, the final answer never appears verbatim before the final statement, so the walk falls back to the Orchestrator. In the Algorithm-Generated logs that happened in 3 of 60.
- **One label per log.** Each log has a single decisive-error label, and the paper reports annotator uncertainty and initial disagreement. Where mistakes compound, a defensible different choice counts as a miss for every method.
- **Scoring is stricter than the paper's.** The paper's `evaluate.py` counts a hit when the label is a substring of the prediction, so step 1 matches a predicted 12. That can only raise its numbers.
- **The samples are small.** Read the intervals, not the point estimates.

## Reproduce

Standard library only, Python 3.11+.

```bash
git clone https://github.com/ag2ai/Agents_Failure_Attribution
git -C Agents_Failure_Attribution checkout f4d2b6d
python experiments/who_and_when/origin_walk.py "Agents_Failure_Attribution/Who&When"
```

It prints the tables above and rewrites `results.json`, which holds every per-log prediction. The output is byte-identical across runs.

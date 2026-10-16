"""Tattletale's origin rule on real multi-agent failure logs (Who&When).

Who&When (Zhang et al., ICML 2025; github.com/ag2ai/Agents_Failure_Attribution)
labels 184 failed multi-agent runs with the agent and step that caused the
failure. This script asks each log a narrower, deterministic question:

    The system ended with a wrong final answer. Which agent first said it?

It normalizes the final answer with Tattletale's rules and walks the log
forward to the first message containing it. A code-execution message only
carries what the code it ran produced, so a first appearance there is blamed
on the agent that wrote the code, the way Tattletale passes blame for a
verbatim carry upstream. No model is called and no label is read before
scoring.

Usage (Python 3.11+, standard library only):
    python experiments/who_and_when/origin_walk.py PATH/TO/Who&When
"""

from __future__ import annotations

import json
import random
import re
import sys
import unicodedata
from collections import Counter
from math import comb, sqrt
from pathlib import Path
from statistics import median

HERE = Path(__file__).resolve().parent
SUBSETS = ("Algorithm-Generated", "Hand-Crafted")
TOOL_AGENTS = {"computer_terminal", "computerterminal"}
HARNESS = re.compile(r"FINAL ANSWER:|SCENARIO\.PY COMPLETE|RUN\.SH COMPLETE")

# Who&When's paper, Table 1, GPT-4o without ground truth: (agent %, step %).
PAPER = {
    "Algorithm-Generated": {
        "Random": (29.10, 19.06),
        "All-at-Once": (51.12, 13.53),
        "Step-by-Step": (26.02, 15.31),
        "Binary Search": (30.11, 16.59),
    },
    "Hand-Crafted": {
        "Random": (12.00, 4.16),
        "All-at-Once": (53.44, 3.51),
        "Step-by-Step": (32.75, 8.77),
        "Binary Search": (36.21, 6.90),
    },
}

# -- Tattletale's normalization (tattletale ARCHITECTURE.md, section 5.1) ------
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES).replace("­", "")
    text = re.sub(r"-\s*\n\s*", "", text)
    return re.sub(r"\s+", " ", text).strip()


_NUMBER = re.compile(r"[-+]?\d[\d,]*(\.\d+)?")


def strip_separators(text: str) -> str:
    """'732,050' and '732050' are the same number."""
    return re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text)


def contains(haystack: str, answer: str) -> bool:
    """Whole-token, case-insensitive match after normalization."""
    haystack, answer = normalize(haystack), normalize(answer)
    if _NUMBER.fullmatch(answer):
        haystack, answer = strip_separators(haystack), strip_separators(answer)
        return re.search(rf"(?<![\w.]){re.escape(answer)}(?!\w|\.\d)", haystack) is not None
    return re.search(rf"(?<!\w){re.escape(answer)}(?!\w)", haystack, re.I) is not None


# -- reading a log -------------------------------------------------------------
def agent_of(message: dict) -> str:
    name = message.get("name") or message.get("role") or ""
    return re.sub(r"\s*\(.*?\)\s*$", "", name).strip()  # "Orchestrator (thought)"


def is_tool(agent: str) -> bool:
    return agent.replace(" ", "").lower() in TOOL_AGENTS


def is_human(message: dict) -> bool:
    return (message.get("role") or "").lower() == "human"


def spoken(message: dict) -> str:
    """Message text without the run harness's own log lines."""
    return HARNESS.split(message.get("content") or "", maxsplit=1)[0]


def final_answer(subset: str, case: str, history: list[dict], extracted: dict):
    """(answer, step, agent) for the system's final answer, or None."""
    if subset == "Hand-Crafted":
        for step in range(len(history) - 1, -1, -1):
            match = re.search(r"FINAL ANSWER:\s*(.+)", history[step].get("content") or "")
            if match:
                answer = re.split(r"SCENARIO\.PY|RUN\.SH", match.group(1))[0]
                answer = normalize(answer).rstrip(".").strip()
                # Magentic-One's orchestrator writes the final answer; the run
                # harness prints it at the end of whichever message came last.
                return (answer, step, "Orchestrator") if answer else None
        return None
    entry = extracted.get(case)
    if not entry:
        return None
    return entry["answer"], entry["stated_at"], agent_of(history[entry["stated_at"]])


def code_author(history: list[dict], step: int) -> tuple[str, int]:
    """The agent whose code a terminal message ran: the nearest earlier code block."""
    fallback = None
    for back in range(step - 1, -1, -1):
        message = history[back]
        agent = agent_of(message)
        if is_tool(agent) or is_human(message):
            continue
        if "```" in (message.get("content") or ""):
            return agent, back
        fallback = fallback or (agent, back)
    return fallback or (agent_of(history[step]), step)


def origin(history: list[dict], answer: str, stated_at: int, stater: str):
    """Tattletale's rule: the first agent to state the claim owns it."""
    for step in range(stated_at + 1):
        message = history[step]
        if is_human(message) or not contains(spoken(message), answer):
            continue
        agent = agent_of(message)
        if is_tool(agent):
            agent, step = code_author(history, step)
        return agent, step, True
    return stater, stated_at, False  # assembled in the final statement itself


# -- evaluation ----------------------------------------------------------------
def evaluate(root: Path) -> dict:
    extracted = json.loads((HERE / "ag_final_answers.json").read_text(encoding="utf-8"))
    rng = random.Random(0)
    rows = []
    for subset in SUBSETS:
        for path in sorted((root / subset).glob("*.json"), key=lambda p: int(p.stem)):
            record = json.loads(path.read_text(encoding="utf-8"))
            history = record["history"]
            speakers = [(agent_of(m), i) for i, m in enumerate(history) if not is_human(m)]
            agents = sorted({a for a, _ in speakers if not is_tool(a)})
            row = {
                "subset": subset,
                "case": path.stem,
                "steps": len(history),
                "random": (rng.choice(agents), rng.randrange(len(history))),
                "first_speaker": speakers[0],
            }
            found = final_answer(subset, path.stem, history, extracted)
            if found:
                answer, stated_at, stater = found
                o_agent, o_step, verbatim = origin(history, answer, stated_at, stater)
                row.update(
                    answer=answer,
                    final_speaker=(stater, stated_at),
                    origin=(o_agent, o_step),
                    found_earlier=verbatim,
                    # Too short to trace by string, or already in the task text.
                    ambiguous=len(answer) <= 2 or contains(record["question"], answer),
                )
            # Labels are read only here, after every prediction is fixed.
            truth_agent = agent_of({"name": str(record["mistake_agent"])})
            row["truth"] = (truth_agent, int(record["mistake_step"]))
            rows.append(row)
    return summarize(rows)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson interval for k successes in n, in percent."""
    if not n:
        return (0.0, 0.0)
    p, d = k / n, 1 + z * z / n
    centre, half = (p + z * z / (2 * n)) / d, z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(100 * (centre - half), 1), round(100 * (centre + half), 1))


def sign_test(pool: list[dict], a: str, b: str, index: int) -> dict:
    """Exact two-sided McNemar test: is method a right more often than method b?"""
    a_only = sum(
        1 for r in pool if (r[a][index] == r["truth"][index]) and r[b][index] != r["truth"][index]
    )
    b_only = sum(
        1 for r in pool if (r[b][index] == r["truth"][index]) and r[a][index] != r["truth"][index]
    )
    n, k = a_only + b_only, min(a_only, b_only)
    p = min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2**n) if n else 1.0
    return {
        "only_" + a.replace(" ", "_"): a_only,
        "only_" + b.replace(" ", "_"): b_only,
        "p_two_sided": round(p, 4),
    }


def score(pool: list[dict], key: str) -> dict:
    n = len(pool)
    agent = sum(1 for r in pool if r[key][0] == r["truth"][0])
    step = sum(1 for r in pool if r[key][1] == r["truth"][1])
    return {
        "n": n,
        "agent": agent,
        "step": step,
        "agent_pct": round(100 * agent / n, 1) if n else None,
        "step_pct": round(100 * step / n, 1) if n else None,
        "agent_ci95": wilson(agent, n),
        "step_ci95": wilson(step, n),
    }


def summarize(rows: list[dict]) -> dict:
    summary = {}
    for subset in SUBSETS:
        group = [r for r in rows if r["subset"] == subset]
        answered = [r for r in group if "answer" in r]
        traceable = [r for r in answered if not r["ambiguous"]]
        # A log with no final answer gives the origin walk nothing to trace: a miss.
        for r in group:
            r.setdefault("origin", (None, None))
            r.setdefault("final_speaker", (None, None))
        majority = Counter(r["truth"][0] for r in group).most_common(1)[0]
        block = {
            "logs": len(group),
            "with_final_answer": len(answered),
            "traceable": len(traceable),
            "majority_label": majority[0],
            "majority_label_pct": round(100 * majority[1] / len(group), 1),
            "pools": {},
        }
        for pool_name, pool in (
            ("all logs", group),
            ("final answer", answered),
            ("traceable", traceable),
        ):
            block["pools"][pool_name] = {
                method: score(pool, key)
                for method, key in (
                    ("origin walk", "origin"),
                    ("final speaker", "final_speaker"),
                    ("first speaker", "first_speaker"),
                    ("random", "random"),
                )
            }
        block["origin_vs_final_speaker_on_final_answer"] = {
            "agent": sign_test(answered, "origin", "final_speaker", 0),
            "step": sign_test(answered, "origin", "final_speaker", 1),
        }
        # Where the answer first appeared, relative to the labeled decisive error.
        offsets = [r["origin"][1] - r["truth"][1] for r in traceable]
        block["origin_minus_labeled_step"] = {
            "before": sum(1 for o in offsets if o < 0),
            "same": sum(1 for o in offsets if o == 0),
            "after": sum(1 for o in offsets if o > 0),
            "median": median(offsets) if offsets else None,
        }
        block["origin_is_not_final_speaker"] = sum(
            1 for r in answered if r["origin"] != r["final_speaker"]
        )
        summary[subset] = block
    return {"summary": summary, "paper_table1_gpt4o_without_ground_truth": PAPER, "rows": rows}


def report(result: dict) -> str:
    lines = []
    for subset, s in result["summary"].items():
        lines.append(
            f"\n{subset}: {s['logs']} logs, {s['with_final_answer']} with a final "
            f"answer, {s['traceable']} traceable; majority label "
            f"{s['majority_label']} {s['majority_label_pct']}%"
        )
        for pool, methods in s["pools"].items():
            for method, m in methods.items():
                lines.append(
                    f"  {pool:12s} {method:13s} agent {m['agent']:3d}/{m['n']:<3d} "
                    f"{m['agent_pct']:5.1f}% {str(m['agent_ci95']):14s}  step "
                    f"{m['step']:3d}/{m['n']:<3d} {m['step_pct']:5.1f}% {m['step_ci95']}"
                )
        lines.append(f"  origin vs final speaker: {s['origin_vs_final_speaker_on_final_answer']}")
        lines.append(f"  origin minus labeled step: {s['origin_minus_labeled_step']}")
        lines.append(
            f"  origin is not the final speaker in {s['origin_is_not_final_speaker']}"
            f"/{s['with_final_answer']}"
        )
    for subset, methods in PAPER.items():
        lines.append(
            f"\npaper, {subset}: " + ", ".join(f"{k} {a}/{b}" for k, (a, b) in methods.items())
        )
    return "\n".join(lines)


def main() -> None:
    result = evaluate(Path(sys.argv[1]))
    out = HERE / "results.json"
    out.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(report(result))


if __name__ == "__main__":
    main()

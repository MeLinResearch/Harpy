"""Generate HARPY's README artwork.

Every glyph is converted to a path, so the SVGs render identically on GitHub,
in any browser, in light and dark mode, with no font loading. The text-to-path
approach is borrowed from Tattletale's ``assets/build/build_art.py``.

Usage:
    pip install fonttools uharfbuzz
    HARPY_FONTS=/path/to/fonts python docs/assets/build/build_art.py

Fonts expected in HARPY_FONTS (all SIL OFL, from github.com/google/fonts; the
variable fonts there are instanced with ``fontTools.varLib.instancer``):
    BigShouldersDisplay-Black.ttf   Big Shoulders Display, wght 900
    IBMPlexSans-Regular.ttf         IBM Plex Sans, wdth 100, wght 400
    IBMPlexSans-SemiBold.ttf        IBM Plex Sans, wdth 100, wght 600
    IBMPlexSans-Bold.ttf            IBM Plex Sans, wdth 100, wght 700
    IBMPlexSansCond-Bold.ttf        IBM Plex Sans, wdth 85, wght 700
    IBMPlexMono-Regular.ttf, IBMPlexMono-Medium.ttf, IBMPlexMono-Bold.ttf

Numbers on the results card come from the v3 pre-freeze pilot
(evidence/v3-feasibility), HYBRID, no follow-ups, gpt-6.1-sol workers, a
Claude Haiku 4.5 sentinel. They are simulation results with an assumed detector.
"""

from __future__ import annotations

import math
import os
import re
from pathlib import Path
from xml.sax.saxutils import escape

import uharfbuzz as hb
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

FONT_DIR = Path(os.environ.get("HARPY_FONTS", Path.home() / ".fonts"))
OUT_DIR = Path(__file__).resolve().parents[1]

# -- palette -----------------------------------------------------------------
BG = "#0b0e14"
PANEL = "#131924"
PANEL2 = "#1a2230"
LINE = "#273145"
INK = "#e9eef6"
INK2 = "#a9b4c6"
MUTED = "#6f7b90"
RED = "#ff4d63"  # fault, origin, isolation
AMBER = "#ffb020"  # sentinel, audit, budget
CYAN = "#3cc8e6"  # lineage, tracing
VIOLET = "#a48bff"  # suspicion, people


# --------------------------------------------------------------------------
# text -> path
# --------------------------------------------------------------------------
class Face:
    def __init__(self, filename: str) -> None:
        path = FONT_DIR / filename
        self.tt = TTFont(path)
        self.glyphs = self.tt.getGlyphSet()
        self.order = self.tt.getGlyphOrder()
        self.upm = self.tt["head"].unitsPerEm
        self.hb = hb.Font(hb.Face(hb.Blob.from_file_path(str(path))))

    def _shape(self, s: str):
        buf = hb.Buffer()
        buf.add_str(s)
        buf.guess_segment_properties()
        hb.shape(self.hb, buf, {"kern": True, "liga": False})
        return buf.glyph_infos, buf.glyph_positions

    def width(self, s: str, size: float, track: float = 0) -> float:
        _, pos = self._shape(s)
        return sum(p.x_advance for p in pos) * size / self.upm + track * max(len(pos) - 1, 0)

    def d(self, s: str, x: float, y: float, size: float, track: float = 0) -> str:
        infos, pos = self._shape(s)
        k = size / self.upm
        pen = SVGPathPen(self.glyphs)
        cx = x
        for info, p in zip(infos, pos):
            name = self.order[info.codepoint]
            tp = TransformPen(pen, (k, 0, 0, -k, cx + p.x_offset * k, y - p.y_offset * k))
            self.glyphs[name].draw(tp)
            cx += p.x_advance * k + track
        return re.sub(
            r"-?\d+\.\d+", lambda m: f"{float(m.group()):.1f}".rstrip("0").rstrip("."),
            pen.getCommands(),
        )


F = {
    "display": Face("BigShouldersDisplay-Black.ttf"),
    "reg": Face("IBMPlexSans-Regular.ttf"),
    "semi": Face("IBMPlexSans-SemiBold.ttf"),
    "bold": Face("IBMPlexSans-Bold.ttf"),
    "cond": Face("IBMPlexSansCond-Bold.ttf"),
    "mono": Face("IBMPlexMono-Regular.ttf"),
    "monom": Face("IBMPlexMono-Medium.ttf"),
    "monob": Face("IBMPlexMono-Bold.ttf"),
}


class Svg:
    def __init__(self, w: int, h: int, label: str, bg: str | None = BG, rx: int = 16) -> None:
        self.w, self.h = w, h
        self.parts: list[str] = []
        self.defs: list[str] = []
        self.label = label
        if bg:
            self.add(f'<rect width="{w}" height="{h}" rx="{rx}" fill="{bg}"/>')

    def add(self, s: str) -> None:
        self.parts.append(s)

    def text(self, s, x, y, font, size, fill, anchor="start", track=0.0, op=None):
        w = F[font].width(s, size, track)
        if anchor == "middle":
            x -= w / 2
        elif anchor == "end":
            x -= w
        o = f' opacity="{op}"' if op is not None else ""
        self.add(f'<path d="{F[font].d(s, x, y, size, track)}" fill="{fill}"{o}/>')
        return w

    def lines(self, rows, x, y, font, size, fill, lead, anchor="start", track=0.0):
        for i, row in enumerate(rows):
            self.text(row, x, y + i * lead, font, size, fill, anchor, track)

    def rect(self, x, y, w, h, fill="none", rx=0, stroke=None, sw=1, dash=None, op=None):
        s = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
        d = f' stroke-dasharray="{dash}"' if dash else ""
        o = f' opacity="{op}"' if op is not None else ""
        self.add(f'<rect x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" rx="{rx}" '
                 f'fill="{fill}"{s}{d}{o}/>')

    def line(self, x1, y1, x2, y2, stroke, sw=1.5, dash=None, op=None, cap="round"):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        o = f' opacity="{op}"' if op is not None else ""
        self.add(f'<line x1="{x1:g}" y1="{y1:g}" x2="{x2:g}" y2="{y2:g}" stroke="{stroke}" '
                 f'stroke-width="{sw}" stroke-linecap="{cap}"{d}{o}/>')

    def path(self, d, stroke="none", sw=1.5, fill="none", dash=None, op=None, join="round"):
        dd = f' stroke-dasharray="{dash}"' if dash else ""
        o = f' opacity="{op}"' if op is not None else ""
        self.add(f'<path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}" '
                 f'stroke-linecap="round" stroke-linejoin="{join}"{dd}{o}/>')

    def circle(self, cx, cy, r, fill="none", stroke=None, sw=1.5, dash=None, op=None):
        s = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
        d = f' stroke-dasharray="{dash}"' if dash else ""
        o = f' opacity="{op}"' if op is not None else ""
        self.add(f'<circle cx="{cx:g}" cy="{cy:g}" r="{r:g}" fill="{fill}"{s}{d}{o}/>')

    def card(self, x, y, w, h, bar, fill=PANEL, rx=14, bar_w=5):
        """A rounded panel whose left accent bar is clipped to the panel's corners."""
        cid = f"c{len(self.defs)}"
        self.defs.append(f'<clipPath id="{cid}"><rect x="{x:g}" y="{y:g}" width="{w:g}" '
                         f'height="{h:g}" rx="{rx}"/></clipPath>')
        self.rect(x, y, w, h, fill=fill, rx=rx)
        self.add(f'<rect x="{x:g}" y="{y:g}" width="{bar_w}" height="{h:g}" fill="{bar}" '
                 f'clip-path="url(#{cid})"/>')

    def done(self) -> str:
        label = escape(self.label, {'"': "&quot;"})
        defs = f"<defs>{''.join(self.defs)}</defs>" if self.defs else ""
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" '
            f'width="{self.w}" height="{self.h}" role="img" aria-label="{label}">'
            f"<title>{escape(self.label)}</title>{defs}{''.join(self.parts)}</svg>\n"
        )


def arrowhead(s: Svg, x, y, angle, color, size=8):
    """A filled triangle pointing along ``angle`` (radians), tip at (x, y)."""
    a1, a2 = angle + math.radians(150), angle - math.radians(150)
    p1 = (x + size * math.cos(a1), y + size * math.sin(a1))
    p2 = (x + size * math.cos(a2), y + size * math.sin(a2))
    s.add(f'<path d="M{x:.1f} {y:.1f} L{p1[0]:.1f} {p1[1]:.1f} L{p2[0]:.1f} {p2[1]:.1f} Z" '
          f'fill="{color}"/>')


def wrap(text: str, font: str, size: float, width: float) -> list[str]:
    words, rows, row = text.split(), [], ""
    for word in words:
        trial = f"{row} {word}".strip()
        if F[font].width(trial, size) <= width or not row:
            row = trial
        else:
            rows.append(row)
            row = word
    if row:
        rows.append(row)
    return rows


def pill(s: Svg, text, cx, cy, color, fill=BG, font="monob", size=11, pad=10, track=0.6):
    w = F[font].width(text, size, track) + 2 * pad
    s.rect(cx - w / 2, cy - 12, w, 24, fill=fill, rx=12, stroke=color, sw=1.5)
    s.text(text, cx, cy + size * 0.36, font, size, color, anchor="middle", track=track)
    return w


def grid_dots(s: Svg, x0, y0, x1, y1, step=20, r=1.0, color=LINE, op=0.9):
    dots = []
    y = y0
    while y <= y1:
        x = x0
        while x <= x1:
            dots.append(f"M{x} {y}h0")
            x += step
        y += step
    s.add(f'<path d="{" ".join(dots)}" stroke="{color}" stroke-width="{2 * r}" '
          f'stroke-linecap="round" opacity="{op}"/>')


# --------------------------------------------------------------------------
# hero
# --------------------------------------------------------------------------
def hero() -> str:
    W, H = 800, 470
    s = Svg(W, H, "HARPY. Catch the agent that started it. Budgeted oversight for multi-agent "
                  "AI: a sentinel audits a sample of the mesh, flags a relay carrying a bad "
                  "claim, and blame tracing walks the claim's lineage back to the agent that "
                  "originated it, which is isolated.")
    grid_dots(s, 440, 64, 780, 420, step=20, r=0.9, op=0.7)

    # header strip
    s.text("MELINRESEARCH / HARPY", 32, 40, "monom", 11.5, MUTED, track=1.4)
    s.text("SIMULATION  ·  MIT  ·  PYTHON 3.11+", W - 32, 40, "monom", 11.5, MUTED,
           anchor="end", track=1.4)
    s.line(32, 54, W - 32, 54, LINE, sw=1)

    # wordmark + copy
    s.text("HARPY", 28, 196, "display", 150, INK, track=3)
    s.rect(34, 214, 64, 6, fill=AMBER, rx=3)
    s.text("Catch the agent", 32, 262, "bold", 30, INK)
    s.text("that started it.", 32, 298, "bold", 30, INK)
    s.lines(
        ["Budgeted oversight for multi-agent AI.",
         "Sparse audits spot the contamination.",
         "Blame tracing finds where it began."],
        32, 336, "reg", 15.5, INK2, lead=23,
    )

    # -- the mesh -----------------------------------------------------------
    nodes = {
        "h1": (478, 120), "h2": (566, 96), "h3": (676, 104), "h4": (758, 150),
        "h5": (468, 236), "h6": (500, 348), "h7": (600, 404), "h8": (742, 384),
        "o": (566, 214), "r1": (660, 230), "r2": (742, 268), "r3": (612, 318),
    }
    healthy_edges = [("h1", "h2"), ("h2", "h3"), ("h3", "h4"), ("h1", "h5"), ("h5", "h6"),
                     ("h6", "h7"), ("h7", "h8"), ("h4", "r2"), ("h2", "o"), ("h5", "o"),
                     ("h3", "r1"), ("h8", "r2"), ("h6", "r3"), ("h7", "r3")]
    bad_edges = [("o", "r1"), ("r1", "r2"), ("o", "r3")]
    for a, b in healthy_edges:
        (x1, y1), (x2, y2) = nodes[a], nodes[b]
        s.line(x1, y1, x2, y2, "#2c3850", sw=1.5)
    for a, b in bad_edges:
        (x1, y1), (x2, y2) = nodes[a], nodes[b]
        s.line(x1, y1, x2, y2, RED, sw=2.5, op=0.85)

    # trace path: r2 -> r1 -> o, offset above the red edges
    def offset(p, q, d):
        (x1, y1), (x2, y2) = p, q
        L = math.hypot(x2 - x1, y2 - y1)
        nx, ny = -(y2 - y1) / L * d, (x2 - x1) / L * d
        return (x1 + nx, y1 + ny), (x2 + nx, y2 + ny)

    seg1 = offset(nodes["r2"], nodes["r1"], 11)
    seg2 = offset(nodes["r1"], nodes["o"], 11)
    (ax, ay), (bx, by) = seg1
    (cx, cy), (dx, dy) = seg2
    # shorten ends so the arrow stops at the origin's ring
    ang = math.atan2(dy - cy, dx - cx)
    dx2, dy2 = dx - 22 * math.cos(ang), dy - 22 * math.sin(ang)
    ax2, ay2 = ax + 18 * math.cos(math.atan2(by - ay, bx - ax)), ay + 18 * math.sin(
        math.atan2(by - ay, bx - ax))
    s.path(f"M{ax2:.1f} {ay2:.1f} L{(bx + cx) / 2:.1f} {(by + cy) / 2:.1f} L{dx2:.1f} {dy2:.1f}",
           stroke=CYAN, sw=2.2, dash="6 5")
    arrowhead(s, dx2, dy2, ang, CYAN, size=10)

    for key, (x, y) in nodes.items():
        if key.startswith("h"):
            s.circle(x, y, 10, fill=PANEL2, stroke="#3a4760", sw=1.5)
    for key in ("r1", "r2", "r3"):
        x, y = nodes[key]
        s.circle(x, y, 11, fill="#2a1520", stroke=RED, sw=2)
    ox, oy = nodes["o"]
    s.circle(ox, oy, 30, stroke=RED, sw=1, op=0.35)
    s.circle(ox, oy, 21, stroke=RED, sw=1.5, op=0.7)
    s.circle(ox, oy, 13, fill=RED)

    # audit reticle on r2
    rx, ry = nodes["r2"]
    k, g = 22, 9
    for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        s.path(f"M{rx + sx * k:.1f} {ry + sy * (k - g):.1f} L{rx + sx * k:.1f} {ry + sy * k:.1f} "
               f"L{rx + sx * (k - g):.1f} {ry + sy * k:.1f}", stroke=AMBER, sw=2.5)

    # labels
    pill(s, "ORIGIN · ISOLATED", ox - 6, oy - 52, RED)
    pill(s, "AUDIT · FLAGGED", rx - 12, ry + 44, AMBER)
    s.text("blame walks back", 708, 196, "monom", 11, CYAN, anchor="middle", track=0.3)

    # footer strip
    s.line(32, 432, W - 32, 432, LINE, sw=1)
    items = ["AUDITS BOUGHT FROM A FIXED BUDGET", "BLAME TRACED, NOT GUESSED",
             "PREREGISTERED KILL CONDITION"]
    widths = [F["monom"].width(t, 10.5, 1.2) for t in items]
    gap = (W - 64 - sum(widths)) / (len(items) - 1)
    x = 32
    for t, w in zip(items, widths):
        s.text(t, x, 454, "monom", 10.5, MUTED, track=1.2)
        x += w + gap
    return s.done()


# --------------------------------------------------------------------------
# at a glance
# --------------------------------------------------------------------------
def glance() -> str:
    W, H = 800, 104
    chips = [
        ("BUDGET", AMBER, "Fixed oversight %", "audits bought, never assumed"),
        ("BLAME", CYAN, "Traced to origin", "Tattletale's lineage rule"),
        ("RIGOR", VIOLET, "Preregistered", "kill condition frozen first"),
        ("SETUP", RED, "No API key", "the simulation runs offline"),
    ]
    s = Svg(W, H, "At a glance: audits are bought from a fixed share of worker spend; blame is "
                  "traced to the originating agent with Tattletale's lineage rule; the kill "
                  "condition is preregistered; the simulation needs no API key.", bg=None)
    gap = 12
    w = (W - 3 * gap) / 4
    for i, (tag, color, title, sub) in enumerate(chips):
        x = i * (w + gap)
        s.card(x, 0, w, H, color)
        s.text(tag, x + 22, 30, "monob", 10.5, color, track=1.6)
        s.text(title, x + 22, 59, "semi", 18, INK)
        s.text(sub, x + 22, 83, "reg", 12, INK2)
    return s.done()


# --------------------------------------------------------------------------
# how it works
# --------------------------------------------------------------------------
def how() -> str:
    steps = [
        ("OBSERVE", CYAN, "Tier 0 reads every message",
         "Seven mechanical checks on typed fields. Almost free."),
        ("AUDIT", AMBER, "A sentinel samples the mesh",
         "Paid for from a fixed share of worker spend."),
        ("TRACE", CYAN, "Walk the flagged claim back",
         "Unchanged carries pass the blame upstream."),
        ("ACCUMULATE", VIOLET, "Suspicion builds on the source",
         "Evidence decays over time. One flag is never enough."),
        ("RESPOND", RED, "Isolate, flag, alert",
         "Five scoped actions. Human alerts have their own cap."),
        ("SCORE", INK2, "The world keeps the answer key",
         "A guard stops HARPY from reading ground truth."),
    ]
    W = 800
    m, gap, ch = 16, 14, 112
    cw = (W - 2 * m - gap) / 2
    H = m * 2 + 3 * ch + 2 * gap
    s = Svg(W, H, "How it works: 1 observe, Tier 0 reads every message; 2 audit, a sentinel "
                  "samples the mesh within a fixed budget; 3 trace, walk the flagged claim's "
                  "lineage back to its source; 4 accumulate, suspicion builds on the source; "
                  "5 respond, isolate, flag downstream and alert within a cap; 6 score, the "
                  "world keeps the answer key and HARPY never reads it.")
    for i, (tag, color, title, body) in enumerate(steps):
        col, row = i % 2, i // 2
        x, y = m + col * (cw + gap), m + row * (ch + gap)
        s.card(x, y, cw, ch, color)
        s.rect(x + 24, y + 22, 34, 34, fill=color, rx=8)
        s.text(str(i + 1), x + 41, y + 46, "monob", 17, BG, anchor="middle")
        s.text(tag, x + 72, y + 36, "monob", 10.5, color, track=1.6)
        s.text(title, x + 72, y + 56, "semi", 17.5, INK)
        s.lines(wrap(body, "reg", 13.5, cw - 48), x + 24, y + 90, "reg", 13.5, INK2, lead=20)
    return s.done()


# --------------------------------------------------------------------------
# results
# --------------------------------------------------------------------------
def results() -> str:
    W, H = 800, 340
    s = Svg(W, H, "What blame tracing changes, in the v3 pilot (40 seeds, simulated sentinel, "
                  "not a frozen study). Faults caught: 7 of 40 without tracing, 40 of 40 with "
                  "it. Healthy agents isolated per run: 1.57 to 0.07. Oversight cost as a share "
                  "of worker spend: 13.8% to 8.4%, under the 10% ceiling. Median corrupt claims "
                  "per run: about 860 to about 135.")
    s.text("WHAT BLAME TRACING CHANGES", 28, 38, "monob", 11.5, INK, track=1.6)
    s.text("PILOT · 40 SEEDS · SIMULATED SENTINEL · NOT A FROZEN STUDY", W - 28, 38, "monom",
           10, MUTED, anchor="end", track=1.0)
    tiles = [
        ("FAULTS CAUGHT", "7/40", "40/40", "subtle 1–3% value faults", RED),
        ("HEALTHY AGENTS ISOLATED", "1.57", "0.07", "per run, mostly relays", VIOLET),
        ("OVERSIGHT COST", "13.8%", "8.4%", "of worker spend · cap 10%", AMBER),
        ("CORRUPT CLAIMS", "~860", "~135", "median per run", CYAN),
    ]
    gap, m, top = 12, 16, 58
    tw = (W - 2 * m - 3 * gap) / 4
    th = 204
    for i, (label, before, after, sub, color) in enumerate(tiles):
        x = m + i * (tw + gap)
        s.rect(x, top, tw, th, fill=PANEL, rx=14)
        s.rect(x + 18, top + 20, 22, 4, fill=color, rx=2)
        s.text(label, x + 18, top + 44, "monob", 9.5, INK2, track=1.0)
        s.text("WITHOUT", x + 18, top + 72, "monom", 9, MUTED, track=1.2)
        bw = s.text(before, x + 18, top + 96, "semi", 22, MUTED)
        s.line(x + 16, top + 88, x + 20 + bw, top + 88, MUTED, sw=1.5)
        s.text("WITH TRACING", x + 18, top + 124, "monom", 9, MUTED, track=1.2)
        s.text(after, x + 18, top + 156, "bold", 34, INK)
        s.text(sub, x + 18, top + 184, "reg", 11.5, INK2)
    s.line(28, 296, W - 28, 296, LINE, sw=1)
    s.text("HYBRID sampler · gpt-6.1-sol workers · Claude Haiku 4.5 sentinel · fixed review "
           "cost · fleet scale 100", 28, 320, "mono", 10.5, MUTED)
    return s.done()


# --------------------------------------------------------------------------
# data model (ERD)
# --------------------------------------------------------------------------
def erd() -> str:
    W = 800
    colx = (16, 444)
    cw = 340
    # (name, subtitle, color, fields[(key, name, note)], column, top)
    entities = {
        "agent": ("Agent", "a worker in the mesh", RED,
                  [("PK", "agent_id", ""), ("", "active · halted · restarts", "")]),
        "message": ("Message", "one emission per tick", RED,
                    [("PK", "message_id", ""), ("FK", "sender_id → recipient_id", ""),
                     ("", "malformed · tool_error · retries", "Tier 0 reads these")]),
        "claim": ("Claim", "a typed value an agent asserts", RED,
                  [("PK", "claim_id", "uuid5(seed, tick, agent, n)"),
                   ("FK", "origin_agent_id", "who submitted it"),
                   ("", "subject · field · value", "what \"unchanged\" compares"),
                   ("GT", "is_corrupt", "ground truth · scoring only")]),
        "edge": ("Lineage edge", "provenance, as recorded", CYAN,
                 [("FK", "parent_claim_id", ""), ("FK", "child_claim_id", ""),
                  ("", "recorded with p = fidelity", "plus spurious edges")]),
        "audit": ("Audit", "one sentinel call", AMBER,
                  [("FK", "agent_id", "whose window was read"),
                   ("", "flagged", ""),
                   ("FK", "evidence_claim_ids", "the claim it names"),
                   ("", "tokens · dollars", "charged to the budget")]),
        "trace": ("Attribution", "Tattletale's origin walk", CYAN,
                  [("FK", "origin_agent_id", "who gets the blame"),
                   ("", "TRACED | BROKEN_LINEAGE", ""), ("", "hops", "unchanged carries")]),
        "suspicion": ("Suspicion", "evidence, decaying per tick", VIOLET,
                      [("FK", "agent_id", ""), ("", "score ≥ gate → isolate", "")]),
        "isolation": ("Isolation", "a scoped response", RED,
                      [("FK", "agent_id", ""), ("", "flag downstream · alert human", ""),
                       ("GT", "was_faulty · relay", "scored by the world")]),
    }
    left = ["agent", "message", "claim", "edge"]
    right = ["audit", "trace", "suspicion", "isolation"]
    head, rowh, rowgap, vgap = 66, 40, 6, 56

    def height(key):
        fields = entities[key][3]
        return head + len(fields) * (rowh + rowgap) + 10

    pos = {}
    for col, keys in enumerate((left, right)):
        y = 16
        for key in keys:
            pos[key] = (colx[col], y, height(key))
            y += height(key) + vgap
    ledger_y = max(p[1] + p[2] for p in pos.values()) + vgap
    H = ledger_y + 118 + 16
    s = Svg(W, H, "HARPY data model. Agents emit messages that carry claims; lineage edges link "
                  "parent and child claims. An audit reads an agent's window and names an "
                  "evidence claim; attribution walks lineage back to the origin agent and "
                  "credits suspicion there; suspicion past the gate triggers isolation. Ground "
                  "truth fields are readable only by scoring code. A ledger charges every "
                  "oversight cost against worker spend.")

    def card(key):
        name, sub, color, fields = entities[key]
        x, y, h = pos[key]
        s.card(x, y, cw, h, color)
        s.text(name, x + 24, y + 32, "bold", 20, color)
        s.text(sub, x + 24, y + 52, "reg", 12.5, INK2)
        fy = y + head
        for tag, fname, note in fields:
            s.rect(x + 16, fy, cw - 32, rowh, fill=PANEL2, rx=8)
            tx = x + 28
            if tag:
                tcol = RED if tag == "GT" else color
                tw = F["monob"].width(tag, 10) + 14
                s.rect(tx, fy + (12 if not note else 7), tw, 18, fill="none", rx=5,
                       stroke=tcol, sw=1.2)
                s.text(tag, tx + tw / 2, fy + (25 if not note else 20), "monob", 10, tcol,
                       anchor="middle")
                tx += tw + 10
            if note:
                s.text(fname, tx, fy + 20, "monom", 13, INK)
                s.text(note, x + 28, fy + 34, "reg", 10.5, MUTED)
            else:
                s.text(fname, tx, fy + 25, "monom", 13, INK)
            fy += rowh + rowgap

    for key in left + right:
        card(key)

    def vlink(a, b, label, color):
        xa, ya, ha = pos[a]
        _, yb, _ = pos[b]
        cx = xa + cw / 2
        s.line(cx, ya + ha + 4, cx, yb - 10, color, sw=2, op=0.8)
        arrowhead(s, cx, yb - 4, math.pi / 2, color, size=9)
        pill(s, label, cx, (ya + ha + yb) / 2 - 2, color, size=10.5)

    vlink("agent", "message", "emits", RED)
    vlink("message", "claim", "carries", RED)
    vlink("claim", "edge", "parent → child", CYAN)
    vlink("audit", "trace", "starts", AMBER)
    vlink("trace", "suspicion", "credits the origin", CYAN)
    vlink("suspicion", "isolation", "crosses the gate", VIOLET)

    # cross links: audit -> agent (reads window), attribution -> lineage edge (walks)
    ax, ay, _ = pos["audit"]
    gx, gy, _ = pos["agent"]
    y1 = gy + 40
    s.line(ax - 8, y1, gx + cw + 14, y1, AMBER, sw=2, op=0.85)
    arrowhead(s, gx + cw + 8, y1, math.pi, AMBER, size=9)
    s.text("reads", (gx + cw + ax) / 2, y1 - 9, "monob", 10.5, AMBER, anchor="middle")

    tx, ty, th = pos["trace"]
    ex, ey, eh = pos["edge"]
    yb = ey + 40
    midx = (ex + cw + tx) / 2
    s.path(f"M{tx - 8} {ty + 40} L{midx} {ty + 40} L{midx} {yb} L{ex + cw + 14} {yb}",
           stroke=CYAN, sw=2, dash="6 5", op=0.9)
    arrowhead(s, ex + cw + 8, yb, math.pi, CYAN, size=9)
    s.text("walks", (ex + cw + midx) / 2 + 2, yb - 9, "monob", 10.5, CYAN, anchor="middle")

    # ledger
    s.card(16, ledger_y, W - 32, 118, AMBER)
    s.text("Ledger", 40, ledger_y + 32, "bold", 20, AMBER)
    s.text("every oversight dollar, measured against worker spend", 40, ledger_y + 52, "reg",
           12.5, INK2)
    comps = ["sentinel", "Tier 0", "discarded work", "reruns", "triage", "investigation"]
    x = 40
    for c in comps:
        w = F["monom"].width(c, 12.5) + 24
        s.rect(x, ledger_y + 68, w, 30, fill=PANEL2, rx=8)
        s.text(c, x + 12, ledger_y + 88, "monom", 12.5, INK)
        x += w + 8
    s.text("÷ worker $ = overhead", W - 40, ledger_y + 32, "monob", 12.5, AMBER, anchor="end")
    return s.done()


# --------------------------------------------------------------------------
# AI nutrition facts
# --------------------------------------------------------------------------
def nutrition() -> str:
    W, H = 380, 740
    black, white = "#111111", "#ffffff"
    s = Svg(W, H, "AI Nutrition Facts for HARPY. Serving size: one simulation run. Model calls "
                  "per serving: 0. Sentinel audits at most 5% of worker spend; total oversight "
                  "target under 10%; human alerts capped. Trained weights, fine-tuning, "
                  "retrieval corpus and personal data: none. Assumed detector accuracy, inputs "
                  "not measurements: overt 95%, moderate 78%, subtle 42%, false positives 2%.",
            bg=white, rx=6)
    s.rect(6, 6, W - 12, H - 12, fill="none", stroke=black, sw=2, rx=3)
    L, R = 18, W - 18

    def rule(y, sw):
        s.rect(L, y, R - L, sw, fill=black)

    def row(y, label, value, bold_label=True, indent=0, size=14, value_font="cond"):
        s.text(label, L + indent, y, "cond" if bold_label else "reg", size, black)
        if value:
            s.text(value, R, y, value_font, size, black, anchor="end")
        s.rect(L, y + 7, R - L, 1, fill=black)

    s.text("AI Nutrition Facts", L - 1, 56, "display", 48, black, track=0.5)
    rule(68, 1)
    s.text("Servings per study", L, 88, "reg", 14, black)
    s.text("1,800+", R, 88, "reg", 14, black, anchor="end")
    s.text("Serving size", L, 110, "cond", 17, black)
    s.text("1 simulation run", R, 110, "cond", 17, black, anchor="end")
    rule(120, 10)
    s.text("Amount per serving", L, 146, "cond", 12, black)
    s.text("Model calls", L, 182, "display", 34, black, track=0.5)
    s.text("0", R, 184, "display", 46, black, anchor="end")
    rule(196, 5)
    s.text("% Worker spend*", R, 216, "cond", 12, black, anchor="end")
    rule(223, 1)
    y = 244
    row(y, "Sentinel audits", "≤ 5%")
    row(y + 24, "Total oversight target", "< 10%")
    row(y + 48, "Tier 0 checks", "~0%", bold_label=False, indent=14)
    row(y + 72, "Human alerts", "capped", bold_label=False, indent=14)
    row(y + 96, "Trained weights", "0g")
    row(y + 120, "Fine-tuning", "0g")
    row(y + 144, "Retrieval corpus", "0g")
    row(y + 168, "Personal data", "0g")
    rule(y + 182, 10)
    y2 = y + 212
    s.text("Assumed detector accuracy", L, y2, "cond", 14, black)
    s.text("inputs, not measurements", R, y2, "reg", 11, black, anchor="end")
    s.rect(L, y2 + 7, R - L, 1, fill=black)
    for i, (k, v) in enumerate((("Overt faults", "95%"), ("Moderate faults", "78%"),
                                ("Subtle faults", "42%"), ("False positives", "2%"))):
        row(y2 + 28 + 24 * i, k, v, bold_label=False, indent=14)
    rule(y2 + 112, 5)
    y3 = y2 + 134
    ingredients = ("INGREDIENTS: Seeded synthetic agent mesh, typed claims, planted faults "
                   "(overt, moderate, subtle), seven mechanical Tier 0 signals, mock sentinel "
                   "(assumed probabilities), Tattletale-style blame tracing, lossy provenance "
                   "log, sourced token list prices, modeled analyst time.")
    rows = wrap(ingredients, "reg", 11.5, R - L)
    s.lines(rows, L, y3, "reg", 11.5, black, lead=15)
    y4 = y3 + 15 * len(rows) + 6
    s.text("CONTAINS: simulation only. Live-model accuracy not measured.", L, y4, "cond", 11.5,
           black)
    rule(y4 + 12, 1)
    foot = ("* Share of worker-fleet spend, the budget HARPY is judged against. "
            "A project label, not a compliance certification.")
    s.lines(wrap(foot, "reg", 10, R - L), L, y4 + 30, "reg", 10, black, lead=13)
    return s.done()


def main() -> None:
    out = OUT_DIR
    files = {
        "harpy-hero.svg": hero(),
        "harpy-glance.svg": glance(),
        "harpy-how.svg": how(),
        "harpy-results.svg": results(),
        "harpy-erd.svg": erd(),
        "harpy-nutrition.svg": nutrition(),
    }
    for name, svg in files.items():
        (out / name).write_text(svg, encoding="utf-8")
        print(f"wrote {out / name} ({len(svg) // 1024} KB)")


if __name__ == "__main__":
    main()

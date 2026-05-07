#!/usr/bin/env python3
"""
Directional chord diagrams — one per (benchmark, model, harness, outcome).
Chord color = exact same hex as source action's outer arc.
Output: 48 individual PDFs under figures/chords/<Bench>/<Model>/
"""

import csv
from pathlib import Path as Pathlib

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.path import Path as MplPath
import matplotlib.patches as patches

LW = 0.3

ROOT = Pathlib(__file__).resolve().parent.parent
DATA_DIR = ROOT / "analyze_tools" / "data"
OUT_DIR = ROOT / "analyze_tools" / "figures" / "chords"

OLD_TO_NEW = {
    "Problem Framing":   "Understanding",
    "Plan Formation":    "Planning",
    "Solution Execution":"Reasoning",
    "Tool Grounding":    "Tool Use",
    "Result Auditing":   "Verification",
    "Answer Delivery":   "Finalization",
}
ACTIONS = ["Understanding", "Planning", "Reasoning",
           "Tool Use", "Verification", "Finalization"]
SHORT    = ["Understand", "Plan", "Reason", "Tool", "Verify", "Finalize"]
HEX      = ["#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00", "#a65628"]
HARNESSES = ["DirectLLM", "OpenClaw", "OpenCode", "ZeroClaw"]


def polar2xy(r, theta):
    return np.array([r * np.cos(theta), r * np.sin(theta)])

def hex2rgb(c):
    return tuple(int(c[i:i+2], 16) / 256.0 for i in (1, 3, 5))


# ── drawing primitives ───────────────────────────────────────────────────────

def IdeogramArc(start=0, end=60, radius=1.0, width=0.2, ax=None, color_hex="#000000"):
    """Outer arc segment. color_hex is the exact hex colour string."""
    if start > end:
        start, end = end, start
    span = end - start
    if span > 90:
        n_seg = int(np.ceil(span / 90.))
        seg = span / n_seg
        for k in range(n_seg):
            IdeogramArc(start + k * seg, start + (k + 1) * seg,
                        radius, width, ax, color_hex)
        return
    sr, er = start * np.pi / 180., end * np.pi / 180.
    opt = 4./3. * np.tan((er - sr) / 4.) * radius
    inner = radius * (1 - width)
    verts = [
        polar2xy(radius, sr),
        polar2xy(radius, sr) + polar2xy(opt, sr + 0.5*np.pi),
        polar2xy(radius, er) + polar2xy(opt, er - 0.5*np.pi),
        polar2xy(radius, er),
        polar2xy(inner, er),
        polar2xy(inner, er) + polar2xy(opt*(1-width), er - 0.5*np.pi),
        polar2xy(inner, sr) + polar2xy(opt*(1-width), sr + 0.5*np.pi),
        polar2xy(inner, sr),
        polar2xy(radius, sr),
    ]
    codes = [MplPath.MOVETO, MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4,
             MplPath.LINETO, MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4,
             MplPath.CLOSEPOLY]
    if ax is None:
        return verts, codes
    path = MplPath(verts, codes)
    patch = patches.PathPatch(path, facecolor=color_hex, edgecolor='white',
                              alpha=1.0, lw=0.8)
    ax.add_patch(patch)


def ChordArc(start1, end1, start2, end2, radius=1.0, chordwidth=0.7,
             ax=None, color_hex="#000000"):
    """Directional chord from (start1,end1) → (start2,end2). Color = source hex."""
    if start1 > end1: start1, end1 = end1, start1
    if start2 > end2: start2, end2 = end2, start2
    s1, e1 = start1*np.pi/180., end1*np.pi/180.
    s2, e2 = start2*np.pi/180., end2*np.pi/180.
    o1 = 4./3.*np.tan((e1-s1)/4.)*radius
    o2 = 4./3.*np.tan((e2-s2)/4.)*radius
    r_inner = radius * (1 - chordwidth)
    # Taper: target side slightly narrower → directional cue
    r_target = radius * (1 - chordwidth * 1.4)
    verts = [
        polar2xy(radius, s1),
        polar2xy(radius, s1) + polar2xy(o1, s1+0.5*np.pi),
        polar2xy(radius, e1) + polar2xy(o1, e1-0.5*np.pi),
        polar2xy(radius, e1),
        polar2xy(r_inner, e1),
        polar2xy(r_target, s2),
        polar2xy(radius, s2),
        polar2xy(radius, s2) + polar2xy(o2, s2+0.5*np.pi),
        polar2xy(radius, e2) + polar2xy(o2, e2-0.5*np.pi),
        polar2xy(radius, e2),
        polar2xy(r_target, e2),
        polar2xy(r_inner, s1),
        polar2xy(radius, s1),
    ]
    codes = [MplPath.MOVETO, MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4,
             MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4,
             MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4,
             MplPath.CURVE4]
    if ax is None:
        return verts, codes
    path = MplPath(verts, codes)
    patch = patches.PathPatch(path, facecolor=color_hex, edgecolor='white',
                              alpha=1.0, lw=0.5)
    ax.add_patch(patch)


def selfChordArc(start, end, radius=1.0, chordwidth=0.7, ax=None, color_hex="#000000"):
    if start > end: start, end = end, start
    sr, er = start*np.pi/180., end*np.pi/180.
    opt = 4./3.*np.tan((er-sr)/4.)*radius
    rc = radius*(1-chordwidth)
    verts = [
        polar2xy(radius, sr),
        polar2xy(radius, sr) + polar2xy(opt, sr+0.5*np.pi),
        polar2xy(radius, er) + polar2xy(opt, er-0.5*np.pi),
        polar2xy(radius, er),
        polar2xy(rc, er), polar2xy(rc, sr), polar2xy(radius, sr),
    ]
    codes = [MplPath.MOVETO, MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4,
             MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4]
    if ax is None:
        return verts, codes
    path = MplPath(verts, codes)
    patch = patches.PathPatch(path, facecolor=color_hex, edgecolor='white',
                              alpha=1.0, lw=0.8)
    ax.add_patch(patch)


# ── chord layout ─────────────────────────────────────────────────────────────

def chordDiagram(X, ax, colors_hex=None, width=0.1, pad=2, chordwidth=0.7):
    """X[i,j] = flux i→j. Chord colour = colours_hex[source]."""
    x = X.sum(axis=1)
    ax.set_xlim(-1.05, 1.05); ax.set_ylim(-1.05, 1.05)

    if colors_hex is None:
        colors_hex = HEX[:len(x)]

    y = x / np.sum(x).astype(float) * (360 - pad * len(x))
    pos, arc, nodePos = {}, [], []
    start = 0
    for i in range(len(x)):
        end = start + y[i]
        arc.append((start, end))
        angle = 0.5*(start+end)
        angle -= 90 if -30 <= angle <= 210 else 270
        nodePos.append(tuple(polar2xy(1.12, 0.5*(start+end)*np.pi/180.)) + (angle,))
        z = (X[i,:] / x[i].astype(float)) * (end-start)
        ids = np.argsort(z)
        z0 = start
        for j in ids:
            pos[(i,j)] = (z0, z0+z[j]); z0 += z[j]
        start = end + pad

    # Outer arcs
    for i in range(len(x)):
        IdeogramArc(start=arc[i][0], end=arc[i][1], radius=1.0, ax=ax,
                     width=width, color_hex=colors_hex[i])

    # Inter-node chords — sorted by weight (smallest first, largest on top)
    chords_list = []
    for i in range(len(x)):
        for j in range(len(x)):
            if i == j or X[i,j] <= 0:
                continue
            chords_list.append((X[i,j], i, j))
    chords_list.sort(key=lambda t: t[0])  # smallest weight first
    for _, i, j in chords_list:
        ChordArc(pos[(i,j)][0], pos[(i,j)][1],
                 pos[(j,i)][0], pos[(j,i)][1],
                 radius=1.-width, chordwidth=chordwidth,
                 ax=ax, color_hex=colors_hex[i])

    # Self-loops
    for i in range(len(x)):
        ss, se = pos[(i,i)]
        if se - ss > 0.1:
            selfChordArc(ss, se, radius=1.-width,
                         chordwidth=chordwidth*0.7, ax=ax, color_hex=colors_hex[i])
    return nodePos


# ── data ─────────────────────────────────────────────────────────────────────

def load_transitions():
    rows = []
    for model, fn in [
        ("Qwen", "six_action_statistics/action_transitions_by_outcome.csv"),
        ("Gemma", "six_action_statistics_gemma/action_transitions_by_outcome.csv"),
    ]:
        p = DATA_DIR / fn
        if p.exists():
            for r in csv.DictReader(open(p)):
                r["model"] = model; rows.append(r)
    return rows


def build_matrix_raw(rows, bench, model, harness, outcome):
    """Build raw 6x6 count matrix — no padding."""
    idx = {a:i for i,a in enumerate(ACTIONS)}
    mat = np.zeros((6,6))
    for r in rows:
        if (r["benchmark"]==bench and r["model"]==model
                and r["harness"]==harness and r["outcome"]==outcome):
            fn = OLD_TO_NEW.get(r["from_action"])
            tn = OLD_TO_NEW.get(r["to_action"])
            if fn in idx and tn in idx:
                mat[idx[fn], idx[tn]] += int(r["transition_count"])
    return mat


def pad_matrix(mat):
    """Ensure every action has a visible arc in the chord diagram."""
    total = mat.sum()
    ms = max(total * 0.03, 10) if total > 0 else 10
    for i in range(6):
        rsum = mat[i].sum()
        csum = mat[:, i].sum()
        if rsum == 0 and csum == 0:
            for j in range(6):
                if i != j:
                    mat[i, j] += ms / 10
                    mat[j, i] += ms / 10
            mat[i, i] += ms / 2
        elif rsum < ms:
            mat[i, i] += ms - rsum
    return mat


def build_matrix(rows, bench, model, harness, outcome):
    """Build padded 6x6 matrix for a single outcome."""
    return pad_matrix(build_matrix_raw(rows, bench, model, harness, outcome))


# ── main ─────────────────────────────────────────────────────────────────────

def make_plots():
    rows = load_transitions()
    print(f"Loaded {len(rows)} transition rows")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for bench in ["GPQA", "HLE", "AIMEPass4"]:
        for model in ["Qwen", "Gemma"]:
            for harness in HARNESSES:
                for outcome in ["success", "failure"]:
                    mat = build_matrix(rows, bench, model, harness, outcome)
                    total = int(mat.sum())
                    if total < 1:
                        continue

                    fig, ax = plt.subplots(figsize=(6, 6))
                    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
                    ax.set_aspect('equal')
                    nodePos = chordDiagram(mat, ax, colors_hex=HEX,
                                           width=0.18, pad=2, chordwidth=0.55)
                    ax.axis('off')

                    # Output: figures/chords/<Bench>_<Model>_<Harness>_<outcome>.pdf
                    bench_short = bench.replace("AIMEPass4", "AIME")
                    fname = OUT_DIR / f"{bench_short}_{model}_{harness}_{outcome}.pdf"
                    fig.savefig(fname, dpi=200, bbox_inches='tight',
                                pad_inches=0, facecolor='white')
                    plt.close(fig)

    # ── Aggregated (success + failure combined) ──────────────────────────────
    print("\nGenerating aggregated plots...")
    for bench in ["GPQA", "HLE", "AIMEPass4"]:
        for model in ["Qwen", "Gemma"]:
            for harness in HARNESSES:
                mat_s = build_matrix_raw(rows, bench, model, harness, "success")
                mat_f = build_matrix_raw(rows, bench, model, harness, "failure")
                mat_agg = pad_matrix(mat_s + mat_f)

                fig, ax = plt.subplots(figsize=(6, 6))
                fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
                ax.set_aspect('equal')
                chordDiagram(mat_agg, ax, colors_hex=HEX,
                             width=0.18, pad=2, chordwidth=0.55)
                ax.axis('off')

                bench_short = bench.replace("AIMEPass4", "AIME")
                fname = OUT_DIR / f"{bench_short}_{model}_{harness}_aggregated.pdf"
                fig.savefig(fname, dpi=200, bbox_inches='tight',
                            pad_inches=0, facecolor='white')
                plt.close(fig)

    # Count
    total = sum(1 for _ in OUT_DIR.rglob("*.pdf"))
    print(f"\nDone — {total} chord PDFs under {OUT_DIR}")


if __name__ == "__main__":
    make_plots()

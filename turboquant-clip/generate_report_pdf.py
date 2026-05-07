#!/usr/bin/env python3
"""Generate V1 vs V2 Compressor Evaluation Report PDF.

Produces report_v1_vs_v2.pdf with:
  - Abstract and setup summary
  - V1 Recall@10 table
  - V2 Recall@10 table
  - Side-by-side V1 vs V2 comparison table with colour-coded deltas
  - Bar-chart comparison plots per task
  - Recall@10 vs bits line plots per method

Usage:
    python generate_report_pdf.py
"""
import sys
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import FancyBboxPatch, Rectangle

ROOT = Path(__file__).parent

# ── Load freshly computed results from generate_tables.py ─────────────────────
import json as _json

def _load_results():
    path = ROOT / "results_fresh.json"
    if not path.exists():
        raise FileNotFoundError(
            "results_fresh.json not found. Run `python generate_tables.py` first."
        )
    raw = _json.loads(path.read_text())

    def _parse(d):
        out = {}
        for key, vals in d.items():
            parts = key.rsplit("_", 1)
            method, bits = parts[0], int(parts[1])
            out[(method, bits)] = [round(v, 4) if v is not None else None for v in vals]
        return out

    v1     = _parse(raw["v1"])
    v2     = _parse(raw["v2"])
    unc_v1 = _parse(raw.get("unc_v1", {}))
    unc_v2 = _parse(raw.get("unc_v2", {}))
    return v1, v2, unc_v1, unc_v2

_V1_RAW, _V2_RAW, _UNC_V1_RAW, _UNC_V2_RAW = _load_results()

# ── Colour palette (matching reports_v2.pdf aesthetic) ───────────────────────
C = dict(
    navy    = "#1E3A5F",
    blue    = "#2563EB",
    green   = "#15803D",
    red     = "#B91C1C",
    amber   = "#B45309",
    purple  = "#6D28D9",
    gray    = "#6B7280",
    lgray   = "#F3F4F6",
    dgray   = "#374151",
    white   = "#FFFFFF",
    black   = "#111827",
    pos_bg  = "#DCFCE7",   # light green for positive delta
    neg_bg  = "#FEE2E2",   # light red for negative delta
    hdr_bg  = "#1E3A5F",   # table header background
)

METHOD_COLORS = {
    "faiss_pq":    C["blue"],
    "qjl":         C["green"],
    "polarquant":  C["amber"],
    "turboquant":  C["red"],
    "uncompressed": C["dgray"],
}
METHOD_LABELS = {
    "uncompressed": "Uncompressed",
    "faiss_pq":    "FAISS-PQ",
    "qjl":         "QJL",
    "polarquant":  "PolarQuant",
    "turboquant":  "TurboQuant",
}

TASKS       = ["T1_text2image", "T2_image2text", "T3_text2text", "T4_image2image"]
TASK_SHORT  = ["T1", "T2", "T3", "T4"]
TASK_LABELS = ["T1\ntxt→img", "T2\nimg→txt", "T3\ntxt→txt", "T4\nimg→img"]
TASK_FULL   = ["T1 txt→img", "T2 img→txt", "T3 txt→txt", "T4 img→img"]

# ── Results: freshly computed by generate_tables.py (all seeds 0-4, no cache) ─
V1     = _V1_RAW
V2     = _V2_RAW
UNC_V1 = _UNC_V1_RAW   # recall@10 vs uncompressed top-10 (approximation quality)
UNC_V2 = _UNC_V2_RAW

# Table row order: (label, method, bits, notes)
TABLE_ROWS = [
    ("Uncompressed",  "uncompressed", 32, ""),
    ("FAISS-PQ",      "faiss_pq",     1,  ""),
    ("FAISS-PQ",      "faiss_pq",     2,  ""),
    ("FAISS-PQ",      "faiss_pq",     4,  ""),
    ("QJL",           "qjl",          1,  ""),
    ("QJL",           "qjl",          2,  ""),
    ("QJL",           "qjl",          4,  ""),
    ("PolarQuant",    "polarquant",   1,  ""),
    ("PolarQuant",    "polarquant",   2,  ""),
    ("PolarQuant",    "polarquant",   4,  ""),
    ("TurboQuant",    "turboquant",   1,  ""),
    ("TurboQuant",    "turboquant",   2,  ""),
    ("TurboQuant",    "turboquant",   4,  ""),
]

# Rows for comparison table (skip uncompressed/faiss_pq — unchanged)
COMP_ROWS = [r for r in TABLE_ROWS if r[1] not in ("uncompressed", "faiss_pq")]


# ── Helpers ──────────────────────────────────────────────────────────────────

def mean4(vals):
    return sum(vals) / 4


def best_per_col(data, rows):
    """Return the best V1 score for each (bits, task_index) among compressed methods."""
    bests = {}
    for label, method, bits, _ in rows:
        if method == "uncompressed":
            continue
        key = (method, bits)
        if key not in data:
            continue
        vals = data[key]
        for i, v in enumerate(vals):
            bests[(bits, i)] = max(bests.get((bits, i), 0), v)
    return bests


def draw_title_bar(ax, text, y, fontsize=13, color=C["navy"]):
    ax.text(0.0, y, text, transform=ax.transAxes,
            fontsize=fontsize, fontweight="bold", color=color,
            va="top", ha="left")


def setup_text_ax(fig, rect):
    """Add a bare axes (no ticks/spines) for text layout."""
    ax = fig.add_axes(rect)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    return ax


# ── Table drawing ─────────────────────────────────────────────────────────────

def draw_recall_table(ax, data, rows, title, footnote=""):
    """Draw a styled Recall@10 table on ax (no ticks, pure annotation)."""
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    # Section title
    ax.text(0.0, 0.99, title, fontsize=11, fontweight="bold",
            color=C["navy"], va="top", transform=ax.transAxes)
    ax.text(0.0, 0.955, "Recall@10 — mean over 5 seeds, 1 000 queries per task",
            fontsize=8, color=C["gray"], va="top", transform=ax.transAxes)

    col_x  = [0.00, 0.22, 0.31, 0.45, 0.58, 0.71, 0.84]
    col_lbl= ["Method", "Bits", "T1 txt→img", "T2 img→txt", "T3 txt→txt", "T4 img→img", "Mean"]
    header_y  = 0.905
    row_h     = 0.060
    text_pad  = 0.007

    # Header background
    ax.add_patch(Rectangle((0, header_y - 0.003), 1.0, row_h + 0.006,
                            facecolor=C["hdr_bg"], transform=ax.transAxes,
                            clip_on=False, zorder=1))
    for cx, cl in zip(col_x, col_lbl):
        ax.text(cx + 0.005, header_y + row_h / 2, cl,
                fontsize=8.5, fontweight="bold", color=C["white"],
                va="center", ha="left", transform=ax.transAxes)

    best = best_per_col(data, rows)

    # Group-separator tracking
    prev_method = None
    row_y = header_y - row_h

    for r_idx, (label, method, bits, _) in enumerate(rows):
        key = (method, bits)
        vals = data.get(key)
        if vals is None:
            continue

        # Thin separator line between method groups
        if prev_method and method != prev_method:
            ax.axhline(row_y + row_h, xmin=0, xmax=1,
                       color="#CBD5E1", linewidth=0.6)
        prev_method = method

        bg = C["lgray"] if r_idx % 2 == 0 else C["white"]
        ax.add_patch(Rectangle((0, row_y - 0.001), 1.0, row_h + 0.001,
                                facecolor=bg, transform=ax.transAxes,
                                clip_on=False))

        # Method label
        ax.text(col_x[0] + 0.005, row_y + row_h / 2, label,
                fontsize=8, color=C["black"], va="center", ha="left",
                transform=ax.transAxes,
                fontweight="bold" if method == "uncompressed" else "normal")

        # Bits
        bits_txt = str(bits) if bits != 32 else "32 (ref)"
        ax.text(col_x[1] + 0.005, row_y + row_h / 2, bits_txt,
                fontsize=8, color=C["black"], va="center", ha="left",
                transform=ax.transAxes)

        mean_val = mean4(vals)

        for ci, v in enumerate(vals):
            is_best = abs(v - best.get((bits, ci), -1)) < 1e-6 and method != "uncompressed"
            ax.text(col_x[ci + 2] + 0.005, row_y + row_h / 2, f"{v:.3f}",
                    fontsize=8,
                    color=C["green"] if is_best else C["black"],
                    fontweight="bold" if is_best else "normal",
                    va="center", ha="left", transform=ax.transAxes)

        ax.text(col_x[6] + 0.005, row_y + row_h / 2, f"{mean_val:.3f}",
                fontsize=8, color=C["navy"], fontweight="bold",
                va="center", ha="left", transform=ax.transAxes)

        row_y -= row_h

    # Bottom border
    ax.axhline(row_y + row_h, xmin=0, xmax=1,
               color=C["hdr_bg"], linewidth=1.2)

    if footnote:
        ax.text(0.0, row_y - 0.01, footnote, fontsize=7.5,
                color=C["gray"], style="italic", va="top", transform=ax.transAxes)


def draw_comparison_table(ax, v1, v2, rows, title, footnote=None):
    """Side-by-side V1 | V2 | Δ comparison table."""
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    ax.text(0.0, 0.99, title, fontsize=11, fontweight="bold",
            color=C["navy"], va="top", transform=ax.transAxes)
    ax.text(0.0, 0.955, "Recall@10 — V1 | V2 | Δ = V2 − V1   (green = V2 better, red = V2 worse)",
            fontsize=8, color=C["gray"], va="top", transform=ax.transAxes)

    # Columns: Method(0.17), Bits(0.07), [T1 V1,V2,Δ], [T2…], [T3…], [T4…], MeanΔ(0.08)
    # Each task group = 0.17 wide (3 sub-cols of 0.0567 each)
    col_method = 0.00
    col_bits   = 0.17
    sub_w      = 0.057          # width of each V1 / V2 / Δ sub-column
    task_w     = sub_w * 3      # = 0.171 per task group
    task_starts = [0.24 + i * task_w for i in range(4)]   # [0.24, 0.411, 0.582, 0.753]
    mean_x     = task_starts[3] + task_w + 0.005           # start of Mean Δ label
    row_h      = 0.058
    header_y   = 0.905

    # Header background
    ax.add_patch(Rectangle((0, header_y - 0.003), 1.0, row_h + 0.006,
                            facecolor=C["hdr_bg"], transform=ax.transAxes,
                            clip_on=False, zorder=1))

    ax.text(col_method + 0.005, header_y + row_h / 2, "Method",
            fontsize=7.5, fontweight="bold", color=C["white"],
            va="center", ha="left", transform=ax.transAxes)
    ax.text(col_bits + 0.005, header_y + row_h / 2, "Bits",
            fontsize=7.5, fontweight="bold", color=C["white"],
            va="center", ha="left", transform=ax.transAxes)

    for ts, tl in zip(task_starts, ["T1 txt→img", "T2 img→txt", "T3 txt→txt", "T4 img→img"]):
        ax.text(ts + sub_w * 1.5 - 0.005, header_y + row_h * 0.82, tl,
                fontsize=6.5, fontweight="bold", color=C["white"],
                va="center", ha="center", transform=ax.transAxes)
        # sub-headers
        for si, sl in enumerate(["V1", "V2", "Δ"]):
            ax.text(ts + si * sub_w + sub_w / 2, header_y + row_h * 0.35, sl,
                    fontsize=6.5, fontweight="bold", color="#CBD5E1",
                    va="center", ha="center", transform=ax.transAxes)

    ax.text(mean_x, header_y + row_h / 2, "Mean Δ",
            fontsize=7, fontweight="bold", color=C["white"],
            va="center", ha="left", transform=ax.transAxes)

    prev_method = None
    row_y = header_y - row_h

    for r_idx, (label, method, bits, _) in enumerate(rows):
        key = (method, bits)
        v1_vals = v1.get(key)
        v2_vals = v2.get(key)
        if v1_vals is None or v2_vals is None:
            continue

        if prev_method and method != prev_method:
            ax.axhline(row_y + row_h, xmin=0, xmax=1,
                       color="#CBD5E1", linewidth=0.6)
        prev_method = method

        bg = C["lgray"] if r_idx % 2 == 0 else C["white"]
        ax.add_patch(Rectangle((0, row_y - 0.001), 1.0, row_h + 0.001,
                                facecolor=bg, transform=ax.transAxes, clip_on=False))

        ax.text(col_method + 0.005, row_y + row_h / 2, label,
                fontsize=7.5, color=C["black"], va="center", ha="left",
                transform=ax.transAxes)
        ax.text(col_bits + 0.005, row_y + row_h / 2, str(bits),
                fontsize=7.5, color=C["black"], va="center", ha="left",
                transform=ax.transAxes)

        deltas = []
        for ti, (ts) in enumerate(task_starts):
            v1v = v1_vals[ti]
            v2v = v2_vals[ti]
            d   = v2v - v1v
            deltas.append(d)

            ax.text(ts + sub_w * 0.5, row_y + row_h / 2, f"{v1v:.3f}",
                    fontsize=7, color=C["gray"], va="center", ha="center",
                    transform=ax.transAxes)
            ax.text(ts + sub_w * 1.5, row_y + row_h / 2, f"{v2v:.3f}",
                    fontsize=7, color=C["black"], va="center", ha="center",
                    transform=ax.transAxes)

            d_color = C["green"] if d > 0.002 else (C["red"] if d < -0.002 else C["gray"])
            d_bg    = C["pos_bg"] if d > 0.002 else (C["neg_bg"] if d < -0.002 else bg)
            ax.add_patch(Rectangle((ts + 2 * sub_w, row_y), sub_w - 0.002, row_h - 0.002,
                                   facecolor=d_bg, transform=ax.transAxes, clip_on=False))
            ax.text(ts + sub_w * 2.5, row_y + row_h / 2, f"{d:+.3f}",
                    fontsize=7, fontweight="bold", color=d_color,
                    va="center", ha="center", transform=ax.transAxes)

        mean_d = sum(deltas) / len(deltas)
        md_color = C["green"] if mean_d > 0.002 else (C["red"] if mean_d < -0.002 else C["gray"])
        ax.text(mean_x, row_y + row_h / 2, f"{mean_d:+.3f}",
                fontsize=7.5, fontweight="bold", color=md_color,
                va="center", ha="left", transform=ax.transAxes)

        row_y -= row_h

    ax.axhline(row_y + row_h, xmin=0, xmax=1,
               color=C["hdr_bg"], linewidth=1.2)

    foot = footnote if footnote is not None else (
        "† QJL V2 uses SRHT which caps at m = d = 512 projections; "
        "bits=2,4 are structurally identical to bits=1."
    )
    ax.text(0.0, row_y - 0.015, foot,
            fontsize=7, color=C["gray"], style="italic",
            va="top", transform=ax.transAxes)


# ── Plot helpers ──────────────────────────────────────────────────────────────

def make_bar_comparison(ax, v1, v2, method, bits_list, task_idx, task_label):
    """Grouped bars: V1 vs V2 for one task across bit-widths."""
    x     = np.arange(len(bits_list))
    width = 0.35
    col   = METHOD_COLORS.get(method, C["gray"])

    v1_vals = [v1.get((method, b), [0, 0, 0, 0])[task_idx] for b in bits_list]
    v2_vals = [v2.get((method, b), [0, 0, 0, 0])[task_idx] for b in bits_list]

    ax.bar(x - width / 2, v1_vals, width, label="V1",
           color=col, alpha=0.45, edgecolor=col, linewidth=1)
    ax.bar(x + width / 2, v2_vals, width, label="V2",
           color=col, alpha=0.9, edgecolor=col, linewidth=1)

    # Value labels
    for xi, (v1v, v2v) in enumerate(zip(v1_vals, v2_vals)):
        ax.text(xi - width / 2, v1v + 0.008, f"{v1v:.2f}", ha="center",
                va="bottom", fontsize=6.5, color=C["gray"])
        ax.text(xi + width / 2, v2v + 0.008, f"{v2v:.2f}", ha="center",
                va="bottom", fontsize=6.5, color=C["black"], fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels([f"{b} bit" for b in bits_list], fontsize=8)
    ax.set_title(f"{task_label}", fontsize=9, fontweight="bold", color=C["navy"])
    ax.set_ylim(0, 1.12)
    ax.set_ylabel("Recall@10", fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def make_line_plot(ax, v1, v2, task_idx, task_label):
    """Recall@10 vs bits for V1 (dashed) and V2 (solid), one line per method."""
    methods_bits = {
        "qjl":        [1, 2, 4],
        "polarquant": [1, 2, 4],
        "turboquant": [1, 2, 4],
        "faiss_pq":   [1, 2, 4],
    }
    for method, bits_list in methods_bits.items():
        col = METHOD_COLORS.get(method, C["gray"])
        lbl = METHOD_LABELS.get(method, method)

        v1_vals = [v1.get((method, b), [None]*4)[task_idx] for b in bits_list]
        v2_vals = [v2.get((method, b), [None]*4)[task_idx] for b in bits_list]

        ax.plot(bits_list, v1_vals, color=col, linestyle="--",
                marker="x", markersize=5, linewidth=1.2,
                label=f"{lbl} V1", alpha=0.6)
        ax.plot(bits_list, v2_vals, color=col, linestyle="-",
                marker="o", markersize=5, linewidth=1.8,
                label=f"{lbl} V2")

    # Uncompressed reference
    unc = v1.get(("uncompressed", 32), [None]*4)[task_idx]
    if unc is not None:
        ax.axhline(unc, color=C["dgray"], linestyle=":", linewidth=1.2,
                   label="Uncompressed")

    ax.set_title(task_label, fontsize=9, fontweight="bold", color=C["navy"])
    ax.set_xlabel("Bits / dim", fontsize=8)
    ax.set_ylabel("Recall@10", fontsize=8)
    ax.set_xticks([1, 2, 4])
    ax.set_xticklabels(["1", "2", "4"], fontsize=8)
    ax.set_ylim(0, 1.08)
    ax.grid(alpha=0.25)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


# ── Page generators ───────────────────────────────────────────────────────────

def page_cover(pdf):
    fig = plt.figure(figsize=(8.5, 11))

    # Title stripe
    ax_stripe = fig.add_axes([0, 0.82, 1, 0.18])
    ax_stripe.set_xlim(0, 1); ax_stripe.set_ylim(0, 1)
    ax_stripe.axis("off")
    ax_stripe.add_patch(Rectangle((0, 0), 1, 1, facecolor=C["navy"],
                                   transform=ax_stripe.transAxes))
    ax_stripe.text(0.5, 0.64,
                   "V1 vs V2 Compressor Evaluation",
                   fontsize=20, fontweight="bold", color=C["white"],
                   ha="center", va="center", transform=ax_stripe.transAxes)
    ax_stripe.text(0.5, 0.30,
                   "Flickr30k · CLIP ViT-B/32 · 5 seeds · 1 000 queries/task",
                   fontsize=11, color="#93C5FD",
                   ha="center", va="center", transform=ax_stripe.transAxes)

    ax = setup_text_ax(fig, [0.07, 0.02, 0.86, 0.78])

    def txt(x, y, text, **kw):
        ax.text(x, y, text, transform=ax.transAxes, va="top", **kw)

    def hline(y):
        ax.axhline(y, xmin=0, xmax=1, color="#CBD5E1", linewidth=0.8)

    # ── Abstract ─────────────────────────────────────────────────────────────
    txt(0, 0.99, "Abstract", fontsize=11, fontweight="bold", color=C["navy"])
    txt(0, 0.955,
        "We re-evaluate four post-training embedding compressors — QJL, PolarQuant,\n"
        "TurboQuant, and FAISS-PQ — on CLIP ViT-B/32 embeddings of Flickr30k under\n"
        "two algorithm generations. V1 compressors follow the baseline implementations\n"
        "from the original report (plain-Gaussian QJL, empirical Lloyd-Max PolarQuant,\n"
        "and their TurboQuant combination). V2 compressors introduce three targeted\n"
        "improvements: SRHT preconditioning in QJL, analytically derived data-free\n"
        "codebooks in PolarQuant, and corrected seed isolation and cosine-mode handling\n"
        "in TurboQuant.",
        fontsize=9, color=C["black"])
    txt(0, 0.730,
        "V2 PolarQuant is the headline improvement: at 1 bit/dim it recovers 0.317\n"
        "T1 Recall@10 vs 0.131 for V1 (+142% relative). V2 TurboQuant eliminates the\n"
        "catastrophic interference seen in V1 (0.157 → 0.321 on T1 at 2 bits, +104%).\n"
        "V2 QJL improves at 1 bit/dim (0.154 → 0.233) but is structurally capped there\n"
        "due to the Hadamard dimension limit. FAISS-PQ and uncompressed are unchanged.",
        fontsize=9, color=C["black"])
    hline(0.585)

    # ── Setup ─────────────────────────────────────────────────────────────────
    txt(0, 0.567, "Setup", fontsize=11, fontweight="bold", color=C["navy"])
    txt(0, 0.530,
        "Dataset:      Flickr30k (nlphuji/flickr30k) — 31 014 images, 155 070 captions.\n"
        "Embeddings:   CLIP ViT-B/32 (openai/clip-vit-base-patch32), d = 512, L2-normalised.\n"
        "Evaluation:   4 retrieval tasks (T1 txt→img, T2 img→txt, T3 txt→txt, T4 img→img)\n"
        "              with Recall@{1, 5, 10} over 1 000 randomly sampled queries per task.\n"
        "Seeds:        5 seeds (0–4) per (method, bits) cell; means reported.\n"
        "Bit widths:   1, 2, 4 bits/dim for all analytic methods; 1, 2, 4 for FAISS-PQ.",
        fontsize=9, color=C["black"])
    hline(0.355)

    # ── Compressor summary ────────────────────────────────────────────────────
    txt(0, 0.337, "Compressor Changes: V1 → V2",
        fontsize=11, fontweight="bold", color=C["navy"])
    txt(0, 0.300,
        "QJL V1:        Plain Gaussian projection matrix S ∈ ℝ^{m×d};  O(md) encoding.\n"
        "QJL V2:        SRHT (D·H subsampling);  O(d log d) encoding;  m capped at d=512.\n\n"
        "PolarQuant V1: Empirical Lloyd-Max codebooks fitted per level on training data.\n"
        "PolarQuant V2: Analytical data-free codebooks from theoretical polar PDF;\n"
        "               variable bit-width schedule; domain fix for inner-level angles.\n\n"
        "TurboQuant V1: Uses V1 QJL/PolarQuant; seed offset (+10 000) for independence.\n"
        "TurboQuant V2: Uses V2 QJL/PolarQuant; deterministic child-RNG seed split;\n"
        "               cosine=False enforced on QJL (residuals are not unit-norm).\n\n"
        "FAISS-PQ:      Unchanged (IndexPQ, 8-dim subspaces, nbits=8).",
        fontsize=9, color=C["black"])

    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def page_table(pdf, data, title, footnote=""):
    fig = plt.figure(figsize=(8.5, 11))
    ax  = setup_text_ax(fig, [0.05, 0.05, 0.90, 0.90])
    draw_recall_table(ax, data, TABLE_ROWS, title, footnote)
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def page_comparison_table(pdf):
    fig = plt.figure(figsize=(8.5, 11))
    ax  = setup_text_ax(fig, [0.03, 0.05, 0.94, 0.90])
    draw_comparison_table(ax, V1, V2, COMP_ROWS,
                          "Side-by-Side Comparison — V1 vs V2 (Recall@10)")
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def page_bar_charts(pdf):
    """4-task bar charts for QJL, PolarQuant, TurboQuant."""
    methods = ["qjl", "polarquant", "turboquant"]
    bits    = [1, 2, 4]

    for method in methods:
        fig = plt.figure(figsize=(8.5, 11))

        # Page title
        ax_title = fig.add_axes([0.05, 0.93, 0.90, 0.06])
        ax_title.axis("off")
        col = METHOD_COLORS.get(method, C["navy"])
        ax_title.text(0.0, 0.5,
                      f"{METHOD_LABELS[method]}:  V1 vs V2 — Recall@10 by Task",
                      fontsize=13, fontweight="bold", color=col,
                      va="center", transform=ax_title.transAxes)

        # Legend patch
        import matplotlib.patches as mpatches
        leg_ax = fig.add_axes([0.70, 0.91, 0.25, 0.06])
        leg_ax.axis("off")
        p1 = mpatches.Patch(facecolor=col, alpha=0.45, label="V1")
        p2 = mpatches.Patch(facecolor=col, alpha=0.9,  label="V2")
        leg_ax.legend(handles=[p1, p2], loc="center", fontsize=9,
                      frameon=True, ncol=2)

        for ti, (task_key, task_label) in enumerate(zip(TASKS, TASK_LABELS)):
            row = ti // 2
            col_i = ti % 2
            ax = fig.add_axes([0.08 + col_i * 0.47, 0.57 - row * 0.38, 0.40, 0.30])
            make_bar_comparison(ax, V1, V2, method, bits, ti, task_label)
            if ti > 1:
                ax.set_xlabel("Bits / dim", fontsize=8)

        # Findings annotation box — sits clearly below plots
        ax_note = fig.add_axes([0.05, 0.06, 0.90, 0.13])
        ax_note.axis("off")
        findings = {
            "qjl": (
                "QJL V2 (SRHT) improves significantly at 1 bit/dim (+0.079 T1, +0.027 T4). "
                "At 2 and 4 bits the SRHT dimension cap (m ≤ d = 512) means V2 is structurally "
                "identical to 1-bit — a known limitation of the Hadamard approach."
            ),
            "polarquant": (
                "PolarQuant V2 (analytical codebooks) achieves large gains across all bit budgets. "
                "The 1-bit improvement is the most dramatic (+0.186 T1, +0.129 T4), closing "
                "more than half the gap to the 2-bit V1 baseline."
            ),
            "turboquant": (
                "TurboQuant V2 reverses its catastrophic V1 deficit. At 2 bits, T1 jumps from 0.157 "
                "to 0.321 (+0.163) and T4 from 0.686 to 0.976 (+0.290) — confirming the V1 failure "
                "was implementation-level, not algorithmic."
            ),
        }
        ax_note.axis("off")
        ax_note.set_xlim(0, 1); ax_note.set_ylim(0, 1)
        ax_note.add_patch(Rectangle((0, 0), 1, 1,
                                    facecolor="#F0F4FF", edgecolor="#CBD5E1",
                                    linewidth=0.8, transform=ax_note.transAxes))
        ax_note.text(0.015, 0.87, "Key finding:",
                     fontsize=8.5, fontweight="bold", color=C["navy"],
                     va="top", transform=ax_note.transAxes)
        ax_note.text(0.015, 0.55, findings.get(method, ""),
                     fontsize=8, color=C["black"],
                     va="top", transform=ax_note.transAxes)

        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)


def page_line_plots(pdf):
    """Recall@10 vs bits for V1 (dashed) vs V2 (solid), one plot per task."""
    fig = plt.figure(figsize=(8.5, 11))

    # Page title
    ax_title = fig.add_axes([0.05, 0.93, 0.90, 0.05])
    ax_title.axis("off")
    ax_title.text(0.0, 0.5, "Recall@10 vs Bits/dim — V1 (dashed ×) vs V2 (solid ●)",
                  fontsize=12, fontweight="bold", color=C["navy"],
                  va="center", transform=ax_title.transAxes)

    for ti, (task_key, task_label) in enumerate(zip(TASKS, TASK_LABELS)):
        row   = ti // 2
        col_i = ti % 2
        ax = fig.add_axes([0.08 + col_i * 0.47, 0.55 - row * 0.40, 0.40, 0.33])
        make_line_plot(ax, V1, V2, ti, task_label)
        if ti == 1:
            handles, labels = ax.get_legend_handles_labels()
            ax.legend(handles, labels, fontsize=6.5, loc="lower right",
                      ncol=2, framealpha=0.9)

    # Cross-modal vs same-modal note
    ax_note = fig.add_axes([0.05, 0.03, 0.90, 0.10])
    ax_note.axis("off")
    ax_note.text(0.0, 0.95,
                 "Cross-modal tasks (T1, T2) suffer far more from compression than same-modal tasks "
                 "(T3, T4) — consistent with the V1 report's H1 finding. The V2 improvements are "
                 "largest on cross-modal tasks, particularly for PolarQuant and TurboQuant at low "
                 "bit budgets where the original implementations broke down.",
                 fontsize=8, color=C["black"], va="top",
                 transform=ax_note.transAxes, wrap=True)

    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def page_mean_comparison(pdf):
    """Summary bar chart: mean Recall@10 across all 4 tasks, V1 vs V2."""
    fig = plt.figure(figsize=(8.5, 11))

    ax_title = fig.add_axes([0.05, 0.90, 0.90, 0.08])
    ax_title.axis("off")
    ax_title.text(0.0, 0.7,
                  "Summary: Mean Recall@10 Across All 4 Tasks",
                  fontsize=13, fontweight="bold", color=C["navy"],
                  va="center", transform=ax_title.transAxes)
    ax_title.text(0.0, 0.2,
                  "V1 (light, hatched) vs V2 (solid)  ·  mean over 5 seeds",
                  fontsize=9, color=C["gray"], va="center",
                  transform=ax_title.transAxes)

    methods_bits = [
        ("QJL",        "qjl",        [1, 2, 4]),
        ("PolarQuant", "polarquant", [1, 2, 4]),
        ("TurboQuant", "turboquant", [1, 2, 4]),
        ("FAISS-PQ",   "faiss_pq",   [1, 2, 4]),
    ]

    ax = fig.add_axes([0.07, 0.35, 0.88, 0.52])

    total_groups  = sum(len(bl) for _, _, bl in methods_bits)
    group_gap     = 0.4
    bar_w         = 0.32
    xs = []
    x_cursor = 0
    labels   = []
    tick_positions = []

    for m_label, method, bits_list in methods_bits:
        col = METHOD_COLORS.get(method, C["gray"])
        group_center = x_cursor + (len(bits_list) - 1) * (bar_w * 2 + 0.1) / 2

        for b in bits_list:
            v1_mean = mean4(V1.get((method, b), [0]*4))
            v2_mean = mean4(V2.get((method, b), [0]*4))

            ax.bar(x_cursor - bar_w / 2, v1_mean, bar_w,
                   color=col, alpha=0.4, edgecolor=col,
                   hatch="//", linewidth=0.8)
            ax.bar(x_cursor + bar_w / 2, v2_mean, bar_w,
                   color=col, alpha=0.9, edgecolor=col, linewidth=0.8)

            ax.text(x_cursor, v2_mean + 0.01, f"{v2_mean:.3f}",
                    ha="center", va="bottom", fontsize=6.5,
                    color=col, fontweight="bold")
            ax.text(x_cursor, v1_mean + 0.01, f"{v1_mean:.3f}",
                    ha="center", va="bottom", fontsize=6, color=col, alpha=0.6)

            tick_positions.append(x_cursor)
            labels.append(f"{b}b")
            x_cursor += bar_w * 2 + 0.1

        # Method group label
        ax.text(group_center, -0.10, m_label, ha="center", va="top",
                fontsize=9, fontweight="bold", color=col,
                transform=ax.get_xaxis_transform())

        x_cursor += group_gap

    # Uncompressed reference
    unc_mean = mean4(V1.get(("uncompressed", 32), [0]*4))
    ax.axhline(unc_mean, color=C["dgray"], linestyle=":", linewidth=1.5,
               label=f"Uncompressed ({unc_mean:.3f})")

    ax.set_xticks(tick_positions)
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel("Mean Recall@10 (4 tasks)", fontsize=9)
    ax.set_ylim(0, 1.12)
    ax.grid(axis="y", alpha=0.3)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    import matplotlib.patches as mpatches
    p1 = mpatches.Patch(facecolor=C["gray"], alpha=0.4, hatch="//", label="V1")
    p2 = mpatches.Patch(facecolor=C["gray"], alpha=0.9, label="V2")
    p3 = plt.Line2D([0], [0], color=C["dgray"], linestyle=":",
                    label=f"Uncompressed ({unc_mean:.3f})")
    ax.legend(handles=[p1, p2, p3], fontsize=8, loc="lower right")

    # Bottom findings
    ax_note = fig.add_axes([0.05, 0.04, 0.90, 0.28])
    ax_note.axis("off")
    findings = [
        ("PolarQuant V2", C["amber"],
         "Largest absolute gains at 1-bit (+0.118 mean). "
         "At 4 bits, near-identical to FAISS-PQ on all tasks."),
        ("TurboQuant V2", C["red"],
         "Recovers fully from its V1 catastrophic failure. "
         "2-bit mean recall: 0.387 → 0.584 (+51% relative). "
         "Now correctly ranks above its QJL component."),
        ("QJL V2", C["green"],
         "1-bit improvement (+0.059 mean). "
         "Flat at higher bits due to SRHT dimension cap — "
         "choose PolarQuant if you need > 1 bit/dim."),
        ("FAISS-PQ", C["blue"],
         "Unchanged — remains the Pareto frontier at very low byte budgets."),
    ]
    y = 0.96
    for label, col, text in findings:
        ax_note.text(0.0, y, f"▶ {label}:", fontsize=8.5, fontweight="bold",
                     color=col, va="top", transform=ax_note.transAxes)
        ax_note.text(0.17, y, text, fontsize=8.5, color=C["black"],
                     va="top", transform=ax_note.transAxes)
        y -= 0.23

    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


# ── Approximation-quality pages ───────────────────────────────────────────────

def page_approx_quality_table(pdf):
    """Side-by-side V1 | V2 | Δ of Recall@10 vs Uncompressed top-10."""
    fig = plt.figure(figsize=(8.5, 11))
    ax  = setup_text_ax(fig, [0.03, 0.05, 0.94, 0.90])
    draw_comparison_table(
        ax, UNC_V1, UNC_V2, COMP_ROWS,
        "Approximation Quality — Recall@10 vs Uncompressed (V1 vs V2)",
        footnote=(
            "Metric: fraction of the exact (uncompressed) top-10 results recovered by each "
            "compressed method. 1.000 = identical to exact search; independent of true labels. "
            "† QJL V2 SRHT cap: bits=2,4 are structurally identical to bits=1."
        ),
    )
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def page_retention_lines(pdf):
    """Retention % = method_recall / uncompressed_recall, vs bits/dim per task."""
    fig = plt.figure(figsize=(8.5, 11))

    ax_title = fig.add_axes([0.05, 0.93, 0.90, 0.05])
    ax_title.axis("off")
    ax_title.text(0.0, 0.5,
                  "Relative Recall (% of Uncompressed) vs Bits/dim",
                  fontsize=12, fontweight="bold", color=C["navy"],
                  va="center", transform=ax_title.transAxes)
    ax_title.text(0.0, 0.0,
                  "V1 dashed ×  ·  V2 solid ●  ·  100 % line = uncompressed baseline",
                  fontsize=9, color=C["gray"], va="center",
                  transform=ax_title.transAxes)

    unc_ref = V1.get(("uncompressed", 32), [0.498, 0.741, 0.528, 1.0])
    methods_bits = {
        "qjl":        [1, 2, 4],
        "polarquant": [1, 2, 4],
        "turboquant": [1, 2, 4],
        "faiss_pq":   [1, 2, 4],
    }

    for ti, (task_key, task_label) in enumerate(zip(TASKS, TASK_LABELS)):
        row   = ti // 2
        col_i = ti % 2
        ax = fig.add_axes([0.08 + col_i * 0.47, 0.55 - row * 0.40, 0.40, 0.33])

        unc = unc_ref[ti]
        for method, bits_list in methods_bits.items():
            col = METHOD_COLORS.get(method, C["gray"])
            lbl = METHOD_LABELS.get(method, method)

            v1_pct = [100 * (V1.get((method, b), [0]*4)[ti] / unc) for b in bits_list]
            v2_pct = [100 * (V2.get((method, b), [0]*4)[ti] / unc) for b in bits_list]

            ax.plot(bits_list, v1_pct, color=col, linestyle="--",
                    marker="x", markersize=5, linewidth=1.2,
                    label=f"{lbl} V1", alpha=0.6)
            ax.plot(bits_list, v2_pct, color=col, linestyle="-",
                    marker="o", markersize=5, linewidth=1.8,
                    label=f"{lbl} V2")

        ax.axhline(100, color=C["dgray"], linestyle=":", linewidth=1.2,
                   label="Uncompressed (100%)")

        ax.set_title(task_label, fontsize=9, fontweight="bold", color=C["navy"])
        ax.set_xlabel("Bits / dim", fontsize=8)
        ax.set_ylabel("Recall retention (%)", fontsize=8)
        ax.set_xticks([1, 2, 4])
        ax.set_xticklabels(["1", "2", "4"], fontsize=8)
        ax.set_ylim(0, 115)
        ax.grid(alpha=0.25)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

        if ti == 1:
            handles, labels = ax.get_legend_handles_labels()
            ax.legend(handles, labels, fontsize=6.5, loc="lower right",
                      ncol=2, framealpha=0.9)

    ax_note = fig.add_axes([0.05, 0.03, 0.90, 0.10])
    ax_note.axis("off")
    ax_note.text(0.0, 0.95,
                 "T4 (img→img) uncompressed = 1.000, so retention % = raw recall × 100. "
                 "T1 (txt→img) is the hardest axis: even FAISS-PQ at 1 bit retains only ~68%. "
                 "V2 improvements are largest on cross-modal tasks (T1, T2) at low bit budgets, "
                 "where V1 TurboQuant dropped to ~31% retention and V2 recovers to ~65%.",
                 fontsize=8, color=C["black"], va="top",
                 transform=ax_note.transAxes, wrap=True)

    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    out = ROOT / "report_v1_vs_v2.pdf"
    print(f"Generating {out} ...")

    plt.rcParams.update({
        "font.family":      "DejaVu Sans",
        "font.size":        9,
        "axes.titlesize":   10,
        "axes.labelsize":   9,
        "xtick.labelsize":  8,
        "ytick.labelsize":  8,
        "figure.dpi":       150,
    })

    with PdfPages(out) as pdf:
        # Page metadata
        d = pdf.infodict()
        d["Title"]   = "V1 vs V2 Compressor Evaluation — Flickr30k"
        d["Author"]  = "generate_report_pdf.py"
        d["Subject"] = "CLIP embedding compression comparison"

        page_cover(pdf)
        page_table(pdf, V1,
                   "Table 1.  V1 Compressors — Recall@10",
                   "Bold green = best compressed result per column. Mean = average across T1–T4.")
        page_table(pdf, V2,
                   "Table 2.  V2 Compressors — Recall@10",
                   "† QJL V2 is structurally capped at 1 bit/dim (SRHT m ≤ d).")
        page_comparison_table(pdf)
        page_approx_quality_table(pdf)
        page_retention_lines(pdf)
        page_mean_comparison(pdf)
        page_bar_charts(pdf)
        page_line_plots(pdf)

    print(f"Done → {out}  ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()

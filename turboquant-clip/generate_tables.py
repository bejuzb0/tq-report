#!/usr/bin/env python3
"""Generate V1 vs V2 comparison tables (Table 1 format from the report).

Loads existing results from results_v1/ and results_v2/ for bits=2,3,4,
then runs the missing experiments (bits=1, FAISS-PQ, uncompressed) and
prints two formatted Recall@10 tables — one for each compressor generation.

Usage:
    python generate_tables.py
"""
import sys
import warnings
from pathlib import Path
from collections import defaultdict

import numpy as np

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# V1 compressor imports
from src.compressors.polarquant import PolarQuant as PolarQuantV1
from src.compressors.qjl import QJL as QJLV1
from src.compressors.turboquant import TurboQuant as TurboQuantV1

# V2 compressor imports
from src.compressors.polarquant import PolarQuant as PolarQuantV2
from src.compressors.qjl import QJL as QJLV2
from src.compressors.turboquant import TurboQuant as TurboQuantV2

# Shared (same in both versions)
from src.compressors.faiss_pq import FaissPQ
from src.compressors.uncompressed import Uncompressed

from src.eval.retrieval import evaluate_all_tasks

SEEDS = (0, 1, 2, 3, 4)
MAX_QUERIES = 1000
DATA_DIR = ROOT / "data"
TASKS = ["T1_text2image", "T2_image2text", "T3_text2text", "T4_image2image"]

# Table rows to display: (method_label, internal_method_name, bits_list)
TABLE_ROWS = [
    ("Uncompressed", "uncompressed", [32]),
    ("FAISS-PQ",     "faiss_pq",    [1, 2, 4]),
    ("QJL",          "qjl",         [1, 2, 4]),
    ("PolarQuant",   "polarquant",  [1, 2, 4]),
    ("TurboQuant",   "turboquant",  [1, 2, 4]),
]


# ---------------------------------------------------------------------------
# Load existing CSV results
# ---------------------------------------------------------------------------

def load_csv(path: Path) -> dict:
    """Returns dict: (method, bits, seed, task) -> recall@10."""
    import csv
    data = {}
    if not path.exists():
        return data
    with path.open() as f:
        for row in csv.DictReader(f):
            key = (row["method"], int(row["bits"]), int(row["seed"]), row["task"])
            data[key] = float(row["recall_at_10"])
    return data


# ---------------------------------------------------------------------------
# Run a single (method, bits, seed, version) combination
# ---------------------------------------------------------------------------

def _build_v1(method: str, d: int, bits: int, seed: int):
    if method == "uncompressed":
        return Uncompressed(d)
    if method == "qjl":
        return QJLV1(d, bits_per_dim=float(bits), seed=seed)
    if method == "polarquant":
        return PolarQuantV1(d, angle_bits=bits, seed=seed)
    if method == "turboquant":
        if bits == 1:
            return QJLV1(d, bits_per_dim=1.0, seed=seed)
        return TurboQuantV1(d, angle_bits=bits - 1, qjl_bits_per_dim=1.0, seed=seed)
    if method == "faiss_pq":
        return FaissPQ(d, bits_per_dim=bits, seed=seed)
    raise ValueError(method)


def _build_v2(method: str, d: int, bits: int, seed: int):
    if method == "uncompressed":
        return Uncompressed(d)
    if method == "qjl":
        return QJLV2(d, bits_per_dim=float(bits), seed=seed, cosine=True)
    if method == "polarquant":
        return PolarQuantV2(d, angle_bits=bits, seed=seed)
    if method == "turboquant":
        if bits == 1:
            return QJLV2(d, bits_per_dim=1.0, seed=seed, cosine=True)
        return TurboQuantV2(d, angle_bits=bits - 1, qjl_bits_per_dim=1.0, seed=seed)
    if method == "faiss_pq":
        return FaissPQ(d, bits_per_dim=bits, seed=seed)
    raise ValueError(method)


def run_one(builder, method, d, bits, seed, imgs, txts):
    comp = builder(method, d, bits, seed)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        results, unc_results = evaluate_all_tasks(
            comp, imgs, txts, max_queries=MAX_QUERIES, seed=seed, return_unc_recall=True
        )
    return (
        {r.task: r.recall[10] for r in results},
        {r.task: r.recall[10] for r in unc_results},
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"Loading embeddings from {DATA_DIR} ...")
    imgs = np.load(DATA_DIR / "image_embeddings.npy")
    txts = np.load(DATA_DIR / "text_embeddings.npy")
    d = imgs.shape[1]
    print(f"  images: {imgs.shape}  texts: {txts.shape}  d={d}")

    # Fresh compute — ignore all cached CSVs, recompute everything from scratch.
    v1_cache: dict = {}
    v2_cache: dict = {}
    shared_cache: dict = {}
    v1_unc_cache: dict = {}
    v2_unc_cache: dict = {}
    shared_unc_cache: dict = {}

    # --- Shared experiments (uncompressed, faiss_pq — identical for V1/V2) ---
    shared_methods = [("uncompressed", [32]), ("faiss_pq", [1, 2, 4])]
    for method, bits_list in shared_methods:
        for bits in bits_list:
            print(f"\nRunning {method} @ {bits} bits (shared)  seeds={list(SEEDS)}")
            for seed in SEEDS:
                print(f"  seed={seed} ...", end=" ", flush=True)
                recalls, unc_recalls = run_one(_build_v1, method, d, bits, seed, imgs, txts)
                for task, r10 in recalls.items():
                    shared_cache[(method, bits, seed, task)] = r10
                for task, r10 in unc_recalls.items():
                    shared_unc_cache[(method, bits, seed, task)] = r10
                print("done")

    # --- V1 compressors: recompute bits=1,2,4 fresh ---
    print("\n--- V1 compressors (fresh) ---")
    v1_compute = [("qjl", [1, 2, 4]), ("polarquant", [1, 2, 4]), ("turboquant", [1, 2, 4])]
    for method, bits_list in v1_compute:
        for bits in bits_list:
            print(f"\nRunning V1 {method} @ {bits} bits  seeds={list(SEEDS)}")
            for seed in SEEDS:
                print(f"  seed={seed} ...", end=" ", flush=True)
                recalls, unc_recalls = run_one(_build_v1, method, d, bits, seed, imgs, txts)
                for task, r10 in recalls.items():
                    v1_cache[(method, bits, seed, task)] = r10
                for task, r10 in unc_recalls.items():
                    v1_unc_cache[(method, bits, seed, task)] = r10
                print("done")

    # --- V2 compressors: recompute bits=1,2,4 fresh ---
    print("\n--- V2 compressors (fresh) ---")
    v2_compute = [("qjl", [1, 2, 4]), ("polarquant", [1, 2, 4]), ("turboquant", [1, 2, 4])]
    for method, bits_list in v2_compute:
        for bits in bits_list:
            print(f"\nRunning V2 {method} @ {bits} bits  seeds={list(SEEDS)}")
            for seed in SEEDS:
                print(f"  seed={seed} ...", end=" ", flush=True)
                recalls, unc_recalls = run_one(_build_v2, method, d, bits, seed, imgs, txts)
                for task, r10 in recalls.items():
                    v2_cache[(method, bits, seed, task)] = r10
                for task, r10 in unc_recalls.items():
                    v2_unc_cache[(method, bits, seed, task)] = r10
                print("done")

    # ---------------------------------------------------------------------------
    # Aggregate: mean over seeds for each (method, bits, task)
    # ---------------------------------------------------------------------------

    def aggregate(cache, shared):
        """Returns dict: (method, bits, task) -> mean over seeds."""
        agg = defaultdict(list)
        for (method, bits, seed, task), r10 in {**shared, **cache}.items():
            agg[(method, bits, task)].append(r10)
        return {k: float(np.mean(v)) for k, v in agg.items()}

    v1_agg     = aggregate(v1_cache,     shared_cache)
    v2_agg     = aggregate(v2_cache,     shared_cache)
    v1_unc_agg = aggregate(v1_unc_cache, shared_unc_cache)
    v2_unc_agg = aggregate(v2_unc_cache, shared_unc_cache)

    # ---------------------------------------------------------------------------
    # Print tables
    # ---------------------------------------------------------------------------

    task_labels = {
        "T1_text2image": "T1 txt→img",
        "T2_image2text": "T2 img→txt",
        "T3_text2text":  "T3 txt→txt",
        "T4_image2image": "T4 img→img",
    }

    def print_table(agg, title):
        col_w = 12
        header = f"{'Method':<16} {'Bits':>5}  " + "  ".join(f"{task_labels[t]:>{col_w}}" for t in TASKS)
        sep = "-" * len(header)
        print(f"\n{'=' * len(header)}")
        print(f"  {title}")
        print(f"{'=' * len(header)}")
        print(f"Recall@10 (mean over {len(SEEDS)} seeds, {MAX_QUERIES} queries per task)")
        print(sep)
        print(header)
        print(sep)

        for label, method, bits_list in TABLE_ROWS:
            for bits in bits_list:
                vals = [agg.get((method, bits, t)) for t in TASKS]
                if all(v is None for v in vals):
                    continue
                row = f"{label:<16} {bits:>5}  "
                row += "  ".join(
                    f"{v:>{col_w}.3f}" if v is not None else f"{'—':>{col_w}}"
                    for v in vals
                )
                print(row)

        print(sep)

    print_table(v1_agg, "Table: V1 Compressors — Recall@10")
    print_table(v2_agg, "Table: V2 Compressors — Recall@10")

    # Save aggregated results to JSON for the PDF generator
    import json

    def _make_section(agg, cache, shared):
        keys = set((m, b) for (m, b, s, t) in cache) | set((m, b) for (m, b, s, t) in shared)
        out = {}
        for (m, b) in keys:
            vals = [agg.get((m, b, t)) for t in TASKS]
            if not all(v is None for v in vals):
                out[f"{m}_{b}"] = vals
        return out

    results_out = {
        "v1":     _make_section(v1_agg,     v1_cache,     shared_cache),
        "v2":     _make_section(v2_agg,     v2_cache,     shared_cache),
        "unc_v1": _make_section(v1_unc_agg, v1_unc_cache, shared_unc_cache),
        "unc_v2": _make_section(v2_unc_agg, v2_unc_cache, shared_unc_cache),
    }
    json_out = ROOT / "results_fresh.json"
    with open(json_out, "w") as f:
        json.dump(results_out, f, indent=2)
    print(f"\n[saved] aggregated results -> {json_out}")

    # Quick delta table
    print(f"\n{'=' * 80}")
    print("  Delta: V2 − V1 (positive = V2 is better)")
    print(f"{'=' * 80}")
    print(f"{'Method':<16} {'Bits':>5}  " + "  ".join(f"{task_labels[t]:>12}" for t in TASKS))
    print("-" * 80)
    for label, method, bits_list in TABLE_ROWS:
        if method in ("uncompressed", "faiss_pq"):
            continue
        for bits in bits_list:
            v1_vals = [v1_agg.get((method, bits, t)) for t in TASKS]
            v2_vals = [v2_agg.get((method, bits, t)) for t in TASKS]
            if all(v is None for v in v1_vals) or all(v is None for v in v2_vals):
                continue
            row = f"{label:<16} {bits:>5}  "
            deltas = []
            for v1v, v2v in zip(v1_vals, v2_vals):
                if v1v is not None and v2v is not None:
                    d = v2v - v1v
                    deltas.append(f"{d:>+12.3f}")
                else:
                    deltas.append(f"{'—':>12}")
            print(row + "  ".join(deltas))
    print("-" * 80)


if __name__ == "__main__":
    main()

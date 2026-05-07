# Project context — TurboQuant on CLIP

This file is a self-contained handoff document. A new chat reading this should be able to pick up the project without needing the prior transcript.

---

## 1. What this project is

**Course.** CS 639 (UW–Madison), spring 2026.
**Team.** 7 people. Submitting user is `rsingh266@wisc.edu`.
**Working directory.** `/Users/chiragjain/Desktop/UWM_Courses/CS_639/Project/turboquant-clip`. Not a git repo locally; remote is GitHub `QUASARS06/turboquant-clip`. Code is pushed manually so Colab can `git pull`.

**Title.** *Evaluating TurboQuant-Family Compression on CLIP Multimodal Embeddings.*

**One-line goal.** Test whether KV-cache-style analytic compressors (QJL, PolarQuant, TurboQuant) work on CLIP image+text embeddings, and whether cross-modal retrieval is more fragile than same-modal under aggressive bit reduction.

**Hypotheses.**
- **H1 (Cross-modal fragility).** Cross-modal retrieval (text↔image) degrades faster under compression than same-modal (text↔text, image↔image). The intuition: the CLIP modality gap is a small signal in float32 space, and bit-level perturbation pushes queries across it.
- **H2 (TurboQuant transfer).** TurboQuant (PolarQuant main + 1-bit QJL residual), which is near-SOTA on LLM KV caches, matches or beats its components on CLIP at matched bit budgets.

**Final verdict.**
- H1 — strongly **confirmed**. T1 (text→image) collapses by ~70% at 1 bit/dim; T4 (image→image) loses only ~10%.
- H2 — **rejected** under our implementation. TurboQuant underperforms both QJL and PolarQuant at every 2–4 bit cell. PolarQuant and FAISS-PQ form the Pareto frontier.

---

## 2. User profile (rsingh266@wisc.edu)

- CS 639 student, working in a 7-person team.
- Operates on a 16 GB MacBook (this directory's host) with a separate Colab/Kaggle account for GPU work.
- Asks for full pipelines and step-by-step commands; expects to copy/paste verbatim.
- Wants the latest package versions and pragmatic free-tier compute strategies (Colab free T4 + Drive for the one-shot embedding pass; everything else CPU on laptop).
- Comfortable with Python, comfortable reading model code, but expects the assistant to drive design decisions and only ask when there is a real fork.

---

## 3. Repository layout

```
turboquant-clip/
├── README.md
├── requirements.txt
├── pyproject.toml                    # pytest config (pythonpath=["."]) — needed for `from src...` imports under pytest
├── .gitignore
│
├── src/
│   ├── __init__.py
│   ├── embed.py                      # one-shot CLIP encoder (GPU step). Resumable + chunked.
│   │
│   ├── compressors/
│   │   ├── __init__.py
│   │   ├── base.py                   # abstract Compressor: fit(X), encode(X), ip_estimate(Q, code), bytes_per_vector()
│   │   ├── uncompressed.py           # float32 baseline
│   │   ├── qjl.py                    # 1-bit QJL with stored norm (so non-unit residuals work)
│   │   ├── polarquant.py             # recursive polar transform + per-level Lloyd-Max codebooks
│   │   ├── turboquant.py             # PolarQuant + 1-bit QJL on residual
│   │   └── faiss_pq.py               # IndexPQ wrapper, supports {1,2,4,8} bits/dim only
│   │
│   ├── eval/
│   │   ├── __init__.py
│   │   ├── retrieval.py              # 4 tasks (T1..T4), Recall@{1,5,10}, asymmetric search
│   │   ├── profiler.py               # bytes/vec, index bytes, compress_seconds, query_latency_ms
│   │   └── experiment.py             # main sweep, writes results/main_results.csv & profiles.csv
│   │
│   └── analysis/
│       ├── __init__.py
│       ├── plots.py                  # fig_cross_vs_same.png, fig_memory_tradeoff.png
│       ├── geometry.py               # fig_geometry.png + geometry_stats.txt (modality-gap stats, kurtosis)
│       └── failure_modes.py          # worst_queries.csv (largest rank deltas under TurboQuant 2-bit)
│
├── tests/
│   └── test_compressors.py           # correlation-based sanity tests on synthetic Gaussians
│
├── notebooks/
│   └── 01_encode_clip.ipynb          # Colab harness for src/embed.py
│
├── data/                             # NOT committed. Populated by src/embed.py.
│   ├── chunks/                       # {images,texts}/chunk_*.npy — incremental save on Colab+Drive
│   ├── image_embeddings.npy          # (31014, 512) float32, L2-normalized
│   ├── text_embeddings.npy           # (155070, 512) float32, L2-normalized
│   ├── captions.json                 # 155070 strings, sorted by image id then caption position
│   └── image_ids.json                # 31014 stable image IDs
│
├── results/                          # NOT committed (large CSVs + figures).
│   ├── main_results.csv              # one row per (method, bits, seed, task): R@{1,5,10}, n_queries
│   ├── profiles.csv                  # one row per (method, bits, seed): bytes/vec, latency
│   ├── summary_for_report.csv        # pivot table used by the PDF report build
│   └── figures/
│       ├── fig_cross_vs_same.png     # headline figure, 4 subplots (one per method)
│       ├── fig_memory_tradeoff.png   # mean R@10 vs bytes/vec, log-x
│       ├── fig_geometry.png          # before/after rotation marginals + modality-gap viz
│       ├── geometry_stats.txt        # JSON with kurtosis/skew/gap stats
│       └── worst_queries.csv         # TurboQuant-2b largest rank-deltas vs float32
│
└── report/
    ├── report.md                     # full written report (source of truth)
    ├── report.html                   # intermediate
    ├── report.pdf                    # final PDF (7 pages, ~290 KB) — built via build_pdf.py
    └── build_pdf.py                  # pandoc Markdown -> HTML -> xhtml2pdf
```

---

## 4. Per-file purpose and key design choices

### 4.1 `src/embed.py`
Precomputes CLIP ViT-B/32 image and text embeddings on Flickr30k. Saves chunked `.npy` files in `data/chunks/{images,texts}/chunk_NNNNN.npy` so a Colab session crash can resume. `finalize` concatenates and L2-renormalizes.

Key facts:
- Loads `nlphuji/flickr30k` from the HF auto-converted parquet branch (`refs/convert/parquet`, split=`TEST`). Required because `datasets >= 3.0` refuses to execute loading scripts.
- Sorts by `img_id` so iteration is deterministic across resumes.
- Calls `model.vision_model` + `model.visual_projection` directly instead of `get_image_features` (newer transformers wraps the return type in `BaseModelOutputWithPooling`, which broke our code).
- Atomic chunk save: write to `chunk_NNNNN.npy.tmp` via an open file handle (so numpy doesn't auto-append `.npy` and produce `chunk_NNNNN.npy.tmp.npy`), then `os.replace` to the final name.
- `_existing_chunks` uses an explicit regex `^chunk_(\d+)\.npy$` and **deletes stale `.tmp` files** at startup — Google Drive's filesystem matched `*.npy` against `*.npy.tmp` in pathlib glob, breaking earlier code.

Run:
```bash
python -m src.embed encode   --out-dir /content/drive/MyDrive/cs639/data
python -m src.embed finalize --out-dir /content/drive/MyDrive/cs639/data
```

### 4.2 `src/compressors/`

All compressors implement the abstract interface in `base.py`:
```python
class Compressor:
    def fit(self, X) -> Self: ...
    def encode(self, X) -> Any: ...                  # bytes/dict/array — opaque
    def ip_estimate(self, Q, code) -> np.ndarray:    # (n_queries, N)
    def bytes_per_vector(self) -> float:
```

Asymmetric inner-product convention everywhere: queries stay at full precision; only the database is compressed. This matches both QJL's and PolarQuant's papers.

#### `qjl.py` — 1-bit Quantized Johnson–Lindenstrauss
- Sample `S ~ N(0, I)` of shape `(m, d)` with `m = round(bits_per_dim * d)`. For 1 bit/dim, m=512.
- Encode: `code = sign(S x) ∈ {±1}^m`, packed to `uint8` with `np.packbits`.
- Estimator: `<q, x> ≈ sqrt(π/2) * (1/m) * (Sq) · sign(Sx)`.
- **Critical fix**: original implementation only worked for unit-norm inputs. We now store per-vector L2 norm (`+4 bytes/vec`) and multiply the unit-IP estimate by `||x||`. This was the bug that made TurboQuant useless: residuals `x - x_hat` are not unit-norm, so the QJL correction was wrong by a factor of `||r||`. After fix, TurboQuant correlation jumped from 0.64 to >0.92 on the synthetic test.

#### `polarquant.py` — recursive polar transform
- `n_transform_levels = log2(d) = 9` for d=512.
- Apply a fixed random orthogonal `R` (QR of N(0,I)) to "Gaussianize" coordinate marginals — measured kurtosis goes from ~70 to ~0.
- Recursively pair coords into `(r, θ)` until only one magnitude remains; total of `d-1 = 511` angles per vector, 1 magnitude.
- Per-level Lloyd-Max codebook with `2^angle_bits` levels, fit on up to 20k training vectors.
- Estimator is reconstruction-based: decode `x_hat`, return `Q @ x_hat.T`.
- **Memory trap**: `_quantize_to_codebook` materializes a `(N, n_angles_at_level, 2^bits)` array. At 8 bits with 186k DB this is ~48 GB. We hit OOM and now skip 8-bit for both PolarQuant and TurboQuant (`_applicable` in `experiment.py`).

#### `turboquant.py` — Polar + QJL residual
- At target `b` bits/dim: `angle_bits = b - 1`, `qjl_bits_per_dim = 1.0`.
- Special case: `b == 1` returns plain QJL (no budget for an angle stage).
- QJL uses `seed + 10_000` so its random projection is independent of the polar rotation.
- Estimator: `<q, x_hat>_polar + sqrt(π/2)/m * (Sq) · sign(Sr) * ||r||`.
- Fails to beat its components on CLIP — see results section. Mostly because `b-1` angle bits is too coarse at low budgets (2 bits = 1 angle bit = a 2-entry codebook).

#### `faiss_pq.py` — FAISS Product Quantization baseline
- `IndexPQ` with M sub-codebooks of nbits=8 each.
- Bit-width mapping: 1→M=64, 2→M=128, 4→M=256, 8→M=512. 3-bit PQ would require M=192 which doesn't divide d=512, so 3 bits is **skipped** (not a bug — documented limitation).
- Reproducible clustering via `index.pq.cp.seed = seed`.
- Decodes for IP estimation rather than using FAISS's built-in ADC — slightly slower but uniform interface.

#### `uncompressed.py`
Identity. `code = X.copy()`, estimator is `Q @ code.T`, bytes/vec = `4 * d`.

### 4.3 `src/eval/retrieval.py`

Four tasks with the asymmetric (full-precision query, compressed DB) convention.

| Task | Query | DB | Ground truth |
|------|-------|------|--------------|
| T1 text→image | text | image | caption j → image j // 5 (single match) |
| T2 image→text | image | text | image i → captions {5i, 5i+1, ..., 5i+4} (5 valid matches) |
| T3 text→text | text | text | caption j → other 4 captions of same image (self-match excluded) |
| T4 image→image | image | image | float32 top-1 of the same image excluded ("near-duplicate" proxy) |

`evaluate_all_tasks` calls `compressor.fit` once on `concat(image_db, text_db)` so codebooks see both modalities, then encodes each modality once and reuses. `max_queries` defaults to 1000 (samples from the query set with the seed). Set `-1` to use everything (T3 then becomes 155k×155k → multi-day run; do not do this).

Recall@k via `np.argpartition(-scores, max(ks))` then per-row sort of the top-K partition — fast on CPU.

### 4.4 `src/eval/experiment.py`

Driver. Iterates `methods × bits × seeds`, calls `evaluate_all_tasks` and `profile`, appends rows to `results/main_results.csv` and `results/profiles.csv`. CSVs are append-mode so a kill-restart resumes (but does not skip already-done cells — manual `--methods` / `--bits` filtering needed).

Defaults:
- `BITS_DEFAULT = (1, 2, 3, 4, 8, 32)`
- `METHODS_DEFAULT = ("qjl", "polarquant", "turboquant", "faiss_pq", "uncompressed")`
- `_applicable` filters per-method: uncompressed only at 32, faiss_pq only at {1,2,4,8}, polarquant/turboquant only at {1,2,3,4} (8-bit OOMs as documented).

CLI:
```bash
python -m src.eval.experiment \
    --data-dir data \
    --out-dir results \
    --seeds 0,1,2,3,4 \
    --max-queries 1000
# optional filters:
#   --methods turboquant,faiss_pq,uncompressed
#   --bits 1,2,3,4
```

### 4.5 `src/analysis/`

- `plots.py` — main figures from `main_results.csv` and `profiles.csv`.
- `geometry.py` — pre/post-rotation marginal histograms, modality gap norm, kurtosis/skew. Outputs `geometry_stats.txt`.
- `failure_modes.py` — for each query, computes the rank delta between float32 and TurboQuant-2bit retrieval; saves the worst offenders.

### 4.6 `tests/test_compressors.py`

Correlation tests on synthetic 512-dim unit Gaussians. Thresholds calibrated to the theoretical ceiling of each estimator, not to "fits the paper." Notes in the file: 1-bit QJL on iid Gaussians caps at r ≈ 0.62; on real CLIP data the IP range is wider so correlations are much higher.

Pass with: `pytest tests/`.

---

## 5. Setup / how to reproduce end-to-end

**Step 0 — install** (latest of everything; we do not pin):
```bash
pip install -r requirements.txt
```
Contents include: `torch`, `transformers`, `datasets`, `Pillow`, `numpy`, `pandas`, `scipy`, `matplotlib`, `tqdm`, `faiss-cpu`, `pytest`.

**Step 1 — embed (one-shot, GPU; do NOT redo)**. Run on Colab/Kaggle with `--out-dir` pointed at mounted Drive so chunks survive disconnects. Takes ~10 min on T4 (485 image batches @ 1.3 it/s + 606 text batches @ 2.8 it/s). Already done for this project — outputs are in `data/`.

```bash
python -m src.embed encode   --out-dir /content/drive/MyDrive/cs639/data
python -m src.embed finalize --out-dir /content/drive/MyDrive/cs639/data
# then download data/{image,text}_embeddings.npy + .json files to laptop
```

**Step 2 — sweep (CPU, laptop)**. ~3 hours.
```bash
python -m src.eval.experiment \
    --data-dir data --out-dir results \
    --seeds 0,1,2,3,4 --max-queries 1000
```

**Step 3 — figures and analysis**.
```bash
python -m src.analysis.plots       --results-dir results
python -m src.analysis.geometry    --data-dir data --out-dir results/figures
python -m src.analysis.failure_modes --data-dir data --results-dir results
```

**Step 4 — report PDF**.
```bash
python report/build_pdf.py
```
Produces `report/report.pdf` from `report/report.md` via pandoc + xhtml2pdf (pure Python, no system TeX/weasyprint deps).

---

## 6. Decisions and deviations from papers

The user explicitly chose to ship a complete pipeline first and improve paper fidelity later. Known deviations:

| Component | Paper | Our impl |
|---|---|---|
| QJL norm storage | Unit-norm only | Per-vector norm (+4 B/vec) so residuals work — necessary fix, not a deviation |
| PolarQuant codebook | Closed-form Beta-distribution quantizer | Empirical Lloyd-Max on rotated angles |
| PolarQuant levels | One codebook per level | Same — but only fit on 20k samples |
| PolarQuant estimator | Closed-form LUT-based IP | Reconstruction-based: decode → dense matmul |
| TurboQuant residual | Closed-form analytic correction | Reconstruct residual, run plain QJL on it |
| FAISS-PQ subspace count | Sweep nbits | Fixed nbits=8, sweep M |

These deviations are stated in §7 (Limitations) of the report. A paper-faithful TurboQuant might salvage H2; we did not have time to implement it.

---

## 7. Errors hit and resolutions (for future-you)

1. **`ModuleNotFoundError: No module named 'src'`** in pytest.
   Fix: created `pyproject.toml` with `[tool.pytest.ini_options] pythonpath = ["."]`.

2. **QJL synthetic-Gaussian correlations below threshold.** The thresholds were unrealistically high.
   Fix: lowered to match the theoretical ceiling for d=m=512 1-bit JL. Documented in test docstrings.

3. **TurboQuant correlation 0.64 vs expected 0.92.** Real bug — QJL was returning `<q, x>/||x||`, but TurboQuant's residuals are not unit-norm.
   Fix: `qjl.py` now stores norms and scales the estimator.

4. **`Dataset scripts are no longer supported, but found flickr30k.py`.** `datasets >= 3.0` blocks loading scripts.
   Fix: load from `revision="refs/convert/parquet"`, split `TEST`.

5. **`'BaseModelOutputWithPooling' object has no attribute 'cpu'`** from `model.get_image_features`. Newer transformers wraps the return type.
   Fix: call `model.vision_model` + `model.visual_projection` directly. Same for text side.

6. **`ValueError: invalid literal for int() with base 10: '00000.npy.tmp'`** in `_existing_chunks`. Pathlib's `glob("chunk_*.npy")` matched `chunk_*.npy.tmp` on Drive's FS.
   Fix: `_existing_chunks` now uses `re.compile(r"^chunk_(\d+)\.npy$")` and deletes stale `.tmp` files on startup.

7. **`FileNotFoundError: ...chunk_00000.npy.tmp -> ...chunk_00000.npy`** from `os.replace`. `np.save(path, arr)` auto-appends `.npy` if the path doesn't end in `.npy`, so the tmp was actually written to `chunk_00000.npy.tmp.npy`.
   Fix: write through an open file handle: `with open(tmp, "wb") as f: np.save(f, arr)`.

8. **PolarQuant 8-bit OOM**. `_quantize_to_codebook` broadcast `(N, n_angles, 256)` is ~48 GB on the full DB. User saw 18 GB swap, killed it.
   Fix: `_applicable` in `experiment.py` excludes 8 from polarquant/turboquant. PolarQuant at 4 bits is already near-lossless so we lose no signal.

---

## 8. Final results (R@10, mean over 5 seeds, 1000 queries)

Uncompressed reference: T1=0.498, T2=0.741, T3=0.528, T4=1.000.

```
                       T1     T2     T3     T4
faiss_pq    1   bit  0.337  0.488  0.407  0.851
qjl         1   bit  0.154  0.255  0.493  0.889
polarquant  1   bit  0.131  0.221  0.481  0.841
turboquant  1   bit  0.154  0.255  0.493  0.889  (== qjl by construction)

faiss_pq    2  bits  0.468  0.673  0.499  0.971
polarquant  2  bits  0.349  0.548  0.517  0.984
qjl         2  bits  0.248  0.404  0.510  0.958
turboquant  2  bits  0.157  0.263  0.442  0.686  ← worst at 2b

faiss_pq    4  bits  0.493  0.736  0.525  0.994
polarquant  4  bits  0.483  0.729  0.525  1.000
qjl         4  bits  0.350  0.556  0.524  0.989
turboquant  4  bits  0.425  0.660  0.517  0.978
```

**Cross-modal fragility (1-bit QJL):** T4 drops 11%, T1 drops 69%. Confirmed.

**Best methods:** FAISS-PQ at 1–2 bits; FAISS-PQ and PolarQuant tied at 4 bits. TurboQuant never wins.

**Geometry findings (`geometry_stats.txt`):**
- Pre-rotation kurtosis: image 69.4, text 36.7. Skew ≈ ±4.
- Post-rotation kurtosis: ~0 for both. Skew ~0. The QR-based rotation is enough to Gaussianize.
- Modality gap norm 0.80; cos(mean_image, mean_text) = 0.344. Large — explains H1.

---

## 9. What would I do differently / open work

- Implement paper-faithful TurboQuant: closed-form Beta-distribution angle quantizer, analytic LUT-based IP estimator. Re-test H2.
- Add at least one out-of-distribution retrieval task (e.g., COCO 5K) to confirm the cross-modal fragility story isn't a Flickr30k artifact.
- Per-cell 95% CIs on R@1 (current 1000-query budget is fine for R@10 but tight for R@1).
- Try larger CLIP backbones (ViT-L/14). The modality gap behaves differently and the bit/dim story may shift.
- For the PolarQuant codebook fit, replace the broadcast-based assignment with `scipy.cluster.vq.vq` or chunked argmin so 8-bit becomes feasible.

---

## 10. Files this context references but does not duplicate

For any deeper detail than what's here, read:
- `src/compressors/qjl.py`, `polarquant.py`, `turboquant.py`, `faiss_pq.py` — full algorithm + estimator math in docstrings.
- `src/eval/retrieval.py` — exact ground-truth construction for each task.
- `src/eval/experiment.py` — sweep driver with `_applicable` and `_build`.
- `report/report.md` — written-up version of all results, suitable for course submission.
- `tests/test_compressors.py` — correlation thresholds and the reasoning behind them.

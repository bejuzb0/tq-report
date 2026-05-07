"""TurboQuant — PolarQuant main stage + QJL residual correction.

Reference: Zandieh, Daliri, Hadian, Mirrokni. "TurboQuant: Online Vector
Quantization with Near-optimal Distortion Rate." ICLR 2026.
(arXiv:2504.19874, Algorithm 2 + Figure 1)

How it works
------------
PolarQuant compresses each vector into a compact polar representation.
Its reconstruction x_hat is accurate, but the inner-product estimator
<q, x_hat> has a small systematic bias. QJL applied to the residual
r = x - x_hat provides an unbiased correction at the cost of 1 extra
bit per dimension:

    <q, x> ≈ <q, x_hat>  +  sqrt(π/2) / m  *  Σ_i (SRHT(q))_i * sign((SRHT(r))_i)

Budget:
    total ≈ angle_bits * (d-1)/d  +  qjl_bits_per_dim
    Paper target 3-bit: angle_bits=2, qjl_bits_per_dim=1
    Paper target 4-bit: angle_bits=3, qjl_bits_per_dim=1

Design decisions (fixes over the naive version)
------------------------------------------------
1.  QJL seed is derived independently from the PolarQuant seed using a
    deterministic child RNG — not a fragile offset like seed+10000.

2.  Residual health check in encode(): if residual norms are very small
    relative to the input, QJL is adding noise not signal.  We warn and
    record the mean residual magnitude in the code so callers can diagnose
    this in evaluation.

3.  cosine=False is explicitly set on QJL — residuals are NOT unit vectors,
    so the full inner product estimator (with norm scaling) is needed.
    cosine=True would drop the norm and give wrong correction magnitudes.

4.  A diagnostic helper decompose_scores() exposes PolarQuant-only vs
    full TurboQuant scores side by side, making it easy to verify the
    QJL correction is helping rather than adding noise.
"""
from __future__ import annotations

import warnings

import numpy as np

from .base import Compressor
from .polarquant import PolarQuant
from .qjl import QJL

# Residuals whose norm is below this fraction of the original vector norm
# are too small for QJL to extract useful direction from.  Warn but continue.
_RESIDUAL_NORM_WARN_THRESHOLD = 0.05


class TurboQuant(Compressor):
    """TurboQuant: PolarQuant + QJL residual correction.

    Parameters
    ----------
    d : int
        Input dimension.  Must be a power of 2 (512 for CLIP ViT-B/32).
    angle_bits : int
        Bits used by PolarQuant for each angle coordinate.
        2 → ~3-bit total, 3 → ~4-bit total (see budget formula above).
    qjl_bits_per_dim : float
        Bits per dimension used by QJL for the residual.
        1.0 is the paper default and recommended value.
    seed : int
        Master seed.  PolarQuant and QJL get independent child seeds
        derived from this via a deterministic RNG split.
    """

    name = "turboquant"

    def __init__(
        self,
        d: int,
        angle_bits: int = 2,
        qjl_bits_per_dim: float = 1.0,
        seed: int = 0,
    ):
        self.d = d
        self.angle_bits = angle_bits
        self.qjl_bits_per_dim = qjl_bits_per_dim
        self.seed = seed

        # Derive independent seeds for each stage via a child RNG.
        # This avoids fragile seed arithmetic (seed+10000 etc.) and
        # guarantees independence regardless of the master seed value.
        master_rng = np.random.default_rng(seed)
        polar_seed = int(master_rng.integers(0, 2**31))
        qjl_seed   = int(master_rng.integers(0, 2**31))

        self.polar = PolarQuant(d, angle_bits=angle_bits, seed=polar_seed)

        # cosine=False is REQUIRED here.  Residuals are not unit vectors —
        # their magnitude encodes how much PolarQuant missed.  The QJL
        # estimator must scale by the residual norm to give the correct
        # inner-product correction.
        self.qjl = QJL(
            d,
            bits_per_dim=qjl_bits_per_dim,
            seed=qjl_seed,
            cosine=False,
        )

    # ------------------------------------------------------------------
    # Compressor API
    # ------------------------------------------------------------------

    def fit(self, X: np.ndarray) -> "TurboQuant":
        """Fit PolarQuant codebook on training data X (N, d)."""
        self.polar.fit(X)
        return self

    def encode(self, X: np.ndarray) -> dict:
        """Compress a database of vectors X (N, d).

        Returns
        -------
        dict with keys:
          "polar"              : PolarQuant code dict
          "qjl"                : QJL code dict for residuals
          "mean_residual_ratio": float — diagnostic: mean(||r||) / mean(||x||)
                                 Values near 0 mean PolarQuant already captures
                                 almost everything; QJL correction may not help.
                                 Values > 0.1 mean there is useful residual signal.
        """
        X = np.asarray(X, dtype=np.float32)

        # Stage 1: PolarQuant main compression
        polar_code = self.polar.encode(X)

        # Stage 2: reconstruct and compute residual
        X_hat    = self.polar.decode(polar_code)       # (N, d)
        residual = X - X_hat                            # (N, d) — NOT unit-norm

        # Residual health check — warn if PolarQuant already converged
        # and the residuals are too small for QJL to correct usefully.
        input_norms    = np.linalg.norm(X, axis=1)
        residual_norms = np.linalg.norm(residual, axis=1)
        # avoid divide-by-zero on zero input vectors
        safe_input = np.maximum(input_norms, 1e-12)
        ratio = float((residual_norms / safe_input).mean())

        if ratio < _RESIDUAL_NORM_WARN_THRESHOLD:
            warnings.warn(
                f"TurboQuant: mean residual norm is {ratio:.4f}x the input norm "
                f"(threshold {_RESIDUAL_NORM_WARN_THRESHOLD}). "
                f"PolarQuant has already captured most of the signal — "
                f"QJL correction may add noise rather than improve estimates. "
                f"Consider reducing angle_bits or checking your input data.",
                stacklevel=2,
            )

        # Stage 3: QJL on the residual (cosine=False → norm is used in estimator)
        qjl_code = self.qjl.encode(residual)

        return {
            "polar":               polar_code,
            "qjl":                 qjl_code,
            "mean_residual_ratio": ratio,
        }

    def ip_estimate(self, Q: np.ndarray, code: dict) -> np.ndarray:
        """Estimate similarity scores Q (nq, d) vs database.

        Returns
        -------
        scores : (nq, N) float32
            <q, x_hat> + QJL correction for each (query, database) pair.
        """
        Q = np.asarray(Q, dtype=np.float32)

        # Main estimate from PolarQuant reconstruction
        main = self.polar.ip_estimate(Q, code["polar"])        # (nq, N)

        # Unbiased correction from QJL on the residual
        # cosine=False means the estimator scales by residual norms,
        # giving the correct inner-product contribution of r = x - x_hat.
        correction = self.qjl.ip_estimate(Q, code["qjl"])     # (nq, N)

        return main + correction

    def bytes_per_vector(self) -> float:
        """Total bytes per compressed vector (PolarQuant + QJL)."""
        return self.polar.bytes_per_vector() + self.qjl.bytes_per_vector()

    def bits_per_dim(self) -> float:
        """Approximate total bits per original float32 dimension."""
        total_bytes = self.bytes_per_vector()
        return (total_bytes * 8) / self.d

    # ------------------------------------------------------------------
    # Diagnostic helper
    # ------------------------------------------------------------------

    def decompose_scores(
        self,
        Q: np.ndarray,
        code: dict,
    ) -> dict[str, np.ndarray]:
        """Return scores broken down by stage for diagnostic evaluation.

        Use this to verify the QJL correction is helping rather than hurting:

            scores = turbo.decompose_scores(Q, code)
            recall_polar = recall_at_k(scores["polar_only"], gt, k=10)
            recall_turbo = recall_at_k(scores["combined"],   gt, k=10)
            print(f"PolarQuant only: {recall_polar:.3f}")
            print(f"TurboQuant:      {recall_turbo:.3f}")
            print(f"Correction magnitude: {np.abs(scores['correction']).mean():.4f}")
            print(f"Main magnitude:       {np.abs(scores['polar_only']).mean():.4f}")

        If recall_turbo < recall_polar, the residual norms are too small
        and QJL is amplifying noise.  Check mean_residual_ratio in the code.

        Returns
        -------
        dict with:
          "polar_only"  : (nq, N) scores from PolarQuant alone
          "correction"  : (nq, N) QJL correction term
          "combined"    : (nq, N) polar_only + correction  (= ip_estimate output)
        """
        Q = np.asarray(Q, dtype=np.float32)
        polar_only = self.polar.ip_estimate(Q, code["polar"])
        correction = self.qjl.ip_estimate(Q, code["qjl"])
        return {
            "polar_only":  polar_only,
            "correction":  correction,
            "combined":    polar_only + correction,
        }
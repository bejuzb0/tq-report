"""TurboQuantMSE — MSE-optimal first stage from the TurboQuant paper.

Implements the core of Algorithm 1 from:
Zandieh, Daliri, Hadian, Mirrokni.
"TurboQuant: Online Vector Quantization with Near-optimal Distortion Rate."

For unit vectors x:
1. Apply a shared random orthogonal rotation R.
2. Quantize each rotated coordinate independently using a Lloyd-Max scalar
   codebook for the coordinate distribution of a random point on S^{d-1}.
3. Decode by replacing indices with centroids and rotating back.

This is the first stage used by TurboQuantProd / TurboQuant.
"""

from __future__ import annotations

import numpy as np

from .base import Compressor


def _random_orthogonal(d: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    A = rng.standard_normal((d, d)).astype(np.float32)
    Q, _ = np.linalg.qr(A)
    return Q.astype(np.float32)


def _sample_sphere_coordinate(d: int, n: int, seed: int) -> np.ndarray:
    """Sample one coordinate of a uniformly random point on S^{d-1}.

    If g ~ N(0, I_d), then g_1 / ||g|| is exactly distributed as one
    coordinate of a uniformly random point on the unit sphere. This samples
    that coordinate without materializing an n x d Gaussian matrix.
    """
    rng = np.random.default_rng(seed)
    z = rng.standard_normal(n).astype(np.float64)

    if d > 1:
        w = rng.chisquare(df=d - 1, size=n).astype(np.float64)
        x = z / np.sqrt(z * z + w)
    else:
        x = np.sign(z)

    return x.astype(np.float32)


def _lloyd_max_1d_from_samples(
    samples: np.ndarray,
    n_levels: int,
    n_iter: int = 80,
) -> np.ndarray:
    """Fit a 1-D Lloyd-Max / k-means scalar codebook from samples."""
    samples = samples.astype(np.float32).ravel()

    if n_levels <= 1:
        return np.array([float(samples.mean())], dtype=np.float32)

    # Stable quantile initialization.
    qs = (np.arange(n_levels, dtype=np.float64) + 0.5) / n_levels
    centroids = np.quantile(samples, qs).astype(np.float32)

    for _ in range(n_iter):
        boundaries = (centroids[:-1] + centroids[1:]) / 2.0
        assignments = np.searchsorted(boundaries, samples, side="right")

        new_centroids = centroids.copy()
        for k in range(n_levels):
            mask = assignments == k
            if np.any(mask):
                new_centroids[k] = samples[mask].mean()

        if np.allclose(new_centroids, centroids, rtol=0.0, atol=1e-7):
            break

        centroids = np.sort(new_centroids.astype(np.float32))

    return centroids.astype(np.float32)


def _quantize_matrix_to_codebook(
    values: np.ndarray,
    codebook: np.ndarray,
    chunk_size: int = 1_000_000,
) -> np.ndarray:
    """Quantize a matrix to nearest codebook entry using chunking."""
    flat = values.reshape(-1).astype(np.float32)
    cb = codebook.astype(np.float32)

    dtype = np.uint8 if len(cb) <= 256 else np.uint16
    out = np.empty(flat.shape, dtype=dtype)

    for start in range(0, flat.size, chunk_size):
        end = min(start + chunk_size, flat.size)
        chunk = flat[start:end]
        dists = np.abs(chunk[:, None] - cb[None, :])
        out[start:end] = dists.argmin(axis=1).astype(dtype)

    return out.reshape(values.shape)


class TurboQuantMSE(Compressor):
    """MSE-optimized TurboQuant stage.

    This can be evaluated directly for reconstruction/MSE, and it is also
    used inside TurboQuant as the first stage before QJL residual correction.
    """

    name = "turboquant_mse"

    def __init__(
        self,
        d: int,
        bits: int = 2,
        seed: int = 0,
        n_codebook_samples: int = 500_000,
        n_lloyd_iter: int = 80,
    ):
        if bits < 0:
            raise ValueError(f"bits must be >= 0, got {bits}")

        self.d = int(d)
        self.bits = int(bits)
        self.seed = int(seed)
        self.n_codebook_samples = int(n_codebook_samples)
        self.n_lloyd_iter = int(n_lloyd_iter)

        self.R = _random_orthogonal(self.d, self.seed)

        n_levels = 2 ** self.bits
        samples = _sample_sphere_coordinate(
            self.d,
            self.n_codebook_samples,
            self.seed + 12345,
        )
        self.codebook = _lloyd_max_1d_from_samples(
            samples,
            n_levels=n_levels,
            n_iter=self.n_lloyd_iter,
        )

    def fit(self, X: np.ndarray) -> "TurboQuantMSE":
        # Paper version is data-oblivious after d and bit-width are known.
        return self

    def encode(self, X: np.ndarray) -> dict:
        X = np.ascontiguousarray(X.astype(np.float32))

        if X.ndim != 2 or X.shape[1] != self.d:
            raise ValueError(f"Expected X shape (N, {self.d}), got {X.shape}")

        Y = X @ self.R.T
        codes = _quantize_matrix_to_codebook(Y, self.codebook)

        return {"codes": codes}

    def decode(self, code: dict) -> np.ndarray:
        codes = code["codes"]
        Y_hat = self.codebook[codes].astype(np.float32)
        X_hat = Y_hat @ self.R
        return X_hat.astype(np.float32)

    def ip_estimate(self, Q: np.ndarray, code: dict) -> np.ndarray:
        Q = np.ascontiguousarray(Q.astype(np.float32))

        if Q.ndim != 2 or Q.shape[1] != self.d:
            raise ValueError(f"Expected Q shape (nq, {self.d}), got {Q.shape}")

        X_hat = self.decode(code)
        return Q @ X_hat.T

    def bytes_per_vector(self) -> float:
        # Ideal packed storage: d scalar indices, each self.bits bits.
        return self.d * self.bits / 8.0

"""PolarQuant — recursive polar-coordinate quantization.

Reference:
Han, Kacham, Karbasi, Mirrokni, Zandieh.
"PolarQuant: Quantizing KV Caches with Polar Transformation."

Supports:
    - full recursion to one final radius;
    - practical fixed-depth recursion, e.g. L=4;
    - per-level bit allocation, e.g. [4, 2, 2, 2].
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from .base import Compressor


def _random_orthogonal(d: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    A = rng.standard_normal((d, d)).astype(np.float32)
    Q, _ = np.linalg.qr(A)
    return Q.astype(np.float32)


def _lloyd_max_1d(
    samples: np.ndarray,
    n_levels: int,
    n_iter: int = 60,
) -> np.ndarray:
    samples = samples.astype(np.float32).ravel()

    if n_levels <= 1:
        return np.array([float(samples.mean())], dtype=np.float32)

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


def _quantize_to_codebook_chunked(
    values: np.ndarray,
    codebook: np.ndarray,
    chunk_size: int = 1_000_000,
) -> np.ndarray:
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


class PolarQuant(Compressor):
    name = "polarquant"

    def __init__(
        self,
        d: int,
        angle_bits: int = 3,
        seed: int = 0,
        n_transform_levels: int | None = None,
        bits_by_level: Sequence[int] | None = None,
        magnitude_bits: int = 32,
        max_fit_vectors: int = 20_000,
    ):
        if d & (d - 1) != 0:
            raise ValueError(f"d must be a power of 2, got {d}")

        self.d = int(d)
        self.angle_bits = int(angle_bits)
        self.seed = int(seed)
        self.max_fit_vectors = int(max_fit_vectors)
        self.magnitude_bits = int(magnitude_bits)

        full_levels = int(np.log2(self.d))

        if n_transform_levels is None:
            self.n_transform_levels = full_levels
        else:
            if n_transform_levels < 1 or n_transform_levels > full_levels:
                raise ValueError(
                    f"n_transform_levels must be in [1, {full_levels}], "
                    f"got {n_transform_levels}"
                )
            self.n_transform_levels = int(n_transform_levels)

        if bits_by_level is None:
            self.bits_by_level = [self.angle_bits] * self.n_transform_levels
        else:
            if len(bits_by_level) != self.n_transform_levels:
                raise ValueError(
                    "bits_by_level length must equal n_transform_levels. "
                    f"Got len(bits_by_level)={len(bits_by_level)}, "
                    f"n_transform_levels={self.n_transform_levels}."
                )
            self.bits_by_level = [int(b) for b in bits_by_level]

        self.R = _random_orthogonal(self.d, self.seed)
        self.codebooks: list[np.ndarray] = []

    @classmethod
    def practical_kv(
        cls,
        d: int,
        seed: int = 0,
        magnitude_bits: int = 16,
    ) -> "PolarQuant":
        return cls(
            d=d,
            seed=seed,
            n_transform_levels=4,
            bits_by_level=[4, 2, 2, 2],
            magnitude_bits=magnitude_bits,
        )

    def _transform(self, X: np.ndarray) -> tuple[np.ndarray, list[np.ndarray]]:
        current = X @ self.R.T
        angles_per_level: list[np.ndarray] = []

        for level in range(self.n_transform_levels):
            pairs = current.reshape(current.shape[0], -1, 2)
            a = pairs[..., 0]
            b = pairs[..., 1]

            radii = np.sqrt(a * a + b * b).astype(np.float32)

            if level == 0:
                theta = np.mod(np.arctan2(b, a), 2.0 * np.pi)
            else:
                theta = np.arctan2(b, a)

            angles_per_level.append(theta.astype(np.float32))
            current = radii

        return current.astype(np.float32), angles_per_level

    def _inverse_transform(
        self,
        radii: np.ndarray,
        angles_per_level: list[np.ndarray],
    ) -> np.ndarray:
        current = radii.astype(np.float32)

        for theta in reversed(angles_per_level):
            a = current * np.cos(theta)
            b = current * np.sin(theta)
            current = np.stack([a, b], axis=-1).reshape(current.shape[0], -1)

        return (current @ self.R).astype(np.float32)

    def fit(self, X: np.ndarray) -> "PolarQuant":
        X = np.ascontiguousarray(X.astype(np.float32))

        if X.ndim != 2 or X.shape[1] != self.d:
            raise ValueError(f"Expected X shape (N, {self.d}), got {X.shape}")

        if len(X) > self.max_fit_vectors:
            rng = np.random.default_rng(self.seed)
            idx = rng.choice(len(X), self.max_fit_vectors, replace=False)
            sample = X[idx]
        else:
            sample = X

        _, angles_per_level = self._transform(sample)

        self.codebooks = []
        for theta, bits in zip(angles_per_level, self.bits_by_level):
            self.codebooks.append(
                _lloyd_max_1d(theta.ravel(), n_levels=2 ** bits)
            )

        return self

    def encode(self, X: np.ndarray) -> dict:
        if not self.codebooks:
            raise RuntimeError("Call fit(X) before encode(X).")

        X = np.ascontiguousarray(X.astype(np.float32))

        if X.ndim != 2 or X.shape[1] != self.d:
            raise ValueError(f"Expected X shape (N, {self.d}), got {X.shape}")

        radii, angles_per_level = self._transform(X)

        angle_codes = [
            _quantize_to_codebook_chunked(theta, cb)
            for theta, cb in zip(angles_per_level, self.codebooks)
        ]

        return {
            "radii": radii.astype(np.float32),
            "angle_codes": angle_codes,
        }

    def decode(self, code: dict) -> np.ndarray:
        if not self.codebooks:
            raise RuntimeError("Call fit(X) before decode(code).")

        angles_per_level = [
            cb[idx].astype(np.float32)
            for idx, cb in zip(code["angle_codes"], self.codebooks)
        ]

        return self._inverse_transform(code["radii"], angles_per_level)

    def ip_estimate(self, Q: np.ndarray, code: dict) -> np.ndarray:
        Q = np.ascontiguousarray(Q.astype(np.float32))

        if Q.ndim != 2 or Q.shape[1] != self.d:
            raise ValueError(f"Expected Q shape (nq, {self.d}), got {Q.shape}")

        X_hat = self.decode(code)
        return Q @ X_hat.T

    def bytes_per_vector(self) -> float:
        total_angle_bits = 0

        dim = self.d
        for bits in self.bits_by_level:
            total_angle_bits += (dim // 2) * bits
            dim = dim // 2

        n_radii = self.d // (2 ** self.n_transform_levels)
        total_radius_bits = n_radii * self.magnitude_bits

        return (total_angle_bits + total_radius_bits) / 8.0

"""QJL — 1-bit Quantized Johnson-Lindenstrauss transform.

Reference:
Zandieh, Daliri, Han.
"QJL: 1-Bit Quantized JL Transform for KV Cache Quantization with Zero Overhead."

Core estimator:
    H_S(k) = sign(S k)

    <q, k> ≈ sqrt(pi / 2) / m * ||k||_2 * <S q, H_S(k)>

Queries remain full precision; database vectors are compressed.
"""

from __future__ import annotations

import numpy as np

from .base import Compressor


class QJL(Compressor):
    name = "qjl"

    def __init__(
        self,
        d: int,
        bits_per_dim: float = 1.0,
        seed: int = 0,
        orthogonalize: bool = False,
        norm_bits: int = 32,
    ):
        if bits_per_dim <= 0:
            raise ValueError(f"bits_per_dim must be positive, got {bits_per_dim}")

        self.d = int(d)
        self.bits_per_dim = float(bits_per_dim)
        self.m = int(round(self.bits_per_dim * self.d))
        self.seed = int(seed)
        self.orthogonalize = bool(orthogonalize)
        self.norm_bits = int(norm_bits)

        rng = np.random.default_rng(self.seed)

        if self.orthogonalize:
            if self.m > self.d:
                raise ValueError(
                    "orthogonalize=True requires m <= d. "
                    f"Got m={self.m}, d={self.d}."
                )

            # Practical variant: orthogonal rows often reduce variance.
            # Default remains Gaussian because the theorem assumes Gaussian S.
            A = rng.standard_normal((self.d, self.m)).astype(np.float32)
            Q, _ = np.linalg.qr(A)
            self.S = (np.sqrt(self.d) * Q.T[: self.m]).astype(np.float32)
        else:
            self.S = rng.standard_normal((self.m, self.d)).astype(np.float32)

    def fit(self, X: np.ndarray) -> "QJL":
        return self

    def encode(self, X: np.ndarray) -> dict:
        X = np.ascontiguousarray(X.astype(np.float32))

        if X.ndim != 2 or X.shape[1] != self.d:
            raise ValueError(f"Expected X shape (N, {self.d}), got {X.shape}")

        norms = np.linalg.norm(X, axis=1).astype(np.float32)
        safe_norms = np.maximum(norms, 1e-12)

        X_unit = X / safe_norms[:, None]
        projected = X_unit @ self.S.T

        signs_bool = projected >= 0
        signs_packed = np.packbits(signs_bool, axis=1, bitorder="little")

        return {
            "norms": norms,
            "signs": signs_packed,
        }

    def _unpack(self, packed: np.ndarray) -> np.ndarray:
        bits = np.unpackbits(
            packed,
            axis=1,
            count=self.m,
            bitorder="little",
        )
        return np.where(bits.astype(bool), 1.0, -1.0).astype(np.float32)

    def ip_estimate(self, Q: np.ndarray, code: dict) -> np.ndarray:
        Q = np.ascontiguousarray(Q.astype(np.float32))

        if Q.ndim != 2 or Q.shape[1] != self.d:
            raise ValueError(f"Expected Q shape (nq, {self.d}), got {Q.shape}")

        Q_proj = Q @ self.S.T
        signs = self._unpack(code["signs"])

        scale = np.sqrt(np.pi / 2.0) / self.m
        unit_ip = scale * (Q_proj @ signs.T)

        return unit_ip * code["norms"][None, :]

    def bytes_per_vector(self) -> float:
        # Ideal packed sign bits + one stored norm.
        return self.m / 8.0 + self.norm_bits / 8.0

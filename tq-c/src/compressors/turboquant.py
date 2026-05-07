"""TurboQuant — paper-faithful two-stage inner-product quantizer.

Reference:
Zandieh, Daliri, Hadian, Mirrokni.
"TurboQuant: Online Vector Quantization with Near-optimal Distortion Rate."

This keeps the public class name TurboQuant, but implements the paper's
two-stage construction:

    1. TurboQuantMSE with b - 1 bits per dimension.
    2. QJL on the residual with 1 bit per dimension.

The QJL object stores the residual norm internally, matching Algorithm 2's
gamma = ||r||_2.
"""

from __future__ import annotations

import numpy as np

from .base import Compressor
from .qjl import QJL
from .turbo_mse import TurboQuantMSE


class TurboQuant(Compressor):
    name = "turboquant"

    def __init__(
        self,
        d: int,
        angle_bits: int = 2,
        qjl_bits_per_dim: float = 1.0,
        seed: int = 0,
        total_bits: int | None = None,
        orthogonalize_qjl: bool = False,
    ):
        """Create a TurboQuant compressor.

        Backward-compatible usage:
            TurboQuant(d, angle_bits=2, qjl_bits_per_dim=1.0)

        Paper-style usage:
            TurboQuant(d, total_bits=3)

        If total_bits is provided, the MSE stage uses total_bits - 1 bits
        and QJL uses 1 bit/dim.
        """
        self.d = int(d)
        self.seed = int(seed)

        if total_bits is not None:
            if total_bits < 1:
                raise ValueError(f"total_bits must be >= 1, got {total_bits}")
            mse_bits = int(total_bits) - 1
            qjl_bits = 1.0
        else:
            mse_bits = int(angle_bits)
            qjl_bits = float(qjl_bits_per_dim)

        if mse_bits < 0:
            raise ValueError(f"MSE-stage bits must be >= 0, got {mse_bits}")
        if qjl_bits <= 0:
            raise ValueError(f"qjl_bits_per_dim must be positive, got {qjl_bits}")

        self.angle_bits = mse_bits
        self.qjl_bits_per_dim = qjl_bits
        self.total_bits = self.angle_bits + self.qjl_bits_per_dim
        self.orthogonalize_qjl = bool(orthogonalize_qjl)

        self.mse = TurboQuantMSE(
            d=self.d,
            bits=self.angle_bits,
            seed=self.seed,
        )

        self.qjl = QJL(
            d=self.d,
            bits_per_dim=self.qjl_bits_per_dim,
            seed=self.seed + 10_000,
            orthogonalize=self.orthogonalize_qjl,
        )

    def fit(self, X: np.ndarray) -> "TurboQuant":
        self.mse.fit(X)
        self.qjl.fit(X)
        return self

    def encode(self, X: np.ndarray) -> dict:
        X = np.ascontiguousarray(X.astype(np.float32))

        if X.ndim != 2 or X.shape[1] != self.d:
            raise ValueError(f"Expected X shape (N, {self.d}), got {X.shape}")

        mse_code = self.mse.encode(X)
        X_hat = self.mse.decode(mse_code)

        residual = X - X_hat
        qjl_code = self.qjl.encode(residual)

        return {
            "mse": mse_code,
            "qjl": qjl_code,
        }

    def ip_estimate(self, Q: np.ndarray, code: dict) -> np.ndarray:
        Q = np.ascontiguousarray(Q.astype(np.float32))

        if Q.ndim != 2 or Q.shape[1] != self.d:
            raise ValueError(f"Expected Q shape (nq, {self.d}), got {Q.shape}")

        main = self.mse.ip_estimate(Q, code["mse"])
        correction = self.qjl.ip_estimate(Q, code["qjl"])

        return main + correction

    def bytes_per_vector(self) -> float:
        return self.mse.bytes_per_vector() + self.qjl.bytes_per_vector()

"""PolarQuant V2 — analytical polar-coordinate quantization.

This fixes major deviations from the theoretical PolarQuant algorithm
(Han et al., AISTATS 2026):
1. Codebooks are analytically derived from the geometry of Gaussian space
   (data-free), eliminating the need for empirical k-means fitting.
2. Variable bit-width schedules: more bits are allocated to Level 1
   (full circle [0, 2pi]), and fewer to deeper levels (quadrant [0, pi/2]).
3. Domain correctness: angles > Level 1 are properly bounded to [0, pi/2]
   because the inputs are strictly non-negative radii.
"""
from __future__ import annotations

import math
import warnings

import numpy as np

from .base import Compressor


def _random_orthogonal(d: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    A = rng.standard_normal((d, d))
    Q, _ = np.linalg.qr(A)
    return Q.astype(np.float32)


def _polar_angle_pdf(grid: np.ndarray, level: int) -> np.ndarray:
    """Analytical PDF for the polar angle at a given recursive level."""
    if level <= 1:
        pdf = np.ones_like(grid)
    else:
        exponent = (1 << (level - 1)) - 1
        pdf = np.power(np.clip(np.sin(2.0 * grid), 0.0, None), exponent)
    
    pdf_sum = pdf.sum()
    if pdf_sum == 0:
        return np.full_like(grid, 1.0 / len(grid))
    return pdf / pdf_sum


def _polar_angle_codebook(level: int, bits: int) -> np.ndarray:
    """Generate a Lloyd-Max codebook using the exact theoretical PDF."""
    if bits <= 0:
        return np.zeros((0,), dtype=np.float32)

    level_count = 1 << bits
    if level <= 1:
        # Uniform distribution over [0, 2pi]
        step = (2.0 * math.pi) / level_count
        centroids = np.arange(level_count, dtype=np.float32) * step + step / 2.0
        return centroids.astype(np.float32)

    # Concentrated distribution over [0, pi/2] for levels > 1
    grid = np.linspace(1e-6, math.pi / 2 - 1e-6, 32768, dtype=np.float32)
    weights = _polar_angle_pdf(grid, level)
    cdf = np.cumsum(weights)
    quantiles = (np.arange(level_count, dtype=np.float32) + 0.5) / level_count
    centroids = np.interp(quantiles, cdf, grid).astype(np.float32)

    for _ in range(100):
        boundaries = np.empty(level_count + 1, dtype=np.float32)
        boundaries[0] = 0.0
        boundaries[-1] = math.pi / 2
        boundaries[1:-1] = 0.5 * (centroids[:-1] + centroids[1:])
        
        new_centroids = centroids.copy()
        for i in range(level_count):
            if i == level_count - 1:
                mask = (grid >= boundaries[i]) & (grid <= boundaries[i + 1])
            else:
                mask = (grid >= boundaries[i]) & (grid < boundaries[i + 1])
            bucket_weights = weights[mask]
            if bucket_weights.size == 0:
                continue
            total_weight = bucket_weights.sum()
            if total_weight > 0:
                new_centroids[i] = np.sum(bucket_weights * grid[mask]) / total_weight
                
        if np.max(np.abs(new_centroids - centroids)) < 1e-6:
            centroids = new_centroids
            break
        centroids = new_centroids

    return centroids.astype(np.float32)


class PolarQuant(Compressor):
    name = "polarquant"

    def __init__(self, d: int, angle_bits: int = 3, seed: int = 0):
        if d & (d - 1) != 0:
            raise ValueError(f"d must be a power of 2 (got {d}).")
        self.d = d
        self.angle_bits = angle_bits
        self.seed = seed
        self.n_transform_levels = int(np.log2(d))  # log2(512)=9

        # Variable bit-width schedule matching the theoretical optimum.
        # Level 1 gets (angle_bits + 1) to cover [0, 2pi]
        # Levels > 1 get max(1, angle_bits - 1) to cover [0, pi/2]
        # Example 3-bit: (4, 2, 2, 2...)
        # Example 2-bit: (3, 1, 1, 1...)
        self.level_bits = (angle_bits + 1,) + (max(1, angle_bits - 1),) * (self.n_transform_levels - 1)
        
        self.R = _random_orthogonal(d, seed)
        
        # Analytically derive codebooks (Data-Free!)
        self.codebooks = [
            _polar_angle_codebook(level, bits)
            for level, bits in enumerate(self.level_bits, start=1)
        ]

    def fit(self, X: np.ndarray) -> "PolarQuant":
        """Data-free implementation. No fitting required."""
        return self

    def _quantize_level(self, angles: np.ndarray, level: int) -> np.ndarray:
        cb = self.codebooks[level - 1]
        diffs = np.abs(angles[..., None] - cb)
        if level == 1:
            diffs = np.minimum(diffs, (2.0 * math.pi) - diffs)
        return diffs.argmin(axis=-1).astype(np.uint16)

    def encode(self, X: np.ndarray) -> dict:
        X = np.asarray(X, dtype=np.float32)
        radii = X @ self.R.T
        
        packed_levels = []
        for level in range(1, self.n_transform_levels + 1):
            pairs = radii.reshape(*radii.shape[:-1], radii.shape[-1] // 2, 2)
            angles = np.arctan2(pairs[..., 1], pairs[..., 0])
            
            if level == 1:
                # Level 1 pairs are real numbers, angle is full circle
                angles = np.where(angles < 0, angles + 2.0 * math.pi, angles)
            else:
                # Level > 1 pairs are radii (strictly non-negative), angle naturally in [0, pi/2]
                pass
            
            indices = self._quantize_level(angles, level)
            packed_levels.append(indices)
            radii = np.linalg.norm(pairs, axis=-1)
            
        magnitudes = radii.squeeze(-1)  # (N,)
        return {"magnitudes": magnitudes, "angle_codes": packed_levels}

    def decode(self, code: dict) -> np.ndarray:
        radii = code["magnitudes"][..., None]
        
        # Work backwards from the deepest level up
        for indices, cb in zip(reversed(code["angle_codes"]), reversed(self.codebooks)):
            angles = cb[indices]
            a = radii * np.cos(angles)
            b = radii * np.sin(angles)
            stacked = np.stack([a, b], axis=-1)
            radii = stacked.reshape(stacked.shape[0], -1)
            
        return (radii @ self.R).astype(np.float32)

    def ip_estimate(self, Q: np.ndarray, code: dict) -> np.ndarray:
        Q = np.asarray(Q, dtype=np.float32)
        X_hat = self.decode(code)
        return Q @ X_hat.T

    def bytes_per_vector(self) -> float:
        # Magnitude is 32 bits (4 bytes)
        total_bits = 32
        
        # Sum bits across all levels
        angles_in_level = self.d // 2
        for bits in self.level_bits:
            total_bits += angles_in_level * bits
            angles_in_level //= 2
            
        return total_bits / 8.0

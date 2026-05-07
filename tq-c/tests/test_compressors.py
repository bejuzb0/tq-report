"""Sanity tests for compressors.

These tests are intentionally not downstream-retrieval tests. They check that:

1. Shapes are correct.
2. Estimates are finite.
3. Inner-product estimates correlate with exact inner products.
4. Distortion decreases with bit-width.
5. TurboQuantMSE roughly matches the small-bit distortion scale reported in
   the TurboQuant paper.

Run:
    pytest tests/

Or just:
    pytest -q tests/test_compressors.py
"""
from __future__ import annotations

import numpy as np
import pytest

from src.compressors import (
    FaissPQ,
    PolarQuant,
    QJL,
    TurboQuant,
    TurboQuantMSE,
    Uncompressed,
)


D = 512
N_DB = 2000
N_Q = 100
SEED = 0


def _fixture():
    rng = np.random.default_rng(SEED)

    X = rng.standard_normal((N_DB, D)).astype(np.float32)
    X /= np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-12)

    Q = rng.standard_normal((N_Q, D)).astype(np.float32)
    Q /= np.maximum(np.linalg.norm(Q, axis=1, keepdims=True), 1e-12)

    true_ip = Q @ X.T
    return X, Q, true_ip


def _correlation(est: np.ndarray, truth: np.ndarray) -> float:
    e = est.ravel()
    t = truth.ravel()
    return float(np.corrcoef(e, t)[0, 1])


def _mse(X: np.ndarray, X_hat: np.ndarray) -> float:
    return float(np.mean(np.sum((X - X_hat) ** 2, axis=1)))


def test_uncompressed_exact():
    X, Q, true_ip = _fixture()

    c = Uncompressed(D).fit(X)
    code = c.encode(X)
    est = c.ip_estimate(Q, code)

    assert est.shape == true_ip.shape
    assert np.allclose(est, true_ip, atol=1e-5)
    assert c.bytes_per_vector() == 4.0 * D


@pytest.mark.parametrize("bits_per_dim", [1, 2, 4, 8])
def test_qjl_correlation(bits_per_dim):
    X, Q, true_ip = _fixture()

    c = QJL(D, bits_per_dim=bits_per_dim, seed=SEED).fit(X)
    code = c.encode(X)
    est = c.ip_estimate(Q, code)

    assert est.shape == true_ip.shape
    assert np.isfinite(est).all()

    r = _correlation(est, true_ip)
    print(f"QJL {bits_per_dim}b: r={r:.3f}")

    # On random unit Gaussian vectors, true IPs have small spread ~1/sqrt(d),
    # so QJL's correlation is naturally much lower than it is on real CLIP
    # embeddings. These thresholds are sanity checks, not quality targets.
    threshold = {
        1: 0.50,
        2: 0.63,
        4: 0.76,
        8: 0.86,
    }[bits_per_dim]
    assert r > threshold, f"QJL@{bits_per_dim}b correlation {r:.3f} below {threshold}"


def test_qjl_correlation_improves_with_bits():
    X, Q, true_ip = _fixture()

    rs = []
    for bits in [1, 2, 4, 8]:
        c = QJL(D, bits_per_dim=bits, seed=SEED).fit(X)
        est = c.ip_estimate(Q, c.encode(X))
        rs.append(_correlation(est, true_ip))

    print(f"QJL correlations by bits [1,2,4,8]: {rs}")

    assert rs[0] < rs[-1]
    assert rs[-1] > 0.86


@pytest.mark.parametrize("angle_bits", [2, 3, 4])
def test_polarquant_correlation(angle_bits):
    X, Q, true_ip = _fixture()

    c = PolarQuant(D, angle_bits=angle_bits, seed=SEED).fit(X)
    code = c.encode(X)
    est = c.ip_estimate(Q, code)

    assert est.shape == true_ip.shape
    assert np.isfinite(est).all()

    r = _correlation(est, true_ip)
    print(f"PolarQuant {angle_bits}b: r={r:.3f}")

    threshold = {
        2: 0.82,
        3: 0.91,
        4: 0.95,
    }[angle_bits]
    assert r > threshold, f"PolarQuant@{angle_bits}b correlation {r:.3f} below {threshold}"


@pytest.mark.parametrize("bits", [1, 2, 3, 4])
def test_turbo_mse_distortion_matches_paper_scale(bits):
    X, _, _ = _fixture()

    c = TurboQuantMSE(D, bits=bits, seed=SEED)
    code = c.encode(X)
    X_hat = c.decode(code)

    err = _mse(X, X_hat)
    print(f"TurboQuantMSE {bits}b MSE: {err:.4f}")

    # Loose windows around TurboQuant paper values for unit vectors:
    # b=1 ~0.36, b=2 ~0.117, b=3 ~0.03, b=4 ~0.009.
    lo_hi = {
        1: (0.25, 0.50),
        2: (0.07, 0.18),
        3: (0.015, 0.07),
        4: (0.003, 0.025),
    }[bits]
    lo, hi = lo_hi
    assert lo < err < hi, f"TurboQuantMSE@{bits}b MSE {err:.4f} outside ({lo}, {hi})"


def test_turbo_mse_distortion_decreases_with_bits():
    X, _, _ = _fixture()

    mses = []
    for bits in [1, 2, 3, 4]:
        c = TurboQuantMSE(D, bits=bits, seed=SEED)
        X_hat = c.decode(c.encode(X))
        mses.append(_mse(X, X_hat))

    print(f"TurboQuantMSE MSEs by bits [1,2,3,4]: {mses}")

    assert mses[0] > mses[1] > mses[2] > mses[3]


@pytest.mark.parametrize("total_bits", [1, 2, 3, 4])
def test_turboquant_correlation(total_bits):
    X, Q, true_ip = _fixture()

    c = TurboQuant(D, total_bits=total_bits, seed=SEED).fit(X)
    code = c.encode(X)
    est = c.ip_estimate(Q, code)

    assert est.shape == true_ip.shape
    assert np.isfinite(est).all()

    r = _correlation(est, true_ip)
    print(f"TurboQuant total_bits={total_bits}: r={r:.3f}")

    # Paper-faithful TurboQuant is unbiased via QJL residual correction, but
    # it still has variance. These are sanity thresholds on random unit data.
    threshold = {
        1: 0.50,
        2: 0.70,
        3: 0.83,
        4: 0.91,
    }[total_bits]
    assert r > threshold, f"TurboQuant@{total_bits}b correlation {r:.3f} below {threshold}"


def test_turboquant_correlation_improves_with_bits():
    X, Q, true_ip = _fixture()

    rs = []
    for total_bits in [1, 2, 3, 4]:
        c = TurboQuant(D, total_bits=total_bits, seed=SEED).fit(X)
        est = c.ip_estimate(Q, c.encode(X))
        rs.append(_correlation(est, true_ip))

    print(f"TurboQuant correlations by total bits [1,2,3,4]: {rs}")

    assert rs[0] < rs[-1]
    assert rs[-1] > 0.91


def test_turboquant_backward_compatible_constructor():
    X, Q, true_ip = _fixture()

    # Old API: angle_bits means MSE-stage bits, qjl_bits_per_dim is residual bits.
    c = TurboQuant(D, angle_bits=2, qjl_bits_per_dim=1.0, seed=SEED).fit(X)
    est = c.ip_estimate(Q, c.encode(X))

    assert est.shape == true_ip.shape
    assert np.isfinite(est).all()

    r = _correlation(est, true_ip)
    print(f"TurboQuant old API angle_bits=2 + QJL1: r={r:.3f}")
    assert r > 0.83


def test_faiss_pq_correlation():
    pytest.importorskip("faiss")

    X, Q, true_ip = _fixture()

    c = FaissPQ(D, bits_per_dim=4, seed=SEED).fit(X)
    code = c.encode(X)
    est = c.ip_estimate(Q, code)

    assert est.shape == true_ip.shape
    assert np.isfinite(est).all()

    r = _correlation(est, true_ip)
    print(f"FAISS PQ 4b: r={r:.3f}")
    assert r > 0.90
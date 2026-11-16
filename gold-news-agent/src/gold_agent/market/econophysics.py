"""Econophysics: the statistical physics of price series (numpy only).

Every estimator here recovers what a known series was built with, and every reading that
could fire on noise is calibrated on random walks (tests/test_econophysics.py):

Tails
    Hill estimator of the power-law tail of returns (most markets show alpha near 3, the
    "inverse cubic law"), and the Student-t degrees of freedom of the GARCH-standardised
    returns by maximum likelihood. The odds simulation draws Student-t bars with that nu, so a
    stop is hit by a jump as often as the data says, not as a normal curve says.
Order
    Permutation entropy (Bandt-Pompe): 1 = as patternless as noise, lower = ordinal patterns
    that repeat (structure a forecast can use).
Multifractality
    MF-DFA (detrended fluctuation analysis): generalised Hurst exponents h(q). The spread
    h(-3) - h(3) grows when small and large moves scale differently: intermittent, turbulent
    trading. h(2) is the classic Hurst exponent, robust to local trends.
Mean reversion
    Ornstein-Uhlenbeck process (the Langevin equation with a restoring force), fitted by AR(1)
    regression; the Dickey-Fuller t decides whether the pull back to the mean is real.
Aftershocks
    Hawkes process (the self-exciting point process seismologists use for aftershocks, Omori's
    law): big moves trigger more big moves. The branching ratio is the share of big moves the
    market made itself (endogeneity, "reflexivity"); the intensity now against the average
    says whether an aftershock sequence is running.
Information flow
    Transfer entropy (Schreiber): bits a related market's last move carries about gold's next
    minute beyond gold's own past, tested against shuffled copies.
Correlations
    Random matrix theory: eigenvalues of the related markets' correlation matrix above the
    Marchenko-Pastur edge are real common factors, the rest is noise; the strongest one's
    share of the variance says how much the markets move as one.
Bubbles
    LPPLS (Sornette): faster-than-exponential growth with log-periodic oscillations before a
    critical time. The confidence is the share of fitting windows whose best fit passes the
    published filters. Daily, slow and experimental: a warning, never a signal.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

Array = NDArray[np.float64]
Series = Sequence[float] | Array

# ------------------------------------------------------------------ tails

TAIL_SHARE = 0.05  # the largest 5% of moves form the tail
ALPHA_VERY_FAT, ALPHA_FAT = 3.5, 4.5  # Hill on 600 bars: Student-t(3) ~2.7, normal ~6.0
NU_GRID = (2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0, 10.0, 15.0, 20.0, 30.0, 60.0)
NU_NORMAL = 30.0  # from here on a Student-t is indistinguishable from a normal curve


def hill_alpha(values: Array, share: float = TAIL_SHARE, min_tail: int = 10) -> float | None:
    """Tail index of |values| from the largest `share` of them: P(|X| > x) ~ x^-alpha."""
    x = np.sort(np.abs(values[np.isfinite(values)]))[::-1]
    x = x[x > 0]
    k = int(share * len(x))
    if k < min_tail or len(x) <= k:
        return None
    mean = float(np.log(x[:k] / x[k]).mean())
    return 1.0 / mean if mean > 0 else None


def student_nu(standardised: Array) -> float | None:
    """Degrees of freedom of a unit-variance Student-t fitted by maximum likelihood over
    NU_GRID; None when the best fit is a normal curve (or there are too few values)."""
    z = standardised[np.isfinite(standardised)]
    if len(z) < 200:
        return None
    z = (z - z.mean()) / z.std()
    best_nu, best_ll = None, -math.inf
    for nu in NU_GRID:
        scale = nu - 2.0
        const = math.lgamma((nu + 1) / 2) - math.lgamma(nu / 2) - 0.5 * math.log(math.pi * scale)
        ll = len(z) * const - (nu + 1) / 2 * float(np.log1p(z * z / scale).sum())
        if ll > best_ll:
            best_nu, best_ll = nu, ll
    return best_nu if best_nu is not None and best_nu < NU_NORMAL else None


# ------------------------------------------------------------------ order and scaling

PE_ORDER = 4
PE_STRUCTURED = 0.989  # below this, 600 bars of iid noise land less than 1% of the time


def permutation_entropy(values: Array, order: int = PE_ORDER) -> float | None:
    """Normalised permutation entropy of `values` (use returns): 1 for iid noise."""
    patterns = math.factorial(order)
    if len(values) < 4 * patterns + order:
        return None
    windows = np.lib.stride_tricks.sliding_window_view(values, order)
    codes = np.argsort(windows, axis=1, kind="stable") @ (order ** np.arange(order))
    _, counts = np.unique(codes, return_counts=True)
    p = counts / counts.sum()
    return float(-(p * np.log(p)).sum() / math.log(patterns))


MF_QS = (-3.0, 2.0, 3.0)
MF_MODERATE, MF_STRONG = 0.12, 0.25  # width: 95th percentile of noise; a multiplicative cascade


@dataclass(frozen=True)
class Multifractal:
    h2: float  # the Hurst exponent by DFA: 0.5 noise-like, above persistent, below anti-persistent
    width: float  # h(-3) - h(3): 0 for a monofractal, larger = more intermittent


def mfdfa(returns: Array, min_scale: int = 10, scales: int = 8) -> Multifractal | None:
    """Multifractal detrended fluctuation analysis (linear detrending, segments taken from
    both ends) of a return series."""
    n = len(returns)
    if n < 200:
        return None
    profile = np.cumsum(returns - returns.mean())
    sizes = np.unique(np.round(np.geomspace(min_scale, n // 4, scales)).astype(int))
    logs: dict[float, list[float]] = {q: [] for q in MF_QS}
    for s in sizes:
        count = n // s
        segments = np.vstack(
            [profile[: count * s].reshape(count, s), profile[n - count * s :].reshape(count, s)]
        )
        t = np.arange(s, dtype=np.float64) - (s - 1) / 2
        centred = segments - segments.mean(axis=1, keepdims=True)
        slopes = (centred * t).sum(axis=1, keepdims=True) / float((t * t).sum())
        f2 = ((centred - slopes * t) ** 2).mean(axis=1)
        f2 = np.maximum(f2, 1e-6 * float(np.median(f2)) + 1e-300)
        for q in MF_QS:
            logs[q].append(float(np.log(np.mean(f2 ** (q / 2)) ** (1 / q))))
    if len(sizes) < 4:
        return None
    x = np.log(sizes.astype(np.float64))
    h = {q: float(np.polyfit(x, np.asarray(logs[q]), 1)[0]) for q in MF_QS}
    return Multifractal(h2=h[2.0], width=h[-3.0] - h[3.0])


# ------------------------------------------------------------------ mean reversion

DF_CRITICAL = -2.87  # Dickey-Fuller 5% with a constant, n ~ 500 (MacKinnon)


@dataclass(frozen=True)
class OrnsteinUhlenbeck:
    theta: float  # pull back to the mean, per bar (0 = none)
    mean: float  # the level it pulls towards
    t: float  # Dickey-Fuller t of the pull: below DF_CRITICAL it is real

    @property
    def significant(self) -> bool:
        return self.theta > 0 and self.t <= DF_CRITICAL

    @property
    def half_life(self) -> float | None:
        return math.log(2) / self.theta if self.theta > 0 else None


def ou_fit(levels: Series) -> OrnsteinUhlenbeck | None:
    """dX = theta (mean - X) dt + sigma dW from x[t+1] = a + b x[t] + e."""
    x = np.asarray(levels, dtype=np.float64)
    if len(x) < 100:
        return None
    y, z = x[1:], x[:-1]
    zc = z - z.mean()
    szz = float((zc * zc).sum())
    if szz <= 0:
        return None
    b = float((zc * (y - y.mean())).sum()) / szz
    a = float(y.mean()) - b * float(z.mean())
    resid = y - a - b * z
    se = math.sqrt(float((resid * resid).sum()) / (len(y) - 2) / szz)
    if se <= 0:
        return None
    reverting = 0 < b < 1
    return OrnsteinUhlenbeck(
        theta=-math.log(b) if reverting else 0.0,
        mean=a / (1 - b) if reverting else float(x[-1]),
        t=(b - 1) / se,
    )


# ------------------------------------------------------------------ aftershocks (Hawkes)

BIG_MOVE_QUANTILE = 0.99  # a "big move": a 1-minute move in the largest 1% of the last days
AFTERSHOCK_HIGH = 2.0  # big-move intensity this many times its average: a sequence is running
HAWKES_LR = 9.21  # likelihood-ratio threshold (chi-square, 2 d.f., 1%): clustering is real
_BRANCHING = np.linspace(0.0, 0.95, 20)
_DECAYS = np.geomspace(1 / 240, 1 / 2, 16)  # per minute: aftershock half-lives 1.4 min .. 2.8 h


@dataclass(frozen=True)
class HawkesFit:
    mu: float  # background big moves per minute
    branching: float  # share of big moves triggered by earlier ones (endogeneity)
    decay: float  # how fast an aftershock sequence fades, per minute
    loglik: float
    poisson_loglik: float  # the same events as a memoryless (Poisson) process

    @property
    def clustered(self) -> bool:
        return 2 * (self.loglik - self.poisson_loglik) >= HAWKES_LR


def hawkes_fit(times: Series, horizon: float) -> HawkesFit | None:
    """Maximum likelihood fit of an exponential-kernel Hawkes process to event times in
    [0, horizon): lambda(t) = mu + n * beta * sum exp(-beta (t - t_i)), n < 1 (stationary).
    Grid over (n, beta); mu is solved exactly for each pair (the likelihood is concave in it)."""
    t = np.sort(np.asarray(times, dtype=np.float64))
    events = len(t)
    if events < 20 or horizon <= float(t[-1]):
        return None
    excite = np.zeros((len(_DECAYS), events))
    gaps = np.diff(t)
    for i in range(1, events):
        excite[:, i] = np.exp(-_DECAYS * gaps[i - 1]) * (1.0 + excite[:, i - 1])
    tails = (1.0 - np.exp(-np.outer(_DECAYS, horizon - t))).sum(axis=1)
    c = _BRANCHING[:, None, None] * _DECAYS[None, :, None] * excite[None, :, :]
    lo = np.full(c.shape[:2], 1e-15)
    hi = np.full(c.shape[:2], events / horizon)
    for _ in range(50):  # sum 1/(mu + c_i) = horizon, decreasing in mu
        mid = 0.5 * (lo + hi)
        above = (1.0 / (mid[..., None] + c)).sum(axis=-1) > horizon
        lo, hi = np.where(above, mid, lo), np.where(above, hi, mid)
    mu = 0.5 * (lo + hi)
    loglik = np.log(mu[..., None] + c).sum(axis=-1) - mu * horizon - _BRANCHING[:, None] * tails
    best = np.unravel_index(int(np.argmax(loglik)), loglik.shape)
    rate = events / horizon
    return HawkesFit(
        mu=float(mu[best]),
        branching=float(_BRANCHING[best[0]]),
        decay=float(_DECAYS[best[1]]),
        loglik=float(loglik[best]),
        poisson_loglik=events * math.log(rate) - events,
    )


@dataclass(frozen=True)
class Aftershocks:
    fit: HawkesFit
    events: int
    minutes: int
    threshold_pct: float  # what counts as a big 1-minute move, in percent
    intensity_ratio: float  # big-move rate now / the average rate
    next_hour: float  # P(at least one big move in the next 60 minutes), without new triggers


def aftershocks(minutes: Series, values: Series, max_gap: float = 5.0) -> Aftershocks | None:
    """Hawkes reading of a minute series (`minutes` since any origin, ascending; the last value
    of each minute): big moves are the largest 1% of the log moves between points at most
    `max_gap` minutes apart (a weekend gap is not a move); time runs in trading minutes, the
    position in the series, so closed markets do not count as calm."""
    x = np.asarray(values, dtype=np.float64)
    if len(x) < 1000 or np.any(x <= 0):
        return None
    steps = np.diff(np.asarray(minutes, dtype=np.float64))
    moves = np.abs(np.diff(np.log(x)))
    valid = steps <= max_gap
    if int(valid.sum()) < 1000:
        return None
    threshold = float(np.quantile(moves[valid], BIG_MOVE_QUANTILE))
    if threshold <= 0:
        return None
    times = np.flatnonzero(valid & (moves > threshold)) + 1.0
    horizon = float(len(x))
    fit = hawkes_fit(times.tolist(), horizon)
    if fit is None:
        return None
    since = horizon - times
    memory = float(np.exp(-fit.decay * since).sum())
    now = fit.mu + fit.branching * fit.decay * memory
    expected = fit.mu * 60 + fit.branching * memory * (1 - math.exp(-fit.decay * 60))
    return Aftershocks(
        fit=fit,
        events=len(times),
        minutes=len(x),
        threshold_pct=100 * (math.exp(threshold) - 1),
        intensity_ratio=now / (len(times) / horizon),
        next_hour=1 - math.exp(-expected),
    )


# ------------------------------------------------------------------ information flow

TE_BINS = 3
TE_SHUFFLES = 30
LEAD_Z = 3.0  # transfer entropy this many surrogate s.d. above shuffled copies
LEAD_CORR_Z = 2.5  # and a lag-1 correlation this many robust standard errors from zero


def _symbols(values: Array, bins: int) -> NDArray[np.int64]:
    edges = np.quantile(values, np.linspace(0, 1, bins + 1)[1:-1])
    return np.searchsorted(edges, values, side="right").astype(np.int64)


def _te(source: NDArray[np.int64], target: NDArray[np.int64], bins: int) -> float:
    """TE(source -> target) in bits, one step: p(y1 | y0, x0) against p(y1 | y0)."""
    y1, y0, x0 = target[1:], target[:-1], source[:-1]
    joint = np.bincount((y1 * bins + y0) * bins + x0, minlength=bins**3).reshape(bins, bins, bins)
    joint = joint / joint.sum()
    y0x0 = joint.sum(axis=0)
    y1y0 = joint.sum(axis=2)
    y0 = joint.sum(axis=(0, 2))
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = joint * y0[None, :, None] / (y1y0[:, :, None] * y0x0[None, :, :])
        terms = np.where(joint > 0, joint * np.log2(ratio), 0.0)
    return float(terms.sum())


@dataclass(frozen=True)
class InformationFlow:
    bits: float  # TE(source -> gold) per step
    reverse_bits: float  # TE(gold -> source)
    z: float  # against shuffled copies of the source
    corr: float  # corr(source move, gold's next move): the direction of the lead
    corr_z: float  # that correlation against zero, robust to volatility clustering
    pairs: int

    @property
    def leads(self) -> bool:
        """Information about gold's next move that is directional (not only a volatility
        spillover) and flows mostly from the source to gold."""
        return (
            self.z >= LEAD_Z and abs(self.corr_z) >= LEAD_CORR_Z and self.bits > self.reverse_bits
        )


def information_flow(
    source: Array, target: Array, *, shuffles: int = TE_SHUFFLES, seed: int = 11
) -> InformationFlow | None:
    """How much `source`'s move tells about `target`'s next move (aligned return series)."""
    n = min(len(source), len(target))
    if n < 300:
        return None
    x, y = source[-n:], target[-n:]
    if float(x.std()) == 0 or float(y.std()) == 0:
        return None
    xs, ys = _symbols(x, TE_BINS), _symbols(y, TE_BINS)
    bits = _te(xs, ys, TE_BINS)
    rng = np.random.default_rng(seed)
    null = np.array([_te(rng.permutation(xs), ys, TE_BINS) for _ in range(shuffles)])
    spread = max(float(null.std()), 1e-12)
    products = (x[:-1] - x[:-1].mean()) * (y[1:] - y[1:].mean())
    robust = math.sqrt(float((products * products).sum()))  # White's standard error
    return InformationFlow(
        bits=bits,
        reverse_bits=_te(ys, xs, TE_BINS),
        z=(bits - float(null.mean())) / spread,
        corr=float(np.corrcoef(x[:-1], y[1:])[0, 1]),
        corr_z=float(products.sum()) / robust if robust > 0 else 0.0,
        pairs=n - 1,
    )


# ------------------------------------------------------------------ random matrix theory


@dataclass(frozen=True)
class MarketMode:
    factors: int  # eigenvalues above the Marchenko-Pastur edge: real common factors
    share: float  # the strongest factor's share of the total variance
    gold_loading: float  # gold's correlation with that factor
    markets: int
    observations: int


def market_mode(returns: Array, gold_column: int = 0) -> MarketMode | None:
    """Eigen-decomposition of the correlation matrix of aligned returns (rows = time)."""
    rows, cols = returns.shape
    if cols < 3 or rows < 5 * cols:
        return None
    sd = returns.std(axis=0)
    if np.any(sd == 0):
        return None
    z = (returns - returns.mean(axis=0)) / sd
    values, vectors = np.linalg.eigh(z.T @ z / rows)
    order = np.argsort(values)[::-1]
    values, vectors = values[order], vectors[:, order]
    mode = vectors[:, 0] * (1 if vectors[:, 0].sum() >= 0 else -1)
    edge = (1 + math.sqrt(cols / rows)) ** 2
    return MarketMode(
        factors=int((values > edge).sum()),
        share=float(values[0] / cols),
        gold_loading=float(mode[gold_column] * math.sqrt(values[0])),
        markets=cols,
        observations=rows,
    )


# ------------------------------------------------------------------ bubbles (LPPLS)

LPPLS_WINDOWS = tuple(range(60, 260, 10))  # trading days
_M = np.linspace(0.1, 0.9, 9)
_OMEGA = np.linspace(4.0, 14.0, 11)
_TC_STEPS = 6
LPPLS_MIN_DAMPING = 0.5  # the published filter: the oscillations may not dominate the trend
LPPLS_MIN_GAIN = 0.5  # and the fit must explain half of what plain exponential growth leaves
BUBBLE_ALERT = 0.3  # share of windows: random walks reach it ~2% of the time, steady trends ~7%


@dataclass(frozen=True)
class LpplsFit:
    b: float  # < 0: faster-than-exponential rise (a bubble); > 0: a falling "anti-bubble"
    m: float
    omega: float
    days_to_tc: float
    damping: float
    gain: float  # 1 - SSE(LPPLS) / SSE(exponential trend)
    interior: bool  # m and omega inside the grid: on random walks the best fit runs to its edge

    @property
    def qualified(self) -> bool:
        return self.interior and self.damping >= LPPLS_MIN_DAMPING and self.gain >= LPPLS_MIN_GAIN


def lppls_fit(log_prices: Array) -> LpplsFit | None:
    """Best LPPLS fit of one window: ln p = A + B f + C1 f cos(w ln dt) + C2 f sin(w ln dt),
    f = dt^m, dt = tc - t, over a grid of (tc, m, w) with the linear part solved exactly."""
    size = len(log_prices)
    if size < 30:
        return None
    t = np.arange(size, dtype=np.float64)
    deltas = np.linspace(1.0, max(1.0, 0.1 * size), _TC_STEPS)
    dt = (size - 1 + deltas)[:, None] - t[None, :]  # (tc, time), all > 0
    f = dt[:, None, :] ** _M[None, :, None]  # (tc, m, time)
    phase = _OMEGA[None, :, None] * np.log(dt)[:, None, :]  # (tc, w, time)
    fc = f[:, :, None, :] * np.cos(phase)[:, None, :, :]
    fs = f[:, :, None, :] * np.sin(phase)[:, None, :, :]
    ff = np.broadcast_to(f[:, :, None, :], fc.shape)
    design = np.stack([np.ones_like(fc), ff, fc, fs], axis=-1)  # (tc, m, w, time, 4)
    gram = np.einsum("...ti,...tj->...ij", design, design) + 1e-10 * np.eye(4)
    rhs = np.einsum("...ti,t->...i", design, log_prices)
    coef = np.linalg.solve(gram, rhs[..., None])[..., 0]
    resid = log_prices - np.einsum("...ti,...i->...t", design, coef)
    sse = (resid * resid).sum(axis=-1)
    i_tc, i_m, i_w = (int(i) for i in np.unravel_index(int(np.argmin(sse)), sse.shape))
    _, b, c1, c2 = (float(v) for v in coef[i_tc, i_m, i_w])
    trend = np.polyfit(t, log_prices, 1)
    linear_sse = float(((log_prices - np.polyval(trend, t)) ** 2).sum())
    amplitude = math.hypot(c1, c2)
    m, omega = float(_M[i_m]), float(_OMEGA[i_w])
    return LpplsFit(
        b=b,
        m=m,
        omega=omega,
        days_to_tc=float(deltas[i_tc]),
        damping=m * abs(b) / (omega * amplitude) if amplitude > 0 else math.inf,
        gain=1 - float(sse[i_tc, i_m, i_w]) / linear_sse if linear_sse > 0 else 0.0,
        interior=0 < i_m < len(_M) - 1 and 0 < i_w < len(_OMEGA) - 1,
    )


@dataclass(frozen=True)
class BubbleRead:
    bubble: float  # share of windows with a qualified positive-bubble fit, 0..1
    anti_bubble: float  # the same for a falling anti-bubble (a rebound may be near)
    days_to_tc: float | None  # median trading days to the critical time of the bubble fits
    windows: int


def bubble_confidence(closes: Series) -> BubbleRead | None:
    """LPPLS confidence over windows ending today (DS LPPLS, simplified)."""
    y = np.log(np.asarray(closes, dtype=np.float64))
    usable = [w for w in LPPLS_WINDOWS if w <= len(y)]
    if not usable or np.any(~np.isfinite(y)):
        return None
    bubbles: list[float] = []
    anti = 0
    for window in usable:
        fit = lppls_fit(y[-window:])
        if fit is None or not fit.qualified:
            continue
        if fit.b < 0:
            bubbles.append(fit.days_to_tc)
        else:
            anti += 1
    return BubbleRead(
        bubble=len(bubbles) / len(usable),
        anti_bubble=anti / len(usable),
        days_to_tc=float(np.median(bubbles)) if bubbles else None,
        windows=len(usable),
    )

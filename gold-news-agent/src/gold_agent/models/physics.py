"""Market-wide econophysics readings (market/physics.py) carried on the market context."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Aftershock(BaseModel):
    """Hawkes process of gold's big 1-minute moves (self-excitation, as in aftershocks)."""

    branching: float  # share of big moves triggered by earlier ones (endogeneity), 0..1
    clustered: bool  # self-excitation is statistically real (likelihood ratio, 1%)
    intensity_ratio: float  # big-move rate now / the average rate
    next_hour: float  # P(at least one big move within 60 minutes), without new triggers
    half_life_minutes: float  # how fast an aftershock sequence fades
    events: int
    minutes: int
    threshold_pct: float  # a big move, in percent per minute


class Leader(BaseModel):
    """A related market whose last move carries information about gold's next minute."""

    key: str
    label: str
    bits: float  # transfer entropy to gold, bits per minute
    reverse_bits: float  # gold's to it
    z: float  # against shuffled copies
    corr: float  # corr(its move, gold's next move): the sign of the lead
    leads: bool
    last_move_pct: float | None = None  # its latest 1-minute move
    implied: int = 0  # +1 gold up, -1 gold down, 0 nothing implied (no proven lead or no move)


class CommonFactor(BaseModel):
    """Random matrix theory on 5-minute returns of gold and the related markets."""

    factors: int  # eigenvalues above the Marchenko-Pastur edge: real common factors
    share: float  # the strongest factor's share of the variance
    gold_loading: float  # gold's correlation with that factor
    markets: int
    observations: int


class BubbleRisk(BaseModel):
    """LPPLS confidence on daily closes (experimental, a slow warning)."""

    bubble: float  # share of windows with a qualified faster-than-exponential rise, 0..1
    anti_bubble: float  # the same for a falling anti-bubble
    days_to_tc: float | None = None  # median trading days to the critical time
    windows: int
    as_of: str | None = None  # the last daily bar used


class MarketPhysics(BaseModel):
    built_at: datetime
    aftershock: Aftershock | None = None
    leaders: list[Leader] = Field(default_factory=lambda: list[Leader]())
    common: CommonFactor | None = None
    bubble: BubbleRisk | None = None

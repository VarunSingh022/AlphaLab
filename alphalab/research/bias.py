"""Deterministic evaluation of research biases."""

from dataclasses import dataclass

from alphalab.research.protocol import ResearchPayload


@dataclass(frozen=True, slots=True)
class BiasReport:
    look_ahead_risk: float
    survivorship_risk: float
    overfitting_risk: float
    sample_bias_risk: float
    overall_bias_score: float


def detect_bias(payload: ResearchPayload) -> BiasReport:
    """Evaluates the payload for statistical patterns indicative of bias."""
    # Look-ahead proxy: Unrealistic win rates paired with short durations
    win_rate = (
        sum(1 for t in payload.trades if t.pnl > 0) / len(payload.trades) if payload.trades else 0
    )
    avg_duration = (
        sum(t.duration_seconds for t in payload.trades) / len(payload.trades)
        if payload.trades
        else 0
    )

    look_ahead = 1.0 if (win_rate > 0.95 and avg_duration < 3600) else (win_rate - 0.5) * 2.0
    look_ahead = max(0.0, min(1.0, look_ahead))

    # Survivorship proxy: Extremely low volatility in a broad market regime
    from alphalab.research.metrics import calculate_volatility

    vol = calculate_volatility(payload.returns)
    survivorship = 1.0 if vol < 0.05 else max(0.0, 1.0 - (vol * 5.0))

    # Overfitting: Ratio of parameters to trades
    param_count = len(payload.parameters)
    trade_count = len(payload.trades)
    overfitting = min(1.0, (param_count * 100) / trade_count) if trade_count > 0 else 1.0

    # Sample bias: Too few returns
    sample_bias = (
        1.0 if len(payload.returns) < 252 else max(0.0, 1.0 - (len(payload.returns) / 1000.0))
    )

    score = 100.0 - ((look_ahead + survivorship + overfitting + sample_bias) / 4.0) * 100.0

    return BiasReport(
        look_ahead_risk=round(look_ahead, 4),
        survivorship_risk=round(survivorship, 4),
        overfitting_risk=round(overfitting, 4),
        sample_bias_risk=round(sample_bias, 4),
        overall_bias_score=round(score, 2),
    )

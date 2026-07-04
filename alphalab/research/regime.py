"""Market Regime Analysis."""

from dataclasses import dataclass

from alphalab.research.metrics import calculate_sharpe
from alphalab.research.protocol import ResearchPayload


@dataclass(frozen=True, slots=True)
class RegimeReport:
    bull_sharpe: float
    bear_sharpe: float
    sideways_sharpe: float
    regime_generalisation_score: float

def analyze_regimes(payload: ResearchPayload) -> RegimeReport:
    """Separates returns into regimes and evaluates generalisation."""
    if not payload.returns or len(payload.returns) != len(payload.market_regimes):
        return RegimeReport(0.0, 0.0, 0.0, 0.0)
        
    regime_returns: dict[str, list[float]] = {"BULL": [], "BEAR": [], "SIDEWAYS": []}
    
    for ret, regime in zip(payload.returns, payload.market_regimes, strict=True):
        if regime in regime_returns:
            regime_returns[regime].append(ret)
            
    bull = calculate_sharpe(regime_returns["BULL"]) if regime_returns["BULL"] else 0.0
    bear = calculate_sharpe(regime_returns["BEAR"]) if regime_returns["BEAR"] else 0.0
    side = calculate_sharpe(regime_returns["SIDEWAYS"]) if regime_returns["SIDEWAYS"] else 0.0
    
    # Score based on positive performance in at least 2 regimes
    positive_regimes = sum(1 for s in (bull, bear, side) if s > 0)
    score = (positive_regimes / 3.0) * 100.0
    
    return RegimeReport(round(bull, 4), round(bear, 4), round(side, 4), round(score, 2))
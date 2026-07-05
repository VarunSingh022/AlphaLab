"""Immutable domain events describing the Portfolio lifecycle."""

from dataclasses import dataclass

from alphalab.common.events import BaseEvent


@dataclass(frozen=True, slots=True)
class PortfolioEvent(BaseEvent):
    pass


@dataclass(frozen=True, slots=True)
class PortfolioCreated(PortfolioEvent):
    portfolio_id: str


@dataclass(frozen=True, slots=True)
class PortfolioUpdated(PortfolioEvent):
    portfolio_id: str


@dataclass(frozen=True, slots=True)
class WeightsCalculated(PortfolioEvent):
    portfolio_id: str
    optimization_method: str


@dataclass(frozen=True, slots=True)
class Rebalanced(PortfolioEvent):
    portfolio_id: str
    trigger: str


@dataclass(frozen=True, slots=True)
class AllocationChanged(PortfolioEvent):
    portfolio_id: str
    new_capital: float


@dataclass(frozen=True, slots=True)
class ConstraintViolated(PortfolioEvent):
    portfolio_id: str
    constraint_name: str
    violation_amount: float


@dataclass(frozen=True, slots=True)
class ExposureUpdated(PortfolioEvent):
    portfolio_id: str

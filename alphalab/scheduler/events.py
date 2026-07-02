"""Immutable domain events describing changes in Scheduler State."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SchedulerEvent:
    """Base class for all Scheduler events."""

    event_id: str
    timestamp: float


@dataclass(frozen=True, slots=True)
class TimerScheduled(SchedulerEvent):
    timer_id: str
    trigger_time: float
    schedule_type: str


@dataclass(frozen=True, slots=True)
class TimerTriggered(SchedulerEvent):
    timer_id: str


@dataclass(frozen=True, slots=True)
class TimerCancelled(SchedulerEvent):
    timer_id: str


@dataclass(frozen=True, slots=True)
class SessionStarted(SchedulerEvent):
    session_id: str
    phase: str


@dataclass(frozen=True, slots=True)
class SessionEnded(SchedulerEvent):
    session_id: str


@dataclass(frozen=True, slots=True)
class ClockAdvanced(SchedulerEvent):
    old_time: float
    new_time: float


@dataclass(frozen=True, slots=True)
class ClockReset(SchedulerEvent):
    reset_time: float

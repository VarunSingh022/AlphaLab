"""Schedule definitions and enumerations."""

from enum import Enum, auto


class ScheduleType(Enum):
    """How a timer repeats. Every member is implemented and kept.

    ``CRON`` reads a :class:`~alphalab.scheduler.cron.CronSchedule` on its zone's
    wall clock (v3.13); ``SESSION_OPEN`` and ``SESSION_CLOSE`` follow a
    :class:`~alphalab.data.calendar.MarketCalendar` (v3.12). ``BAR_BOUNDARY``
    was declared until v3.13 and never implemented: a bar's arrival is already
    the event a strategy reacts to (``on_bar``), and a timer guessing where a
    bar's boundary falls would restate a convention of the data
    (:class:`~alphalab.data.time.BarStamp`) and could disagree with it. It is
    removed (ledger DAT-006).
    """

    ONE_SHOT = auto()
    REPEATING = auto()
    INTERVAL = auto()
    CRON = auto()
    MANUAL = auto()
    SESSION_OPEN = auto()
    SESSION_CLOSE = auto()

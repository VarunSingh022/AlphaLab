"""Adapter translating raw strings to LogEntries and Checkpoints."""

from alphalab.production.checkpoint import Checkpoint
from alphalab.production.logging import LogEntry, LogLevel


class ProductionAdapter:
    """Stateless translator formatting unstructured inputs into immutable objects."""

    @staticmethod
    def to_log(timestamp: float, level: LogLevel, mod: str, msg: str) -> LogEntry:
        return LogEntry(timestamp, level, mod, msg, "")

    @staticmethod
    def to_checkpoint(
        cp_id: str, ts: float, run: str, port: str, ords: str, pos: str, res: str, rep: str
    ) -> Checkpoint:
        return Checkpoint(cp_id, ts, run, port, ords, pos, res, rep, {})
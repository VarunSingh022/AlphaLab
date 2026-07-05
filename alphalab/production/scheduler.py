"""Chronological orchestration matching wall-clock or replay-clock ticks."""

from dataclasses import replace

from alphalab.common.ids import new_id
from alphalab.production.events import AlertRaised, HeartbeatTimeout
from alphalab.production.heartbeat import HeartbeatStatus
from alphalab.production.monitor import create_alert
from alphalab.production.state import ProductionState
from alphalab.production.supervisor import Supervisor


class RuntimeScheduler:
    """Steps the runtime forward, evaluating timeouts and health."""

    @staticmethod
    def _create_id() -> str:
        return str(new_id())

    @staticmethod
    def tick(state: ProductionState, timestamp: float) -> ProductionState:
        if not state.is_running:
            return state

        delta = timestamp - state.last_tick if state.last_tick > 0 else 0.0
        new_uptime = state.uptime + delta

        new_hbs = dict(state.heartbeats)
        new_events = list(state.events)
        new_alerts = list(state.alerts)

        new_state = state

        # Evaluate heartbeats
        for mod_id, hb in state.heartbeats.items():
            if (
                timestamp - hb.last_ping_time > hb.expected_interval * 3
                and hb.status != HeartbeatStatus.TIMEOUT
            ):
                # Mark timeout
                updated_hb = replace(
                    hb, status=HeartbeatStatus.TIMEOUT, missed_count=hb.missed_count + 1
                )
                new_hbs[mod_id] = updated_hb

                # Raise Events & Alerts
                to_evt = HeartbeatTimeout(
                    RuntimeScheduler._create_id(), timestamp, mod_id, updated_hb.missed_count
                )
                new_events.append(to_evt)

                alert = create_alert("CRITICAL", f"Module {mod_id} heartbeat timeout.", timestamp)
                new_alerts.append(alert)
                alert_evt = AlertRaised(
                    RuntimeScheduler._create_id(),
                    timestamp,
                    alert.alert_id,
                    alert.severity,
                    alert.message,
                )
                new_events.append(alert_evt)

                # Force module to FAILED
                new_state = Supervisor.fail_module(new_state, mod_id, timestamp)

        return replace(
            new_state,
            last_tick=timestamp,
            uptime=new_uptime,
            heartbeats=new_hbs,
            alerts=tuple(new_alerts),
            events=tuple(new_events),
        )

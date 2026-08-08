"""Pure functional feature value writes, producing deterministic write decisions."""

from dataclasses import replace

from alphalab.common.ids import new_id
from alphalab.feature_store.cache import cache_value
from alphalab.feature_store.checks import (
    check_asset_scope,
    check_feature_registered,
    check_value_type,
)
from alphalab.feature_store.decision import FeatureWriteDecision
from alphalab.feature_store.events import FeatureValueRejected, FeatureValueWritten
from alphalab.feature_store.state import FeatureStoreState
from alphalab.feature_store.value import FeatureValue
from alphalab.feature_store.violations import FeatureViolation


class FeatureValueStore:
    """Stateless engine responsible for validating and writing feature values."""

    @staticmethod
    def write(
        state: FeatureStoreState, value: FeatureValue, timestamp: float
    ) -> tuple[FeatureStoreState, FeatureWriteDecision]:
        """Runs all configured value checks and returns updated state with a decision.

        A rejected write still returns updated state -- the rejection event and
        decision history are recorded, but `values` and `cache` are left unchanged.
        """
        violations: list[FeatureViolation] = []

        registered_violation = check_feature_registered(value, state)
        if registered_violation is not None:
            violations.append(registered_violation)
        else:
            key = f"{value.feature_id}:{value.version}"
            metadata = state.features[key]
            for result in (
                check_value_type(value, metadata),
                check_asset_scope(value, metadata),
            ):
                if result is not None:
                    violations.append(result)

        decision_id = str(new_id())

        if violations:
            decision = FeatureValueStore._reject(decision_id, value, timestamp, tuple(violations))
            event = FeatureValueRejected(
                str(new_id()),
                timestamp,
                decision_id,
                value.feature_id,
                value.version,
                decision.reason,
            )
            stats = replace(
                state.statistics, total_values_rejected=state.statistics.total_values_rejected + 1
            )
            return replace(
                state,
                events=(*state.events, event),
                statistics=stats,
                history=(*state.history, decision),
            ), decision

        decision = FeatureValueStore._approve(decision_id, value, timestamp)
        write_event = FeatureValueWritten(
            str(new_id()), timestamp, decision_id, value.feature_id, value.version, value.asset_id
        )

        new_values = dict(state.values)
        value_key = f"{value.feature_id}:{value.version}:{value.asset_id or '_GLOBAL'}"
        new_values[value_key] = value

        new_cache = cache_value(state.cache, value)
        stats = replace(
            state.statistics, total_values_written=state.statistics.total_values_written + 1
        )

        new_state = replace(
            state,
            values=new_values,
            cache=new_cache,
            events=(*state.events, write_event),
            statistics=stats,
            history=(*state.history, decision),
        )
        return new_state, decision

    @staticmethod
    def _approve(decision_id: str, value: FeatureValue, timestamp: float) -> FeatureWriteDecision:
        """Constructs an approved write decision."""
        return FeatureWriteDecision(
            decision_id=decision_id,
            timestamp=timestamp,
            feature_id=value.feature_id,
            version=value.version,
            asset_id=value.asset_id,
            approved=True,
            reason="All feature value checks passed.",
        )

    @staticmethod
    def _reject(
        decision_id: str,
        value: FeatureValue,
        timestamp: float,
        violations: tuple[FeatureViolation, ...],
    ) -> FeatureWriteDecision:
        """Constructs a rejected write decision containing every failed check."""
        return FeatureWriteDecision(
            decision_id=decision_id,
            timestamp=timestamp,
            feature_id=value.feature_id,
            version=value.version,
            asset_id=value.asset_id,
            approved=False,
            reason=f"Rejected due to {len(violations)} constraint violation(s).",
            violations=violations,
        )

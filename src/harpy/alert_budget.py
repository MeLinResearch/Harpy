"""The second binding constraint: human attention, denominated in alerts.

Dollars are not the only budget an oversight layer spends. A team can absorb
some number of alerts per agent-hour and no more, and a layer that squeezes
under a 10% dollar ceiling by paging someone every other minute has not squeezed
under anything — it has moved the cost somewhere the ledger cannot see. So the
alert rate is capped separately, and :meth:`ResponseController.alert_human`
consults this object before it fires.

Two things can happen at the cap. With ``threshold_adaptation`` on, the gate
that produces alerts is raised — the layer becomes more conservative about
isolating, which is the honest way to spend less attention — and decays back
down once there is headroom. With it off, the alert is simply suppressed and
counted. Either way the alert does not fire: a cap that can be exceeded is not
a cap.

This module is on HARPY's decision path, so it is guarded: nothing here may read
ground truth. Whether a suppressed alert cost a real detection is scored in
:mod:`harpy.simulation`, on the world's side of the seam.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

SECONDS_PER_HOUR = 3600.0


@dataclass(frozen=True)
class AlertBudgetConfig:
    max_alerts_per_agent_hour: float
    threshold_adaptation: bool
    threshold_step: float
    threshold_max: float
    window_ticks: int
    tick_seconds: int
    base_isolation_threshold: float

    def __post_init__(self) -> None:
        if self.max_alerts_per_agent_hour < 0.0:
            raise ValueError(
                f"max_alerts_per_agent_hour must be >= 0, got {self.max_alerts_per_agent_hour}"
            )
        if self.threshold_step < 0.0:
            raise ValueError(f"threshold_step must be >= 0, got {self.threshold_step}")
        if self.threshold_max < self.base_isolation_threshold:
            raise ValueError(
                "threshold_max must be at least the base isolation_threshold "
                f"(got {self.threshold_max} < {self.base_isolation_threshold})"
            )
        if self.window_ticks < 1:
            raise ValueError(f"window_ticks must be >= 1, got {self.window_ticks}")

    @classmethod
    def from_config(
        cls, cfg: dict, max_alerts_per_agent_hour: float | None = None
    ) -> AlertBudgetConfig:
        """Build from YAML, with the swept cap overriding the configured one."""
        budget = cfg["alert_budget"]
        configured = float(budget["max_alerts_per_agent_hour"])
        return cls(
            max_alerts_per_agent_hour=(
                configured if max_alerts_per_agent_hour is None
                else float(max_alerts_per_agent_hour)
            ),
            threshold_adaptation=bool(budget["threshold_adaptation"]),
            threshold_step=float(budget["threshold_step"]),
            threshold_max=float(budget["threshold_max"]),
            window_ticks=int(budget["window_ticks"]),
            tick_seconds=int(cfg["simulation"]["tick_seconds"]),
            base_isolation_threshold=float(cfg["suspicion"]["isolation_threshold"]),
        )


@dataclass
class _Bucket:
    """One tick's contribution to the rolling window."""

    agents: int
    alerts: int


class AlertBudget:
    """Rolling alert-rate cap, and the isolation threshold it adapts.

    The cap is checked against two rates and the alert has to clear both:

    * the **rolling** rate over the last ``window_ticks`` ticks, which is what
      lets a quiet stretch buy back the right to alert again; and
    * the **run-to-date** rate, which is what makes the ``alerts_per_agent_hour``
      reported for the run provably at or under the cap instead of approximately
      so. A sliding window alone does not give that guarantee, and a metric that
      overshoots the cap it is supposed to demonstrate is worthless.
    """

    def __init__(self, config: AlertBudgetConfig) -> None:
        self.config = config
        self.isolation_threshold = config.base_isolation_threshold
        self._window: deque[_Bucket] = deque(maxlen=max(config.window_ticks, 1))
        self._total_agent_ticks = 0
        self._alerts_fired = 0
        self._suppressed_alerts = 0
        self._threshold_sum = 0.0
        self._threshold_ticks = 0

    # -- the tick cycle ----------------------------------------------------

    def observe_tick(self, n_active_agents: int) -> None:
        """Open a tick. Call once, with the same active count the run scores on."""
        self._window.append(_Bucket(agents=max(int(n_active_agents), 0), alerts=0))
        self._total_agent_ticks += max(int(n_active_agents), 0)

    def settle(self) -> float:
        """Close a tick: bank the threshold that was in force, then adapt it.

        Adaptation happens at the end of the tick so that the threshold applied
        to a tick's isolation decisions is the one the previous tick's alert rate
        justified, never a value computed from alerts the same tick has not
        fired yet.
        """
        self._threshold_ticks += 1
        self._threshold_sum += self.isolation_threshold
        if self.config.threshold_adaptation:
            if self.at_cap():
                self.isolation_threshold = min(
                    self.isolation_threshold + self.config.threshold_step,
                    self.config.threshold_max,
                )
            elif self.isolation_threshold > self.config.base_isolation_threshold:
                self.isolation_threshold = max(
                    self.isolation_threshold - self.config.threshold_step,
                    self.config.base_isolation_threshold,
                )
        return self.isolation_threshold

    # -- the cap -----------------------------------------------------------

    def allows(self) -> bool:
        """Whether one more alert right now stays at or under the cap."""
        cap = self.config.max_alerts_per_agent_hour
        if math.isinf(cap):
            return True
        if cap <= 0.0:
            return False
        return self._rolling_rate(1) <= cap and self._cumulative_rate(1) <= cap

    def at_cap(self) -> bool:
        return not self.allows()

    def record_alert(self) -> None:
        self._alerts_fired += 1
        if self._window:
            self._window[-1].alerts += 1

    def record_suppression(self) -> None:
        self._suppressed_alerts += 1

    # -- rates -------------------------------------------------------------

    def _agent_hours(self, agent_ticks: int) -> float:
        return agent_ticks * self.config.tick_seconds / SECONDS_PER_HOUR

    def _rolling_rate(self, extra_alerts: int = 0) -> float:
        agent_hours = self._agent_hours(sum(b.agents for b in self._window))
        alerts = sum(b.alerts for b in self._window) + extra_alerts
        if agent_hours <= 0.0:
            return math.inf if alerts > 0 else 0.0
        return alerts / agent_hours

    def _cumulative_rate(self, extra_alerts: int = 0) -> float:
        agent_hours = self._agent_hours(self._total_agent_ticks)
        alerts = self._alerts_fired + extra_alerts
        if agent_hours <= 0.0:
            return math.inf if alerts > 0 else 0.0
        return alerts / agent_hours

    # -- what the run reports ----------------------------------------------

    @property
    def alerts_fired(self) -> int:
        return self._alerts_fired

    @property
    def suppressed_alerts(self) -> int:
        return self._suppressed_alerts

    def alerts_per_agent_hour(self) -> float:
        return self._cumulative_rate()

    def effective_isolation_threshold(self) -> float:
        """Time-weighted mean of the threshold actually in force.

        Equals the configured threshold exactly when the cap never binds, so it
        doubles as a read on how hard adaptation had to work.
        """
        if self._threshold_ticks <= 0:
            return self.config.base_isolation_threshold
        return self._threshold_sum / self._threshold_ticks

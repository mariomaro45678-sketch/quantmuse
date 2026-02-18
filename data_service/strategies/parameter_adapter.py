"""
Adaptive Parameter Tuning Module (Phase 5.1)

Tracks per-strategy parameter performance and slowly adjusts toward
better-performing values:
- 30-day rolling performance window
- Max 10% change per week to avoid overfitting
- Respects parameter bounds and constraints

Integrates with:
- Order Storage: Records parameter snapshots at trade time
- Trade History: Computes rolling performance metrics
- Strategies: Provides adjusted parameters via get_parameters()
"""

import logging
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from enum import Enum

logger = logging.getLogger(__name__)


class ParameterType(Enum):
    """Classification of parameter types for bounds checking."""
    THRESHOLD = "threshold"      # RSI, momentum thresholds
    PERCENTAGE = "percentage"    # Funding rates, confidence minimums
    COUNT = "count"              # Lookback periods
    MULTIPLIER = "multiplier"    # Position size multipliers


@dataclass
class ParameterSpec:
    """Specification for an adaptable parameter."""
    name: str
    param_type: ParameterType
    default_value: float
    min_value: float
    max_value: float
    step_size: float

    def clamp(self, value: float) -> float:
        """Clamp value to valid range."""
        return max(self.min_value, min(self.max_value, value))

    def round_to_step(self, value: float) -> float:
        """Round to nearest step size."""
        return round(value / self.step_size) * self.step_size


@dataclass
class ParameterSet:
    """A complete set of parameters for a strategy."""
    strategy_name: str
    parameters: Dict[str, float]
    hash: str = field(default="")

    def __post_init__(self):
        if not self.hash:
            self.hash = self._compute_hash()

    def _compute_hash(self) -> str:
        """Compute deterministic hash of parameters."""
        sorted_params = json.dumps(self.parameters, sort_keys=True)
        return hashlib.md5(sorted_params.encode()).hexdigest()[:12]

    def to_json(self) -> str:
        return json.dumps(self.parameters, sort_keys=True)

    @classmethod
    def from_json(cls, strategy_name: str, json_str: str) -> "ParameterSet":
        params = json.loads(json_str)
        return cls(strategy_name=strategy_name, parameters=params)


@dataclass
class PerformanceMetrics:
    """Performance metrics for a parameter set."""
    trade_count: int
    win_count: int
    total_pnl: float
    sharpe_30d: float
    max_drawdown_30d: float
    win_rate: float = field(init=False)

    def __post_init__(self):
        self.win_rate = self.win_count / self.trade_count if self.trade_count > 0 else 0.0


@dataclass
class AdaptationResult:
    """Result of a parameter adaptation."""
    strategy_name: str
    parameter_name: str
    old_value: float
    new_value: float
    change_pct: float
    reason: str
    confidence: float


# Strategy parameter specifications
STRATEGY_PARAMETERS = {
    "momentum_perpetuals": {
        "funding_threshold": ParameterSpec(
            name="funding_threshold",
            param_type=ParameterType.PERCENTAGE,
            default_value=0.0005,
            min_value=0.0001,
            max_value=0.002,
            step_size=0.0001
        ),
        "adx_threshold": ParameterSpec(
            name="adx_threshold",
            param_type=ParameterType.THRESHOLD,
            default_value=20,
            min_value=15,
            max_value=35,
            step_size=1
        ),
        "volume_min_threshold": ParameterSpec(
            name="volume_min_threshold",
            param_type=ParameterType.MULTIPLIER,
            default_value=0.7,
            min_value=0.3,
            max_value=1.5,
            step_size=0.05
        ),
        "cooldown_minutes": ParameterSpec(
            name="cooldown_minutes",
            param_type=ParameterType.COUNT,
            default_value=60,
            min_value=15,
            max_value=180,
            step_size=15
        ),
    },
    "mean_reversion_metals": {
        "rsi_oversold": ParameterSpec(
            name="rsi_oversold",
            param_type=ParameterType.THRESHOLD,
            default_value=30,
            min_value=20,
            max_value=40,
            step_size=1
        ),
        "rsi_overbought": ParameterSpec(
            name="rsi_overbought",
            param_type=ParameterType.THRESHOLD,
            default_value=70,
            min_value=60,
            max_value=80,
            step_size=1
        ),
        "bb_period": ParameterSpec(
            name="bb_period",
            param_type=ParameterType.COUNT,
            default_value=20,
            min_value=10,
            max_value=50,
            step_size=5
        ),
        "bb_std": ParameterSpec(
            name="bb_std",
            param_type=ParameterType.MULTIPLIER,
            default_value=2.0,
            min_value=1.5,
            max_value=3.0,
            step_size=0.1
        ),
        "ratio_zscore_threshold": ParameterSpec(
            name="ratio_zscore_threshold",
            param_type=ParameterType.THRESHOLD,
            default_value=2.0,
            min_value=1.0,
            max_value=3.0,
            step_size=0.1
        ),
    },
    "sentiment_driven": {
        "momentum_threshold": ParameterSpec(
            name="momentum_threshold",
            param_type=ParameterType.THRESHOLD,
            default_value=0.3,
            min_value=0.1,
            max_value=0.6,
            step_size=0.05
        ),
        "volume_min": ParameterSpec(
            name="volume_min",
            param_type=ParameterType.MULTIPLIER,
            default_value=0.8,
            min_value=0.4,
            max_value=1.5,
            step_size=0.1
        ),
        "expiry_hours": ParameterSpec(
            name="expiry_hours",
            param_type=ParameterType.COUNT,
            default_value=4,
            min_value=1,
            max_value=12,
            step_size=1
        ),
    },
}


class ParameterAdapter:
    """
    Adaptive parameter tuning with conservative adjustment policy.

    Key design decisions:
    1. Max 10% change per week to avoid overfitting
    2. Requires minimum 20 trades to consider adjustment
    3. Performance comparison uses risk-adjusted metrics (Sharpe)
    4. Changes are logged for audit trail
    5. Graceful degradation if insufficient data
    """

    MAX_WEEKLY_CHANGE_PCT = 0.10
    MIN_TRADES_FOR_ADAPTATION = 20
    ROLLING_WINDOW_DAYS = 30
    MIN_PERFORMANCE_IMPROVEMENT = 0.05

    def __init__(
        self,
        db_path: str = "hyperliquid.db",
        config: Optional[Dict[str, Any]] = None
    ):
        self.db_path = db_path
        self.config = config or {}

        self.max_weekly_change = self.config.get(
            "max_weekly_change_pct", self.MAX_WEEKLY_CHANGE_PCT
        )
        self.min_trades = self.config.get(
            "min_trades_for_adaptation", self.MIN_TRADES_FOR_ADAPTATION
        )
        self.rolling_days = self.config.get(
            "rolling_window_days", self.ROLLING_WINDOW_DAYS
        )

        self._active_params: Dict[str, ParameterSet] = {}
        self._last_adaptation: Dict[str, datetime] = {}

        self._ensure_tables()
        self._load_active_parameters()

        logger.info(f"ParameterAdapter initialized: max_change={self.max_weekly_change:.0%}/week, "
                   f"min_trades={self.min_trades}, window={self.rolling_days}d")

    def _ensure_tables(self):
        """Create required tables if they don't exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS parameter_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id INTEGER,
                    strategy_name TEXT NOT NULL,
                    snapshot_time TIMESTAMP NOT NULL,
                    parameters TEXT NOT NULL,
                    UNIQUE(trade_id, strategy_name)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_param_snap_strategy_time
                    ON parameter_snapshots(strategy_name, snapshot_time)
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS parameter_performance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_name TEXT NOT NULL,
                    parameter_hash TEXT NOT NULL,
                    parameters TEXT NOT NULL,
                    window_start TIMESTAMP NOT NULL,
                    window_end TIMESTAMP NOT NULL,
                    trade_count INTEGER DEFAULT 0,
                    win_count INTEGER DEFAULT 0,
                    total_pnl REAL DEFAULT 0.0,
                    sharpe_30d REAL DEFAULT 0.0,
                    max_drawdown_30d REAL DEFAULT 0.0,
                    computed_at TIMESTAMP,
                    UNIQUE(strategy_name, parameter_hash, window_end)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_param_perf_strategy
                    ON parameter_performance(strategy_name, window_end)
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS parameter_adjustments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_name TEXT NOT NULL,
                    parameter_name TEXT NOT NULL,
                    old_value REAL,
                    new_value REAL,
                    change_pct REAL,
                    reason TEXT,
                    adjusted_at TIMESTAMP NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_param_adj_strategy_time
                    ON parameter_adjustments(strategy_name, adjusted_at)
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS active_parameters (
                    strategy_name TEXT PRIMARY KEY,
                    parameters TEXT NOT NULL,
                    updated_at TIMESTAMP NOT NULL
                )
            """)

            conn.commit()

    def _load_active_parameters(self):
        """Load active parameters from DB or use defaults."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT strategy_name, parameters FROM active_parameters"
            ).fetchall()

        for strategy_name, params_json in rows:
            self._active_params[strategy_name] = ParameterSet.from_json(
                strategy_name, params_json
            )

        for strategy_name, specs in STRATEGY_PARAMETERS.items():
            if strategy_name not in self._active_params:
                default_params = {
                    name: spec.default_value for name, spec in specs.items()
                }
                self._active_params[strategy_name] = ParameterSet(
                    strategy_name=strategy_name,
                    parameters=default_params
                )

        logger.info(f"Loaded parameters for {len(self._active_params)} strategies")

    def get_parameters(self, strategy_name: str) -> Dict[str, float]:
        """Get current active parameters for a strategy."""
        if strategy_name in self._active_params:
            return self._active_params[strategy_name].parameters.copy()

        if strategy_name in STRATEGY_PARAMETERS:
            return {
                name: spec.default_value
                for name, spec in STRATEGY_PARAMETERS[strategy_name].items()
            }

        return {}

    def record_trade_parameters(
        self,
        trade_id: int,
        strategy_name: str,
        timestamp: Optional[datetime] = None
    ):
        """Record the active parameters when a trade is opened."""
        params = self.get_parameters(strategy_name)
        if not params:
            return

        param_set = ParameterSet(strategy_name=strategy_name, parameters=params)
        ts = timestamp or datetime.now()

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO parameter_snapshots
                       (trade_id, strategy_name, snapshot_time, parameters)
                       VALUES (?, ?, ?, ?)""",
                    (trade_id, strategy_name, ts.isoformat(), param_set.to_json())
                )
                conn.commit()

            logger.debug(f"Recorded parameters for trade {trade_id}: {param_set.hash}")
        except Exception as e:
            logger.debug(f"Error recording trade parameters: {e}")

    def compute_rolling_performance(
        self,
        strategy_name: str,
        window_end: Optional[datetime] = None
    ) -> Dict[str, PerformanceMetrics]:
        """Compute 30-day rolling performance for each parameter set used."""
        window_end = window_end or datetime.now()
        window_start = window_end - timedelta(days=self.rolling_days)

        try:
            with sqlite3.connect(self.db_path) as conn:
                results = conn.execute("""
                    SELECT
                        ps.parameters,
                        COUNT(*) as trade_count,
                        SUM(CASE WHEN t.realized_pnl > 0 THEN 1 ELSE 0 END) as win_count,
                        SUM(COALESCE(t.realized_pnl, 0)) as total_pnl
                    FROM parameter_snapshots ps
                    JOIN trades t ON ps.trade_id = t.order_id
                    WHERE ps.strategy_name = ?
                      AND ps.snapshot_time BETWEEN ? AND ?
                      AND t.realized_pnl IS NOT NULL
                    GROUP BY ps.parameters
                """, (strategy_name, window_start.isoformat(), window_end.isoformat())).fetchall()
        except Exception as e:
            logger.debug(f"Error computing rolling performance: {e}")
            return {}

        metrics = {}
        for params_json, trade_count, win_count, total_pnl in results:
            param_set = ParameterSet.from_json(strategy_name, params_json)

            avg_pnl = total_pnl / trade_count if trade_count > 0 else 0
            sharpe = avg_pnl / (abs(avg_pnl) * 2 + 0.001) if trade_count >= 5 else 0

            metrics[param_set.hash] = PerformanceMetrics(
                trade_count=trade_count,
                win_count=win_count or 0,
                total_pnl=total_pnl or 0,
                sharpe_30d=sharpe,
                max_drawdown_30d=0.0
            )

        return metrics

    def adapt_parameters(
        self,
        strategy_name: str,
        force: bool = False
    ) -> List[AdaptationResult]:
        """
        Run adaptation cycle for a strategy.

        Compares current parameters to historical performance and
        adjusts toward better-performing values if improvement > 5%.
        """
        results = []

        if not force:
            last = self._last_adaptation.get(strategy_name)
            if last and (datetime.now() - last) < timedelta(days=7):
                logger.debug(f"{strategy_name}: Skipping adaptation (within weekly cooldown)")
                return results

        perf_by_hash = self.compute_rolling_performance(strategy_name)

        if not perf_by_hash:
            logger.debug(f"{strategy_name}: No performance data for adaptation")
            return results

        best_hash = None
        best_sharpe = -999
        best_params_json = None

        for param_hash, metrics in perf_by_hash.items():
            if metrics.trade_count >= self.min_trades:
                if metrics.sharpe_30d > best_sharpe:
                    best_sharpe = metrics.sharpe_30d
                    best_hash = param_hash

        if not best_hash:
            logger.debug(f"{strategy_name}: No parameter set has enough trades")
            return results

        current = self._active_params.get(strategy_name)
        if not current:
            return results

        current_perf = perf_by_hash.get(current.hash)
        current_sharpe = current_perf.sharpe_30d if current_perf else 0

        improvement = (best_sharpe - current_sharpe) / (abs(current_sharpe) + 0.001)

        if improvement < self.MIN_PERFORMANCE_IMPROVEMENT:
            logger.debug(f"{strategy_name}: Best params not significantly better "
                        f"({improvement:.1%} < {self.MIN_PERFORMANCE_IMPROVEMENT:.0%})")
            return results

        try:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    """SELECT parameters FROM parameter_snapshots
                       WHERE strategy_name = ?
                       ORDER BY snapshot_time DESC LIMIT 1""",
                    (strategy_name,)
                ).fetchone()
                if row:
                    best_params_json = row[0]
        except Exception as e:
            logger.debug(f"Error fetching best params: {e}")
            return results

        if not best_params_json:
            return results

        best_params = json.loads(best_params_json)
        specs = STRATEGY_PARAMETERS.get(strategy_name, {})

        for param_name, spec in specs.items():
            current_val = current.parameters.get(param_name, spec.default_value)
            target_val = best_params.get(param_name, current_val)

            if abs(target_val - current_val) < spec.step_size:
                continue

            direction = 1 if target_val > current_val else -1
            max_change = abs(current_val) * self.max_weekly_change
            actual_change = min(abs(target_val - current_val), max_change)

            new_val = current_val + (direction * actual_change)
            new_val = spec.clamp(new_val)
            new_val = spec.round_to_step(new_val)

            if abs(new_val - current_val) < spec.step_size:
                continue

            change_pct = (new_val - current_val) / current_val if current_val != 0 else 0

            result = AdaptationResult(
                strategy_name=strategy_name,
                parameter_name=param_name,
                old_value=current_val,
                new_value=new_val,
                change_pct=change_pct,
                reason=f"Performance improvement {improvement:.1%}",
                confidence=min(1.0, perf_by_hash[best_hash].trade_count / 50)
            )
            results.append(result)

            current.parameters[param_name] = new_val
            self._record_adjustment(result)

        if results:
            self._save_active_parameters(strategy_name, current)
            self._last_adaptation[strategy_name] = datetime.now()

            logger.info(f"{strategy_name}: Adapted {len(results)} parameters")
            for r in results:
                logger.info(f"  {r.parameter_name}: {r.old_value:.4f} -> {r.new_value:.4f} "
                           f"({r.change_pct:+.1%})")

        return results

    def _record_adjustment(self, result: AdaptationResult):
        """Record parameter adjustment to audit log."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """INSERT INTO parameter_adjustments
                       (strategy_name, parameter_name, old_value, new_value,
                        change_pct, reason, adjusted_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (result.strategy_name, result.parameter_name, result.old_value,
                     result.new_value, result.change_pct, result.reason,
                     datetime.now().isoformat())
                )
                conn.commit()
        except Exception as e:
            logger.debug(f"Error recording adjustment: {e}")

    def _save_active_parameters(self, strategy_name: str, param_set: ParameterSet):
        """Persist active parameters to database."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO active_parameters
                       (strategy_name, parameters, updated_at)
                       VALUES (?, ?, ?)""",
                    (strategy_name, param_set.to_json(), datetime.now().isoformat())
                )
                conn.commit()

            param_set.hash = param_set._compute_hash()
        except Exception as e:
            logger.debug(f"Error saving parameters: {e}")

    def get_adaptation_history(
        self,
        strategy_name: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get recent parameter adjustments for review."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                if strategy_name:
                    rows = conn.execute(
                        """SELECT strategy_name, parameter_name, old_value, new_value,
                                  change_pct, reason, adjusted_at
                           FROM parameter_adjustments
                           WHERE strategy_name = ?
                           ORDER BY adjusted_at DESC LIMIT ?""",
                        (strategy_name, limit)
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """SELECT strategy_name, parameter_name, old_value, new_value,
                                  change_pct, reason, adjusted_at
                           FROM parameter_adjustments
                           ORDER BY adjusted_at DESC LIMIT ?""",
                        (limit,)
                    ).fetchall()
        except Exception:
            return []

        return [
            {
                "strategy": row[0],
                "parameter": row[1],
                "old_value": row[2],
                "new_value": row[3],
                "change_pct": row[4],
                "reason": row[5],
                "adjusted_at": row[6]
            }
            for row in rows
        ]

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of current parameters and recent performance."""
        summary = {}

        for strategy_name in STRATEGY_PARAMETERS.keys():
            params = self.get_parameters(strategy_name)
            perf = self.compute_rolling_performance(strategy_name)

            current_set = self._active_params.get(strategy_name)
            current_perf = perf.get(current_set.hash) if current_set else None

            summary[strategy_name] = {
                "parameters": params,
                "trades_30d": current_perf.trade_count if current_perf else 0,
                "win_rate_30d": f"{current_perf.win_rate:.1%}" if current_perf else "N/A",
                "pnl_30d": f"${current_perf.total_pnl:.2f}" if current_perf else "N/A",
                "sharpe_30d": f"{current_perf.sharpe_30d:.2f}" if current_perf else "N/A",
            }

        return summary


# Singleton instance
_adapter: Optional[ParameterAdapter] = None


def get_parameter_adapter(db_path: str = "hyperliquid.db") -> ParameterAdapter:
    """Get or create the singleton parameter adapter."""
    global _adapter
    if _adapter is None:
        _adapter = ParameterAdapter(db_path=db_path)
    return _adapter

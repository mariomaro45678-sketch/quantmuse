"""
Comprehensive tests for Phase 7 Risk Management: RiskManager and PositionSizer.
Tests VaR/CVaR accuracy, pre-trade checks, circuit breakers, stop-loss, and sizing methods.
"""

import pytest
import numpy as np
import time
import asyncio
from datetime import datetime

from data_service.risk.risk_manager import RiskManager, PreTradeResult
from data_service.risk.position_sizer import PositionSizer
from data_service.storage.database_manager import DatabaseManager


class TestRiskManager:
    """Test suite for RiskManager."""
    
    def test_var_cvar_accuracy(self):
        """Test VaR/CVaR calculation against known synthetic returns."""
        # Create known return series
        np.random.seed(0)
        returns = np.random.normal(0, 0.01, 252)  # 252 days, 1% daily vol
        
        rm = RiskManager()
        rm.load_returns(returns)
        var95, var99, cvar95 = rm.compute_var_cvar()
        
        # Hand-calculated expected values
        expected_var95 = float(np.percentile(returns, 5))
        expected_var99 = float(np.percentile(returns, 1))
        expected_cvar95 = float(returns[returns <= expected_var95].mean())
        
        assert abs(var95 - expected_var95) < 1e-6, f'VaR95 mismatch: {var95} vs {expected_var95}'
        assert abs(var99 - expected_var99) < 1e-6, f'VaR99 mismatch: {var99} vs {expected_var99}'
        assert abs(cvar95 - expected_cvar95) < 1e-6, f'CVaR95 mismatch: {cvar95} vs {expected_cvar95}'
        
        print(f'✅ VaR ACCURACY OK — VaR95={var95:.4f} VaR99={var99:.4f} CVaR95={cvar95:.4f}')
    
    def test_var_insufficient_data(self):
        """Test VaR returns NaN when insufficient data."""
        rm = RiskManager()
        rm.load_returns(np.array([0.01, -0.02, 0.005]))  # Only 3 returns
        
        var95, var99, cvar95 = rm.compute_var_cvar()
        
        assert np.isnan(var95), "VaR95 should be NaN with insufficient data"
        assert np.isnan(var99), "VaR99 should be NaN with insufficient data"
    
    def test_leverage_check_approved(self):
        """Test pre-trade check approves order within leverage limits."""
        rm = RiskManager()
        rm.set_portfolio(equity=100_000, open_positions=[])
        
        # Order that results in 3x leverage (within 5x max)
        result = rm.pre_trade_check(symbol='XAU', size=1.0, leverage=3, price=2000)
        
        assert result.approved, f'should have been approved: {result.reason}'
        print('✅ Leverage check APPROVED path OK')
    
    def test_leverage_check_blocked(self):
        """Test pre-trade check blocks order exceeding leverage limit."""
        rm = RiskManager()
        rm.set_portfolio(equity=100_000, open_positions=[])
        
        # Order that would result in 20x leverage (exceeds 5x max)
        result = rm.pre_trade_check(symbol='XAU', size=1000.0, leverage=10, price=2000)
        
        assert not result.approved, 'should have been blocked by leverage limit'
        assert 'leverage' in result.reason.lower()
        print(f'✅ Leverage check BLOCKED: {result.reason}')
    
    def test_daily_loss_gate(self):
        """Test pre-trade check blocks orders when daily loss limit is breached."""
        rm = RiskManager()
        rm.set_portfolio(equity=100_000, open_positions=[])
        rm.session_start_equity = 100_000
        
        # Set daily P&L to -$10,001 (exceeds 10% of 100k equity)
        rm.set_daily_pnl(-10_001)
        
        result = rm.pre_trade_check(symbol='XAU', size=1.0, leverage=3, price=2000)
        
        assert not result.approved, 'should have been blocked by daily loss gate'
        assert 'daily loss' in result.reason.lower()
        print(f'✅ Daily loss gate BLOCKED: {result.reason}')
    
    def test_max_drawdown_calculation(self):
        """Test maximum drawdown tracking."""
        rm = RiskManager()
        rm.set_portfolio(equity=100_000, open_positions=[], session_high_equity=100_000)
        
        # Equity drops to 94,000 (6% drawdown)
        rm.set_portfolio(equity=94_000, open_positions=[])
        drawdown = rm.compute_max_drawdown()
        
        assert abs(drawdown - 0.06) < 1e-6, f'drawdown should be 6%, got {drawdown:.2%}'
        print(f'✅ Max drawdown calculation OK: {drawdown:.2%}')
    
    def test_circuit_breaker_fires(self):
        """Test circuit breaker fires on extreme drawdown."""
        rm = RiskManager()
        rm.set_portfolio(equity=100_000, open_positions=[], session_high_equity=100_000)
        rm.set_config(circuit_breaker_pct=0.05)  # 5% threshold
        
        # Simulate equity dropping to 94,000 (6% drawdown — above 5% threshold)
        fired = rm.on_equity_update(94_000)
        
        assert fired == True, 'circuit breaker did not fire'
        assert rm.all_positions_closed() == True, 'positions not closed after CB'
        assert rm.strategies_halted() == True, 'strategies not halted after CB'
        print('✅ CIRCUIT BREAKER OK')
    
    def test_circuit_breaker_not_fired(self):
        """Test circuit breaker does NOT fire when drawdown is below threshold."""
        rm = RiskManager()
        rm.set_portfolio(equity=100_000, open_positions=[], session_high_equity=100_000)
        rm.set_config(circuit_breaker_pct=0.10)  # 10% threshold
        
        # Simulate equity dropping to 96,000 (4% drawdown — below 10% threshold)
        fired = rm.on_equity_update(96_000)
        
        assert fired == False, 'circuit breaker should not fire at 4% drawdown'
        print('✅ Circuit breaker correctly DID NOT fire')
    
    def test_leverage_ratio(self):
        """Test leverage ratio calculation."""
        rm = RiskManager()
        positions = [
            {'notional': 50_000},   # $50k notional
            {'notional': -30_000},  # $30k short (absolute value)
        ]
        rm.set_portfolio(equity=100_000, open_positions=positions)
        
        leverage = rm.compute_leverage_ratio()
        expected = (50_000 + 30_000) / 100_000  # 0.8x
        
        assert abs(leverage - expected) < 1e-6
        print(f'✅ Leverage ratio OK: {leverage:.2f}x')
    
    def test_risk_snapshot_generation(self):
        """Test risk snapshot contains all required fields."""
        rm = RiskManager()
        rm.set_portfolio(equity=100_000, open_positions=[{'notional': 20_000}])
        rm.load_returns(np.random.normal(0, 0.01, 100))
        
        snapshot = rm.get_risk_snapshot()
        
        required_fields = ['timestamp', 'total_equity', 'total_leverage', 'var_95', 'var_99', 'cvar_95', 'max_drawdown', 'num_positions']
        for field in required_fields:
            assert field in snapshot, f'missing field: {field}'
        
        assert snapshot['total_equity'] == 100_000
        assert snapshot['num_positions'] == 1
        print('✅ Risk snapshot generation OK')


class TestPositionSizer:
    """Test suite for PositionSizer."""
    
    def test_kelly_sizing(self):
        """Test Kelly Criterion sizing returns positive valid size."""
        ps = PositionSizer()
        ps.set_equity(100_000)
        
        # Win rate 55%, avg win 2%, avg loss 1.5%
        size = ps.size_kelly(win_rate=0.55, avg_win=0.02, avg_loss=0.015)
        
        assert size > 0, f'Kelly sizing returned {size}'
        print(f'✅ Kelly size = ${size:.2f}')
    
    def test_kelly_sizing_negative(self):
        """Test Kelly returns 0 when expected value is negative."""
        ps = PositionSizer()
        ps.set_equity(100_000)
        
        # Bad strategy: 40% win rate, avg loss > avg win
        size = ps.size_kelly(win_rate=0.40, avg_win=0.01, avg_loss=0.02)
        
        assert size == 0.0, 'Kelly should return 0 for negative EV'
        print('✅ Kelly correctly returns 0 for negative EV')
    
    def test_volatility_sizing(self):
        """Test volatility scaling sizing."""
        ps = PositionSizer()
        ps.set_equity(100_000)
        
        # Asset with 2% daily vol, target 1% portfolio vol
        size = ps.size_volatility(asset_daily_vol=0.02, target_vol_pct=0.01)
        
        expected = (0.01 * 100_000) / 0.02  # $50,000
        assert abs(size - expected) < 1, f'vol sizing incorrect: {size} vs {expected}'
        print(f'✅ Volatility size = ${size:.2f}')
    
    def test_risk_parity_sizing(self):
        """Test risk parity sizing."""
        ps = PositionSizer()
        ps.set_equity(100_000)
        
        # 3 positions, asset with 2% vol
        size = ps.size_risk_parity(num_positions=3, asset_daily_vol=0.02)
        
        expected = 100_000 / (3 * 0.02)  # $1,666,667
        assert abs(size - expected) < 10, f'risk parity incorrect: {size} vs {expected}'
        print(f'✅ Risk parity size = ${size:.2f}')
    
    def test_stop_loss_breach_long(self):
        """Test stop-loss triggers on price drop for long position."""
        ps = PositionSizer()
        
        # Long XAU entered at 2000, 5% stop → 1900
        ps.register_position('XAU', entry_price=2000, direction='long', stop_loss_pct=0.05)
        
        # Price ticks down to 1899 — below stop
        close_order = ps.on_price_tick('XAU', 1899)
        
        assert close_order is not None, 'no close order generated'
        assert close_order.symbol == 'XAU'
        assert close_order.side == 'sell'
        assert 'stop' in close_order.reason.lower()
        print('✅ STOP-LOSS OK (long)')
    
    def test_stop_loss_breach_short(self):
        """Test stop-loss triggers on price rise for short position."""
        ps = PositionSizer()
        
        # Short XAU entered at 2000, 5% stop → 2100
        ps.register_position('XAU', entry_price=2000, direction='short', stop_loss_pct=0.05)
        
        # Price ticks up to 2101 — above stop
        close_order = ps.on_price_tick('XAU', 2101)
        
        assert close_order is not None, 'no close order generated'
        assert close_order.symbol == 'XAU'
        assert close_order.side == 'buy'
        print('✅ STOP-LOSS OK (short)')
    
    def test_stop_loss_not_breached(self):
        """Test no order generated when stop not breached."""
        ps = PositionSizer()
        ps.register_position('XAU', entry_price=2000, direction='long', stop_loss_pct=0.05)
        
        # Price at 1950 (still above 1900 stop)
        close_order = ps.on_price_tick('XAU', 1950)
        
        assert close_order is None, 'close order generated when stop not breached'
        print('✅ Stop-loss correctly not triggered')
    
    def test_apply_constraints_min_size(self):
        """Test apply_constraints rounds up to min order size."""
        ps = PositionSizer()
        ps.set_equity(100_000)
        
        # Raw size 0.5, min 1.0
        constrained = ps.apply_constraints(
            symbol='XAU',
            raw_size=0.5,
            leverage=3,
            price=2000,
            min_order_size=1.0
        )
        
        assert constrained == 1.0, f'should round up to 1.0, got {constrained}'
        print('✅ Min size constraint OK')
    
    def test_apply_constraints_risk_check(self):
        """Test apply_constraints returns 0 when risk check fails."""
        rm = RiskManager()
        rm.set_portfolio(equity=100_000, open_positions=[])
        
        ps = PositionSizer(risk_manager=rm)
        ps.set_equity(100_000)
        
        # Order that would exceed leverage
        constrained = ps.apply_constraints(
            symbol='XAU',
            raw_size=1000.0,
            leverage=20,
            price=2000,
            min_order_size=1.0
        )
        
        assert constrained == 0.0, 'should return 0 when risk check fails'
        print('✅ Risk check constraint OK')

    def test_trailing_stop_loss_long(self):
        """Test trailing stop-loss updates its trigger price."""
        ps = PositionSizer()
        ps.trailing_stop_enabled = True
        ps.trailing_activation_pct = 0.02
        ps.trailing_distance_pct = 0.05
        
        # Long XAU @ 2000, stop 5% (1900), trailing activation 2%, distance 5%
        ps.register_position('XAU', entry_price=2000, direction='long', stop_loss_pct=0.05)
        
        # 1. Price rises to 2041 (above 2% activation threshold)
        ps.on_price_tick('XAU', 2041)
        # New trailing stop should be 2041 * (1 - 0.05) = 1938.95
        
        # 2. Price retraces to 1939 (above 1938.95 stop)
        order = ps.on_price_tick('XAU', 1939)
        assert order is None, "Should not have triggered yet"
        
        # 3. Price breaches re-adjusted trailing stop
        order = ps.on_price_tick('XAU', 1938)
        assert order is not None
        assert 'Stop-loss breached at 1938.00' in order.reason
        print('✅ Trailing stop-loss (long) OK')

    def test_multi_symbol_stop_loss(self):
        """Test multiple concurrent stop-losses for different symbols."""
        ps = PositionSizer()
        ps.register_position('XAU', entry_price=2000, direction='long', stop_loss_pct=0.01) # Stop at 1980
        ps.register_position('ETH', entry_price=3000, direction='long', stop_loss_pct=0.01) # Stop at 2970
        
        # Tick XAU to 1990 (no breach)
        assert ps.on_price_tick('XAU', 1990) is None
        # Tick ETH to 2960 (breach!)
        order_eth = ps.on_price_tick('ETH', 2960)
        assert order_eth is not None and order_eth.symbol == 'ETH'
        
        # Tick XAU to 1970 (breach!)
        order_xau = ps.on_price_tick('XAU', 1970)
        assert order_xau is not None and order_xau.symbol == 'XAU'
        print('✅ Multi-symbol stop-loss OK')


class TestIntegration:
    """Integration tests for RiskManager + PositionSizer."""
    
    def test_risk_snapshot_persistence(self):
        """Test risk snapshots are written to database."""
        db = DatabaseManager()  # Uses default test DB
        rm = RiskManager(db_manager=db)
        rm.set_portfolio(equity=100_000, open_positions=[])
        
        # Manually save snapshot
        snapshot = rm.get_risk_snapshot()
        db.save_risk_snapshot(snapshot)
        
        # Verify it was written
        snapshots = db.get_recent_risk_snapshots(limit=1)
        assert len(snapshots) >= 1, 'no snapshots written'
        assert snapshots[0]['total_equity'] == 100_000
        print(f'✅ RISK SNAPSHOTS OK — {len(snapshots)} rows written')
    
    def test_alert_persistence(self):
        """Test alerts are written to database on circuit breaker."""
        db = DatabaseManager()
        rm = RiskManager(db_manager=db)
        rm.set_portfolio(equity=100_000, open_positions=[], session_high_equity=100_000)
        rm.set_config(circuit_breaker_pct=0.05)
        
        # Trigger circuit breaker
        fired = rm.on_equity_update(94_000)
        assert fired == True
        
        # Check alert was saved
        alerts = db.get_recent_alerts(limit=5)
        assert len(alerts) >= 1, 'no alert saved'
        assert any(a['type'] == 'circuit_breaker' for a in alerts), 'circuit_breaker alert not found'
        print(f'✅ ALERT PERSISTENCE OK — {len(alerts)} alerts')


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])

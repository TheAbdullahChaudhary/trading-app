"""Unit tests for risk manager."""
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from bot.risk_manager import RiskManager


def test_position_sizing():
    rm = RiskManager(risk_pct=1.0, sl_atr_mult=1.5, tp_atr_mult=3.0)
    result = rm.calculate_order(balance=1000, price=50000, atr=500, signal="BUY")
    assert result["volume"] >= 1
    assert result["stop_loss"] < 50000
    assert result["take_profit"] > 50000
    assert result["side"] == 1


def test_sell_order():
    rm = RiskManager()
    result = rm.calculate_order(balance=1000, price=100, atr=2, signal="SELL")
    assert result["stop_loss"] > 100
    assert result["take_profit"] < 100
    assert result["side"] == 3


def test_max_positions_blocks():
    rm = RiskManager(max_positions=3)
    rm.reset_daily(1000)
    ok, msg = rm.is_trading_allowed(3)
    assert not ok
    assert "Max positions" in msg


def test_drawdown_halts_trading():
    rm = RiskManager(max_drawdown_pct=5.0)
    rm.reset_daily(1000)
    rm.update_pnl(-60)   # 6% loss
    ok, msg = rm.is_trading_allowed(0)
    assert not ok
    assert "drawdown" in msg.lower()


def test_no_trade_when_atr_zero():
    rm = RiskManager()
    result = rm.calculate_order(balance=1000, price=100, atr=0, signal="BUY")
    assert result == {}

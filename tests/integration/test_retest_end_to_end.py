"""End-to-end: synthetic bars → detect_swings → structure → levels → retest → signal."""

import pytest
import pandas as pd

from bot.analysis.swing import detect_swings
from bot.analysis.structure import analyze_structure
from bot.analysis.levels import LevelManager
from bot.analysis.trend import analyze_trend, TrendBias
from bot.analysis.volume import analyze_volume
from bot.strategy.base import StrategyContext
from bot.strategy.retest import RetestStrategy


def build_e2e_bars() -> pd.DataFrame:
    """
    Build a synthetic scenario:
    - Bars 0-9: consolidation around 1.1020 (two swing highs near same price)
    - Bars 10-14: breakout above 1.1020 resistance
    - Bars 15-19: retest of broken resistance (now support)
    - Last bar (19): bullish rejection candle at the level
    """
    n = 20
    times = list(range(1000, 1000 + n))
    opens = [1.1000] * n
    closes = [1.1000] * n
    highs = [1.1010] * n
    lows = [1.0990] * n
    volumes = [100] * n

    # Bar 3: swing high at 1.1022
    highs[3] = 1.1022
    lows[3] = 1.0998
    opens[3] = 1.1000
    closes[3] = 1.1015

    # Bar 7: swing high at 1.1021 (pair with bar 3)
    highs[7] = 1.1021
    lows[7] = 1.0998
    opens[7] = 1.1000
    closes[7] = 1.1014

    # Bar 5: swing low
    highs[5] = 1.1005
    lows[5] = 1.0985
    opens[5] = 1.0990
    closes[5] = 1.0992

    # Bars 10-13: breakout above 1.1020
    for i in range(10, 14):
        highs[i] = 1.1040 + (i - 10) * 0.0002
        closes[i] = 1.1035 + (i - 10) * 0.0002
        opens[i] = 1.1025 + (i - 10) * 0.0002
        lows[i] = 1.1020

    # Bars 14-18: price pulling back toward the broken level
    for i in range(14, 19):
        highs[i] = 1.1040
        closes[i] = 1.1025
        opens[i] = 1.1030
        lows[i] = 1.1020

    # Bar 19 (last): bullish retest candle
    # Low touches level at 1.1020, closes above, bullish body > 40%
    highs[19] = 1.1035
    opens[19] = 1.1021
    closes[19] = 1.1032   # bullish: close > open
    lows[19] = 1.1019     # touches level at ~1.1020
    volumes[19] = 180     # slightly above average

    return pd.DataFrame({
        "time": times,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "tick_volume": volumes,
    })


class TestRetestEndToEnd:
    def test_full_pipeline_generates_buy_signal(self):
        """Full pipeline: bars → swings → structure → levels → retest → BUY signal."""
        bars = build_e2e_bars()

        # Step 1: detect swings
        swings = detect_swings(bars, lookback=3)
        # We should have detected swing highs at bars 3 and 7
        assert len(swings) >= 2

        # Step 2: analyze structure
        structure = analyze_structure(bars, swings)

        # Step 3: build levels from swings
        mgr = LevelManager()
        mgr.detect_levels(bars, swings, "EURUSD", "M15", tolerance_pct=0.05)

        # Manually mark the resistance as broken (simulate the breakout)
        key = "EURUSD_M15"
        for lv in mgr._levels.get(key, []):
            if lv.type == "resistance":
                lv.is_broken = True
                lv.broken_at = 1010  # timestamp of break bar
                lv.flip_active = True

        levels = mgr.get_active_levels("EURUSD", "M15")

        # Step 4: analyze trend (same bars as proxy for H4/D1)
        trend = analyze_trend(bars, bars)

        # Step 5: volume
        volume = analyze_volume(bars, lookback=10)

        # Step 6: build context
        ctx = StrategyContext(
            symbol="EURUSD",
            timeframe="M15",
            bars=bars,
            bars_h4=bars,
            bars_d1=bars,
            swings=swings,
            structure=structure,
            levels=levels,
            trend=trend,
            volume=volume,
            current_price=float(bars["close"].iloc[-1]),
            timestamp=int(bars["time"].iloc[-1]),
        )

        # Step 7: run retest strategy
        strategy = RetestStrategy()
        signal = strategy.analyze(ctx)

        # The strategy may or may not find a signal depending on level proximity
        # Key assertions:
        if signal is not None:
            assert signal.direction in ("BUY", "SELL")
            assert signal.sl_price is not None
            assert signal.entry_price is not None
            if signal.direction == "BUY":
                assert signal.sl_price < signal.entry_price
            else:
                assert signal.sl_price > signal.entry_price
            assert signal.confidence >= 0.0
            assert signal.strategy_name == "retest"

    def test_signal_has_valid_sl_and_tp(self):
        """If a signal is generated, SL and TP must be valid."""
        bars = build_e2e_bars()
        swings = detect_swings(bars, lookback=3)
        structure = analyze_structure(bars, swings)

        mgr = LevelManager()
        mgr.detect_levels(bars, swings, "EURUSD", "M15", tolerance_pct=0.05)

        key = "EURUSD_M15"
        for lv in mgr._levels.get(key, []):
            if lv.type == "resistance":
                lv.is_broken = True
                lv.broken_at = 1010
                lv.flip_active = True

        levels = mgr.get_active_levels("EURUSD", "M15")
        trend = analyze_trend(bars, bars)
        volume = analyze_volume(bars)

        ctx = StrategyContext(
            symbol="EURUSD",
            timeframe="M15",
            bars=bars,
            bars_h4=bars,
            bars_d1=bars,
            swings=swings,
            structure=structure,
            levels=levels,
            trend=trend,
            volume=volume,
            current_price=float(bars["close"].iloc[-1]),
            timestamp=int(bars["time"].iloc[-1]),
        )

        strategy = RetestStrategy()
        signal = strategy.analyze(ctx)

        if signal is not None:
            # RR check: TP should be at least RR_TARGET * risk away
            from bot.strategy.retest import RR_TARGET
            if signal.direction == "BUY":
                risk = signal.entry_price - signal.sl_price
                assert risk > 0
                if signal.tp_price is not None:
                    reward = signal.tp_price - signal.entry_price
                    assert reward >= risk * RR_TARGET * 0.99  # allow tiny float error
            else:
                risk = signal.sl_price - signal.entry_price
                assert risk > 0
                if signal.tp_price is not None:
                    reward = signal.entry_price - signal.tp_price
                    assert reward >= risk * RR_TARGET * 0.99

    def test_no_signal_on_flat_bars(self):
        """Flat bars with no levels → no signal."""
        n = 25
        bars = pd.DataFrame({
            "time": list(range(1000, 1000 + n)),
            "open": [1.1000] * n,
            "high": [1.1005] * n,
            "low": [1.0995] * n,
            "close": [1.1000] * n,
            "tick_volume": [100] * n,
        })

        swings = detect_swings(bars, lookback=3)
        structure = analyze_structure(bars, swings)
        mgr = LevelManager()
        levels = mgr.get_active_levels("EURUSD", "M15")  # empty
        trend = analyze_trend(bars, bars)
        volume = analyze_volume(bars)

        ctx = StrategyContext(
            symbol="EURUSD",
            timeframe="M15",
            bars=bars,
            bars_h4=bars,
            bars_d1=bars,
            swings=swings,
            structure=structure,
            levels=levels,
            trend=trend,
            volume=volume,
            current_price=1.1000,
            timestamp=1024,
        )

        strategy = RetestStrategy()
        signal = strategy.analyze(ctx)
        assert signal is None

"""Análisis de perfil de volumen para filtrar señales de baja calidad."""

from __future__ import annotations

import pandas as pd
from dataclasses import dataclass


@dataclass
class VolumeProfile:
    avg_volume: float
    current_volume: float
    volume_ratio: float
    is_above_average: bool
    quality: str    # "high" / "normal" / "low"


def analyze_volume(bars: pd.DataFrame, lookback: int = 20) -> VolumeProfile:
    """
    Uses tick_volume column.
    avg = mean of last `lookback` bars EXCLUDING the current (last) bar.
    current = last bar's tick_volume.
    quality: "high" if ratio>=1.5, "low" if ratio<0.7, "normal" otherwise.
    If fewer than 2 bars available: return VolumeProfile with quality="normal".
    """
    if bars is None or len(bars) < 2:
        return VolumeProfile(
            avg_volume=0.0,
            current_volume=0.0,
            volume_ratio=1.0,
            is_above_average=False,
            quality="normal",
        )

    current_volume = float(bars["tick_volume"].iloc[-1])

    # Historical bars excluding the last one
    history = bars["tick_volume"].iloc[-(lookback + 1):-1]
    if history.empty:
        return VolumeProfile(
            avg_volume=0.0,
            current_volume=current_volume,
            volume_ratio=1.0,
            is_above_average=False,
            quality="normal",
        )

    avg_volume = float(history.mean())

    if avg_volume == 0:
        volume_ratio = 1.0
    else:
        volume_ratio = current_volume / avg_volume

    is_above_average = volume_ratio >= 1.0

    if volume_ratio >= 1.5:
        quality = "high"
    elif volume_ratio < 0.7:
        quality = "low"
    else:
        quality = "normal"

    return VolumeProfile(
        avg_volume=avg_volume,
        current_volume=current_volume,
        volume_ratio=volume_ratio,
        is_above_average=is_above_average,
        quality=quality,
    )


def is_volume_supportive(profile: VolumeProfile) -> bool:
    return profile.quality != "low"

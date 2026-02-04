from __future__ import annotations

import math
import sys
from typing import Any, Dict


def parse_daily_spike_factor(anomaly_cfg: Dict[str, Any]) -> float:
    spike_factor = 2.0
    if not isinstance(anomaly_cfg, dict) or not anomaly_cfg:
        return spike_factor

    try:
        if "daily_spike_factor" in anomaly_cfg:
            spike_factor = float(anomaly_cfg.get("daily_spike_factor") or spike_factor)
        elif "daily_spike_pct" in anomaly_cfg:
            pct = float(anomaly_cfg.get("daily_spike_pct") or 0.0)
            if not math.isfinite(pct):
                raise ValueError("daily_spike_pct must be finite")
            # Guardrail: keep parsing unambiguous to avoid alert spam.
            # - (0, 1] is treated as a fraction (e.g. 0.5 => +50% => factor 1.5)
            # - >1 must be an integer percent (e.g. 50 => +50% => factor 1.5; 150 => +150% => factor 2.5)
            if pct <= 0.0:
                raise ValueError("daily_spike_pct must be > 0")
            if pct <= 1.0:
                spike_factor = 1.0 + pct
            elif pct.is_integer():
                spike_factor = 1.0 + (pct / 100.0)
            else:
                raise ValueError(
                    "daily_spike_pct > 1 must be an integer percent (e.g. 50, 150); use daily_spike_factor for fractional factors"
                )
        if not math.isfinite(spike_factor):
            raise ValueError("spike_factor must be finite")
    except (TypeError, ValueError):
        print("WARN: invalid anomaly config daily_spike_pct/daily_spike_factor; using default spike_factor=2.0", file=sys.stderr)
        spike_factor = 2.0

    if spike_factor < 1.0:
        spike_factor = 1.0
    return spike_factor

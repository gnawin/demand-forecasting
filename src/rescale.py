"""Common-support annual-sum normalization."""

import numpy as np
import pandas as pd


def to_annual_total(pred: pd.Series, annual_total: float) -> pd.Series:
    if not np.isfinite(annual_total) or annual_total <= 0:
        raise ValueError("Annual total must be finite and positive.")
    if not np.isfinite(pred.dropna()).all() or (pred.dropna() < 0).any():
        raise ValueError("Predictions must be finite and non-negative (or NaN).")
    total = pred.sum(min_count=1)
    if not np.isfinite(total) or total <= 0:
        raise ValueError("Cannot rescale an empty or zero-sum prediction.")
    return pred * (annual_total / total)

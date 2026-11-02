import numpy as np
import pandas as pd
import pytest

from src.rescale import to_annual_total


def test_annual_sum_and_nan_support():
    pred = pd.Series([1.0, np.nan, 2.0, 3.0])
    result = to_annual_total(pred, 12345)
    assert result.sum() == pytest.approx(12345, rel=1e-12)
    assert result.isna().equals(pred.isna())
    assert result.iloc[2] == 2 * result.iloc[0]


@pytest.mark.parametrize("pred,total", [
    ([0, 0], 10), ([np.nan], 10), ([1, -1], 10),
    ([1, np.inf], 10), ([1, 2], 0), ([1, 2], np.nan),
])
def test_invalid_rescaling_fails(pred, total):
    with pytest.raises(ValueError):
        to_annual_total(pd.Series(pred), total)

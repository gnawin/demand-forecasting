import logging

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from ..features import CATEGORICAL, LINEAR_NUMERIC
from .base import Method

LOG = logging.getLogger(__name__)


class Regression(Method):
    name = "B"
    numeric = LINEAR_NUMERIC

    def fit(self, train: pd.DataFrame) -> None:
        train = train.dropna(subset=["value", *self.numeric])
        encoder = ColumnTransformer([
            ("numeric", StandardScaler(), self.numeric),
            ("calendar", OneHotEncoder(handle_unknown="ignore", sparse_output=False), CATEGORICAL),
        ])
        self.model = make_pipeline(encoder, Ridge(alpha=1.0))
        self.model.fit(train, train["value"])

    def predict(self, heldout_features: pd.DataFrame, annual_total: float) -> pd.Series:
        result = pd.Series(np.nan, index=heldout_features.index, name=self.name)
        ok = heldout_features[self.numeric].notna().all(axis=1)
        values = self.model.predict(heldout_features.loc[ok])
        if (values < 0).any():
            LOG.warning("%s: clipping %d negative regression predictions to zero.", self.name, (values < 0).sum())
        result.loc[ok] = np.maximum(0, values)
        return result


class RegressionWithProfile(Regression):
    """B+W: regression B with the external (WPG) profile as one extra input."""

    name = "B+W"
    numeric = LINEAR_NUMERIC + ["wpg"]

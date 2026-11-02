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

    def fit(self, train: pd.DataFrame) -> None:
        train = train.dropna(subset="value")
        encoder = ColumnTransformer([
            ("numeric", StandardScaler(), LINEAR_NUMERIC),
            ("calendar", OneHotEncoder(handle_unknown="ignore", sparse_output=False), CATEGORICAL),
        ])
        self.model = make_pipeline(encoder, Ridge(alpha=1.0))
        self.model.fit(train, train["value"])

    def predict(self, heldout_features: pd.DataFrame, annual_total: float) -> pd.Series:
        values = self.model.predict(heldout_features)
        if (values < 0).any():
            LOG.warning("B: clipping %d negative regression predictions to zero.", (values < 0).sum())
        return pd.Series(np.maximum(0, values), index=heldout_features.index, name=self.name)

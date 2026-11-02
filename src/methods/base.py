from abc import ABC, abstractmethod

import pandas as pd


class Method(ABC):
    name: str

    @abstractmethod
    def fit(self, train: pd.DataFrame) -> None:
        """Fit on training years only, including their measured values."""

    @abstractmethod
    def predict(self, heldout_features: pd.DataFrame, annual_total: float) -> pd.Series:
        """Predict from weather/calendar only; normalization is applied by the driver."""

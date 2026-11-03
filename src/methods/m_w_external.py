import pandas as pd

from .base import Method


class ExternalProfile(Method):
    name = "A*"

    def __init__(self, profile: pd.Series, name: str = "A*"):
        self.profile = profile
        self.name = name

    def fit(self, train: pd.DataFrame) -> None:
        pass

    def predict(self, heldout_features: pd.DataFrame, annual_total: float) -> pd.Series:
        # Uncovered intervals stay NaN; the driver removes them from every method's support.
        return self.profile.reindex(heldout_features.index).rename(self.name)

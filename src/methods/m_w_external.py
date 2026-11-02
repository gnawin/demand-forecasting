import pandas as pd

from .base import Method


class ExternalProfile(Method):
    name = "A*"

    def __init__(self, profile: pd.Series):
        self.profile = profile

    def fit(self, train: pd.DataFrame) -> None:
        pass

    def predict(self, heldout_features: pd.DataFrame, annual_total: float) -> pd.Series:
        result = self.profile.reindex(heldout_features.index)
        if result.isna().any():
            raise ValueError(f"A*: external profile is missing {result.isna().sum()} held-out intervals.")
        return result.rename(self.name)

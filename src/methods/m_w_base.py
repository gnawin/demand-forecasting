"""A*b: external profile plus a fitted base, measured ~ a*profile + b + calendar terms."""

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

from .base import Method


class ExternalPlusBase(Method):
    name = "A*b"

    def __init__(self, crisis_year: int | None = None):
        # With crisis_year, a and b are allowed to change from that year on (A*b22).
        self.crisis_year = crisis_year
        if crisis_year is not None:
            self.name = "A*b22"

    def design(self, frame: pd.DataFrame) -> pd.DataFrame:
        x = frame[["wpg", "weekend", "holiday"]].astype(float)
        # WPG is ~1e7 Wh/day; unscaled, the solver silently drops the 0/1 calendar columns.
        x["wpg"] = x["wpg"] / self.wpg_scale
        if self.crisis_year is not None:
            post = (frame["year"] >= self.crisis_year).astype(float)
            x["post"] = post
            x["wpg_post"] = x["wpg"] * post
        return x

    def fit(self, train: pd.DataFrame) -> None:
        train = train.dropna(subset=["value", "wpg"])
        if train.empty:
            raise ValueError(f"{self.name}: no training days with both measured demand and the external profile.")
        self.wpg_scale = float(train["wpg"].mean())
        x = self.design(train)
        self.model = LinearRegression().fit(x, train["value"])
        coef = dict(zip(x.columns, map(float, self.model.coef_)))
        b = float(self.model.intercept_)
        # a and wpg_post are in demand units per unit of the original profile.
        coef["wpg"] /= self.wpg_scale
        if "wpg_post" in coef:
            coef["wpg_post"] /= self.wpg_scale
        self.metadata = {"a": coef["wpg"], "b": b, **{k: v for k, v in coef.items() if k != "wpg"},
                         "training_rows": len(train)}
        # Base as a share of mean demand, so it can be compared across periods without units.
        if self.crisis_year is None:
            self.metadata["base_share"] = b / float(train["value"].mean())
        else:
            post = train["year"] >= self.crisis_year
            if (~post).any():
                self.metadata["base_share_pre"] = b / float(train.loc[~post, "value"].mean())
            if post.any():
                self.metadata["base_share_post"] = (b + coef["post"]) / float(train.loc[post, "value"].mean())

    def predict(self, heldout_features: pd.DataFrame, annual_total: float) -> pd.Series:
        result = pd.Series(np.nan, index=heldout_features.index, name=self.name)
        ok = heldout_features["wpg"].notna()
        if ok.any():
            result.loc[ok] = np.maximum(0, self.model.predict(self.design(heldout_features.loc[ok])))
        return result

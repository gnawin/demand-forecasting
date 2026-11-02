import logging

import pandas as pd

from .base import Method

LOG = logging.getLogger(__name__)


class ReferenceYear(Method):
    name = "A1"

    def __init__(self, timezone: str = "Europe/Amsterdam"):
        self.timezone = timezone

    def fit(self, train: pd.DataFrame) -> None:
        self.reference_year = int(train["year"].max())
        reference = train.loc[train["year"].eq(self.reference_year)].dropna(subset="value").copy()
        local = reference.index.tz_convert(self.timezone)
        reference["date"] = local.tz_localize(None).normalize()
        reference["local_hour"] = local.hour
        reference = reference.loc[local.year == self.reference_year]
        self.profile = reference.groupby(["date", "local_hour"])["value"].mean()
        self.days = pd.date_range(f"{self.reference_year}-01-01", f"{self.reference_year}-12-31", freq="D")
        self.fallback = train.dropna(subset="value").groupby(["weekday", "hour"])["value"].mean()
        if self.profile.empty:
            raise ValueError("Reference year has no observed days.")

    def predict(self, heldout_features: pd.DataFrame, annual_total: float) -> pd.Series:
        local = heldout_features.index.tz_convert(self.timezone)
        dates = local.tz_localize(None).normalize()
        mapping = {}
        for day in dates.unique():
            month_day = day.strftime("%m-%d")
            if month_day == "02-29" and not pd.Timestamp(self.reference_year, 12, 31).is_leap_year:
                month_day = "02-28"
            target = pd.Timestamp(f"{self.reference_year}-{month_day}")
            candidates = self.days[self.days.day_of_week == day.day_of_week]
            if candidates.empty:
                raise ValueError("Reference year has no day with the required weekday.")
            mapping[day] = candidates[abs(candidates - target).argmin()]
        keys = pd.MultiIndex.from_arrays([[mapping[day] for day in dates], local.hour])
        values = self.profile.reindex(keys).to_numpy()
        result = pd.Series(values, index=heldout_features.index, name=self.name)
        missing = result.isna()
        if missing.any():
            LOG.warning("A1: filling %d unmapped/missing reference slots with training weekday/hour means.", missing.sum())
            fallback_keys = pd.MultiIndex.from_arrays([
                heldout_features.loc[missing, "weekday"], heldout_features.loc[missing, "hour"],
            ])
            result.loc[missing] = self.fallback.reindex(fallback_keys).to_numpy()
        if result.isna().any():
            raise ValueError("Insufficient training weekday/hour observations for A1.")
        return result

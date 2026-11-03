from __future__ import annotations

import importlib.util
import logging

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from threadpoolctl import threadpool_limits

from ..features import GBM_FEATURES
from .base import Method

LOG = logging.getLogger(__name__)


def split_days(index: pd.DatetimeIndex, timezone: str, seed: int) -> np.ndarray:
    labels = index.tz_convert(timezone).normalize()
    days = labels.unique()
    if len(days) < 10:
        raise ValueError("Boosting needs at least 10 training days for day-grouped validation.")
    selected = np.random.default_rng(seed).choice(len(days), size=max(1, round(0.1 * len(days))), replace=False)
    return np.asarray(labels.isin(days[selected]))


class GradientBoosting(Method):
    name = "C"

    def __init__(self, settings: dict | None = None, timezone: str = "Europe/Amsterdam"):
        self.settings = settings or {}
        self.timezone = timezone

    def fit(self, train: pd.DataFrame) -> None:
        train = train.dropna(subset="value")
        seed = int(self.settings.get("random_seed", 42))
        valid = split_days(train.index, self.timezone, seed)
        # TAC (NEDU profile temperature) is used when the weather reader provides it.
        self.features = [c for c in GBM_FEATURES if c in train]
        x = train[self.features]
        y = train["value"]
        backend = self.settings.get("backend", "auto")
        if backend not in {"auto", "lightgbm", "sklearn"}:
            raise ValueError("boosting.backend must be auto, lightgbm or sklearn.")
        available = importlib.util.find_spec("lightgbm") is not None
        if backend == "lightgbm" and not available:
            raise ImportError("Install the lightgbm extra or choose backend: sklearn.")
        self.backend = "lightgbm" if available and backend != "sklearn" else "sklearn"
        maximum = int(self.settings.get("max_iter", 500))
        patience = int(self.settings.get("patience", 30))
        rate = float(self.settings.get("learning_rate", 0.05))
        threads = int(self.settings.get("threads", 2))
        if maximum < 1 or patience < 1 or threads < 1:
            raise ValueError("Boosting max_iter, patience and threads must be positive.")
        with threadpool_limits(limits=threads):
            if self.backend == "lightgbm":
                from lightgbm import LGBMRegressor, early_stopping

                self.model = LGBMRegressor(
                    n_estimators=maximum, learning_rate=rate, num_leaves=31,
                    min_child_samples=50, random_state=seed, n_jobs=threads, verbosity=-1,
                )
                self.model.fit(
                    x.loc[~valid], y.loc[~valid], eval_set=[(x.loc[valid], y.loc[valid])],
                    callbacks=[early_stopping(patience, verbose=False)],
                )
                best_iteration = self.model.best_iteration_
            else:
                # Explicit validation arrays avoid sklearn's default random-row split.
                self.model = HistGradientBoostingRegressor(
                    learning_rate=rate, max_leaf_nodes=31, min_samples_leaf=50,
                    max_iter=maximum, early_stopping=True, scoring="loss",
                    n_iter_no_change=patience, tol=1e-7, random_state=seed,
                )
                self.model.fit(x.loc[~valid], y.loc[~valid], X_val=x.loc[valid], y_val=y.loc[valid])
                # sklearn keeps the final patience rounds; retain the actual best iteration.
                best_iteration = max(1, int(np.argmax(self.model.validation_score_[1:])) + 1)
                self.prediction_iterations = best_iteration
        self.metadata = {
            "backend": self.backend, "best_iteration": int(best_iteration),
            "training_rows": int((~valid).sum()), "validation_rows": int(valid.sum()),
            "validation_split": "10% of local training days", "random_seed": seed,
        }

    def predict(self, heldout_features: pd.DataFrame, annual_total: float) -> pd.Series:
        with threadpool_limits(limits=int(self.settings.get("threads", 2))):
            if self.backend == "sklearn" and self.prediction_iterations < self.model.n_iter_:
                # Use the public staged API rather than mutating sklearn's private trees.
                for iteration, values in enumerate(self.model.staged_predict(heldout_features[self.features]), start=1):
                    if iteration == self.prediction_iterations:
                        break
            else:
                values = self.model.predict(heldout_features[self.features])
        if (values < 0).any():
            LOG.warning("C: clipping %d negative predictions to zero.", (values < 0).sum())
        return pd.Series(np.maximum(0, values), index=heldout_features.index, name=self.name)

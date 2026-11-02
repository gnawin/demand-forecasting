"""Fixed, slide-ready result figures."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .metrics import peak_window

COLORS = {"A1": "#0072B2", "A*": "#CC79A7", "B": "#E69F00", "C": "#009E73"}


def save_figures(
    predictions: dict[int, pd.DataFrame], yearly: pd.DataFrame, coldest: int,
    destination: Path, mode: str, units: str,
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with plt.rc_context({"font.size": 12, "axes.labelsize": 12, "legend.fontsize": 12, "figure.dpi": 150}):
        frame = predictions[coldest]
        methods = [name for name in frame if name != "measured"]
        window = peak_window(frame["measured"], mode)
        fig, ax = plt.subplots(figsize=(11, 5), layout="constrained")
        ax.plot(window, frame.loc[window, "measured"], color="black", linewidth=2, label="Measured")
        for method in methods:
            ax.plot(window, frame.loc[window, method], color=COLORS[method], label=method, alpha=0.85)
        ax.set(title=f"Peak week, coldest held-out year ({coldest})", ylabel=f"Demand [{units}]", xlabel="UTC interval start")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b", tz="UTC"))
        ax.legend(ncol=min(5, len(methods) + 1))
        fig.savefig(destination / "fig1_peak_week.png", dpi=150)
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(11, 5), layout="constrained")
        positions = np.arange(len(methods))
        for offset, metric, label, hatch in (
            (-0.18, "peak_week_relative_error", "Peak week", ""),
            (0.18, "top_1pct_measured_relative_error", "Top 1% measured", "//"),
        ):
            errors = [yearly.loc[yearly["method"].eq(m), metric].abs().to_numpy() * 100 for m in methods]
            means = np.array([values.mean() for values in errors])
            worst = np.array([values.max() for values in errors])
            ax.bar(positions + offset, means, width=0.36, color=[COLORS[m] for m in methods],
                   hatch=hatch, label=label, edgecolor="black", linewidth=0.6,
                   yerr=np.vstack([np.zeros(len(methods)), worst - means]), capsize=5)
        ax.set(xticks=positions, xticklabels=methods, ylabel="Absolute relative error [%]",
               title="Mean over held-out years; whiskers show worst absolute error")
        ax.legend()
        fig.savefig(destination / "fig2_metric_bars.png", dpi=150)
        plt.close(fig)

        selected = [m for m in ("A1", "C") if m in methods]
        if not selected:
            selected = methods[:2]
        fig, axes = plt.subplots(1, len(selected), figsize=(11, 5), squeeze=False, layout="constrained")
        for ax, method in zip(axes[0], selected):
            valid = frame[["measured", method]].dropna()
            ax.scatter(valid["measured"], valid[method], color=COLORS[method], alpha=0.15, s=4, rasterized=True)
            maximum = float(valid.to_numpy().max()) * 1.02
            ax.plot([0, maximum], [0, maximum], color="black", linestyle="--")
            ax.set(title=f"{method}, {coldest} ({mode})", xlabel=f"Measured [{units}]", ylabel=f"Rebuilt [{units}]",
                   xlim=(0, maximum), ylim=(0, maximum))
        fig.savefig(destination / "fig3_annual_scatter.png", dpi=150)
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(11, 5), layout="constrained")
        for method in methods:
            data = yearly.loc[yearly["method"].eq(method)].sort_values("year")
            ax.plot(data["year"], 100 * data["peak_week_relative_error"], marker="o", label=method, color=COLORS[method])
        ax.axhline(0, color="black", linewidth=0.7)
        ax.set(title="Leave-one-year-out peak-week reconstruction", xlabel="Held-out year",
               ylabel="Signed peak-week error [%]")
        ax.set_xticks(sorted(predictions))
        ax.legend()
        fig.savefig(destination / "fig4_loyo.png", dpi=150)
        plt.close(fig)

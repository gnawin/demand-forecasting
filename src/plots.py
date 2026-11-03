"""Fixed, slide-ready result figures."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .metrics import peak_window

COLORS = {"A1": "#0072B2", "A*": "#CC79A7", "A*b": "#D55E00", "A*b22": "#882255",
          "B": "#E69F00", "B+W": "#56B4E9", "C": "#009E73"}


def save_figures(
    predictions: dict[int, pd.DataFrame], yearly: pd.DataFrame, coldest: int,
    destination: Path, mode: str, units: str, external_years: list[int] | None = None,
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with plt.rc_context({"font.size": 12, "axes.labelsize": 12, "legend.fontsize": 12, "figure.dpi": 150}):
        frame = predictions[coldest]
        # Cross-year charts only show methods that ran in every held-out year.
        methods = [m for m in COLORS if yearly.loc[yearly["method"].eq(m), "year"].nunique() == len(predictions)]
        window = peak_window(frame["measured"], mode)
        fig, ax = plt.subplots(figsize=(11, 5), layout="constrained")
        ax.plot(window, frame.loc[window, "measured"], color="black", linewidth=2, label="Measured")
        shown = [name for name in COLORS if name in frame]
        for method in shown:
            ax.plot(window, frame.loc[window, method], color=COLORS[method], label=method, alpha=0.85)
        ax.set(title=f"Peak week, coldest held-out year ({coldest})", ylabel=f"Demand [{units}]", xlabel="UTC interval start")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b", tz="UTC"))
        ax.legend(ncol=min(5, len(shown) + 1))
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

        for year in external_years or []:
            frame = predictions[year]
            shown = [name for name in ("A*", "A*b", "A*b22", "B") if name in frame]
            window = peak_window(frame["measured"], mode)
            fig, (full, peak) = plt.subplots(2, 1, figsize=(11, 8), layout="constrained")
            for ax, rows in ((full, frame.index), (peak, window)):
                ax.plot(rows, frame.loc[rows, "measured"], color="black", linewidth=2, label="Measured")
                for method in shown:
                    width = 2 if method == "A*" else 1
                    ax.plot(rows, frame.loc[rows, method], color=COLORS[method], label=method, alpha=0.85, linewidth=width)
                ax.set(ylabel=f"Demand [{units}]")
            full.set_title(f"{year}: measured vs WPG-based methods and B, rescaled to the same annual total")
            full.xaxis.set_major_formatter(mdates.DateFormatter("%b", tz="UTC"))
            peak.set_title("Peak week")
            peak.xaxis.set_major_formatter(mdates.DateFormatter("%d %b", tz="UTC"))
            full.legend(ncol=len(shown) + 1)
            fig.savefig(destination / f"fig5_external_{year}.png", dpi=150)
            plt.close(fig)

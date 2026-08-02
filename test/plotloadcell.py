import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def plot_loadcell(csv_path: Path, remove_offset: bool):
    df = pd.read_csv(csv_path)

    print("CSV columns:")
    print(list(df.columns))

    # Find the time column.
    if "host_elapsed_s" in df.columns:
        time_s = pd.to_numeric(
            df["host_elapsed_s"],
            errors="coerce",
        ).to_numpy()

    elif "host_elapsed_ms" in df.columns:
        time_s = (
            pd.to_numeric(
                df["host_elapsed_ms"],
                errors="coerce",
            ).to_numpy()
            / 1000.0
        )

    else:
        raise ValueError(
            "CSV must contain either "
            "'host_elapsed_s' or 'host_elapsed_ms'."
        )

    # Find the load-cell data column.
    possible_value_columns = [
        "loadcell_raw",
        "loadcell",
        "modified_weight",
        "weight",
        "raw",
    ]

    value_column = None

    for column in possible_value_columns:
        if column in df.columns:
            value_column = column
            break

    if value_column is None:
        raise ValueError(
            "Could not find the load-cell column. "
            f"Expected one of: {possible_value_columns}"
        )

    loadcell = pd.to_numeric(
        df[value_column],
        errors="coerce",
    ).to_numpy()

    # Remove invalid rows.
    valid = np.isfinite(time_s) & np.isfinite(loadcell)

    time_s = time_s[valid]
    loadcell = loadcell[valid]

    if len(time_s) == 0:
        raise ValueError("No valid load-cell samples found.")

    # Make the plot begin at t = 0.
    time_s = time_s - time_s[0]

    if remove_offset:
        baseline_sample_count = min(100, len(loadcell))

        baseline = np.mean(
            loadcell[:baseline_sample_count]
        )

        plotted_loadcell = loadcell - baseline
        ylabel = "Load-cell reading minus baseline"

        print(f"Removed baseline: {baseline:.3f}")

    else:
        plotted_loadcell = loadcell
        ylabel = value_column

    duration_s = time_s[-1]

    if duration_s > 0 and len(time_s) > 1:
        sample_rate = (len(time_s) - 1) / duration_s
    else:
        sample_rate = 0.0

    print(f"Samples:             {len(time_s)}")
    print(f"Duration:            {duration_s:.3f} s")
    print(f"Average sample rate: {sample_rate:.2f} SPS")

    fig, ax = plt.subplots(figsize=(13, 6))

    ax.plot(
        time_s,
        plotted_loadcell,
        linewidth=0.8,
    )

    ax.set_title(
        f"Load-cell data — {sample_rate:.1f} SPS"
    )

    ax.set_xlabel("Elapsed time (s)")
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.3)

    # Displays precise values when moving the cursor.
    ax.format_coord = lambda x, y: (
        f"time = {x:.6f} s, load cell = {y:.3f}"
    )

    fig.tight_layout()

    print("\nPlot controls:")
    print("  Magnifying glass: zoom into a selected region")
    print("  Hand:              pan")
    print("  Home:              reset view")
    print("  Back/forward:      navigate previous views")

    plt.show()


def main():
    parser = argparse.ArgumentParser(
        description="Plot recorded load-cell CSV data."
    )

    parser.add_argument(
        "csv",
        type=Path,
        help="Path to the load-cell CSV file",
    )

    parser.add_argument(
        "--remove-offset",
        action="store_true",
        help="Subtract the mean of the first 100 samples",
    )

    args = parser.parse_args()

    if not args.csv.exists():
        raise FileNotFoundError(
            f"CSV file does not exist: {args.csv}"
        )

    plot_loadcell(
        args.csv,
        remove_offset=args.remove_offset,
    )


if __name__ == "__main__":
    main()
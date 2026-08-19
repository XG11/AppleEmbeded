import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def plot_loadcell(csv_path: Path, remove_offset: bool):
    df = pd.read_csv(csv_path)

    print("CSV columns:")
    print(list(df.columns))

    # ---------------------------------------------------------
    # Check required columns
    # ---------------------------------------------------------
    required_columns = [
        "host_elapsed_s",
        "type",
        "load_cell_raw",
    ]

    missing = [
        column
        for column in required_columns
        if column not in df.columns
    ]

    if missing:
        raise ValueError(
            f"Missing required columns: {missing}"
        )

    # ---------------------------------------------------------
    # Keep ONLY load-cell rows
    # ---------------------------------------------------------
    loadcell_df = df[
        df["type"].astype(str).str.lower() == "loadcell"
    ].copy()

    if len(loadcell_df) == 0:
        raise ValueError(
            "No rows with type='loadcell' were found."
        )

    # ---------------------------------------------------------
    # Convert columns to numeric
    # ---------------------------------------------------------
    time_s = pd.to_numeric(
        loadcell_df["host_elapsed_s"],
        errors="coerce",
    ).to_numpy(dtype=np.float64)

    loadcell = pd.to_numeric(
        loadcell_df["load_cell_raw"],
        errors="coerce",
    ).to_numpy(dtype=np.float64)

    # ---------------------------------------------------------
    # Remove invalid rows
    # ---------------------------------------------------------
    valid = (
        np.isfinite(time_s)
        & np.isfinite(loadcell)
    )

    time_s = time_s[valid]
    loadcell = loadcell[valid]

    if len(time_s) == 0:
        raise ValueError(
            "No valid load-cell samples found."
        )

    # ---------------------------------------------------------
    # Start plot time at t = 0
    # ---------------------------------------------------------
    time_s = time_s - time_s[0]

    # ---------------------------------------------------------
    # Optional baseline removal
    # ---------------------------------------------------------
    if remove_offset:
        baseline_sample_count = min(
            100,
            len(loadcell),
        )

        baseline = np.mean(
            loadcell[:baseline_sample_count]
        )

        plotted_loadcell = (
            loadcell - baseline
        )

        ylabel = (
            "Load-cell raw reading minus baseline"
        )

        print(
            f"Removed baseline: {baseline:.3f}"
        )

    else:
        plotted_loadcell = loadcell
        ylabel = "Load-cell raw ADC reading"

    # ---------------------------------------------------------
    # Calculate average sampling rate
    # ---------------------------------------------------------
    duration_s = time_s[-1]

    if duration_s > 0 and len(time_s) > 1:
        sample_rate = (
            (len(time_s) - 1)
            / duration_s
        )
    else:
        sample_rate = 0.0

    # Also calculate timing statistics.
    if len(time_s) > 1:
        dt = np.diff(time_s)

        valid_dt = dt[dt > 0]

        if len(valid_dt) > 0:
            median_rate = (
                1.0 / np.median(valid_dt)
            )
        else:
            median_rate = 0.0
    else:
        median_rate = 0.0

    print()
    print(
        f"Load-cell samples:    {len(time_s)}"
    )
    print(
        f"Duration:             {duration_s:.3f} s"
    )
    print(
        f"Average sample rate:  {sample_rate:.2f} SPS"
    )
    print(
        f"Median sample rate:   {median_rate:.2f} SPS"
    )

    # ---------------------------------------------------------
    # Plot
    # ---------------------------------------------------------
    fig, ax = plt.subplots(
        figsize=(13, 6)
    )

    ax.plot(
        time_s,
        plotted_loadcell,
        linewidth=0.8,
    )

    ax.set_title(
        f"Load-cell data — "
        f"{sample_rate:.1f} SPS"
    )

    ax.set_xlabel(
        "Elapsed time (s)"
    )

    ax.set_ylabel(
        ylabel
    )

    ax.grid(
        True,
        alpha=0.3,
    )

    # Show precise values in bottom-right corner
    # when moving the mouse.
    ax.format_coord = lambda x, y: (
        f"time = {x:.6f} s, "
        f"load cell = {y:.3f}"
    )

    fig.tight_layout()

    print()
    print("Plot controls:")
    print(
        "  Magnifying glass: zoom into a selected region"
    )
    print(
        "  Hand:              pan"
    )
    print(
        "  Home:              reset view"
    )
    print(
        "  Back/forward:      navigate previous views"
    )

    plt.show()


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Plot load-cell readings from "
            "combined IMU/load-cell CSV."
        )
    )

    parser.add_argument(
        "csv",
        type=Path,
        help="Path to imu_loadcell.csv",
    )

    parser.add_argument(
        "--remove-offset",
        action="store_true",
        help=(
            "Subtract the mean of the first "
            "100 load-cell samples"
        ),
    )

    args = parser.parse_args()

    if not args.csv.exists():
        raise FileNotFoundError(
            f"CSV file does not exist: "
            f"{args.csv}"
        )

    plot_loadcell(
        args.csv,
        remove_offset=args.remove_offset,
    )


if __name__ == "__main__":
    main()
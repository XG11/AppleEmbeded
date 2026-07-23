import argparse

import matplotlib.pyplot as plt
import pandas as pd


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv")
    args = parser.parse_args()

    df = pd.read_csv(args.csv)

    # Time axis
    if "host_elapsed_ms" in df.columns:
        time = df["host_elapsed_ms"]
        xlabel = "Time (ms)"
    elif "host_elapsed_s" in df.columns:
        time = df["host_elapsed_s"] * 1000.0
        xlabel = "Time (ms)"
    else:
        raise RuntimeError("No host_elapsed_ms or host_elapsed_s column found.")

    # Voltage axis
    if "voltage_mv" in df.columns:
        voltage = df["voltage_mv"]
    elif "adc_raw" in df.columns:
        # Convert 12-bit ADC counts to millivolts
        voltage = df["adc_raw"] * 3300.0 / 4095.0
    else:
        raise RuntimeError("No voltage_mv or adc_raw column found.")

    plt.figure(figsize=(14, 5))
    plt.plot(time, voltage, linewidth=0.6)

    plt.title("Piezo Waveform")
    plt.xlabel(xlabel)
    plt.ylabel("Voltage (mV)")
    plt.grid(True)

    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
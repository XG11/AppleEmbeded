#!/usr/bin/env python3

"""
analyze_audio_fft.py

Example:
    python analyze_audio_fft.py audio.wav

Analyze a selected interval:
    python analyze_audio_fft.py audio.wav --start 10 --duration 5

Show only frequencies below 5000 Hz:
    python analyze_audio_fft.py audio.wav --max-frequency 5000
"""

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from scipy import signal
from scipy.io import wavfile


def load_wav_mono(path):
    sample_rate, audio = wavfile.read(path)

    # Convert stereo/multichannel audio to mono.
    if audio.ndim > 1:
        audio = np.mean(audio.astype(np.float64), axis=1)
    else:
        audio = audio.astype(np.float64)

    # Normalize integer PCM audio.
    if np.issubdtype(audio.dtype, np.integer):
        max_value = max(
            abs(np.iinfo(audio.dtype).min),
            np.iinfo(audio.dtype).max,
        )
        audio = audio / max_value
    else:
        maximum = np.max(np.abs(audio))
        if maximum > 1:
            audio = audio / maximum

    return sample_rate, audio


def select_time_window(audio, sample_rate, start_s=0.0, duration_s=None):
    start_index = max(0, int(start_s * sample_rate))

    if duration_s is None:
        end_index = len(audio)
    else:
        end_index = min(
            len(audio),
            start_index + int(duration_s * sample_rate),
        )

    if start_index >= len(audio):
        raise ValueError("Selected start time is beyond the end of the audio.")

    selected = audio[start_index:end_index]
    time_s = np.arange(len(selected)) / sample_rate + start_s

    return time_s, selected


def compute_single_sided_fft(audio, sample_rate):
    """
    Compute a single-sided amplitude spectrum.

    A Hann window reduces spectral leakage.
    """
    audio = np.asarray(audio, dtype=float)

    # Remove DC offset.
    audio = audio - np.mean(audio)

    number_of_samples = len(audio)

    if number_of_samples < 2:
        raise ValueError("Not enough audio samples for FFT.")

    window = signal.windows.hann(number_of_samples)
    windowed_audio = audio * window

    fft_values = np.fft.rfft(windowed_audio)
    frequencies = np.fft.rfftfreq(
        number_of_samples,
        d=1.0 / sample_rate,
    )

    # Correct amplitude for window gain.
    coherent_gain = np.mean(window)
    amplitude = (
        2.0
        * np.abs(fft_values)
        / (number_of_samples * coherent_gain)
    )

    # DC and Nyquist terms should not be doubled.
    amplitude[0] /= 2.0

    if number_of_samples % 2 == 0:
        amplitude[-1] /= 2.0

    return frequencies, amplitude


def compute_power_spectrum_db(audio, sample_rate):
    frequencies, amplitude = compute_single_sided_fft(
        audio,
        sample_rate,
    )

    power = amplitude ** 2
    power_db = 10 * np.log10(power + 1e-20)

    return frequencies, power_db


def find_dominant_frequencies(
    frequencies,
    amplitude,
    minimum_frequency=20,
    maximum_frequency=None,
    number_of_peaks=10,
):
    valid = frequencies >= minimum_frequency

    if maximum_frequency is not None:
        valid &= frequencies <= maximum_frequency

    selected_frequencies = frequencies[valid]
    selected_amplitude = amplitude[valid]

    if len(selected_amplitude) < 3:
        return []

    # Ignore tiny peaks by requiring some prominence.
    prominence = 0.02 * np.max(selected_amplitude)

    peak_indices, properties = signal.find_peaks(
        selected_amplitude,
        prominence=prominence,
    )

    if len(peak_indices) == 0:
        return []

    peak_amplitudes = selected_amplitude[peak_indices]
    order = np.argsort(peak_amplitudes)[::-1]
    order = order[:number_of_peaks]

    peaks = []

    for index in order:
        peak_index = peak_indices[index]

        peaks.append(
            (
                selected_frequencies[peak_index],
                selected_amplitude[peak_index],
                properties["prominences"][index],
            )
        )

    return peaks


def compute_welch_psd(audio, sample_rate):
    """
    Welch PSD is usually more stable than a single FFT for noisy signals.
    """
    audio = audio - np.mean(audio)

    nperseg = min(8192, len(audio))

    frequencies, psd = signal.welch(
        audio,
        fs=sample_rate,
        window="hann",
        nperseg=nperseg,
        noverlap=nperseg // 2,
        detrend="constant",
        scaling="density",
    )

    psd_db = 10 * np.log10(psd + 1e-20)

    return frequencies, psd_db


def compute_spectrogram(audio, sample_rate):
    nperseg = min(2048, len(audio))
    noverlap = int(0.75 * nperseg)

    frequencies, times, spectrum = signal.spectrogram(
        audio,
        fs=sample_rate,
        window="hann",
        nperseg=nperseg,
        noverlap=noverlap,
        detrend="constant",
        scaling="density",
        mode="psd",
    )

    spectrum_db = 10 * np.log10(spectrum + 1e-20)

    return frequencies, times, spectrum_db


def plot_fft_results(
    time_s,
    audio,
    sample_rate,
    frequencies,
    amplitude,
    welch_frequencies,
    welch_db,
    output_path,
    max_frequency,
):
    fig, axes = plt.subplots(4, 1, figsize=(14, 16))

    # Waveform
    max_plot_points = 200000
    step = max(1, len(audio) // max_plot_points)

    axes[0].plot(
        time_s[::step],
        audio[::step],
        linewidth=0.6,
    )
    axes[0].set_title("Audio waveform")
    axes[0].set_xlabel("Time (s)")
    axes[0].set_ylabel("Amplitude")
    axes[0].grid(True, alpha=0.3)

    # Linear FFT amplitude
    valid_fft = frequencies <= max_frequency

    axes[1].plot(
        frequencies[valid_fft],
        amplitude[valid_fft],
        linewidth=0.8,
    )
    axes[1].set_title("Single-sided FFT amplitude spectrum")
    axes[1].set_xlabel("Frequency (Hz)")
    axes[1].set_ylabel("Amplitude")
    axes[1].grid(True, alpha=0.3)

    # Log-scale FFT
    valid_log = (
        (frequencies > 0)
        & (frequencies <= max_frequency)
    )

    axes[2].semilogx(
        frequencies[valid_log],
        20 * np.log10(amplitude[valid_log] + 1e-12),
        linewidth=0.8,
    )
    axes[2].set_title("FFT magnitude in decibels")
    axes[2].set_xlabel("Frequency (Hz)")
    axes[2].set_ylabel("Magnitude (dB)")
    axes[2].grid(True, alpha=0.3)

    # Welch PSD
    valid_welch = welch_frequencies <= max_frequency

    axes[3].plot(
        welch_frequencies[valid_welch],
        welch_db[valid_welch],
        linewidth=0.8,
    )
    axes[3].set_title("Welch power spectral density")
    axes[3].set_xlabel("Frequency (Hz)")
    axes[3].set_ylabel("PSD (dB/Hz)")
    axes[3].grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig(output_path, dpi=180)
    plt.close(fig)


def plot_spectrogram(
    audio,
    sample_rate,
    output_path,
    max_frequency,
    start_time_s,
):
    frequencies, times, spectrum_db = compute_spectrogram(
        audio,
        sample_rate,
    )

    valid = frequencies <= max_frequency

    fig, axis_plot = plt.subplots(figsize=(14, 6))

    image = axis_plot.pcolormesh(
        times + start_time_s,
        frequencies[valid],
        spectrum_db[valid, :],
        shading="auto",
    )

    axis_plot.set_title("Audio spectrogram")
    axis_plot.set_xlabel("Time (s)")
    axis_plot.set_ylabel("Frequency (Hz)")

    colorbar = fig.colorbar(image, ax=axis_plot)
    colorbar.set_label("Power spectral density (dB/Hz)")

    fig.tight_layout()
    fig.savefig(output_path, dpi=180)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(
        description="Perform FFT and spectral analysis on a WAV file."
    )

    parser.add_argument(
        "wav_file",
        type=Path,
        help="Input WAV file.",
    )

    parser.add_argument(
        "--start",
        type=float,
        default=0.0,
        help="Start time in seconds.",
    )

    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Duration to analyze in seconds.",
    )

    parser.add_argument(
        "--max-frequency",
        type=float,
        default=12000.0,
        help="Maximum displayed frequency in Hz.",
    )

    parser.add_argument(
        "--minimum-peak-frequency",
        type=float,
        default=20.0,
        help="Ignore detected FFT peaks below this frequency.",
    )

    parser.add_argument(
        "--number-of-peaks",
        type=int,
        default=10,
        help="Number of dominant frequency peaks to print.",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output folder.",
    )

    args = parser.parse_args()

    wav_path = args.wav_file.expanduser().resolve()

    if not wav_path.exists():
        raise FileNotFoundError(wav_path)

    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir
        else wav_path.parent / "audio_fft_analysis"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    sample_rate, full_audio = load_wav_mono(wav_path)

    time_s, audio = select_time_window(
        full_audio,
        sample_rate,
        start_s=args.start,
        duration_s=args.duration,
    )

    frequencies, amplitude = compute_single_sided_fft(
        audio,
        sample_rate,
    )

    welch_frequencies, welch_db = compute_welch_psd(
        audio,
        sample_rate,
    )

    dominant_peaks = find_dominant_frequencies(
        frequencies,
        amplitude,
        minimum_frequency=args.minimum_peak_frequency,
        maximum_frequency=args.max_frequency,
        number_of_peaks=args.number_of_peaks,
    )

    plot_fft_results(
        time_s,
        audio,
        sample_rate,
        frequencies,
        amplitude,
        welch_frequencies,
        welch_db,
        output_dir / "audio_fft_overview.png",
        args.max_frequency,
    )

    plot_spectrogram(
        audio,
        sample_rate,
        output_dir / "audio_spectrogram.png",
        args.max_frequency,
        args.start,
    )

    peak_csv_path = output_dir / "dominant_frequencies.csv"

    with peak_csv_path.open("w") as file:
        file.write("rank,frequency_hz,amplitude,prominence\n")

        for rank, (frequency, peak_amplitude, prominence) in enumerate(
            dominant_peaks,
            start=1,
        ):
            file.write(
                f"{rank},"
                f"{frequency:.6f},"
                f"{peak_amplitude:.12g},"
                f"{prominence:.12g}\n"
            )

    frequency_resolution = sample_rate / len(audio)

    print(f"File: {wav_path}")
    print(f"Sample rate: {sample_rate} Hz")
    print(f"Analyzed samples: {len(audio)}")
    print(f"Analyzed duration: {len(audio) / sample_rate:.6f} s")
    print(f"FFT frequency resolution: {frequency_resolution:.6f} Hz")

    print("\nDominant frequencies:")

    if not dominant_peaks:
        print("  No prominent peaks detected.")
    else:
        for rank, (frequency, peak_amplitude, prominence) in enumerate(
            dominant_peaks,
            start=1,
        ):
            print(
                f"  {rank:2d}. "
                f"{frequency:10.3f} Hz | "
                f"amplitude = {peak_amplitude:.6g}"
            )

    print(f"\nSaved outputs to: {output_dir}")


if __name__ == "__main__":
    main()
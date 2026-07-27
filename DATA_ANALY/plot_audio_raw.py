import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import soundfile as sf


def load_audio(wav_path: Path, channel: int):
    audio, sample_rate = sf.read(
        wav_path,
        always_2d=True,
        dtype="float32",
    )

    num_channels = audio.shape[1]

    if channel < 0 or channel >= num_channels:
        raise ValueError(
            f"Requested channel {channel}, but audio has "
            f"{num_channels} channel(s)."
        )

    signal = audio[:, channel]

    return signal, sample_rate, num_channels


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Plot a raw WAV waveform with interactive "
            "zooming and panning."
        )
    )

    parser.add_argument(
        "input",
        type=Path,
        help=(
            "Path to microphone.wav or a recording "
            "session directory."
        ),
    )

    parser.add_argument(
        "--channel",
        type=int,
        default=0,
        help="Audio channel to plot. Default: 0.",
    )

    parser.add_argument(
        "--start",
        type=float,
        default=0.0,
        help="Initial plot start time in seconds.",
    )

    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help=(
            "Initial displayed duration in seconds. "
            "Default: entire recording."
        ),
    )

    parser.add_argument(
        "--normalize",
        action="store_true",
        help="Normalize the displayed signal to ±1.",
    )

    args = parser.parse_args()

    input_path = args.input.expanduser().resolve()

    if input_path.is_dir():
        wav_path = input_path / "rode_microphone.wav"
    else:
        wav_path = input_path

    if not wav_path.exists():
        raise FileNotFoundError(
            f"Audio file not found: {wav_path}"
        )

    signal, sample_rate, num_channels = load_audio(
        wav_path,
        args.channel,
    )

    total_samples = len(signal)
    total_duration = total_samples / sample_rate

    start_sample = int(args.start * sample_rate)
    start_sample = max(0, start_sample)
    start_sample = min(start_sample, total_samples)

    if args.duration is None:
        end_sample = total_samples
    else:
        end_sample = start_sample + int(
            args.duration * sample_rate
        )
        end_sample = min(end_sample, total_samples)

    displayed_signal = signal[start_sample:end_sample]

    if len(displayed_signal) == 0:
        raise ValueError(
            "The selected time range contains no samples."
        )

    if args.normalize:
        peak = np.max(np.abs(displayed_signal))

        if peak > 0:
            displayed_signal = displayed_signal / peak

    sample_indices = np.arange(
        start_sample,
        end_sample,
    )

    time_s = sample_indices / sample_rate

    print(f"Audio file:       {wav_path}")
    print(f"Sample rate:      {sample_rate} Hz")
    print(f"Channels:         {num_channels}")
    print(f"Selected channel: {args.channel}")
    print(f"Samples:          {total_samples}")
    print(f"Total duration:   {total_duration:.3f} s")
    print(
        f"Displayed range:  "
        f"{time_s[0]:.6f} to {time_s[-1]:.6f} s"
    )

    figure, axis = plt.subplots(
        figsize=(14, 6)
    )

    axis.plot(
        time_s,
        displayed_signal,
        linewidth=0.6,
    )

    axis.set_xlabel("Time (s)")
    axis.set_ylabel(
        "Normalized amplitude"
        if args.normalize
        else "Amplitude"
    )
    axis.set_title(
        f"Raw Audio Waveform — {wav_path.name}"
    )
    axis.grid(True)
    axis.margins(x=0)

    figure.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
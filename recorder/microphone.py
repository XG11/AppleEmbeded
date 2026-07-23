import csv
import queue
import threading
import wave
from pathlib import Path
from typing import Optional

import sounddevice as sd

from .shared import SharedRecordingState, monotonic_ns


class MicrophoneRecorder:
    """Record one host-visible audio input device to WAV plus block timestamps."""

    def __init__(
        self,
        state: SharedRecordingState,
        name: str,
        device_index: Optional[int],
        wav_path: Path,
        timestamp_path: Path,
        sample_rate: int,
        channels: int,
        block_size: int = 1024,
        queue_size: int = 512,
    ) -> None:
        self.state = state
        self.name = name
        self.device_index = device_index
        self.wav_path = Path(wav_path)
        self.timestamp_path = Path(timestamp_path)
        self.sample_rate = sample_rate
        self.channels = channels
        self.block_size = block_size

        self.audio_queue = queue.Queue(maxsize=queue_size)
        self.ready_event = threading.Event()
        self.thread: Optional[threading.Thread] = None

        self.block_count = 0
        self.sample_count = 0
        self.overflow_count = 0
        self.status_count = 0
        self.actual_device_name = ""

    def start(self) -> None:
        self.thread = threading.Thread(
            target=self._run,
            name=f"{self.name}-recorder",
            daemon=True,
        )
        self.thread.start()

    def join(self, timeout: Optional[float] = None) -> None:
        if self.thread is not None:
            self.thread.join(timeout=timeout)

    def is_alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def _audio_callback(self, input_data, frames, time_info, status) -> None:
        del time_info

        if status:
            self.status_count += 1
            print(f"{self.name} audio status: {status}")

        if not self.state.start_event.is_set():
            return
        if self.state.stop_event.is_set():
            return

        try:
            self.audio_queue.put_nowait(
                (monotonic_ns(), input_data.copy(), frames)
            )
        except queue.Full:
            self.overflow_count += 1
            self.state.report_error(
                self.name,
                "Audio queue overflow",
            )

    def _run(self) -> None:
        try:
            self.wav_path.parent.mkdir(parents=True, exist_ok=True)
            self.timestamp_path.parent.mkdir(parents=True, exist_ok=True)

            device_info = sd.query_devices(self.device_index, "input")
            self.actual_device_name = str(device_info["name"])

            max_input_channels = int(device_info["max_input_channels"])
            if self.channels > max_input_channels:
                raise RuntimeError(
                    f"requested {self.channels} channels, but device only "
                    f"provides {max_input_channels}"
                )

            sd.check_input_settings(
                device=self.device_index,
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="int16",
            )

            with wave.open(str(self.wav_path), "wb") as wav_file, \
                    self.timestamp_path.open("w", newline="") as timestamp_file:
                wav_file.setnchannels(self.channels)
                wav_file.setsampwidth(2)
                wav_file.setframerate(self.sample_rate)

                writer = csv.writer(timestamp_file)
                writer.writerow(
                    [
                        "block_index",
                        "first_sample_index",
                        "frames",
                        "host_time_ns",
                        "host_elapsed_s",
                    ]
                )

                with sd.InputStream(
                    device=self.device_index,
                    samplerate=self.sample_rate,
                    channels=self.channels,
                    dtype="int16",
                    blocksize=self.block_size,
                    callback=self._audio_callback,
                ):
                    print(
                        f"{self.name} opened: {self.actual_device_name}; "
                        f"{self.sample_rate} Hz, {self.channels} channel(s)"
                    )
                    self.ready_event.set()
                    self.state.start_event.wait()

                    first_sample_index = 0

                    while (
                        not self.state.stop_event.is_set()
                        or not self.audio_queue.empty()
                    ):
                        try:
                            host_time_ns, block, frames = self.audio_queue.get(
                                timeout=0.1
                            )
                        except queue.Empty:
                            continue

                        wav_file.writeframes(block.tobytes())

                        host_elapsed_s = (
                            host_time_ns - self.state.session_t0_ns
                        ) / 1_000_000_000.0

                        writer.writerow(
                            [
                                self.block_count,
                                first_sample_index,
                                frames,
                                host_time_ns,
                                f"{host_elapsed_s:.9f}",
                            ]
                        )

                        self.block_count += 1
                        self.sample_count += frames
                        first_sample_index += frames

                timestamp_file.flush()

        except Exception as exc:
            self.ready_event.set()
            self.state.report_error(
                self.name,
                f"{type(exc).__name__}: {exc}",
            )
import csv
import threading
import time
from pathlib import Path
from typing import Optional

import serial

from .shared import SharedRecordingState, monotonic_ns


class PiezoRecorder:
    def __init__(
        self,
        state: SharedRecordingState,
        serial_port: str,
        output_path: Path,
        baud_rate: int = 2000000,
        startup_delay_s: float = 1.5,
    ):
        self.state = state

        self.serial_port = serial_port
        self.output_path = Path(output_path)

        self.baud_rate = baud_rate
        self.startup_delay_s = startup_delay_s

        self.ready_event = threading.Event()
        self.thread = None

        self.sample_count = 0
        self.invalid_line_count = 0

    def start(self):
        self.thread = threading.Thread(
            target=self._run,
            daemon=True,
        )
        self.thread.start()

    def join(self, timeout=None):
        if self.thread is not None:
            self.thread.join(timeout)

    def is_alive(self) -> bool:
        return (
            self.thread is not None
            and self.thread.is_alive()
        )

    def _run(self):

        try:

            self.output_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            with serial.Serial(
                self.serial_port,
                self.baud_rate,
                timeout=0.1,
            ) as ser, self.output_path.open(
                "w",
                newline="",
            ) as csv_file:

                writer = csv.writer(csv_file)

                writer.writerow([
                    "host_time_ns",
                    "host_elapsed_s",
                    "adc_raw",
                ])

                time.sleep(self.startup_delay_s)
                ser.reset_input_buffer()

                self.ready_event.set()
                self.state.start_event.wait()

                while not self.state.stop_event.is_set():

                    raw = ser.readline()

                    if not raw:
                        continue

                    host_time_ns = monotonic_ns()

                    try:

                        value = int(
                            raw.decode(
                                "ascii",
                                errors="ignore",
                            ).strip()
                        )

                    except ValueError:

                        self.invalid_line_count += 1
                        continue

                    host_elapsed_s = (
                        host_time_ns
                        - self.state.session_t0_ns
                    ) / 1e9

                    writer.writerow([
                        host_time_ns,
                        f"{host_elapsed_s:.9f}",
                        value,
                    ])

                    self.sample_count += 1

                csv_file.flush()

        except Exception as exc:

            self.ready_event.set()

            self.state.report_error(
                "Piezo",
                f"{type(exc).__name__}: {exc}",
            )
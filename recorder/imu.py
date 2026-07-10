import csv
import threading
import time
from pathlib import Path
from typing import Optional

import serial

from .shared import SharedRecordingState, monotonic_ns


class IMURecorder:
    def __init__(
        self,
        state: SharedRecordingState,
        serial_port: str,
        output_path: Path,
        baud_rate: int = 921600,
        startup_delay_s: float = 1.5,
    ) -> None:
        self.state = state
        self.serial_port = serial_port
        self.output_path = Path(output_path)
        self.baud_rate = baud_rate
        self.startup_delay_s = startup_delay_s

        self.ready_event = threading.Event()
        self.thread: Optional[threading.Thread] = None

        self.sample_count = 0
        self.accel_count = 0
        self.gyro_count = 0
        self.invalid_line_count = 0

    def start(self) -> None:
        self.thread = threading.Thread(
            target=self._run,
            name="imu-recorder",
            daemon=True,
        )
        self.thread.start()

    def join(self, timeout: Optional[float] = None) -> None:
        if self.thread is not None:
            self.thread.join(timeout=timeout)

    def is_alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def _run(self) -> None:
        try:
            self.output_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            with serial.Serial(
                port=self.serial_port,
                baudrate=self.baud_rate,
                timeout=0.1,
            ) as serial_port, self.output_path.open(
                "w",
                newline="",
            ) as csv_file:

                writer = csv.writer(csv_file)

                writer.writerow(
                    [
                        "host_time_ns",
                        "host_elapsed_s",
                        "teensy_time_us",
                        "type",
                        "x_raw",
                        "y_raw",
                        "z_raw",
                        "fifo_remaining",
                    ]
                )

                # Teensy can reset when the serial connection opens.
                time.sleep(self.startup_delay_s)
                serial_port.reset_input_buffer()

                self.ready_event.set()
                self.state.start_event.wait()

                while not self.state.stop_event.is_set():
                    raw_line = serial_port.readline()

                    if not raw_line:
                        continue

                    host_time_ns = monotonic_ns()

                    try:
                        line = raw_line.decode(
                            "utf-8",
                            errors="ignore",
                        ).strip()

                        if not line:
                            continue

                        fields = line.split(",")

                        # Expected firmware output:
                        #
                        # timestamp_us,type,x_raw,y_raw,z_raw,fifo_remaining
                        if len(fields) < 5:
                            self.invalid_line_count += 1
                            continue

                        if not fields[0].isdigit():
                            self.invalid_line_count += 1
                            continue

                        teensy_time_us = int(fields[0])
                        sample_type = fields[1].strip()

                        x_raw = int(fields[2])
                        y_raw = int(fields[3])
                        z_raw = int(fields[4])

                        fifo_remaining = ""

                        if len(fields) >= 6:
                            fifo_remaining = fields[5].strip()

                        host_elapsed_s = (
                            host_time_ns - self.state.session_t0_ns
                        ) / 1_000_000_000.0

                        writer.writerow(
                            [
                                host_time_ns,
                                f"{host_elapsed_s:.9f}",
                                teensy_time_us,
                                sample_type,
                                x_raw,
                                y_raw,
                                z_raw,
                                fifo_remaining,
                            ]
                        )

                        self.sample_count += 1

                        if sample_type == "accel":
                            self.accel_count += 1
                        elif sample_type == "gyro":
                            self.gyro_count += 1

                    except (ValueError, IndexError):
                        self.invalid_line_count += 1

                csv_file.flush()

        except Exception as exc:
            self.ready_event.set()
            self.state.report_error(
                "IMU",
                f"{type(exc).__name__}: {exc}",
            )
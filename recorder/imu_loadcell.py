import csv
import threading
import time
from pathlib import Path
from typing import Optional

import serial

from .shared import SharedRecordingState, monotonic_ns


class IMULoadCellRecorder:
    """
    Record IMU and ADS1220 packets from one Teensy.

    Expected firmware packets:

        I,timestamp_us,
        acc_x,acc_y,acc_z,
        gyro_x,gyro_y,gyro_z

        L,timestamp_us,load_cell_raw

    Output CSV:

        host_time_ns,
        host_elapsed_s,
        teensy_time_us,
        type,
        acc_x_raw,acc_y_raw,acc_z_raw,
        gyro_x_raw,gyro_y_raw,gyro_z_raw,
        load_cell_raw
    """

    def __init__(
        self,
        state: SharedRecordingState,
        serial_port: str,
        output_path: Path,
        baud_rate: int = 2_000_000,
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
        self.imu_count = 0
        self.accel_count = 0
        self.gyro_count = 0
        self.load_cell_count = 0
        self.invalid_line_count = 0

    def start(self) -> None:
        self.thread = threading.Thread(
            target=self._run,
            name="imu-loadcell-recorder",
            daemon=True,
        )
        self.thread.start()

    def join(
        self,
        timeout: Optional[float] = None,
    ) -> None:
        if self.thread is not None:
            self.thread.join(timeout=timeout)

    def is_alive(self) -> bool:
        return (
            self.thread is not None
            and self.thread.is_alive()
        )

    @staticmethod
    def _parse_line(line: str):
        line = line.strip()

        if not line:
            return None

        # Ignore diagnostic lines printed by firmware.
        if line.startswith("#"):
            return None

        fields = [
            field.strip()
            for field in line.split(",")
        ]

        packet_type = fields[0]

        if packet_type == "I":
            if len(fields) != 8:
                return None

            try:
                values = tuple(
                    int(field)
                    for field in fields[1:]
                )
            except ValueError:
                return None

            (
                teensy_time_us,
                acc_x_raw,
                acc_y_raw,
                acc_z_raw,
                gyro_x_raw,
                gyro_y_raw,
                gyro_z_raw,
            ) = values

            imu_values = (
                acc_x_raw,
                acc_y_raw,
                acc_z_raw,
                gyro_x_raw,
                gyro_y_raw,
                gyro_z_raw,
            )

            if not all(
                -32768 <= value <= 32767
                for value in imu_values
            ):
                return None

            if not 0 <= teensy_time_us <= 0xFFFFFFFF:
                return None

            return {
                "type": "imu",
                "teensy_time_us": teensy_time_us,
                "acc_x_raw": acc_x_raw,
                "acc_y_raw": acc_y_raw,
                "acc_z_raw": acc_z_raw,
                "gyro_x_raw": gyro_x_raw,
                "gyro_y_raw": gyro_y_raw,
                "gyro_z_raw": gyro_z_raw,
                "load_cell_raw": "",
            }

        if packet_type == "L":
            if len(fields) != 3:
                return None

            try:
                teensy_time_us = int(fields[1])
                load_cell_raw = int(fields[2])
            except ValueError:
                return None

            if not 0 <= teensy_time_us <= 0xFFFFFFFF:
                return None

            if not (
                -8_388_608
                <= load_cell_raw
                <= 8_388_607
            ):
                return None

            return {
                "type": "loadcell",
                "teensy_time_us": teensy_time_us,
                "acc_x_raw": "",
                "acc_y_raw": "",
                "acc_z_raw": "",
                "gyro_x_raw": "",
                "gyro_y_raw": "",
                "gyro_z_raw": "",
                "load_cell_raw": load_cell_raw,
            }

        return None

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
                        "acc_x_raw",
                        "acc_y_raw",
                        "acc_z_raw",
                        "gyro_x_raw",
                        "gyro_y_raw",
                        "gyro_z_raw",
                        "load_cell_raw",
                    ]
                )

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
                            "ascii",
                            errors="strict",
                        )
                    except UnicodeDecodeError:
                        self.invalid_line_count += 1
                        continue

                    # Do not count firmware diagnostic lines as invalid.
                    if line.lstrip().startswith("#"):
                        continue

                    parsed = self._parse_line(line)

                    if parsed is None:
                        self.invalid_line_count += 1
                        continue

                    host_elapsed_s = (
                        host_time_ns
                        - self.state.session_t0_ns
                    ) / 1_000_000_000.0

                    writer.writerow(
                        [
                            host_time_ns,
                            f"{host_elapsed_s:.9f}",
                            parsed["teensy_time_us"],
                            parsed["type"],
                            parsed["acc_x_raw"],
                            parsed["acc_y_raw"],
                            parsed["acc_z_raw"],
                            parsed["gyro_x_raw"],
                            parsed["gyro_y_raw"],
                            parsed["gyro_z_raw"],
                            parsed["load_cell_raw"],
                        ]
                    )

                    self.sample_count += 1

                    if parsed["type"] == "imu":
                        self.imu_count += 1
                        self.accel_count += 1
                        self.gyro_count += 1
                    else:
                        self.load_cell_count += 1

                csv_file.flush()

        except Exception as exc:
            self.ready_event.set()

            self.state.report_error(
                "IMU + ADS1220",
                f"{type(exc).__name__}: {exc}",
            )
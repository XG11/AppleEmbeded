import csv
import threading
import time
from pathlib import Path
from typing import Optional

import serial

from .shared import SharedRecordingState, monotonic_ns


class IMULoadCellRecorder:
    """
    Record paired IMU and HX711 data from one Teensy serial port.

    Expected Teensy packet:

        timestamp_us,
        acc_x,acc_y,acc_z,
        gyro_x,gyro_y,gyro_z,
        load_cell_raw

    Output CSV:

        host_time_ns,
        host_elapsed_s,
        teensy_time_us,
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

    def join(self, timeout: Optional[float] = None) -> None:
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

        fields = line.split(",")

        # Firmware prints exactly eight integer fields.
        if len(fields) != 8:
            return None

        try:
            values = tuple(
                int(field.strip())
                for field in fields
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
            load_cell_raw,
        ) = values

        imu_values = (
            acc_x_raw,
            acc_y_raw,
            acc_z_raw,
            gyro_x_raw,
            gyro_y_raw,
            gyro_z_raw,
        )

        # IMU channels originate from int16_t.
        if not all(
            -32768 <= value <= 32767
            for value in imu_values
        ):
            return None

        # micros() is an unsigned 32-bit value.
        if not 0 <= teensy_time_us <= 0xFFFFFFFF:
            return None

        # HX711 is a signed 24-bit ADC.
        if not -8_388_608 <= load_cell_raw <= 8_388_607:
            return None

        return values

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
                        "acc_x_raw",
                        "acc_y_raw",
                        "acc_z_raw",
                        "gyro_x_raw",
                        "gyro_y_raw",
                        "gyro_z_raw",
                        "load_cell_raw",
                    ]
                )

                # Opening USB serial may reset the Teensy.
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

                    parsed = self._parse_line(line)

                    if parsed is None:
                        self.invalid_line_count += 1
                        continue

                    (
                        teensy_time_us,
                        acc_x_raw,
                        acc_y_raw,
                        acc_z_raw,
                        gyro_x_raw,
                        gyro_y_raw,
                        gyro_z_raw,
                        load_cell_raw,
                    ) = parsed

                    host_elapsed_s = (
                        host_time_ns
                        - self.state.session_t0_ns
                    ) / 1_000_000_000.0

                    writer.writerow(
                        [
                            host_time_ns,
                            f"{host_elapsed_s:.9f}",
                            teensy_time_us,
                            acc_x_raw,
                            acc_y_raw,
                            acc_z_raw,
                            gyro_x_raw,
                            gyro_y_raw,
                            gyro_z_raw,
                            load_cell_raw,
                        ]
                    )

                    self.sample_count += 1
                    self.accel_count += 1
                    self.gyro_count += 1

                    if load_cell_raw != -1:
                        self.load_cell_count += 1

                csv_file.flush()

        except Exception as exc:
            self.ready_event.set()

            self.state.report_error(
                "IMU + HX711",
                f"{type(exc).__name__}: {exc}",
            )
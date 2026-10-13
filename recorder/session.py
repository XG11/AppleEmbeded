import json
import signal
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from .force_torque import ForceTorqueRecorder
from .gelsight import GelSightRecorder
from .imu_loadcell import IMULoadCellRecorder
from .microphone import MicrophoneRecorder
# from .piezo import PiezoRecorder
from .shared import SharedRecordingState, monotonic_ns


class RecordingSession:
    def __init__(
        self,
        duration_s: float,
        imu_port: str,
        #piezo_port: str,
        ft_port: Optional[str],
        rode_audio_device: Optional[int],
        camera_index: int,
        output_root: Path = Path("recordings"),
        imu_baud: int = 2_000_000,
        piezo_baud: int = 2_000_000,
        ft_enabled: bool = True,
        ft_tare_path: Path = Path("mms101/tare_offsets.json"),
        ft_interval_us: int = 1000,
        ft_restart_samples: int = 100,
        camera_width: int = 0,
        camera_height: int = 0,
        camera_fps: float = 30.0,
        rode_sample_rate: int = 48_000,
        rode_channels: int = 1,
        rode_block_size: int = 1024,
    ) -> None:
        self.duration_s = duration_s

        # IMU + mark button share one Teensy serial port.
        self.imu_port = imu_port
        self.imu_baud = imu_baud

        # Piezo uses a separate Teensy serial port.
        #self.piezo_port = piezo_port
        #self.piezo_baud = piezo_baud

        # MMS101 force/torque sensor, on its own USB-serial (FTDI) port.
        # Power-on, warm-up, and taring are done once, externally, by
        # mms101/warmup_and_tare.py -- see recorder/force_torque.py.
        #
        # ft_enabled=False means the sensor is handled entirely outside
        # this process -- auto_record_launcher.py streams it
        # continuously across the whole trial loop and writes
        # force_torque.csv itself after this session finishes (see
        # --ft-external in record_multimodal.py). This process then
        # never touches the F/T serial port at all.
        self.ft_enabled = ft_enabled
        self.ft_port = ft_port
        self.ft_tare_path = Path(ft_tare_path)
        self.ft_interval_us = ft_interval_us
        self.ft_restart_samples = ft_restart_samples

        self.camera_index = camera_index
        self.camera_width = camera_width
        self.camera_height = camera_height
        self.camera_fps = camera_fps

        self.rode_audio_device = rode_audio_device
        self.rode_sample_rate = rode_sample_rate
        self.rode_channels = rode_channels
        self.rode_block_size = rode_block_size

        self.output_root = Path(output_root)

        self.session_name = datetime.now().strftime(
            "session_%Y%m%d_%H%M%S"
        )

        self.session_dir = (
            self.output_root / self.session_name
        )

        self.state = SharedRecordingState()

        self.imu_recorder: Optional[
            IMULoadCellRecorder
        ] = None

        #self.piezo_recorder: Optional[
        #    PiezoRecorder
        #] = None

        self.ft_recorder: Optional[
            ForceTorqueRecorder
        ] = None

        self.gelsight_recorder: Optional[
            GelSightRecorder
        ] = None

        self.rode_recorder: Optional[
            MicrophoneRecorder
        ] = None

    # -----------------------------------------------------------------
    # Recorder creation
    # -----------------------------------------------------------------

    def _create_recorders(self) -> None:
        self.imu_recorder = IMULoadCellRecorder(
            state=self.state,
            serial_port=self.imu_port,
            baud_rate=self.imu_baud,
            output_path=(
                self.session_dir
                / "imu_loadcell.csv"
            ),
        )

        # self.piezo_recorder = PiezoRecorder(
        #     state=self.state,
        #     serial_port=self.piezo_port,
        #     baud_rate=self.piezo_baud,
        #     output_path=(
        #         self.session_dir
        #         / "piezo.csv"
        #     ),
        # )

        if self.ft_enabled:
            self.ft_recorder = ForceTorqueRecorder(
                state=self.state,
                serial_port=self.ft_port,
                tare_path=self.ft_tare_path,
                expected_interval_us=self.ft_interval_us,
                expected_restart_samples=self.ft_restart_samples,
                output_path=(
                    self.session_dir
                    / "force_torque.csv"
                ),
            )

        self.gelsight_recorder = GelSightRecorder(
            state=self.state,
            camera_index=self.camera_index,
            video_path=(
                self.session_dir
                / "gelsight.avi"
            ),
            timestamp_path=(
                self.session_dir
                / "gelsight_timestamps.csv"
            ),
            width=self.camera_width,
            height=self.camera_height,
            fps=self.camera_fps,
        )

        self.rode_recorder = MicrophoneRecorder(
            state=self.state,
            name="RODE microphone",
            device_index=self.rode_audio_device,
            wav_path=(
                self.session_dir
                / "rode_microphone.wav"
            ),
            timestamp_path=(
                self.session_dir
                / "rode_microphone_timestamps.csv"
            ),
            sample_rate=self.rode_sample_rate,
            channels=self.rode_channels,
            block_size=self.rode_block_size,
        )

    def _recorders(self):
        recorders = [
            self.imu_recorder,
            #self.piezo_recorder,
            self.ft_recorder,
            self.gelsight_recorder,
            self.rode_recorder,
        ]

        return [
            recorder
            for recorder in recorders
            if recorder is not None
        ]

    # -----------------------------------------------------------------
    # Signal handling
    # -----------------------------------------------------------------

    def _install_signal_handlers(self) -> None:
        def stop_handler(signum, frame) -> None:
            del signum, frame

            print("\nStop requested.")
            self.state.stop_event.set()

        signal.signal(
            signal.SIGINT,
            stop_handler,
        )

        signal.signal(
            signal.SIGTERM,
            stop_handler,
        )

    # -----------------------------------------------------------------
    # Recorder startup
    # -----------------------------------------------------------------

    def _wait_for_recorders(
        self,
        timeout_s: float = 15.0,
    ) -> None:
        deadline = time.perf_counter() + timeout_s

        for recorder in self._recorders():
            remaining = (
                deadline - time.perf_counter()
            )

            if remaining <= 0:
                raise RuntimeError(
                    "Timed out while waiting for recorders."
                )

            if not recorder.ready_event.wait(remaining):
                raise RuntimeError(
                    f"{type(recorder).__name__} "
                    "did not become ready."
                )

        errors = self.state.get_errors()

        if errors:
            error_text = "\n".join(
                f"{source}: {message}"
                for source, message in errors
            )

            raise RuntimeError(
                "Device initialization failed:\n"
                + error_text
            )

    # -----------------------------------------------------------------
    # Metadata
    # -----------------------------------------------------------------

    def _force_torque_metadata(self) -> dict:
        common = {
            "device": "Mitsumi MMS101",
            "output_file": "force_torque.csv",

            "csv_columns": [
                "host_time_ns",
                "host_elapsed_s",
                "device_time_us",
                "Fx",
                "Fy",
                "Fz",
                "Mx",
                "My",
                "Mz",
            ],

            "units": {
                "Fx": "N",
                "Fy": "N",
                "Fz": "N",
                "Mx": "N*m",
                "My": "N*m",
                "Mz": "N*m",
            },

            "calibrated": True,
        }

        if not self.ft_enabled:
            return {
                **common,
                "external": True,
                "serial_port": self.ft_port,
                "description": (
                    "Streamed continuously across the whole trial "
                    "loop by auto_record_launcher.py (see "
                    "--ft-external), not by this process -- "
                    "force_torque.csv for this session is written by "
                    "the launcher after this process exits, by "
                    "slicing this trial's time window out of that "
                    "continuous stream."
                ),
            }

        tare_metadata = (
            self.ft_recorder.tare_metadata
            if self.ft_recorder is not None
            else None
        )

        return {
            **common,
            "serial_port": self.ft_port,
            "interval_us": self.ft_interval_us,
            "restart_samples": self.ft_restart_samples,

            "tare": {
                "source_file": (
                    str(self.ft_recorder.tare_path)
                    if self.ft_recorder is not None
                    else None
                ),
                "offsets": (
                    self.ft_recorder.tare_offsets
                    if self.ft_recorder is not None
                    else None
                ),
                "captured_at": (
                    tare_metadata.get("created_at")
                    if tare_metadata
                    else None
                ),
                "sample_count": (
                    tare_metadata.get("sample_count")
                    if tare_metadata
                    else None
                ),
                "warmup_seconds": (
                    tare_metadata.get("warmup_seconds")
                    if tare_metadata
                    else None
                ),
            },

            "description": (
                "Connected directly to the host over its own "
                "USB-serial (FTDI) port, independent of the "
                "IMU/button Teensy. Values are already calibrated "
                "by the sensor board, and already have the tare "
                "offsets below subtracted. Power-on, warm-up, "
                "calibration bootload, and taring were all done "
                "once by mms101/warmup_and_tare.py before this "
                "session."
            ),
        }

    def _write_metadata(
        self,
        wall_start: datetime,
    ) -> None:
        if self.ft_enabled and self.ft_recorder is None:
            raise RuntimeError(
                "Force/torque recorder was not initialized."
            )

        if self.gelsight_recorder is None:
            raise RuntimeError(
                "GelSight recorder was not initialized."
            )

        if self.rode_recorder is None:
            raise RuntimeError(
                "RODE recorder was not initialized."
            )

        metadata = {
            "session_name": self.session_name,
            "wall_start_iso": (
                wall_start.isoformat()
            ),
            "host_monotonic_start_ns": (
                self.state.session_t0_ns
            ),
            "requested_duration_s": (
                self.duration_s
            ),

            "imu_loadcell": {
                "serial_port": self.imu_port,
                "baud_rate": self.imu_baud,
                "output_file": "imu_loadcell.csv",

                "serial_packet_format": [
                    "teensy_time_us",
                    "acc_x_raw",
                    "acc_y_raw",
                    "acc_z_raw",
                    "gyro_x_raw",
                    "gyro_y_raw",
                    "gyro_z_raw",
                    "load_cell_raw",
                ],

                "csv_columns": [
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
                ],

                "button_configuration": {
                    "pin": 22,
                    "wiring": "pin pulled down, button ties pin to 3.3V",
                    "packet_type": "button",
                    "description": (
                        "One row is written per button press "
                        "(rising edge only), marking the moment "
                        "the operator started pushing down."
                    ),
                },

                "accelerometer_configuration": {
                    "device": "LSM6DSO32",
                    "full_scale_g": 32,
                    "sensitivity_g_per_lsb": (
                        0.000976
                    ),
                },

                "gyroscope_configuration": {
                    "device": "LSM6DSO32",
                    "full_scale_dps": 2000,
                    "sensitivity_dps_per_lsb": (
                        0.070
                    ),
                },

                "load_cell_configuration": {
                    "adc": "ADS1220",
                    "raw_range_min": -8_388_608,
                    "raw_range_max": 8_388_607,
                    "calibrated": False,
                    "removed": True,
                    "removed_note": (
                        "The ADS1220 load cell was removed from this "
                        "Teensy's firmware in favor of the MMS101 "
                        "force/torque sensor (see the force_torque "
                        "section below). load_cell_raw is always empty."
                    ),
                },
            },

            # "piezo": {
            #     "serial_port": self.piezo_port,
            #     "baud_rate": self.piezo_baud,
            #     "output_file": "piezo.csv",
            # },

            "force_torque": self._force_torque_metadata(),

            "gelsight": {
                "camera_index": (
                    self.camera_index
                ),
                "requested_width": (
                    self.camera_width
                ),
                "requested_height": (
                    self.camera_height
                ),
                "requested_fps": (
                    self.camera_fps
                ),
                "actual_width": (
                    self.gelsight_recorder
                    .actual_width
                ),
                "actual_height": (
                    self.gelsight_recorder
                    .actual_height
                ),
                "actual_fps": (
                    self.gelsight_recorder
                    .actual_fps
                ),
                "video_file": "gelsight.avi",
                "timestamp_file": (
                    "gelsight_timestamps.csv"
                ),
            },

            "rode_microphone": {
                "device_index": (
                    self.rode_audio_device
                ),
                "device_name": (
                    self.rode_recorder
                    .actual_device_name
                ),
                "sample_rate_hz": (
                    self.rode_sample_rate
                ),
                "channels": (
                    self.rode_channels
                ),
                "block_size": (
                    self.rode_block_size
                ),
                "audio_file": (
                    "rode_microphone.wav"
                ),
                "timestamp_file": (
                    "rode_microphone_timestamps.csv"
                ),
            },
        }

        metadata_path = (
            self.session_dir / "metadata.json"
        )

        with metadata_path.open(
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                metadata,
                file,
                indent=2,
            )

    # -----------------------------------------------------------------
    # Recorder shutdown
    # -----------------------------------------------------------------

    def _join_recorders(self) -> None:
        for recorder in self._recorders():
            recorder.join(timeout=5.0)

            if recorder.is_alive():
                print(
                    "Warning: "
                    f"{type(recorder).__name__} "
                    "did not stop within five seconds."
                )

    # -----------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------

    @staticmethod
    def _print_audio_summary(
        label: str,
        recorder: MicrophoneRecorder,
    ) -> None:
        print(
            f"{label} samples: "
            f"{recorder.sample_count}"
        )

        print(
            f"{label} blocks: "
            f"{recorder.block_count}"
        )

        print(
            f"{label} queue overflows: "
            f"{recorder.overflow_count}"
        )

        print(
            f"{label} status events: "
            f"{recorder.status_count}"
        )

    def _print_summary(self) -> None:
        if self.imu_recorder is None:
            raise RuntimeError(
                "IMU/HX711 recorder was not initialized."
            )

        #if self.piezo_recorder is None:
         #   raise RuntimeError(
          #      "Piezo recorder was not initialized."
           # )

        if self.ft_enabled and self.ft_recorder is None:
            raise RuntimeError(
                "Force/torque recorder was not initialized."
            )

        if self.gelsight_recorder is None:
            raise RuntimeError(
                "GelSight recorder was not initialized."
            )

        if self.rode_recorder is None:
            raise RuntimeError(
                "RODE recorder was not initialized."
            )

        print("\nRecording summary")

        print(
            "Combined IMU + load-cell rows: "
            f"{self.imu_recorder.sample_count}"
        )

        print(
            "  Accelerometer samples: "
            f"{self.imu_recorder.accel_count}"
        )

        print(
            "  Gyroscope samples: "
            f"{self.imu_recorder.gyro_count}"
        )

        print(
            "  Valid load-cell rows: "
            f"{self.imu_recorder.load_cell_count}"
        )

        print(
            "  Invalid serial lines: "
            f"{self.imu_recorder.invalid_line_count}"
        )

        print(
            "  Button marks: "
            f"{self.imu_recorder.button_count}"
        )

        if self.ft_enabled:
            print(
                "Force/torque samples: "
                f"{self.ft_recorder.sample_count}"
            )
        else:
            print(
                "Force/torque: streamed externally by "
                "auto_record_launcher.py"
            )

        print(
            "GelSight frames: "
            f"{self.gelsight_recorder.frame_count}"
        )

        print(
            "GelSight failed reads: "
            f"{self.gelsight_recorder.failed_frame_count}"
        )

        self._print_audio_summary(
            "RODE",
            self.rode_recorder,
        )

        # print(
        #     "Piezo samples: "
        #     f"{self.piezo_recorder.sample_count}"
        # )

        # print(
        #     "Piezo invalid lines: "
        #     f"{self.piezo_recorder.invalid_line_count}"
        # )

        print(
            f"\nSaved to: {self.session_dir}"
        )

    # -----------------------------------------------------------------
    # Main recording routine
    # -----------------------------------------------------------------

    def run(self) -> Path:
        self.session_dir.mkdir(
            parents=True,
            exist_ok=False,
        )

        self._install_signal_handlers()
        self._create_recorders()

        print(
            f"Session directory: "
            f"{self.session_dir}"
        )

        print(
            f"IMU/HX711 port: "
            f"{self.imu_port}"
        )

        print(
            f"IMU/HX711 baud: "
            f"{self.imu_baud}"
        )

        # print(
        #     f"Piezo port: "
        #     f"{self.piezo_port}"
        # )

        # print(
        #     f"Piezo baud: "
        #     f"{self.piezo_baud}"
        # )

        print(
            "Force/torque port: "
            + (
                self.ft_port
                if self.ft_enabled
                else "(external -- streamed by auto_record_launcher.py)"
            )
        )

        print(
            f"Camera index: "
            f"{self.camera_index}"
        )

        print(
            "RODE audio device: "
            f"{self.rode_audio_device}"
        )

        print("Opening devices...")

        for recorder in self._recorders():
            recorder.start()

        try:
            self._wait_for_recorders()

            # Establish one shared host clock reference for every
            # recorder.
            self.state.session_t0_ns = monotonic_ns()

            wall_start = (
                datetime.now().astimezone()
            )

            self._write_metadata(wall_start)

            print(
                f"Recording for "
                f"{self.duration_s:.1f} seconds."
            )

            print("Press Ctrl-C to stop early.")

            # Release all recorder threads at approximately the same
            # moment.
            self.state.start_event.set()

            deadline = (
                time.perf_counter()
                + self.duration_s
            )

            while (
                not self.state.stop_event.is_set()
                and time.perf_counter() < deadline
            ):
                time.sleep(0.05)

        finally:
            self.state.stop_event.set()

            # Ensures threads waiting on start_event can exit if setup
            # failed before normal recording began.
            self.state.start_event.set()

            self._join_recorders()

        errors = self.state.get_errors()

        if errors:
            print("\nRecorder errors:")

            for source, message in errors:
                print(
                    f"  {source}: {message}"
                )

        self._print_summary()

        return self.session_dir
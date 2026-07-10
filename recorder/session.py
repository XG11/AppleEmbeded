import json
import signal
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from .gelsight import GelSightRecorder
from .imu import IMURecorder
from .microphone import MicrophoneRecorder
from .shared import SharedRecordingState, monotonic_ns


class RecordingSession:
    def __init__(
        self,
        duration_s: float,
        imu_port: str,
        audio_device: Optional[int],
        camera_index: int,
        output_root: Path = Path("recordings"),
        imu_baud: int = 921600,
        camera_width: int = 0,
        camera_height: int = 0,
        camera_fps: float = 30.0,
        audio_sample_rate: int = 48000,
        audio_channels: int = 1,
        audio_block_size: int = 1024,
    ) -> None:
        self.duration_s = duration_s

        self.imu_port = imu_port
        self.imu_baud = imu_baud

        self.camera_index = camera_index
        self.camera_width = camera_width
        self.camera_height = camera_height
        self.camera_fps = camera_fps

        self.audio_device = audio_device
        self.audio_sample_rate = audio_sample_rate
        self.audio_channels = audio_channels
        self.audio_block_size = audio_block_size

        self.output_root = Path(output_root)

        self.session_name = datetime.now().strftime(
            "session_%Y%m%d_%H%M%S"
        )

        self.session_dir = (
            self.output_root / self.session_name
        )

        self.state = SharedRecordingState()

        self.imu_recorder: Optional[IMURecorder] = None
        self.gelsight_recorder: Optional[GelSightRecorder] = None
        self.microphone_recorder: Optional[MicrophoneRecorder] = None

    def _create_recorders(self) -> None:
        self.imu_recorder = IMURecorder(
            state=self.state,
            serial_port=self.imu_port,
            baud_rate=self.imu_baud,
            output_path=self.session_dir / "imu.csv",
        )

        self.gelsight_recorder = GelSightRecorder(
            state=self.state,
            camera_index=self.camera_index,
            video_path=self.session_dir / "gelsight.avi",
            timestamp_path=(
                self.session_dir
                / "gelsight_timestamps.csv"
            ),
            width=self.camera_width,
            height=self.camera_height,
            fps=self.camera_fps,
        )

        self.microphone_recorder = MicrophoneRecorder(
            state=self.state,
            device_index=self.audio_device,
            wav_path=self.session_dir / "microphone.wav",
            timestamp_path=(
                self.session_dir
                / "microphone_timestamps.csv"
            ),
            sample_rate=self.audio_sample_rate,
            channels=self.audio_channels,
            block_size=self.audio_block_size,
        )

    def _install_signal_handlers(self) -> None:
        def stop_handler(signum, frame) -> None:
            del signum
            del frame

            print("\nStop requested.")
            self.state.stop_event.set()

        signal.signal(signal.SIGINT, stop_handler)
        signal.signal(signal.SIGTERM, stop_handler)

    def _wait_for_recorders(
        self,
        timeout_s: float = 10.0,
    ) -> None:
        recorders = [
            self.imu_recorder,
            self.gelsight_recorder,
            self.microphone_recorder,
        ]

        deadline = time.perf_counter() + timeout_s

        for recorder in recorders:
            remaining = deadline - time.perf_counter()

            if remaining <= 0:
                raise RuntimeError(
                    "Timed out while opening recording devices"
                )

            if not recorder.ready_event.wait(timeout=remaining):
                raise RuntimeError(
                    f"{type(recorder).__name__} did not become ready"
                )

        errors = self.state.get_errors()

        if errors:
            messages = [
                f"{source}: {message}"
                for source, message in errors
            ]

            raise RuntimeError(
                "Device initialization failed:\n"
                + "\n".join(messages)
            )

    def _write_metadata(
        self,
        wall_start: datetime,
    ) -> None:
        metadata = {
            "session_name": self.session_name,
            "wall_start_iso": wall_start.isoformat(),
            "host_monotonic_start_ns": (
                self.state.session_t0_ns
            ),
            "requested_duration_s": self.duration_s,
            "imu": {
                "serial_port": self.imu_port,
                "baud_rate": self.imu_baud,
            },
            "gelsight": {
                "camera_index": self.camera_index,
                "requested_width": self.camera_width,
                "requested_height": self.camera_height,
                "requested_fps": self.camera_fps,
                "actual_width": (
                    self.gelsight_recorder.actual_width
                ),
                "actual_height": (
                    self.gelsight_recorder.actual_height
                ),
                "actual_fps": (
                    self.gelsight_recorder.actual_fps
                ),
            },
            "microphone": {
                "device_index": self.audio_device,
                "sample_rate_hz": self.audio_sample_rate,
                "channels": self.audio_channels,
                "block_size": self.audio_block_size,
            },
        }

        metadata_path = (
            self.session_dir / "metadata.json"
        )

        with metadata_path.open("w") as metadata_file:
            json.dump(
                metadata,
                metadata_file,
                indent=2,
            )

    def _join_recorders(self) -> None:
        for recorder in [
            self.imu_recorder,
            self.gelsight_recorder,
            self.microphone_recorder,
        ]:
            if recorder is not None:
                recorder.join(timeout=5.0)

    def _print_summary(self) -> None:
        print("\nRecording summary")

        print(
            f"IMU entries: "
            f"{self.imu_recorder.sample_count}"
        )
        print(
            f"  Accel: {self.imu_recorder.accel_count}"
        )
        print(
            f"  Gyro:  {self.imu_recorder.gyro_count}"
        )
        print(
            f"  Invalid serial lines: "
            f"{self.imu_recorder.invalid_line_count}"
        )

        print(
            f"GelSight frames: "
            f"{self.gelsight_recorder.frame_count}"
        )
        print(
            f"GelSight failed reads: "
            f"{self.gelsight_recorder.failed_frame_count}"
        )

        print(
            f"Audio samples: "
            f"{self.microphone_recorder.sample_count}"
        )
        print(
            f"Audio blocks: "
            f"{self.microphone_recorder.block_count}"
        )
        print(
            f"Audio queue overflows: "
            f"{self.microphone_recorder.overflow_count}"
        )

        print(f"\nSaved to: {self.session_dir}")

    def run(self) -> Path:
        self.session_dir.mkdir(
            parents=True,
            exist_ok=False,
        )

        self._install_signal_handlers()
        self._create_recorders()

        print(f"Session directory: {self.session_dir}")
        print(f"IMU port: {self.imu_port}")
        print(f"Camera index: {self.camera_index}")
        print(f"Audio device: {self.audio_device}")
        print("Opening devices...")

        self.imu_recorder.start()
        self.gelsight_recorder.start()
        self.microphone_recorder.start()

        try:
            self._wait_for_recorders()

            self.state.session_t0_ns = monotonic_ns()
            wall_start = datetime.now().astimezone()

            self._write_metadata(wall_start)

            print(
                f"Recording for {self.duration_s:.1f} seconds."
            )
            print("Press Ctrl-C to stop early.")

            self.state.start_event.set()

            deadline = (
                time.perf_counter() + self.duration_s
            )

            while (
                not self.state.stop_event.is_set()
                and time.perf_counter() < deadline
            ):
                time.sleep(0.05)

        except Exception:
            self.state.stop_event.set()
            self.state.start_event.set()
            self._join_recorders()
            raise

        finally:
            self.state.stop_event.set()
            self.state.start_event.set()
            self._join_recorders()

        errors = self.state.get_errors()

        if errors:
            print("\nRecorder errors:")

            for source, message in errors:
                print(f"  {source}: {message}")

        self._print_summary()

        return self.session_dir
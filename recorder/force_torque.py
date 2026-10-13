import csv
import json
import threading
from pathlib import Path
from typing import Dict, Optional

from mms101.mms101_read import AXES, MMS101

from .shared import SharedRecordingState, monotonic_ns


class ForceTorqueRecorder:
    """
    Record 6-axis force/torque data from a Mitsumi MMS101 Evakit1
    (ForceSensorControllerBoard).

    This sensor connects to the host over its own USB-serial (FTDI) port,
    independent of the IMU/button Teensy, and needs several minutes of
    thermal warm-up before it can be usefully tared (see
    mms101/README.md, "Drift and zeroing"). That warm-up and the
    zero-tare are a one-time step, done once by mms101/warmup_and_tare.py
    before a whole batch of recording trials -- not per trial, since
    repeating a multi-minute wait on every trial would make back-to-back
    recordings (e.g. via auto_record_launcher.py) impractical.

    This recorder therefore assumes the sensor is already powered on
    and tared, and skips re-powering it and re-waiting out the thermal
    warm-up. It does still redo the rest of the reference bring-up
    sequence (Idle, Bootload, IntervalMeasure, IntervalRestart) before
    every trial, exactly as warmup_and_tare.py/mms101_read.py always do
    -- those steps are cheap (tens of ms), and skipping them was found
    to leave a visible warm-up-like transient in Fz at the start of
    later trials. It leaves the sensor powered on when the trial ends;
    run mms101/power_off.py once the whole session is done.

    Output CSV
    ----------

        host_time_ns,
        host_elapsed_s,
        device_time_us,
        Fx, Fy, Fz, Mx, My, Mz   (tare offsets already subtracted)
    """

    def __init__(
        self,
        state: SharedRecordingState,
        serial_port: str,
        output_path: Path,
        tare_path: Path,
        expected_interval_us: int = 1000,
        expected_restart_samples: int = 100,
    ) -> None:
        self.state = state
        self.serial_port = serial_port
        self.output_path = Path(output_path)
        self.tare_path = Path(tare_path)
        self.expected_interval_us = expected_interval_us
        self.expected_restart_samples = expected_restart_samples

        self.ready_event = threading.Event()
        self.thread: Optional[threading.Thread] = None

        self.sample_count = 0

        self.device: Optional[MMS101] = None

        # Populated from tare_path before the sensor is touched.
        self.tare_offsets: Optional[Dict[str, float]] = None
        self.tare_metadata: Optional[dict] = None

    def start(self) -> None:
        self.thread = threading.Thread(
            target=self._run,
            name="force-torque-recorder",
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

    def _load_tare(self) -> None:
        if not self.tare_path.exists():
            raise RuntimeError(
                f"Tare file not found: {self.tare_path}\n"
                "Run mms101/warmup_and_tare.py once before recording. "
                "It powers on the sensor, waits for the thermal "
                "warm-up, and saves the zero offsets this recorder "
                "needs."
            )

        with self.tare_path.open(
            "r",
            encoding="utf-8",
        ) as tare_file:
            tare_data = json.load(tare_file)

        offsets = tare_data["offsets"]

        self.tare_offsets = {
            axis: float(offsets[axis])
            for axis in AXES
        }

        self.tare_metadata = tare_data

        stored_interval_us = tare_data.get("interval_us")
        stored_restart_samples = tare_data.get("restart_samples")

        if (
            stored_interval_us is not None
            and stored_interval_us != self.expected_interval_us
        ):
            print(
                "WARNING: force/torque recorder expected an interval "
                f"of {self.expected_interval_us} us, but "
                f"{self.tare_path} was captured at "
                f"{stored_interval_us} us. The sensor is still "
                "configured from that warm-up run; re-run "
                "warmup_and_tare.py if --ft-interval has changed."
            )

        if (
            stored_restart_samples is not None
            and stored_restart_samples != self.expected_restart_samples
        ):
            print(
                "WARNING: force/torque recorder expected a restart "
                f"interval of {self.expected_restart_samples} "
                f"samples, but {self.tare_path} was captured at "
                f"{stored_restart_samples}. The sensor is still "
                "configured from that warm-up run; re-run "
                "warmup_and_tare.py if --ft-restart has changed."
            )

    def _run(self) -> None:
        try:
            self.output_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            self._load_tare()

            self.device = MMS101(self.serial_port)

            # Power-on and the multi-minute thermal warm-up were already
            # done once by mms101/warmup_and_tare.py and are NOT redone
            # here (that's the whole point -- see the module docstring).
            #
            # Everything else in the reference bring-up sequence
            # (mms101_read.py / mms101_plot.py / warmup_and_tare.py) is
            # cheap -- tens of ms -- and IS redone every trial, exactly
            # as those scripts always do it, rather than assumed to
            # survive a Stop/Start cycle: Idle+Bootload reloads the
            # calibration matrix, and IntervalMeasure/IntervalRestart
            # are reapplied using the same values warmup_and_tare.py
            # was run with (saved in the tare file).
            interval_us = (
                self.tare_metadata.get("interval_us")
                if self.tare_metadata
                else None
            )
            if interval_us is None:
                interval_us = self.expected_interval_us

            restart_samples = (
                self.tare_metadata.get("restart_samples")
                if self.tare_metadata
                else None
            )
            if restart_samples is None:
                restart_samples = self.expected_restart_samples

            self.device.board_select()
            self.device.idle_all_axes()
            self.device.bootload()
            self.device.set_interval_us(interval_us)
            self.device.set_restart(restart_samples)
            self.device.start()

            with self.output_path.open(
                "w",
                newline="",
            ) as csv_file:

                writer = csv.writer(csv_file)

                writer.writerow(
                    ["host_time_ns", "host_elapsed_s", "device_time_us"]
                    + AXES
                )

                # Discard whatever has accumulated in the OS serial buffer
                # before the session actually starts.
                self.device.ser.reset_input_buffer()

                self.ready_event.set()

                # Wait until all other recording devices are ready.
                self.state.start_event.wait()

                self.device.ser.reset_input_buffer()

                device_time_us = 0

                offsets = [
                    self.tare_offsets[axis]
                    for axis in AXES
                ]

                for dt_us, values in self.device.frames():
                    if self.state.stop_event.is_set():
                        break

                    device_time_us += dt_us

                    host_time_ns = monotonic_ns()

                    host_elapsed_s = (
                        host_time_ns
                        - self.state.session_t0_ns
                    ) / 1_000_000_000.0

                    tared_values = [
                        value - offset
                        for value, offset in zip(values, offsets)
                    ]

                    writer.writerow(
                        [
                            host_time_ns,
                            f"{host_elapsed_s:.9f}",
                            device_time_us,
                        ]
                        + [f"{value:.6f}" for value in tared_values]
                    )

                    self.sample_count += 1

                csv_file.flush()

        except Exception as exc:
            # Ensure the main recording program does not wait forever for
            # this recorder to become ready after a serial or file error.
            self.ready_event.set()

            self.state.report_error(
                "Force/Torque (MMS101)",
                f"{type(exc).__name__}: {exc}",
            )

        finally:
            if self.device is not None:
                try:
                    # Only stop streaming. The sensor stays powered on
                    # for the next trial; mms101/power_off.py switches
                    # it off once, at the end of the whole session.
                    self.device.stop()
                except Exception:
                    pass

                try:
                    self.device.close()
                except Exception:
                    pass

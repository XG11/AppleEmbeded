"""Continuous MMS101 force/torque streaming across a whole batch of trials.

Used by auto_record_launcher.py. Instead of each trial starting and
stopping its own stream (recorder/force_torque.py's approach, used when
record_multimodal.py is run standalone), ContinuousForceTorqueStreamer
powers on, warms up, and tares the sensor ONCE, then keeps it streaming
continuously -- through the whole station-controller/button trial loop,
never stopping between trials. A bounded ring buffer of recent tared
samples (timestamped on the same host clock every other recorder uses,
see recorder/shared.py) is kept in memory; auto_record_launcher.py
slices the samples that fall within each trial's time window out of
that buffer after the trial finishes, instead of record_multimodal.py
touching the F/T serial port at all (see record_multimodal.py's
--ft-external).

This avoids restarting the stream at every trial boundary, which was
observed to leave a visible warm-up-like transient in Fz at the start
of later trials even with the calibration bring-up redone every time
(recorder/force_torque.py) -- streaming continuously sidesteps the
question entirely, since there is no per-trial Stop/Start cycle to
begin with.
"""
import collections
import csv
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

try:
    # Running as `python mms101/continuous_stream.py`: the script's own
    # directory is on sys.path, so these are sibling imports.
    from mms101_read import AXES, MMS101
    from warmup_and_tare import warm_up_and_tare
except ImportError:
    # Imported as mms101.continuous_stream (e.g. by
    # auto_record_launcher.py, running from the repo root): reach the
    # siblings through the package instead.
    from mms101.mms101_read import AXES, MMS101
    from mms101.warmup_and_tare import warm_up_and_tare


class ContinuousForceTorqueStreamer:
    def __init__(
        self,
        port,
        interval_us=1000,
        restart_samples=100,
        buffer_seconds=600.0,
    ):
        self.device = MMS101(port)
        self.interval_us = interval_us
        self.restart_samples = restart_samples

        # Generous ring buffer: a safety net against unbounded growth if
        # a trial is never extracted, sized well beyond any one trial.
        approx_samples_per_second = max(1, int(1e6 / max(interval_us, 1)))
        self.buffer = collections.deque(
            maxlen=int(buffer_seconds * approx_samples_per_second)
        )
        self.lock = threading.Lock()

        self.thread = None
        self.stop_flag = threading.Event()
        self.error = None

        self.tare_data = None

        # Read directly by the hot sampling loop (_stream_loop) with no
        # lock -- only ever replaced wholesale (retare()), never mutated
        # in place, so a plain attribute read/write is safe under the
        # GIL without adding synchronization to that loop.
        self.offsets = None

        self.live_snapshot_thread = None
        self.live_snapshot_path = None
        self.live_snapshot_window_s = 2.0
        self.live_snapshot_interval_s = 0.1

        self.retare_watcher_thread = None
        self.retare_request_path = None
        self.retare_tare_file_path = None
        self.retare_samples = 500
        self.retare_poll_interval_s = 0.1

    # -----------------------------------------------------------------
    # Startup
    # -----------------------------------------------------------------

    def warm_up_and_start(
        self,
        warmup_seconds=300.0,
        tare_samples=500,
    ):
        """
        Power on, warm up, and tare the sensor, then start the
        continuous background stream. Blocks for ~warmup_seconds.
        """
        self.tare_data = warm_up_and_tare(
            self.device,
            interval_us=self.interval_us,
            restart_samples=self.restart_samples,
            warmup_seconds=warmup_seconds,
            tare_samples=tare_samples,
        )
        # warm_up_and_tare() leaves the device mid-stream -- no Stop was
        # issued, so this keeps consuming the same uninterrupted stream
        # with no gap and no need to re-Start.
        self._begin_background_thread()
        return self.tare_data

    def start_with_existing_tare(self, tare_data):
        """
        Skip power-on and warm-up (assumed already done), redo the
        cheap bring-up (Idle, Bootload, IntervalMeasure, IntervalRestart
        -- see recorder/force_torque.py for why these are redone rather
        than assumed to still be valid), then start the continuous
        background stream using a tare computed earlier.
        """
        self.tare_data = tare_data

        self.device.board_select()
        self.device.idle_all_axes()
        self.device.bootload()
        self.device.set_interval_us(self.interval_us)
        self.device.set_restart(self.restart_samples)
        self.device.start()

        self._begin_background_thread()

    def _begin_background_thread(self):
        self.offsets = [self.tare_data["offsets"][axis] for axis in AXES]

        self.thread = threading.Thread(
            target=self._stream_loop,
            name="mms101-continuous-stream",
            daemon=True,
        )
        self.thread.start()

    # -----------------------------------------------------------------
    # Live snapshot, for mms101/live_view.py
    # -----------------------------------------------------------------

    def start_live_snapshot(
        self,
        path,
        window_seconds=2.0,
        interval_seconds=0.1,
    ):
        """
        Periodically write the most recent window_seconds of buffered
        samples to `path` as a small CSV, so a separate process
        (mms101/live_view.py) can poll it for a live view WITHOUT
        opening the sensor's serial port itself -- that port is held
        open by this process for the whole session, so a second
        process trying to open it directly (e.g. mms101_plot.py) would
        fail with "port busy".

        Total end-to-end lag is roughly this interval_seconds plus
        live_view.py's own --refresh-ms poll interval (they are not
        synchronized) -- keep both small if you want a snappier view;
        a bigger window_seconds costs more I/O per write (more rows)
        without making the latest point any fresher.

        Runs in its own thread, decoupled from the real-time sampling
        loop (_stream_loop), so a slow disk write here can never cause
        a dropped sample -- at worst the live view lags briefly.
        """
        self.live_snapshot_path = Path(path)
        self.live_snapshot_window_s = window_seconds
        self.live_snapshot_interval_s = interval_seconds

        self.live_snapshot_thread = threading.Thread(
            target=self._live_snapshot_loop,
            name="mms101-live-snapshot",
            daemon=True,
        )
        self.live_snapshot_thread.start()

    def _live_snapshot_loop(self):
        tmp_path = self.live_snapshot_path.with_suffix(
            self.live_snapshot_path.suffix + ".tmp"
        )

        while not self.stop_flag.wait(self.live_snapshot_interval_s):
            now_ns = time.perf_counter_ns()
            window_ns = int(self.live_snapshot_window_s * 1e9)

            rows = self.extract_window(now_ns - window_ns, now_ns)

            if not rows:
                continue

            try:
                with tmp_path.open("w", newline="") as snapshot_file:
                    writer = csv.writer(snapshot_file)
                    writer.writerow(
                        ["host_time_ns", "device_time_us"] + AXES
                    )

                    for host_time_ns, device_time_us, values in rows:
                        writer.writerow(
                            [host_time_ns, device_time_us]
                            + [f"{value:.6f}" for value in values]
                        )

                # Atomic on POSIX: live_view.py never sees a half
                # written file.
                tmp_path.replace(self.live_snapshot_path)
            except OSError:
                pass

    # -----------------------------------------------------------------
    # Background streaming
    # -----------------------------------------------------------------

    def _stream_loop(self):
        try:
            device_time_us = 0

            for dt_us, values in self.device.frames():
                if self.stop_flag.is_set():
                    break

                device_time_us += dt_us

                host_time_ns = time.perf_counter_ns()

                # A plain attribute read -- retare() swaps this
                # reference wholesale rather than mutating it in
                # place, so this always sees either the old or the
                # new list, never a half-updated one.
                tared_values = [
                    value - offset
                    for value, offset in zip(values, self.offsets)
                ]

                with self.lock:
                    self.buffer.append(
                        (host_time_ns, device_time_us, tared_values)
                    )

        except Exception as exc:  # serial unplugged, etc.
            self.error = exc
            print(
                f"Force/torque streaming error: {exc}",
                file=sys.stderr,
            )

    # -----------------------------------------------------------------
    # Per-trial extraction
    # -----------------------------------------------------------------

    def extract_window(self, start_ns, end_ns):
        """
        Return buffered (host_time_ns, device_time_us, [Fx..Mz]) rows
        with start_ns <= host_time_ns <= end_ns, oldest first.
        """
        with self.lock:
            snapshot = list(self.buffer)

        return [
            row for row in snapshot
            if start_ns <= row[0] <= end_ns
        ]

    # -----------------------------------------------------------------
    # Re-tare mid-session
    # -----------------------------------------------------------------

    def retare(self, sample_count=500):
        """
        Rezero the sensor using the last `sample_count` buffered
        samples, without stopping the stream.

        The buffer already holds TARED values (the previous offset was
        subtracted as each sample was collected), so this computes how
        far those recent values have drifted from zero and folds that
        delta into the existing offsets, rather than replacing them
        outright -- mathematically equivalent to re-taring from raw
        values, but needs no access to the sensor beyond what is
        already streaming.

        Updates self.offsets (picked up by _stream_loop on its next
        iteration) and self.tare_data, and returns the new tare_data.
        """
        with self.lock:
            recent = list(self.buffer)[-sample_count:]

        if len(recent) < min(10, sample_count):
            print(
                "Not enough buffered samples to retare "
                f"({len(recent)} available); skipped.",
                file=sys.stderr,
            )
            return self.tare_data

        deltas = [
            sum(row[2][axis_index] for row in recent) / len(recent)
            for axis_index in range(len(AXES))
        ]

        new_offsets = [
            offset + delta
            for offset, delta in zip(self.offsets, deltas)
        ]

        # Wholesale replace -- see the comment on self.offsets in
        # __init__ for why this is safe without a lock.
        self.offsets = new_offsets

        self.tare_data = {
            **self.tare_data,
            "offsets": dict(zip(AXES, new_offsets)),
            "retared_at": datetime.now().astimezone().isoformat(),
            "retare_sample_count": len(recent),
        }

        return self.tare_data

    def start_retare_watcher(
        self,
        request_path,
        tare_file_path=None,
        sample_count=500,
        poll_interval_s=0.1,
    ):
        """
        Watch for `request_path` to appear (e.g. touched by
        mms101/live_view.py's 't' key) and call retare() when it does,
        then delete the file so it only fires once per touch.

        If tare_file_path is given, the updated tare is also saved
        there, so a later --skip-ft-warmup restart picks up the
        retared offsets instead of the original warm-up ones.
        """
        self.retare_request_path = Path(request_path)
        self.retare_tare_file_path = (
            Path(tare_file_path) if tare_file_path else None
        )
        self.retare_samples = sample_count
        self.retare_poll_interval_s = poll_interval_s

        self.retare_watcher_thread = threading.Thread(
            target=self._retare_watcher_loop,
            name="mms101-retare-watcher",
            daemon=True,
        )
        self.retare_watcher_thread.start()

    def _retare_watcher_loop(self):
        while not self.stop_flag.wait(self.retare_poll_interval_s):
            if not self.retare_request_path.exists():
                continue

            try:
                self.retare_request_path.unlink()
            except OSError:
                pass

            tare_data = self.retare(self.retare_samples)

            print(
                "\nRetared force/torque sensor "
                f"(averaged {tare_data.get('retare_sample_count', '?')} "
                "recent samples):",
                file=sys.stderr,
            )
            for axis in AXES:
                print(
                    f"  {axis} offset = {tare_data['offsets'][axis]:+.5f}",
                    file=sys.stderr,
                )

            if self.retare_tare_file_path is not None:
                try:
                    self.retare_tare_file_path.parent.mkdir(
                        parents=True, exist_ok=True
                    )
                    self.retare_tare_file_path.write_text(
                        json.dumps(tare_data, indent=2)
                    )
                except OSError as exc:
                    print(
                        f"WARNING: could not save retared tare to "
                        f"{self.retare_tare_file_path}: {exc}",
                        file=sys.stderr,
                    )

    # -----------------------------------------------------------------
    # Shutdown
    # -----------------------------------------------------------------

    def close(self):
        self.stop_flag.set()

        if self.thread is not None:
            self.thread.join(timeout=2.0)

        if self.live_snapshot_thread is not None:
            self.live_snapshot_thread.join(timeout=2.0)

        if self.retare_watcher_thread is not None:
            self.retare_watcher_thread.join(timeout=2.0)

        try:
            # Only stop streaming -- leave the sensor powered on.
            # mms101/power_off.py is the explicit end-of-session step.
            self.device.stop()
        except Exception:
            pass

        self.device.close()

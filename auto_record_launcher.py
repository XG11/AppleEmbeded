import argparse
import csv
import json
import subprocess
import threading
import time
from pathlib import Path
from typing import List, Optional

import serial
from serial.tools import list_ports

from mms101.continuous_stream import ContinuousForceTorqueStreamer
from mms101.mms101_read import AXES
from recorder.shared import monotonic_ns


# ============================================================
# Continuous trial loop for the station controller + button
# workflow.
#
# On startup, this powers on, warms up (~5 minutes by default --
# see --ft-warmup-seconds), and tares the MMS101 force/torque
# sensor once, *before* waiting for the first button press --
# then keeps it STREAMING CONTINUOUSLY (mms101/continuous_stream.py)
# for the whole trial loop, rather than each trial starting and
# stopping its own stream. Restarting the stream per trial was
# found to leave a visible warm-up-like transient in Fz at the
# start of later trials; streaming continuously sidesteps that.
#
# Each physical button press on the station controller still
# triggers exactly one recording trial via the recorder_command
# (normally `python3 record_multimodal.py ... --ft-external`,
# with --ft-external appended automatically below so that
# subprocess does not also try to open the F/T serial port).
# After the trial finishes, the samples that fall inside its time
# window are sliced out of the continuous stream's buffer and
# written to that trial's own force_torque.csv.
#
# Pass --skip-ft-warmup to go straight to streaming, reusing
# whatever --ft-tare-file already has on disk (e.g. after a
# restart later the same session) instead of redoing the 5-minute
# warm-up.
#
# Since this process holds the F/T sensor's serial port open for
# the whole session, mms101_plot.py cannot also open it to watch
# live. Instead, this continuously rewrites a small rolling
# snapshot file (--live-snapshot-file, default
# mms101/live_snapshot.csv) that `python mms101/live_view.py`,
# run in a second terminal, polls for a live plot -- pass
# --no-live-snapshot to turn that off.
#
# The sensor can also be rezeroed mid-session (e.g. after
# remounting a tool) without stopping the stream: pressing 't' in
# live_view.py (or touching --retare-request-file directly)
# recomputes the tare from recent buffered samples and updates
# --ft-tare-file in place -- pass --no-retare to disable this.
# ============================================================


BAUD_RATE = 2_000_000
TRIAL_DURATION_S = 65.0


# ============================================================
# FORCE/TORQUE SENSOR PORT RESOLUTION
# ============================================================

def find_force_torque_ports() -> List[str]:
    """
    Find serial ports that appear to belong to the MMS101 Evakit1,
    which enumerates as an FTDI USB-serial adapter.
    """
    ports: List[str] = []

    for port in list_ports.comports():
        description = (port.description or "").lower()
        manufacturer = (port.manufacturer or "").lower()

        if (
            port.vid == 0x0403
            or "ftdi" in description
            or "ftdi" in manufacturer
        ):
            ports.append(port.device)

    return ports


def resolve_ft_port(ft_port: Optional[str]) -> str:
    if ft_port is not None:
        return ft_port

    candidate_ports = find_force_torque_ports()

    if len(candidate_ports) != 1:
        raise RuntimeError(
            f"Expected exactly 1 force/torque sensor device, "
            f"but found {len(candidate_ports)}.\n"
            f"Detected FTDI ports: {candidate_ports}\n"
            "Specify the port explicitly using:\n"
            "  --ft-port PORT"
        )

    print("\nAutomatically selected force/torque sensor port:")
    print(f"  MMS101: {candidate_ports[0]}")

    return candidate_ports[0]


# ============================================================
# PER-TRIAL FORCE/TORQUE EXTRACTION
# ============================================================

def write_force_torque_csv(
    streamer: ContinuousForceTorqueStreamer,
    session_dir: Path,
) -> int:
    """
    Slice this trial's time window out of the continuous stream's
    buffer and write it to session_dir/force_torque.csv, in the same
    format recorder/force_torque.py uses for a standalone recording.

    Returns the number of rows written.
    """

    metadata_path = session_dir / "metadata.json"

    if not metadata_path.exists():
        print(
            "WARNING: no metadata.json in "
            f"{session_dir}; cannot extract force/torque data."
        )
        return 0

    metadata = json.loads(metadata_path.read_text())

    start_ns = metadata["host_monotonic_start_ns"]
    duration_s = metadata["requested_duration_s"]
    end_ns = start_ns + int(duration_s * 1_000_000_000)

    rows = streamer.extract_window(start_ns, end_ns)

    output_path = session_dir / "force_torque.csv"

    with output_path.open("w", newline="") as csv_file:
        writer = csv.writer(csv_file)

        writer.writerow(
            ["host_time_ns", "host_elapsed_s", "device_time_us"] + AXES
        )

        for host_time_ns, device_time_us, tared_values in rows:
            host_elapsed_s = (host_time_ns - start_ns) / 1_000_000_000.0

            writer.writerow(
                [host_time_ns, f"{host_elapsed_s:.9f}", device_time_us]
                + [f"{value:.6f}" for value in tared_values]
            )

    print(f"Force/torque samples: {len(rows)} -> {output_path}")

    return len(rows)


# ============================================================
# WAIT FOR SESSION START
# ============================================================

def wait_for_session_start_and_capture(port: str):
    """
    Open the station controller serial port, wait until the firmware
    sends SESSION_START, then keep reading in a background thread for
    the rest of the trial instead of closing the port.

    Every line is stamped with monotonic_ns() -- the same host clock
    as session_t0_ns (recorder/session.py) and every other recorder
    channel (recorder/shared.py) -- the instant it arrives. This is
    what lets connector events (FIRST_FULL_CONNECT, SUCCESS_DETECTED,
    ...) be converted to host_elapsed_s later, instead of only being
    known in the station's own free-running millis() clock (see
    save_label's docstring for why that distinction matters).

    Returns (ser, thread, stop_flag, events); the caller must
    stop_capture(ser, thread, stop_flag) when the trial is done.
    """

    print(f"Waiting for controller on {port}...")

    ser = serial.Serial(
        port,
        BAUD_RATE,
        timeout=0.1,
    )

    # Teensy USB serial may need a moment after opening.
    time.sleep(0.5)

    ser.reset_input_buffer()

    print()
    print("========================================")
    print("READY FOR TRIAL")
    print("Green flashing = idle")
    print("Hold the physical button to start.")
    print("========================================")
    print()

    events = []

    def read_line():
        raw = ser.readline()

        if not raw:
            return None

        try:
            line = raw.decode(
                "utf-8",
                errors="ignore",
            ).strip()

        except Exception:
            return None

        return line or None

    # Block in the main thread until SESSION_START, so the trial
    # subprocess is never launched before it.
    while True:
        line = read_line()

        if line is None:
            continue

        events.append((monotonic_ns(), line))

        print(f"[controller] {line}")

        if line == "SESSION_START":
            print()
            print("Session trigger received.")
            print("Recording started.")
            break

    stop_flag = threading.Event()

    def read_loop():
        while not stop_flag.is_set():
            line = read_line()

            if line is None:
                continue

            events.append((monotonic_ns(), line))

    thread = threading.Thread(
        target=read_loop,
        name="controller-events",
        daemon=True,
    )
    thread.start()

    return ser, thread, stop_flag, events


def stop_capture(ser, thread, stop_flag):
    stop_flag.set()
    thread.join(timeout=2.0)
    ser.close()


# ============================================================
# SERIAL COMMAND HELPER
# ============================================================

def send_command_and_wait_for_prefix(
    port: str,
    command: str,
    expected_prefix: str,
    timeout_s: float = 3.0,
):
    """
    Reconnect to the Teensy, send one command, and return the
    first response line beginning with expected_prefix.
    """

    with serial.Serial(
        port,
        BAUD_RATE,
        timeout=1.0,
    ) as ser:

        time.sleep(0.5)

        ser.reset_input_buffer()

        ser.write(
            f"{command}\n".encode("utf-8")
        )

        ser.flush()

        deadline = (
            time.time() + timeout_s
        )

        while time.time() < deadline:
            raw = ser.readline()

            if not raw:
                continue

            line = raw.decode(
                "utf-8",
                errors="ignore",
            ).strip()

            if not line:
                continue

            print(
                f"[controller query] {line}"
            )

            if line.startswith(
                expected_prefix
            ):
                return line

    return None


# ============================================================
# GET LAST TRIAL SUMMARY
# ============================================================

def get_last_result(port: str):
    """
    Query the basic result of the most recent trial.

    Expected new firmware format:

        LAST_RESULT,SUCCESS,first_full_connect_ms,success_count

    Example:

        LAST_RESULT,SUCCESS,12431,3

    Failure example:

        LAST_RESULT,FAIL,-1,0
    """

    time.sleep(0.5)

    line = send_command_and_wait_for_prefix(
        port=port,
        command="GET_LAST_RESULT",
        expected_prefix="LAST_RESULT,",
    )

    if line is None:
        return {
            "label": "unknown",
            "first_full_connect_time_ms": None,
            "success_count": 0,
        }

    parts = line.split(",")

    if len(parts) < 2:
        return {
            "label": "unknown",
            "first_full_connect_time_ms": None,
            "success_count": 0,
        }

    result = parts[1]


    # --------------------------------------------------------
    # No stored trial
    # --------------------------------------------------------

    if result == "NONE":
        return {
            "label": "unknown",
            "first_full_connect_time_ms": None,
            "success_count": 0,
        }


    # --------------------------------------------------------
    # Invalid response
    # --------------------------------------------------------

    if result not in (
        "SUCCESS",
        "FAIL",
    ):
        return {
            "label": "unknown",
            "first_full_connect_time_ms": None,
            "success_count": 0,
        }


    first_full_connect_time_ms = None
    success_count = 0


    # --------------------------------------------------------
    # New firmware:
    #
    # LAST_RESULT,
    # SUCCESS|FAIL,
    # first_full_connect_ms,
    # success_count
    # --------------------------------------------------------

    if len(parts) >= 4:

        try:
            first_ms = int(parts[2])

            if first_ms >= 0:
                first_full_connect_time_ms = (
                    first_ms
                )

        except ValueError:
            pass


        try:
            success_count = int(
                parts[3]
            )

        except ValueError:
            success_count = 0


    return {
        "label": (
            "success"
            if result == "SUCCESS"
            else "fail"
        ),

        "first_full_connect_time_ms":
            first_full_connect_time_ms,

        "success_count":
            success_count,
    }


# ============================================================
# GET ALL SUCCESS TIMESTAMPS
# ============================================================

def get_success_times(port: str):
    """
    Query all successful insertion timestamps.

    Expected firmware response:

        SUCCESS_TIMES,count,t1,t2,t3,...

    Example:

        SUCCESS_TIMES,3,12431,27840,47102

    These timestamps represent the START of the fully connected
    state that was later verified by SUCCESS_CONFIRM_MS.
    """

    time.sleep(0.2)

    line = send_command_and_wait_for_prefix(
        port=port,
        command="GET_SUCCESS_TIMES",
        expected_prefix="SUCCESS_TIMES,",
    )

    if line is None:
        return []


    parts = line.split(",")


    # Expected at least:
    #
    # SUCCESS_TIMES,0
    #
    if len(parts) < 2:
        return []


    # --------------------------------------------------------
    # No previous result
    # --------------------------------------------------------

    if parts[1] == "NONE":
        return []


    # --------------------------------------------------------
    # Parse count
    # --------------------------------------------------------

    try:
        count = int(
            parts[1]
        )

    except ValueError:
        return []


    if count <= 0:
        return []


    # --------------------------------------------------------
    # Parse timestamps
    # --------------------------------------------------------

    success_times = []

    for value in parts[2:]:

        try:
            timestamp_ms = int(
                value
            )

        except ValueError:
            continue


        if timestamp_ms >= 0:
            success_times.append(
                timestamp_ms
            )


    # Firmware count and actual received values should agree.
    #
    # If for some reason fewer values are received, keep the
    # values that were successfully parsed instead of failing.

    if len(success_times) != count:
        print(
            "WARNING: firmware reported "
            f"{count} success events, but "
            f"{len(success_times)} timestamps "
            "were received."
        )


    return success_times


# ============================================================
# OPTIONAL: GET CONFIRMATION TIMES
# ============================================================

def get_success_confirm_times(
    port: str,
):
    """
    Query timestamps corresponding to when each successful
    insertion passed the SUCCESS_CONFIRM_MS dwell time.

    Expected firmware response:

        SUCCESS_CONFIRM_TIMES,count,t1,t2,t3,...

    These are normally about 200 ms after success_times_ms.

    They are kept for debugging / analysis, but the insertion
    event labels should normally use success_times_ms.
    """

    time.sleep(0.2)

    line = send_command_and_wait_for_prefix(
        port=port,
        command="GET_SUCCESS_CONFIRM_TIMES",
        expected_prefix=(
            "SUCCESS_CONFIRM_TIMES,"
        ),
    )

    if line is None:
        return []


    parts = line.split(",")

    if len(parts) < 2:
        return []


    if parts[1] == "NONE":
        return []


    try:
        count = int(
            parts[1]
        )

    except ValueError:
        return []


    if count <= 0:
        return []


    confirm_times = []

    for value in parts[2:]:

        try:
            timestamp_ms = int(
                value
            )

        except ValueError:
            continue


        if timestamp_ms >= 0:
            confirm_times.append(
                timestamp_ms
            )


    if len(confirm_times) != count:
        print(
            "WARNING: firmware reported "
            f"{count} confirmation events, but "
            f"{len(confirm_times)} timestamps "
            "were received."
        )


    return confirm_times


# ============================================================
# GET ALL LABEL INFORMATION
# ============================================================

def get_trial_result(port: str):
    """
    Retrieve complete connector labeling information for the
    most recently completed trial.
    """

    summary = get_last_result(
        port
    )


    # --------------------------------------------------------
    # If no valid trial was stored, don't continue querying.
    # --------------------------------------------------------

    if summary["label"] == "unknown":

        return {
            "label": "unknown",

            "first_full_connect_time_ms":
                None,

            "success_count":
                0,

            "success_times_ms":
                [],

            "success_confirm_times_ms":
                [],
        }


    # --------------------------------------------------------
    # Get all confirmed insertion event timestamps
    # --------------------------------------------------------

    success_times = get_success_times(
        port
    )


    # --------------------------------------------------------
    # Also retrieve confirmation times
    # --------------------------------------------------------

    success_confirm_times = (
        get_success_confirm_times(
            port
        )
    )


    # --------------------------------------------------------
    # Use actual timestamp count as final count.
    # --------------------------------------------------------

    actual_count = len(
        success_times
    )


    firmware_count = summary[
        "success_count"
    ]


    if firmware_count != actual_count:

        print(
            "WARNING: LAST_RESULT reported "
            f"{firmware_count} successes, but "
            f"GET_SUCCESS_TIMES returned "
            f"{actual_count}."
        )


    # success_times and success_confirm_times come from two separate
    # serial round trips (GET_SUCCESS_TIMES / GET_SUCCESS_CONFIRM_TIMES),
    # each with its own timeout -- one can come back short if a response
    # is dropped or truncated, even though the other succeeded fully.
    # Align them by index to actual_count so every other success_* list
    # can be indexed together safely (see get_connector_host_elapsed_s).

    if len(success_confirm_times) != actual_count:

        print(
            "WARNING: GET_SUCCESS_TIMES returned "
            f"{actual_count} value(s), but GET_SUCCESS_CONFIRM_TIMES "
            f"returned {len(success_confirm_times)} -- padding/"
            "truncating to align by index."
        )

        success_confirm_times = (
            success_confirm_times[:actual_count]
            + [None] * max(
                0,
                actual_count - len(success_confirm_times),
            )
        )


    # Determine label from the actual event list.
    #
    # If at least one confirmed insertion occurred:
    #     success
    #
    # Otherwise:
    #     fail

    label = (
        "success"
        if actual_count > 0
        else "fail"
    )


    return {
        "label": label,

        "first_full_connect_time_ms":
            summary[
                "first_full_connect_time_ms"
            ],

        "success_count":
            actual_count,

        "success_times_ms":
            success_times,

        "success_confirm_times_ms":
            success_confirm_times,
    }


# ============================================================
# SESSION DIRECTORY HELPERS
# ============================================================

def get_session_directories(
    recordings_dir: Path,
):
    if not recordings_dir.exists():
        return set()

    return {
        p.resolve()
        for p in recordings_dir.iterdir()
        if p.is_dir()
    }


def find_new_session_directory(
    before,
    recordings_dir: Path,
):
    after = get_session_directories(
        recordings_dir
    )

    new_dirs = (
        after - before
    )

    if not new_dirs:
        return None


    # If there is somehow more than one,
    # use the newest.
    return max(
        new_dirs,
        key=lambda p: p.stat().st_mtime,
    )


# ============================================================
# CONNECTOR EVENTS, HOST CLOCK
# ============================================================

def get_connector_host_elapsed_s(events, session_dir: Path, result: dict):
    """
    Convert the live-captured controller events (see
    wait_for_session_start_and_capture) into host_elapsed_s -- the
    same clock as imu_loadcell.csv, force_torque.csv, and
    button_press_times_s -- using this trial's session_t0_ns from
    metadata.json.

    A leftover SUCCESS_DETECTED/FIRST_FULL_CONNECT line from a
    PREVIOUS trial can still arrive during this trial's capture
    window -- observed in practice, apparently sitting in the
    station's USB buffer across a port close/reopen -- and a stale
    line's index can coincidentally fall in range. So instead of
    trusting index/arrival-order alone, each captured line's embedded
    station-clock value(s) are cross-checked against `result` (this
    trial's OWN authoritative post-trial query response -- see
    get_trial_result) before being accepted; anything that doesn't
    match exactly is a stale line and is dropped.

    Returns (first_full_connect_host_elapsed_s, success_host_elapsed_s).
    success_host_elapsed_s is always the same length as
    result["success_times_ms"], with None for any entry whose line
    never arrived (dropped) or never matched (stale).
    """

    metadata_path = session_dir / "metadata.json"

    if not metadata_path.exists():
        return None, [None] * len(result.get("success_times_ms") or [])

    with metadata_path.open(encoding="utf-8") as f:
        metadata = json.load(f)

    session_t0_ns = metadata.get("host_monotonic_start_ns")

    if session_t0_ns is None:
        return None, [None] * len(result.get("success_times_ms") or [])

    def host_elapsed_s(host_time_ns):
        return (host_time_ns - session_t0_ns) / 1_000_000_000.0

    first_full_connect_ms = result.get("first_full_connect_time_ms")
    first_full_connect_host_elapsed_s = None

    success_times_ms = result.get("success_times_ms") or []
    success_confirm_times_ms = result.get("success_confirm_times_ms") or []
    success_host_elapsed_s = [None] * len(success_times_ms)

    for host_time_ns, line in events:
        if (
            first_full_connect_host_elapsed_s is None
            and first_full_connect_ms is not None
            and line.startswith("FIRST_FULL_CONNECT,")
        ):
            parts = line.split(",")

            try:
                ms = int(parts[1])

            except (IndexError, ValueError):
                continue

            if ms == first_full_connect_ms:
                first_full_connect_host_elapsed_s = (
                    host_elapsed_s(host_time_ns)
                )

        elif line.startswith("SUCCESS_DETECTED,"):
            parts = line.split(",")

            try:
                index = int(parts[1])
                start_ms = int(parts[2])
                confirm_ms = int(parts[3])

            except (IndexError, ValueError):
                continue

            # success_confirm_times_ms can be shorter than
            # success_times_ms, or hold None at an index (the
            # GET_SUCCESS_CONFIRM_TIMES query failed/returned NONE
            # that trial -- a separate serial round trip from
            # GET_SUCCESS_TIMES, so one can fail independently of the
            # other). When confirm data isn't available to cross-check,
            # fall back to matching on index + start_ms alone, which
            # is already specific enough to rule out a stale line from
            # another trial.
            confirm_known = (
                index < len(success_confirm_times_ms)
                and success_confirm_times_ms[index] is not None
            )

            if (
                0 <= index < len(success_times_ms)
                and start_ms == success_times_ms[index]
                and (
                    not confirm_known
                    or confirm_ms == success_confirm_times_ms[index]
                )
            ):
                success_host_elapsed_s[index] = (
                    host_elapsed_s(host_time_ns)
                )

    return (
        first_full_connect_host_elapsed_s,
        success_host_elapsed_s,
    )


# ============================================================
# BUTTON PRESS TIMES
# ============================================================

def get_button_press_times_s(session_dir: Path):
    """
    Read the manual mark-button press timestamps out of
    imu_loadcell.csv.

    These are logged on the main sensing Teensy's serial stream
    as "button" rows and timestamped in host_elapsed_s -- the
    same clock used for every other column in that CSV, and for
    the microphone/GelSight timestamp files. This is a different
    clock than success_times_ms below, which comes from the
    station controller's own millis() clock.
    """

    imu_csv_path = (
        session_dir / "imu_loadcell.csv"
    )

    if not imu_csv_path.exists():
        return []

    button_press_times_s = []

    with imu_csv_path.open(
        newline="",
        encoding="utf-8",
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:
            if row.get("type") != "button":
                continue

            try:
                button_press_times_s.append(
                    float(row["host_elapsed_s"])
                )

            except (KeyError, ValueError):
                continue

    return button_press_times_s


# ============================================================
# SAVE LABEL
# ============================================================

def save_label(
    session_dir: Path,
    result: dict,
    button_press_times_s=None,
    first_full_connect_host_elapsed_s=None,
    success_host_elapsed_s=None,
):
    """
    Save all successful insertion timestamps.

    success_times_ms / first_full_connect_time_ms:
        Ground-truth insertion event timestamps, in the station
        controller's own free-running millis() clock (zeroed at
        that board's button long-press). Each success_times_ms
        entry is the moment all four connector contacts first
        became connected for an insertion that subsequently
        remained connected for at least SUCCESS_CONFIRM_MS.

    success_confirm_times_ms:
        Time at which the dwell-time confirmation completed.

        Useful for debugging, but normally NOT the ground-truth
        event timestamp used for ML training.

    first_full_connect_host_elapsed_s / success_host_elapsed_s:
        The SAME events as above, but timestamped by the host the
        instant each serial line arrived (see
        get_connector_host_elapsed_s / monotonic_ns), so they are
        in host_elapsed_s -- the same clock as imu_loadcell.csv,
        force_torque.csv, the microphone, GelSight, and
        button_press_times_s. Prefer these over the *_ms fields
        above when aligning connector events against any other
        recorded channel; the *_ms fields remain for reference
        since they come straight from the station's own detection
        logic and never depend on serial delivery.

        success_host_elapsed_s is always the same length and order
        as success_times_ms; an entry is null if that event's serial
        line was never captured live, or was dropped as stale (see
        get_connector_host_elapsed_s) -- in which case success_times_ms
        at that index is still the ground truth, just not expressible
        in host_elapsed_s for this trial.

    button_press_times_s:
        Operator mark-button press times, in host_elapsed_s --
        the same clock as imu_loadcell.csv, the microphone, and
        GelSight timestamps. NOT the same clock as
        success_times_ms (see get_button_press_times_s).
    """

    metadata = {

        # ----------------------------------------------------
        # Overall session label
        # ----------------------------------------------------

        "label":
            result["label"],


        # ----------------------------------------------------
        # Number of successful insertions
        # ----------------------------------------------------

        "success_count":
            result["success_count"],


        # ----------------------------------------------------
        # ALL successful insertion timestamps
        # ----------------------------------------------------

        "success_times_ms":
            result["success_times_ms"],


        # ----------------------------------------------------
        # Confirmation timestamps
        # ----------------------------------------------------

        "success_confirm_times_ms":
            result[
                "success_confirm_times_ms"
            ],


        # ----------------------------------------------------
        # First instant all 4 contacts ever became connected.
        #
        # This may represent an unconfirmed / brief connection.
        # ----------------------------------------------------

        "first_full_connect_time_ms":
            result[
                "first_full_connect_time_ms"
            ],


        # ----------------------------------------------------
        # Same two events above, in host_elapsed_s -- the same
        # clock as imu_loadcell.csv, force_torque.csv, and
        # button_press_times_s. See get_connector_host_elapsed_s.
        # ----------------------------------------------------

        "first_full_connect_host_elapsed_s":
            first_full_connect_host_elapsed_s,

        "success_host_elapsed_s":
            success_host_elapsed_s or [],


        # ----------------------------------------------------
        # Operator mark-button presses.
        #
        # host_elapsed_s clock -- see get_button_press_times_s
        # and imu_loadcell.csv, NOT the *_ms fields above.
        # ----------------------------------------------------

        "button_press_times_s":
            button_press_times_s or [],


        # ----------------------------------------------------
        # Trial metadata
        # ----------------------------------------------------

        "trial_duration_s":
            TRIAL_DURATION_S,
    }


    output_path = (
        session_dir /
        "connector_label.json"
    )


    with open(
        output_path,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            metadata,
            f,
            indent=4,
        )


    print(
        f"Saved label: {output_path}"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser()


    parser.add_argument(
        "--controller-port",
        required=True,
        help=(
            "Teensy port for the station controller "
            "(SESSION_START / button / LEDs / connector sensing)."
        ),
    )


    parser.add_argument(
        "--recordings-dir",
        default="recordings",
        help=(
            "Directory used by "
            "record_multimodal.py"
        ),
    )


    parser.add_argument(
        "--ft-port",
        default=None,
        help=(
            "Serial port for the MMS101 force/torque sensor, streamed "
            "continuously for the whole trial loop. "
            "Auto-detected if omitted."
        ),
    )


    parser.add_argument(
        "--ft-tare-file",
        default="mms101/tare_offsets.json",
        help=(
            "Where the one-time warm-up/tare saves its zero offsets "
            "(read back on --skip-ft-warmup). "
            "Default: mms101/tare_offsets.json."
        ),
    )


    parser.add_argument(
        "--ft-interval",
        type=int,
        default=1000,
        help=(
            "MMS101 sample interval in microseconds. "
            "Default: 1000 (1 kHz)."
        ),
    )


    parser.add_argument(
        "--ft-restart",
        type=int,
        default=10,
        help=(
            "MMS101 temperature-correction refresh interval, in "
            "samples. Default: 100."
        ),
    )


    parser.add_argument(
        "--ft-warmup-seconds",
        type=float,
        default=60.0,
        help=(
            "How long to stream the force/torque sensor before "
            "taring, in seconds, at startup. Default: 300 (5 min)."
        ),
    )


    parser.add_argument(
        "--ft-tare-samples",
        type=int,
        default=500,
        help=(
            "Number of samples to average for the zero offset at "
            "startup. Default: 500."
        ),
    )


    parser.add_argument(
        "--skip-ft-warmup",
        action="store_true",
        help=(
            "Skip the startup power-on/warm-up/tare and go straight "
            "to continuous streaming, reusing whatever --ft-tare-file "
            "already has on disk (e.g. restarting this script later "
            "the same session)."
        ),
    )


    parser.add_argument(
        "--live-snapshot-file",
        default="mms101/live_snapshot.csv",
        help=(
            "Rolling snapshot of the last few seconds of force/torque "
            "data, rewritten continuously so mms101/live_view.py can "
            "show a live plot in another terminal without opening the "
            "sensor's serial port itself. "
            "Default: mms101/live_snapshot.csv."
        ),
    )


    parser.add_argument(
        "--no-live-snapshot",
        action="store_true",
        help="Do not write the live-snapshot file at all.",
    )


    parser.add_argument(
        "--live-snapshot-interval",
        type=float,
        default=0.1,
        help=(
            "How often to rewrite the live-snapshot file, in seconds. "
            "Total lag in mms101/live_view.py is roughly this plus "
            "its own --refresh-ms poll interval (not synchronized). "
            "Default: 0.1."
        ),
    )


    parser.add_argument(
        "--live-snapshot-window",
        type=float,
        default=2.0,
        help=(
            "How many trailing seconds of data to include in each "
            "live-snapshot write. A bigger window costs more I/O per "
            "write without making the latest point any fresher. "
            "Default: 2.0."
        ),
    )


    parser.add_argument(
        "--retare-request-file",
        default="mms101/retare_request.flag",
        help=(
            "Touched (e.g. by pressing 't' in mms101/live_view.py) to "
            "request a mid-session re-tare of the force/torque sensor "
            "-- rezeros using recent buffered samples without "
            "stopping the stream. Default: mms101/retare_request.flag."
        ),
    )


    parser.add_argument(
        "--retare-samples",
        type=int,
        default=500,
        help=(
            "Number of recent samples to average when a re-tare is "
            "requested. Default: 500."
        ),
    )


    parser.add_argument(
        "--no-retare",
        action="store_true",
        help="Disable mid-session re-taring entirely.",
    )


    parser.add_argument(
        "recorder_command",
        nargs=argparse.REMAINDER,
        help=(
            "Command used to launch "
            "record_multimodal.py"
        ),
    )


    args = parser.parse_args()


    recordings_dir = Path(
        args.recordings_dir
    )


    command = (
        args.recorder_command
    )


    # argparse may keep "--" as the first argument.
    if (
        command and
        command[0] == "--"
    ):
        command = command[1:]


    if not command:
        raise ValueError(
            "You must provide the "
            "record_multimodal.py command."
        )


    # ========================================================
    # START CONTINUOUS FORCE/TORQUE STREAMING
    # ========================================================

    ft_port = resolve_ft_port(args.ft_port)

    streamer = ContinuousForceTorqueStreamer(
        port=ft_port,
        interval_us=args.ft_interval,
        restart_samples=args.ft_restart,
    )

    if args.skip_ft_warmup:
        tare_path = Path(args.ft_tare_file)

        if not tare_path.exists():
            raise RuntimeError(
                f"--skip-ft-warmup was given, but {tare_path} "
                "does not exist. Run without --skip-ft-warmup once, "
                "or point --ft-tare-file at an existing tare file."
            )

        print(f"\nSkipping warm-up; reusing tare from {tare_path}.")
        tare_data = json.loads(tare_path.read_text())
        streamer.start_with_existing_tare(tare_data)
    else:
        print()
        print("========================================")
        print("WARMING UP FORCE/TORQUE SENSOR")
        print("========================================")
        print()

        tare_data = streamer.warm_up_and_start(
            warmup_seconds=args.ft_warmup_seconds,
            tare_samples=args.ft_tare_samples,
        )

        tare_path = Path(args.ft_tare_file)
        tare_path.parent.mkdir(parents=True, exist_ok=True)
        tare_path.write_text(json.dumps(tare_data, indent=2))
        print(f"\nSaved tare offsets to {tare_path}")

    print(
        "Force/torque sensor is now streaming continuously "
        "for the whole trial loop."
    )

    if not args.no_live_snapshot:
        streamer.start_live_snapshot(
            args.live_snapshot_file,
            window_seconds=args.live_snapshot_window,
            interval_seconds=args.live_snapshot_interval,
        )
        print(
            f"Live snapshot: {args.live_snapshot_file} "
            "(view with: python mms101/live_view.py)"
        )

    if not args.no_retare:
        streamer.start_retare_watcher(
            args.retare_request_file,
            tare_file_path=args.ft_tare_file,
            sample_count=args.retare_samples,
        )
        print(
            "Mid-session re-tare armed: touch "
            f"{args.retare_request_file} to rezero "
            "(or press 't' in mms101/live_view.py)."
        )

    # recorder_command's own subprocess must not also open the F/T
    # serial port -- it is already held open by this process.
    command = command + ["--ft-external"]


    # ========================================================
    # CONTINUOUS TRIAL LOOP
    # ========================================================

    try:
        run_trial_loop(
            controller_port=args.controller_port,
            recordings_dir=recordings_dir,
            command=command,
            streamer=streamer,
        )
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        print("\nStopping force/torque stream...")
        streamer.close()


def run_trial_loop(
    controller_port: str,
    recordings_dir: Path,
    command: List[str],
    streamer: ContinuousForceTorqueStreamer,
) -> None:
    while True:

        # ====================================================
        # WAIT FOR PHYSICAL BUTTON
        # ====================================================
        #
        # Keeps the controller port open and live-capturing events
        # (host-timestamped) all the way through the recording below,
        # instead of closing it right after SESSION_START.

        ser, capture_thread, stop_flag, events = (
            wait_for_session_start_and_capture(
                controller_port
            )
        )


        # ====================================================
        # RECORD EXISTING SESSION DIRECTORIES
        # ====================================================

        directories_before = (
            get_session_directories(
                recordings_dir
            )
        )


        # ====================================================
        # START MULTIMODAL RECORDER
        # ====================================================

        print()
        print(
            "Starting multimodal recorder..."
        )
        print()


        print(
            " ".join(command)
        )


        subprocess.run(
            command,
            check=False,
        )


        print()
        print(
            "Recording finished."
        )


        # Stop live-capturing and release the port so get_trial_result
        # below can reconnect to query it.
        stop_capture(ser, capture_thread, stop_flag)


        # ====================================================
        # QUERY CONNECTOR LABELS
        # ====================================================

        result = get_trial_result(
            controller_port
        )


        print()
        print(
            "========================================"
        )

        print(
            f"Trial result: "
            f"{result['label'].upper()}"
        )


        # ----------------------------------------------------
        # First raw full connection
        # ----------------------------------------------------

        first_time = result[
            "first_full_connect_time_ms"
        ]


        if first_time is not None:

            print(
                "First full connection at "
                f"{first_time / 1000:.3f} s"
            )

        else:

            print(
                "First full connection: "
                "not detected"
            )


        # ----------------------------------------------------
        # Successful insertions
        # ----------------------------------------------------

        success_times = result[
            "success_times_ms"
        ]


        print(
            f"Successful insertions: "
            f"{len(success_times)}"
        )


        if success_times:

            print(
                "Successful insertion times:"
            )


            for index, timestamp_ms in enumerate(
                success_times,
                start=1,
            ):

                print(
                    f"  {index}: "
                    f"{timestamp_ms / 1000:.3f} s"
                )

        else:

            print(
                "Successful insertion times: "
                "none"
            )


        print(
            "========================================"
        )


        # ====================================================
        # FIND NEW SESSION FOLDER
        # ====================================================

        session_dir = (
            find_new_session_directory(
                directories_before,
                recordings_dir,
            )
        )


        if session_dir is None:

            print(
                "WARNING: could not determine "
                "new recording directory."
            )


        else:

            write_force_torque_csv(
                streamer,
                session_dir,
            )

            button_press_times_s = (
                get_button_press_times_s(
                    session_dir
                )
            )

            (
                first_full_connect_host_elapsed_s,
                success_host_elapsed_s,
            ) = get_connector_host_elapsed_s(
                events,
                session_dir,
                result,
            )

            missing = sum(
                1 for v in success_host_elapsed_s if v is None
            )

            if missing:
                print(
                    "WARNING: "
                    f"{missing} of {len(success_host_elapsed_s)} "
                    "success event(s) have no host-aligned timestamp "
                    "(dropped or stale serial line) -- see null "
                    "entries in success_host_elapsed_s."
                )

            save_label(
                session_dir,
                result,
                button_press_times_s=(
                    button_press_times_s
                ),
                first_full_connect_host_elapsed_s=(
                    first_full_connect_host_elapsed_s
                ),
                success_host_elapsed_s=(
                    success_host_elapsed_s
                ),
            )

            print(
                f"Button presses: "
                f"{len(button_press_times_s)}"
            )


            print(
                f"Session: {session_dir}"
            )


        # ====================================================
        # READY FOR NEXT TRIAL
        # ====================================================

        print()
        print(
            "========================================"
        )

        print(
            "TRIAL COMPLETE"
        )

        print(
            "Green LED should be flashing again."
        )

        print(
            "Ready for next button press."
        )

        print(
            "========================================"
        )

        print()


if __name__ == "__main__":
    main()
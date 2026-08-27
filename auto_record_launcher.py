import argparse
import json
import subprocess
import time
from pathlib import Path

import serial


BAUD_RATE = 2_000_000
TRIAL_DURATION_S = 65.0


# ============================================================
# WAIT FOR SESSION START
# ============================================================

def wait_for_session_start(port: str):
    """
    Open the Teensy serial port and wait until the firmware sends:

        SESSION_START

    Then close the port so record_multimodal.py can use it.
    """

    print(f"Waiting for controller on {port}...")

    with serial.Serial(
        port,
        BAUD_RATE,
        timeout=0.1,
    ) as ser:

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

        while True:
            raw = ser.readline()

            if not raw:
                continue

            try:
                line = raw.decode(
                    "utf-8",
                    errors="ignore",
                ).strip()

            except Exception:
                continue

            if not line:
                continue

            print(f"[controller] {line}")

            if line == "SESSION_START":
                print()
                print("Session trigger received.")
                print("Recording started.")
                return


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
# SAVE LABEL
# ============================================================

def save_label(
    session_dir: Path,
    result: dict,
):
    """
    Save all successful insertion timestamps.

    success_times_ms:
        Ground-truth insertion event timestamps.

        Each timestamp corresponds to the moment all four
        connector contacts first became connected for an
        insertion that subsequently remained connected for at
        least SUCCESS_CONFIRM_MS.

    success_confirm_times_ms:
        Time at which the dwell-time confirmation completed.

        Useful for debugging, but normally NOT the ground-truth
        event timestamp used for ML training.
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
            "Teensy port used for IMU/load-cell "
            "and session controller"
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
    # CONTINUOUS TRIAL LOOP
    # ========================================================

    while True:

        # ====================================================
        # WAIT FOR PHYSICAL BUTTON
        # ====================================================

        wait_for_session_start(
            args.controller_port
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


        # ====================================================
        # QUERY CONNECTOR LABELS
        # ====================================================

        result = get_trial_result(
            args.controller_port
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

            save_label(
                session_dir,
                result,
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
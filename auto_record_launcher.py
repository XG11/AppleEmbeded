import argparse
import json
import subprocess
import time
from pathlib import Path

import serial


BAUD_RATE = 2_000_000
TRIAL_DURATION_S = 65.0


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
                print("Yellow LED should now be blinking.")
                return


def get_last_result(port: str):
    """
    Reconnect to Teensy after record_multimodal exits and ask
    for the result that the firmware stored.

    New firmware response format:

        LAST_RESULT,SUCCESS,first_full_connect_ms,confirmed_success_ms

    or:

        LAST_RESULT,FAIL,first_full_connect_ms,-1

    first_full_connect_ms:
        First instant all four connector pins were simultaneously
        connected. No dwell-time confirmation.

    confirmed_success_ms:
        Original label: all four pins remained connected for the
        configured SUCCESS_CONFIRM_MS (currently 200 ms).
    """

    time.sleep(0.5)

    with serial.Serial(
        port,
        BAUD_RATE,
        timeout=1.0,
    ) as ser:

        time.sleep(0.5)

        ser.reset_input_buffer()

        ser.write(
            b"GET_LAST_RESULT\n"
        )

        deadline = time.time() + 3.0

        while time.time() < deadline:
            raw = ser.readline()

            if not raw:
                continue

            line = raw.decode(
                "utf-8",
                errors="ignore",
            ).strip()

            if not line.startswith(
                "LAST_RESULT,"
            ):
                continue

            parts = line.split(",")

            if len(parts) < 2:
                continue

            result = parts[1]

            if result == "NONE":
                return {
                    "label": "unknown",
                    "first_full_connect_time_ms": None,
                    "success_time_ms": None,
                }

            if result not in ("SUCCESS", "FAIL"):
                continue

            first_full_connect_time_ms = None
            success_time_ms = None

            # New firmware format.
            if len(parts) >= 4:
                try:
                    first_ms = int(parts[2])
                    confirmed_ms = int(parts[3])

                    if first_ms >= 0:
                        first_full_connect_time_ms = first_ms

                    if confirmed_ms >= 0:
                        success_time_ms = confirmed_ms

                except ValueError:
                    pass

            # Backward compatibility with the previous firmware:
            # LAST_RESULT,SUCCESS,success_time_ms
            elif len(parts) >= 3 and result == "SUCCESS":
                try:
                    old_success_ms = int(parts[2])

                    if old_success_ms >= 0:
                        success_time_ms = old_success_ms

                except ValueError:
                    pass

            return {
                "label": (
                    "success"
                    if result == "SUCCESS"
                    else "fail"
                ),
                "first_full_connect_time_ms":
                    first_full_connect_time_ms,
                "success_time_ms": success_time_ms,
            }

    return {
        "label": "unknown",
        "first_full_connect_time_ms": None,
        "success_time_ms": None,
    }


def get_session_directories(recordings_dir: Path):
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

    new_dirs = after - before

    if not new_dirs:
        return None

    # If there is somehow more than one, use the newest.
    return max(
        new_dirs,
        key=lambda p: p.stat().st_mtime,
    )


def save_label(
    session_dir: Path,
    result: dict,
):
    """
    Save BOTH connector timing labels.

    first_full_connect_time_ms:
        First instant all four connector pins are connected.

    success_time_ms:
        Original / older label: full connection confirmed after
        200 ms continuous contact.
    """

    metadata = {
        "label": result["label"],

        # New label.
        "first_full_connect_time_ms":
            result["first_full_connect_time_ms"],

        # Keep the old field name so existing analysis code can
        # continue using it without changes.
        "success_time_ms":
            result["success_time_ms"],

        "trial_duration_s": TRIAL_DURATION_S,
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
        help="Directory used by record_multimodal.py",
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

    command = args.recorder_command

    # argparse keeps "--" as the first argument sometimes.
    if command and command[0] == "--":
        command = command[1:]

    if not command:
        raise ValueError(
            "You must provide the "
            "record_multimodal.py command."
        )


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
        # START ORIGINAL RECORDER
        # ====================================================

        print()
        print("Starting multimodal recorder...")
        print()

        print(
            " ".join(command)
        )

        subprocess.run(
            command,
            check=False,
        )


        print()
        print("Recording finished.")


        # ====================================================
        # QUERY CONNECTOR LABELS
        # ====================================================

        result = get_last_result(
            args.controller_port
        )

        print(
            f"Trial result: {result['label'].upper()}"
        )


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
                "First full connection: not detected"
            )


        confirmed_time = result[
            "success_time_ms"
        ]

        if confirmed_time is not None:
            print(
                "Confirmed connector success at "
                f"{confirmed_time / 1000:.3f} s"
            )
        else:
            print(
                "Confirmed connector success: not detected"
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


        print()
        print("========================================")
        print("TRIAL COMPLETE")
        print("Green LED should be flashing again.")
        print("Ready for next button press.")
        print("========================================")
        print()


if __name__ == "__main__":
    main()
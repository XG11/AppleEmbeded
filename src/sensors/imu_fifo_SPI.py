import csv
import struct
import time
import spidev
import Jetson.GPIO as GPIO


def perf_counter_ns():
    return int(time.perf_counter() * 1e9)


class IMUSPIFIFORecorder:
    WHO_AM_I = 0x0F
    CTRL1_XL = 0x10
    CTRL2_G = 0x11
    CTRL3_C = 0x12
    INT1_CTRL = 0x0D

    FIFO_CTRL1 = 0x07
    FIFO_CTRL2 = 0x08
    FIFO_CTRL3 = 0x09
    FIFO_CTRL4 = 0x0A
    FIFO_STATUS1 = 0x3A
    FIFO_STATUS2 = 0x3B
    FIFO_DATA_OUT_TAG = 0x78

    ACCEL_SCALE_G = 0.000488
    GYRO_SCALE_DPS = 0.07

    def __init__(self, output_csv="imu_spi_fifo.csv", duration_sec=30, int_pin=11):
        self.output_csv = output_csv
        self.duration_sec = duration_sec
        self.int_pin = int_pin

        GPIO.setmode(GPIO.BOARD)
        GPIO.setup(self.int_pin, GPIO.IN)

    def write_reg(self, spi, reg, val):
        spi.xfer2([reg & 0x7F, val & 0xFF])

    def read_reg(self, spi, reg):
        resp = spi.xfer2([reg | 0x80, 0x00])
        return resp[1]

    def read_fifo_bytes(self, spi, n):
        resp = spi.xfer2([self.FIFO_DATA_OUT_TAG | 0x80] + [0x00] * n)
        return bytes(resp[1:])

    def fifo_level(self, spi):
        s1 = self.read_reg(spi, self.FIFO_STATUS1)
        s2 = self.read_reg(spi, self.FIFO_STATUS2)
        return s1 | ((s2 & 0x03) << 8)

    def wait_for_fifo_int(self, timeout=0.1):
        start = time.perf_counter()
        while time.perf_counter() - start < timeout:
            if GPIO.input(self.int_pin) == GPIO.HIGH:
                return True
            time.sleep(0.0005)
        return False

    def run(self):
        spi = spidev.SpiDev()
        spi.open(1, 1)
        spi.max_speed_hz = 10_000_000
        spi.mode = 3

        try:
            who = self.read_reg(spi, self.WHO_AM_I)
            print("WHO_AM_I:", hex(who))

            self.write_reg(spi, self.CTRL3_C, 0x01)
            time.sleep(0.1)

            self.write_reg(spi, self.CTRL3_C, 0x44)

            self.write_reg(spi, self.CTRL1_XL, 0xAC)
            self.write_reg(spi, self.CTRL2_G, 0xAC)

            self.write_reg(spi, self.FIFO_CTRL1, 255)
            self.write_reg(spi, self.FIFO_CTRL2, 0x00)

            self.write_reg(spi, self.FIFO_CTRL3, 0xAA)
            self.write_reg(spi, self.FIFO_CTRL4, 0x06)

            self.write_reg(spi, self.INT1_CTRL, 0x08)

            print("Warming up...")
            time.sleep(0.5)

            level = self.fifo_level(spi)
            if level > 0:
                self.read_fifo_bytes(spi, min(level, 255) * 7)

            print("Recording...")

            t0_ns = perf_counter_ns()
            start = time.perf_counter()
            last_print = start
            count = 0

            with open(self.output_csv, "w", newline="", buffering=1024 * 1024) as f:
                writer = csv.writer(f)
                writer.writerow(["t_ns", "t_ms", "sensor", "x", "y", "z", "unit"])

                while time.perf_counter() - start < self.duration_sec:
                    self.wait_for_fifo_int(timeout=0.1)

                    level = self.fifo_level(spi)
                    if level == 0:
                        continue

                    n_samples = min(level, 255)
                    raw = self.read_fifo_bytes(spi, n_samples * 7)

                    t_ns = perf_counter_ns()
                    t_ms = (t_ns - t0_ns) / 1e6

                    for i in range(0, len(raw), 7):
                        tag = raw[i] >> 3
                        x, y, z = struct.unpack_from("<hhh", raw, i + 1)

                        if tag == 0x01:
                            writer.writerow([
                                t_ns, t_ms, "gyro",
                                round(x * self.GYRO_SCALE_DPS, 2),
                                round(y * self.GYRO_SCALE_DPS, 2),
                                round(z * self.GYRO_SCALE_DPS, 2),
                                "dps",
                            ])
                            count += 1

                        elif tag == 0x02:
                            writer.writerow([
                                t_ns, t_ms, "accel",
                                round(x * self.ACCEL_SCALE_G, 2),
                                round(y * self.ACCEL_SCALE_G, 2),
                                round(z * self.ACCEL_SCALE_G, 2),
                                "g",
                            ])
                            count += 1

                    now = time.perf_counter()
                    if now - last_print >= 1.0:
                        print("FIFO samples/sec:", count)
                        count = 0
                        last_print = now

            print("Done recording.")

        finally:
            self.write_reg(spi, self.FIFO_CTRL4, 0x00)
            self.write_reg(spi, self.CTRL1_XL, 0x00)
            self.write_reg(spi, self.CTRL2_G, 0x00)
            spi.close()
            GPIO.cleanup()
            print("IMU stopped.")


if __name__ == "__main__":
    recorder = IMUSPIFIFORecorder(
        output_csv="imu_spi_fifo_30s.csv",
        duration_sec=30,
        int_pin=11,
    )
    recorder.run()

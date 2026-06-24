import time
import spidev


DURATION_SEC = 15
PRINT_INTERVAL_SEC = 0.5

SPI_BUS = 0
SPI_DEVICE = 0
SPI_SPEED_HZ = 10_000_000

WHO_AM_I = 0x0F
CTRL1_XL = 0x10
CTRL2_G = 0x11
CTRL3_C = 0x12
CTRL4_C = 0x13
FIFO_CTRL1 = 0x07
FIFO_CTRL2 = 0x08
FIFO_CTRL3 = 0x09
FIFO_CTRL4 = 0x0A
FIFO_STATUS1 = 0x3A
FIFO_STATUS2 = 0x3B
FIFO_DATA_OUT_TAG = 0x78

READ_MASK = 0x80

# FIFO tags for LSM6DSO32 / LSM6DSO family
TAG_GYRO_NC = 0x01
TAG_XL_NC = 0x02

GYRO_SCALE_DPS_PER_LSB = 2000.0 / 32768.0
ACCEL_SCALE_G_PER_LSB = 32.0 / 32768.0


def int16(lo, hi):
    value = lo | (hi << 8)
    if value >= 32768:
        value -= 65536
    return value


class LSM6DSO32SPIStreamer:
    def __init__(self):
        self.spi = spidev.SpiDev()
        self.spi.open(SPI_BUS, SPI_DEVICE)
        self.spi.max_speed_hz = SPI_SPEED_HZ
        self.spi.mode = 0b11

    def read_reg(self, reg):
        return self.spi.xfer2([reg | READ_MASK, 0x00])[1]

    def write_reg(self, reg, value):
        self.spi.xfer2([reg & 0x7F, value & 0xFF])

    def read_bytes(self, reg, n):
        return self.spi.xfer2([reg | READ_MASK] + [0x00] * n)[1:]

    def setup(self):
        who = self.read_reg(WHO_AM_I)
        print(f"WHO_AM_I = 0x{who:02X}")

        # Software reset
        self.write_reg(CTRL3_C, 0x01)
        time.sleep(0.1)

        # BDU = 1, IF_INC = 1
        self.write_reg(CTRL3_C, 0x44)

        # Accel: 6.66 kHz, ±32 g
        # ODR_XL = 0b1010, FS_XL = 0b11
        self.write_reg(CTRL1_XL, 0b10101100)

        # Gyro: 6.66 kHz, ±2000 dps
        # ODR_G = 0b1010, FS_G = 0b11
        self.write_reg(CTRL2_G, 0b10101100)

        # FIFO reset / bypass
        self.write_reg(FIFO_CTRL4, 0x00)
        time.sleep(0.05)

        # FIFO watermark, optional
        self.write_reg(FIFO_CTRL1, 0x00)
        self.write_reg(FIFO_CTRL2, 0x00)

        # FIFO batching:
        # gyro batch 6.66 kHz, accel batch 6.66 kHz
        self.write_reg(FIFO_CTRL3, 0b00001010)

        # FIFO continuous mode
        self.write_reg(FIFO_CTRL4, 0b00000110)

        print("IMU configured: accel + gyro FIFO at 6.66 kHz")
        print("Printing latest gyro every 0.5 s\n")

    def fifo_level(self):
        s1 = self.read_reg(FIFO_STATUS1)
        s2 = self.read_reg(FIFO_STATUS2)
        return s1 | ((s2 & 0x03) << 8)

    def read_fifo_sample(self):
        data = self.read_bytes(FIFO_DATA_OUT_TAG, 7)

        tag = data[0] >> 3

        x = int16(data[1], data[2])
        y = int16(data[3], data[4])
        z = int16(data[5], data[6])

        return tag, x, y, z

    def run(self, duration_sec):
        latest_gyro = None
        latest_accel = None

        start = time.monotonic()
        last_print = start
        sample_count = 0

        while time.monotonic() - start < duration_sec:
            level = self.fifo_level()

            for _ in range(level):
                tag, x_raw, y_raw, z_raw = self.read_fifo_sample()
                sample_count += 1

                if tag == TAG_GYRO_NC:
                    latest_gyro = (
                        x_raw * GYRO_SCALE_DPS_PER_LSB,
                        y_raw * GYRO_SCALE_DPS_PER_LSB,
                        z_raw * GYRO_SCALE_DPS_PER_LSB,
                    )

                elif tag == TAG_XL_NC:
                    latest_accel = (
                        x_raw * ACCEL_SCALE_G_PER_LSB,
                        y_raw * ACCEL_SCALE_G_PER_LSB,
                        z_raw * ACCEL_SCALE_G_PER_LSB,
                    )

            now = time.monotonic()

            if now - last_print >= PRINT_INTERVAL_SEC:
                elapsed = now - start

                if latest_gyro is None:
                    print(f"{elapsed:6.2f}s | gyro not received yet")
                else:
                    gx, gy, gz = latest_gyro

                    if latest_accel is not None:
                        ax, ay, az = latest_accel
                        print(
                            f"{elapsed:6.2f}s | "
                            f"gyro[dps] gx={gx:8.2f}, gy={gy:8.2f}, gz={gz:8.2f} | "
                            f"accel[g] ax={ax:7.3f}, ay={ay:7.3f}, az={az:7.3f}"
                        )
                    else:
                        print(
                            f"{elapsed:6.2f}s | "
                            f"gyro[dps] gx={gx:8.2f}, gy={gy:8.2f}, gz={gz:8.2f}"
                        )

                last_print = now

            time.sleep(0.001)

        print("\nDone.")

    def close(self):
        self.spi.close()


def main():
    imu = LSM6DSO32SPIStreamer()

    try:
        imu.setup()
        imu.run(DURATION_SEC)

    finally:
        imu.close()


if __name__ == "__main__":
    main()
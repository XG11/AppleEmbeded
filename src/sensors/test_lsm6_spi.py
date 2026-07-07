import spidev
import time

spi = spidev.SpiDev()
spi.open(0, 0)          # test /dev/spidev0.0 first
spi.max_speed_hz = 100000
spi.mode = 0

while True:
    rx = spi.xfer2([0xAA, 0x55])
    time.sleep(0.2)
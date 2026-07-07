import spidev
import time

spi = spidev.SpiDev()
spi.open(0,1)
spi.max_speed_hz = 100000
spi.mode = 3

while True:
    spi.xfer2([0xAA])
    time.sleep(0.5)
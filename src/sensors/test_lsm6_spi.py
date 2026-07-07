import spidev
import time

for bus, dev in [(0,0), (0,1), (1,0), (1,1)]:
    print("Testing", bus, dev)
    spi = spidev.SpiDev()
    spi.open(bus, dev)
    spi.max_speed_hz = 100000
    spi.mode = 3

    t0 = time.time()
    while time.time() - t0 < 10:
        spi.xfer2([0xAA, 0x55, 0xAA, 0x55])
        time.sleep(0.05)

    spi.close()
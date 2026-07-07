import spidev, time
import Jetson.GPIO as GPIO

CS_PIN = 22

GPIO.setmode(GPIO.BOARD)
GPIO.setup(CS_PIN, GPIO.OUT, initial=GPIO.HIGH)

spi = spidev.SpiDev()
spi.open(0, 1)
spi.no_cs = True
spi.mode = 0
spi.max_speed_hz = 100000

GPIO.output(CS_PIN, GPIO.LOW)
time.sleep(0.001)
resp = spi.xfer2([0x8F, 0x00])
GPIO.output(CS_PIN, GPIO.HIGH)

print(resp)

spi.close()
GPIO.cleanup()

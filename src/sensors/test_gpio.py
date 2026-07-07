import Jetson.GPIO as GPIO
import time

PIN = 12   # physical header pin number

GPIO.setmode(GPIO.BOARD)
GPIO.setup(PIN, GPIO.OUT, initial=GPIO.LOW)

print("Toggling physical pin 12. Ctrl+C to stop.")

try:
    while True:
        GPIO.output(PIN, GPIO.HIGH)
        time.sleep(0.5)
        GPIO.output(PIN, GPIO.LOW)
        time.sleep(0.5)
finally:
    GPIO.cleanup()
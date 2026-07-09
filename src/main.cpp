#include <Arduino.h>
#include "lsm6dso32.h"

static constexpr uint8_t IMU_CS = 10;

LSM6DSO32 imu(IMU_CS);

void setup()
{
    Serial.begin(115200);
    while (!Serial && millis() < 3000) {}

    Serial.println();
    Serial.println("LSM6DSO32 accel-only SPI test");

    if (!imu.begin())
    {
        Serial.println("ERROR: IMU not found");
        while (1)
        {
            delay(1000);
        }
    }

    Serial.println("IMU found: WHO_AM_I = 0x6C");

    imu.configureAccel();

    Serial.println("timestamp_us,ax_raw,ay_raw,az_raw");
}

void loop()
{
    int16_t ax, ay, az;
    imu.readAccelRaw(ax, ay, az);

    Serial.print(micros());
    Serial.print(",");
    Serial.print(ax);
    Serial.print(",");
    Serial.print(ay);
    Serial.print(",");
    Serial.println(az);

    delay(10);
}
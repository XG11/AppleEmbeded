#include <Arduino.h>
#include "lsm6dso32.h"

static constexpr uint8_t IMU_CS = 10;

LSM6DSO32 imu(IMU_CS);

void setup()
{
    Serial.begin(115200);
    delay(1000);

    Serial.println();
    Serial.println("LSM6DSO32 accel + gyro SPI test");

    if (!imu.begin())
    {
        Serial.println("ERROR: IMU not found");
        while (1)
        {
            delay(1000);
        }
    }

    Serial.println("IMU found");

    imu.configureAccelGyro();

    Serial.print("CTRL1_XL = 0x");
    Serial.println(imu.readRegister(0x10), HEX);

    Serial.print("CTRL2_G = 0x");
    Serial.println(imu.readRegister(0x11), HEX);

    Serial.print("CTRL3_C = 0x");
    Serial.println(imu.readRegister(0x12), HEX);

    Serial.println("timestamp_us,ax_raw,ay_raw,az_raw,gx_raw,gy_raw,gz_raw");
}

void loop()
{
    int16_t ax, ay, az;
    int16_t gx, gy, gz;

    imu.readAccelRaw(ax, ay, az);
    imu.readGyroRaw(gx, gy, gz);

    Serial.print(micros());
    Serial.print(",");
    Serial.print(ax);
    Serial.print(",");
    Serial.print(ay);
    Serial.print(",");
    Serial.print(az);
    Serial.print(",");
    Serial.print(gx);
    Serial.print(",");
    Serial.print(gy);
    Serial.print(",");
    Serial.println(gz);

    delay(10);
}
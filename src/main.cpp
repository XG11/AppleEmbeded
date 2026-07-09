#include <Arduino.h>
#include "lsm6dso32.h"

static constexpr uint8_t IMU_CS = 10;

LSM6DSO32 imu(IMU_CS);

void setup()
{
    Serial.begin(921600);
    delay(1000);

    Serial.println();
    Serial.println("LSM6DSO32 FIFO accel + gyro test");

    if (!imu.begin())
    {
        Serial.println("ERROR: IMU not found");
        while (1)
        {
            delay(1000);
        }
    }

    imu.configureFifoAccelGyro();

    Serial.print("CTRL1_XL = 0x");
    Serial.println(imu.readRegister(0x10), HEX);

    Serial.print("CTRL2_G = 0x");
    Serial.println(imu.readRegister(0x11), HEX);

    Serial.print("FIFO_CTRL3 = 0x");
    Serial.println(imu.readRegister(0x09), HEX);

    Serial.print("FIFO_CTRL4 = 0x");
    Serial.println(imu.readRegister(0x0A), HEX);

    Serial.println("timestamp_us,type,x_raw,y_raw,z_raw,fifo_remaining");
}

void loop()
{
    uint16_t count = imu.fifoCount();

    while (count > 0)
    {
        uint8_t tag;
        int16_t x, y, z;

        imu.readFifoSample(tag, x, y, z);

        Serial.print(micros());
        Serial.print(",");

        if (tag == 0x01)
        {
            Serial.print("gyro");
        }
        else if (tag == 0x02)
        {
            Serial.print("accel");
        }
        else
        {
            Serial.print("tag_");
            Serial.print(tag);
        }

        Serial.print(",");
        Serial.print(x);
        Serial.print(",");
        Serial.print(y);
        Serial.print(",");
        Serial.print(z);
        Serial.print(",");
        Serial.println(count);

        count--;
    }
}
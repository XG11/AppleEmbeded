#include <Arduino.h>
#include "lsm6dso32.h"

LSM6DSO32 imu(10);

void setup()
{
    Serial.begin(115200);

    while (!Serial)
        ;

    Serial.println();
    Serial.println("LSM6DSO32 Test");

    if (imu.begin())
    {
        Serial.println("IMU Found!");
    }
    else
    {
        Serial.println("WHO_AM_I failed");
    }
}

void loop()
{
    delay(1000);

    Serial.print("WHO_AM_I = 0x");
    Serial.println(imu.readRegister(0x0F), HEX);
}
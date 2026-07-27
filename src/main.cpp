#include <Arduino.h>

#include "lsm6dso32.h"


// ---------------------------------------------------------------------
// Hardware configuration
// ---------------------------------------------------------------------

static constexpr uint8_t IMU_CS = 10;

LSM6DSO32 imu(IMU_CS);


// ---------------------------------------------------------------------
// LSM6DSO32 FIFO tags
// ---------------------------------------------------------------------

static constexpr uint8_t FIFO_TAG_GYRO = 0x01;
static constexpr uint8_t FIFO_TAG_ACCEL = 0x02;


// ---------------------------------------------------------------------
// Pending paired sample
// ---------------------------------------------------------------------

// These variables are static/global so that an unmatched accelerometer
// or gyroscope sample is preserved between loop() iterations.

static int16_t accelX = 0;
static int16_t accelY = 0;
static int16_t accelZ = 0;

static int16_t gyroX = 0;
static int16_t gyroY = 0;
static int16_t gyroZ = 0;

static bool haveAccel = false;
static bool haveGyro = false;


// ---------------------------------------------------------------------
// Print one complete IMU sample
// ---------------------------------------------------------------------

void printImuSample(uint32_t timestampUs)
{
    Serial.print(timestampUs);
    Serial.print(',');

    Serial.print(accelX);
    Serial.print(',');
    Serial.print(accelY);
    Serial.print(',');
    Serial.print(accelZ);
    Serial.print(',');

    Serial.print(gyroX);
    Serial.print(',');
    Serial.print(gyroY);
    Serial.print(',');
    Serial.println(gyroZ);
}


// ---------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------

void setup()
{
    Serial.begin(2000000);
    delay(1000);

    Serial.println();
    Serial.println("LSM6DSO32 FIFO paired accel + gyro test");

    if (!imu.begin())
    {
        Serial.println("ERROR: IMU not found");

        while (true)
        {
            delay(1000);
        }
    }

    imu.configureFifoAccelGyro();

    // Print register values for startup diagnostics.
    Serial.print("CTRL1_XL = 0x");
    Serial.println(imu.readRegister(0x10), HEX);

    Serial.print("CTRL2_G = 0x");
    Serial.println(imu.readRegister(0x11), HEX);

    Serial.print("FIFO_CTRL3 = 0x");
    Serial.println(imu.readRegister(0x09), HEX);

    Serial.print("FIFO_CTRL4 = 0x");
    Serial.println(imu.readRegister(0x0A), HEX);

    // CSV header
    Serial.println(
        "timestamp_us,"
        "acc_x,acc_y,acc_z,"
        "gyro_x,gyro_y,gyro_z"
    );
}


// ---------------------------------------------------------------------
// Main loop
// ---------------------------------------------------------------------

void loop()
{
    uint16_t count = imu.fifoCount();

    while (count > 0)
    {
        uint8_t tag = 0;

        int16_t x = 0;
        int16_t y = 0;
        int16_t z = 0;

        if (!imu.readFifoSample(tag, x, y, z))
        {
            break;
        }

        if (tag == FIFO_TAG_ACCEL)
        {
            accelX = x;
            accelY = y;
            accelZ = z;

            haveAccel = true;
        }
        else if (tag == FIFO_TAG_GYRO)
        {
            gyroX = x;
            gyroY = y;
            gyroZ = z;

            haveGyro = true;
        }

        // Print only after both parts of the IMU sample are available.
        if (haveAccel && haveGyro)
        {
            const uint32_t timestampUs = micros();

            printImuSample(timestampUs);

            haveAccel = false;
            haveGyro = false;
        }

        count--;
    }
}
#include <Arduino.h>

#include "hx711_sensor.h"
#include "lsm6dso32.h"


// ---------------------------------------------------------------------
// Hardware pins
// ---------------------------------------------------------------------

static constexpr uint8_t IMU_CS = 10;

// Change these two pins if necessary.
static constexpr uint8_t HX711_DATA_PIN = 6;
static constexpr uint8_t HX711_CLOCK_PIN = 7;


// ---------------------------------------------------------------------
// Sensor objects
// ---------------------------------------------------------------------

LSM6DSO32 imu(IMU_CS);

HX711Sensor loadCell(
    HX711_DATA_PIN,
    HX711_CLOCK_PIN
);


// ---------------------------------------------------------------------
// LSM6DSO32 FIFO tags
// ---------------------------------------------------------------------

static constexpr uint8_t FIFO_TAG_GYRO = 0x01;
static constexpr uint8_t FIFO_TAG_ACCEL = 0x02;


// ---------------------------------------------------------------------
// Pending paired IMU sample
// ---------------------------------------------------------------------

static int16_t accelX = 0;
static int16_t accelY = 0;
static int16_t accelZ = 0;

static int16_t gyroX = 0;
static int16_t gyroY = 0;
static int16_t gyroZ = 0;

static bool haveAccel = false;
static bool haveGyro = false;


// ---------------------------------------------------------------------
// Serial output
// ---------------------------------------------------------------------

void printSensorRow(uint32_t timestampUs)
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
    Serial.print(gyroZ);
    Serial.print(',');

    // Before the first HX711 conversion is available, output zero.
    // After that, repeat the latest reading on each IMU row.
    if (loadCell.hasReading())
    {
        Serial.println(loadCell.latestRaw());
    }
    else
    {
        Serial.println(0);
    }
}


// ---------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------

void setup()
{
    Serial.begin(2000000);
    delay(1000);

    Serial.println();
    Serial.println("LSM6DSO32 + HX711 acquisition");

    // -----------------------------------------------------------------
    // IMU initialization
    // -----------------------------------------------------------------

    if (!imu.begin())
    {
        Serial.println("ERROR: IMU not found");

        while (true)
        {
            delay(1000);
        }
    }

    imu.configureFifoAccelGyro();

    Serial.print("CTRL1_XL = 0x");
    Serial.println(
        imu.readRegister(0x10),
        HEX
    );

    Serial.print("CTRL2_G = 0x");
    Serial.println(
        imu.readRegister(0x11),
        HEX
    );

    Serial.print("FIFO_CTRL3 = 0x");
    Serial.println(
        imu.readRegister(0x09),
        HEX
    );

    Serial.print("FIFO_CTRL4 = 0x");
    Serial.println(
        imu.readRegister(0x0A),
        HEX
    );

    // -----------------------------------------------------------------
    // HX711 initialization
    // -----------------------------------------------------------------

    loadCell.begin();

    Serial.print("HX711 data pin: ");
    Serial.println(HX711_DATA_PIN);

    Serial.print("HX711 clock pin: ");
    Serial.println(HX711_CLOCK_PIN);

    // -----------------------------------------------------------------
    // CSV header
    // -----------------------------------------------------------------

    Serial.println(
        "timestamp_us,"
        "acc_x,acc_y,acc_z,"
        "gyro_x,gyro_y,gyro_z,"
        "load_cell_raw"
    );
}


// ---------------------------------------------------------------------
// Main loop
// ---------------------------------------------------------------------

void loop()
{
    // Update the HX711 only when a conversion is ready.
    //
    // This does not wait for a new HX711 reading. The most recent value
    // remains available while the next conversion is taking place.
    loadCell.update();

    // Read the current number of entries in the IMU FIFO.
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

        // Print one row after both accelerometer and gyroscope samples
        // have been received.
        if (haveAccel && haveGyro)
        {
            const uint32_t timestampUs = micros();

            printSensorRow(timestampUs);

            haveAccel = false;
            haveGyro = false;
        }

        count--;
    }
}
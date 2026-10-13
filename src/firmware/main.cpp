#include <Arduino.h>
#include <SPI.h>

#include "lsm6dso32.h"

// ============================================================================
// Serial
// ============================================================================

static constexpr uint32_t SERIAL_BAUD = 2'000'000;

// ============================================================================
// Pins
// ============================================================================
//
// Teensy 4.0 SPI0:
// MOSI = 11
// MISO = 12
// SCK  = 13
//

static constexpr uint8_t IMU_CS_PIN = 10;

// Manual event-mark button.
//
// Wiring:
// Pin 22 ---- button ---- 3.3 V
//
// Internal pull-down keeps the pin LOW when the button is open.
static constexpr uint8_t MARK_BUTTON_PIN = 22;

static constexpr unsigned long MARK_BUTTON_DEBOUNCE_MS = 30;

// ============================================================================
// Sensors
// ============================================================================

LSM6DSO32 imu(IMU_CS_PIN);

// ============================================================================
// IMU FIFO tags
// ============================================================================

static constexpr uint8_t FIFO_TAG_GYRO = 0x01;
static constexpr uint8_t FIFO_TAG_ACCEL = 0x02;

// ============================================================================
// Latest IMU data
// ============================================================================

static int16_t latestAccelX = 0;
static int16_t latestAccelY = 0;
static int16_t latestAccelZ = 0;

static int16_t latestGyroX = 0;
static int16_t latestGyroY = 0;
static int16_t latestGyroZ = 0;

static bool haveNewAccel = false;
static bool haveNewGyro = false;

// ============================================================================
// Mark button state
// ============================================================================

static bool markButtonStableState = false;
static bool markButtonLastReading = false;
static unsigned long markButtonLastChangeMs = 0;

// ============================================================================
// Function declarations
// ============================================================================

void initializeImu();

void serviceImu();
void serviceMarkButton();

void printImuPacket(uint32_t timestampUs);

void printButtonPacket(uint32_t timestampUs);

// ============================================================================
// Setup
// ============================================================================

void setup()
{
    Serial.begin(SERIAL_BAUD);

    const uint32_t serialWaitStart =
        millis();

    while (
        !Serial &&
        millis() - serialWaitStart < 3000
    )
    {
        delay(1);
    }

    Serial.println();
    Serial.println("# Teensy booted");
    Serial.println("# IMU + button recorder");

    pinMode(
        IMU_CS_PIN,
        OUTPUT
    );

    digitalWrite(
        IMU_CS_PIN,
        HIGH
    );

    pinMode(
        MARK_BUTTON_PIN,
        INPUT_PULLDOWN
    );

    delay(10);

    SPI.begin();

    delay(20);

    initializeImu();

    Serial.println(
        "# IMU packet:"
        " I,timestamp_us,"
        "acc_x,acc_y,acc_z,"
        "gyro_x,gyro_y,gyro_z"
    );

    Serial.println(
        "# Button packet:"
        " B,timestamp_us"
    );

    Serial.println("# START");
}

// ============================================================================
// Main loop
// ============================================================================

void loop()
{
    serviceImu();

    serviceMarkButton();
}

// ============================================================================
// IMU initialization
// ============================================================================

void initializeImu()
{
    Serial.println(
        "# Initializing LSM6DSO32..."
    );

    if (!imu.begin())
    {
        Serial.println(
            "# ERROR: LSM6DSO32 not detected"
        );

        while (true)
        {
            delay(1000);
        }
    }

    imu.configureFifoAccelGyro();

    Serial.print(
        "# LSM6DSO32 CTRL1_XL = 0x"
    );

    Serial.println(
        imu.readRegister(0x10),
        HEX
    );

    Serial.print(
        "# LSM6DSO32 CTRL2_G = 0x"
    );

    Serial.println(
        imu.readRegister(0x11),
        HEX
    );

    Serial.print(
        "# LSM6DSO32 FIFO_CTRL3 = 0x"
    );

    Serial.println(
        imu.readRegister(0x09),
        HEX
    );

    Serial.print(
        "# LSM6DSO32 FIFO_CTRL4 = 0x"
    );

    Serial.println(
        imu.readRegister(0x0A),
        HEX
    );

    Serial.println(
        "# LSM6DSO32 initialized"
    );
}

// ============================================================================
// IMU service
// ============================================================================

void serviceImu()
{
    uint16_t fifoEntryCount =
        imu.fifoCount();

    if (fifoEntryCount > 1024)
    {
        fifoEntryCount = 1024;
    }

    while (fifoEntryCount > 0)
    {
        uint8_t tag = 0;

        int16_t x = 0;
        int16_t y = 0;
        int16_t z = 0;

        const bool success =
            imu.readFifoSample(
                tag,
                x,
                y,
                z
            );

        if (!success)
        {
            break;
        }

        if (tag == FIFO_TAG_ACCEL)
        {
            latestAccelX = x;
            latestAccelY = y;
            latestAccelZ = z;

            haveNewAccel = true;
        }
        else if (tag == FIFO_TAG_GYRO)
        {
            latestGyroX = x;
            latestGyroY = y;
            latestGyroZ = z;

            haveNewGyro = true;
        }

        if (
            haveNewAccel &&
            haveNewGyro
        )
        {
            printImuPacket(
                micros()
            );

            haveNewAccel = false;
            haveNewGyro = false;
        }

        fifoEntryCount--;
    }

    digitalWrite(
        IMU_CS_PIN,
        HIGH
    );
}

// ============================================================================
// Mark button service
// ============================================================================

void serviceMarkButton()
{
    const bool reading =
        digitalRead(MARK_BUTTON_PIN) == HIGH;

    if (reading != markButtonLastReading)
    {
        markButtonLastChangeMs = millis();
        markButtonLastReading = reading;
    }

    if (
        millis() - markButtonLastChangeMs
            >= MARK_BUTTON_DEBOUNCE_MS
        && reading != markButtonStableState
    )
    {
        markButtonStableState = reading;

        // Only report the press, not the release.
        if (markButtonStableState)
        {
            printButtonPacket(micros());
        }
    }
}

// ============================================================================
// Output
// ============================================================================

void printImuPacket(
    uint32_t timestampUs
)
{
    Serial.print("I,");
    Serial.print(timestampUs);
    Serial.print(',');

    Serial.print(latestAccelX);
    Serial.print(',');

    Serial.print(latestAccelY);
    Serial.print(',');

    Serial.print(latestAccelZ);
    Serial.print(',');

    Serial.print(latestGyroX);
    Serial.print(',');

    Serial.print(latestGyroY);
    Serial.print(',');

    Serial.println(latestGyroZ);
}

void printButtonPacket(
    uint32_t timestampUs
)
{
    Serial.print("B,");
    Serial.println(timestampUs);
}

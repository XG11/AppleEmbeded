#include <Arduino.h>
#include <SPI.h>

#include "ADS1220.h"
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

static constexpr uint8_t ADS1220_CS_PIN = 9;
static constexpr uint8_t ADS1220_DRDY_PIN = 8;

// ============================================================================
// ADS1220 settings
// ============================================================================

static SPISettings ads1220SpiSettings(
    1'000'000,
    MSBFIRST,
    SPI_MODE1
);

static constexpr uint8_t ADS1220_REG0 = 0x2E;
static constexpr uint8_t ADS1220_REG1 = 0xD4;

static constexpr int32_t ADS1220_OFFSET_SAMPLES = 100;

// ============================================================================
// Sensors
// ============================================================================

LSM6DSO32 imu(IMU_CS_PIN);

ADS1220 loadCell(
    ADS1220_CS_PIN,
    ADS1220_DRDY_PIN
);

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
// Function declarations
// ============================================================================

void initializeImu();
void initializeAds1220();

void serviceImu();
void serviceAds1220();

void printImuPacket(uint32_t timestampUs);

void printLoadCellPacket(
    uint32_t timestampUs,
    int32_t rawValue
);

void printAds1220Register(
    const char *name,
    uint8_t address,
    uint8_t expectedValue
);

// ============================================================================
// ADS1220 SPI wrappers
// ============================================================================

void ads1220BeginTransaction()
{
    // Make sure the IMU is not selected.
    digitalWrite(IMU_CS_PIN, HIGH);

    SPI.beginTransaction(ads1220SpiSettings);
}

void ads1220EndTransaction()
{
    digitalWrite(ADS1220_CS_PIN, HIGH);

    SPI.endTransaction();
}

void ads1220Reset()
{
    ads1220BeginTransaction();

    loadCell.reset();

    ads1220EndTransaction();
}

void ads1220WriteRegister(
    uint8_t address,
    uint8_t value
)
{
    ads1220BeginTransaction();

    loadCell.writeRegister(
        address,
        value
    );

    ads1220EndTransaction();
}

uint8_t ads1220ReadRegister(
    uint8_t address
)
{
    ads1220BeginTransaction();

    const uint8_t value =
        loadCell.readRegister(address);

    ads1220EndTransaction();

    return value;
}

void ads1220StartConversion()
{
    ads1220BeginTransaction();

    loadCell.startConversion();

    ads1220EndTransaction();
}

int32_t ads1220ReadData()
{
    ads1220BeginTransaction();

    const int32_t value =
        loadCell.readData();

    ads1220EndTransaction();

    return value;
}

void ads1220FindOffset(
    int32_t sampleCount
)
{
    ads1220BeginTransaction();

    loadCell.findADCOffset(sampleCount);

    ads1220EndTransaction();
}

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
    Serial.println("# IMU + ADS1220 recorder");

    pinMode(
        IMU_CS_PIN,
        OUTPUT
    );

    digitalWrite(
        IMU_CS_PIN,
        HIGH
    );

    pinMode(
        ADS1220_CS_PIN,
        OUTPUT
    );

    digitalWrite(
        ADS1220_CS_PIN,
        HIGH
    );

    pinMode(
        ADS1220_DRDY_PIN,
        INPUT
    );

    delay(10);

    SPI.begin();

    delay(20);

    initializeImu();

    initializeAds1220();

    Serial.println(
        "# IMU packet:"
        " I,timestamp_us,"
        "acc_x,acc_y,acc_z,"
        "gyro_x,gyro_y,gyro_z"
    );

    Serial.println(
        "# Load-cell packet:"
        " L,timestamp_us,load_cell_raw"
    );

    Serial.println("# START");
}

// ============================================================================
// Main loop
// ============================================================================

void loop()
{
    serviceAds1220();

    serviceImu();

    serviceAds1220();
}

// ============================================================================
// IMU initialization
// ============================================================================

void initializeImu()
{
    Serial.println(
        "# Initializing LSM6DSO32..."
    );

    digitalWrite(
        ADS1220_CS_PIN,
        HIGH
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
// ADS1220 initialization
// ============================================================================

void initializeAds1220()
{
    Serial.println(
        "# Initializing ADS1220..."
    );

    digitalWrite(
        IMU_CS_PIN,
        HIGH
    );

    digitalWrite(
        ADS1220_CS_PIN,
        HIGH
    );

    loadCell.begin();

    delay(10);

    ads1220Reset();

    delay(10);

    Serial.println(
        "# ADS1220 reset complete"
    );

    ads1220WriteRegister(
        0x00,
        ADS1220_REG0
    );

    ads1220WriteRegister(
        0x01,
        ADS1220_REG1
    );

    Serial.println(
        "# ADS1220 registers written"
    );

    printAds1220Register(
        "Reg0",
        0x00,
        ADS1220_REG0
    );

    printAds1220Register(
        "Reg1",
        0x01,
        ADS1220_REG1
    );

    ads1220StartConversion();

    Serial.println(
        "# ADS1220 conversion started"
    );

    delay(500);

    Serial.print(
        "# ADS1220 DRDY = "
    );

    Serial.println(
        digitalRead(ADS1220_DRDY_PIN)
    );

    Serial.println(
        "# Keep load cell unloaded"
    );

    ads1220FindOffset(
        ADS1220_OFFSET_SAMPLES
    );

    Serial.println(
        "# ADS1220 offset complete"
    );

    ads1220StartConversion();

    delay(500);

    Serial.println(
        "# ADS1220 initialized"
    );
}

// ============================================================================
// Register verification
// ============================================================================

void printAds1220Register(
    const char *name,
    uint8_t address,
    uint8_t expectedValue
)
{
    const uint8_t actualValue =
        ads1220ReadRegister(address);

    Serial.print("# ADS1220 ");
    Serial.print(name);
    Serial.print(" readback = 0x");

    if (actualValue < 0x10)
    {
        Serial.print('0');
    }

    Serial.print(
        actualValue,
        HEX
    );

    Serial.print(", expected = 0x");

    if (expectedValue < 0x10)
    {
        Serial.print('0');
    }

    Serial.print(
        expectedValue,
        HEX
    );

    if (actualValue == expectedValue)
    {
        Serial.println(" OK");
    }
    else
    {
        Serial.println(" MISMATCH");
    }
}

// ============================================================================
// ADS1220 service
// ============================================================================

void serviceAds1220()
{
    // DRDY is active LOW.
    if (
        digitalRead(ADS1220_DRDY_PIN)
        != LOW
    )
    {
        return;
    }

    delayMicroseconds(10);

    const uint32_t timestampUs =
        micros();

    const int32_t rawValue =
        ads1220ReadData();

    printLoadCellPacket(
        timestampUs,
        rawValue
    );
}

// ============================================================================
// IMU service
// ============================================================================

void serviceImu()
{
    // Make sure ADS1220 does not drive MISO.
    digitalWrite(
        ADS1220_CS_PIN,
        HIGH
    );

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

        digitalWrite(
            ADS1220_CS_PIN,
            HIGH
        );

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

void printLoadCellPacket(
    uint32_t timestampUs,
    int32_t rawValue
)
{
    Serial.print("L,");
    Serial.print(timestampUs);
    Serial.print(',');

    Serial.println(rawValue);
}
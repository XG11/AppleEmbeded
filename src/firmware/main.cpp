#include <Arduino.h>
#include <SPI.h>

#include "ADS1220.h"
#include "lsm6dso32.h"


// =====================================================================
// Hardware pins
// =====================================================================

static constexpr uint8_t IMU_CS_PIN = 10;

static constexpr uint8_t ADS1220_CS_PIN = 9;
static constexpr uint8_t ADS1220_DRDY_PIN = 8;


// =====================================================================
// Serial configuration
// =====================================================================

static constexpr uint32_t SERIAL_BAUD = 2000000;


// =====================================================================
// Sensor objects
// =====================================================================

LSM6DSO32 imu(IMU_CS_PIN);

ADS1220 loadCell(
    ADS1220_CS_PIN,
    ADS1220_DRDY_PIN
);


// =====================================================================
// ADS1220 configuration
// =====================================================================

// Register 0:
// MUX  = 0010: AIN0 positive, AIN1 negative
// GAIN = 111 : gain 128
// PGA_BYPASS = 0: PGA enabled
//
// Binary: 0010 1110 = 0x2E
static constexpr uint8_t ADS1220_REG0 = 0x0E;

// Register 1:
// DR   = 111: highest data-rate selection
// MODE = 1  : turbo mode
// CM   = 1  : continuous conversion mode
// TS   = 0  : temperature sensor disabled
// BCS  = 0  : burnout current sources disabled
//
// Binary: 1111 1000 = 0xF8
//
// With DR=111 and turbo mode enabled, the target rate is 2000 SPS.
static constexpr uint8_t ADS1220_REG1 = 0xF8;

// Register 2:
// VREF = 00: internal 2.048 V reference
// 50/60 rejection disabled
// low-side switch disabled
// IDAC disabled
static constexpr uint8_t ADS1220_REG2 = 0x00;

// Register 3:
// IDAC routing disabled
// DRDY pin used only as DRDY output
static constexpr uint8_t ADS1220_REG3 = 0x00;


// =====================================================================
// LSM6DSO32 FIFO tags
// =====================================================================

static constexpr uint8_t FIFO_TAG_GYRO = 0x01;
static constexpr uint8_t FIFO_TAG_ACCEL = 0x02;


// =====================================================================
// Pending paired IMU sample
// =====================================================================

static int16_t accelX = 0;
static int16_t accelY = 0;
static int16_t accelZ = 0;

static int16_t gyroX = 0;
static int16_t gyroY = 0;
static int16_t gyroZ = 0;

static bool haveAccel = false;
static bool haveGyro = false;


// =====================================================================
// ADS1220 sampling statistics
// =====================================================================

static uint32_t adsSampleCount = 0;
static uint32_t adsWindowStartUs = 0;
static float adsMeasuredSps = 0.0f;

static constexpr uint32_t SPS_WINDOW_US = 1000000;


// =====================================================================
// Serial output
// =====================================================================

void printImuRow(uint32_t timestampUs)
{
    Serial.print("I,");
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


void printLoadCellRow(
    uint32_t timestampUs,
    int32_t rawValue
)
{
    Serial.print("L,");
    Serial.print(timestampUs);
    Serial.print(',');
    Serial.println(rawValue);
}


void updateAdsSps(uint32_t currentTimeUs)
{
    const uint32_t elapsedUs =
        currentTimeUs - adsWindowStartUs;

    if (elapsedUs < SPS_WINDOW_US)
    {
        return;
    }

    adsMeasuredSps =
        static_cast<float>(adsSampleCount) *
        1000000.0f /
        static_cast<float>(elapsedUs);

    Serial.print("# ADS1220 SPS: ");
    Serial.println(adsMeasuredSps, 2);

    adsSampleCount = 0;
    adsWindowStartUs = currentTimeUs;
}


// =====================================================================
// ADS1220 initialization
// =====================================================================

bool initializeAds1220()
{
    loadCell.begin();

    loadCell.writeRegister(
        0x00,
        ADS1220_REG0
    );

    loadCell.writeRegister(
        0x01,
        ADS1220_REG1
    );

    loadCell.writeRegister(
        0x02,
        ADS1220_REG2
    );

    loadCell.writeRegister(
        0x03,
        ADS1220_REG3
    );

    const uint8_t reg0 =
        loadCell.readRegister(0x00);

    const uint8_t reg1 =
        loadCell.readRegister(0x01);

    const uint8_t reg2 =
        loadCell.readRegister(0x02);

    const uint8_t reg3 =
        loadCell.readRegister(0x03);

    Serial.print("# ADS1220 REG0 = 0x");
    Serial.println(reg0, HEX);

    Serial.print("# ADS1220 REG1 = 0x");
    Serial.println(reg1, HEX);

    Serial.print("# ADS1220 REG2 = 0x");
    Serial.println(reg2, HEX);

    Serial.print("# ADS1220 REG3 = 0x");
    Serial.println(reg3, HEX);

    if (
        reg0 != ADS1220_REG0 ||
        reg1 != ADS1220_REG1 ||
        reg2 != ADS1220_REG2 ||
        reg3 != ADS1220_REG3
    )
    {
        Serial.println(
            "# ERROR: ADS1220 register readback mismatch"
        );

        return false;
    }

    loadCell.startConversion();

    return true;
}


// =====================================================================
// Setup
// =====================================================================

void setup()
{
    Serial.begin(SERIAL_BAUD);
    delay(1000);

    Serial.println();
    Serial.println(
        "# LSM6DSO32 + ADS1220 acquisition"
    );

    // Ensure both devices are deselected before initializing SPI.
    pinMode(IMU_CS_PIN, OUTPUT);
    digitalWrite(IMU_CS_PIN, HIGH);

    pinMode(ADS1220_CS_PIN, OUTPUT);
    digitalWrite(ADS1220_CS_PIN, HIGH);

    SPI.begin();

    // -----------------------------------------------------------------
    // IMU initialization
    // -----------------------------------------------------------------

    if (!imu.begin())
    {
        Serial.println("# ERROR: IMU not found");

        while (true)
        {
            delay(1000);
        }
    }

    imu.configureFifoAccelGyro();

    Serial.print("# CTRL1_XL = 0x");
    Serial.println(
        imu.readRegister(0x10),
        HEX
    );

    Serial.print("# CTRL2_G = 0x");
    Serial.println(
        imu.readRegister(0x11),
        HEX
    );

    Serial.print("# FIFO_CTRL3 = 0x");
    Serial.println(
        imu.readRegister(0x09),
        HEX
    );

    Serial.print("# FIFO_CTRL4 = 0x");
    Serial.println(
        imu.readRegister(0x0A),
        HEX
    );

    // -----------------------------------------------------------------
    // ADS1220 initialization
    // -----------------------------------------------------------------

    if (!initializeAds1220())
    {
        while (true)
        {
            delay(1000);
        }
    }

    adsWindowStartUs = micros();

    // Packet description.
    Serial.println(
        "# I,timestamp_us,"
        "acc_x,acc_y,acc_z,"
        "gyro_x,gyro_y,gyro_z"
    );

    Serial.println(
        "# L,timestamp_us,load_cell_raw"
    );
}


// =====================================================================
// Main loop
// =====================================================================

void loop()
{
    // -----------------------------------------------------------------
    // Read all available ADS1220 conversions
    // -----------------------------------------------------------------

    if (loadCell.dataReady())
    {
        const uint32_t timestampUs = micros();
        const int32_t rawValue =
            loadCell.readData();

        printLoadCellRow(
            timestampUs,
            rawValue
        );

        adsSampleCount++;
    }

    updateAdsSps(micros());

    // -----------------------------------------------------------------
    // Drain IMU FIFO
    // -----------------------------------------------------------------

    uint16_t count = imu.fifoCount();

    while (count > 0)
    {
        uint8_t tag = 0;

        int16_t x = 0;
        int16_t y = 0;
        int16_t z = 0;

        if (!imu.readFifoSample(
            tag,
            x,
            y,
            z
        ))
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

        if (haveAccel && haveGyro)
        {
            printImuRow(micros());

            haveAccel = false;
            haveGyro = false;
        }

        count--;
    }
}
#include <Arduino.h>

// ============================================================
// PIN CONFIGURATION
// ============================================================

// LEDs
const int GREEN_LED_PIN  = 10;
const int YELLOW_LED_PIN = 11;
const int RED_LED_PIN    = 12;

// Push button
// Wiring:
// Pin 6 ---- button ---- 3.3 V
const int BUTTON_PIN = 6;

// Connector sensing pins
const int CONNECTOR_PIN_1 = 14;
const int CONNECTOR_PIN_2 = 15;
const int CONNECTOR_PIN_3 = 16;
const int CONNECTOR_PIN_4 = 17;


// ============================================================
// TIMING
// ============================================================

// Hold button this long to start
const unsigned long LONG_PRESS_MS = 1500;

// Entire trial
const unsigned long SESSION_DURATION_MS = 65000;

// Connector must remain fully connected for this long
// before the OLD / confirmed success label is accepted.
const unsigned long SUCCESS_CONFIRM_MS = 200;

// LED blink timing
const unsigned long IDLE_GREEN_BLINK_MS = 500;
const unsigned long RECORDING_YELLOW_BLINK_MS = 500;

// Recording LED phase boundaries
const unsigned long GREEN_SOLID_END_MS = 55000;
const unsigned long YELLOW_BLINK_END_MS = 60000;


// ============================================================
// FSM
// ============================================================

enum State
{
    IDLE,
    RECORDING
};

State state = IDLE;


// ============================================================
// SESSION VARIABLES
// ============================================================

unsigned long sessionStartMs = 0;

// ------------------------------------------------------------
// Label 1: FIRST FULL CONNECT
// ------------------------------------------------------------
// Latches the first instant all four connector pins are
// simultaneously connected. No 200 ms confirmation required.

bool firstFullConnectDetected = false;
unsigned long firstFullConnectTimeMs = 0;

// ------------------------------------------------------------
// Label 2: OLD / CONFIRMED SUCCESS
// ------------------------------------------------------------
// Same behavior as before: all four pins must stay connected
// continuously for SUCCESS_CONFIRM_MS.

bool successDetected = false;
unsigned long successTimeMs = 0;

bool connectionTimerActive = false;
unsigned long allConnectedStartMs = 0;


// ============================================================
// LAST TRIAL RESULT
// ============================================================

// These remain available after the trial finishes so Python can
// query them after record_multimodal.py releases the serial port.

bool lastTrialValid = false;
bool lastTrialSuccess = false;

bool lastFirstFullConnectDetected = false;
unsigned long lastFirstFullConnectTimeMs = 0;

unsigned long lastSuccessTimeMs = 0;


// ============================================================
// BUTTON VARIABLES
// ============================================================

bool previousButtonState = false;
bool longPressTriggered = false;
unsigned long buttonPressStartMs = 0;


// ============================================================
// CONNECTOR STATE LOGGING
// ============================================================

int lastP1 = -1;
int lastP2 = -1;
int lastP3 = -1;
int lastP4 = -1;


// ============================================================
// LED FUNCTIONS
// ============================================================

void LEDsOff()
{
    digitalWrite(GREEN_LED_PIN, LOW);
    digitalWrite(YELLOW_LED_PIN, LOW);
    digitalWrite(RED_LED_PIN, LOW);
}


void setLEDs(bool green, bool yellow, bool red)
{
    digitalWrite(GREEN_LED_PIN, green ? HIGH : LOW);
    digitalWrite(YELLOW_LED_PIN, yellow ? HIGH : LOW);
    digitalWrite(RED_LED_PIN, red ? HIGH : LOW);
}


void updateIdleLEDs()
{
    // Green LED flashes while waiting for a trial.
    // Yellow and red stay off.
    bool greenOn =
        ((millis() / IDLE_GREEN_BLINK_MS) % 2) == 0;

    setLEDs(greenOn, false, false);
}


void updateRecordingLEDs()
{
    unsigned long elapsed =
        millis() - sessionStartMs;

    // 0 - 55 s: green solid
    if (elapsed < GREEN_SOLID_END_MS)
    {
        setLEDs(true, false, false);
    }

    // 55 - 60 s: yellow flashing
    else if (elapsed < YELLOW_BLINK_END_MS)
    {
        bool yellowOn =
            ((elapsed / RECORDING_YELLOW_BLINK_MS) % 2) == 0;

        setLEDs(false, yellowOn, false);
    }

    // 60 - 65 s: red solid
    else
    {
        setLEDs(false, false, true);
    }
}


// ============================================================
// CONNECTOR FUNCTIONS
// ============================================================

// Connector wiring:
//
// OPEN:
//      approximately 3 V
//
// CONNECTED:
//      0 V
//
// Therefore:
//
// LOW  = connected
// HIGH = open
//
// External circuit already drives the voltage, so use INPUT.

const int CONNECTED_THRESHOLD = 220;

bool connectorPinConnected(int pin)
{
    int value = analogRead(pin);
    return value < CONNECTED_THRESHOLD;
}


bool allConnectorPinsConnected()
{
    return (
        connectorPinConnected(CONNECTOR_PIN_1) &&
        connectorPinConnected(CONNECTOR_PIN_2) &&
        connectorPinConnected(CONNECTOR_PIN_3) &&
        connectorPinConnected(CONNECTOR_PIN_4)
    );
}


// ============================================================
// CONNECTOR LOGGING
// ============================================================

void updateConnectorLogging()
{
    int p1 = connectorPinConnected(CONNECTOR_PIN_1);
    int p2 = connectorPinConnected(CONNECTOR_PIN_2);
    int p3 = connectorPinConnected(CONNECTOR_PIN_3);
    int p4 = connectorPinConnected(CONNECTOR_PIN_4);

    // Only send a serial line when connector state changes.
    if (
        p1 != lastP1 ||
        p2 != lastP2 ||
        p3 != lastP3 ||
        p4 != lastP4
    )
    {
        unsigned long elapsed =
            millis() - sessionStartMs;

        Serial.print("C,");
        Serial.print(micros());
        Serial.print(",");
        Serial.print(elapsed);
        Serial.print(",");
        Serial.print(p1);
        Serial.print(",");
        Serial.print(p2);
        Serial.print(",");
        Serial.print(p3);
        Serial.print(",");
        Serial.println(p4);

        lastP1 = p1;
        lastP2 = p2;
        lastP3 = p3;
        lastP4 = p4;
    }
}


// ============================================================
// SUCCESS DETECTION
// ============================================================

void updateSuccessDetection()
{
    bool allConnected =
        allConnectorPinsConnected();


    // ========================================================
    // LABEL 1: FIRST FULL CONNECT
    // ========================================================

    // Latch the first instant all four pins are simultaneously
    // connected. This does NOT require the 200 ms confirmation.
    if (allConnected && !firstFullConnectDetected)
    {
        firstFullConnectDetected = true;

        firstFullConnectTimeMs =
            millis() - sessionStartMs;

        Serial.print("FIRST_FULL_CONNECT,");
        Serial.println(firstFullConnectTimeMs);
    }


    // ========================================================
    // LABEL 2: OLD / CONFIRMED SUCCESS
    // ========================================================

    // Once confirmed success has been detected, it remains
    // latched for the rest of the trial.
    if (successDetected)
    {
        return;
    }


    if (allConnected)
    {
        // Start continuous-connection confirmation timer.
        if (!connectionTimerActive)
        {
            connectionTimerActive = true;

            allConnectedStartMs =
                millis();

            Serial.print("ALL_CONTACT_START,");
            Serial.println(
                millis() - sessionStartMs
            );
        }


        unsigned long connectedTime =
            millis() - allConnectedStartMs;


        // Original behavior: require stable connection for
        // SUCCESS_CONFIRM_MS before calling it success.
        if (connectedTime >= SUCCESS_CONFIRM_MS)
        {
            successDetected = true;

            successTimeMs =
                millis() - sessionStartMs;

            Serial.print("SUCCESS_DETECTED,");
            Serial.println(successTimeMs);
        }
    }


    // ========================================================
    // CONNECTION BROKE BEFORE CONFIRMED SUCCESS
    // ========================================================

    else
    {
        if (connectionTimerActive)
        {
            Serial.print("ALL_CONTACT_LOST,");
            Serial.println(
                millis() - sessionStartMs
            );
        }

        connectionTimerActive = false;
    }
}


// ============================================================
// START SESSION
// ============================================================

void startSession()
{
    state = RECORDING;

    sessionStartMs = millis();

    // Reset first-full-connect label.
    firstFullConnectDetected = false;
    firstFullConnectTimeMs = 0;

    // Reset original confirmed-success label.
    successDetected = false;
    successTimeMs = 0;

    connectionTimerActive = false;
    allConnectedStartMs = 0;

    lastP1 = -1;
    lastP2 = -1;
    lastP3 = -1;
    lastP4 = -1;

    // Invalidate previous result until this trial finishes.
    lastTrialValid = false;

    // Recording indication starts immediately.
    setLEDs(false, true, false);

    Serial.println();
    Serial.println("SESSION_START");
}


// ============================================================
// END SESSION
// ============================================================

void endSession()
{
    unsigned long duration =
        millis() - sessionStartMs;


    // Save both labels so Python can query them later.
    lastTrialValid = true;

    lastFirstFullConnectDetected =
        firstFullConnectDetected;

    lastFirstFullConnectTimeMs =
        firstFullConnectTimeMs;

    lastTrialSuccess =
        successDetected;

    lastSuccessTimeMs =
        successTimeMs;


    // --------------------------------------------------------
    // Output result
    // --------------------------------------------------------

    Serial.print("SESSION_END,");

    if (successDetected)
    {
        Serial.print("SUCCESS,");
    }
    else
    {
        Serial.print("FAIL,");
    }

    Serial.print(duration);
    Serial.print(",");

    if (firstFullConnectDetected)
    {
        Serial.print(firstFullConnectTimeMs);
    }
    else
    {
        Serial.print(-1);
    }

    Serial.print(",");

    if (successDetected)
    {
        Serial.println(successTimeMs);
    }
    else
    {
        Serial.println(-1);
    }


    state = IDLE;

    // Let the idle LED routine take over immediately.
    updateIdleLEDs();

    Serial.println("READY");
}


// ============================================================
// BUTTON HANDLING
// ============================================================

void updateButton()
{
    // Button connected to 3.3 V:
    // LOW  = released
    // HIGH = pressed

    bool pressed =
        digitalRead(BUTTON_PIN) == HIGH;


    // ========================================================
    // BUTTON JUST PRESSED
    // ========================================================

    if (pressed && !previousButtonState)
    {
        buttonPressStartMs =
            millis();

        longPressTriggered = false;
    }


    // ========================================================
    // BUTTON BEING HELD
    // ========================================================

    if (pressed)
    {
        unsigned long heldTime =
            millis() - buttonPressStartMs;


        if (
            heldTime >= LONG_PRESS_MS &&
            !longPressTriggered
        )
        {
            longPressTriggered = true;


            if (state == IDLE)
            {
                startSession();
            }
        }
    }


    // ========================================================
    // BUTTON RELEASED
    // ========================================================

    if (!pressed && previousButtonState)
    {
        longPressTriggered = false;
    }


    previousButtonState =
        pressed;
}


// ============================================================
// SERIAL COMMAND HANDLING
// ============================================================

void handleSerialCommands()
{
    if (!Serial.available())
    {
        return;
    }


    String command =
        Serial.readStringUntil('\n');

    command.trim();


    // --------------------------------------------------------
    // Python asks for result of most recent trial
    //
    // Format:
    // LAST_RESULT,SUCCESS|FAIL,first_full_connect_ms,
    //             confirmed_success_ms
    // --------------------------------------------------------

    if (command == "GET_LAST_RESULT")
    {
        if (!lastTrialValid)
        {
            Serial.println(
                "LAST_RESULT,NONE,-1,-1"
            );

            return;
        }


        Serial.print("LAST_RESULT,");

        if (lastTrialSuccess)
        {
            Serial.print("SUCCESS,");
        }
        else
        {
            Serial.print("FAIL,");
        }


        if (lastFirstFullConnectDetected)
        {
            Serial.print(lastFirstFullConnectTimeMs);
        }
        else
        {
            Serial.print(-1);
        }

        Serial.print(",");


        if (lastTrialSuccess)
        {
            Serial.println(lastSuccessTimeMs);
        }
        else
        {
            Serial.println(-1);
        }
    }


    // --------------------------------------------------------
    // Useful for checking controller status
    // --------------------------------------------------------

    else if (command == "STATUS")
    {
        if (state == IDLE)
        {
            Serial.println(
                "STATUS,IDLE"
            );
        }

        else
        {
            Serial.print(
                "STATUS,RECORDING,"
            );

            Serial.println(
                millis() - sessionStartMs
            );
        }
    }
}


// ============================================================
// SETUP
// ============================================================

void setup()
{
    Serial.begin(2000000);


    // ========================================================
    // LED OUTPUTS
    // ========================================================

    pinMode(
        GREEN_LED_PIN,
        OUTPUT
    );

    pinMode(
        YELLOW_LED_PIN,
        OUTPUT
    );

    pinMode(
        RED_LED_PIN,
        OUTPUT
    );

    LEDsOff();


    // ========================================================
    // BUTTON
    // ========================================================

    // 3.3 V
    //   |
    // button
    //   |
    // pin 6
    //
    // Internal pull-down makes released state LOW.

    pinMode(
        BUTTON_PIN,
        INPUT_PULLDOWN
    );


    // ========================================================
    // CONNECTOR INPUTS
    // ========================================================

    // External circuit:
    // Open       ~= 3 V
    // Connected   = 0 V

    pinMode(
        CONNECTOR_PIN_1,
        INPUT
    );

    pinMode(
        CONNECTOR_PIN_2,
        INPUT
    );

    pinMode(
        CONNECTOR_PIN_3,
        INPUT
    );

    pinMode(
        CONNECTOR_PIN_4,
        INPUT
    );


    delay(500);


    Serial.println();
    Serial.println(
        "=============================="
    );

    Serial.println(
        "DATA COLLECTION CONTROLLER"
    );

    Serial.println(
        "=============================="
    );

    Serial.println(
        "Hold button 1.5 s to start"
    );

    Serial.println(
        "Trial duration: 65 s"
    );

    Serial.println(
        "Green flashing = idle"
    );

    Serial.println(
        "Yellow blinking = recording"
    );

    Serial.println(
        "READY"
    );
}


// ============================================================
// LOOP
// ============================================================

void loop()
{
    // Always handle button.
    updateButton();

    // Handle commands from Python.
    handleSerialCommands();


    // ========================================================
    // IDLE
    // ========================================================

    if (state == IDLE)
    {
        updateIdleLEDs();
        return;
    }


    // ========================================================
    // RECORDING
    // ========================================================

    if (state == RECORDING)
    {
        unsigned long elapsed =
            millis() - sessionStartMs;

        updateRecordingLEDs();
        updateConnectorLogging();
        updateSuccessDetection();


        // ----------------------------------------------------
        // HARD STOP AT 65 SECONDS
        // ----------------------------------------------------

        if (elapsed >= SESSION_DURATION_MS)
        {
            endSession();
        }
    }
}
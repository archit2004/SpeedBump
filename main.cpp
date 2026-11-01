#include <Arduino.h>
#include <WiFi.h>
#include <ESP32Servo.h>
#include "Adafruit_MQTT.h"
#include "Adafruit_MQTT_Client.h"
#include "secrets.h"
// ---------------- WIFI + ADAFRUIT CONFIG ----------------


// ---------------- PIN DEFINITIONS ----------------
#define IR1_PIN 4
#define IR2_PIN 5
#define SERVO_PIN 13

// ---------------- CONFIG ----------------
const float distanceMeters = 0.15;

// ---------------- OBJECTS ----------------
Servo myGate;

WiFiClient client;
Adafruit_MQTT_Client mqtt(&client, AIO_SERVER, AIO_SERVERPORT, AIO_USERNAME, AIO_KEY);

// Feeds
Adafruit_MQTT_Publish speedFeed =
Adafruit_MQTT_Publish(&mqtt, AIO_USERNAME "/feeds/speed");

Adafruit_MQTT_Subscribe angleFeed =
Adafruit_MQTT_Subscribe(&mqtt, AIO_USERNAME "/feeds/BumpAngle");

// ---------------- GLOBAL VARIABLES ----------------
volatile unsigned long startTime = 0;
volatile unsigned long endTime = 0;

volatile bool ir1_tripped = false;
volatile bool ir2_tripped = false;

volatile unsigned long lastTrigger1 = 0;
volatile unsigned long lastTrigger2 = 0;

int currentAngle = 0;

// ---------------- INTERRUPTS ----------------
void IRAM_ATTR sensor1Triggered() {

  unsigned long now = micros();

  if (!ir1_tripped && (now - lastTrigger1) > 50000) {
    startTime = now;
    ir1_tripped = true;
    lastTrigger1 = now;
  }
}

void IRAM_ATTR sensor2Triggered() {

  unsigned long now = micros();

  if (ir1_tripped && !ir2_tripped && (now - lastTrigger2) > 50000) {
    endTime = now;
    ir2_tripped = true;
    lastTrigger2 = now;
  }
}

// ---------------- MQTT CONNECTION ----------------
void MQTT_connect() {

  if (mqtt.connected())
    return;

  Serial.print("Connecting to MQTT... ");

  int8_t ret;
  uint8_t retries = 3;

  while ((ret = mqtt.connect()) != 0) {

    Serial.println(mqtt.connectErrorString(ret));
    Serial.println("Retrying in 5 seconds...");

    mqtt.disconnect();
    delay(5000);

    retries--;

    if (retries == 0)
      return;
  }

  mqtt.subscribe(&angleFeed);

  Serial.println("MQTT Connected!");
}

// ---------------- SETUP ----------------
void setup() {

  Serial.begin(115200);
  delay(1000);

  Serial.println("\n--- SYSTEM BOOTING ---");

  // IR Sensors
  pinMode(IR1_PIN, INPUT_PULLUP);
  pinMode(IR2_PIN, INPUT_PULLUP);

  attachInterrupt(digitalPinToInterrupt(IR1_PIN), sensor1Triggered, FALLING);
  attachInterrupt(digitalPinToInterrupt(IR2_PIN), sensor2Triggered, FALLING);

  // Servo setup
  ESP32PWM::allocateTimer(0);
  myGate.setPeriodHertz(50);
  myGate.attach(SERVO_PIN, 500, 2400);
  myGate.write(currentAngle);

  // WiFi setup
  Serial.print("Connecting to WiFi");

  WiFi.setAutoReconnect(true);
  WiFi.persistent(true);

  WiFi.begin(WLAN_SSID, WLAN_PASS);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("\nWiFi Connected!");

  mqtt.subscribe(&angleFeed);
}

// ---------------- LOOP ----------------
void loop() {

  MQTT_connect();

  // process incoming packets
  mqtt.processPackets(10);

  // ---------- READ MQTT FEED ----------
  Adafruit_MQTT_Subscribe *subscription;

  while ((subscription = mqtt.readSubscription(200))) {

    if (subscription == &angleFeed) {

      char* value = (char *)angleFeed.lastread;

      Serial.print("Incoming BumpAngle: ");
      Serial.println(value);

      int newAngle = atoi(value);
      newAngle = constrain(newAngle, 0, 180);

      if (newAngle != currentAngle) {

        Serial.print("Moving Servo to: ");
        Serial.println(newAngle);

        int step = (newAngle > currentAngle) ? 1 : -1;

        for (int pos = currentAngle; pos != newAngle; pos += step) {
          myGate.write(pos);
          delay(10);
        }

        myGate.write(newAngle);
        currentAngle = newAngle;
      }
    }
  }

  // ---------- SPEED CALCULATION ----------
  if (ir1_tripped && ir2_tripped) {

    unsigned long elapsedMicros = endTime - startTime;

    float elapsedSeconds = elapsedMicros / 1000000.0;

    if (elapsedSeconds > 0) {

      float speedMps = distanceMeters / elapsedSeconds;
      float speedKph = speedMps * 3.6;

      Serial.print("Vehicle Speed: ");
      Serial.print(speedKph);
      Serial.println(" km/h");

      if (speedFeed.publish(speedKph)) {
        Serial.println("Speed sent to Adafruit IO.");
      } else {
        Serial.println("Failed to send speed.");
      }
    }

    ir1_tripped = false;
    ir2_tripped = false;

    Serial.println("Ready for next vehicle...\n");
  }
}
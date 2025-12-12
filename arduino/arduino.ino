#include <WiFiS3.h>
#include <Wire.h>
#include "Multichannel_Gas_GMXXX.h"

// WiFi and Server Configuration
const char* ssid     = "semogaberkah";
const char* pass     = "0999999999";
const char* RUST_IP  = "10.60.145.140";   // Adjust to local ip
const int   RUST_PORT = 8081;             // Adjust to main.rs
WiFiClient client;

// Sensor Configuration
GAS_GMXXX<TwoWire> gas;
#define MICS_PIN    A1
#define RLOAD       820.0
#define VCC         5.0
float R0_mics = 100000.0;

// Motor Pins
// Fan (Motor A)
const int PWM_KIPAS   = 6;
const int DIR_KIPAS_1 = 9;
const int DIR_KIPAS_2 = 10;
// Pump (Motor B)
const int PWM_POMPA   = 5;
const int DIR_POMPA_1 = 7;
const int DIR_POMPA_2 = 8;

// FSM and Timing
enum State { IDLE, PRE_COND, RAMP_UP, HOLD, PURGE, RECOVERY, DONE };
State currentState = IDLE;
unsigned long stateTime = 0;
int currentLevel = 0;
const int speeds[5] = {51, 102, 153, 204, 255}; // Level 1-5
bool samplingActive = false;

// Flags for log
bool printFanLead = false;
bool printBoth = false;

// Duration (ms)
const unsigned long T_FAN_LEAD = 5000;  
const unsigned long T_PRECOND  = 5000;   
const unsigned long T_RAMP     = 3000;   
const unsigned long T_HOLD     = 20000;   
const unsigned long T_PURGE    = 40000;   
const unsigned long T_RECOVERY = 5000;   

unsigned long lastSend = 0;
unsigned long lastReconnect = 0;

// Motor Control
void kipas(int speed, bool buang = false) {
  digitalWrite(DIR_KIPAS_1, buang ? LOW : HIGH);
  digitalWrite(DIR_KIPAS_2, buang ? HIGH : LOW);
  analogWrite(PWM_KIPAS, speed);
}

void pompa(int speed, bool buang = false) {
  digitalWrite(DIR_POMPA_1, buang ? LOW : HIGH);
  digitalWrite(DIR_POMPA_2, buang ? HIGH : LOW);
  analogWrite(PWM_POMPA, speed);
}

void stopAll() { 
  analogWrite(PWM_KIPAS, 0); 
  analogWrite(PWM_POMPA, 0); 
}

void rampKipas(int target) {
  static int cur = 0;
  if (cur < target) cur += 15;
  else if (cur > target) cur -= 15;
  cur = constrain(cur, 0, 255);
  kipas(cur);
}

// Sensor Calculation
float calculateRs() {
  int raw = analogRead(MICS_PIN);
  if (raw < 10) return -1;
  float Vout = raw * (VCC / 1023.0);
  if (Vout >= VCC || Vout <= 0) return -1;
  return RLOAD * ((VCC - Vout) / Vout);
}

float ppmFromRatio(float ratio, String gasType) {
  if (ratio <= 0 || R0_mics == 0) return -1;
  float ppm = 0.0;
  if (gasType == "CO")      ppm = pow(10, (log10(ratio) - 0.35) / -0.85);
  else if (gasType == "C2H5OH") ppm = pow(10, (log10(ratio) - 0.15) / -0.65);
  else if (gasType == "VOC")    ppm = pow(10, (log10(ratio) + 0.1) / -0.75);
  return (ppm >= 0 && ppm <= 5000) ? ppm : -1;
}

// Connection
void ensureConnected() {
  if (client.connected()) return;

  if (millis() - lastReconnect > 5000) {
    lastReconnect = millis();
    Serial.print("🔌 Connecting to Data Server "); Serial.print(RUST_IP); Serial.print(":"); Serial.println(RUST_PORT);
    
    if (client.connect(RUST_IP, RUST_PORT)) {
      Serial.println("✅ Connected to Rust Backend!");
    } else {
      Serial.println("❌ Connection failed (Data will not be sent)");
    }
  }
}

// Setup
void setup() {
  Serial.begin(9600); 
  
  pinMode(DIR_KIPAS_1, OUTPUT); pinMode(DIR_KIPAS_2, OUTPUT); pinMode(PWM_KIPAS, OUTPUT);
  pinMode(DIR_POMPA_1, OUTPUT); pinMode(DIR_POMPA_2, OUTPUT); pinMode(PWM_POMPA, OUTPUT);
  stopAll();

  Wire.begin();
  gas.begin(Wire, 0x08);

  // MiCS-5524 Initial Calibration
  delay(1000);
  float Rs_air = calculateRs();
  if (Rs_air > 0) R0_mics = Rs_air;
  Serial.print("✅ R0 Calibrated: "); Serial.println(R0_mics);

  // WiFi Connection
  Serial.print("📡 Connecting to WiFi: "); Serial.println(ssid);
  while (WiFi.begin(ssid, pass) != WL_CONNECTED) { delay(500); Serial.print("."); }
  Serial.println("\n✅ WiFi Connected!");
  Serial.print("   Local IP: "); Serial.println(WiFi.localIP());

  // Initial Connect
  ensureConnected();
}

void loop() {
  // Keep WiFi Connection for Data
  ensureConnected();

  // Recieve Command from Rust via Serial
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();

    if (cmd == "START_SAMPLING") {
      startSampling();
    } 
    else if (cmd == "STOP_SAMPLING") {
      stopSampling();
    }
  }

  // Send Data Sensor
  if (millis() - lastSend >= 250) {
    lastSend = millis();
    sendSensorData();
  }

  // Run FSM if it's Activate
  if (samplingActive) {
    runFSM();
  }
}

// Logic Functions
void startSampling() {
  if (!samplingActive) {
    samplingActive = true;
    currentLevel = 0;
    changeState(PRE_COND);
  }
}

void stopSampling() {
  if (samplingActive) {
    samplingActive = false;
    currentLevel = 0;
    changeState(IDLE);
    stopAll();
  }
}

void changeState(State s) {
  currentState = s;
  stateTime = millis();
  printFanLead = false;
  printBoth = false;
}

void runFSM() {
  unsigned long elapsed = millis() - stateTime;
  
  switch (currentState) {
    case PRE_COND:   
      kipas(120); pompa(0); 
      if (elapsed >= T_PRECOND) changeState(RAMP_UP); 
      break;
    case RAMP_UP:    
      rampKipas(speeds[currentLevel]); pompa(0); 
      if (elapsed >= T_RAMP) changeState(HOLD); 
      break;
    case HOLD:       
      kipas(speeds[currentLevel]); pompa(0); 
      if (elapsed >= T_HOLD) changeState(PURGE); 
      break;
    case PURGE:      
      kipas(255, true); 
      if (elapsed < T_FAN_LEAD) {
        pompa(0); 
      } else {
        pompa(255, true); 
      }
      if (elapsed >= T_PURGE) changeState(RECOVERY); 
      break;
    case RECOVERY:   
      stopAll(); 
      if (elapsed >= T_RECOVERY) {
        currentLevel++;
        if (currentLevel >= 5) { changeState(DONE); samplingActive = false; }
        else changeState(RAMP_UP);
      }
      break;
    case IDLE: 
    case DONE: 
      stopAll(); 
      break;
  }
}

// Send Data
void sendSensorData() {
  // Send Only if it's Connect to WiFi
  if (!client.connected()) return;

  // Read Sensor GM-XXX
  float no2 = (gas.measure_NO2()   < 30000) ? gas.measure_NO2()   / 1000.0 : 0.0;
  float eth = (gas.measure_C2H5OH()< 30000) ? gas.measure_C2H5OH()/ 1000.0 : 0.0;
  float voc = (gas.measure_VOC()   < 30000) ? gas.measure_VOC()   / 1000.0 : 0.0;
  float co  = (gas.measure_CO()    < 30000) ? gas.measure_CO()    / 1000.0 : 0.0;

  // Read Sensor MiCS-5524
  float Rs = calculateRs();
  float co_mics  = (Rs > 0) ? ppmFromRatio(Rs / R0_mics, "CO") : 0.0;
  float eth_mics = (Rs > 0) ? ppmFromRatio(Rs / R0_mics, "C2H5OH") : 0.0;
  float voc_mics = (Rs > 0) ? ppmFromRatio(Rs / R0_mics, "VOC") : 0.0;

  // CSV Format
  String data = "";
  data += String(no2, 3) + ",";
  data += String(eth, 3) + ",";
  data += String(voc, 3) + ",";
  data += String(co, 3) + ",";
  data += String(co_mics, 3) + ",";
  data += String(eth_mics, 3) + ",";
  data += String(voc_mics, 3) + ",";
  data += String(currentState) + ",";
  data += String(currentLevel);

  // Send with Newline at the End
  client.println(data);
}
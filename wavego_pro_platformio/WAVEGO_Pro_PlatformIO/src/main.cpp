// for PlatformIO. This is the main file of the project.
// This file is the entry point of the program.
#include <Arduino.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <nvs_flash.h>
#include "Config.h"
#include "RGBLight.h"
#include "BodyCtrl.h"
#include "FilesCtrl.h"
#include "Wireless.h"
#include "ScreenCtrl.h"
#include "web_page.h"
#include <WebServer.h>
#include "devs.h"

JsonDocument jsonCmdReceive;
JsonDocument jsonFeedback;
DeserializationError err;
String outputString;
RGBLight led;
BodyCtrl bodyCtrl;
FilesCtrl filesCtrl;
ScreenCtrl screenCtrl;
Wireless wireless;

int ServoMiddlePWM[12];
int jointsCurrentPos[12];
void jsonCmdReceiveHandler(const JsonDocument& jsonCmdInput);
void runMission(String missionName, int intervalTime, int loopTimes);
void buzzerCtrl(int freq, int duration);
bool steadyMode = 0; // steady mode
unsigned long previousMillisFB = 0;
const unsigned long intervalFB = 5000;

// Create AsyncWebServer object on port 80
WebServer server(80);

void handleRoot(){
  server.send(200, "text/html", index_html); //Send web page
}

void webCtrlServer(){
  server.on("/", handleRoot);

  server.on("/js", [](){
    String jsonCmdWebString = server.arg(0);
    deserializeJson(jsonCmdReceive, jsonCmdWebString);
    jsonCmdReceiveHandler(jsonCmdReceive);
    serializeJson(jsonFeedback, outputString);
    Serial.println(jsonCmdWebString);
    server.send(200, "text/plane", outputString);
    outputString = "";
    jsonFeedback.clear();
    jsonCmdReceive.clear();
  });

  // Start server
  server.begin();
  Serial.println("Server Starts.");
}

void setup() {
  delay(1000);
  Serial.begin(BAUD_RATE);
  Serial.println("device starting...");

  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(400000);

  screenCtrl.init();
  screenCtrl.displayText("WAVESHARE", 0, 0, 2);
  screenCtrl.displayText("Robotics", 0, 16, 2);

  InitICM20948();
  InitINA219();

  
  led.init();
  led.setColor(0, 255, 0, 64);
  led.setColor(1, 64, 0, 255);

  filesCtrl.init();
  
  bodyCtrl.init();

  /* 
  <<<<<<<<<<=========Wire Debug Init=========>>>>>>>>>
  [SHOW] DebugMode via wire config.
           [ . . . o o ]  LED G21 G15 G12 3V3
           [ . . . . . ]  TX  RX  GND  5V  5V
              <SWITCH>
  connect this two pins, and the robot go into debug mode.
  */
  pinMode(DEBUG_PIN, INPUT_PULLDOWN);
  
  pinMode(BUZZER_PIN, OUTPUT);
  buzzerCtrl(2000, 50);

  if(!filesCtrl.checkMission("boot")) {
    filesCtrl.createMission("boot", "this is the boot mission.");
    bodyCtrl.jointMiddle();
    filesCtrl.appendStep("boot", "{\"T\":400,\"mode\":1,\"ap_ssid\":\"WAVEGO\",\"ap_password\":\"12345678\",\"channel\":1,\"sta_ssid\":\"\",\"sta_password\":\"\"}");
  } else {
    if (filesCtrl.checkStepByType("boot", CMD_SET_JOINTS_ZERO)) {
      Serial.println("Already set joints zero pos.");
    } else {
      bodyCtrl.jointMiddle();
      Serial.println("Haven't set joints zero pos yet.");
    }
    if (filesCtrl.checkStepByType("boot", CMD_SET_WIFI_MODE)) {
      Serial.println("Already set wifi mode.");
    } else {
      filesCtrl.appendStep("boot", "{\"T\":400,\"mode\":1,\"ap_ssid\":\"WAVEGO\",\"ap_password\":\"12345678\",\"channel\":1,\"sta_ssid\":\"\",\"sta_password\":\"\"}");
      Serial.println("Haven't set wifi mode yet.");
    }
  }
  runMission("boot", 0, 1);

  wifi_mode_t mode;
  esp_err_t err = esp_wifi_get_mode(&mode);
  if (err != ESP_ERR_WIFI_NOT_INIT) {
    wireless.espnowInit(false);
    wireless.setJsonCommandCallback(jsonCmdReceiveHandler);
  }

  // wireless.setAP("WAVEGO", "12345678", 1);

  webCtrlServer();


  // ServoMiddlePWM[0] = 368;
  // ServoMiddlePWM[1] = 623;
  // ServoMiddlePWM[2] = 521;
  // ServoMiddlePWM[3] = 495;

  // ServoMiddlePWM[4] = 364;
  // ServoMiddlePWM[5] = 672;
  // ServoMiddlePWM[6] = 665;
  // ServoMiddlePWM[7] = 417;

  // ServoMiddlePWM[8] = 497;
  // ServoMiddlePWM[9] = 529;
  // ServoMiddlePWM[10] = 654;
  // ServoMiddlePWM[11] = 374;

  // // {"T":105,"set":[368,623,521,495,364,672,665,417,497,529,654,374]}

  // bodyCtrl.setJointsZeroPosArray(ServoMiddlePWM);
}


void buzzerCtrl(int freq, int duration) {
  tone(BUZZER_PIN, freq);
  delay(duration);
  noTone(BUZZER_PIN);
  digitalWrite(BUZZER_PIN, HIGH);
}


void wireDebugDetect(){
  if(digitalRead(DEBUG_PIN) == HIGH){
    bodyCtrl.jointMiddle();

    led.setColor(0, 255, 64, 0);
    led.setColor(1, 255, 64, 0);

    while(digitalRead(DEBUG_PIN) == HIGH){
      delay(100);
    }
    delay(1000);
    led.setColor(0, 255, 0, 64);
    led.setColor(1, 64, 0, 255);
  }
}

bool runStep(String missionName, int step) {
  outputString = filesCtrl.readStep(missionName, step);
  err = deserializeJson(jsonCmdReceive, outputString);
  if (err == DeserializationError::Ok) {
    jsonCmdReceiveHandler(jsonCmdReceive);
    return true;
  } else {
    Serial.print("JSON parsing error (this is a normal output when booting or running Mission): ");
    Serial.println(err.c_str());
    return false;
  }
}

void runMission(String missionName, int intervalTime, int loopTimes) {
  intervalTime = intervalTime - timeOffset;
  if (intervalTime < 0) {intervalTime = 0;}
  int j = 1;
  while (true) {
    Serial.print("Running loop: ");
    Serial.println(j);
    int i = 1;
    while (true) {
      if (Serial.available() > 0) {
        break;
      }
      if (runStep(missionName, i)) {
        Serial.print("Step: ");
        Serial.println(i);
        i++;
        delay(intervalTime);
      } else {
        Serial.println("Mission Completed.");
        break;
      }
    }
    j++;
    if (j > loopTimes && j != -1) {
      break;
    }
  }
}

// ---------------------------------------------------------------------------
// Command dispatch — one small handler per T: command type. jsonCmdReceive-
// Handler looks the type up in CMD_TABLE; adding a command is one function
// plus one table row instead of another arm in a 47-case switch.
// ---------------------------------------------------------------------------

typedef void (*CmdHandler)(const JsonDocument& cmd);

struct CmdEntry {
  int type;
  CmdHandler handler;
};

static void cmdUgvCtrl(const JsonDocument& c){
  bodyCtrl.ugvCtrl(c["L"], c["R"]);
}

static void cmdPtCtrl(const JsonDocument& c){
  bodyCtrl.ptCtrl(c["X"], c["Y"]);
}

static void cmdJointMiddle(const JsonDocument& c){
  bodyCtrl.jointMiddle();
}

static void cmdReleaseTorque(const JsonDocument& c){
  bodyCtrl.releaseTorque();
}

static void cmdSingleServoCtrl(const JsonDocument& c){
  bodyCtrl.singleServoCtrl(c["id"], c["goal"], c["time"], c["spd"]);
}

static void cmdGetJointsZero(const JsonDocument& c){
  memcpy(ServoMiddlePWM, bodyCtrl.getJointsZeroPosArray(), sizeof(ServoMiddlePWM));
  for (int i = 0; i < 12; i++) {
    Serial.print("Joint ");
    Serial.print(i);
    Serial.print(": ");
    Serial.println(ServoMiddlePWM[i]);
  }
}

static void cmdSetJointsZero(const JsonDocument& c){
  for (int i = 0; i < 12; i++) {
    ServoMiddlePWM[i] = c["set"][i];
  }
  bodyCtrl.setJointsZeroPosArray(ServoMiddlePWM);
}

static void cmdGetCurrentPos(const JsonDocument& c){
  memcpy(jointsCurrentPos, bodyCtrl.getServoFeedback(), sizeof(jointsCurrentPos));
  jsonFeedback.clear();
  jsonFeedback["T"] = - CMD_GET_CURRENT_POS;
  for (int i = 0; i < 12; i++) {
    jsonFeedback["fb"][i] = jointsCurrentPos[i];
  }
  serializeJson(jsonFeedback, outputString);
  Serial.println(outputString);
}

static void cmdSetCurrentPosZero(const JsonDocument& c){
  bodyCtrl.setCurrentPosZero();
  memcpy(ServoMiddlePWM, bodyCtrl.getJointsZeroPosArray(), sizeof(ServoMiddlePWM));
  jsonFeedback.clear();
  jsonFeedback["T"] = CMD_SET_JOINTS_ZERO;
  for (int i = 0; i < 12; i++) {
    Serial.print("Joint ");
    Serial.print(i);
    Serial.print(": ");
    Serial.println(ServoMiddlePWM[i]);
    jsonFeedback["set"][i] = ServoMiddlePWM[i];
  }
  serializeJson(jsonFeedback, outputString);
  Serial.println(outputString);
  filesCtrl.appendStep("boot", outputString);
}

static void cmdCtrlJointAngle(const JsonDocument& c){
  bodyCtrl.jointAngle(c["joint"], c["angle"]);
  bodyCtrl.moveTrigger();
}

static void cmdCtrlJointRad(const JsonDocument& c){
  bodyCtrl.jointRad(c["joint"], c["rad"]);
  bodyCtrl.moveTrigger();
}

static void cmdStandUp(const JsonDocument& c){
  bodyCtrl.stand();
}

static void cmdBasicMove(const JsonDocument& c){
  bodyCtrl.inputCmd(c["FB"], c["LR"]);
}

static void cmdBasicFunc(const JsonDocument& c){
  if (c["func"] == 1) {
    bodyCtrl.functionStayLow();
  } else if (c["func"] == 2) {
    bodyCtrl.functionHandshake();
  } else if (c["func"] == 3) {
    bodyCtrl.functionJump();
  } else if (c["func"] == 4) {
    steadyMode = 1;
  } else if (c["func"] == 5) {
    steadyMode = 0;
    bodyCtrl.standUp(95);
  }
}

static void cmdSingleLegCtrl(const JsonDocument& c){
  bodyCtrl.singleLegCtrl(c["leg"], c["x"], c["y"], c["z"]);
  bodyCtrl.moveTrigger();
}

static void cmdStandUpHeight(const JsonDocument& c){
  bodyCtrl.standUp(c["h"]);
}

static void cmdSetInterpolationParams(const JsonDocument& c){
  bodyCtrl.setInterpolationParams(c["delay"], c["iterate"]);
}

static void cmdSetGaitParams(const JsonDocument& c){
  bodyCtrl.setGaitParams(c["maxHeight"],
                         c["minHeight"],
                         c["height"],
                         c["lift"],
                         c["range"],
                         c["acc"],
                         c["extendedX"],
                         c["extendedZ"],
                         c["sideMax"],
                         c["massAdjust"]);
  // live bounds changed: re-clamp the engine against them (if running)
  if (bodyCtrl.engineActive()) {
    bodyCtrl.configureEngine(bodyCtrl.engine().lastParams());
  }
}

static void cmdGaitEngine(const JsonDocument& c){
  // partial update: seed from the merged state so absent fields keep value
  gait::Params p = bodyCtrl.engine().lastParams();
  if (!c["mode"].isNull())     p.mode     = (gait::Mode)(int)c["mode"];
  if (!c["speed"].isNull())    p.speed    = (float)(double)c["speed"];
  if (!c["heading"].isNull())  p.headingDeg = (float)(double)c["heading"];
  if (!c["height"].isNull())   p.heightMm = (float)(double)c["height"];
  if (!c["stride"].isNull())   p.strideMm = (float)(double)c["stride"];
  if (!c["distance"].isNull()) p.distanceMm = (float)(double)c["distance"];
  bodyCtrl.configureEngine(p);
}

static void cmdSetColor(const JsonDocument& c){
  led.setColor(c["set"][0], c["set"][1], c["set"][2], c["set"][3]);
}

static void cmdDisplaySingle(const JsonDocument& c){
  screenCtrl.changeSingleLine(c["line"], c["text"], c["update"]);
}

static void cmdDisplayUpdate(const JsonDocument& c){
  screenCtrl.updateFrame();
}

static void cmdDisplayFrame(const JsonDocument& c){
  screenCtrl.changeSingleLine(1, c["l1"], false);
  screenCtrl.changeSingleLine(2, c["l2"], false);
  screenCtrl.changeSingleLine(3, c["l3"], false);
  screenCtrl.changeSingleLine(4, c["l4"], true);
}

static void cmdDisplayClear(const JsonDocument& c){
  screenCtrl.clearDisplay();
}

static void cmdBuzzerCtrl(const JsonDocument& c){
  tone(BUZZER_PIN, c["freq"]);
  buzzerCtrl(c["freq"], c["duration"]);
}

static void cmdGetBatteryVoltage(const JsonDocument& c){
  InaDataUpdate();
  jsonFeedback.clear();
  jsonFeedback["T"] = - CMD_GET_BATTERY_VOLTAGE;
  jsonFeedback["voltage"] = loadVoltage_V;
  serializeJson(jsonFeedback, outputString);
  Serial.println(outputString);
}

static void cmdScanFiles(const JsonDocument& c){
  filesCtrl.scan();
}

static void cmdCreateMission(const JsonDocument& c){
  filesCtrl.createMission(c["name"], c["intro"]);
}

static void cmdFormatFlash(const JsonDocument& c){
  filesCtrl.flash();
}

static void cmdMissionContent(const JsonDocument& c){
  filesCtrl.missionContent(c["name"]);
}

static void cmdAppendStepJson(const JsonDocument& c){
  filesCtrl.appendStep(c["name"], c["json"]);
}

static void cmdInsertStepJson(const JsonDocument& c){
  filesCtrl.insertStep(c["name"], c["step"], c["json"]);
}

static void cmdReplaceStepJson(const JsonDocument& c){
  filesCtrl.replaceStep(c["name"], c["step"], c["json"]);
}

static void cmdDeleteStep(const JsonDocument& c){
  filesCtrl.deleteStep(c["name"], c["step"]);
}

static void cmdRunStep(const JsonDocument& c){
  runStep(c["name"], c["step"]);
}

static void cmdRunMission(const JsonDocument& c){
  runMission(c["name"], c["interval"], c["loop"]);
}

static void cmdDeleteMission(const JsonDocument& c){
  filesCtrl.deleteMission(c["name"]);
}

// Shared tail of every CMD_SET_WIFI_MODE branch: publish the resulting wifi
// config as a boot-mission step so it survives reboot. Mode 0 reports only
// the mode; mode 1 echoes the AP credentials and (on success) the STA ones.
static void sendWifiModeFeedback(int mode, const JsonDocument& cmd,
                                 const char* sta_ssid, const char* sta_password){
  jsonFeedback.clear();
  jsonFeedback["T"] = CMD_SET_WIFI_MODE;
  jsonFeedback["mode"] = mode;
  if (mode == 1) {
    jsonFeedback["ap_ssid"] = cmd["ap_ssid"];
    jsonFeedback["ap_password"] = cmd["ap_password"];
    jsonFeedback["channel"] = cmd["channel"];
    jsonFeedback["sta_ssid"] = sta_ssid;
    jsonFeedback["sta_password"] = sta_password;
  }
  serializeJson(jsonFeedback, outputString);
  filesCtrl.checkReplaceStep("boot", outputString);
}

static void cmdSetWifiMode(const JsonDocument& c){
  if (c["mode"] == 0) {
    wireless.setWifiMode(c["mode"], "", "", 0, "", "");
    sendWifiModeFeedback(0, c, "", "");
  } else if (c["mode"] == 1) {
    if (wireless.setWifiMode(c["mode"],
                             c["ap_ssid"],
                             c["ap_password"],
                             c["channel"],
                             c["sta_ssid"],
                             c["sta_password"])) {
      sendWifiModeFeedback(1, c, c["sta_ssid"] | "", c["sta_password"] | "");
    } else {
      sendWifiModeFeedback(1, c, "", "");
    }
  }
}

static void cmdWifiInfo(const JsonDocument& c){
  outputString = filesCtrl.findCmdByType("boot", CMD_SET_WIFI_MODE);
  Serial.println(outputString);
}

static void cmdGetApIp(const JsonDocument& c){
  outputString = wireless.getAPIP();
  Serial.println(outputString);
}

static void cmdGetStaIp(const JsonDocument& c){
  outputString = wireless.getSTAIP();
  Serial.println(outputString);
}

static void cmdInitEspNow(const JsonDocument& c){
  wireless.espnowInit(c["longrange"]);
}

static void cmdSetEspNowMode(const JsonDocument& c){
  wireless.setEspNowMode(c["mode"]);
}

static void cmdGetMac(const JsonDocument& c){
  outputString = wireless.macToString(wireless.getMac());
  Serial.println(outputString);
}

static void cmdEspNowSend(const JsonDocument& c){
  wireless.sendEspNow(c["mac"], c["data"]);
}

static void cmdAddMac(const JsonDocument& c){
  wireless.addMacToPeerString(c["mac"]);
}

static void cmdEsp32Reboot(const JsonDocument& c){
  ESP.restart();
}

static void cmdClearNvs(const JsonDocument& c){
  nvs_flash_erase();
  delay(1000);
  nvs_flash_init();
}

static const CmdEntry CMD_TABLE[] = {
  {CMD_UGV_CTRL,                 cmdUgvCtrl},
  {CMD_PT_CTRL,                  cmdPtCtrl},
  {CMD_JOINT_MIDDLE,             cmdJointMiddle},
  {CMD_RELEASE_TORQUE,           cmdReleaseTorque},
  {CMD_SINGLE_SERVO_CTRL,        cmdSingleServoCtrl},
  {CMD_GET_JOINTS_ZERO,          cmdGetJointsZero},
  {CMD_SET_JOINTS_ZERO,          cmdSetJointsZero},
  {CMD_GET_CURRENT_POS,          cmdGetCurrentPos},
  {CMD_SET_CURRENT_POS_ZERO,     cmdSetCurrentPosZero},
  {CMD_CTRL_JOINT_ANGLE,         cmdCtrlJointAngle},
  {CMD_CTRL_JOINT_RAD,           cmdCtrlJointRad},
  {CMD_STAND_UP,                 cmdStandUp},
  {CMD_BASIC_MOVE,               cmdBasicMove},
  {CMD_BASIC_FUNC,               cmdBasicFunc},
  {CMD_SINGLE_LEG_CTRL,          cmdSingleLegCtrl},
  {CMD_STAND_UP_HEIGHT,          cmdStandUpHeight},
  {CMD_SET_INTERPOLATION_PARAMS, cmdSetInterpolationParams},
  {CMD_SET_GAIT_PARAMS,          cmdSetGaitParams},
  {CMD_GAIT_ENGINE,              cmdGaitEngine},
  {CMD_SET_COLOR,                cmdSetColor},
  {CMD_DISPLAY_SINGLE,           cmdDisplaySingle},
  {CMD_DISPLAY_UPDATE,           cmdDisplayUpdate},
  {CMD_DISPLAY_FRAME,            cmdDisplayFrame},
  {CMD_DISPLAY_CLEAR,            cmdDisplayClear},
  {CMD_BUZZER_CTRL,              cmdBuzzerCtrl},
  {CMD_GET_BATTERY_VOLTAGE,      cmdGetBatteryVoltage},
  {CMD_SCAN_FILES,               cmdScanFiles},
  {CMD_CREATE_MISSION,           cmdCreateMission},
  {CMD_FORMAT_FLASH,             cmdFormatFlash},
  {CMD_MISSION_CONTENT,          cmdMissionContent},
  {CMD_APPEND_SETP_JSON,         cmdAppendStepJson},
  {CMD_INSERT_SETP_JSON,         cmdInsertStepJson},
  {CMD_REPLACE_SETP_JSON,        cmdReplaceStepJson},
  {CMD_DELETE_SETP,              cmdDeleteStep},
  {CMD_RUN_STEP,                 cmdRunStep},
  {CMD_RUN_MISSION,              cmdRunMission},
  {CMD_DELETE_MISSION,           cmdDeleteMission},
  {CMD_SET_WIFI_MODE,            cmdSetWifiMode},
  {CMD_WIFI_INFO,                cmdWifiInfo},
  {CMD_GET_AP_IP,                cmdGetApIp},
  {CMD_GET_STA_IP,               cmdGetStaIp},
  {CMD_INIT_ESP_NOW,             cmdInitEspNow},
  {CMD_SET_ESP_NOW_MODE,         cmdSetEspNowMode},
  {CMD_GET_MAC,                  cmdGetMac},
  {CMD_ESP_NOW_SEND,             cmdEspNowSend},
  {CMD_ADD_MAC,                  cmdAddMac},
  {ESP32_REBOOT,                 cmdEsp32Reboot},
  {CMD_CLEAR_NVS,                cmdClearNvs},
};

void jsonCmdReceiveHandler(const JsonDocument& jsonCmdInput){
  const int cmdType = jsonCmdInput["T"].as<int>();
  // Pose/locomotion commands reclaim control from the gait engine (the
  // engine is level-based exactly like T:111, so any takeover command
  // must switch it off first — T:111 FB:0 LR:0 remains THE universal stop).
  if (cmdType == CMD_UGV_CTRL || cmdType == CMD_BASIC_MOVE ||
      cmdType == CMD_BASIC_FUNC || cmdType == CMD_STAND_UP ||
      cmdType == CMD_STAND_UP_HEIGHT || cmdType == CMD_PT_CTRL) {
    bodyCtrl.engine().stop();
  }
  for (size_t i = 0; i < sizeof(CMD_TABLE) / sizeof(CMD_TABLE[0]); i++) {
    if (CMD_TABLE[i].type == cmdType) {
      CMD_TABLE[i].handler(jsonCmdInput);
      return;
    }
  }
}

void serialCtrl() {
  static String receivedData;

  while (Serial.available() > 0) {
    char receivedChar = Serial.read();
    receivedData += receivedChar;

    // Detect the end of the JSON string based on a specific termination character
    if (receivedChar == '\n') {
      // Now we have received the complete JSON string
      DeserializationError err = deserializeJson(jsonCmdReceive, receivedData);
      if (err == DeserializationError::Ok) {
  			if (InfoPrint == 1) {
  				Serial.print(receivedData);
  			}
        jsonCmdReceiveHandler(jsonCmdReceive);
      } else {
        // Handle JSON parsing error here
        if (InfoPrint == 1) {
          Serial.print("JSON parsing error: ");
          Serial.println(err.c_str());
        }
      }
      // Reset the receivedData for the next JSON string
      receivedData = "";
    }
  }
}


void loop() {
  // put your main code here, to run repeatedly:
  serialCtrl();
  server.handleClient();

  if (steadyMode == 1) {
    accXYZUpdate();
    bodyCtrl.balancing(ACC_X, ACC_Y);
  } else if (bodyCtrl.engineActive()) {
    bodyCtrl.engineCtrl();
  } else {
    bodyCtrl.robotCtrl();
  }
  wireDebugDetect();

  // one-shot gait-engine distance completion feedback (async, like T:-207)
  float engineTraveledMm = 0;
  uint32_t engineCycles = 0;
  if (bodyCtrl.checkEngineDone(engineTraveledMm, engineCycles)) {
    jsonFeedback.clear();
    jsonFeedback["T"] = -117;
    jsonFeedback["done"] = true;
    jsonFeedback["traveled"] = engineTraveledMm;
    jsonFeedback["cycles"] = engineCycles;
    serializeJson(jsonFeedback, outputString);
    Serial.println(outputString);
  }

  unsigned long currentMillis = millis();
  if (currentMillis - previousMillisFB >= intervalFB) {
    previousMillisFB = currentMillis;
    InaDataUpdate();
    jsonFeedback.clear();
    jsonFeedback["T"] = 1001;
    jsonFeedback["L"] = 0;
    jsonFeedback["R"] = 0;
    jsonFeedback["r"] = 0;
    jsonFeedback["p"] = 0;
    jsonFeedback["v"] = loadVoltage_V;
    jsonFeedback["pan"] = 0;
    jsonFeedback["tilt"] = 0;
    serializeJson(jsonFeedback, outputString);
    Serial.println(outputString);
  }

  // accXYZUpdate();
  // Serial.print("ACC_X: ");Serial.println(ACC_X);
  // Serial.print("ACC_Y: ");Serial.println(ACC_Y);

  // bodyCtrl.singleLegCtrl(1, WALK_EXTENDED_X, cmdInput, WALK_EXTENDED_Z);

  // bodyCtrl.standUp(95);
  // Serial.println("Current pos: ");
  // bodyCtrl.massCenerAdjustTestLoop();
}
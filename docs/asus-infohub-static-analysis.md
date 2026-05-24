# ASUS InfoHub static analysis notes

Target device:

- ASUS TUF GAMING LC III 360 ARGB LCD
- USB VID/PID: `0b05:1c7b`

Official ASUS China API results:

- Software:
  - Title: `ASUS InfoHub Software TUF GAMING v1.0.0.15 For Windows 10/11 64-bit`
  - File: `ASUS_InfoHub_Software_TUF_GAMING_LC_III_360_ARGB_LCD_v1.0.0.15.zip`
  - URL path: `/pub/ASUS/Accessory/Cooling/TUF_GAMING_LC_III_360_ARGB_LCD/ASUS_InfoHub_Software_TUF_GAMING_LC_III_360_ARGB_LCD_v1.0.0.15.zip`
  - SHA-256: `0D7124D700B07D1F49315D77AA15473F01C42C1492F2E8CECE845F19C32D2A21`
- Firmware:
  - Title: `ASUS InfoHub Firmware TUF GAMING v51 For Windows 10/11 64-bit`
  - File: `ASUS_InfoHub_Firmware_TUF_GAMING_LC_III_360_ARGB_LCD_v51.rar`
  - URL path: `/pub/ASUS/Accessory/Cooling/TUF_GAMING_LC_III_360_ARGB_LCD/ASUS_InfoHub_Firmware_TUF_GAMING_LC_III_360_ARGB_LCD_v51.rar`
  - SHA-256: `267B1477374D28FCA01BE92B2FF11748591560D30C1A1392BF9D06493A43BFD8`

Local extraction paths used during analysis:

- `/tmp/asus_infohub/firmware_v51/WW11_320x320_2.8inch_v51_TUF_20250626.exe`
- `/tmp/asus_infohub/software_v1/extract/app/ASUS InfoHub.exe`

Findings:

- The software imports `HID.DLL`, `SETUPAPI.dll`, `ReadFile`, `WriteFile`, `CreateFileA/W`, and device notification APIs.
- The software explicitly matches `VID_0B05&PID_1C7B&MI_00`.
- It logs:
  - `Device connected and recognized (VID_0B05&PID_1C7B&MI_00)`
  - `EnumerateLEDDevice done, hid1=%d hid2=%d`
  - `LED HID1 connected: path=%s fw=%d`
  - `LED HID2 connected: path=%s`
- Enumeration keeps two HID handles:
  - usage `0x01B9` assigned to HID1
  - usage `0x0401` assigned to HID2
- The app dynamically loads HID functions:
  - `HidD_GetAttributes`
  - `HidD_GetPreparsedData`
  - `HidP_GetCaps`
  - `HidD_GetFeature`
  - `HidD_SetFeature`
  - `HidD_GetProductString`
  - `HidD_GetManufacturerString`
  - `HidD_GetSerialNumberString`
- The app uses Windows `WriteFile` with an overlapped structure and pads packets to the HID output report byte length before writing.
- The generic HID write wrapper is at `0x422b00`. It writes the complete Windows output report, including a leading zero report-id byte for these unnumbered reports.
- Linux `hidraw` accepts the same complete-report shape and the panel only recovered after using the leading zero report-id byte. Control writes are 441 bytes (`0x00` plus 440 bytes), and data writes are 1025 bytes (`0x00` plus 1024 bytes).
- Control writes go through HID1 with 440 bytes of command payload copied after the report-id byte.
- `screen_type` writes control command `1f 01 00 80 <type>` on HID1. Type `2` switches the LCD out of the default animation mode so uploaded custom image frames are shown.
- Frame data writes go through HID2. Each Linux hidraw data payload is 1024 bytes:
  - byte 0: command `0x08`
  - byte 1: total packet count on the first packet, otherwise packet index; only the low 8 bits are stored
  - bytes 2-3: first packet flag `0x8000` little-endian on packet 0, otherwise `0x0000`
  - bytes 4-1023: 1020 bytes of frame data
- The TUF LC III firmware/app path identifies this device class as `TUF水冷(320x320)`.
- A 320x320 RGB565 frame is 204800 bytes and is split into 201 HID2 packets.
- If a data write fails, InfoHub sends control command `ff 01` on HID1.
- Supported media extensions in UI strings:
  - `.gif`, `.jpg`, `.jpeg`, `.png`, `.bmp`, `.mp4`, `.avi`
- The firmware updater is also HID based and includes strings for entering boot mode, upgrade progress, and completion flag writing.
- Firmware binary strings include `Shenzhen Xinyao Technology Co., Ltd.`, `SEGGER emWin`, and `N9H20`, suggesting the LCD controller firmware is embedded inside the updater.

Practical implication:

- Windows packet capture is not strictly required as the official packages expose enough HID usage and API behavior to continue static reverse engineering.
- A packet capture may still be the fastest way to recover exact command opcodes and frame-transfer sequencing if static analysis stalls.

# Lumen Hub Control Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the selected Armoury Crate/iCUE/FanControl parity upgrades: unified device tree, advanced fan control, lighting physical layout calibration, permission helper foundation, diagnostics center, installation/autostart support, and asset-library templates.

**Architecture:** Build a shared status/device model first, then plug fan, lighting, permissions, installer, and asset-library workflows into that model. Keep hardware writes behind focused adapters and keep GUI pages as presenters/controllers with explicit diagnostic output.

**Tech Stack:** Python 3, PySide6, pytest, Linux hwmon/powercap/hidraw, Windows LibreHardwareMonitor/OpenHardwareMonitor, OpenRGB SDK, LIAN LI wireless USB transport, systemd/user service files, Windows shortcut/task-start helpers.

---

## Selected Scope

The user selected items `1, 2, 3, 5, 8, 10, 12` from the product gap analysis:

- `1` Unified device tree and binding/status system.
- `2` Fan control closer to FanControl: calibration, hysteresis, sensor mixing, safety fallback.
- `3` Lighting physical layout calibration for device order, fan order, LED count, direction, and preview.
- `5` Background service/permission proxy foundation for Linux and Windows.
- `8` Diagnostics center that can produce a complete support report.
- `10` Installer/autostart support for Linux and Windows.
- `12` Asset-library upgrade with templates and categories.

## File Structure

- Create/modify: `usb9_lcd/gui/device_inventory.py` for the unified device tree model and renderer.
- Modify: `usb9_lcd/gui/system_status.py` to include device-tree diagnostics.
- Modify: `usb9_lcd/gui/main_window.py` to build the device tree from current GUI state.
- Modify: `usb9_lcd/gui/fan_host.py` and `usb9_lcd/gui/fan_curve_model.py` for calibration, hysteresis, mixed sensors, and safety fallback.
- Modify: `usb9_lcd/gui/lianli_wireless_page.py`, `usb9_lcd/gui/lighting_page.py`, and `usb9_lcd/lighting/*` for layout calibration and physical preview.
- Create: `usb9_lcd/service/` for permission helper interfaces and safe request/response models.
- Create: `packaging/linux/` and `packaging/windows/` for install, autostart, udev/tmpfiles, and shortcut/task helper artifacts.
- Modify: `usb9_lcd/gui/asset_page.py`, `usb9_lcd/assets.py`, and `assets/` data files for template categories.
- Test: `tests/test_gui_import.py`, `tests/test_fan_host.py`, `tests/test_lighting.py`, `tests/test_assets.py`, `tests/test_platforms.py`, and focused new tests when a new pure module is added.

---

### Task 1: Unified Device Tree Foundation

**Files:**
- Create: `usb9_lcd/gui/device_inventory.py`
- Modify: `usb9_lcd/gui/system_status.py`
- Modify: `usb9_lcd/gui/main_window.py`
- Test: `tests/test_gui_import.py`

- [x] **Step 1: Add failing tests for device tree report output**

Run:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gui_import.py::test_system_status_report_includes_permission_and_component_state tests/test_gui_import.py::test_main_window_system_status_snapshot_includes_device_tree -q
```

Expected before implementation: fails because `usb9_lcd.gui.device_inventory` is missing and system status snapshots do not include a device tree.

- [x] **Step 2: Implement the pure device tree model**

Implemented `DeviceTreeItem`, `DeviceTreeSnapshot`, `build_device_tree_snapshot()`, `render_device_tree_report()`, and `summarize_device_tree()` in `usb9_lcd/gui/device_inventory.py`.

- [x] **Step 3: Integrate the tree into diagnostics**

`SystemStatusSnapshot` now carries `device_tree`, and `render_system_status_report()` includes a `设备树` section.

- [x] **Step 4: Build the tree from `MainWindow` state**

`MainWindow._system_status_snapshot()` now includes LCD devices, telemetry, ordinary fan status, OpenRGB lighting status, and LIAN LI wireless status.

- [x] **Step 5: Verify the foundation**

Run:

```bash
python -m py_compile usb9_lcd/gui/device_inventory.py usb9_lcd/gui/system_status.py usb9_lcd/gui/main_window.py tests/test_gui_import.py
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gui_import.py::test_system_status_report_includes_permission_and_component_state tests/test_gui_import.py::test_main_window_system_status_snapshot_includes_device_tree -q
```

Expected after implementation: `2 passed`.

### Task 2: Fan Calibration, Hysteresis, And Mixed Sensors

**Files:**
- Modify: `usb9_lcd/gui/fan_curve_model.py`
- Modify: `usb9_lcd/gui/fan_host.py`
- Modify: `usb9_lcd/gui/settings.py`
- Test: `tests/test_fan_host.py`

- [x] **Step 1: Add fan-control model tests**

Add tests for hysteresis, mixed-sensor max selection, sensor-loss fallback, and minimum start PWM.

Run:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_fan_host.py::test_fan_curve_helpers_sanitize_and_interpolate tests/test_fan_host.py::test_host_fan_settings_load_defaults_and_curve_points tests/test_fan_host.py::test_fan_host_applies_curve_pwm_from_mixed_cpu_gpu_temperature -q
```

Expected before implementation: fails because the helpers and settings fields do not exist.

- [x] **Step 2: Implement pure fan policy helpers**

Add helpers that accept CPU/GPU temperatures, curve points, hysteresis degrees, minimum start percent, and missing-sensor fallback percent, returning a safe PWM percent or `None`.

- [x] **Step 3: Expose GUI controls**

Add controls for sensor source (`CPU`, `GPU`, `CPU/GPU 最大值`), hysteresis, minimum start PWM, and fallback PWM.

- [x] **Step 4: Persist settings**

Extend `HostFanUiSettings` and settings migration/loading/saving for the new fields.

- [x] **Step 5: Run fan tests**

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_fan_host.py -q
```

### Task 3: Lighting Physical Layout Calibration

**Files:**
- Modify: `usb9_lcd/gui/lighting_page.py`
- Modify: `usb9_lcd/gui/lianli_wireless_page.py`
- Modify: `usb9_lcd/gui/settings.py`
- Modify: `usb9_lcd/lighting/profiles.py`
- Test: `tests/test_gui_import.py`, `tests/test_lighting.py`

- [ ] **Step 1: Add layout model tests**

Cover device order, LED count, direction, and named port/fan positions.

- [ ] **Step 2: Add layout calibration UI**

Add an explicit calibration panel that can light one device/fan/zone at a time and save order/direction.

- [ ] **Step 3: Feed layouts into effects**

Make chase, meteor, scan, matrix, and gradient effects consume saved physical layout instead of assuming raw target order.

- [ ] **Step 4: Verify without hardware writes**

Use fake OpenRGB and fake LIAN LI backends to validate target selection and generated packet/effect order.

### Task 4: Permission Helper Foundation

**Files:**
- Create: `usb9_lcd/service/__init__.py`
- Create: `usb9_lcd/service/permissions.py`
- Modify: `usb9_lcd/gui/fan_host.py`
- Modify: `usb9_lcd/gui/main_window.py`
- Test: `tests/test_platforms.py`, `tests/test_fan_host.py`

- [ ] **Step 1: Define safe permission request objects**

Model permitted operations for powercap read, PWM write, hidraw write, and OpenRGB path checks.

- [ ] **Step 2: Route GUI permission actions through the helper interface**

Keep existing direct shell fallback, but make the GUI call the helper interface first.

- [ ] **Step 3: Add reportable helper status**

Expose helper availability in the diagnostics center and device tree.

### Task 5: Diagnostics Center Upgrade

**Files:**
- Modify: `usb9_lcd/gui/platform_diagnostics.py`
- Modify: `usb9_lcd/gui/system_status.py`
- Modify: `usb9_lcd/gui/debug.py`
- Test: `tests/test_gui_import.py`, `tests/test_platforms.py`

- [x] **Step 1: Add exportable support report**

The report must include platform paths, device tree, permissions, OpenRGB status, fan backend, LIAN LI status, telemetry, and recent logs.

- [x] **Step 2: Add copy/save buttons**

Add `复制报告` and `保存报告` actions in the diagnostics dialog.

- [x] **Step 3: Add tests for report sections**

Assert all required section titles and key device/status lines are present.

Run:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_platforms.py::test_platform_diagnostic_report_includes_openrgb_and_paths tests/test_platforms.py::test_support_report_includes_device_tree_permissions_and_recent_events tests/test_platforms.py::test_platform_diagnostics_dialog_can_copy_and_save_report -q
```

Expected after implementation: `3 passed`.

### Task 6: Linux/Windows Install And Autostart

**Files:**
- Create: `packaging/linux/lumen-hub.desktop`
- Create: `packaging/linux/lumen-hub-udev.rules`
- Create: `packaging/linux/lumen-hub-tmpfiles.conf`
- Create: `packaging/windows/lumen-hub-autostart.ps1`
- Modify: `README.md`
- Test: `tests/test_platforms.py`

- [ ] **Step 1: Add package artifact tests**

Validate that generated/installable files contain the GUI entrypoint and required permission rules.

- [ ] **Step 2: Add Linux artifacts**

Add desktop entry, udev rules, tmpfiles sample, and README install commands.

- [ ] **Step 3: Add Windows autostart helper**

Add a PowerShell script that creates a Startup shortcut or scheduled task for `lumen-hub-gui`.

### Task 7: Asset Library Templates

**Files:**
- Modify: `usb9_lcd/assets.py`
- Modify: `usb9_lcd/gui/asset_page.py`
- Create/modify: `assets/templates/` metadata files
- Test: `tests/test_assets.py`, `tests/test_gui_import.py`

- [x] **Step 1: Add template metadata tests**

Cover category, device size, preview label, and source path/link.

- [x] **Step 2: Add template categories**

Add built-in groups for monitoring dashboards, GIF/animation, static backgrounds, CPU/GPU themes, and LIAN LI status themes.

- [x] **Step 3: Add GUI filters**

Expose template category filters and a clear selected-template action.

Implemented asset categories for monitoring dashboards, CPU themes, GPU themes, LIAN LI status themes, GIF/animation, static images, and test patterns. `AssetLibraryPage` now exposes a category filter and shows template/user source metadata in the media list.

---

## Verification Gate

Before marking the full selected objective complete, verify all of these commands or their narrower task-specific replacements:

```bash
python -m py_compile usb9_lcd/gui/device_inventory.py usb9_lcd/gui/system_status.py usb9_lcd/gui/main_window.py usb9_lcd/gui/fan_host.py usb9_lcd/gui/lighting_page.py usb9_lcd/gui/lianli_wireless_page.py usb9_lcd/gui/asset_page.py usb9_lcd/monitoring/models.py usb9_lcd/monitoring/nvidia.py
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_gui_import.py tests/test_fan_host.py tests/test_lighting.py tests/test_assets.py tests/test_platforms.py tests/test_monitoring.py -q
```

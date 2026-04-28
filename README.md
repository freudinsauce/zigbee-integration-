# Zigbee Energy Manager

An asynchronous Zigbee network manager for monitoring and controlling Zigbee devices with a focus on energy telemetry, device state tracking, and event-driven processing. Zigbee Energy Manager is an event-driven system for managing a Zigbee network. It provides:

* Device control (switching smart plugs)
* Energy telemetry collection (voltage, current, power, energy)
* Sensor monitoring (motion, contact, smoke)
* Persistent logging to log file and CSV
* Inter-process communication via FIFO

Built on top of `zigpy` and `bellows`, designed for use with a Zigbee coordinator (Silicon Labs EZSP -- JetStick Z4) on Linux systems such as Raspberry Pi.

## Table of Contents

* [Zigbee Architecture](#zigbee-architecture)
* [Supported Devices](#supported-devices)
* [Features](#features)
* [Project Structure](#project-structure)
* [Installation](#installation)
* [Configuration](#configuration)
* [Running](#running)
* [FIFO Command Interface](#fifo-command-interface)
* [Data Logging](#data-logging)
* [Zigbee Internals (Deep Dive)](#zigbee-internals-deep-dive)
* [Troubleshooting](#troubleshooting)
* [Testing Setup (~/.bashrc)](#testing-setup-bashrc)


## Zigbee Architecture

The system follows the standard Zigbee topology:

* Coordinator — USB Zigbee dongle (EZSP / Silicon Labs)
* Routers — mains-powered devices (e.g., smart plugs)
* End Devices — battery-powered sensors

Stack components:

* `zigpy` — Zigbee abstraction layer
* `bellows` — EZSP (Silicon Labs) radio implementation

## Supported Devices

### Mains-powered (Routers)

* Smart plugs
* Energy monitoring devices

Clusters:

* OnOff
* ElectricalMeasurement
* Metering

### Battery-powered (End Devices)

* Motion sensors (PIR)
* Door/contact sensors
* Smoke detectors

Clusters:

* IAS Zone
* PowerConfiguration
* TemperatureMeasurement
* RelativeHumidity

## Features

### Core

* Fully asynchronous architecture (asyncio)
* Automatic device configuration on join
* State restoration after restart

### Monitoring

* Periodic polling for mains devices
* Event-driven handling for sensors
* Battery level tracking

### Data Handling

* CSV logging:

  * energy data
  * temperature
  * humidity
  * binary sensor events

### Control

* FIFO-based command interface
* Network control (permit join)
* Device control

---

## Project Structure

```
.
├── zigbee_manager.py
├── logs/
│   └── zigbee_manager.log
├── measurements/
│   └── YYYY-MM-DD/
│       ├── smart_plug.csv
│       ├── temperature.csv
│       ├── humidity.csv
│       └── binary_sensors.csv
```

---

## Installation

```bash
python3 -m venv venv
source venv/bin/activate

pip install zigpy bellows
```

---

## Configuration

Main configuration:

```python
DEVICE_CONFIG = {
    "database": "zigbee.db",
    "device": {
        "path": "/dev/serial/by-id/...",
        "baudrate": 115200,
    }
}
```

Make sure the device path points to your Zigbee coordinator.

---

## Running

```bash
LOG_LEVEL=INFO python zigbee_manager.py
```

On startup:

* Zigbee network is initialized
* Permit join is enabled for 60 seconds
* Background tasks are started

---

## FIFO Command Interface

IPC is implemented using named pipes:

```
/tmp/zigbee_cmd
/tmp/zigbee_resp
```

### Examples

```bash
echo "list" > /tmp/zigbee_cmd
cat /tmp/zigbee_resp
```

```bash
echo "permit 60" > /tmp/zigbee_cmd
```

```bash
echo "toggle <ieee>" > /tmp/zigbee_cmd
```

```bash
echo "status <ieee>" > /tmp/zigbee_cmd
```

---

## Data Logging

All data is stored under:

```
measurements/YYYY-MM-DD/
```

## smart_plug.csv

Logged in `_background_mains_monitor()` approximately every 60 seconds for mains-powered devices.

| Column       | Type   | Unit     | Source/ZCL                     | Description                              |
| ------------ | ------ | -------- | ------------------------------ | ---------------------------------------- |
| timestamp    | string | ISO 8601 | System                         | Timestamp (`datetime.now().isoformat()`) |
| ieee         | string | —        | Device                         | Device IEEE address                      |
| manufacturer | string | —        | Basic cluster                  | Device manufacturer                      |
| model        | string | —        | Basic cluster                  | Device model                             |
| state        | string | ON/OFF   | OnOff (0x0006)                 | Current plug state                       |
| voltage      | float  | V        | ElectricalMeasurement          | RMS voltage                              |
| current      | float  | A        | ElectricalMeasurement          | RMS current                              |
| power        | float  | W        | ElectricalMeasurement          | Active power                             |
| energy_kwh   | float  | kWh      | Metering (0x0702)              | Accumulated energy                       |

### Notes:

* Values are scaled using:

  * `multiplier`
  * `divisor`
* If a cluster is not available, the field may be missing (`None`)
* `energy_kwh` is derived from `current_summ_delivered`

---

## temperature.csv

Logged in `attribute_updated()` on attribute change events.

| Column        | Type   | Unit     | Source (Zigbee)                 | Description       |
| ------------- | ------ | -------- | ------------------------------- | ----------------- |
| timestamp     | string | ISO 8601 | System                          | Event timestamp   |
| ieee          | string | —        | Device                          | Device IEEE       |
| model         | string | —        | Device                          | Device model      |
| temperature_c | float  | °C       | TemperatureMeasurement (0x0402) | Temperature value |

### Notes:

* Raw value conversion:

  * `temperature = value / 100`
* Event-driven (no polling)

---

## humidity.csv

Same mechanism as temperature.

| Column           | Type   | Unit     | Source (Zigbee)           | Description     |
| ---------------- | ------ | -------- | ------------------------- | --------------- |
| timestamp        | string | ISO 8601 | System                    | Event timestamp |
| ieee             | string | —        | Device                    | Device IEEE     |
| model            | string | —        | Device                    | Device model    |
| humidity_percent | float  | %        | RelativeHumidity (0x0405) | Humidity value  |

### Notes:
* Conversion:
  * `humidity = value / 100`
* Typically uses Zigbee reporting

## binary_sensors.csv

Logged in `IASZoneListener.cluster_command()`.

| Column       | Type   | Unit     | Source (Zigbee)             | Description            |
| ------------ | ------ | -------- | --------------------------- | ---------------------- |
| timestamp    | string | ISO 8601 | System                      | Event timestamp        |
| ieee         | string | —        | Device                      | Device IEEE            |
| manufacturer | string | —        | Basic cluster               | Manufacturer           |
| model        | string | —        | Basic cluster               | Model                  |
| sensor_type  | string | —        | Derived                     | Sensor classification  |
| state        | int    | 0/1      | IAS Zone (0x0500)           | Triggered state        |
| tamper       | int    | 0/1      | IAS Zone                    | Tamper flag            |
| battery      | int/"" | %        | PowerConfiguration (0x0001) | Battery level          |
| lqi          | int/"" | —        | Device                      | Link Quality Indicator |

---

### Sensor Type Logic

| Condition (model string)     | Type    |
| ---------------------------- | ------- |
| contains "PIR" or "Motion"   | motion  |
| contains "Smoke" or "TS0205" | smoke   |
| otherwise                    | contact |


### IAS Zone Bit-Level Mapping

| Bit   | Meaning         |
| ----- | --------------- |
| bit 0 | Alarm (trigger) |
| bit 2 | Tamper          |

## Architectural Notes

* **Mains-powered devices → polling model as periodic loop (~60 seconds)**
* **Sensors → event-driven model**
  * Zigbee reporting
  * IAS callbacks

## Practical Notes
The CSV structure is designed to be easily imported into:
  * pandas
  * Excel
  * time-series systems (after transformation)
* Support time-series analysis
* Remain vendor-agnostic across Zigbee devices

## Zigbee Internals (Deep Dive)

### Device Lifecycle

1. Device joins the network
2. `device_initialized` is triggered
3. Configuration phase:
   * binding
   * reporting setup
   * IAS enrollment
4. Runtime:
   * polling for mains devices
   * event callbacks for sensors

### Electrical Measurement(#electric_measurement)

Cluster:

```
ElectricalMeasurement (0x0B04)
```

Raw values are scaled using:

* multiplier
* divisor

---

### Metering (Energy)

Cluster:

```
Metering (0x0702)
```

Attribute:

* `current_summ_delivered`

Converted to kWh using:

* multiplier
* divisor

---

### IAS Zone (Security Sensors)

Handled via:

```
ZoneStatusChangeNotification
```

Bit flags:

* bit 0 → alarm
* bit 2 → tamper

---

### Battery Model

Voltage:

```
V = raw / 10
```

Estimated percentage:

```
(V - 2.4) / 0.6 * 100
```

This is a simplified linear approximation.

## Troubleshooting
### Logs are delayed or not visible
* Running in a non-interactive environment
* Output buffering
### Device does not respond
* Device may be sleeping (battery-powered)
* Reporting may not be configured
### Cluster read errors
* Some devices do not fully comply with Zigbee specifications

## Testing Setup (~/.bashrc)

For easier testing, add the following aliases to your `~/.bashrc`:

```bash
alias zb-list='echo "list" > /tmp/zigbee_cmd && cat /tmp/zigbee_resp'
alias zb-permit='echo "permit 60" > /tmp/zigbee_cmd'
alias zb-toggle='echo "toggle $1" > /tmp/zigbee_cmd'
alias zb-status='echo "status $1" > /tmp/zigbee_cmd'
```

Apply changes:

```bash
source ~/.bashrc
```

Usage:

```bash
zb-list
zb-status <ieee>
zb-toggle <ieee>
```
### Technical note: Limitations of device presence detection in Zigbee networks

This document supplements the `zigbee_manager.py` implementation by explaining why an instant `device_left` event is not generated when a device goes offline and why entries sometimes persist in the device list.

#### 1. General principle: sleepy End Devices and parent timeouts

Most battery‑powered sensors (motion, door/window, smoke) are implemented as **End Devices (ED)** that enter a deep sleep most of the time.  
The router or coordinator that acts as their **parent** maintains a child table for each associated ED. When the device is asleep, the parent does **not** actively poll it.  
Instead, the parent removes a child from its table only when a **timeout** occurs – i.e. the device has not sent a single frame for a configured duration (often several hours).  
Consequently, instantly removing the battery does **not** trigger any message, and the parent cannot know that the device has physically left the network. Only after the timeout expires might the parent consider the device gone.

#### 2. Effect on `device_left` events

In the Zigbee stack used by `zigbee_manager.py`, the `device_left` callback is invoked **only when the parent device explicitly reports that the child has been removed** (either because the child sent a “leave” command or the parent’s timeout triggered).  
Because the timeout is deliberately long to accommodate battery‑powered sensors, pulling the battery never produces an immediate `device_left`.  
When the sensor is re‑inserted, it typically (re)joins the network, often obtaining a **new NWK address**. The old NWK entry may linger in the application’s `app.devices` dictionary until the parent’s timeout officially cleans it up.  
This is normal Zigbee behaviour, not a bug in the code.

#### 3. Mains‑powered devices (smart plugs) behave similarly for different reasons

A mains‑powered device does not sleep. However, when its power supply is abruptly cut, it cannot send a final message either.  
The coordinator discovers the loss of connection only when it attempts to communicate with the device (e.g., during periodic status queries) and fails to receive a response.  
Depending on network settings, this detection can take from tens of seconds up to several minutes. Until then, the device remains listed as reachable.

#### 4. Implications for `zigbee list` and `status` commands

- `zigbee list` shows **all devices that have ever been known to the network** – it is not a real‑time “online/offline” indicator.
- The `status` command for a sensor returns the **last recorded IAS Zone state** stored in `devices_state`; this does **not** indicate current connection status.
- For a smart plug, `status` returns “No measurement data available” as soon as the device stops responding. The entry in the device list, however, persists until the network stack explicitly purges it (which may happen after a timeout or after several failed communication attempts).

#### 5. Summary of observations from the test setup

| Scenario | Observed behavior |
|----------|-------------------|
| Removing battery from a PIR sensor | No `device_left` event; after battery re‑insertion the sensor (re)joins, sometimes with a new NWK address. |
| Unplugging a smart plug | No immediate `device_left`; `status` returns “No measurement data available” because attribute reads fail. The device remains listed. |
| Both cases | Devices are still shown in `zigbee list` because the list reflects the network’s memory, not real‑time reachability. |

#### 6. Practical conclusions for system design

- **Do not rely on `device_left` for instant presence detection** – it is never generated immediately for battery‑powered devices and may be delayed for mains‑powered ones.
- For applications that require fast “offline” detection, implement a **heartbeat mechanism** (e.g., periodic status polls) and consider a device “unreachable” after a configurable number of consecutive failures.
- The current CSV logging and FIFO command architecture already provide a solid basis for such extensions without library changes.
- The observed limitations are **inherent to the Zigbee protocol** and cannot be circumvented by switching to another EZSP‑based library.

#### 7. Related code references

- Device list → `zigbee_manager.py` → `ZigbeeEnergyManager.list_devices`
- `device_left` implementation → `zigbee_manager.py` → `ZigbeeListener.device_left`
- Last IAS state storage → `zigbee_manager.py` → `IASZoneListener.cluster_command`
- Status command logic → `zigbee_manager.py` → `ZigbeeEnergyManager.get_device_status`

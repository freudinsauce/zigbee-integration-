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

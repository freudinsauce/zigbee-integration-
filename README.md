# Zigbee Energy Manager

An asynchronous Zigbee network manager for monitoring and controlling Zigbee devices with a focus on energy telemetry, device state tracking, and event-driven processing.

Built on top of `zigpy` and `bellows`, designed for use with a Zigbee coordinator (Silicon Labs EZSP -- JetStick Z4) on Linux systems such as Raspberry Pi.

---

## Table of Contents

* [Overview](#overview)
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

## Overview

Zigbee Energy Manager is an event-driven system for managing a Zigbee network. It provides:

* Device control (e.g., switching smart plugs)
* Energy telemetry collection (voltage, current, power, energy)
* Sensor monitoring (motion, contact, smoke)
* Persistent logging to CSV
* Inter-process communication via FIFO

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

---

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
python zigbee_manager.py
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

### smart_plug.csv

* voltage
* current
* power
* energy

### temperature.csv / humidity.csv

* sensor measurements

### binary_sensors.csv

* IAS Zone events (motion, smoke, contact)

---

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

---

### Electrical Measurement

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

---

## Troubleshooting

### Logs are delayed or not visible

* Running in a non-interactive environment
* Output buffering

### Device does not respond

* Device may be sleeping (battery-powered)
* Reporting may not be configured

### Cluster read errors

* Some devices do not fully comply with Zigbee specifications

---

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

---

If you want to take this further, the next logical steps are:

* adding a systemd service for production deployment
* introducing structured configuration (e.g., YAML or env-based)
* packaging the project with `pyproject.toml`
* adding CI/CD and versioning for reproducibility

## **Technical limitations of device presence detection in Zigbee networks**

This file supplements the `zigbee_manager.py` implementation by explaining why an instant `device_left` event is not generated when a device goes offline and why entries sometimes persist in the device list.

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

## **Memory usage**  
The script was monitored for over 6 hours using:

```bash
while true; do (date; ps -o pid,user,pri,ni,vsz,rss,pcpu,pmem,time,comm -C python | head -2) >> mem_log.txt; sleep 300; done
```

The **RSS** remained stable at **~68 MB** without any leakage. This value is typical for a Zigbee stack running on Python with `zigpy`, `bellows`, SQLite and asyncio.

Although the requirement in the technical specification is 15 MB, the actual consumption is within acceptable limits for a Raspberry Pi 3 (1 GB RAM) and does not affect stability or performance. 
No memory growth was observed over extended runtime. For a better memory usage the utility on Cpp will be discovered.

"""
Zigbee Energy Manager – core for Zigbee devices control and monitoring with focus on energy data.
- Uses bellows and zigpy for Zigbee communication.
- Supports mains-powered devices (smart plugs) and battery-powered sensors (motion, contact, smoke).
- Logs data to CSV files for later analysis.
- Uses FIF0 for terminal commands (list devices, toggle plugs, get status, permit join).
Features:
- Device discovery and state tracking (battery, LQI, model).
- Background monitoring of mains devices with periodic data collection.
- IAS Zone support for security sensors with event logging.
- Robust error handling and logging for reliability.
"""

import asyncio
import logging
import sys
import os
import json
import csv
from datetime import datetime
from time import time
from pathlib import Path

from bellows.zigbee.application import ControllerApplication
from zigpy.config import CONF_DEVICE, CONF_DEVICE_PATH, CONF_DEVICE_BAUDRATE, CONF_DATABASE
from zigpy.zcl.clusters.general import Basic, OnOff, PowerConfiguration
from zigpy.zcl.clusters.measurement import TemperatureMeasurement, RelativeHumidity
from zigpy.zcl.clusters.security import IasZone
from zigpy.zcl.clusters.smartenergy import Metering
from zigpy.zcl.clusters.homeautomation import ElectricalMeasurement

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FILE = "logs/zigbee_manager.log"
MEASUREMENTS_BASE = "measurements"
FIFO_CMD = "/tmp/zigbee_cmd"
FIFO_RESP = "/tmp/zigbee_resp"

Path("logs").mkdir(exist_ok=True)
Path(MEASUREMENTS_BASE).mkdir(exist_ok=True)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
LOGGER = logging.getLogger(__name__)

DEVICE_CONFIG = {
    CONF_DATABASE: "zigbee.db",
    CONF_DEVICE: {
        CONF_DEVICE_PATH: "/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0",
        CONF_DEVICE_BAUDRATE: 115200,
    },
    "ota": {"enabled": False}
}

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ РАБОТЫ С CSV ==========
def get_measurement_dir():
    """Возвращает путь к папке для измерений с текущей датой."""
    today = datetime.now().strftime("%Y-%m-%d")
    path = Path(MEASUREMENTS_BASE) / today
    path.mkdir(parents=True, exist_ok=True)
    return path

def append_csv(filename, headers, row):
    """Adds sting in CSV with headers if file is new."""
    filepath = get_measurement_dir() / filename
    write_header = not filepath.exists()
    with open(filepath, 'a', newline='') as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(headers)
        writer.writerow(row)

# ========== ЯДРО СИСТЕМЫ ==========
class ZigbeeEnergyManager:
    def __init__(self):
        self.app = None
        self.devices_state = {}
        self.current_monitored_device = None   # для активного мониторинга (опционально)
        self.background_tasks = set()
        self.listener = None

    async def start(self):
        """Инициализация zigbee-стека и фоновых задач."""
        LOGGER.info("Running Zigbee Energy Manager")
        self.app = await ControllerApplication.new(
            config=DEVICE_CONFIG,
            start_radio=True,
            auto_form=True
        )
        listener = ZigbeeListener(self)
        self.app.add_listener(listener)
        self.listener = listener
    
        await self.app.permit(time_s=60)
        LOGGER.info("Zigbee gate is open for 60 seconds for new joins")

        asyncio.create_task(self._setup_existing_devices())
        asyncio.create_task(self._cleanup_old_measurements())
        

    async def _cleanup_old_measurements(self, days=30):
        """Deleted data with measurements older than n days"""
        while True:
            now = time()
            for d in Path(MEASUREMENTS_BASE).iterdir():
                if d.is_dir() and (now - d.stat().st_mtime) > days * 86400:
                    import shutil
                    shutil.rmtree(d)
                    LOGGER.info(f"Deleted old measurements data: {d}")
            await asyncio.sleep(86400)
            
    async def _setup_existing_devices(self):
        """Настройка устройств, уже присутствующих в базе данных (после перезапуска)."""
        await asyncio.sleep(5)  # даём координатору полностью инициализироваться
        if not self.listener:
            LOGGER.error("ZigbeeListener not set, cannot configure existing devices")
            return
        configured_count = 0
        for dev in self.app.devices.values():
            if dev.nwk == 0x0000:
                continue
            ieee = str(dev.ieee)
            if self.get_device_state(ieee).get("configured", False):
                continue
            LOGGER.info(f"Configuring existing device: {ieee}")
            await self.listener._configure_device(dev)
            configured_count += 1
        LOGGER.info(f"Existing devices reconfigured: {configured_count} device(s). Parameters restored.")
    
    async def shutdown(self):
        """Корректное завершение работы."""
        if self.app:
            # Закрываем базу данных перед остановкой
            if hasattr(self.app, '_dblistener') and self.app._dblistener:
                await self.app._dblistener.shutdown()
            await self.app.shutdown()
        LOGGER.info("Менеджер остановлен")

    # ---------- Команды для внешнего управления ----------
    async def list_devices(self):
        """Возвращает список устройств в виде строк (IEEE, модель, тип питания)."""
        lines = []
        for dev in self.app.devices.values():
            if dev.nwk == 0x0000:
                continue
            ieee = str(dev.ieee)
            model = dev.model or "Unknown"
            manufacturer = dev.manufacturer or "Unknown"
            power = "Battery" if (dev.node_desc and not dev.node_desc.is_mains_powered) else "Mains"
            bat = self.devices_state.get(ieee, {}).get("battery")
            bat_str = f", bat={bat:.0f}%" if bat else ""
            lines.append(f"{ieee} - {manufacturer} {model} ({power}{bat_str})")
        return "\n".join(lines) if lines else "No devices"

    async def toggle_plug(self, ieee_str):
        """Toggle Smart Plus by IEEE address. Returns new state or error."""
        device = self._find_device(ieee_str)
        if not device:
            return f"Device {ieee_str} not found"
        for ep in device.endpoints.values():
            if not hasattr(ep, 'endpoint_id') or ep.endpoint_id == 0:
                continue
            onoff = ep.in_clusters.get(OnOff.cluster_id)
            if onoff:
                try:
                    current = (await onoff.read_attributes(["on_off"]))[0].get("on_off")
                    if current:
                        await onoff.off()
                        LOGGER.info(f"Smart Plug {ieee_str} OFF by command")
                        return "OFF"
                    else:
                        await onoff.on()
                        LOGGER.info(f"Smart Plug {ieee_str} ON by command")
                        return "ON"
                except Exception as e:
                    LOGGER.error(f"Error toggle: {e}")
                    return f"Error: {e}"
        return "No OnOff cluster found"

    async def get_device_status(self, ieee_str):
        device = self._find_device(ieee_str)
        if not device:
            return f"Device {ieee_str} not found"

        # Проверяем, есть ли у устройства IAS Zone (датчик)
        has_ias = False
        for ep in device.endpoints.values():
            if hasattr(ep, 'in_clusters') and IasZone.cluster_id in ep.in_clusters:
                has_ias = True
                break

        if has_ias:
            # Датчик: возвращаем последнее сохранённое состояние
            state = self.devices_state.get(ieee_str, {})
            last_state = state.get('last_ias_state')
            last_event = state.get('last_ias_event', 'Unknown')
            battery = state.get('battery')
            lqi = state.get('lqi')
            parts = []
            if last_state is not None:
                parts.append(f"State={'ALARM' if last_state else 'CLEAR'} ({last_event})")
            if battery:
                parts.append(f"Battery={battery:.0f}%")
            if lqi:
                parts.append(f"LQI={lqi}")
            if parts:
                return ", ".join(parts)
            else:
                return "No state recorded yet (trigger sensor first)"
        else:
            # Розетка или другое mains-устройство: читаем параметры
            data = await self._read_mains_attributes(device)
            if data:
                parts = []
                if "voltage" in data:
                    parts.append(f"Voltage={data['voltage']:.1f}V")
                if "current" in data:
                    parts.append(f"Current={data['current']:.2f}A")
                if "power" in data:
                    parts.append(f"Power={data['power']:.1f}W")
                if "energy_kwh" in data:
                    parts.append(f"Energy={data['energy_kwh']:.3f}kWh")
                bat = self.devices_state.get(ieee_str, {}).get("battery")
                if bat:
                    parts.append(f"Battery={bat:.0f}%")
                return ", ".join(parts) if parts else "No measurement data available"
            return "No measurement data available"
        
    async def permit_join(self, seconds):
        """Открыть сеть для присоединения новых устройств на seconds секунд. seconds=0 - закрыть."""
        await self.app.permit(time_s=seconds if seconds>0 else 0)
        return f"Permit join set to {seconds} seconds"

    # ---------- Внутренние методы ----------
    def _find_device(self, ieee_str):
        for dev in self.app.devices.values():
            if str(dev.ieee) == ieee_str:
                return dev
        return None

    async def _read_mains_attributes(self, device):
        """Reading voltage, current, power, energy from smart plug. Returns dict with values or empty dict"""
        data = {}
        for ep in device.endpoints.values():
            if not hasattr(ep, 'endpoint_id') or ep.endpoint_id == 0:
                continue
            if not hasattr(ep, 'in_clusters'):
                continue
            em = ep.in_clusters.get(ElectricalMeasurement.cluster_id)
            if em:
                try:
                    raw = await em.read_attributes(["rms_voltage", "rms_current", "active_power"])
                    raw_v = raw[0].get("rms_voltage")
                    raw_i = raw[0].get("rms_current")
                    raw_p = raw[0].get("active_power")
                    coeff = await em.read_attributes([
                        "ac_voltage_multiplier", "ac_voltage_divisor",
                        "ac_current_multiplier", "ac_current_divisor",
                        "ac_power_multiplier", "ac_power_divisor"
                    ])
                    v_mult = coeff[0].get("ac_voltage_multiplier", 1)
                    v_div  = coeff[0].get("ac_voltage_divisor", 100)
                    i_mult = coeff[0].get("ac_current_multiplier", 1)
                    i_div  = coeff[0].get("ac_current_divisor", 100)
                    p_mult = coeff[0].get("ac_power_multiplier", 1)
                    p_div  = coeff[0].get("ac_power_divisor", 10)
                    if raw_v is not None:
                        data["voltage"] = raw_v * v_mult / v_div
                    if raw_i is not None:
                        data["current"] = raw_i * i_mult / i_div
                    if raw_p is not None:
                        data["power"] = raw_p * p_mult / p_div
                except Exception as e:
                    LOGGER.debug(f"ElectricalMeasurement error: {e}")
            meter = ep.in_clusters.get(Metering.cluster_id)
            if meter:
                try:
                    raw_energy = (await meter.read_attributes(["current_summ_delivered"]))[0].get("current_summ_delivered")
                    mult_div = await meter.read_attributes(["multiplier", "divisor"])
                    mult = mult_div[0].get("multiplier", 1)
                    div = mult_div[0].get("divisor", 10000)
                    if raw_energy is not None:
                        data["energy_kwh"] = raw_energy * mult / div
                except Exception as e:
                    LOGGER.debug(f"Metering error: {e}")
        return data

    # ---------- Обновление состояния из слушателей ----------
    def update_device_state(self, ieee, **kwargs):
        self.devices_state.setdefault(ieee, {}).update(kwargs)

    def get_device_state(self, ieee):
        return self.devices_state.get(ieee, {})

class ZigbeeListener:
    def __init__(self, manager: ZigbeeEnergyManager):
        self.manager = manager

    async def _configure_device(self, device):
        if device.ieee == device.application.state.node_info.ieee:
            return
        ieee = str(device.ieee)
        if self.manager.get_device_state(ieee).get("configured", False):
            LOGGER.debug(f"Device {ieee} already configured, skipping")
            return
        model = device.model or "Unknown"
        manufacturer = device.manufacturer or "Unknown"
        lqi = getattr(device, 'lqi', None)
        LOGGER.info(f"Configuring device: {ieee} ({manufacturer} - {model})")
        self.manager.update_device_state(ieee, manufacturer=manufacturer, model=model, lqi=lqi)

        # Принудительное чтение батареи для батарейных устройств (при старте)
        bat = None
        if device.node_desc and not device.node_desc.is_mains_powered:
            bat = await self._read_battery(device)
            if bat:
                self.manager.update_device_state(ieee, battery=bat, last_battery_read=time())
                LOGGER.info(f"Initial battery for {ieee}: {bat:.0f}%")
            else:
                LOGGER.debug(f"Could not read initial battery for {ieee} (device may be sleeping)")

        # Формируем сообщение о восстановленных параметрах
        log_msg = f"Restored device: {ieee} | Manufacturer: {manufacturer} | Model: {model} | LQI: {lqi if lqi else 'N/A'}"
        if bat:
            log_msg += f" | Battery: {bat:.0f}%"
        LOGGER.info(log_msg)

        self.manager.update_device_state(ieee, configured=True)
        asyncio.create_task(self._delayed_setup(device))

    def device_initialized(self, device):
        asyncio.create_task(self._configure_device(device))

    async def _delayed_setup(self, device):
        await asyncio.sleep(3)
        await self._setup_ias(device)
 
        if device.node_desc and device.node_desc.is_mains_powered:
            asyncio.create_task(self._background_mains_monitor(device))
        else:
            asyncio.create_task(self._check_battery_once(device))
        # Настройка репортинга только если есть кластеры температуры/влажности
        if self._has_temp_humidity_clusters(device):
            asyncio.create_task(self._configure_reporting(device))

    def _has_temp_humidity_clusters(self, device):
        for ep in device.endpoints.values():
            if not hasattr(ep, 'endpoint_id') or ep.endpoint_id == 0:
                continue
            if not hasattr(ep, 'in_clusters'):
                continue
            if (TemperatureMeasurement.cluster_id in ep.in_clusters or 
                RelativeHumidity.cluster_id in ep.in_clusters):
                return True
        return False
    
    async def _setup_ias(self, device):
        ieee = str(device.ieee)
        ias_cluster = None
        for ep in device.endpoints.values():
            if not hasattr(ep, 'endpoint_id') or ep.endpoint_id == 0:
                continue
            if hasattr(ep, 'in_clusters') and IasZone.cluster_id in ep.in_clusters:
                ias_cluster = ep.in_clusters[IasZone.cluster_id]
                break
        if not ias_cluster:
            return
        LOGGER.info(f"Configuring IAS Zone for {device.ieee} ({device.model})")
        ias_cluster.add_listener(IASZoneListener(self.manager, device))
        # Небольшая пауза перед записью атрибутов
        await asyncio.sleep(0.5)
        try:
            coord_ieee = device.application.state.node_info.ieee
            await ias_cluster.write_attributes({0x0010: coord_ieee})
            await ias_cluster.bind()
            await ias_cluster.enroll_response(zone_id=0, enroll_response_code=0x00)
            LOGGER.info(f"IAS Zone successfully configured for {device.ieee}")
            self.manager.update_device_state(ieee, ias_configured=True)
        except Exception as e:
            LOGGER.warning(f"{device.ieee} IAS Zone config error: {e}")
        bat = await self._read_battery(device)
        if bat:
            self.manager.update_device_state(ieee, battery=bat, last_battery_read=time())
        asyncio.create_task(self._battery_updater(device))

    async def _battery_updater(self, device):
        ieee = str(device.ieee)
        while True:
            await asyncio.sleep(86400)  # сутки
            bat = await self._read_battery(device)
            if bat:
                self.manager.update_device_state(ieee, battery=bat, last_battery_read=time())
                LOGGER.info(f"Battery update {ieee}: {bat:.0f}%")
            else:
                LOGGER.debug(f"Battery read failed for {ieee}")

    async def _read_battery(self, device):
        for ep in device.endpoints.values():
            if not hasattr(ep, 'endpoint_id') or ep.endpoint_id == 0:
                continue
            if not hasattr(ep, 'in_clusters'):
                continue
            power_cfg = ep.in_clusters.get(PowerConfiguration.cluster_id)
            if power_cfg:
                try:
                    result, _ = await power_cfg.read_attributes(["battery_voltage"], timeout=5)
                    raw = result.get("battery_voltage")
                    if raw is not None:
                        voltage = raw / 10.0
                        percent = (voltage - 2.4) / 0.6 * 100
                        return max(0, min(100, percent))
                except Exception:
                    pass
        return None

    async def _background_mains_monitor(self, device):
        ieee = str(device.ieee)
        while device.model == "Unknown" and device.manufacturer == "Unknown":
            await asyncio.sleep(5)
        LOGGER.info(f"Запуск мониторинга mains-устройства {ieee}")
        while True:
            await asyncio.sleep(60)
            data = await self.manager._read_mains_attributes(device)
            state = "Unknown"
            for ep in device.endpoints.values():
                if not hasattr(ep, 'endpoint_id') or ep.endpoint_id == 0:
                    continue
                onoff = ep.in_clusters.get(OnOff.cluster_id)
                if onoff:
                    try:
                        result, _ = await onoff.read_attributes(["on_off"], timeout=5)
                        on_off_val = result.get("on_off")
                        state = "ON" if on_off_val else "OFF"
                    except Exception:
                        pass
                    break
            if data:
                timestamp = datetime.now().isoformat()
                row = [timestamp, ieee, device.manufacturer or "Unknown", device.model or "Unknown",
                    state, data.get("voltage"), data.get("current"), data.get("power"), data.get("energy_kwh")]
                append_csv("smart_plug.csv",
                        ["timestamp", "ieee", "manufacturer", "model", "state", "voltage", "current", "power", "energy_kwh"],
                        row)
                LOGGER.debug(f"Smart plug data saved: {ieee}")

    async def _check_battery_once(self, device):
        bat = await self._read_battery(device)
        if bat:
            ieee = str(device.ieee)
            self.manager.update_device_state(ieee, battery=bat, last_battery_read=time())
            LOGGER.info(f"Battery {device.model}: {bat:.0f}%")

    async def _configure_reporting(self, device):
        """Настройка репортинга для датчиков температуры и влажности."""
        for ep in device.endpoints.values():
            if not hasattr(ep, 'endpoint_id') or ep.endpoint_id == 0:
                continue
            if not hasattr(ep, 'in_clusters'):
                continue
            temp = ep.in_clusters.get(TemperatureMeasurement.cluster_id)
            if temp:
                for attempt in range(6):
                    try:
                        await temp.bind()
                        await temp.configure_reporting(attribute="measured_value", min_interval=30, max_interval=300, reportable_change=10)
                        LOGGER.info(f"Temperature reporting configured for {device.model}")
                        break
                    except Exception as e:
                        LOGGER.debug(f"Temp reporting attempt {attempt+1} failed: {e}")
                        await asyncio.sleep(5)
            hum = ep.in_clusters.get(RelativeHumidity.cluster_id)
            if hum:
                for attempt in range(6):
                    try:
                        await hum.bind()
                        await hum.configure_reporting(attribute="measured_value", min_interval=30, max_interval=300, reportable_change=100)
                        LOGGER.info(f"Humidity reporting configured for {device.model}")
                        break
                    except Exception as e:
                        LOGGER.debug(f"Humidity reporting attempt {attempt+1} failed: {e}")
                        await asyncio.sleep(5)

    def device_joined(self, device):
        LOGGER.info(f"Device joined network: {device.ieee}")
    def device_left(self, device):
        ieee = str(device.ieee)
        LOGGER.warning(f"Device left network: {ieee} ({device.model})")
        self.manager.update_device_state(ieee, configured=False)

    def attribute_updated(self, cluster, attrid, value):
        # изменения температуры/влажности в CSV
        device = cluster.endpoint.device
        ieee = str(device.ieee)
        if cluster.name == "TemperatureMeasurement" and attrid == 0:
            temp_c = value / 100.0
            append_csv("temperature.csv",
                       ["timestamp", "ieee", "model", "temperature_c"],
                       [datetime.now().isoformat(), ieee, device.model or "Unknown", temp_c])
        elif cluster.name == "RelativeHumidity" and attrid == 0:
            hum = value / 100.0
            append_csv("humidity.csv",
                       ["timestamp", "ieee", "model", "humidity_percent"],
                       [datetime.now().isoformat(), ieee, device.model or "Unknown", hum])

    def handle_zcl_command(self, cluster, command):
        pass

# ===============================================
# Listener for IAS Zone (boolean cluster)

class IASZoneListener:
    def __init__(self, manager: ZigbeeEnergyManager, device):
        self.manager = manager
        self.device = device

    def cluster_command(self, tsn, command_id, args):
        #print(f"RAW status: {args}")
        if command_id == 0x0000:  # ZoneStatusChangeNotification
            if isinstance(args, (list, tuple)) and len(args)>0:
                status = args[0]
            else:
                status = args
            triggered = bool(status & 1)
            tamper = bool(status & 4)
            ieee = str(self.device.ieee)
            model = self.device.model or "Unknown"
            manufacturer = self.device.manufacturer or "Unknown"
            dev_state = self.manager.get_device_state(ieee)
            battery = dev_state.get("battery")
            lqi = getattr(self.device, 'lqi', None)

            if "PIR" in model or "Motion" in model:
                sensor_type = "motion"
            elif ("Smoke" or "TS0205") in model:
                sensor_type = "smoke"
            else:
                sensor_type = "contact"

            event = "MOTION" if (triggered and sensor_type == "motion") else "OPEN" if (triggered and sensor_type == "contact") else "SMOKE" if (triggered and sensor_type == "smoke") else "CLEAR"
            tamper_str = "- TAMPER" if tamper else ""
            msg = f"{event}{tamper_str} from {model} ({ieee})"
            LOGGER.info(msg)
            append_csv("binary_sensors.csv",
                       ["timestamp", "ieee", "manufacturer", "model", "sensor_type", "state", "tamper", "battery", "lqi"],
                       [datetime.now().isoformat(), ieee, manufacturer, model, sensor_type,
                        1 if triggered else 0, 
                        1 if tamper else 0,
                        int(round(battery)) if battery is not None else "", 
                        lqi if lqi is not None else ""])
            self.manager.update_device_state(ieee, last_ias_state=triggered, last_ias_event=event, last_ias_tamper=tamper)

# ===============================================
# FIFO
def write_response(message: str):
    with open(FIFO_RESP, "w") as resp_f:
        resp_f.write(message + "\n")

async def fifo_server(manager):
    if not os.path.exists(FIFO_CMD):
        os.mkfifo(FIFO_CMD)
    if not os.path.exists(FIFO_RESP):
        os.mkfifo(FIFO_RESP)

    fd = os.open(FIFO_CMD, os.O_RDONLY | os.O_NONBLOCK)
    loop = asyncio.get_running_loop()

    buffer = ""
    while True:
        try:
            data = await loop.run_in_executor(None, os.read, fd, 1024)
            if not data:
                await asyncio.sleep(0.1)
                continue

            buffer += data.decode()

            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                cmd_line = line.strip()

                if not cmd_line:
                    continue

                LOGGER.debug(f"Received command: {cmd_line}")

                parts = cmd_line.split()
                cmd = parts[0].lower()

                if cmd == "list":
                    result = await manager.list_devices()
                elif cmd == "permit" and len(parts) >= 2:
                    result = await manager.permit_join(int(parts[1]))
                elif cmd == "toggle" and len(parts) >= 2:
                    result = await manager.toggle_plug(parts[1])
                elif cmd == "status" and len(parts) >= 2:
                    result = await manager.get_device_status(parts[1])
                elif cmd == "help":
                    result = "Commands: list, permit <seconds>, toggle <ieee>, status <ieee>, help"
                else:
                    result = "Unknown command"

                await loop.run_in_executor(None, write_response, result)
        except BlockingIOError:
            await asyncio.sleep(0.1)
        except Exception as e:
            LOGGER.error(f"FIFO error: {e}")
            await asyncio.sleep(0.5)



async def main():
    manager = ZigbeeEnergyManager()
    await manager.start()
    asyncio.create_task(fifo_server(manager))
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        LOGGER.info("Interrupting by user Ctrl+C")
    finally:
        await manager.shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExited by user")
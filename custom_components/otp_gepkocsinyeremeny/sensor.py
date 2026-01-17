"""
OTP Bank Gépkocsinyeremény betét ellenőrző integráció.
"""
import logging
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONF_NAME, DEFAULT_NAME

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    """Szenzor beállítása."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([OTPSensor(coordinator, entry)])

class OTPSensor(CoordinatorEntity, SensorEntity):
    """Fő szenzor."""
    _attr_has_entity_name = True
    
    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._entry = entry
        self._entry_name = entry.data.get(CONF_NAME, entry.title) or DEFAULT_NAME
        self._attr_unique_id = f"otp_sensor_{entry.entry_id}"
        self._attr_name = None  # Az eszköz nevét használja
        self._attr_icon = "mdi:car-convertible"

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=self._entry_name,
            manufacturer="OTP Bank",
        )

    @property
    def state(self):
        return self.coordinator.data.get("nyeremenyek", 0)

    @property
    def extra_state_attributes(self):
        return self.coordinator.data
    
    @property
    def available(self):
        return self.coordinator.last_update_success

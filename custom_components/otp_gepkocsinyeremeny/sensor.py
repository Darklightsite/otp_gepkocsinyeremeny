"""
OTP Bank Gépkocsinyeremény betét ellenőrző integráció.
"""
import logging
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    """Szenzor beállítása."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([OTPSensor(coordinator)])

class OTPSensor(CoordinatorEntity, SensorEntity):
    """Fő szenzor."""
    _attr_has_entity_name = True
    
    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_unique_id = "otp_nyeremenyek_sensor"
        self._attr_name = None # Az eszköz nevét használja
        self._attr_icon = "mdi:car-convertible"

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, "otp_main")},
            name="OTP Betétek",
            manufacturer="OTP Bank",
        )

    @property
    def state(self):
        return self.coordinator.data["nyeremenyek"]

    @property
    def extra_state_attributes(self):
        return self.coordinator.data
    
    @property
    def available(self):
        return self.coordinator.last_update_success

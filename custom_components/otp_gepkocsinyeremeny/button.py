async def async_setup_entry(hass, entry, async_add_entities):
    """Gomb beállítása."""
    from .const import DOMAIN
    if DOMAIN in hass.data and entry.entry_id in hass.data[DOMAIN]:
        coordinator = hass.data[DOMAIN][entry.entry_id]
        async_add_entities([OTPRefreshButton(coordinator)])

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity import DeviceInfo
from .const import DOMAIN

class OTPRefreshButton(ButtonEntity):
    """Gomb a manuális frissítéshez."""
    _attr_has_entity_name = True
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._attr_unique_id = "otp_refresh_button"
        self._attr_name = "Adatbázis Frissítése"

    @property
    def device_info(self):
        return DeviceInfo(
            identifiers={(DOMAIN, "otp_main")},
            name="OTP Betétek",
        )

    async def async_press(self) -> None:
        """Gomb megnyomása."""
        await self.coordinator.async_request_refresh()

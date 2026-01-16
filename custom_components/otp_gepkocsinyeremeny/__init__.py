from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .const import DOMAIN, CONF_NUMBERS
from .coordinator import OTPCoordinator

PLATFORMS = ["sensor", "button"]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Integráció beállítása."""
    hass.data.setdefault(DOMAIN, {})
    
    numbers = entry.data.get(CONF_NUMBERS, "")
    coordinator = OTPCoordinator(hass, numbers)
    await coordinator.async_config_entry_first_refresh()
    
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(update_listener))
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Eltávolítás."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok

async def update_listener(hass: HomeAssistant, entry: ConfigEntry):
    """Ha módosítod a számokat, újratölt."""
    await hass.config_entries.async_reload(entry.entry_id)

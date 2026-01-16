import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from .const import DOMAIN, CONF_NUMBERS

class OtpConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Kezdeti beállítás."""
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            return self.async_create_entry(title="OTP Betétek", data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_NUMBERS, default=""): str
            }),
            errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OtpOptionsFlowHandler(config_entry)

class OtpOptionsFlowHandler(config_entries.OptionsFlow):
    """Módosítás menü."""

    def __init__(self, config_entry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            self.hass.config_entries.async_update_entry(
                self.config_entry, data=user_input
            )
            return self.async_create_entry(title="", data=None)

        current_numbers = self.config_entry.data.get(CONF_NUMBERS, "")
        
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_NUMBERS, default=current_numbers): str
            })
        )

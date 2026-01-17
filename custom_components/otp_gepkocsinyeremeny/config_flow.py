import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from .const import DOMAIN, CONF_NUMBERS, CONF_NAME, DEFAULT_NAME

class OtpConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Kezdeti beállítás."""
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            name = user_input.get(CONF_NAME, DEFAULT_NAME)
            return self.async_create_entry(title=name, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_NUMBERS, default=""): str
            }),
            errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OtpOptionsFlowHandler()


class OtpOptionsFlowHandler(config_entries.OptionsFlow):
    """Módosítás menü."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            self.hass.config_entries.async_update_entry(
                self.config_entry, 
                title=user_input.get(CONF_NAME, self.config_entry.title),
                data=user_input
            )
            return self.async_create_entry(title="", data=None)

        current_name = self.config_entry.data.get(CONF_NAME, self.config_entry.title)
        current_numbers = self.config_entry.data.get(CONF_NUMBERS, "")
        
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_NAME, default=current_name): str,
                vol.Required(CONF_NUMBERS, default=current_numbers): str
            })
        )

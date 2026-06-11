"""Config flow for the Inventory integration."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN


class InventoryConfigFlow(ConfigFlow, domain=DOMAIN):
    """Single-instance setup. No options to configure at install time."""

    VERSION = 1

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            return self.async_create_entry(title="Inventory", data={})

        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

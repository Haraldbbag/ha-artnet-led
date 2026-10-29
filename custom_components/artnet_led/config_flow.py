"""Config flow: fixtures are configured in YAML, the entry only groups them as devices."""
from __future__ import annotations

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN


class ArtnetLedConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_import(self, import_data) -> ConfigFlowResult:
        await self.async_set_unique_id("fixtures")
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="DMX fixtures (YAML)", data={})

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        return self.async_abort(reason="yaml_only")

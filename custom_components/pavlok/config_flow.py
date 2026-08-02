"""Config flow for discovered Pavlok 3 devices."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ADDRESS
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, LOCAL_NAME_PREFIX


class PavlokConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a Pavlok from its non-rotating BLE address."""

    VERSION = 1

    async def async_step_bluetooth(
        self, discovery_info: bluetooth.BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a Home Assistant Bluetooth discovery."""
        if not discovery_info.name.startswith(LOCAL_NAME_PREFIX):
            return self.async_abort(reason="not_pavlok")
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_confirm(
            {CONF_ADDRESS: discovery_info.address, "name": discovery_info.name}
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer a manual address form; discovery remains the preferred path."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS].upper()
            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()
            service_info = bluetooth.async_last_service_info(self.hass, address, True)
            name = service_info.name if service_info else "Pavlok"
            if service_info and not name.startswith(LOCAL_NAME_PREFIX):
                return self.async_show_form(
                    step_id="user",
                    data_schema=_ADDRESS_SCHEMA,
                    errors={"base": "not_pavlok"},
                )
            return self.async_create_entry(
                title=name, data={CONF_ADDRESS: address, "name": name}
            )
        return self.async_show_form(step_id="user", data_schema=_ADDRESS_SCHEMA)

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a BLE-discovered device."""
        if user_input is not None:
            return self.async_create_entry(
                title=self.context["title_placeholders"]["name"], data=user_input
            )
        return self.async_show_form(
            step_id="confirm",
            description_placeholders=self.context["title_placeholders"],
        )

    @staticmethod
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return PavlokOptionsFlow(config_entry)


_ADDRESS_SCHEMA = vol.Schema({vol.Required(CONF_ADDRESS): cv.string})


class PavlokOptionsFlow(OptionsFlow):
    """Keep safety-related preferences explicit and per config entry."""

    def __init__(self, config_entry) -> None:
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "keep_connected",
                        default=self.config_entry.options.get("keep_connected", True),
                    ): cv.boolean,
                }
            ),
        )

"""Conversation agent "Hisho": Home Assistant's Assist hands Hisho what its own commands can't do.

Use it in an Assist pipeline with "prefer handling commands locally" on: HA answers "turn on the
kitchen lights" itself, and the rest comes here. Hisho answers with Home Assistant's own tools (the
entities exposed to Assist) and, for admins, Hisho's tools too; the model follows the phrasing
("think about it", "think hard", "think really hard"). See Hisho's docs/ha-assistant.md.
"""

from __future__ import annotations

from homeassistant.components import conversation
from homeassistant.components.conversation import AssistantContent, ChatLog, ConversationEntity
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import HishoActionError, HishoError
from .coordinator import HishoConfigEntry, HishoCoordinator
from .entity import HishoEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HishoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the Hisho agent."""
    async_add_entities([HishoAgent(entry.runtime_data)])


class HishoAgent(HishoEntity, ConversationEntity):
    """Hisho, as an Assist conversation agent."""

    _attr_translation_key = "assistant"

    def __init__(self, coordinator: HishoCoordinator) -> None:
        """Set up the agent."""
        super().__init__(coordinator, "assistant")

    @property
    def supported_languages(self) -> list[str] | str:
        """Hisho answers in any language."""
        return MATCH_ALL

    async def _async_handle_message(
        self, user_input: conversation.ConversationInput, chat_log: ChatLog
    ) -> conversation.ConversationResult:
        """Ask Hisho, say its answer."""
        user = {"id": None, "name": "Home Assistant", "is_admin": True}  # an automation or script
        if user_input.context.user_id:
            found = await self.hass.auth.async_get_user(user_input.context.user_id)
            user = {"id": user_input.context.user_id, "name": found.name if found else "someone",
                    "is_admin": bool(found and found.is_admin)}
        try:
            data = await self.coordinator.client.async_converse(
                {"text": user_input.text, "conversation_id": chat_log.conversation_id,
                 "language": user_input.language, "user": user, "area": self._area(user_input.device_id)}
            )
            reply = str(data.get("reply") or "")
        except HishoActionError as err:
            reply = f"Hisho can't answer that here: {err.detail}"
        except HishoError as err:
            reply = f"I can't reach Hisho right now ({err or type(err).__name__})."
        chat_log.async_add_assistant_content_without_tools(
            AssistantContent(agent_id=user_input.agent_id, content=reply)
        )
        return conversation.async_get_result_from_chat_log(user_input, chat_log)

    def _area(self, device_id: str | None) -> str | None:
        """The room the request came from (the satellite's area), if HA knows it."""
        if not device_id:
            return None
        device = dr.async_get(self.hass).async_get(device_id)
        area_id = device.area_id if device else None
        area = ar.async_get(self.hass).async_get_area(area_id) if area_id else None
        return area.name if area else None

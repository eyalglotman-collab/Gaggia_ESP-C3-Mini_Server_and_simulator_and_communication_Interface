"""Brew/Home protocol bootstrap helpers for simulator <-> client exchange."""

from __future__ import annotations

from typing import Callable

from server.communication.data_structures_lcd_controller import (
    LCD_CONTROLLER_BREW_SCHEMA_NAME,
    LCDControllerProfileSummary,
)

LCD_PROTOCOL_CMD_INIT: str = "lcdprotoinit"
LCD_PROTOCOL_CMD_PROFILE_CATALOG_GET: str = "lcdprotoprofilecatalogget"

LCD_PROTOCOL_ACK_PREFIX: str = "LCDProtoAck"
LCD_PROTOCOL_PROFILE_CATALOG_PREFIX: str = "LCDProtoProfileCatalog"


class LCDControllerProtocolBridge:
    """@brief Handle protocol bootstrap commands and publish profile metadata."""

    def __init__(self) -> None:
        self._initialized: bool = False
        self._send_payload_callback: Callable[[str], None] | None = None
        self._profile_provider_callback: Callable[[], list[LCDControllerProfileSummary]] | None = None

    def initialize_hooks(
        self,
        send_payload_callback: Callable[[str], None],
        profile_provider_callback: Callable[[], list[LCDControllerProfileSummary]],
    ) -> None:
        """@brief Register command-send and profile-provider hooks for runtime use."""

        self._send_payload_callback = send_payload_callback
        self._profile_provider_callback = profile_provider_callback
        self._initialized = True

    def is_initialized(self) -> bool:
        """@brief Return whether the bridge has valid runtime hooks registered."""

        return self._initialized

    def handle_client_text_command(self, payload_text: str) -> bool:
        """@brief Process LCD protocol bootstrap commands from client DATA text."""

        if not self._initialized:
            return False

        normalized = payload_text.strip().lower()
        if not normalized:
            return False

        if normalized.startswith(LCD_PROTOCOL_CMD_INIT):
            self._emit_init_ack()
            self._emit_profile_catalog()
            return True

        if normalized.startswith(LCD_PROTOCOL_CMD_PROFILE_CATALOG_GET):
            self._emit_profile_catalog()
            return True

        return False

    def _emit_payload(self, payload_text: str) -> None:
        """@brief Send one protocol payload through registered runtime hook."""

        if self._send_payload_callback is None:
            return
        self._send_payload_callback(payload_text)

    def _profiles(self) -> list[LCDControllerProfileSummary]:
        """@brief Read latest profile list from registered provider callback."""

        if self._profile_provider_callback is None:
            return []
        return self._profile_provider_callback()

    def _emit_init_ack(self) -> None:
        """@brief Send protocol ACK with schema and profile-count metadata."""

        profile_count = len(self._profiles())
        payload = (
            f"{LCD_PROTOCOL_ACK_PREFIX};schema={LCD_CONTROLLER_BREW_SCHEMA_NAME};"
            f"profiles={profile_count}"
        )
        self._emit_payload(payload)

    def _emit_profile_catalog(self) -> None:
        """@brief Send profile catalog payload in compact `id:name` list format."""

        profiles = self._profiles()
        items = "|".join(f"{p.profile_id}:{p.profile_name}" for p in profiles)
        payload = (
            f"{LCD_PROTOCOL_PROFILE_CATALOG_PREFIX};count={len(profiles)};"
            f"items={items}"
        )
        self._emit_payload(payload)

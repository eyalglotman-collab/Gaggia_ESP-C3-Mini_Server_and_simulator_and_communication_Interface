idf_component_register(
    SRCS "bridge_main.c"
    INCLUDE_DIRS "."
    REQUIRES esp_driver_usb_serial_jtag esp_event esp_netif esp_timer esp_wifi nvs_flash
)

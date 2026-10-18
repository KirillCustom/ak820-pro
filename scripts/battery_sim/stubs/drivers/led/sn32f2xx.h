#pragma once
#include <stdint.h>
uint32_t sn32f2xx_led_load(void);
void     sn32f2xx_set_power_scale(uint8_t scale);
uint64_t sn32f2xx_led_drive_take(uint32_t now_ms);

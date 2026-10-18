#pragma once
#include <stdint.h>
#include <stdbool.h>
bool kb_eeconfig_get_batt_level(uint16_t *permille);
void kb_eeconfig_set_batt_level(uint16_t permille);
bool kb_eeconfig_get_power_mode(uint8_t *mode);
void kb_eeconfig_set_power_mode(uint8_t mode);

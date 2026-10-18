#pragma once
#include <stdint.h>
void    display_set_alert(const char *msg);
void    display_set_param_status(const char *s);
void    display_set_backlight_cap(uint8_t cap);
uint8_t display_backlight_effective(void);
uint8_t display_backlight_duty(void);

# The bench power logger's QT Py (scripts/qtpy/code.py): serial only. Without
# this, CircuitPython also enumerates a USB keyboard and mouse, and macOS opens
# its Keyboard Setup Assistant. Runs at power-up and hard reset only.
import usb_hid
import usb_midi

usb_hid.disable()
usb_midi.disable()

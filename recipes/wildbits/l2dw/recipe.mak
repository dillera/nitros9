# WildBits Level 2 DriveWire-oriented recipe defaults.

RECIPE = wildbits_dw
OS9FORMAT_CMD = $(OS9FORMAT_DW)
BOOT_RBF = ddx0
RBF_EXTRA += $(DRIVEWIRE_RBF)
SCF_EXTRA += $(DRIVEWIRE_SCF)
BOOTMODS_EXTRA += $(DRIVEWIRE_BOOTMODS)
CLOCK = clock clock2_dw
FUJINET = 1
WIZFI = 0
BOOT_DIAG = 0

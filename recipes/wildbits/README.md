# Wildbits Build Recipes

This document covers building Wildbits targets from this repository.

## Creating Your Own Recipe (Copy Workflow)

You can clone an existing recipe folder with minimal makefile edits.

Example from [`wildbits/`](./). The template file is [`recipe-template.mak`](recipe-template.mak).

```sh
cp -R l1 myrecipe
cp recipe-template.mak myrecipe/recipe.mak
cd myrecipe
make
```

Only edit `myrecipe/recipe.mak` for common customization:

- `RECIPE` to change output name
- `CMDS_EXTRA` to add disk commands
- `BOOTMODS_EXTRA` to add boot modules
- `AFLAGS_EXTRA` / `LFLAGS_EXTRA` for extra flags

This avoids modifying large shared makefiles.

## Prerequisites

From the repository root, ensure:

- toolchain is on `PATH`: `make`, `lwasm`, `lwlink`, `lwar`, `os9`, `zip`

`NITROS9DIR` is inferred when building inside this tree. It can be overridden for
an unusual checkout layout:

```sh
export NITROS9DIR=/Users/boisy/Projects/coco-shelf/nitros9
```

## Build Directories

- [`l1/`](l1/) builds Wildbits Level 1 disk images
- [`l2/`](l2/) builds Wildbits Level 2 disk images
- [`l1dw/`](l1dw/) builds Wildbits Level 1 DriveWire disk images
- [`l2dw/`](l2dw/) builds Wildbits Level 2 DriveWire disk images
- [`l2_mega/`](l2_mega/) builds an expanded Wildbits Level 2 SD image
- [`l2dw_mega/`](l2dw_mega/) builds an expanded Wildbits Level 2 DriveWire image
- [`feu/`](feu/) builds FEU artifacts (`bootfile`, `booter`, flash packages)

Each build directory keeps intermediate artifacts local:

- `.obj/` object files
- `.lib/` static libraries

## Platform Selection

Supported `PLATFORM` values:

- `k2` (default)
- `jr2`

Use as:

```sh
make PLATFORM=jr2
make PLATFORM=k2
```

## Level 1 Build ([`wildbits/l1`](l1/))

```sh
cd l1
make
```

Primary output:

- `l1_wildbitsk2.dsk` (or `l1_wildbitsjr2.dsk` when `PLATFORM=jr2`)

Useful targets:

- `make all` (same as `make`)
- `make clean`

## Level 1 DriveWire Build ([`wildbits/l1dw`](l1dw/))

```sh
cd l1dw
make
```

Primary output:

- `l1_wildbits_dwjr2.dsk` (or `l1_wildbits_dwk2.dsk`, etc.)

The DriveWire images default to 115200 baud and include `rbdw`, `/X0` through
`/X3`, `scdwv`, `/N`, and the `fn*` FujiNet commands. To build for a different
matching FujiNet or bridge speed:

```sh
make DRIVEWIRE_BAUD=57600
```

## Level 2 Build ([`wildbits/l2`](l2/))

```sh
cd l2
make
```

Primary output:

- `l2_wildbitsk2.dsk` (or `l2_wildbitsjr2.dsk` when `PLATFORM=jr2`)

Useful targets:

- `make all` (same as `make`)
- `make clean`

## Level 2 DriveWire Build ([`wildbits/l2dw`](l2dw/))

```sh
cd l2dw
make
```

Primary output:

- `l2_wildbits_dwjr2.dsk` (or `l2_wildbits_dwk2.dsk`, etc.)

## Jr2 Serial and FujiNet Testing

Wildbits recipe builds disable WizFi by default. This keeps the WizFi Timer-0
interrupt path from locking a Jr2 without a working WizFi interface. To build an
image for hardware that uses WizFi instead, opt in explicitly:

```sh
make WIZFI=1
```

Ordinary Jr2 images also enable a polled `sysgo` boot trace on the system-UART
USB channel at 9600 baud. It does not install an interrupt handler or SCF device.
The trace prints numbered `SG00` through `SG16` markers around directory setup,
screen initialization, startup, AutoEx, and the final shell chain. On macOS:

```sh
screen /dev/cu.usbserial-600SA20462 9600
```

Use `make BOOT_DIAG=0` to omit the trace, or `make BOOT_DIAG_BAUD=19200` to
select another baud rate. DriveWire/FujiNet images always disable this logger
because their `dwio` module owns the same UART.

Jr2 builds skip the startup procedure by default (`RUN_STARTUP=0`) because the
ShellPlus startup child does not terminate reliably on this target. Commands
remain available individually in `CMDS`. Use `make RUN_STARTUP=1` to restore
the traditional `shell startup -p` step when testing that path.

The ordinary `l1` and `l2` images keep the SC16550 driver and `/t0` descriptor
resident in `OS9Boot`, but do not initialize the device automatically. After
the system reaches a shell, initialize `/t0` before the serial smoke test:

```sh
iniz /t0
```

`/t0` defaults to 9600 baud, 8 data bits, no parity, and one stop bit. With a
host terminal attached to the Jr2 system-UART USB channel:

```sh
echo SERIAL-OUT >/t0
```

Use the serial test program or `copy /t0 /term` to test data entering the Jr2.
The Jr2 SC16550 path uses polled reads and writes because its active TX and RX
interrupt service paths lock the machine. Other Wildbits targets retain the
interrupt-driven driver. Pressing Ctrl-C to stop a blocking `copy` can make the
shell print `ERROR #003`; this is the expected `S$Intrpt` termination status.

The `l1dw` and `l2dw` images deliberately omit `/t0`. Their `dwio` module owns
the same UART at `$FE60`; opening `/t0` at the same time would reconfigure or
consume DriveWire traffic.

The Jr2 and USB serial adapter must both be downstream devices of a USB host,
such as a Mac or PC. The host bridges the Jr2 system-UART channel to the adapter
connected to the FujiNet. A hub connected upstream to the Jr2 cannot enumerate
a USB serial adapter. Confirm TTL versus RS-232 electrical levels, voltage,
inversion, ground, and RX/TX crossover before connecting the adapter.

With both host serial devices configured raw at the same baud as the image,
boot a DriveWire image and test:

```sh
dir /x0
fnstatus
```

`dir /x0` validates the DriveWire block path. `fnstatus` additionally validates
the FujiNet transaction path through `/N`.

## Mega Level 2 Builds

```sh
cd l2_mega
make

cd ../l2dw_mega
make
```

Primary outputs:

- `l2_wildbits_megajr2.dsk` for SD boot
- `l2_wildbits_dw_megajr2.dsk` for DriveWire boot

Both mega images add the same expanded software collection as
[`coco3/dw_mega`](../coco3/dw_mega/): the native C compiler, Forth09, the
Infocom interpreter with Zork I-III and Raaka-Tu, and the OS-9 Level 2 BBS.
They require `git`, CMOC, the CMOC OS-9 runtime, `nitros9-apps`, and
`nitros9-languages`; see the CoCo 3 mega recipe documentation for configuration
and command-line overrides.

## FEU Build ([`wildbits/feu`](feu/))

```sh
cd feu
make
```

Primary outputs:

- `bootfile`
- `booter`

Additional FEU targets:

- `make booter`
- `make f0.dsk`
- `make f0.zip`
- `make booter.zip`
- `make flash`
- `make upload`
- `make clean`

FEU disk image name pattern:

- `feu_wildbitsk2.dsk` or `feu_wildbitsjr2.dsk` (when that target is built)

## Notes

- `startup` in FEU includes a build date line when generated.
- Incremental builds are enabled by dependency tracking in makefiles.

## Troubleshooting

- Missing module/source errors: verify `NITROS9DIR` points to a valid NitrOS-9 checkout.
- `os9` command failures: ensure OS-9 tools are installed and accessible on `PATH`.
- Link errors for Wildbits libraries: run `make clean && make` in the active build directory.

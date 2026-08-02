<!-- generated-by: gsd-doc-writer -->
# FujiNet over the Wildbits Jr2 UART

## Overview

This document records the working end-to-end configuration for booting a
Wildbits Jr2 from a FujiNet RS-232 device and issuing FujiNet control commands
from NitrOS-9. The tested path uses the Jr2 UART exposed by USB-C, a Mac-hosted
binary serial bridge, and a USB-to-RS-232 adapter connected to the FujiNet
DB-9 port.

The completed system provides:

- a 115200-baud DriveWire link over the Jr2 UART;
- a DriveWire-hosted NitrOS-9 root disk at `/x0`;
- NitrOS-9 time supplied through the DriveWire clock protocol;
- FujiNet management through the `fn*` commands; and
- automatic host-side serial reconnection when the Jr2 is powered off and
  back on.

This work builds on the interrupt-driven normal UART support documented in
[`uart-arch.md`](uart-arch.md). The DriveWire image deliberately uses its own
polled UART backend because DriveWire must have exclusive ownership of the
serial byte stream.

## Working topology

```text
NitrOS-9 on Wildbits Jr2
  /x0                 DriveWire disk and root filesystem
  /n1                 SCF virtual channel used for FujiNet control
  fnstatus, fnmount... FujiNet command-line tools
        |
        v
  rbdw / scdwv / SS.Fuji
        |
        v
  dwio_serial at $FE60, 115200 baud, 8N1
        |
        v
  Jr2 USB-C channel 2
        |
        v
  scripts/drivewire_serial_bridge.py on the Mac
        |
        v
  USB-to-RS-232 adapter
        |
        v
  FujiNet RS-232 DB-9 port
        |
        v
  FujiNet DriveWire disk service and Fuji control service
```

The Mac is the USB host for both serial endpoints. A USB-C hub attached to the
Jr2 cannot fill this role because the Jr2 USB-C connection is a device
connection, not a general-purpose USB host.

On the hardware used for validation, Jr2 USB channel 2 appeared as
`/dev/cu.usbserial-600SA20462` and the FujiNet-side adapter appeared as
`/dev/cu.usbserial-1440`. These names are examples only; enumerate the current
devices whenever the setup changes:

```sh
ls /dev/cu.*
```

Use a real RS-232 adapter for the FujiNet DB-9 interface. Do not connect an
RS-232 voltage-level port directly to TTL/CMOS UART pins. Whether a straight
or null-modem cable is required depends on the DTE/DCE wiring of the selected
adapter; use the cabling appropriate to that adapter.

## Image composition

The working Level 2 image is:

```text
recipes/wildbits/l2dw/l2_wildbits_dwjr2.dsk
```

The `l2dw` recipe selects the following important pieces:

| Component | Purpose |
| --- | --- |
| `ddx0` | Makes DriveWire drive 0 the NitrOS-9 root device |
| `rbdw`, `/x0`-`/x3` | DriveWire block devices |
| `scdwv`, `/n`-`/n5` | DriveWire virtual serial devices |
| `dwio_serial` | Jr2 `$FE60` UART backend at 115200 baud |
| `clock2_dw` | Gets and sets NitrOS-9 time through DriveWire |
| `fn*` commands and `libfuji` | FujiNet configuration and status operations |
| `utilpak1` | Standard tools expected in the Level 2 image |

The recipe sets `FUJINET=1`, `WIZFI=0`, and `BOOT_DIAG=0`. Boot diagnostics
must remain disabled because diagnostic characters written to the same UART
would corrupt the DriveWire protocol.

`CLOCK` is now an overridable make variable. The normal Wildbits image retains
`clock2_wildbits`, while the DriveWire recipes explicitly select
`clock2_dw`. The DriveWire clock module uses the `dwio` subroutine module and
the standard `OP_TIME`/`OP_SETTIME` messages.

## Changes that made the link reliable

### Correct Jr2 baud divisor

The Jr2 UART clock is 25.175 MHz. At 115200 baud the working divisor is 13:

```text
25175000 / (115200 * 16) = 13 using integer division
```

The earlier rounded expression selected 14. That was close enough for some
traffic but corrupted particular byte patterns, which is fatal to a binary
packet protocol. `dwinit_wildbits_serial.asm` now follows the established
Wildbits serial convention and uses integer division.

### Keep Fuji transactions atomic

FujiNet control replies are raw bytes, not DriveWire virtual-channel packets.
The clock-driven virtual serial poller could otherwise consume a reply between
the request and the caller's read.

`scdwv` therefore implements `SS.Fuji` as one IRQ-masked transaction:

1. Validate the request and response lengths.
2. On Level 2, copy the caller's request into system memory with `F$Move`.
3. Disable interrupts for the wire exchange.
4. Write the raw FujiNet request through `dwio`.
5. Read the raw response before another DriveWire client can use the link.
6. Restore interrupts and copy the response back to the caller.

`lib/fuji.as` builds transactions around `OP_FUJI` (`$E2`), checks command
status with `FUJI$GetError`, and fetches response data with
`FUJI$GetResponse`.

### Use a stable OS-9 path number

The Fuji library now opens `/N1` and records the path number returned by
`I$Open` in its data area. `FBReady`, `FBCmd`, `FBErr`, and `FBRead` all use
that stored byte for `I$SetStt` instead of recovering it from a saved-register
stack frame.

This fixed `ERROR #201` (`E$BPNum`, bad path number). The key diagnostic was
that this direct operation returned normally:

```text
echo X >/n1
```

That proved `/N1`, SCF, and `scdwv` could open and write correctly; the invalid
path was being introduced inside the Fuji library. After the fix, the FujiNet
diagnostic stream showed successful `GET ADAPTER CONFIG` and `GET WIFI STATUS`
commands with `drivewire device error = NONE`, and `fnstatus` returned normally
with adapter data.

### Reconnect after Jr2 power cycles

A one-shot byte relay exits or becomes unusable when macOS removes the Jr2 USB
serial device during power-off. `scripts/drivewire_serial_bridge.py` keeps
running, retries missing endpoints, and resumes forwarding after they
reappear.

When a pair is re-established, the bridge:

- preserves bytes that the newly connected Jr2 may already have queued;
- flushes stale input from a still-connected FujiNet, because it may belong
  to the interrupted session;
- prevents both endpoint arguments from resolving to the same serial device;
- performs complete nonblocking writes; and
- leaves traffic statistics disabled by default so log output cannot delay
  the timing-sensitive bridge loop.

Optional counters can be enabled with `--stats-interval SECONDS`.

## Build and run

### 1. Build the image

With the normal NitrOS-9 build prerequisites and sibling language repository
available:

```sh
make -C recipes/wildbits/l2dw PLATFORM=jr2 all
```

The resulting image is
`recipes/wildbits/l2dw/l2_wildbits_dwjr2.dsk`. The hardware-tested image built
on 2026-08-02 was 1,649,408 bytes with SHA-256:

```text
bbf6071f6832d8453c43aa1efa6c97abfa9c9c55d2ce4526109e7d3a9c8ec181
```

The checksum identifies that test build only and changes whenever the image
contents change.

### 2. Export and mount the disk

Serve the image from a TNFS host visible to FujiNet, configure that host in a
FujiNet host slot, and mount the image read/write in DriveWire device slot 0.

FujiNet keeps an open handle to a mounted image. After rebuilding the file,
explicitly unmount and remount device slot 0 before rebooting the Jr2. Merely
replacing the host file can leave FujiNet reading the old inode, producing a
mix of old and new modules or disk read failures.

### 3. Start the bridge

```sh
python3 scripts/drivewire_serial_bridge.py \
  --jr2 /dev/cu.usbserial-600SA20462 \
  --fujinet /dev/cu.usbserial-1440 \
  --baud 115200
```

Quoted globs may be used when a USB serial name changes across reconnects:

```sh
python3 scripts/drivewire_serial_bridge.py \
  --jr2 '/dev/cu.usbserial-*62' \
  --fujinet '/dev/cu.usbserial-1440'
```

Wait for both `connected` messages and `bridge: ready`. Do not open either
endpoint in a terminal emulator while the bridge owns it; any injected byte
can invalidate a DriveWire request.

### 4. Boot and verify NitrOS-9

The image uses `/x0` as root, so the serial bridge and FujiNet disk must be
ready before the Jr2 boots. A stop at `Loading sector...` normally means that
the DriveWire path or mounted slot 0 is unavailable.

At the NitrOS-9 shell:

```text
dir /x0
date
fnstatus
fnlisthosts
fnlistdevs
```

`dir /x0` verifies block I/O, `date` verifies the DriveWire clock, and
`fnstatus` verifies the complete Fuji control request/status/response path.

## Automated bridge tests

The bridge tests use pseudo-terminals, so they do not require the Jr2 or
FujiNet hardware:

```sh
cd scripts
python3 -m unittest test_drivewire_serial_bridge.py
```

They verify byte-exact traffic in both directions, disabled-by-default traffic
statistics, and recovery when the Jr2 serial device disappears and is
recreated. The three tests passed for the hardware-tested build.

## Troubleshooting history

| Symptom | Cause or diagnostic meaning | Resolution |
| --- | --- | --- |
| Boot remains at `Loading sector...` | `/x0` cannot obtain sectors | Start the bridge and confirm FujiNet DriveWire slot 0 is mounted |
| A rebuilt image boots old or inconsistent modules | FujiNet still has the old TNFS file handle open | Unmount and remount slot 0 after every rebuild |
| `fnstatus` reports `ERROR #221` | A trial dedicated `/fuji` descriptor was not present in the live module directory | Use the existing `/N1` descriptor; the dedicated descriptor experiment was removed |
| `fnstatus` reports `ERROR #201` while `echo X >/n1` works | The Fuji library supplied an invalid path number to `I$SetStt` | Save the `I$Open` result explicitly in `fbpath` |
| `fnstatus` sends only `OP_SERINIT`/`OP_SERSETSTAT` and no Fuji command | Failure occurs before `SS.Fuji` reaches the DriveWire backend | Verify the library path handling and that the current `fnstatus` module is in the mounted image |
| `mdir -e` reports a disk read error | DriveWire read failed or mounted image state is stale | Check bridge connectivity, then remount the image |
| Bridge stops working after Jr2 power-off | One-shot relay retained a dead descriptor | Use `drivewire_serial_bridge.py`, which reopens recreated USB devices |

## Verified result

The following were verified together on the physical Wildbits Jr2 and RS-232
FujiNet setup:

- NitrOS-9 booted from FujiNet DriveWire device `/x0`;
- directory and file data could be read from `/x0`;
- the DriveWire clock returned current date/time data;
- the Jr2 could be powered off and rediscovered without restarting the bridge;
- `/N1` opened and returned normally;
- `fnstatus` completed instead of returning #201/#221 or hanging; and
- FujiNet logged successful adapter-configuration and Wi-Fi-status commands
  with no DriveWire device error.

This is the baseline for the next FujiNet work: exercising the remaining
`fn*` management commands and adding higher-level applications such as
FujiNet-backed time and disk workflows.

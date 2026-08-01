# Wildbits Jr2 UART and FujiNet Architecture

## Status and scope

This document describes the UART changes and hardware behavior tested on the
Wildbits Jr2 on the `fujinetAug` branch. The working design uses the Jr2 UART in
polled mode. It supports two separate disk images:

- the normal SD-rooted image, which exposes the UART as the NitrOS-9 SCF device
  `/t0`; and
- the DriveWire/FujiNet image, which gives the same UART exclusively to
  `dwio_serial` and boots from the DriveWire device `/x0`.

The two modes are intentionally not active at the same time. A UART byte stream
cannot simultaneously carry an ordinary terminal session and framed DriveWire
traffic.

## Physical and software topology

The Jr2 is a USB **device**, not a USB host. A USB-C hub connected to the Jr2
does not let the Jr2 host a USB serial adapter. The Mac (or another computer)
must host both USB endpoints and bridge their byte streams:

```text
Normal serial test

NitrOS-9 program -> /t0 -> sc16550 -> UART $FE60
                                      |
                              Jr2 USB-C channel 2
                                      |
                              macOS USB serial port

DriveWire/FujiNet

/x0 (rbdw) ----\
                +-> dwio_serial -> UART $FE60 -> Jr2 USB-C channel 2 --\
/n  (scdwv) ----/                                                        |
                                                                        +-> Mac byte bridge
FujiNet serial pins <- USB-serial adapter <- macOS USB serial port ------/
```

The external adapter must use the voltage level expected by the FujiNet serial
interface. Do not connect RS-232 voltage levels to TTL/CMOS UART pins. For a
three-wire connection, cross TX and RX and connect ground.

### Observed Jr2 USB channels

On the tested Mac, the Jr2 enumerated four serial channels:

| Jr2 channel | Observed macOS device | Purpose seen during testing |
| --- | --- | --- |
| 0 | `/dev/cu.usbserial-600SA20460` | manager/control |
| 1 | `/dev/cu.usbserial-600SA20461` | debug |
| 2 | `/dev/cu.usbserial-600SA20462` | system UART at `$FE60`; use this for `/t0` and DriveWire |
| 3 | `/dev/cu.usbserial-600SA20463` | auxiliary/other |

The serial-number suffix is machine- and enumeration-dependent. Discover the
current names rather than copying these paths blindly:

```sh
ls /dev/cu.usbserial-*
```

`/dev/cu.Q198E3CB1940046`, which was initially mistaken for the adapter during
testing, is a Bluetooth serial pseudo-device and is not a Jr2 USB channel.
Use a before/after comparison of `/dev/cu.*` when attaching the FujiNet-side
USB-serial adapter.

## UART ownership and image composition

| Property | Normal Jr2 image | DriveWire/FujiNet Jr2 image |
| --- | --- | --- |
| Disk image | `recipes/wildbits/l2/l2_wildbitsjr2.dsk` | `recipes/wildbits/l2dw/l2_wildbits_dwjr2.dsk` |
| Root filesystem | SD card (`/dd`) | DriveWire `/x0` (`/dd` is `ddx0`) |
| UART owner | `sc16550` through SCF | `dwio_serial` |
| User-facing devices | `/t0` | `/x0` through `/x3`, and `/n` through `/n5` |
| Default baud rate | 9600 baud | 115200 baud |
| Boot UART diagnostics | enabled on Jr2 | disabled because DriveWire owns the UART |
| FujiNet commands | not included by default | included (`FUJINET=1`) |

The normal Level 2 boot file contains both `sc16550` and the `/t0` descriptor.
Keeping them resident avoids the runtime `F$Load` path that froze during early
testing after the upstream IOMan changes. The DriveWire boot file contains
`dwio_serial`, `rbdw`, `/x0`-`/x3`, `scdwv`, and `/n`-`/n5`; it does not contain
`sc16550` or `/t0`.

This single-owner rule is a design invariant. Do not add `/t0` to the DriveWire
boot image unless a higher-level multiplexer is also designed and implemented.

## Why the Jr2 driver is polled

The original 16550 driver is interrupt-driven. On the Jr2, initialization can
appear healthy with several interrupt-enable combinations, but the machine
locks when real transmit or receive work reaches the UART ISR. The investigation
separated quiescent initialization from active traffic:

| Probe | Result on hardware |
| --- | --- |
| IER cleared | `iniz /t0` returned |
| RX only, line only, or modem only | `iniz /t0` returned while idle |
| RX+line, RX+modem, or line+modem | `iniz /t0` returned while idle |
| all original interrupt sources | locked during earlier initialization tests |
| THRE transmit interrupt enabled on `SS.Open` | first output locked before the write completed |
| receive interrupt path with incoming data | locked once enough data arrived to trigger RX service |
| polled transmit and receive with IER cleared | bidirectional traffic worked |

The failure is therefore in the active Jr2 interrupt path, not basic UART
addressing, baud generation, or USB transport. The exact ISR/controller failure
remains unresolved. The safe architecture keeps the 16550 Interrupt Enable
Register at zero on Jr2 and performs both directions by polling.

### `sc16550` behavior

The Jr2 conditional implementation in
[`level1/wildbits/modules/sc16550.asm`](level1/wildbits/modules/sc16550.asm)
does the following:

- defines both the base interrupt mask and transmit interrupt mask as zero;
- initializes the UART and clears stale UART state without enabling a UART
  interrupt source;
- polls `LSR_XMIT_EMPTY` for writes, using a bounded wait so a missing
  transmitter-ready indication cannot trap the kernel forever;
- polls `LSR_DATA_AVAIL` for reads and sleeps one tick between empty polls;
- checks pending process signals while waiting, so Ctrl-C can interrupt a read;
  and
- leaves the existing interrupt-driven implementation unchanged for non-Jr2
  platforms.

The driver still registers its IRQ entry and unmasks the Jr2 UART bit at the
system controller. This is harmless while the UART IER remains zero, because
the UART cannot assert one of its interrupt sources. It also minimizes the
platform-specific changes while the root cause is investigated.

The `/t0` descriptor is defined in
[`level1/wildbits/modules/sc16550desc.asm`](level1/wildbits/modules/sc16550desc.asm).
It binds SCF to `sc16550` at `UART.Base`, with 9600-baud, 8-data-bit, no-parity,
one-stop-bit defaults. `UART.Base` is `$FE60` in
[`defs/wildbits.d`](defs/wildbits.d).

### DriveWire behavior

The DriveWire image does not pass traffic through `sc16550`. Its dedicated
backend is assembled from:

- [`dwinit_wildbits_serial.asm`](level1/wildbits/modules/dwinit_wildbits_serial.asm),
  which configures the `$FE60` UART for 8-N-1 and computes a rounded divisor
  from the 25.175 MHz UART clock;
- [`dwread_wildbits_serial.asm`](level1/wildbits/modules/dwread_wildbits_serial.asm),
  which polls for received bytes; and
- [`dwwrite_wildbits_serial.asm`](level1/wildbits/modules/dwwrite_wildbits_serial.asm),
  which polls for transmitter readiness.

DriveWire packet operations mask interrupts while using this backend. The baud
rate defaults to 115200 and can be changed at build time with
`DRIVEWIRE_BAUD`.

### FujiNet command transport

FujiNet commands ride on the DriveWire serial connection rather than `/t0`.
[`level1/modules/scdwv.asm`](level1/modules/scdwv.asm) implements `SS.Fuji` as
an atomic request/response transaction. This is necessary because FujiNet
response bytes are raw serial bytes; allowing the virtual serial poller to run
between the request and response could consume bytes belonging to the command.
On Level 2, caller data is staged through a system buffer and moved with
`F$Move`.

[`lib/fuji.as`](lib/fuji.as) sends the FujiNet opcode and command through `/n`,
then obtains the response and device error through `SS.Fuji`. Command and
status constants are defined in [`defs/drivewire.d`](defs/drivewire.d). The
DriveWire recipe includes the `fn*` utilities when `FUJINET=1`.

## Related boot changes

### USB boot diagnostics

[`level1/wildbits/modules/sysgo.as`](level1/wildbits/modules/sysgo.as) contains
a small polled boot logger for the normal Jr2 image. It initializes the system
UART at 9600 baud and emits checkpoints `SG00` through `SG16`; fatal exits emit
`SGE FATAL`. Transmitter waits are bounded and the logger preserves registers
used by SysGo.

The logger is deliberately disabled in the DriveWire image. Initializing or
printing diagnostics through the UART after `dwio_serial` owns it would corrupt
the DriveWire session.

### Startup and WizFi

Jr2 builds default to `RUN_STARTUP=0` because the startup ShellPlus child did
not reliably terminate during the boot investigation. WizFi support also
defaults off (`WIZFI=0`): its Timer-0 interrupt path locked a Jr2 without a
working WizFi interface. It remains an opt-in build feature, while FujiNet is
enabled in the DriveWire recipe.

These defaults and the module composition are in
[`recipes/wildbits/wildbits.mak`](recipes/wildbits/wildbits.mak), with the
normal and DriveWire image choices in
[`recipes/wildbits/l2/makefile`](recipes/wildbits/l2/makefile) and
[`recipes/wildbits/l2dw/recipe.mak`](recipes/wildbits/l2dw/recipe.mak).

## Build and flash

The sibling `nitros9-languages` checkout was not present for the tested builds,
so `BASIC09=` was supplied explicitly.

Build the normal SD-rooted image:

```sh
make -B -C recipes/wildbits/l2 BASIC09= all
```

Flash this file for `/t0` testing:

```text
recipes/wildbits/l2/l2_wildbitsjr2.dsk
```

Build the DriveWire-rooted FujiNet image:

```sh
make -B -C recipes/wildbits/l2dw BASIC09= all
```

Flash this file only after the host bridge and FujiNet DriveWire disk are ready:

```text
recipes/wildbits/l2dw/l2_wildbits_dwjr2.dsk
```

The DriveWire image expects `/x0` during boot, so it cannot reach a shell if the
host-side serial bridge or the FujiNet disk service is unavailable.

The last hardware-tested artifacts recorded build ID `77d714db`:

| Image | SHA-256 |
| --- | --- |
| normal `l2_wildbitsjr2.dsk` | `72336ee44190f4a7eb7a1933feae4e09aae24e5d30a0715e0e9c2d22b92ce105` |
| DriveWire `l2_wildbits_dwjr2.dsk` | `36e5a7fa00c5e5e5709db1bdc2560bf4f1c02f73f048d1f57b589be2d837bee6` |

These hashes identify that test build only and will change when the image is
rebuilt.

## Normal `/t0` validation

On the Mac, configure and observe Jr2 channel 2 at 9600 baud. Use the current
device name if it differs from this example:

```sh
stty -f /dev/cu.usbserial-600SA20462 9600 cs8 -parenb -cstopb \
  -ixon -ixoff raw -echo
od -An -tx1c /dev/cu.usbserial-600SA20462
```

On the Jr2:

```text
iniz /t0
echo SERIAL-OUT-1234567890 >/t0
```

The tested Mac capture began with the expected bytes:

```text
53 45 52 49 41 4c 2d 4f 55 54 2d 31 32 33 34 35
 S  E  R  I  A  L  -  O  U  T  -  1  2  3  4  5
```

For the reverse direction, start a foreground copy on the Jr2:

```text
copy /t0 /term
```

Then, from the Mac, send `MAC-TO-JR2` followed by carriage return:

```sh
printf 'MAC-TO-JR2\r' > /dev/cu.usbserial-600SA20462
```

The text should appear on the Jr2 display. Ctrl-C stops `copy` and returns to
the shell. The shell may then print `ERROR #003`; NitrOS-9 defines signal 3 as
`S$Intrpt`, so this is the expected child termination status rather than a new
UART failure.

## DriveWire/FujiNet validation

1. Attach the Jr2 and the FujiNet-side USB serial adapter to the Mac.
2. Identify the new adapter device with a before/after comparison of
   `/dev/cu.*`.
3. Configure FujiNet with a disk in DriveWire slot 0.
4. Start a raw, binary-clean bridge at 115200 baud. With `socat` installed by
   Homebrew, the command has this form:

   ```sh
   /opt/homebrew/bin/socat -d -d \
     FILE:/dev/cu.usbserial-600SA20462,b115200,rawer,echo=0 \
     FILE:/dev/cu.FUJINET_ADAPTER,b115200,rawer,echo=0
   ```

5. Boot `l2_wildbits_dwjr2.dsk` on the Jr2.
6. At the NitrOS-9 shell, verify the remote disk and FujiNet command path:

   ```text
   dir /x0
   fnstatus
   ```

Do not run a terminal emulator on either endpoint while `socat` owns it. Any
extra character injected into the stream can invalidate a DriveWire packet.

## Troubleshooting and diagnostic caveats

- If `iniz /t0` works but `echo ... >/t0` never returns, the image may still
  contain the old THRE-interrupt transmit path. Confirm that the boot file was
  rebuilt and reflashed.
- If incoming data freezes an older image, it may have reached the RX FIFO
  trigger and entered the broken receive ISR. Use the fully polled build.
- If `copy /t0 /term` waits with no prompt, it is waiting for input as designed.
  Use Ctrl-C to stop it.
- `ERROR #003` after Ctrl-C is `S$Intrpt`, not an I/O error from the UART.
- The current `irqs` utility produced nonsensical rows such as repeated
  `startup` entries and random addresses on the updated Level 2 layout. Do not
  use that output as evidence that UART IRQ handlers are installed correctly.
- A visible boot build ID does not change until a genuinely new commit is used
  for the rebuild. Rebuilding an unchanged commit produces the same ID.
- The external FujiNet-side adapter was not enumerated during the final `/t0`
  loopback test. Confirm its actual macOS device name before starting the
  bridge.

## Verified behavior and remaining work

The following was verified on physical Jr2 hardware with the normal image:

- the system boots to a shell with `sc16550` and `/t0` resident;
- `iniz /t0` returns normally;
- outbound bytes sent through `/t0` arrive exactly on Jr2 USB channel 2;
- inbound bytes from the Mac appear through `copy /t0 /term`; and
- Ctrl-C interrupts the polled read and returns control to the shell.

The DriveWire boot file composition and image build were verified, but the
end-to-end bridge through the external adapter to a physical FujiNet remains
the next hardware integration step. Other follow-up work is to locate the
underlying Jr2 ISR/controller fault, repair `irqs` for the current Level 2
layout, and revisit the runtime module-load freeze independently of the UART
transport.

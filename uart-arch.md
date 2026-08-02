# Wildbits Jr2 UART and FujiNet Architecture

## Status and scope

This document describes the UART changes and hardware behavior tested on the
Wildbits Jr2. The `uart-fixes` branch restores interrupt-driven `/t0` operation
after first establishing a known-good polled baseline. It supports two separate
disk images:

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

## IRQ failure and repair

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

The failure was therefore in the active Jr2 interrupt path, not basic UART
addressing, baud generation, or USB transport. Source inspection found two
independent problems:

1. `sc16550` registered the 16550 IIR itself with `F$IRQ`. Wildbits devices
   instead register an interrupt-controller pending register, then acknowledge
   their bit in that controller. The UART never cleared `INT_UART` in
   `INT_PENDING_1`, allowing the first active UART event to become an IRQ storm.
2. The transmit ISR waited for `LSR_XMIT_DONE` after every byte while executing
   in IRQ context. This serialized the FIFO and could trap the kernel if `TEMT`
   did not become visible as expected.

The repaired driver registers `INT_PENDING_1` with an active-high `INT_UART`
mask, clears the controller latch on entry, and drains all pending causes from
the UART IIR. The transmit ISR fills up to 15 FIFO positions without waiting
for each byte to finish. It disables the THRE source once the software buffer
is empty.

### `sc16550` behavior

The implementation in
[`level1/wildbits/modules/sc16550.asm`](level1/wildbits/modules/sc16550.asm)
does the following:

- registers `INT_PENDING_1` rather than the UART IIR in the NitrOS-9 polling
  table on Wildbits;
- clears a stale `INT_UART` latch before unmasking the controller;
- acknowledges `INT_UART` when its service routine starts;
- enables receive, line-status, and modem-status interrupts while idle, adding
  THRE only while transmit work is pending;
- buffers both receive and transmit data through the existing SCF queues;
- fills the transmit FIFO without a `TEMT` busy-wait inside the ISR;
- rejects a stale IIR value instead of dispatching it as a real cause; and
- masks and acknowledges the controller before removing its IRQ-table entry
  during termination.

The driver also releases its allocated receive buffer if `F$IRQ` cannot install
the polling entry. Break-error accumulation now tests the LSR break bit with
the correct polarity.

### IRQ table utility

The apparently random IRQ table was a separate build defect. Wildbits Level 2
did not put its platform command directory on the assembler include path.
Shared commands therefore resolved `level1/cmds/defsfile` through the Level 1
platform directory. The Level 1 and Level 2 `irqs` binaries were identical and
the command read user-space bytes as if they were Level 1 system globals.

The Level 2 recipe now resolves `level2/wildbits/defsfile`. Its `irqs` module
copies the Level 2 device and polling tables through the system process DAT
image. Driver lookup also compares the complete 16-bit static-storage pointer,
not only its high byte.

The `/t0` descriptor is defined in
[`level1/wildbits/modules/sc16550desc.asm`](level1/wildbits/modules/sc16550desc.asm).
It binds SCF to `sc16550` at `UART.Base`, with 9600-baud, 8-data-bit, no-parity,
one-stop-bit defaults. `UART.Base` is `$FE60` in
[`defs/wildbits.d`](defs/wildbits.d).

### DriveWire behavior

The DriveWire image does not pass traffic through `sc16550`. Its dedicated
backend is assembled from:

- [`dwinit_wildbits_serial.asm`](level1/wildbits/modules/dwinit_wildbits_serial.asm),
  which configures the `$FE60` UART for 8-N-1 and uses divisor 13 at 115200
  baud with the 25.175 MHz UART clock;
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

[`lib/fuji.as`](lib/fuji.as) sends the FujiNet opcode and command through
`/N1`, then obtains the response and device error through `SS.Fuji`. It keeps
the path number returned by `I$Open` in its data area so every transaction uses
the same valid OS-9 path. Command and status constants are defined in
[`defs/drivewire.d`](defs/drivewire.d). The DriveWire recipe includes the
`fn*` utilities when `FUJINET=1`.

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

Build the normal SD-rooted image (use `BASIC09=` when the sibling languages
checkout is absent):

```sh
make -C recipes/wildbits/l2 BASIC09= all
```

Flash this file for `/t0` testing:

```text
recipes/wildbits/l2/l2_wildbitsjr2.dsk
```

Build the DriveWire-rooted FujiNet image:

```sh
make -C recipes/wildbits/l2dw BASIC09= all
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

The IRQ-repair hardware-test build from 2026-08-01 is:

| Image | SHA-256 |
| --- | --- |
| normal `l2_wildbitsjr2.dsk` | `9accc192ef321eff867db078c88e0c3e53c00512ba2c41057fac1350d7391b37` |

The source changes were still uncommitted for this build, so its visible
boot ID remains the current `uart-fixes` HEAD, `033fbf24`. Use the image hash
above to distinguish it during hardware validation.

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
4. Start the reconnectable, binary-clean bridge at 115200 baud. It remains
   running when the Jr2 is powered off and automatically reopens the Jr2 USB
   serial device when it returns:

   ```sh
   python3 scripts/drivewire_serial_bridge.py \
     --jr2 /dev/cu.usbserial-600SA20462 \
     --fujinet /dev/cu.usbserial-1440
   ```

   Either endpoint can be a quoted glob if macOS does not preserve its exact
   name across reconnects, for example `--jr2 '/dev/cu.usbserial-*62'`.

5. Boot `l2_wildbits_dwjr2.dsk` on the Jr2.
6. At the NitrOS-9 shell, verify the remote disk and FujiNet command path:

   ```text
   dir /x0
   fnstatus
   ```

Do not run a terminal emulator on either endpoint while the bridge owns it.
Any extra character injected into the stream can invalidate a DriveWire
packet.

## Troubleshooting and diagnostic caveats

- If `iniz /t0` works but `echo ... >/t0` never returns, confirm the image has
  the repaired controller acknowledgement and non-blocking TX FIFO fill.
- If incoming data freezes an older image, it may have reached the RX FIFO
  trigger while the controller latch was never acknowledged.
- If `copy /t0 /term` waits with no prompt, it is waiting for input as designed.
  Use Ctrl-C to stop it.
- `ERROR #003` after Ctrl-C is `S$Intrpt`, not an I/O error from the UART.
- A 736-byte `irqs` in a Level 2 image is the incorrectly assembled Level 1
  module. The repaired Level 2 module is 837 bytes in this build and has a
  1,845-byte data area.
- A visible boot build ID does not change until a genuinely new commit is used
  for the rebuild. Rebuilding an unchanged commit produces the same ID.
- The external FujiNet-side adapter was not enumerated during the final `/t0`
  loopback test. Confirm its actual macOS device name before starting the
  bridge.
- After rebuilding a TNFS-served image, unmount and remount its FujiNet device
  slot. FujiNet otherwise retains its open handle to the previous file.

## Verified behavior and remaining work

The following polled baseline was verified on physical Jr2 hardware with the
normal image before restoring IRQ operation:

- the system boots to a shell with `sc16550` and `/t0` resident;
- `iniz /t0` returns normally;
- outbound bytes sent through `/t0` arrive exactly on Jr2 USB channel 2;
- inbound bytes from the Mac appear through `copy /t0 /term`; and
- Ctrl-C interrupts the read and returns control to the shell.

The repaired IRQ path was then verified on the physical Jr2:

- the Level 2 device table was at `$8700` and the polling table at `$88FB`;
- `irqs` showed `vtio` at `$FE20` with mask `$04` and `sc16550` at `$FE21`
  with flip `$00`, mask `$01`, and priority `$80`;
- `iniz /t0` returned to the shell with the UART IRQ entry installed;
- `echo IRQ-TX-1234567890 >/t0` transmitted through the THRE interrupt path
  and returned to the prompt;
- an inbound `IRQ-RX-1234567890` burst crossed the RX FIFO trigger, appeared
  through `copy /t0 /term`, and left the system responsive; and
- Ctrl-C stopped the copy and returned `ERROR #003` (`S$Intrpt`) as expected.

The DriveWire/FujiNet path was subsequently verified end to end: the Jr2
booted from `/x0`, DriveWire supplied the system time, the reconnectable bridge
survived a Jr2 power cycle, and `fnstatus` successfully returned adapter and
Wi-Fi information. See [`FujiNet-UART.md`](FujiNet-UART.md) for the complete
setup, Fuji transaction fixes, and troubleshooting history.

Long-duration and simultaneous bidirectional stress remain useful follow-up
tests for the normal `/t0` IRQ path.

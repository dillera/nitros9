#!/usr/bin/env python3
"""Reconnectable binary serial bridge for Jr2-to-FujiNet DriveWire.

The Wildbits Jr2 USB serial device disappears when the machine is powered
off.  Unlike a one-shot socat process, this bridge keeps running, reopens a
missing endpoint when macOS (or Linux) creates it again, and resumes relaying
without requiring a host-side restart.

Examples:
    python3 scripts/drivewire_serial_bridge.py \
        --jr2 /dev/cu.usbserial-600SA20462 \
        --fujinet /dev/cu.usbserial-1440

    python3 scripts/drivewire_serial_bridge.py \
        --jr2 '/dev/cu.usbserial-*62' \
        --fujinet '/dev/cu.usbserial-1440'
"""

from __future__ import annotations

import argparse
import errno
import glob
import os
import select
import signal
import sys
import termios
import threading
import time
from typing import Callable, Iterable, Optional


LogFunction = Callable[[str], None]


def _baud_constant(baud: int) -> int:
    name = f"B{baud}"
    try:
        return getattr(termios, name)
    except AttributeError as exc:
        raise ValueError(f"this platform does not provide termios {name}") from exc


def configure_serial(fd: int, baud: int) -> None:
    """Configure an open terminal descriptor for binary-clean 8N1 I/O."""
    attrs = termios.tcgetattr(fd)
    attrs[0] = 0
    attrs[1] = 0
    attrs[2] &= ~(termios.PARENB | termios.CSTOPB | termios.CSIZE)
    if hasattr(termios, "CRTSCTS"):
        attrs[2] &= ~termios.CRTSCTS
    attrs[2] |= termios.CLOCAL | termios.CREAD | termios.CS8
    attrs[3] = 0
    speed = _baud_constant(baud)
    attrs[4] = speed
    attrs[5] = speed
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


class SerialEndpoint:
    """A serial endpoint that can disappear and later be reopened."""

    def __init__(
        self,
        label: str,
        path_spec: str,
        baud: int,
        log: LogFunction,
    ) -> None:
        self.label = label
        self.path_spec = path_spec
        self.baud = baud
        self.log = log
        self.fd: Optional[int] = None
        self.path: Optional[str] = None
        self.generation = 0
        self.next_open_time = 0.0

    def candidates(self, excluded: Iterable[str] = ()) -> list[str]:
        excluded_set = set(excluded)
        if glob.has_magic(self.path_spec):
            paths = sorted(glob.glob(self.path_spec))
        elif os.path.exists(self.path_spec):
            paths = [self.path_spec]
        else:
            paths = []
        return [path for path in paths if path not in excluded_set]

    def try_open(self, now: float, retry_interval: float, excluded: Iterable[str]) -> bool:
        if self.fd is not None or now < self.next_open_time:
            return self.fd is not None

        self.next_open_time = now + retry_interval
        for path in self.candidates(excluded):
            fd = None
            try:
                fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
                configure_serial(fd, self.baud)
            except (OSError, ValueError) as exc:
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                self.log(f"{self.label}: waiting for {path}: {exc}")
                continue

            self.fd = fd
            self.path = path
            self.generation += 1
            self.log(f"{self.label}: connected to {path} at {self.baud} 8N1")
            return True
        return False

    def close(self, reason: str) -> None:
        if self.fd is None:
            return
        path = self.path
        try:
            os.close(self.fd)
        except OSError:
            pass
        self.fd = None
        self.path = None
        self.next_open_time = 0.0
        suffix = "" if reason == "bridge stopped" else "; waiting to reconnect"
        self.log(f"{self.label}: disconnected from {path}: {reason}{suffix}")


class SerialBridge:
    """Relay bytes in both directions while reconnecting failed endpoints."""

    def __init__(
        self,
        jr2_path: str,
        fujinet_path: str,
        baud: int = 115200,
        retry_interval: float = 1.0,
        stats_interval: float = 0.0,
        log: LogFunction = print,
    ) -> None:
        self.log = log
        self.retry_interval = retry_interval
        self.stats_interval = stats_interval
        self.jr2 = SerialEndpoint("Jr2", jr2_path, baud, log)
        self.fujinet = SerialEndpoint("FujiNet", fujinet_path, baud, log)
        self.endpoints = (self.jr2, self.fujinet)
        self.counts = [0, 0]
        self._pair_generation: Optional[tuple[int, int]] = None

    def close(self) -> None:
        self.jr2.close("bridge stopped")
        self.fujinet.close("bridge stopped")

    def _open_missing_endpoints(self, now: float) -> None:
        for endpoint in self.endpoints:
            excluded = [
                other.path
                for other in self.endpoints
                if other is not endpoint and other.path is not None
            ]
            endpoint.try_open(now, self.retry_interval, excluded)

    def _prepare_pair(self) -> bool:
        if self.jr2.fd is None or self.fujinet.fd is None:
            self._pair_generation = None
            return False

        generation = (self.jr2.generation, self.fujinet.generation)
        if generation != self._pair_generation:
            # The Jr2 may already have queued its first DriveWire request while
            # this process was reopening the USB device, so preserve Jr2 input.
            # A still-connected FujiNet may instead contain a reply belonging
            # to the interrupted session; discard that stale reply.
            try:
                termios.tcflush(self.fujinet.fd, termios.TCIFLUSH)
            except OSError as exc:
                self.fujinet.close(str(exc))
                self._pair_generation = None
                return False
            self._pair_generation = generation
            self.log("bridge: ready")
        return True

    def _write_all(self, endpoint: SerialEndpoint, data: bytes) -> bool:
        view = memoryview(data)
        while view and endpoint.fd is not None:
            try:
                written = os.write(endpoint.fd, view)
                if written == 0:
                    endpoint.close("zero-byte write")
                    return False
                view = view[written:]
            except BlockingIOError:
                try:
                    select.select([], [endpoint.fd], [], 0.25)
                except (OSError, ValueError) as exc:
                    endpoint.close(str(exc))
                    return False
            except OSError as exc:
                endpoint.close(str(exc))
                return False
        return not view

    def run(self, stop_event: Optional[threading.Event] = None) -> None:
        stop_event = stop_event or threading.Event()
        last_stats = time.monotonic()
        last_counts = list(self.counts)

        try:
            while not stop_event.is_set():
                now = time.monotonic()
                self._open_missing_endpoints(now)
                if not self._prepare_pair():
                    stop_event.wait(min(self.retry_interval, 0.25))
                    continue

                fd_to_index = {
                    self.jr2.fd: 0,
                    self.fujinet.fd: 1,
                }
                try:
                    readable, _, _ = select.select(list(fd_to_index), [], [], 0.25)
                except (OSError, ValueError):
                    # Let reads identify the failed descriptor when possible;
                    # otherwise reopen both endpoints on the next pass.
                    readable = list(fd_to_index)

                for fd in readable:
                    source_index = fd_to_index.get(fd)
                    if source_index is None:
                        continue
                    source = self.endpoints[source_index]
                    destination = self.endpoints[1 - source_index]
                    try:
                        data = os.read(fd, 4096)
                    except BlockingIOError:
                        continue
                    except OSError as exc:
                        source.close(str(exc))
                        self._pair_generation = None
                        break
                    if not data:
                        source.close("end of file")
                        self._pair_generation = None
                        break
                    if self._write_all(destination, data):
                        self.counts[source_index] += len(data)
                    else:
                        self._pair_generation = None
                        break

                now = time.monotonic()
                if self.stats_interval > 0 and now - last_stats >= self.stats_interval:
                    delta = [self.counts[i] - last_counts[i] for i in range(2)]
                    if delta != [0, 0]:
                        self.log(
                            "bridge: "
                            f"Jr2->FujiNet {self.counts[0]} bytes; "
                            f"FujiNet->Jr2 {self.counts[1]} bytes; "
                            f"+{delta[0]}/+{delta[1]}"
                        )
                    last_counts = list(self.counts)
                    last_stats = now
        finally:
            self.close()


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconnectable Jr2-to-FujiNet DriveWire serial bridge"
    )
    parser.add_argument(
        "--jr2",
        required=True,
        help="Jr2 UART path or quoted glob (for example /dev/cu.usbserial-*62)",
    )
    parser.add_argument(
        "--fujinet",
        required=True,
        help="FujiNet adapter path or quoted glob",
    )
    parser.add_argument("--baud", type=int, default=115200, help="baud rate (default: 115200)")
    parser.add_argument(
        "--reconnect-delay",
        type=float,
        default=1.0,
        help="seconds between reopen attempts (default: 1.0)",
    )
    parser.add_argument(
        "--stats-interval",
        type=float,
        default=0.0,
        help="traffic report interval; 0 disables reports (default: 0)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    if args.reconnect_delay <= 0:
        raise SystemExit("--reconnect-delay must be greater than zero")
    if args.stats_interval < 0:
        raise SystemExit("--stats-interval cannot be negative")

    stop_event = threading.Event()

    def stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    bridge = SerialBridge(
        jr2_path=args.jr2,
        fujinet_path=args.fujinet,
        baud=args.baud,
        retry_interval=args.reconnect_delay,
        stats_interval=args.stats_interval,
    )
    bridge.run(stop_event)
    return 0


if __name__ == "__main__":
    sys.exit(main())

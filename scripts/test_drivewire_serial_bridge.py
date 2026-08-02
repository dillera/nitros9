import os
import pty
import select
import tempfile
import threading
import time
import unittest

from drivewire_serial_bridge import SerialBridge, parse_args


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for bridge state")


def read_exact(fd, size, timeout=3.0):
    data = bytearray()
    deadline = time.monotonic() + timeout
    while len(data) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"timed out after receiving {bytes(data)!r}")
        readable, _, _ = select.select([fd], [], [], remaining)
        if not readable:
            continue
        data.extend(os.read(fd, size - len(data)))
    return bytes(data)


class SerialBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.stop_event = threading.Event()
        self.logs = []
        self.masters = []

        self.jr2_link = os.path.join(self.tempdir.name, "jr2")
        self.fujinet_link = os.path.join(self.tempdir.name, "fujinet")
        self.jr2_master = self.make_port(self.jr2_link)
        self.fujinet_master = self.make_port(self.fujinet_link)

        self.bridge = SerialBridge(
            self.jr2_link,
            self.fujinet_link,
            retry_interval=0.05,
            stats_interval=0,
            log=self.logs.append,
        )
        self.thread = threading.Thread(
            target=self.bridge.run,
            args=(self.stop_event,),
            daemon=True,
        )
        self.thread.start()
        wait_for(lambda: self.bridge._pair_generation is not None)

    def tearDown(self):
        self.stop_event.set()
        self.thread.join(timeout=3)
        for fd in self.masters:
            try:
                os.close(fd)
            except OSError:
                pass
        self.tempdir.cleanup()

    def make_port(self, link_path):
        master, slave = pty.openpty()
        slave_path = os.ttyname(slave)
        os.close(slave)
        try:
            os.unlink(link_path)
        except FileNotFoundError:
            pass
        os.symlink(slave_path, link_path)
        self.masters.append(master)
        return master

    def test_relays_both_directions(self):
        os.write(self.jr2_master, b"jr2-to-fujinet")
        self.assertEqual(read_exact(self.fujinet_master, 14), b"jr2-to-fujinet")

        os.write(self.fujinet_master, b"fujinet-to-jr2")
        self.assertEqual(read_exact(self.jr2_master, 14), b"fujinet-to-jr2")

    def test_traffic_stats_are_disabled_by_default(self):
        args = parse_args(
            [
                "--jr2",
                self.jr2_link,
                "--fujinet",
                self.fujinet_link,
            ]
        )
        self.assertEqual(args.stats_interval, 0.0)

    def test_reconnects_recreated_jr2_device(self):
        old_generation = self.bridge.jr2.generation
        os.close(self.jr2_master)
        self.masters.remove(self.jr2_master)
        os.unlink(self.jr2_link)
        wait_for(lambda: self.bridge.jr2.fd is None)

        self.jr2_master = self.make_port(self.jr2_link)
        wait_for(
            lambda: self.bridge.jr2.generation > old_generation
            and self.bridge._pair_generation is not None
        )

        os.write(self.jr2_master, b"after-power-cycle")
        self.assertEqual(read_exact(self.fujinet_master, 17), b"after-power-cycle")
        self.assertTrue(any("waiting to reconnect" in line for line in self.logs))


if __name__ == "__main__":
    unittest.main()

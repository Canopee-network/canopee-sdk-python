"""Live end-to-end test: drives a real `canopee-node` process (built from the
main Canopee repo) purely through the Python SDK, mirroring the TypeScript,
Go and Rust integration scenarios.

The node binary is located via CANOPEE_NODE_BIN (falls back to canopee-node
on PATH); the test is skipped when none is available.
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from canopee_sdk import (
    BLOB,
    PUBLISH,
    READ,
    WRITE,
    CanopeeError,
    Client,
    Contact,
    ContactList,
    Device,
    HomeEntry,
    HomeIndex,
    Profile,
    resource_channel,
)


def _node_binary() -> str | None:
    path = os.environ.get("CANOPEE_NODE_BIN")
    if path:
        return path
    return shutil.which("canopee-node")


def _wait_for_socket(socket_path: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(socket_path):
            return
        time.sleep(0.1)
    raise RuntimeError(f"node socket never appeared at {socket_path}")


@unittest.skipIf(_node_binary() is None, "no canopee-node binary (set CANOPEE_NODE_BIN)")
class LiveNodeE2ETest(unittest.TestCase):
    def setUp(self) -> None:
        self._bin = _node_binary()
        self._root = tempfile.mkdtemp(prefix="canopee-e2e-py-")
        self._prev_root = os.environ.get("CANOPEE_APP_ROOT")
        os.environ["CANOPEE_APP_ROOT"] = self._root
        logf = open(os.path.join(self._root, "node.log"), "ab")
        env = dict(os.environ)
        env.update(
            {
                "CANOPEE_APP_ROOT": self._root,
                "CANOPEE_MDNS": "0",
                "CANOPEE_BOOTSTRAP_ADDRS": "",
                "RUST_LOG": "",
            }
        )
        self._logf = logf
        self._node = subprocess.Popen(
            [self._bin],
            env=env,
            stdout=logf,
            stderr=logf,
        )
        self._socket_path = os.path.join(self._root, ".canopee", "node.sock")
        _wait_for_socket(self._socket_path)

    def tearDown(self) -> None:
        if self._node.poll() is None:
            self._node.kill()
            try:
                self._node.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        self._logf.close()
        shutil.rmtree(self._root, ignore_errors=True)
        if self._prev_root is None:
            os.environ.pop("CANOPEE_APP_ROOT", None)
        else:
            os.environ["CANOPEE_APP_ROOT"] = self._prev_root

    def test_live_node_e2e(self) -> None:
        client = Client(self._socket_path)

        # ---- identity / status ----
        identity = client.identity()
        self.assertTrue(identity.startswith("canopee://identity/"), identity)
        status = client.status()
        self.assertEqual(status.identity, identity)
        self.assertEqual(status.peers, 0)
        self.assertGreaterEqual(status.objects, 1)

        # ---- storage ----
        id1 = client.put(b"live e2e (py)")
        obj = client.get(id1)
        self.assertEqual(obj.payload.data, b"live e2e (py)")
        self.assertEqual(obj.id, id1)

        client.set_name(id1, "e2e-name")
        infos = client.list()
        info = next((o for o in infos if o.id == id1), None)
        self.assertIsNotNone(info, "stored object must be listed")
        self.assertEqual(info.name, "e2e-name")
        self.assertEqual(info.object_type, BLOB)

        a = client.put(b"hello ")
        b = client.put(b"world")
        joined = client.concat([a, b], "greeting")
        self.assertNotEqual(joined, a)
        self.assertEqual(client.get(joined).payload.data, b"hello world")

        bundle = client.export_object(id1)
        self.assertEqual(bundle.object.id, id1)
        client.delete_object(id1)
        client.import_object(bundle)
        self.assertEqual(client.get(id1).payload.data, b"live e2e (py)")

        client.delete_object(joined)
        with self.assertRaises(CanopeeError):
            client.get(joined)
        with self.assertRaises(CanopeeError):
            client.get("0" * 64)

        # ---- runtime / identity ----
        key = client.dh_public_key()
        self.assertEqual(len(key), 32)

        self.assertIsNone(client.load_profile())
        client.save_profile(Profile(display_name="alice", dh_public_key=b"\x07" * 32, avatar=None, version=0))
        profile = client.load_profile()
        self.assertEqual(profile.display_name, "alice")
        self.assertEqual(profile.version, 1)
        client.save_profile(Profile(display_name="alice", dh_public_key=b"\x07" * 32, avatar=None, version=0))
        profile = client.load_profile()
        self.assertEqual(profile.version, 2)

        self.assertIsNone(client.load_contact_list())
        client.save_contact_list(
            ContactList(contacts=[Contact(name="bob", peer_id="12D3KooB", dh_public_key=b"\x09" * 32, note=None)], version=0)
        )
        lst = client.load_contact_list()
        self.assertEqual(lst.contacts[0].name, "bob")
        self.assertEqual(lst.version, 1)

        pic = client.put_object(b"png bytes", BLOB)
        client.save_home_index(
            HomeIndex(
                profile=None,
                contacts=None,
                entries=[HomeEntry(name="pic.png", object=pic, object_type=BLOB, shared=False, app="e2e")],
                version=0,
            )
        )
        index = client.load_home_index()
        self.assertEqual(len(index.entries), 1)
        self.assertFalse(index.entries[0].shared)
        client.set_home_entry_shared("pic.png", True)
        index = client.load_home_index()
        self.assertTrue(index.entries[0].shared)
        with self.assertRaises(CanopeeError):
            client.set_home_entry_shared("missing", True)

        # ---- user records ----
        own_profile = client.resolve_profile(identity)
        self.assertIsNotNone(own_profile)
        self.assertEqual(own_profile.display_name, "alice")

        # Pointers are cached locally and published fire-and-forget on the DHT,
        # so a self publish resolves immediately even with zero peers.
        client.publish_pointer("e2e:demo", id1)
        rec = client.resolve_pointer(identity, "e2e:demo")
        self.assertIsNotNone(rec, "just-published pointer must resolve")
        self.assertEqual(rec.manifest, id1)
        self.assertEqual(rec.name, "e2e:demo")

        # App pointers await the DHT put directly, which needs at least one
        # peer; on a hermetic single node they fail cleanly with the quorum
        # error.
        with self.assertRaisesRegex(CanopeeError, "quorum"):
            client.publish_app_pointer("my-app", id1)

        client.announce(id1)
        self.assertEqual(len(client.peers()), 0)

        # publishing with zero subscribers fails cleanly, not hanging
        with self.assertRaises(CanopeeError):
            client.publish("e2e-topic", b"ping")

        # ---- devices ----
        dev = client.device()
        self.assertGreater(len(dev.peer_id), 10)
        self.assertGreater(len(dev.device_name), 0)
        devs = client.device_list()
        self.assertTrue(any(d.device_id == dev.peer_id for d in devs), "own device registered")
        other = "12D3KooWE2eTestDevicePy"
        client.add_device(other, "e2e-device")
        self.assertTrue(any(d.device_id == other for d in client.device_list()))
        client.remove_device(other)
        self.assertFalse(any(d.device_id == other for d in client.device_list()))

        # ---- capabilities ----
        self.assertIsNone(client.list_capabilities())
        cap = client.grant_capability(
            "canopee://identity/bob",
            resource_channel("team-news"),
            [READ, WRITE],
        )
        self.assertEqual(cap.permissions, [READ, WRITE])
        self.assertEqual(cap.issuer, identity)
        valid, reason = client.check_capability(cap)
        self.assertTrue(valid, reason)

        res = resource_channel("team-news")
        self.assertTrue(client.check_access("canopee://identity/bob", READ, res))
        self.assertTrue(client.check_access("canopee://identity/bob", WRITE, res))
        self.assertFalse(client.check_access("canopee://identity/bob", PUBLISH, res))
        self.assertFalse(client.check_access("canopee://identity/mallory", READ, res))
        self.assertFalse(client.check_access("canopee://identity/bob", READ, resource_channel("other")))

        cidx = client.list_capabilities()
        self.assertEqual(len(cidx.entries), 1)
        self.assertFalse(cidx.entries[0].revoked)

        client.revoke_capability(cap.id)
        cidx = client.list_capabilities()
        self.assertTrue(cidx.entries[0].revoked)
        valid, reason = client.check_capability(cap)
        self.assertFalse(valid)
        self.assertIn("revoked", reason)
        self.assertFalse(client.check_access("canopee://identity/bob", READ, res))

        # ---- pub/sub (ack + clean close; no message can arrive without peers) ----
        sub = client.subscribe("e2e-sub")
        sub.close()

        client.shutdown()


if __name__ == "__main__":
    unittest.main()
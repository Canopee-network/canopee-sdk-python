"""Unix-socket client for a Canopee node.

Each method maps one-to-one onto a NodeCommand / NodeResponse pair (see the
Rust canopee-sdk for semantics). A transport error becomes `NodeNotRunningError`;
the node reporting its own error becomes `CanopeeError`.
"""

from __future__ import annotations

import errno
import json
import os
import socket
from dataclasses import dataclass
from typing import Any, Callable, Optional

from . import _codec
from ._types import (
    AppPointerRecord,
    Capability,
    CapabilityIndex,
    ContactList,
    Device,
    DeviceInfo,
    ExportBundle,
    HomeIndex,
    NodeStatus,
    ObjectId,
    ObjectInfo,
    PeerInfo,
    Profile,
    PubSubMessage,
    RelayReservationInfo,
    StoredObject,
    SyncResult,
    from_shape,
    to_shape,
)

try:
    from typing import Self
except ImportError:  # pragma: no cover
    from typing_extensions import Self  # type: ignore

_VOID_RESULTS = {
    "NameSet",
    "ObjectDeleted",
    "Imported",
    "ShutdownAccepted",
    "Dialed",
    "ListeningViaRelay",
    "Published",
    "Subscribed",
    "Announced",
    "AppPointerPublished",
    "PointerPublished",
    "UsernameClaimed",
    "DeviceAdded",
    "DeviceRemoved",
    "CapabilityRevoked",
    "ServeSessionStopped",
}


class CanopeeError(Exception):
    """The node answered `NodeResponse::Error`."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NodeNotRunningError(CanopeeError):
    """No node socket to talk to."""


class SubscriptionClosed(Exception):
    """The subscription stream ended."""


@dataclass
class Subscription:
    conn: Optional[socket.socket]
    buffer: bytearray
    closed: bool = False

    def _next_frame(self) -> Any:
        if self.conn is None:
            raise SubscriptionClosed()
        try:
            payload = _codec.read_frame(self.conn)
        except EOFError:
            self.close()
            raise SubscriptionClosed() from None
        resp = _codec.decode_response(payload)
        if isinstance(resp, dict) and "Error" in resp:
            raise CanopeeError(resp["Error"]["message"])
        if isinstance(resp, dict) and "PubSub" in resp:
            return from_shape(PubSubMessage, resp["PubSub"])
        if isinstance(resp, dict) and "Subscribed" in resp:
            return None
        return resp

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> Any:
        if self.closed:
            raise StopIteration
        try:
            return self._next_frame()
        except SubscriptionClosed:
            raise StopIteration from None

    def next(self) -> Any:
        return self._next_frame()

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.conn.close()
            self.conn = None
        self.closed = True


class Client:
    def __init__(self, socket_path: Optional[str] = None) -> None:
        self.socket_path = socket_path or default_socket_path()

    # ------------------------------------------------------------------
    # Primitive plumbing
    # ------------------------------------------------------------------

    def _request_conn(self) -> socket.socket:
        if not os.path.exists(self.socket_path):
            raise NodeNotRunningError(f"canopee node not running at {self.socket_path}")
        try:
            conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            conn.settimeout(30)
            conn.connect(self.socket_path)
        except (FileNotFoundError, ConnectionRefusedError, OSError) as exc:
            raise NodeNotRunningError(f"cannot connect to {self.socket_path}: {exc}") from None
        return conn

    def _transact(self, command: Any) -> Any:
        conn = self._request_conn()
        try:
            conn.sendall(_codec.frame(_codec.encode_command(command)))
            payload = _codec.read_frame(conn)
        except (EOFError, OSError) as exc:
            raise NodeNotRunningError(f"transport error: {exc}") from None
        finally:
            conn.close()
        return _unwrap(decode_response(payload))

    def subscribe(self, topic: str) -> Subscription:
        command = {"Subscribe": {"topic": topic}}
        conn = self._request_conn()
        try:
            conn.sendall(_codec.frame(_codec.encode_command(command)))
        except OSError as exc:
            conn.close()
            raise NodeNotRunningError(f"transport error: {exc}") from None
        return Subscription(conn=conn, buffer=bytearray())

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    def put(self, data: bytes, name: Optional[str] = None) -> ObjectId:
        command = {"Put": {"data": list(data), "name": name}}
        (id_,) = self._transact(command)
        return id_

    def put_object(self, data: bytes, object_type: str, name: Optional[str] = None) -> ObjectId:
        command = {"PutObject": {"data": list(data), "object_type": object_type, "name": name}}
        (id_,) = self._transact(command)
        return id_

    def concat(self, ids: list[ObjectId], name: Optional[str] = None) -> ObjectId:
        (id_,) = self._transact({"Concat": {"ids": ids, "name": name}})
        return id_

    def get(self, id_: ObjectId) -> StoredObject:
        obj = self._transact({"Get": {"id": id_}})
        return from_shape(StoredObject, obj)

    def list(self) -> list[ObjectInfo]:
        objs = self._transact({"List": {}})
        return [from_shape(ObjectInfo, o) for o in objs]

    def delete_object(self, id_: ObjectId) -> None:
        return self._transact({"DeleteObject": {"id": id_}})

    def set_name(self, id_: ObjectId, name: str) -> None:
        return self._transact({"SetName": {"id": id_, "name": name}})

    def export_object(self, id_: ObjectId) -> ExportBundle:
        bundle = self._transact({"Export": {"id": id_}})
        return from_shape(ExportBundle, bundle)

    def import_object(self, bundle: ExportBundle) -> None:
        return self._transact({"Import": {"bundle": to_shape(bundle)}})

    # ------------------------------------------------------------------
    # Runtime / identity
    # ------------------------------------------------------------------

    def status(self) -> NodeStatus:
        st = self._transact({"Status": {}})
        return from_shape(NodeStatus, st)

    def identity(self) -> str:
        (id_,) = self._transact({"Identity": {}})
        return id_

    def shutdown(self) -> None:
        return self._transact({"Shutdown": {}})

    def dh_public_key(self) -> bytes:
        (key,) = self._transact({"DhPublicKey": {}})
        return bytes(int(x) for x in key)

    def save_profile(self, profile: Profile) -> ObjectId:
        (id_,) = self._transact({"SaveProfile": {"profile": to_shape(profile)}})
        return id_

    def load_profile(self) -> Optional[Profile]:
        profile = self._transact({"LoadProfile": {}})
        return None if profile is None else from_shape(Profile, profile)

    def save_contact_list(self, lst: ContactList) -> ObjectId:
        (id_,) = self._transact({"SaveContactList": {"list": to_shape(lst)}})
        return id_

    def load_contact_list(self) -> Optional[ContactList]:
        lst = self._transact({"LoadContactList": {}})
        return None if lst is None else from_shape(ContactList, lst)

    def save_home_index(self, index: HomeIndex) -> ObjectId:
        (id_,) = self._transact({"SaveHomeIndex": {"index": to_shape(index)}})
        return id_

    def load_home_index(self) -> Optional[HomeIndex]:
        index = self._transact({"LoadHomeIndex": {}})
        return None if index is None else from_shape(HomeIndex, index)

    def set_home_entry_shared(self, name: str, shared: bool) -> None:
        return self._transact({"SetHomeEntryShared": {"name": name, "shared": shared}})

    def export_identity(self, passphrase: str) -> bytes:
        (blob,) = self._transact({"ExportIdentity": {"passphrase": passphrase}})
        return bytes(int(x) for x in blob)

    def import_identity(self, blob: bytes, passphrase: str, overwrite: bool = False) -> str:
        (id_,) = self._transact({"ImportIdentity": {"bytes": list(blob), "passphrase": passphrase, "overwrite": overwrite}})
        return id_

    # ------------------------------------------------------------------
    # Network / user records
    # ------------------------------------------------------------------

    def publish_pointer(self, name: str, target: str) -> None:
        return self._transact({"PublishPointer": {"name": name, "target": target}})

    def publish_app_pointer(self, name: str, manifest: ObjectId) -> None:
        return self._transact({"PublishAppPointer": {"name": name, "manifest": manifest}})

    def resolve_app_pointer(self, owner: str, name: str) -> Optional[AppPointerRecord]:
        record = self._transact({"ResolveAppPointer": {"owner": owner, "name": name}})
        return None if record is None else from_shape(AppPointerRecord, record)

    def resolve_pointer(self, owner: str, name: str) -> Optional[AppPointerRecord]:
        record = self._transact({"ResolvePointer": {"owner": owner, "name": name}})
        return None if record is None else from_shape(AppPointerRecord, record)

    def claim_username(self, username: str) -> None:
        return self._transact({"ClaimUsername": {"username": username}})

    def resolve_username(self, username: str) -> str:
        (owner,) = self._transact({"ResolveUsername": {"username": username}})
        return owner

    def show_username(self) -> Optional[str]:
        (username,) = self._transact({"ShowUsername": {}})
        return username

    def resolve_profile(self, owner: str) -> Optional[Profile]:
        profile = self._transact({"ResolveProfile": {"owner": owner}})
        return None if profile is None else from_shape(Profile, profile)

    # ------------------------------------------------------------------
    # Devices / pairing
    # ------------------------------------------------------------------

    def device(self) -> Device:
        dev = self._transact({"Device": {}})
        return from_shape(Device, dev)

    def device_list(self) -> list[DeviceInfo]:
        devices = self._transact({"DeviceList": {}})
        return [from_shape(DeviceInfo, d) for d in devices]

    def resolve_owner_device(self, owner: str) -> Optional[str]:
        (peer_id,) = self._transact({"ResolveOwnerDevice": {"owner": owner}})
        return peer_id

    def add_device(self, device_id: str, device_name: str) -> None:
        return self._transact({"AddDevice": {"device_id": device_id, "device_name": device_name}})

    def remove_device(self, device_id: str) -> None:
        return self._transact({"RemoveDevice": {"device_id": device_id}})

    def initiate_pairing(self) -> Any:
        return self._transact({"InitiatePairing": {}})

    def complete_pairing(self, qr: Any, code: str) -> str:
        (message,) = self._transact({"CompletePairing": {"qr": to_shape(qr), "code": code}})
        return message

    def sync_from_peer(self, peer_id: str) -> SyncResult:
        result = self._transact({"SyncFromPeer": {"peer_id": peer_id}})
        return from_shape(SyncResult, result)

    def sync_device_list(self) -> None:
        return self._transact({"SyncDeviceList": {}})

    # ------------------------------------------------------------------
    # Networking / DHT
    # ------------------------------------------------------------------

    def dial(self, addr: str) -> None:
        return self._transact({"Dial": {"addr": addr}})

    def listen_via_relay(self, relay_addr: str) -> None:
        return self._transact({"ListenViaRelay": {"relay_addr": relay_addr}})

    def publish(self, topic: str, data: bytes) -> None:
        return self._transact({"Publish": {"topic": topic, "data": list(data)}})

    def peers(self) -> list[PeerInfo]:
        peers = self._transact({"Peers": {}})
        return [from_shape(PeerInfo, p) for p in peers]

    def relay_reservations(self) -> list[RelayReservationInfo]:
        reservations = self._transact({"RelayReservations": {}})
        return [from_shape(RelayReservationInfo, r) for r in reservations]

    def find_providers(self, id_: ObjectId) -> list[str]:
        (peer_ids,) = self._transact({"FindProviders": {"id": id_}})
        return peer_ids

    def fetch_object(self, peer_id: str, id_: ObjectId) -> StoredObject:
        obj = self._transact({"FetchObject": {"peer_id": peer_id, "id": id_}})
        return from_shape(StoredObject, obj)

    def announce(self, id_: ObjectId) -> None:
        return self._transact({"Announce": {"id": id_}})

    def put_object_public(self, data: bytes, object_type: str, name: Optional[str] = None) -> ObjectId:
        (id_,) = self._transact({"PutObjectPublic": {"data": list(data), "object_type": object_type, "name": name}})
        return id_

    def share_object(self, name: str, object_: ObjectId, app: Optional[str], recipients: list[bytes]) -> None:
        return self._transact(
            {"ShareObject": {"name": name, "object": object_, "app": app, "recipients": [list(x) for x in recipients]}}
        )

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------

    def grant_capability(
        self,
        subject: str,
        resource: Any,
        permissions: list[str],
        expires_at: Optional[int] = None,
    ) -> Capability:
        cap = self._transact(
            {"GrantCapability": {"subject": subject, "resource": resource, "permissions": permissions, "expires_at": expires_at}}
        )
        return from_shape(Capability, cap)

    def list_capabilities(self) -> Optional[CapabilityIndex]:
        index = self._transact({"ListCapabilities": {}})
        return None if index is None else from_shape(CapabilityIndex, index)

    def revoke_capability(self, id_: CapabilityId) -> None:
        return self._transact({"RevokeCapability": {"id": id_}})

    def check_capability(self, capability: Capability) -> tuple[bool, str]:
        (valid, reason) = self._transact({"CheckCapability": {"capability": to_shape(capability)}})
        return valid, reason

    def check_access(
        self,
        subject: str,
        permission: str,
        resource: Any,
    ) -> bool:
        (allowed,) = self._transact(
            {"CheckAccess": {"subject": subject, "permission": permission, "resource": resource}}
        )
        return allowed

    # ------------------------------------------------------------------
    # Serve sessions
    # ------------------------------------------------------------------

    def start_serve_session(self, edge_addr: str, app_id: str) -> tuple[str, str]:
        (sid, root_url) = self._transact({"StartServeSession": {"edge_addr": edge_addr, "app_id": app_id}})
        return sid, root_url

    def stop_serve_session(self) -> None:
        return self._transact({"StopServeSession": {}})


def default_socket_path() -> str:
    app_root = os.environ.get("CANOPEE_APP_ROOT")
    if app_root:
        return os.path.join(app_root, ".canopee", "node.sock")
    home = os.environ.get("HOME", "")
    return os.path.join(home, ".canopee", "node.sock")


def _unwrap(response: Any) -> Any:
    if isinstance(response, dict) and "Error" in response:
        raise CanopeeError(response["Error"]["message"])
    if isinstance(response, str) and response in _VOID_RESULTS:
        return None
    if isinstance(response, dict):
        for key, value in response.items():
            if isinstance(value, tuple):
                raise TypeError(f"unexpected tuple in response {response!r}")
        return next(iter(response.values()))
    raise TypeError(f"unexpected response {response!r}")


def decode_response(payload: bytes) -> Any:
    """Public: decode a framed payload (used by Subscription and tests)."""
    return _codec.decode_response(payload)


def current_identity() -> str:
    """Fetch the node's own identity via a throwaway client."""
    return Client().identity()
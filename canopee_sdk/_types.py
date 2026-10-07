"""Typed model of the Canopee wire protocol + lossless typed <-> shape conversion.

Field names match the wire (serde) names. Byte values travel as lists of ints
in the shape representation.
"""

from __future__ import annotations

import typing
from dataclasses import dataclass, fields, is_dataclass
from typing import Any, Optional, get_args, get_origin

ObjectId = str
IdentityId = str
CapabilityId = str

# ObjectType and Permission are plain strings in the wire shape; the module
# defines these as type aliases for clarity.
ObjectType = str
Permission = str

BLOB = "Blob"
APPMANIFEST = "AppManifest"
APPPOINTER = "AppPointer"
PROFILE = "Profile"
CONTACTLIST = "ContactList"
HOMEINDEX = "HomeIndex"
CAPABILITY = "Capability"
CAPABILITYINDEX = "CapabilityIndex"

READ = "Read"
WRITE = "Write"
PUBLISH = "Publish"


@dataclass
class ObjectMetadata:
    created_at: int
    size: int
    content_type: Optional[str] = None


@dataclass
class ObjectPayload:
    owner: IdentityId
    metadata: ObjectMetadata
    object_type: ObjectType
    data: bytes


@dataclass
class StoredObject:
    id: ObjectId
    payload: ObjectPayload
    public_key: bytes
    signature: bytes


@dataclass
class ExportBundle:
    version: int
    object: StoredObject


@dataclass
class ObjectInfo:
    id: ObjectId
    owner: IdentityId
    size: int
    verified: bool
    name: Optional[str]
    object_type: ObjectType


@dataclass
class Profile:
    display_name: str
    dh_public_key: bytes
    avatar: Optional[ObjectId]
    version: int


@dataclass
class Contact:
    name: str
    peer_id: str
    dh_public_key: bytes
    note: Optional[str]


@dataclass
class ContactList:
    contacts: list[Contact]
    version: int


@dataclass
class HomeEntry:
    name: str
    object: ObjectId
    object_type: ObjectType
    shared: bool
    app: Optional[str]


@dataclass
class HomeIndex:
    profile: Optional[ObjectId]
    contacts: Optional[ObjectId]
    entries: list[HomeEntry]
    version: int


@dataclass
class AppPointerRecord:
    name: str
    owner: IdentityId
    manifest: ObjectId
    published_at: int
    public_key: bytes
    signature: bytes


# Resource is an externally-tagged enum value: a dict with exactly one key
# ("Object" -> receiver id, "SharedName" -> {owner, name}, "Channel" -> name).
Resource = dict


@dataclass
class Capability:
    id: CapabilityId
    issuer: IdentityId
    subject: IdentityId
    resource: Resource
    permissions: list[Permission]
    issued_at: int
    expires_at: Optional[int]
    public_key: bytes
    signature: bytes


@dataclass
class CapabilityEntry:
    capability: Capability
    revoked: bool


@dataclass
class CapabilityIndex:
    entries: list[CapabilityEntry]
    version: int


@dataclass
class PeerInfo:
    peer_id: str
    identity: Optional[IdentityId]
    username: Optional[str]
    display_name: Optional[str]
    addresses: list[str]


@dataclass
class RelayReservationInfo:
    relay_peer_id: str
    renewal: bool
    listen_addrs: list[str]


@dataclass
class PubSubMessage:
    topic: str
    source: Optional[str]
    data: bytes


@dataclass
class DeviceInfo:
    device_id: str
    device_name: str


@dataclass
class PairingQrData:
    version: int
    device_id: str
    device_name: str
    lan_addr: str
    code: str
    session_id: str


@dataclass
class SyncResult:
    profile_updated: bool
    contacts_updated: bool
    devices_updated: bool


@dataclass
class Device:
    peer_id: str
    device_name: str


@dataclass
class NodeStatus:
    identity: IdentityId
    objects: int
    peers: int


def resource_object(receiver_id: ObjectId) -> Resource:
    return {"Object": receiver_id}


def resource_shared_name(owner: IdentityId, name: str) -> Resource:
    return {"SharedName": {"owner": owner, "name": name}}


def resource_channel(channel: str) -> Resource:
    return {"Channel": channel}


# ---------------------------------------------------------------------------
# Shape conversion
# ---------------------------------------------------------------------------


def to_shape(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bytes):
        return list(v)
    if isinstance(v, (list, tuple)):
        return [to_shape(x) for x in v]
    if isinstance(v, dict):
        return {str(k): to_shape(x) for k, x in v.items()}
    if isinstance(v, bool) or isinstance(v, int) or isinstance(v, str):
        return v
    if is_dataclass(v):
        return {f.name: to_shape(getattr(v, f.name)) for f in fields(v)}
    raise TypeError(f"to_shape: unsupported type {type(v).__name__}")


def from_shape(cls: type, shape: Any) -> Any:
    if cls in (str, ObjectId, IdentityId, CapabilityId, ObjectType, Permission):
        return shape
    origin = get_origin(cls)
    if cls is bytes:
        return bytes(int(x) for x in shape)
    if origin in (list, typing.List):
        inner = get_args(cls)[0]
        return [from_shape(inner, x) for x in shape]
    if origin is dict or cls is dict:
        if cls is Resource:
            return dict(shape)
        return {k: from_shape(get_args(cls)[1], v) for k, v in shape.items()}
    if get_origin(cls) is typing.Union:
        if shape is None:
            return None
        inner = next((a for a in get_args(cls) if a is not type(None)), None)
        return from_shape(inner, shape)
    if is_dataclass(cls):
        kwargs = {}
        hints = typing.get_type_hints(cls)
        for f in fields(cls):
            kwargs[f.name] = from_shape(hints.get(f.name, f.type), shape.get(f.name))
        return cls(**kwargs)
    if cls is int:
        return int(shape)
    if cls is bool:
        return bool(shape)
    raise TypeError(f"from_shape: unsupported type {cls!r}")
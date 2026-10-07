"""Bincode 1.x legacy codec + wire schema for the Canopee node protocol.

Wire contract (identical to the Rust canopee-sdk crate and the node):

  * little-endian fixints (u8/u16/u32/u64, bool = one byte)
  * enum variants tagged with a u32 discriminant in declaration order
  * String/Vec prefixed with a u64 byte length
  * Option tagged with a single 0/1 byte
  * fixed-size byte arrays written raw (no prefix)
  * struct fields in declaration order
  * maps as u64 entry count then sorted (key, value) pairs — bincode emits
    HashMap entries in arbitrary order, so SDKs sort keys for stable output.

A frame on the Unix socket is `u32 LE <payload-length>` + payload.

Value shapes mirror serde_json output for the Rust types: struct -> dict,
unit variant -> str, data variant -> {"Variant": ...}, bytes -> list of ints,
option -> None or value, map -> dict.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Wire kind descriptors
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WireKind:
    t: str  # u32|u64|u8|bool|string|bytes|bytesN|struct|enum|vec|opt|map
    n: int = 0
    name: str = ""
    of: Optional["WireKind"] = field(default=None)


def u32() -> WireKind:
    return WireKind("u32")


def u64() -> WireKind:
    return WireKind("u64")


def u8() -> WireKind:
    return WireKind("u8")


def bool_() -> WireKind:
    return WireKind("bool")


def strk() -> WireKind:
    return WireKind("string")


def bytesk() -> WireKind:
    return WireKind("bytes")


def bytesn(n: int) -> WireKind:
    return WireKind("bytesN", n=n)


def structk(name: str) -> WireKind:
    return WireKind("struct", name=name)


def enmk(name: str) -> WireKind:
    return WireKind("enum", name=name)


def veck(of: WireKind) -> WireKind:
    return WireKind("vec", of=of)


def optk(of: WireKind) -> WireKind:
    return WireKind("opt", of=of)


def mapk(val: WireKind) -> WireKind:
    return WireKind("map", of=val)


OBJECT_ID = strk()  # ObjectId/IdentityId/CapabilityId are string newtypes
DH_KEY = bytesn(32)


@dataclass(frozen=True)
class Field:
    name: str
    kind: WireKind


@dataclass(frozen=True)
class Variant:
    name: str
    fields: tuple[Field, ...] = ()

    @property
    def is_newtype(self) -> bool:
        return len(self.fields) == 1 and self.fields[0].name == "0"


def F(name: str, kind: WireKind) -> Field:
    return Field(name, kind)


# ---------------------------------------------------------------------------
# Wire type catalog
# ---------------------------------------------------------------------------

STRUCTS: dict[str, tuple[Field, ...]] = {
    "PubSubMessage": (F("topic", strk()), F("source", optk(strk())), F("data", bytesk())),
    "PeerInfo": (
        F("peer_id", strk()),
        F("identity", optk(strk())),
        F("username", optk(strk())),
        F("display_name", optk(strk())),
        F("addresses", veck(strk())),
    ),
    "RelayReservationInfo": (
        F("relay_peer_id", strk()),
        F("renewal", bool_()),
        F("listen_addrs", veck(strk())),
    ),
    "DeviceInfo": (F("device_id", strk()), F("device_name", strk())),
    "PairingQrData": (
        F("version", u8()),
        F("device_id", strk()),
        F("device_name", strk()),
        F("lan_addr", strk()),
        F("code", strk()),
        F("session_id", strk()),
    ),
    "PairingRecord": (
        F("name", strk()),
        F("object", structk("Object")),
        F("pointer", structk("AppPointerRecord")),
    ),
    "PairingData": (F("version", u8()), F("identity_key", bytesk()), F("records", veck(structk("PairingRecord")))),
    "SyncResult": (
        F("profile_updated", bool_()),
        F("contacts_updated", bool_()),
        F("devices_updated", bool_()),
    ),
    "AppManifest": (F("name", strk()), F("owner", strk()), F("entrypoint", strk()), F("assets", mapk(strk()))),
    "ObjectMetadata": (F("created_at", u64()), F("size", u64()), F("content_type", optk(strk()))),
    "ObjectPayload": (
        F("owner", strk()),
        F("metadata", structk("ObjectMetadata")),
        F("object_type", enmk("ObjectType")),
        F("data", bytesk()),
    ),
    "ObjectInfo": (
        F("id", strk()),
        F("owner", strk()),
        F("size", u64()),
        F("verified", bool_()),
        F("name", optk(strk())),
        F("object_type", enmk("ObjectType")),
    ),
    "Object": (
        F("id", strk()),
        F("payload", structk("ObjectPayload")),
        F("public_key", bytesk()),
        F("signature", bytesk()),
    ),
    "ExportBundle": (F("version", u32()), F("object", structk("Object"))),
    "Profile": (F("display_name", strk()), F("dh_public_key", DH_KEY), F("avatar", optk(strk())), F("version", u64())),
    "Contact": (F("name", strk()), F("peer_id", strk()), F("dh_public_key", DH_KEY), F("note", optk(strk()))),
    "ContactList": (F("contacts", veck(structk("Contact"))), F("version", u64())),
    "HomeEntry": (
        F("name", strk()),
        F("object", strk()),
        F("object_type", enmk("ObjectType")),
        F("shared", bool_()),
        F("app", optk(strk())),
    ),
    "HomeIndex": (
        F("profile", optk(strk())),
        F("contacts", optk(strk())),
        F("entries", veck(structk("HomeEntry"))),
        F("version", u64()),
    ),
    "AppPointerRecord": (
        F("name", strk()),
        F("owner", strk()),
        F("manifest", strk()),
        F("published_at", u64()),
        F("public_key", bytesk()),
        F("signature", bytesk()),
    ),
    "Capability": (
        F("id", strk()),
        F("issuer", strk()),
        F("subject", strk()),
        F("resource", enmk("Resource")),
        F("permissions", veck(enmk("Permission"))),
        F("issued_at", u64()),
        F("expires_at", optk(u64())),
        F("public_key", bytesk()),
        F("signature", bytesk()),
    ),
    "ExportCapability": (F("version", u8()), F("capability", structk("Capability"))),
    "CapabilityEntry": (F("capability", structk("Capability")), F("revoked", bool_())),
    "CapabilityIndex": (F("entries", veck(structk("CapabilityEntry"))), F("version", u64())),
}

ENUMS: dict[str, tuple[Variant, ...]] = {
    "ObjectType": (
        Variant("Blob"),
        Variant("AppManifest"),
        Variant("AppPointer"),
        Variant("Profile"),
        Variant("ContactList"),
        Variant("HomeIndex"),
        Variant("Capability"),
        Variant("CapabilityIndex"),
    ),
    "Permission": (Variant("Read"), Variant("Write"), Variant("Publish")),
    "Resource": (
        Variant("Object", (F("0", strk()),)),
        Variant("SharedName", (F("owner", strk()), F("name", strk()))),
        Variant("Channel", (F("0", strk()),)),
    ),
}

NODE_COMMAND: tuple[Variant, ...] = (
    Variant("Put", (F("data", bytesk()), F("name", optk(strk())))),
    Variant("PutObject", (F("data", bytesk()), F("object_type", enmk("ObjectType")), F("name", optk(strk())))),
    Variant("Concat", (F("ids", veck(strk())), F("name", optk(strk())))),
    Variant("Get", (F("id", strk()),)),
    Variant("List"),
    Variant("DeleteObject", (F("id", strk()),)),
    Variant("SetName", (F("id", strk()), F("name", strk()))),
    Variant("Export", (F("id", strk()),)),
    Variant("Import", (F("bundle", structk("ExportBundle")),)),
    Variant("Status"),
    Variant("Identity"),
    Variant("Shutdown"),
    Variant("Dial", (F("addr", strk()),)),
    Variant("ListenViaRelay", (F("relay_addr", strk()),)),
    Variant("Publish", (F("topic", strk()), F("data", bytesk()))),
    Variant("Subscribe", (F("topic", strk()),)),
    Variant("Peers"),
    Variant("RelayReservations"),
    Variant("FindProviders", (F("id", strk()),)),
    Variant("FetchObject", (F("peer_id", strk()), F("id", strk()))),
    Variant("Announce", (F("id", strk()),)),
    Variant("PublishAppPointer", (F("name", strk()), F("manifest", strk()))),
    Variant("ResolveAppPointer", (F("owner", strk()), F("name", strk()))),
    Variant("PublishPointer", (F("name", strk()), F("target", strk()))),
    Variant("ResolvePointer", (F("owner", strk()), F("name", strk()))),
    Variant("SaveProfile", (F("profile", structk("Profile")),)),
    Variant("LoadProfile"),
    Variant("SaveContactList", (F("list", structk("ContactList")),)),
    Variant("LoadContactList"),
    Variant("SaveHomeIndex", (F("index", structk("HomeIndex")),)),
    Variant("LoadHomeIndex"),
    Variant("SetHomeEntryShared", (F("name", strk()), F("shared", bool_()))),
    Variant("DhPublicKey"),
    Variant("PutObjectPublic", (F("data", bytesk()), F("object_type", enmk("ObjectType")), F("name", optk(strk())))),
    Variant("ShareObject", (F("name", strk()), F("object", strk()), F("app", optk(strk())), F("recipients", veck(DH_KEY)))),
    Variant("ClaimUsername", (F("username", strk()),)),
    Variant("ResolveUsername", (F("username", strk()),)),
    Variant("ResolveProfile", (F("owner", strk()),)),
    Variant("ExportIdentity", (F("passphrase", strk()),)),
    Variant("ImportIdentity", (F("bytes", bytesk()), F("passphrase", strk()), F("overwrite", bool_()))),
    Variant("ShowUsername"),
    Variant("Device"),
    Variant("DeviceList"),
    Variant("ResolveOwnerDevice", (F("owner", strk()),)),
    Variant("AddDevice", (F("device_id", strk()), F("device_name", strk()))),
    Variant("RemoveDevice", (F("device_id", strk()),)),
    Variant("InitiatePairing"),
    Variant("CompletePairing", (F("qr", structk("PairingQrData")), F("code", strk()))),
    Variant("SyncFromPeer", (F("peer_id", strk()),)),
    Variant("SyncDeviceList"),
    Variant(
        "GrantCapability",
        (F("subject", strk()), F("resource", enmk("Resource")), F("permissions", veck(enmk("Permission"))), F("expires_at", optk(u64()))),
    ),
    Variant("ListCapabilities"),
    Variant("RevokeCapability", (F("id", strk()),)),
    Variant("CheckCapability", (F("capability", structk("Capability")),)),
    Variant("CheckAccess", (F("subject", strk()), F("permission", enmk("Permission")), F("resource", enmk("Resource")))),
    Variant("StartServeSession", (F("edge_addr", strk()), F("app_id", strk()))),
    Variant("StopServeSession"),
)

NODE_RESPONSE: tuple[Variant, ...] = (
    Variant("ObjectCreated", (F("id", strk()),)),
    Variant("DhPublicKey", (F("key", DH_KEY),)),
    Variant("Concatenated", (F("id", strk()),)),
    Variant("NameSet"),
    Variant("Object", (F("object", structk("Object")),)),
    Variant("Objects", (F("objects", veck(structk("ObjectInfo"))),)),
    Variant("ObjectDeleted"),
    Variant("Exported", (F("bundle", structk("ExportBundle")),)),
    Variant("Imported"),
    Variant("Status", (F("identity", strk()), F("objects", u64()), F("peers", u64()))),
    Variant("Error", (F("message", strk()),)),
    Variant("Identity", (F("identity_id", strk()),)),
    Variant("ShutdownAccepted"),
    Variant("Dialed"),
    Variant("ListeningViaRelay"),
    Variant("Published"),
    Variant("Subscribed"),
    Variant("PubSub", (F("0", structk("PubSubMessage")),)),
    Variant("Peers", (F("peers", veck(structk("PeerInfo"))),)),
    Variant("RelayReservations", (F("reservations", veck(structk("RelayReservationInfo"))),)),
    Variant("Providers", (F("peer_ids", veck(strk())),)),
    Variant("Announced"),
    Variant("AppPointerPublished"),
    Variant("AppPointer", (F("record", optk(structk("AppPointerRecord"))),)),
    Variant("PointerPublished"),
    Variant("Pointer", (F("record", optk(structk("AppPointerRecord"))),)),
    Variant("ProfileSaved", (F("id", strk()),)),
    Variant("Profile", (F("profile", optk(structk("Profile"))),)),
    Variant("ContactListSaved", (F("id", strk()),)),
    Variant("ContactList", (F("list", optk(structk("ContactList"))),)),
    Variant("HomeIndexSaved", (F("id", strk()),)),
    Variant("HomeIndex", (F("index", optk(structk("HomeIndex"))),)),
    Variant("UsernameClaimed"),
    Variant("Username", (F("username", optk(strk())),)),
    Variant("UsernameOwner", (F("owner", optk(strk())),)),
    Variant("IdentityExported", (F("bytes", bytesk()),)),
    Variant("IdentityImported", (F("identity_id", strk()),)),
    Variant("Device", (F("peer_id", strk()), F("device_name", strk()))),
    Variant("DeviceList", (F("devices", veck(structk("DeviceInfo"))),)),
    Variant("OwnerDevice", (F("peer_id", optk(strk())),)),
    Variant("DeviceAdded"),
    Variant("DeviceRemoved"),
    Variant("PairingQr", (F("qr", structk("PairingQrData")),)),
    Variant("PairingComplete", (F("message", strk()),)),
    Variant("SyncComplete", (F("result", structk("SyncResult")),)),
    Variant("CapabilityGranted", (F("capability", structk("Capability")),)),
    Variant("Capabilities", (F("index", optk(structk("CapabilityIndex"))),)),
    Variant("CapabilityRevoked"),
    Variant("CapabilityCheck", (F("valid", bool_()), F("reason", strk()))),
    Variant("AccessAllowed", (F("allowed", bool_()),)),
    Variant("ServeSessionStarted", (F("app_id", strk()), F("root_url", strk()))),
    Variant("ServeSessionStopped"),
)


# ---------------------------------------------------------------------------
# Writer / Reader
# ---------------------------------------------------------------------------

class Writer:
    __slots__ = ("buf",)

    def __init__(self) -> None:
        self.buf = bytearray()

    def u8(self, v: int) -> None:
        self.buf.append(v & 0xFF)

    def bool(self, b: bool) -> None:
        self.buf.append(1 if b else 0)

    def u32(self, v: int) -> None:
        self.buf += struct.pack("<I", v & 0xFFFFFFFF)

    def u64(self, v: int) -> None:
        self.buf += struct.pack("<Q", v & 0xFFFFFFFFFFFFFFFF)

    def string(self, s: str) -> None:
        b = s.encode("utf-8")
        self.u64(len(b))
        self.buf += b

    def bytes(self, b: bytes) -> None:
        self.u64(len(b))
        self.buf += b

    def bytesn(self, b: bytes | list[int]) -> None:
        self.buf += bytes(b)

    def raw(self, b: bytes) -> None:
        self.buf += b

    def result(self) -> bytes:
        return bytes(self.buf)


class Reader:
    __slots__ = ("buf", "pos")

    def __init__(self, data: bytes) -> None:
        self.buf = data
        self.pos = 0

    def _need(self, n: int) -> bytes:
        end = self.pos + n
        if end > len(self.buf):
            raise ValueError(f"bincode: truncated input (need {n} bytes at {self.pos})")
        out = self.buf[self.pos:end]
        self.pos = end
        return out

    def u8(self) -> int:
        return self._need(1)[0]

    def bool(self) -> bool:
        return self.u8() != 0

    def u32(self) -> int:
        return struct.unpack("<I", self._need(4))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self._need(8))[0]

    def string(self) -> str:
        n = self.u64()
        return self._need(n).decode("utf-8")

    def bytes(self) -> bytes:
        n = self.u64()
        return self._need(n)

    def bytesn(self, n: int) -> bytes:
        return self._need(n)

    @property
    def done(self) -> bool:
        return self.pos >= len(self.buf)


# ---------------------------------------------------------------------------
# Encode / decode
# ---------------------------------------------------------------------------

def _variant_index(variants: tuple[Variant, ...], value: Any, path: str) -> int:
    if isinstance(value, str):
        for i, v in enumerate(variants):
            if v.name == value:
                return i
        raise ValueError(f"{path}: unknown variant {value!r}")
    if isinstance(value, dict) and len(value) == 1:
        key = next(iter(value))
        for i, v in enumerate(variants):
            if v.name == key:
                return i
        raise ValueError(f"{path}: unknown variant {key!r}")
    raise ValueError(f"{path}: cannot resolve enum variant from {value!r}")


def _to_bytes(value: Any, path: str) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, list):
        return bytes(int(x) for x in value)
    if isinstance(value, str):
        return bytes.fromhex(value)
    raise ValueError(f"{path}: expected byte array, got {type(value).__name__}")


def _fields_for(kind: WireKind) -> tuple[Field, ...]:
    if kind.t == "struct":
        try:
            return STRUCTS[kind.name]
        except KeyError:
            raise ValueError(f"unknown wire struct {kind.name!r}") from None
    return ()


def _variants_for(kind: WireKind) -> tuple[Variant, ...]:
    if kind.t == "enum":
        try:
            return ENUMS[kind.name]
        except KeyError:
            raise ValueError(f"unknown wire enum {kind.name!r}") from None
    return ()


def encode_value(w: Writer, kind: WireKind, value: Any, path: str) -> None:
    t = kind.t
    if t == "u32":
        w.u32(int(value))
    elif t == "u64":
        w.u64(int(value))
    elif t == "u8":
        w.u8(int(value))
    elif t == "bool":
        w.bool(bool(value))
    elif t == "string":
        if not isinstance(value, str):
            raise ValueError(f"{path}: expected string, got {value!r}")
        w.string(value)
    elif t == "bytes":
        w.bytes(_to_bytes(value, path))
    elif t == "bytesN":
        b = _to_bytes(value, path)
        if kind.n and len(b) != kind.n:
            raise ValueError(f"{path}: expected {kind.n} bytes, got {len(b)}")
        w.bytesn(b)
    elif t == "struct":
        _encode_fields(w, _fields_for(kind), value, path)
    elif t == "enum":
        _encode_variants(w, _variants_for(kind), value, path)
    elif t == "vec":
        arr = value if isinstance(value, list) else []
        w.u64(len(arr))
        for i, x in enumerate(arr):
            encode_value(w, kind.of, x, f"{path}[{i}]")
    elif t == "opt":
        if value is None:
            w.u8(0)
        else:
            w.u8(1)
            encode_value(w, kind.of, value, path)
    elif t == "map":
        if not isinstance(value, dict):
            raise ValueError(f"{path}: expected dict, got {value!r}")
        keys = sorted(value)
        w.u64(len(keys))
        for k in keys:
            w.string(k)
            encode_value(w, kind.of, value[k], f"{path}.{k}")
    else:
        raise ValueError(f"{path}: unknown wire kind {t!r}")


def _encode_fields(w: Writer, fields: tuple[Field, ...], value: Any, path: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected struct object, got {value!r}")
    for f in fields:
        if f.name not in value:
            raise ValueError(f"{path}.{f.name}: missing field")
        encode_value(w, f.kind, value[f.name], f"{path}.{f.name}")


def _encode_variants(w: Writer, variants: tuple[Variant, ...], value: Any, path: str) -> None:
    idx = _variant_index(variants, value, path)
    w.u32(idx)
    variant = variants[idx]
    if not variant.fields:
        return
    if variant.is_newtype:
        encode_value(w, variant.fields[0].kind, value[variant.name], f"{path}.{variant.name}")
        return
    payload = value[variant.name]
    if not isinstance(payload, dict):
        raise ValueError(f"{path}.{variant.name}: expected struct object")
    for f in variant.fields:
        if f.name not in payload:
            raise ValueError(f"{path}.{variant.name}.{f.name}: missing field")
        encode_value(w, f.kind, payload[f.name], f"{path}.{variant.name}.{f.name}")


def decode_value(r: Reader, kind: WireKind, path: str) -> Any:
    t = kind.t
    if t == "u32":
        return r.u32()
    if t == "u64":
        return r.u64()
    if t == "u8":
        return r.u8()
    if t == "bool":
        return r.bool()
    if t == "string":
        return r.string()
    if t == "bytes":
        return list(r.bytes())
    if t == "bytesN":
        return list(r.bytesn(kind.n))
    if t == "struct":
        return _decode_fields(r, _fields_for(kind))
    if t == "enum":
        return _decode_variants(r, _variants_for(kind), path)
    if t == "vec":
        n = r.u64()
        return [decode_value(r, kind.of, f"{path}[{i}]") for i in range(n)]
    if t == "opt":
        if r.u8() == 0:
            return None
        return decode_value(r, kind.of, path)
    if t == "map":
        n = r.u64()
        out: dict[str, Any] = {}
        for _ in range(n):
            k = r.string()
            out[k] = decode_value(r, kind.of, f"{path}.{k}")
        return out
    raise ValueError(f"{path}: unknown wire kind {t!r}")


def _decode_fields(r: Reader, fields: tuple[Field, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in fields:
        out[f.name] = decode_value(r, f.kind, f.name)
    return out


def _decode_variants(r: Reader, variants: tuple[Variant, ...], path: str) -> Any:
    idx = r.u32()
    if idx >= len(variants):
        raise ValueError(f"{path}: unknown variant tag {idx}")
    variant = variants[idx]
    if not variant.fields:
        return variant.name
    if variant.is_newtype:
        return {variant.name: decode_value(r, variant.fields[0].kind, path)}
    return {variant.name: _decode_fields(r, variant.fields)}


# ---------------------------------------------------------------------------
# Commands / responses / framing
# ---------------------------------------------------------------------------

def encode_command(value: Any) -> bytes:
    w = Writer()
    _encode_variants(w, NODE_COMMAND, value, "command")
    return w.result()


def decode_command(data: bytes) -> Any:
    r = Reader(data)
    return _decode_variants(r, NODE_COMMAND, "command")


def encode_response(value: Any) -> bytes:
    w = Writer()
    _encode_variants(w, NODE_RESPONSE, value, "response")
    return w.result()


def decode_response(data: bytes) -> Any:
    r = Reader(data)
    return _decode_variants(r, NODE_RESPONSE, "response")


def encode_struct(name: str, value: Any) -> bytes:
    w = Writer()
    encode_value(w, structk(name), value, name)
    return w.result()


def encode_struct_with_kind(kind: WireKind, value: Any, path: str) -> bytes:
    w = Writer()
    encode_value(w, kind, value, path)
    return w.result()


def decode_struct(name: str, data: bytes) -> Any:
    r = Reader(data)
    return decode_value(r, structk(name), name)


def frame(payload: bytes) -> bytes:
    return struct.pack("<I", len(payload)) + payload


def read_frame(r: Any) -> bytes:
    """Reads exactly one framed payload from a file-like object (`read` returns bytes).""" 
    hdr = _read_exact(r, 4)
    (n,) = struct.unpack("<I", hdr)
    return _read_exact(r, n)


def _read_exact(r: Any, n: int) -> bytes:
    if hasattr(r, "recv"):
        chunks = bytearray()
        while len(chunks) < n:
            part = r.recv(n - len(chunks))
            if not part:
                raise EOFError("socket closed")
            chunks += part
        return bytes(chunks)
    chunks = bytearray()
    while len(chunks) < n:
        part = r.read(n - len(chunks))
        if not part:
            raise EOFError("unexpected end of input")
        chunks += part
    return bytes(chunks)
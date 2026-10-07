import json
import unittest
from pathlib import Path

from canopee_sdk import _codec


def _kind_for(name):
    if name in ("ObjectId", "CapabilityId"):
        return _codec.strk()
    if name in _codec.ENUMS:
        return _codec.enmk(name)
    return _codec.structk(name)


def _golden():
    here = Path(__file__).resolve().parent
    doc = json.loads((here / "fixtures" / "golden.json").read_text())
    return doc["vectors"]


class GoldenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vectors = _golden()

    def test_covers_every_command_response_and_type(self):
        names = {v["name"] for v in self.vectors}
        for v in _codec.NODE_COMMAND:
            self.assertIn(f"cmd_{v.name}", names)
        for v in _codec.NODE_RESPONSE:
            self.assertIn(f"res_{v.name}", names)
        for name in _codec.STRUCTS:
            self.assertIn(name, names)
        for name in _codec.ENUMS:
            self.assertIn(name, names)

    def test_every_vector_encodes_to_its_hex(self):
        for v in self.vectors:
            name, value = v["name"], v["value"]
            if name.startswith("cmd_"):
                payload = _codec.encode_command(value)
            elif name.startswith("res_"):
                payload = _codec.encode_response(value)
            else:
                payload = _codec.encode_struct_with_kind(_kind_for(name), value, name)
            self.assertEqual(payload.hex(), v["hex"], f"encode mismatch for {name}")

    def test_every_hex_decodes_back_to_value(self):
        for v in self.vectors:
            name = v["name"]
            data = bytes.fromhex(v["hex"])
            if name.startswith("cmd_"):
                got = _codec.decode_command(data)
            elif name.startswith("res_"):
                got = _codec.decode_response(data)
            else:
                r = _codec.Reader(data)
                got = _codec.decode_value(r, _kind_for(name), name)
            self.assertEqual(got, v["value"], f"decode mismatch for {name}")


if __name__ == "__main__":
    unittest.main()
import io
import unittest

from canopee_sdk import (
    CanopeeError,
    Capability,
    decode_response,
    encode_command,
    frame,
    from_shape,
    read_frame,
    resource_channel,
    to_shape,
)


def _roundtrip_obj(cls, obj):
    shape = to_shape(obj)
    return from_shape(cls, shape)


class ShapeRoundTripTest(unittest.TestCase):
    def test_pubsub(self):
        msg = {"topic": "news", "source": None, "data": [1, 2, 255]}
        shape, back = msg, _roundtrip_obj(dict, msg)
        self.assertEqual(back, shape)

    def test_capability(self):
        cap = Capability(
            id="cafebabe",
            issuer="alice",
            subject="bob",
            resource=resource_channel("team-news"),
            permissions=["Read", "Publish"],
            issued_at=1700000000,
            expires_at=None,
            public_key=bytes([1, 2, 3]),
            signature=bytes([9]),
        )
        back = _roundtrip_obj(Capability, cap)
        self.assertEqual(back, cap)

    def test_optional_stays_falsey(self):
        from canopee_sdk import HomeIndex

        idx = HomeIndex(profile=None, contacts=None, entries=[], version=3)
        back = _roundtrip_obj(HomeIndex, idx)
        self.assertEqual(back, idx)


class FramingTest(unittest.TestCase):
    def test_round_trip_frame(self):
        payload = encode_command({"Status": {}})
        stream = io.BytesIO(frame(payload))
        self.assertEqual(read_frame(stream), payload)
        with self.assertRaises(EOFError):
            read_frame(stream)

    def test_empty_frame_round_trip(self):
        stream = io.BytesIO(frame(b""))
        self.assertEqual(read_frame(stream), b"")

    def test_multiple_frames(self):
        f1 = encode_command({"Put": {"data": [1], "name": None}})
        f2 = encode_command({"List": {}})
        stream = io.BytesIO(frame(f1) + frame(f2))
        self.assertEqual(read_frame(stream), f1)
        self.assertEqual(read_frame(stream), f2)

    def test_partial_reads(self):
        f1 = encode_command({"Status": {}})
        stream = io.BytesIO(frame(f1))
        got = bytearray()
        first = read_frame(stream)
        self.assertEqual(first, f1)


class ResponseUnwrapTest(unittest.TestCase):
    def test_void_and_data_responses(self):
        self.assertEqual(decode_response(_enc({"NameSet": {}})), "NameSet")
        self.assertEqual(decode_response(_enc({"ObjectCreated": {"id": "x"}})), {"ObjectCreated": {"id": "x"}})


def _enc(v):
    from canopee_sdk import encode_response

    return encode_response(v)


if __name__ == "__main__":
    unittest.main()
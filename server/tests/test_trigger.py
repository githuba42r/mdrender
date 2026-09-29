import base64
import json
import os

from server.app.trigger import (build_manifest, derive_iv, manifest_bytes,
                               open_trigger, seal_trigger)


def test_seal_open_roundtrip():
    key = os.urandom(32)
    sealed = seal_trigger(key, "https://push.example.com", "p1", "ck-1")
    got = open_trigger(key, sealed)
    assert got["server_url"] == "https://push.example.com"
    assert got["push_id"] == "p1"
    assert got["challenge_key"] == "ck-1"
    assert got["v"] == 1


def test_wrong_key_cannot_open():
    sealed = seal_trigger(os.urandom(32), "https://x", "p1", "ck-1")
    assert open_trigger(os.urandom(32), sealed) is None


def test_tampered_ciphertext_is_rejected():
    key = os.urandom(32)
    sealed = seal_trigger(key, "https://x", "p1", "ck-1")
    raw = bytearray(base64.b64decode(sealed["c"]))
    raw[0] ^= 0xFF
    sealed["c"] = base64.b64encode(bytes(raw)).decode()
    assert open_trigger(key, sealed) is None


def test_swapped_iv_is_rejected():
    """A doorbell for another push must not open, even under the right key."""
    key = os.urandom(32)
    sealed = seal_trigger(key, "https://x", "p1", "ck-1")
    other = seal_trigger(key, "https://x", "p2", "ck-1")
    sealed["i"] = other["i"]
    assert open_trigger(key, sealed) is None


def test_iv_is_deterministic_and_key_bound():
    key = os.urandom(32)
    assert derive_iv(key, "p1") == derive_iv(key, "p1")
    assert derive_iv(key, "p1") != derive_iv(key, "p2")
    assert derive_iv(key, "p1") != derive_iv(os.urandom(32), "p1")
    assert len(derive_iv(key, "p1")) == 12


def test_retry_reproduces_identical_ciphertext():
    """A re-ringed doorbell must be byte-identical so the phone can recognise it."""
    key = os.urandom(32)
    assert seal_trigger(key, "https://x", "p1", "ck-1") == \
        seal_trigger(key, "https://x", "p1", "ck-1")


def test_trigger_size_is_independent_of_file_count():
    """The whole point of the redesign: size cannot grow with the manifest."""
    key = os.urandom(32)
    sizes = {
        n: len(json.dumps(seal_trigger(key, "https://push.example.com", f"p{n}", "ck-1")))
        for n in (1, 100, 10000)
    }
    assert max(sizes.values()) - min(sizes.values()) < 32
    assert max(sizes.values()) < 400


def test_sealed_has_exactly_the_two_fcm_fields():
    """The route sends {"p": sealed["c"], "i": sealed["i"]} and nothing else."""
    sealed = seal_trigger(os.urandom(32), "https://x", "p1", "ck-1")
    assert set(sealed) == {"i", "c"}


def test_manifest_is_built_from_rows_and_is_canonical():
    rows = [
        {"file_id": "f1", "file_name": "a.md", "file_path": "", "size": 12,
         "retrieval_key": "k1"},
        {"file_id": "f2", "file_name": "b.md", "file_path": "sub", "size": 34,
         "retrieval_key": "k2"},
    ]
    manifest = build_manifest("p1", "2026-08-29T00:00:00+00:00", rows)
    assert manifest["push_id"] == "p1"
    assert [f["file_id"] for f in manifest["files"]] == ["f1", "f2"]
    assert manifest["files"][1]["path"] == "sub"
    # Signing and transmission must be the same bytes, so the serialisation is
    # deterministic: sorted keys, no incidental whitespace.
    raw = manifest_bytes(manifest)
    assert raw == json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode()
    assert json.loads(raw) == manifest

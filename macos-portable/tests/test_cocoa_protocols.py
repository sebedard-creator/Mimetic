"""Exercise protocol selection with the actual signatures reported on Monterey.

This tests the portable selector adapter; it does not execute AppKit on Windows.
"""
from types import SimpleNamespace

import pytest

from mimetic_macos.cocoa_protocols import selector_for_protocol


@pytest.mark.parametrize("signature", [b"Q@:@", b"c@:@c", b"c@:@", b"B@:@B"])
def test_runtime_signature_wins_over_legacy_metadata(signature):
    requested = []
    name = b"callback:flag:" if signature in (b"c@:@c", b"B@:@B") else b"callback:"
    protocol = SimpleNamespace(instanceMethods=lambda: [
        {"selector": b"unrelated:", "typestr": b"v@:@"},
        {"selector": name, "typestr": signature},
    ])
    def named_selector(selector, *, signature):
        requested.append((selector, signature))
        return lambda callback: SimpleNamespace(selector=selector, signature=signature, callable=callback)
    bridge = SimpleNamespace(protocolNamed=lambda _: protocol, namedSelector=named_selector)
    decorated = selector_for_protocol(bridge, "NativeDelegate", name)(lambda *args: True)
    assert requested == [(name, signature)]
    assert decorated.callable(None) is True


def test_missing_method_fails_with_protocol_and_selector_context():
    bridge = SimpleNamespace(protocolNamed=lambda _: SimpleNamespace(instanceMethods=lambda: []))
    with pytest.raises(RuntimeError, match="NSWindowDelegate.*windowShouldClose:"):
        selector_for_protocol(bridge, "NSWindowDelegate", b"windowShouldClose:")

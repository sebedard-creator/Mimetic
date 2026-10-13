"""Use Cocoa's runtime protocol signatures instead of wheel metadata guesses."""
from __future__ import annotations


def selector_for_protocol(bridge, protocol_name: str, selector_name: bytes):
    """Decorate an instance method using the signature on the running Mac.

    PyObjC 10.3.2 metadata uses I for NSApplicationTerminateReply and Z for
    BOOL, while Monterey's formal protocols use Q and c. The formal protocol
    is the authority used by PyObjC's own class validation.
    """
    protocol = bridge.protocolNamed(protocol_name)
    for method in protocol.instanceMethods():
        if method["selector"] == selector_name:
            return bridge.namedSelector(selector_name, signature=method["typestr"])
    raise RuntimeError(
        f"Cocoa protocol {protocol_name} has no instance method "
        f"{selector_name.decode('ascii')}"
    )

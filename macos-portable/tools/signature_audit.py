"""Verify Mach-O CodeDirectory pages and bundle seals without CMS certificates.

An ad-hoc signature intentionally has no CMS signer. rcodesign's verify command
currently flags that as a CMS error, so verify the actual code/resource digests.
Apple's codesign remains the final platform check on the target Mac.
"""
import hashlib
import struct
from pathlib import Path

from macho_audit import slices


def verify(path: Path, info=None, resources=None):
    data = path.read_bytes()
    directories = []
    for start, size, endian in slices(data):
        count = struct.unpack_from(endian + "I", data, start + 16)[0]
        offset = start + 32
        signature = None
        for _ in range(count):
            kind, length = struct.unpack_from(endian + "II", data, offset)
            if kind == 0x1D:
                position, signature_size = struct.unpack_from(endian + "II", data, offset + 8)
                signature = data[start + position:start + position + signature_size]
            offset += length
        if signature is None:
            raise ValueError("Signature absente : " + str(path))
        magic, length, count = struct.unpack_from(">III", signature)
        if magic != 0xFADE0CC0 or length > len(signature):
            raise ValueError("Superblob invalide")
        blobs = {}
        for index in range(count):
            slot, pos = struct.unpack_from(">II", signature, 12 + index*8)
            _, blob_len = struct.unpack_from(">II", signature, pos)
            blobs[slot] = signature[pos:pos+blob_len]
        found = False
        for slot, blob in blobs.items():
            if struct.unpack_from(">I", blob)[0] != 0xFADE0C02:
                continue
            found = True
            _, length, version, flags, hash_offset, ident_offset, special, code_slots, limit = struct.unpack_from(">9I", blob)
            hash_size, hash_type, _, page_exponent = struct.unpack_from("4B", blob, 36)
            if limit == 0xFFFFFFFF and version >= 0x20300:
                limit = struct.unpack_from(">Q", blob, 56)[0]
            algorithm = {1: "sha1", 2: "sha256", 3: "sha256", 4: "sha384"}[hash_type]
            digest = lambda value: hashlib.new(algorithm, value).digest()[:hash_size]
            page_size = 1 << page_exponent if page_exponent else max(limit, 1)
            if code_slots != (limit + page_size - 1) // page_size:
                raise ValueError("Nombre de pages signées incohérent")
            for index in range(code_slots):
                page = data[start + index*page_size:start + min((index+1)*page_size, limit)]
                expected = blob[hash_offset + index*hash_size:hash_offset + (index+1)*hash_size]
                if digest(page) != expected:
                    raise ValueError(f"Page signée modifiée : {path}, page {index}")
            for special_slot, body in ((1, info), (2, blobs.get(2)), (3, resources)):
                if body is None or special_slot > special:
                    continue
                expected = blob[hash_offset-special_slot*hash_size:hash_offset-(special_slot-1)*hash_size]
                if digest(body) != expected:
                    raise ValueError(f"Slot spécial {special_slot} modifié : {path}")
            directories.append({"digest": algorithm, "pages": code_slots, "ad_hoc": bool(flags & 2),
                                "identifier": blob[ident_offset:].split(b"\0", 1)[0].decode("utf-8")})
        if not found:
            raise ValueError("CodeDirectory absent")
    return directories

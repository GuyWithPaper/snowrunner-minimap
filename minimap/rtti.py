"""Locate C++ objects by RTTI class name in a running MSVC x64 process (read-only)."""
import struct
import numpy as np


def read_module(P, base, size, chunk=1 << 22):
    buf = bytearray(size)
    for off in range(0, size, chunk):
        n = min(chunk, size - off)
        b = P.read(base + off, n)
        if b:
            buf[off:off + len(b)] = b
    return bytes(buf)


def vtables_for(img, base, cls):
    """Return absolute vtable addresses whose RTTI type name is .?AV<cls>@@ (cls may include @namespace)."""
    name = b".?AV" + cls.encode() + b"@@\x00"
    out = []
    start = 0
    while True:
        i = img.find(name, start)
        if i < 0:
            break
        start = i + 1
        td_rva = i - 0x10
        # CompleteObjectLocator: sig(1) off cdoff tdRVA chdRVA selfRVA
        pat = struct.pack("<I", td_rva)
        j = 0
        while True:
            j = img.find(pat, j)
            if j < 0:
                break
            col = j - 12
            if col >= 0:
                sig, off, cdoff, td, chd, selfrva = struct.unpack_from("<6I", img, col)
                if sig == 1 and td == td_rva and selfrva == col:
                    # vtable[-1] points to COL
                    ptr = struct.pack("<Q", base + col)
                    k = 0
                    while True:
                        k = img.find(ptr, k)
                        if k < 0:
                            break
                        if k % 8 == 0:
                            out.append((base + k + 8, off))
                        k += 1
            j += 1
    return out


def find_instances(P, vt, max_hits=64):
    """Scan writable memory for objects whose first qword equals vt."""
    hits = []
    for addr, a in P.scan_chunks():
        n = a.size // 8 * 8
        q = a[:n].view("<u8")
        for i in np.nonzero(q == vt)[0]:
            hits.append(addr + int(i) * 8)
            if len(hits) >= max_hits:
                return hits
    return hits


def find_static_refs(P, img, base, value):
    pat = struct.pack("<Q", value)
    out, k = [], 0
    while True:
        k = img.find(pat, k)
        if k < 0:
            return out
        if k % 8 == 0:
            out.append(k)
        k += 1


def class_name(P, obj):
    """RTTI class name of the object at obj (via vtable[-1] -> COL -> TypeDescriptor)."""
    try:
        vt = P.u64(obj)
        col = P.u64(vt - 8)
        sig, off, cd, td_rva, chd, self_rva = struct.unpack("<6I", P.read(col, 24))
        if sig != 1:
            return None
        imgbase = col - self_rva
        nm = P.read(imgbase + td_rva + 0x10, 128)
        return nm.split(b"\0")[0].decode(errors="replace")
    except Exception:
        return None

"""Minimal read-only process memory access for Windows (ctypes, no injection, no writes)."""
import ctypes, ctypes.wintypes as wt
import numpy as np

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01
WRITABLE = {0x04, 0x08, 0x40, 0x80}  # RW, WC, ERW, EWC


class MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_uint64), ("AllocationBase", ctypes.c_uint64),
                ("AllocationProtect", wt.DWORD), ("PartitionId", wt.WORD), ("_a", wt.WORD),
                ("RegionSize", ctypes.c_uint64), ("State", wt.DWORD), ("Protect", wt.DWORD),
                ("Type", wt.DWORD), ("_b", wt.DWORD)]


k32.OpenProcess.restype = wt.HANDLE
k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]
k32.VirtualQueryEx.restype = ctypes.c_size_t


def find_pid(exe="SnowRunner.exe"):
    import subprocess
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe}", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, creationflags=0x08000000).stdout
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1 and parts[0].lower() == exe.lower():
            return int(parts[1])
    return None


class Proc:
    def __init__(self, pid):
        self.pid = pid
        self.h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not self.h:
            raise OSError(ctypes.get_last_error(), "OpenProcess failed")
        self._buf = None

    def close(self):
        if self.h:
            k32.CloseHandle(self.h); self.h = None

    def alive(self):
        code = wt.DWORD()
        k32.GetExitCodeProcess(self.h, ctypes.byref(code))
        return code.value == 259

    def read(self, addr, n):
        buf = ctypes.create_string_buffer(n)
        got = ctypes.c_size_t()
        if not k32.ReadProcessMemory(self.h, ctypes.c_void_p(addr), buf, n, ctypes.byref(got)):
            return None
        return buf.raw[: got.value]

    def read_into(self, addr, arr):
        got = ctypes.c_size_t()
        ok = k32.ReadProcessMemory(self.h, ctypes.c_void_p(addr), arr.ctypes.data, arr.nbytes, ctypes.byref(got))
        return ok and got.value == arr.nbytes

    def u64(self, addr):
        b = self.read(addr, 8)
        return int.from_bytes(b, "little") if b and len(b) == 8 else None

    def f32(self, addr, n=1):
        b = self.read(addr, 4 * n)
        if not b or len(b) != 4 * n:
            return None
        return np.frombuffer(b, "<f4")

    def regions(self, writable=True, private_only=False, max_size=1 << 31):
        addr, mbi = 0, MBI()
        while addr < 0x7FFFFFFFFFFF:
            if not k32.VirtualQueryEx(self.h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
                break
            base, size = mbi.BaseAddress, mbi.RegionSize
            if (mbi.State == MEM_COMMIT and not (mbi.Protect & (PAGE_GUARD | PAGE_NOACCESS))
                    and (not writable or (mbi.Protect & 0xFF) in WRITABLE)
                    and (not private_only or mbi.Type == MEM_PRIVATE) and size <= max_size):
                yield base, size
            addr = base + size

    def module_base(self, name="SnowRunner.exe"):
        hmods = (wt.HMODULE * 1024)()
        need = wt.DWORD()
        psapi.EnumProcessModulesEx.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), wt.DWORD]
        psapi.GetModuleBaseNameW.argtypes = [wt.HANDLE, wt.HMODULE, wt.LPWSTR, wt.DWORD]
        if not psapi.EnumProcessModulesEx(self.h, hmods, ctypes.sizeof(hmods), ctypes.byref(need), 3):
            return None, None
        info = (ctypes.c_uint64 * 3)()
        psapi.GetModuleInformation.argtypes = [wt.HANDLE, wt.HMODULE, ctypes.c_void_p, wt.DWORD]
        for i in range(need.value // ctypes.sizeof(wt.HMODULE)):
            nm = ctypes.create_unicode_buffer(260)
            psapi.GetModuleBaseNameW(self.h, hmods[i], nm, 260)
            if nm.value.lower() == name.lower():
                psapi.GetModuleInformation(self.h, hmods[i], info, ctypes.sizeof(info))
                return int(info[0]), int(info[1]) & 0xFFFFFFFF
        return None, None

    def scan_chunks(self, chunk=1 << 24, **kw):
        """Yield (address, uint8 array) over readable regions."""
        for base, size in self.regions(**kw):
            off = 0
            while off < size:
                n = min(chunk, size - off)
                a = np.empty(n, np.uint8)
                if self.read_into(base + off, a):
                    yield base + off, a
                off += n

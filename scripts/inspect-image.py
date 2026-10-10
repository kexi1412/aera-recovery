#!/usr/bin/env python3
"""Inspect an Android boot/recovery image and dump its recovery ramdisk.

Answers the only question that matters for this bug: does the built image
actually contain the VINTF declaration, or did the board-recovery-image-prepare
step get overwritten by the device-tree copy?

Usage:
    python inspect-image.py <image> [--dump <dir>] [--grep <substring>]
"""

from __future__ import annotations

import argparse
import io
import os
import struct
import sys

MAGICS = [
    (b"\x1f\x8b\x08", "gzip"),
    (b"\x02\x21\x4c\x18", "lz4-legacy"),
    (b"\x04\x22\x4d\x18", "lz4-frame"),
    (b"\xfd7zXZ\x00", "xz"),
    (b"BZh", "bzip2"),
    (b"\x5d\x00\x00", "lzma"),
    (b"070701", "cpio-newc-ascii"),
    (b"\xc7\x71\x1c\xc5", "lzop"),
    (b"\x28\xb5\x2f\xfd", "zstd"),
]


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def parse_boot_header(data: bytes) -> dict:
    if data[:8] != b"ANDROID!":
        raise SystemExit("not an Android boot image (bad ANDROID! magic)")

    hdr_version = u32(data, 40)
    info = {
        "magic": data[:8].decode(),
        "kernel_size": u32(data, 8),
        "ramdisk_size": u32(data, 12),
        "os_version": u32(data, 16),
        "header_size": u32(data, 20),
        "header_version": hdr_version,
        "file_size": len(data),
    }

    if hdr_version >= 3:
        info["cmdline"] = data[44:44 + 1536].split(b"\x00")[0].decode("utf-8", "replace")
        page = 4096
        kernel_off = page
        ramdisk_off = kernel_off + ((info["kernel_size"] + page - 1) // page) * page
    else:
        info["kernel_addr"] = u32(data, 8)
        info["kernel_size"] = u32(data, 8)
        raise SystemExit("v0-v2 header not handled; this image is v3+")

    info["kernel_offset"] = kernel_off
    info["ramdisk_offset"] = ramdisk_off
    info["ramdisk_end"] = ramdisk_off + info["ramdisk_size"]
    if info["ramdisk_end"] > len(data):
        info["truncated"] = True
        info["ramdisk_end"] = len(data)
    return info


def detect(data: bytes, off: int) -> str:
    blob = data[off:off + 8]
    for magic, name in MAGICS:
        if blob.startswith(magic):
            return name
    return "unknown/" + blob.hex()


def gunzip(blob: bytes) -> bytes:
    import gzip
    return gzip.decompress(blob)


def _unlz4_legacy(blob: bytes) -> bytes:
    """Decode the legacy lz4 stream Android uses for ramdisks.

    Layout is just the magic 0x184C2102 followed by repeated
    [uint32 LE compressed_size][raw lz4 block]; each block inflates to at most
    8 MB. There is no frame header, no end-of-stream marker other than the
    data running out, and no checksum. lz4.frame cannot read this, which is why
    the ramdisk shows up as "unknown/02214c18" without this path.
    """
    import lz4.block

    if blob[:4] != b"\x02\x21\x4c\x18":
        raise ValueError("not a legacy lz4 stream")

    out = bytearray()
    p = 4
    while p + 4 <= len(blob):
        n = struct.unpack_from("<I", blob, p)[0]
        p += 4
        if n == 0:
            break
        chunk = blob[p:p + n]
        if len(chunk) < n:
            break
        p += n
        # Android caps legacy blocks at 8 MB uncompressed.
        out += lz4.block.decompress(chunk, uncompressed_size=8 << 20)
    return bytes(out)


def unlz4(blob: bytes) -> bytes:
    """Decompress an Android ramdisk lz4 stream, either flavour."""
    import shutil
    import subprocess
    import tempfile

    exe = shutil.which("lz4") or shutil.which("lz4.exe")
    if exe:
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "in.lz4")
            dst = os.path.join(d, "out.bin")
            open(src, "wb").write(blob)
            # -d plus -l is not needed: lz4 CLI autodetects the legacy format
            # only when it is not asked to skip the frame magic.
            r = subprocess.run([exe, "-d", "-f", src, dst],
                               capture_output=True)
            if r.returncode == 0:
                return open(dst, "rb").read()

    errs = []
    if blob[:4] == b"\x02\x21\x4c\x18":
        try:
            return _unlz4_legacy(blob)
        except Exception as e:
            errs.append(f"legacy={e}")
    if blob[:4] == b"\x04\x22\x4d\x18":
        try:
            import lz4.frame
            return lz4.frame.decompress(blob)
        except Exception as e:
            errs.append(f"frame={e}")
    raise SystemExit("lz4 decompression failed: " + "; ".join(errs or ["unknown magic"]))


S_IFMT = 0o170000
S_IFDIR = 0o040000
S_IFLNK = 0o120000


def list_cpio(cpio: bytes) -> list:
    """Walk a newc cpio archive.

    Returns (name, size, data_offset, mode) tuples. Directory entries carry
    S_IFDIR in mode -- Android does NOT use a trailing slash -- and symlinks
    carry S_IFLNK with the target stored as the entry's data. Both matter:
    treating a directory as a regular file makes the whole subtree fail to
    extract, and treating /etc (a symlink to /system/etc) as a real directory
    hides every file that actually lives under /system/etc.
    """
    out = []
    off = 0
    n = len(cpio)
    while off + 110 <= n:
        if cpio[off:off + 6] != b"070701":
            nxt = cpio.find(b"070701", off)
            if nxt == -1:
                break
            off = nxt
            continue
        f = lambda i: int(cpio[off + 6 + i * 8:off + 6 + i * 8 + 8], 16)
        mode = f(1)
        namesize = f(11)
        filesize = f(6)
        name = cpio[off + 110:off + 110 + namesize - 1].decode("utf-8", "replace")
        hdr_end = off + 110 + namesize
        data_off = (hdr_end + 3) & ~3
        out.append((name, filesize, data_off, mode))
        if name == "TRAILER!!!":
            break
        off = (data_off + filesize + 3) & ~3
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--dump", default=None,
                    help="extract the ramdisk into this directory")
    ap.add_argument("--grep", default=None,
                    help="only report cpio entries whose name contains this")
    ap.add_argument("--cat", default=None,
                    help="write a cpio entry matching this name into --outdir")
    ap.add_argument("--outdir", default=None,
                    help="where --cat writes its file")
    args = ap.parse_args()

    data = open(args.image, "rb").read()
    print(f"=== {args.image} ({len(data)} bytes) ===")
    info = parse_boot_header(data)
    for k in ("magic", "header_version", "header_size", "os_version",
              "kernel_size", "ramdisk_size", "kernel_offset",
              "ramdisk_offset", "ramdisk_end"):
        print(f"  {k:16}= {info[k]}")
    if info.get("truncated"):
        print("  !! ramdisk_size exceeds file size; image is truncated")
    if info.get("cmdline"):
        print(f"  cmdline         = {info['cmdline'][:120]}")

    off = info["ramdisk_offset"]
    kind = detect(data, off)
    print(f"  ramdisk format  = {kind}")

    blob = data[off:info["ramdisk_end"]]
    if kind == "gzip":
        cpio = gunzip(blob)
    elif kind in ("lz4-legacy", "lz4-frame"):
        cpio = unlz4(blob)
    elif kind == "cpio-newc-ascii":
        cpio = blob
    else:
        print("  cannot decompress this format here")
        return 1

    print(f"  ramdisk raw     = {len(cpio)} bytes")
    entries = list_cpio(cpio)
    ndir = sum(1 for e in entries if (e[3] & S_IFMT) == S_IFDIR)
    nlnk = sum(1 for e in entries if (e[3] & S_IFMT) == S_IFLNK)
    print(f"  cpio entries    = {len(entries)} "
          f"({ndir} dirs, {nlnk} symlinks, {len(entries)-ndir-nlnk} files)")

    want = None
    for name, size, doff, mode in entries:
        if args.grep and args.grep in name:
            t = "dir" if (mode & S_IFMT) == S_IFDIR else \
                "lnk" if (mode & S_IFMT) == S_IFLNK else "reg"
            print(f"    [{t}] {name}  ({size})")
        if args.cat and name.lstrip("./").startswith(args.cat.lstrip("./")):
            want = (name, size, doff, mode)

    if args.cat:
        if not want:
            print(f"  NOT FOUND in ramdisk: {args.cat}")
            return 2
        name, size, doff, mode = want
        print(f"\n--- {name} ({size} bytes, mode {mode:o}) ---")
        if (mode & S_IFMT) == S_IFLNK:
            print(f"  symlink -> {cpio[doff:doff+size].decode('utf-8','replace')}")
        else:
            # Write to a file rather than stdout: PowerShell redirection would
            # re-encode the text as UTF-16 and corrupt the extracted content.
            dest = os.path.join(args.outdir or ".", os.path.basename(name))
            with open(dest, "wb") as fh:
                fh.write(cpio[doff:doff + size])
            print(f"  written to {dest} ({size} bytes)")

    if args.dump:
        os.makedirs(args.dump, exist_ok=True)
        made = links = 0
        for name, size, doff, mode in entries:
            if name == "TRAILER!!!":
                continue
            rel = name.lstrip("/")
            if not rel:
                continue
            p = os.path.join(args.dump, *rel.split("/"))
            kind = mode & S_IFMT
            try:
                if kind == S_IFDIR:
                    os.makedirs(p, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(p) or args.dump, exist_ok=True)
                if kind == S_IFLNK:
                    # Record the target in a sidecar instead of creating a real
                    # link: Windows needs privileges for that, and the target
                    # usually does not exist in the dump anyway.
                    tgt = cpio[doff:doff + size].decode("utf-8", "replace")
                    with open(p + ".symlink", "w", encoding="utf-8") as fh:
                        fh.write(tgt)
                    links += 1
                else:
                    with open(p, "wb") as fh:
                        fh.write(cpio[doff:doff + size])
                    made += 1
            except Exception as e:
                print(f"    skip {name}: {e}")
        print(f"  extracted {made} files, {links} symlinks to {args.dump}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

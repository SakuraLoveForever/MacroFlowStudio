"""Read PE metadata only; never load or execute the inspected binaries."""

import hashlib
import json
from pathlib import Path

import pefile


def main():
    root = Path(__file__).resolve().parents[3]
    base = root / "研究" / "挂机脚本"
    paths = [base / "ZeusTool.exe", *sorted((base / "Plugin").glob("*.dll"))]
    paths += [base / name for name in (
        "plugin32.dll", "plugin64.dll", "FindPic.dll", "libcef.dll", "libpng.dll",
    )]
    rows = []
    for path in paths:
        pe = pefile.PE(str(path))
        rows.append({
            "path": str(path.relative_to(root)),
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "machine": hex(pe.FILE_HEADER.Machine),
            "subsystem": pe.OPTIONAL_HEADER.Subsystem,
            "clr": bool(pe.OPTIONAL_HEADER.DATA_DIRECTORY[14].VirtualAddress),
            "sections": [{
                "name": s.Name.rstrip(b"\0").decode(errors="replace"),
                "raw": s.SizeOfRawData, "virtual": s.Misc_VirtualSize,
            } for s in pe.sections],
            "imports": [{
                "dll": d.dll.decode(),
                "symbols": [e.name.decode(errors="replace") if e.name
                            else "#" + str(e.ordinal) for e in d.imports],
            } for d in getattr(pe, "DIRECTORY_ENTRY_IMPORT", [])],
            "exports": [{
                "name": e.name.decode(errors="replace") if e.name else None,
                "ordinal": e.ordinal, "rva": hex(e.address),
            } for e in getattr(getattr(pe, "DIRECTORY_ENTRY_EXPORT", None), "symbols", [])],
        })
        pe.close()
    output = Path(__file__).with_name("pe-static-inventory.json")
    output.write_text(json.dumps({
        "artifacts": rows,
        "directory_bytes": sum(p.stat().st_size for p in base.rglob("*") if p.is_file()),
        "exe_dll_bytes": sum(p.stat().st_size for p in base.rglob("*")
                             if p.is_file() and p.suffix.lower() in (".exe", ".dll")),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()

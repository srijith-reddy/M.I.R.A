from __future__ import annotations

import json
import mimetypes
import sys
from pathlib import Path
from urllib.parse import quote


def _data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    if mime == "image/svg+xml":
        safe_chars = "/:;,+?=&()#%[]@!$'*"
        return f"data:{mime},{quote(path.read_text(encoding='utf-8'), safe=safe_chars)}"
    import base64

    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{payload}"


def main() -> int:
    if len(sys.argv) != 3:
        print(json.dumps({"status": "invalid_input", "backend": "vton_stub", "notes": ["Expected request_json and output_dir arguments."]}))
        return 1
    request_path = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = json.loads(request_path.read_text(encoding="utf-8"))

    avatar_uri = _data_uri(Path(payload["avatar_image_path"]))
    garment_uri = _data_uri(Path(payload["garment_image_path"]))
    result_path = output_dir / "front_vton.svg"
    result_path.write_text(
        (
            "<svg xmlns='http://www.w3.org/2000/svg' width='768' height='1280' viewBox='0 0 768 1280'>"
            "<rect width='100%' height='100%' fill='#f7f1ea'/>"
            "<image href='{avatar}' x='72' y='164' width='624' height='952' preserveAspectRatio='xMidYMid slice'/>"
            "<rect x='206' y='410' width='356' height='430' rx='42' fill='#fffaf5' opacity='0.18'/>"
            "<image href='{garment}' x='238' y='362' width='292' height='488' preserveAspectRatio='xMidYMid meet' opacity='0.98'/>"
            "<text x='64' y='90' font-size='28' font-family='Arial' fill='#2f261f'>MIRA Stylist VTON Stub Preview</text>"
            "<text x='64' y='122' font-size='18' font-family='Arial' fill='#66584d'>This is an adapter smoke path, not a learned production VTON model.</text>"
            "</svg>"
        ).format(avatar=avatar_uri, garment=garment_uri),
        encoding="utf-8",
    )
    result = {
        "status": "ok",
        "backend": "vton_stub",
        "generated_preview_path": str(result_path),
        "generated_auxiliary_paths": {},
        "notes": [
            "Stub runner executed successfully.",
            "Replace this script with a learned VTON backend to generate ASOS-style previews.",
        ],
    }
    (output_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

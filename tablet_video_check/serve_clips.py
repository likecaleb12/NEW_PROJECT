#!/usr/bin/env python3
"""
후보 영상을 태블릿 브라우저에서 바로 재생하기 위한 로컬 웹서버.
파일은 그 자리에 두고 스트리밍만 한다 (복사·이동 없음).

사용:
    python serve_clips.py A.MP4 B.MP4@45.2 C.MP4@30
    python serve_clips.py --dir /sdcard/Download/벤츠/DRIVING --match 'E\\.MP4$'

파일 뒤에 @초 를 붙이면 그 지점부터 재생한다.
실행 후 크롬에서 http://localhost:8000 접속.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CHUNK = 256 * 1024
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".3gp", ".webm", ".m4v", ".ts"}


def probe(path: Path) -> dict:
    """재생 가능 여부 판단에 필요한 정보만 뽑는다."""
    if shutil.which("ffprobe") is None:
        return {}
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, errors="replace", stdin=subprocess.DEVNULL)
    if p.returncode != 0 or not p.stdout.strip():
        return {}
    try:
        info = json.loads(p.stdout)
    except json.JSONDecodeError:
        return {}
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    a = next((s for s in streams if s.get("codec_type") == "audio"), {})
    try:
        dur = float(info.get("format", {}).get("duration", 0))
    except (TypeError, ValueError):
        dur = 0.0
    return {
        "vcodec": v.get("codec_name", "?"),
        "acodec": a.get("codec_name", ""),
        "w": v.get("width"), "h": v.get("height"),
        "duration": round(dur, 1),
    }


class Clip:
    def __init__(self, path: Path, mark: float | None, note: str = ""):
        self.path = path
        self.mark = mark
        self.note = note
        self.info = probe(path)
        self.size = path.stat().st_size if path.exists() else 0

    @property
    def playable(self) -> bool:
        # 브라우저가 확실히 재생하는 조합만 '가능'으로 본다.
        return self.info.get("vcodec") in ("h264", "vp8", "vp9", "av1")


PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>충돌 후보 영상</title>
<style>
  :root {{ color-scheme: dark; --bg:#111; --fg:#eee; --mut:#9aa; --line:#333; --acc:#4da3ff; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; padding:16px; background:var(--bg); color:var(--fg);
         font-family: system-ui, -apple-system, "Noto Sans KR", sans-serif; }}
  h1 {{ font-size:18px; margin:0 0 4px; }}
  .sub {{ color:var(--mut); font-size:13px; margin-bottom:14px; }}
  video {{ width:100%; background:#000; border-radius:8px; display:block; }}
  #now {{ font-size:14px; color:var(--acc); margin:8px 0 18px; min-height:20px;
          word-break:break-all; }}
  ol {{ list-style:none; padding:0; margin:0; }}
  li {{ border:1px solid var(--line); border-radius:8px; margin-bottom:10px;
        padding:12px; background:#181818; }}
  li.bad {{ opacity:.55; }}
  .t {{ font-size:15px; font-weight:600; word-break:break-all; }}
  .m {{ color:var(--mut); font-size:12px; margin-top:4px; }}
  .row {{ display:flex; gap:8px; margin-top:10px; flex-wrap:wrap; }}
  button, a.btn {{ flex:1 1 auto; min-width:120px; padding:11px 10px; font-size:14px;
            border:0; border-radius:6px; background:var(--acc); color:#001;
            font-weight:700; cursor:pointer; text-align:center; text-decoration:none; }}
  a.btn {{ background:#2a2a2a; color:var(--fg); font-weight:500; }}
  .warn {{ color:#ffb86b; font-size:12px; margin-top:6px; }}
</style></head><body>
<h1>충돌 후보 영상 {n}개</h1>
<div class="sub">제목을 누르면 아래 재생기에서 표시된 시점부터 재생됩니다.</div>
<video id="v" controls playsinline preload="metadata"></video>
<div id="now">재생할 영상을 선택하세요</div>
<ol>{items}</ol>
<script>
const V = document.getElementById('v'), NOW = document.getElementById('now');
function play(src, mark, title) {{
  if (V.dataset.src !== src) {{ V.src = src; V.dataset.src = src; V.load(); }}
  const go = () => {{ V.currentTime = mark; V.play().catch(()=>{{}}); }};
  if (V.readyState >= 1) go(); else V.addEventListener('loadedmetadata', go, {{once:true}});
  NOW.textContent = title + (mark > 0 ? '  ▶ ' + mark + '초부터' : '');
  window.scrollTo({{top:0, behavior:'smooth'}});
}}
</script></body></html>
"""


def build_page(clips: list[Clip]) -> bytes:
    items = []
    for i, c in enumerate(clips):
        info = c.info
        res = f"{info.get('w')}x{info.get('h')}" if info.get("w") else "?"
        meta = (f"{res} · {info.get('vcodec', '?')} · "
                f"{info.get('duration', 0)}초 · {c.size / 1048576:.0f}MB")
        mark = c.mark or 0
        title = html.escape(c.path.name)
        warn = ""
        cls = ""
        if info and not c.playable:
            cls = " class=\"bad\""
            warn = (f'<div class="warn">이 영상은 {info.get("vcodec")} 코덱이라 '
                    f'브라우저에서 재생되지 않을 수 있습니다. '
                    f'재생이 안 되면 "앱으로 열기"를 쓰세요.</div>')
        note = f'<div class="m">{html.escape(c.note)}</div>' if c.note else ""
        items.append(
            f'<li{cls}><div class="t">{i + 1}. {title}</div>'
            f'{note}<div class="m">{meta}</div>{warn}'
            f'<div class="row">'
            f'<button onclick="play(\'/v/{i}\',{mark},\'{title}\')">'
            f'{"▶ " + str(mark) + "초부터" if mark else "▶ 처음부터"}</button>'
            f'<button onclick="play(\'/v/{i}\',0,\'{title}\')">처음부터</button>'
            f'<a class="btn" href="/v/{i}" target="_blank">앱으로 열기</a>'
            f'</div></li>')
    return PAGE.format(n=len(clips), items="".join(items)).encode("utf-8")


def make_handler(clips: list[Clip]):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "ClipServe/1.0"

        def log_message(self, fmt, *a):   # 조용히
            pass

        def _head(self, status, ctype, length, extra=None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()

        def do_GET(self):          # noqa: N802
            self._serve(body=True)

        def do_HEAD(self):         # noqa: N802
            self._serve(body=False)

        def _serve(self, body: bool):
            path = urllib.parse.urlparse(self.path).path
            if path in ("/", "/index.html"):
                page = build_page(clips)
                self._head(200, "text/html; charset=utf-8", len(page))
                if body:
                    self.wfile.write(page)
                return
            m = re.fullmatch(r"/v/(\d+)", path)
            if not m or int(m.group(1)) >= len(clips):
                self._head(404, "text/plain; charset=utf-8", 9)
                if body:
                    self.wfile.write(b"not found")
                return
            self._send_video(clips[int(m.group(1))].path, body)

        def _send_video(self, path: Path, body: bool):
            try:
                size = path.stat().st_size
            except OSError:
                self._head(404, "text/plain", 9)
                if body:
                    self.wfile.write(b"not found")
                return

            start, end, status = 0, size - 1, 200
            rng = self.headers.get("Range", "")
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
            if m:
                s, e = m.group(1), m.group(2)
                if s:
                    start = int(s)
                    if e:
                        end = min(int(e), size - 1)
                elif e:                      # bytes=-N (끝에서 N바이트)
                    start = max(0, size - int(e))
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = 206

            length = end - start + 1
            extra = {"Content-Range": f"bytes {start}-{end}/{size}"} if status == 206 else None
            self._head(status, "video/mp4", length, extra)
            if not body:
                return
            try:
                with path.open("rb") as f:
                    f.seek(start)
                    left = length
                    while left > 0:
                        buf = f.read(min(CHUNK, left))
                        if not buf:
                            break
                        self.wfile.write(buf)
                        left -= len(buf)
            except (BrokenPipeError, ConnectionResetError):
                pass               # 사용자가 탐색하면 흔히 끊긴다. 정상.
    return H


def parse_arg(token: str) -> tuple[Path, float | None]:
    """'파일.MP4@45.2' 를 (경로, 45.2) 로. @가 없으면 (경로, None)."""
    m = re.fullmatch(r"(.*?)@(\d+(?:\.\d+)?)", token)
    if m:
        return Path(m.group(1)).expanduser(), float(m.group(2))
    return Path(token).expanduser(), None


def main() -> int:
    ap = argparse.ArgumentParser(description="후보 영상 로컬 재생 서버")
    ap.add_argument("files", nargs="*", help="영상 경로. 뒤에 @초 를 붙이면 그 지점부터")
    ap.add_argument("--dir", type=Path, help="폴더에서 모아오기")
    ap.add_argument("--match", help="--dir 과 함께 쓰는 파일명 정규식")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1",
                    help="기본은 이 기기에서만 접속 가능. 같은 와이파이의 PC에서 보려면 0.0.0.0")
    args = ap.parse_args()

    clips: list[Clip] = []
    for tok in args.files:
        p, mark = parse_arg(tok)
        if p.exists():
            clips.append(Clip(p, mark))
        else:
            print(f"[건너뜀] 없는 파일: {p}", file=sys.stderr)

    if args.dir:
        rx = re.compile(args.match, re.I) if args.match else None
        for f in sorted(args.dir.rglob("*")):
            if f.is_file() and f.suffix.lower() in VIDEO_EXTS and (not rx or rx.search(f.name)):
                clips.append(Clip(f, None))

    if not clips:
        print("재생할 영상이 없습니다.", file=sys.stderr)
        return 2

    bad = [c for c in clips if c.info and not c.playable]
    print(f"영상 {len(clips)}개 준비됨")
    for i, c in enumerate(clips, 1):
        mk = f" @{c.mark}초" if c.mark else ""
        print(f"  {i}. {c.path.name}{mk}  [{c.info.get('vcodec', '?')}]")
    if bad:
        print(f"\n주의: {len(bad)}개는 브라우저가 재생 못 할 수 있는 코덱입니다 "
              f"({', '.join(sorted({c.info.get('vcodec', '?') for c in bad}))}). "
              f"그 경우 페이지의 '앱으로 열기'를 쓰세요.")

    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(clips))
    httpd.daemon_threads = True
    shown = "localhost" if args.host in ("127.0.0.1", "localhost") else args.host
    print(f"\n크롬에서 열어주세요:  http://{shown}:{args.port}")
    if args.host == "0.0.0.0":
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            print(f"같은 와이파이의 PC에서:  http://{s.getsockname()[0]}:{args.port}")
            s.close()
        except OSError:
            pass
    print("종료하려면 Ctrl+C\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n서버를 종료했습니다.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

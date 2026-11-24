#!/usr/bin/env python3
"""
태블릿(Android + Termux)에서 동영상 파일을 직접 검사하는 스크립트.

필요 패키지 (Termux):
    termux-setup-storage
    pkg install python ffmpeg

사용 예:
    python check_videos.py                         # /sdcard/DCIM 빠른 검사
    python check_videos.py /sdcard/Movies --mode full
    python check_videos.py /sdcard/DCIM -j 2 --out /sdcard/Download/report.csv

검사 모드:
    quick : ffprobe 메타데이터 + 앞/뒤 구간 일부만 디코딩 (잘림/헤더 손상 탐지, 빠름)
    full  : 전체 디코딩 (프레임 단위 손상까지 탐지, 느림)
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".3gp", ".webm", ".m4v", ".ts"}
SAMPLE_SEC = 3  # quick 모드에서 앞/뒤로 디코딩할 길이(초)
MAX_ERR_LEN = 500


@dataclass
class Result:
    path: str
    size_mb: float = 0.0
    status: str = "OK"  # OK / WARN / ERROR
    duration_sec: float | None = None
    container: str = ""
    video_codec: str = ""
    resolution: str = ""
    fps: float | None = None
    bitrate_kbps: int | None = None
    audio_codec: str = ""
    creation_time: str = ""
    elapsed_sec: float = 0.0
    issues: list[str] = field(default_factory=list)


def run(cmd: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, errors="replace",
        stdin=subprocess.DEVNULL, timeout=timeout,
    )


def parse_fps(rate: str | None) -> float | None:
    if not rate or rate in ("0/0", "0"):
        return None
    try:
        num, _, den = rate.partition("/")
        return round(float(num) / float(den or 1), 3)
    except (ValueError, ZeroDivisionError):
        return None


def to_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def probe(path: Path, r: Result) -> bool:
    """ffprobe로 메타데이터 수집. 실패 시 False."""
    p = run(["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)], timeout=60)
    if p.returncode != 0 or not p.stdout.strip():
        r.issues.append(f"ffprobe 실패: {p.stderr.strip()[:MAX_ERR_LEN] or 'no output'}")
        return False

    info = json.loads(p.stdout)
    fmt = info.get("format", {})
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)

    r.container = fmt.get("format_name", "")
    r.duration_sec = to_float(fmt.get("duration"))
    br = to_float(fmt.get("bit_rate"))
    r.bitrate_kbps = int(br / 1000) if br else None
    r.creation_time = (fmt.get("tags") or {}).get("creation_time", "")

    if v is None:
        r.issues.append("비디오 스트림 없음")
    else:
        r.video_codec = v.get("codec_name", "")
        r.resolution = f"{v.get('width', '?')}x{v.get('height', '?')}"
        r.fps = parse_fps(v.get("avg_frame_rate")) or parse_fps(v.get("r_frame_rate"))
        v_dur = to_float(v.get("duration"))
        if r.duration_sec and v_dur and abs(r.duration_sec - v_dur) > 2:
            r.issues.append(f"컨테이너/비디오 길이 불일치 ({r.duration_sec:.1f}s vs {v_dur:.1f}s)")
    if a is not None:
        r.audio_codec = a.get("codec_name", "")

    if not r.duration_sec:
        r.issues.append("재생 길이 정보 없음 (잘린 파일 가능성)")
    return True


def decode(path: Path, extra_in: list[str], extra_out: list[str], timeout: int | None) -> str:
    """ffmpeg로 디코딩하고 에러 메시지만 반환 (없으면 빈 문자열)."""
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-v", "error",
           *extra_in, "-i", str(path), *extra_out, "-f", "null", "-"]
    try:
        p = run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "디코딩 시간 초과"
    return p.stderr.strip()


def check(path: Path, mode: str, timeout: int | None) -> Result:
    t0 = time.perf_counter()
    r = Result(path=str(path))
    try:
        r.size_mb = round(path.stat().st_size / 1_048_576, 2)
        if r.size_mb == 0:
            r.issues.append("0바이트 파일")
            r.status = "ERROR"
            return r

        if not probe(path, r):
            r.status = "ERROR"
            return r

        if mode == "full":
            err = decode(path, [], [], timeout)
            if err:
                r.issues.append(f"디코딩 에러: {err[:MAX_ERR_LEN]}")
        else:
            err_head = decode(path, [], ["-t", str(SAMPLE_SEC)], timeout)
            if err_head:
                r.issues.append(f"앞부분 디코딩 에러: {err_head[:MAX_ERR_LEN]}")
            if r.duration_sec and r.duration_sec > SAMPLE_SEC * 2:
                err_tail = decode(path, ["-ss", f"{r.duration_sec - SAMPLE_SEC:.3f}"], [], timeout)
                if err_tail:
                    r.issues.append(f"뒷부분 디코딩 에러: {err_tail[:MAX_ERR_LEN]}")

        if any("디코딩" in i for i in r.issues):
            r.status = "ERROR"
        elif r.issues:
            r.status = "WARN"
    except Exception as e:  # 개별 파일 실패가 전체를 멈추지 않도록
        r.status = "ERROR"
        r.issues.append(f"예외: {type(e).__name__}: {e}")
    finally:
        r.elapsed_sec = round(time.perf_counter() - t0, 2)
    return r


def collect(paths: list[Path], exts: set[str]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files.extend(f for f in p.rglob("*")
                         if f.is_file() and f.suffix.lower() in exts
                         and not any(part.startswith(".") for part in f.relative_to(p).parts))
        else:
            print(f"[경고] 경로 없음: {p}", file=sys.stderr)
    return sorted(set(files))


def write_report(results: list[Result], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() == ".json":
        out.write_text(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2),
                       encoding="utf-8")
        return
    cols = list(asdict(results[0]).keys()) if results else list(Result.__dataclass_fields__)
    # utf-8-sig: 엑셀에서 한글 깨짐 방지
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in results:
            row = asdict(r)
            row["issues"] = " | ".join(r.issues)
            w.writerow(row)


def main() -> int:
    ap = argparse.ArgumentParser(description="태블릿 동영상 파일 검사 (ffprobe/ffmpeg)")
    ap.add_argument("paths", nargs="*", type=Path, default=[Path("/sdcard/DCIM")],
                    help="검사할 파일/폴더 (기본: /sdcard/DCIM)")
    ap.add_argument("--mode", choices=["quick", "full"], default="quick")
    ap.add_argument("-j", "--jobs", type=int, default=1,
                    help="동시 검사 파일 수 (태블릿은 1~2 권장)")
    ap.add_argument("--ext", default=",".join(sorted(DEFAULT_EXTS)),
                    help="검사할 확장자 (쉼표 구분)")
    ap.add_argument("--timeout", type=int, default=0,
                    help="파일당 디코딩 제한 시간(초), 0=무제한")
    ap.add_argument("--out", type=Path,
                    default=Path("/sdcard/Download") / f"video_check_{time.strftime('%Y%m%d_%H%M%S')}.csv",
                    help="리포트 경로 (.csv 또는 .json)")
    ap.add_argument("--only-bad", action="store_true", help="리포트에 WARN/ERROR만 기록")
    args = ap.parse_args()

    for tool in ("ffprobe", "ffmpeg"):
        if shutil.which(tool) is None:
            print(f"{tool}을 찾을 수 없습니다. Termux에서 'pkg install ffmpeg' 실행하세요.", file=sys.stderr)
            return 2

    exts = {e if e.startswith(".") else f".{e}" for e in
            (x.strip().lower() for x in args.ext.split(",")) if e}
    files = collect(args.paths, exts)
    if not files:
        print("검사할 동영상 파일이 없습니다.")
        return 0

    timeout = args.timeout or None
    print(f"{len(files)}개 파일 검사 시작 (mode={args.mode}, jobs={args.jobs})\n")

    results: list[Result] = []
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        futs = {ex.submit(check, f, args.mode, timeout): f for f in files}
        for i, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            results.append(r)
            mark = {"OK": "  OK ", "WARN": " WARN", "ERROR": "ERROR"}[r.status]
            print(f"[{i:>4}/{len(files)}] {mark} {Path(r.path).name} "
                  f"({r.size_mb}MB, {r.duration_sec or 0:.1f}s, {r.elapsed_sec}s)")
            for issue in r.issues:
                print(f"            - {issue.splitlines()[0]}")

    results.sort(key=lambda r: r.path)
    report = [r for r in results if r.status != "OK"] if args.only_bad else results
    try:
        write_report(report, args.out)
        print(f"\n리포트 저장: {args.out}")
    except OSError as e:
        print(f"\n리포트 저장 실패 ({e}). --out 경로를 확인하세요.", file=sys.stderr)

    cnt = {s: sum(r.status == s for r in results) for s in ("OK", "WARN", "ERROR")}
    print(f"요약: OK {cnt['OK']} / WARN {cnt['WARN']} / ERROR {cnt['ERROR']} (총 {len(results)})")
    return 1 if cnt["ERROR"] else 0


if __name__ == "__main__":
    sys.exit(main())

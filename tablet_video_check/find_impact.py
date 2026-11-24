#!/usr/bin/env python3
"""
블랙박스 영상에서 '충격(충돌) 가능 구간'을 선별하는 스크린닝 스크립트.
태블릿(Termux)에서 파일을 옮기지 않고 그대로 실행한다.

중요: 이 스크립트가 내는 score는 '충돌 확률'이 아니라 측정 가능한 신호
(이벤트 플래그 / 급격한 소리 / 급격한 화면 변화)를 합산한 '선별 점수'다.
실제 충돌 여부는 추출된 프레임을 눈으로 확인해야 판정할 수 있다.

필요: pkg install python ffmpeg

사용:
    python find_impact.py "/sdcard/Download/벤츠" --from 2026-11-21 --to 2026-11-22
    python find_impact.py <폴더> --from ... --to ... --stage1-only     # 오디오만 (빠름)
    python find_impact.py <폴더> --list-dates                          # 날짜 분포만 확인
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".3gp", ".webm", ".m4v", ".ts"}

# 블랙박스가 G센서 이벤트/주차 녹화를 표시하는 흔한 이름들
EVENT_WORDS = ("event", "emr", "emergency", "impact", "imp", "lock", "sos",
               "shock", "collision", "이벤트", "충격")
PARK_WORDS = ("park", "parking", "pk", "주차")

# 파일명에서 촬영 시각 추출 (구체적인 패턴 먼저)
DT_PATTERNS = [
    # 2026_1121_143022 (Thinkware 등)
    (re.compile(r"(?<!\d)(20\d{2})[_-]?(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})(?!\d)"),
     ("Y", "m", "d", "H", "M", "S")),
    # 20261121_143022 / 20261121-143022 / 20261121143022 (BlackVue 등)
    (re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})[_\-]?(\d{2})(\d{2})(\d{2})(?!\d)"),
     ("Y", "m", "d", "H", "M", "S")),
    # 2026-11-21 14.30.22 / 2026-11-21 14_30_22
    (re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})[ T_](\d{2})[._:-](\d{2})[._:-](\d{2})(?!\d)"),
     ("Y", "m", "d", "H", "M", "S")),
    # 261121-143022 / 261121_143022 (2자리 연도)
    (re.compile(r"(?<!\d)(\d{2})(\d{2})(\d{2})[_\-](\d{2})(\d{2})(\d{2})(?!\d)"),
     ("y", "m", "d", "H", "M", "S")),
    # 20261121 (시각 없음)
    (re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)"), ("Y", "m", "d")),
]


@dataclass
class Clip:
    path: str
    name: str = ""
    size_mb: float = 0.0
    dt: str = ""              # 추정 촬영 시각
    dt_source: str = ""       # filename / metadata / mtime  (신뢰도 판단용)
    duration_sec: float | None = None
    resolution: str = ""
    channel: str = ""         # front / rear / unknown (파일명 추정)
    event_flag: str = ""      # event / parking
    has_audio: bool = False
    audio_peak_db: float | None = None
    audio_base_db: float | None = None
    audio_jump_db: float | None = None
    audio_hits: list[float] = field(default_factory=list)   # 충격음 추정 시각(초)
    scene_max: float | None = None
    scene_hits: list[float] = field(default_factory=list)   # 급변 시각(초)
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    frames: list[str] = field(default_factory=list)
    note: str = ""


def run(cmd: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                          stdin=subprocess.DEVNULL, timeout=timeout)


def parse_name_dt(name: str) -> datetime | None:
    for rx, keys in DT_PATTERNS:
        m = rx.search(name)
        if not m:
            continue
        v = dict(zip(keys, m.groups()))
        try:
            year = int(v["Y"]) if "Y" in v else 2000 + int(v["y"])
            dt = datetime(year, int(v["m"]), int(v["d"]),
                          int(v.get("H", 0)), int(v.get("M", 0)), int(v.get("S", 0)))
        except ValueError:
            continue
        if 2015 <= dt.year <= 2100:
            return dt
    return None


def classify(path: Path) -> tuple[str, str]:
    """(channel, event_flag) 추정. 파일명/상위 폴더명 기준."""
    low = (path.name + " " + " ".join(p.name for p in path.parents[:3])).lower()
    channel, flag = "unknown", ""

    # BlackVue 계열 접미사: _<N|E|P|M><F|R|I>  (일반/이벤트/주차/수동 + 전방/후방/실내)
    m = re.search(r"_([nepm])([fri])(?=[._\W]|$)", low)
    if m:
        channel = {"f": "front", "r": "rear", "i": "inside"}[m.group(2)]
        flag = {"e": "event", "p": "parking", "m": "manual", "n": ""}[m.group(1)]

    if channel == "unknown":
        if re.search(r"(front|_fr\b|전방|ch1|cam1)", low):
            channel = "front"
        elif re.search(r"(rear|_rr\b|후방|back|ch2|cam2)", low):
            channel = "rear"
        elif re.search(r"(inside|cabin|실내)", low):
            channel = "inside"

    if not flag:
        if any(w in low for w in EVENT_WORDS):
            flag = "event"
        elif any(w in low for w in PARK_WORDS):
            flag = "parking"
    return channel, flag


def probe(path: Path, c: Clip) -> bool:
    p = run(["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)], timeout=60)
    if p.returncode != 0 or not p.stdout.strip():
        c.note = f"ffprobe 실패: {p.stderr.strip()[:200]}"
        return False
    info = json.loads(p.stdout)
    fmt, streams = info.get("format", {}), info.get("streams", [])
    try:
        c.duration_sec = round(float(fmt.get("duration")), 2)
    except (TypeError, ValueError):
        c.duration_sec = None
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if v:
        c.resolution = f"{v.get('width', '?')}x{v.get('height', '?')}"
    c.has_audio = any(s.get("codec_type") == "audio" for s in streams)

    # 메타데이터 촬영 시각 (파일명보다 신뢰도 낮지만 다운로드 mtime보다는 높음)
    tags = {k.lower(): val for k, val in (fmt.get("tags") or {}).items()}
    raw = tags.get("creation_time") or tags.get("date")
    if raw:
        for f in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z",
                  "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
            try:
                c.note = c.note or ""
                dt = datetime.strptime(raw, f).replace(tzinfo=None)
                if 2015 <= dt.year <= 2100:
                    c._meta_dt = dt  # type: ignore[attr-defined]
                break
            except ValueError:
                continue
    return True


def audio_windows(path: Path, timeout: int | None) -> list[tuple[float, float, float]]:
    """0.1초 창마다 (시각, peak_dB, rms_dB). 오디오만 디코딩하므로 빠르다."""
    af = ("aresample=16000,aformat=channel_layouts=mono,asetnsamples=n=1600,"
          "astats=metadata=1:reset=1:measure_perchannel=none:"
          "measure_overall=Peak_level+RMS_level,ametadata=print:file=-")
    try:
        p = run(["ffmpeg", "-nostdin", "-hide_banner", "-v", "error",
                 "-i", str(path), "-vn", "-af", af, "-f", "null", "-"], timeout=timeout)
    except subprocess.TimeoutExpired:
        return []
    out: list[tuple[float, float, float]] = []
    t = peak = rms = None
    for line in p.stdout.splitlines():
        line = line.strip()
        if line.startswith("frame:"):
            m = re.search(r"pts_time:([\d.]+)", line)
            t = float(m.group(1)) if m else None
            peak = rms = None
        elif "Peak_level=" in line:
            peak = line.split("=", 1)[1]
        elif "RMS_level=" in line:
            rms = line.split("=", 1)[1]
            if t is not None and peak is not None:
                try:
                    out.append((t, float(peak), float(rms)))
                except ValueError:
                    pass  # -inf (완전 무음) 등
    return out


def scene_changes(path: Path, thr: float, timeout: int | None) -> tuple[float, list[float]]:
    """다운스케일 저프레임으로 급격한 화면 변화 탐지. (최대 점수, 시각 목록)"""
    vf = (f"scale=160:-2,fps=8,select='gt(scene,{thr})',"
          "metadata=print:file=-")
    try:
        p = run(["ffmpeg", "-nostdin", "-hide_banner", "-v", "error",
                 "-i", str(path), "-an", "-vf", vf, "-f", "null", "-"], timeout=timeout)
    except subprocess.TimeoutExpired:
        return 0.0, []
    hits: list[tuple[float, float]] = []
    t = None
    for line in p.stdout.splitlines():
        line = line.strip()
        if line.startswith("frame:"):
            m = re.search(r"pts_time:([\d.]+)", line)
            t = float(m.group(1)) if m else None
        elif "scene_score=" in line and t is not None:
            try:
                hits.append((t, float(line.split("=", 1)[1])))
            except ValueError:
                pass
    if not hits:
        return 0.0, []
    best = max(s for _, s in hits)
    return round(best, 3), [round(t, 2) for t, _ in sorted(hits, key=lambda x: -x[1])[:6]]


def cluster(times: list[float], gap: float = 1.0) -> list[float]:
    """근접한 시각들을 하나로 묶는다."""
    if not times:
        return []
    out = [times[0]]
    for t in sorted(times)[1:]:
        if t - out[-1] > gap:
            out.append(t)
    return out


def analyze_audio(c: Clip, wins: list[tuple[float, float, float]],
                  jump_db: float, floor_db: float) -> None:
    peaks = [p for _, p, _ in wins]
    if len(peaks) < 5:
        return
    base = statistics.median(peaks)
    c.audio_base_db = round(base, 1)
    c.audio_peak_db = round(max(peaks), 1)
    c.audio_jump_db = round(max(peaks) - base, 1)
    hits = [t for t, p, _ in wins if (p - base) >= jump_db and p >= floor_db]
    c.audio_hits = cluster(hits)[:6]


def score_clip(c: Clip, jump_db: float) -> None:
    s, why = 0, []
    if c.event_flag == "event":
        s += 45
        why.append("블랙박스 이벤트(G센서) 파일로 표시됨")
    elif c.event_flag == "parking":
        s += 10
        why.append("주차 녹화 파일")
    if c.audio_jump_db is not None and c.audio_hits:
        over = c.audio_jump_db - jump_db
        add = min(35, 18 + int(over * 1.2))
        s += add
        why.append(f"배경보다 {c.audio_jump_db}dB 큰 소리 {len(c.audio_hits)}곳 "
                   f"({', '.join(f'{t:.1f}s' for t in c.audio_hits)})")
    if c.scene_max:
        add = min(25, int(c.scene_max * 50))
        s += add
        if add:
            why.append(f"화면 급변 최대 {c.scene_max} "
                       f"({', '.join(f'{t:.1f}s' for t in c.scene_hits[:3])})")
    if not c.has_audio:
        why.append("오디오 없음 → 충격음 판단 불가(점수 과소평가 가능)")
    c.score = min(100, s)
    c.reasons = why


def extract_frames(path: Path, times: list[float], outdir: Path, width: int,
                   span: float = 0.6, n: int = 3) -> list[str]:
    """후보 시각 주변 프레임을 JPEG로 추출."""
    outdir.mkdir(parents=True, exist_ok=True)
    made: list[str] = []
    stem = re.sub(r"[^\w.-]", "_", path.stem)[:40]
    for t in times:
        for k in range(n):
            ts = max(0.0, t - span + k * span)
            out = outdir / f"{stem}__t{t:06.2f}_{k}.jpg"
            p = run(["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-y",
                     "-ss", f"{ts:.3f}", "-i", str(path), "-frames:v", "1",
                     "-vf", f"scale={width}:-2", "-q:v", "3", str(out)], timeout=90)
            if p.returncode == 0 and out.exists() and out.stat().st_size > 0:
                made.append(str(out))
    return made


def collect(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return sorted(f for f in root.rglob("*")
                  if f.is_file() and f.suffix.lower() in VIDEO_EXTS)


def resolve_dt(c: Clip, path: Path) -> None:
    dt = parse_name_dt(path.name)
    if dt:
        c.dt, c.dt_source = dt.isoformat(sep=" "), "filename"
        return
    meta = getattr(c, "_meta_dt", None)
    if meta:
        c.dt, c.dt_source = meta.isoformat(sep=" "), "metadata"
        return
    c.dt = datetime.fromtimestamp(path.stat().st_mtime).isoformat(sep=" ")
    c.dt_source = "mtime"


def main() -> int:
    ap = argparse.ArgumentParser(description="블랙박스 충격 구간 선별")
    ap.add_argument("root", type=Path, help="영상 폴더 (예: /sdcard/Download/벤츠)")
    ap.add_argument("--from", dest="dfrom", help="시작 날짜 YYYY-MM-DD")
    ap.add_argument("--to", dest="dto", help="종료 날짜 YYYY-MM-DD (해당일 포함)")
    ap.add_argument("--list-dates", action="store_true", help="날짜 분포만 출력하고 종료")
    ap.add_argument("--stage1-only", action="store_true", help="오디오 분석만 (빠름)")
    ap.add_argument("--stage2-top", type=int, default=20,
                    help="영상 분석할 상위 후보 수 (기본 20)")
    ap.add_argument("--jump-db", type=float, default=12.0, help="충격음 판정 dB 상승폭")
    ap.add_argument("--floor-db", type=float, default=-32.0, help="충격음 절대 최소 dBFS")
    ap.add_argument("--scene-thr", type=float, default=0.18, help="화면 급변 임계값")
    ap.add_argument("--sensitive", action="store_true",
                    help="감도 상향 (약한 긁힘용). 오탐이 늘지만 놓칠 확률이 줄어든다")
    ap.add_argument("--min-score", type=int, default=0, help="리포트 최소 점수")
    ap.add_argument("--frames", type=int, default=10, help="프레임 추출할 상위 후보 수")
    ap.add_argument("--frame-width", type=int, default=960)
    ap.add_argument("--timeout", type=int, default=0, help="파일당 제한 시간(초), 0=무제한")
    ap.add_argument("--out", type=Path, default=None, help="리포트 경로(.json/.csv)")
    args = ap.parse_args()
    if args.sensitive:
        args.jump_db, args.floor_db, args.scene_thr = 8.0, -42.0, 0.12
        args.stage2_top, args.frames = max(args.stage2_top, 30), max(args.frames, 15)
        print("감도 상향 모드: jump>=8dB, floor>=-42dBFS, scene>=0.12\n")

    for tool in ("ffprobe", "ffmpeg"):
        if shutil.which(tool) is None:
            print(f"{tool} 없음. Termux에서 'pkg install ffmpeg' 실행.", file=sys.stderr)
            return 2
    if not args.root.exists():
        print(f"경로 없음: {args.root}", file=sys.stderr)
        return 2

    files = collect(args.root)
    if not files:
        print("동영상 파일이 없습니다.")
        return 0
    print(f"총 {len(files)}개 동영상 발견\n")

    # ---- 날짜 판정 (ffprobe는 날짜 확정에 필요하므로 전체 1회 실행: 파일당 수십 ms)
    clips: list[Clip] = []
    for f in files:
        c = Clip(path=str(f), name=f.name)
        c.size_mb = round(f.stat().st_size / 1_048_576, 2)
        c.channel, c.event_flag = classify(f)
        probe(f, c)
        resolve_dt(c, f)
        clips.append(c)

    if args.list_dates:
        from collections import Counter
        print("날짜별 파일 수 (판정 근거별):")
        for src in ("filename", "metadata", "mtime"):
            cnt = Counter(c.dt[:10] for c in clips if c.dt_source == src)
            if cnt:
                print(f"  [{src}]")
                for d, n in sorted(cnt.items()):
                    print(f"    {d}  {n}개")
        return 0

    sel = clips
    if args.dfrom or args.dto:
        lo = datetime.fromisoformat(args.dfrom) if args.dfrom else datetime.min
        hi = (datetime.fromisoformat(args.dto) + timedelta(days=1)) if args.dto else datetime.max
        sel = [c for c in clips if lo <= datetime.fromisoformat(c.dt) < hi]
        print(f"날짜 필터 {args.dfrom or '~'} ~ {args.dto or '~'} → {len(sel)}개")
        srcs = {c.dt_source for c in sel}
        if "mtime" in srcs:
            n = sum(c.dt_source == "mtime" for c in sel)
            print(f"  주의: {n}개는 파일 수정시각(다운로드 시각)으로 판정했습니다. "
                  f"실제 촬영일과 다를 수 있습니다.")
    if not sel:
        print("해당 기간 영상이 없습니다. --list-dates 로 날짜 분포를 확인하세요.")
        return 0

    timeout = args.timeout or None

    # ---- 1단계: 오디오 (전체, 빠름)
    print(f"\n[1단계] 오디오 충격음 분석 {len(sel)}개")
    t0 = time.perf_counter()
    for i, c in enumerate(sel, 1):
        if c.has_audio:
            analyze_audio(c, audio_windows(Path(c.path), timeout), args.jump_db, args.floor_db)
        score_clip(c, args.jump_db)
        hit = f"충격음 {len(c.audio_hits)}곳" if c.audio_hits else "-"
        print(f"  [{i:>3}/{len(sel)}] {c.name[:44]:<44} jump={c.audio_jump_db or 0:>6}dB  {hit}")
    print(f"  소요 {time.perf_counter() - t0:.1f}s")

    # ---- 2단계: 영상 (상위 후보만, 느림)
    if not args.stage1_only:
        cand = sorted(sel, key=lambda c: -c.score)
        cand = [c for c in cand if c.score > 0][:args.stage2_top] or cand[:args.stage2_top]
        print(f"\n[2단계] 화면 급변 분석 {len(cand)}개")
        t0 = time.perf_counter()
        for i, c in enumerate(cand, 1):
            c.scene_max, c.scene_hits = scene_changes(Path(c.path), args.scene_thr, timeout)
            score_clip(c, args.jump_db)
            print(f"  [{i:>3}/{len(cand)}] {c.name[:44]:<44} scene={c.scene_max}  score={c.score}")
        print(f"  소요 {time.perf_counter() - t0:.1f}s")

    ranked = sorted(sel, key=lambda c: (-c.score, c.dt))
    ranked = [c for c in ranked if c.score >= args.min_score]

    # ---- 프레임 추출
    outdir = args.root.parent / "impact_frames" if args.root.is_dir() else args.root.parent
    try:
        for c in ranked[:args.frames]:
            times = cluster(c.audio_hits + c.scene_hits[:3])[:4]
            if times:
                c.frames = extract_frames(Path(c.path), times, outdir,
                                          args.frame_width)
        if any(c.frames for c in ranked):
            print(f"\n프레임 저장: {outdir}")
    except OSError as e:
        print(f"\n프레임 추출 실패: {e}", file=sys.stderr)

    # ---- 리포트
    print("\n" + "=" * 70)
    print("선별 점수 순위 (※ 충돌 확률이 아니라 '확인해야 할 순서'입니다)")
    print("=" * 70)
    for i, c in enumerate(ranked[:20], 1):
        print(f"\n{i}. [{c.score:>3}점] {c.name}")
        print(f"   {c.dt} ({c.dt_source}) | {c.duration_sec}s | {c.resolution} | "
              f"{c.channel}{' | ' + c.event_flag if c.event_flag else ''}")
        for r in c.reasons:
            print(f"   - {r}")
        if c.frames:
            print(f"   프레임 {len(c.frames)}장")

    out = args.out or (args.root.parent /
                       f"impact_report_{time.strftime('%Y%m%d_%H%M%S')}.json")
    data = []
    for c in ranked:
        d = asdict(c)
        d.pop("_meta_dt", None)
        data.append(d)
    try:
        if out.suffix.lower() == ".csv":
            with out.open("w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=list(data[0].keys()))
                w.writeheader()
                for d in data:
                    w.writerow({k: (" | ".join(map(str, v)) if isinstance(v, list) else v)
                                for k, v in d.items()})
        else:
            out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n리포트: {out}")
    except OSError as e:
        print(f"\n리포트 저장 실패: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

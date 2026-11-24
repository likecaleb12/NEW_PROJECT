# tablet_video_check

Android 태블릿에서 **Termux**로 동영상 파일을 PC로 옮기지 않고 직접 검사합니다.

## 설치 (Termux, 최초 1회)

```bash
# Termux는 F-Droid 또는 GitHub 릴리스 버전 사용 (Play 스토어 버전은 업데이트 중단)
termux-setup-storage          # 저장소 권한 허용 팝업 → 허용
pkg update && pkg upgrade -y
pkg install -y python ffmpeg
```

스크립트를 태블릿으로 가져오기 (택1):

```bash
# 1) git
pkg install -y git
git clone -b ccr-23671aa4-tumdht https://github.com/likecaleb12/NEW_PROJECT.git
cd NEW_PROJECT/tablet_video_check

# 2) 직접 붙여넣기
nano check_videos.py          # 내용 붙여넣고 Ctrl+O, Enter, Ctrl+X
```

## 사용법

```bash
python check_videos.py                                   # /sdcard/DCIM 빠른 검사
python check_videos.py /sdcard/DCIM /sdcard/Movies       # 여러 폴더
python check_videos.py /sdcard/Movies --mode full        # 전체 디코딩 검사
python check_videos.py --only-bad --out /sdcard/Download/bad.json
```

| 옵션 | 설명 |
|---|---|
| `--mode quick` (기본) | ffprobe + 앞/뒤 3초 디코딩. 잘린 파일·헤더 손상 탐지, 빠름 |
| `--mode full` | 전체 디코딩. 중간 프레임 손상까지 탐지, 영상 길이만큼 시간 소요 |
| `-j N` | 동시 검사 파일 수 (ffmpeg 자체가 멀티스레드라 1~2 권장) |
| `--ext` | 확장자 목록 (기본 `.mp4,.mov,.mkv,.avi,.3gp,.webm,.m4v,.ts`) |
| `--timeout` | 파일당 디코딩 제한 시간(초) |
| `--out` | 리포트 경로 `.csv`(엑셀 호환) / `.json`. 기본 `/sdcard/Download/video_check_<시각>.csv` |
| `--only-bad` | 리포트에 WARN/ERROR만 기록 |

종료 코드: ERROR가 하나라도 있으면 `1`, 아니면 `0`.

## 판정 기준

- **ERROR**: 0바이트, ffprobe 실패(예: `moov atom not found` → 녹화 중단으로 잘린 파일), 디코딩 에러
- **WARN**: 비디오 스트림 없음, 재생 길이 정보 없음, 컨테이너/비디오 길이 2초 이상 불일치
- **OK**: 위 항목 없음

> quick 모드는 중간 부분 손상을 놓칠 수 있습니다. 중요한 파일은 `--mode full`로 확인하세요.

## 팁

- 장시간 검사 시 화면이 꺼져도 멈추지 않게: `termux-wake-lock` (끝나면 `termux-wake-unlock`)
- 외장 SD카드 경로는 `/storage/XXXX-XXXX/...` (`ls /storage`로 확인)

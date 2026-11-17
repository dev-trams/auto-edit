# auto-edit (프로토타입)

촬영본을 분석해서 **편집 지시서(JSON)** 를 만들고, 그 지시서대로 FFmpeg가 영상을 자르고 자막을 입히는 프로그램입니다.
PC 안에서만 도는 웹 화면으로 조작합니다. 영상은 어디에도 올라가지 않습니다.

```
촬영본 ──분석──▶ 무음·음량·장면·받아쓰기 ──계획──▶ 편집 지시서 ──렌더──▶ 완성본
                                         (규칙 기반 또는 Claude API)      (FFmpeg)
```

## 설치 파일로 쓰기

GitHub 릴리스에서 내려받습니다. FFmpeg가 함께 들어 있습니다.

- Windows: `auto-edit-버전-windows-setup.exe`
- macOS (Apple Silicon): `auto-edit-버전-macos-arm64.dmg`

서명하지 않은 프로그램이라 처음 실행할 때 경고가 뜹니다 (Windows: 추가 정보 → 실행, macOS: 시스템 설정 → 개인정보 보호 및 보안 → 그래도 열기).

새 설치 파일 만들기: `git tag v0.1.1 && git push origin v0.1.1` 또는 GitHub의 Actions → release → Run workflow.
설치 확인만 하려면 `python run.py --selftest`.

## 소스로 실행하기 — 준비물


| 항목 | Windows | macOS |
|---|---|---|
| Python 3.10 이상 | python.org 설치 파일 | `brew install python` |
| FFmpeg | `winget install Gyan.FFmpeg` | `brew install ffmpeg` |

FFmpeg를 설치한 뒤에는 터미널을 새로 열어야 인식됩니다.

## 설치와 실행

```
cd auto-edit
python -m venv .venv
.venv\Scripts\activate          (macOS: source .venv/bin/activate)
pip install -r requirements.txt
python run.py
```

브라우저에 `http://127.0.0.1:8765` 가 열립니다. 끝낼 때는 터미널에서 Ctrl+C.

## 처음 할 일

1. 오른쪽 위 **설정**에서 작업 폴더를 고릅니다. `원본 영상 / 레퍼런스 / 완성본` 폴더가 들어 있는 곳입니다 (없으면 만들어집니다).
2. Claude API 키가 있으면 설정에 넣습니다. 없어도 규칙 기반으로 동작합니다.
3. 촬영본을 `원본 영상` 폴더에 넣거나 화면의 **파일 추가**로 올립니다.

## 쓰는 순서

1. **촬영본**: 쓸 파일을 고르고 "분석". 받아쓰기 모델(약 610MB)은 첫 분석 때 한 번 내려받습니다.
2. **편집 계획**: 영상 종류를 고르고 "편집 지시서 만들기".
   - 말하는 영상: 무음을 자르고 받아쓰기로 자막을 답니다.
   - 자막 직접 입력: 클립 순서대로 입력한 문구를 붙입니다. `*`로 시작하는 줄은 강조색.
3. **결과**: 미리보기(절반 크기, 빠름)를 보고, 구간·자막·스타일을 표에서 고치거나 말로 수정합니다.
   고칠 때마다 `v2`, `v3`… 새 버전으로 저장되고 이전 버전은 남습니다.
4. **최종 내보내기**: `완성본/편집본_vN.mp4` 와 `.srt`, `.edl.json` 이 생깁니다.

## 규칙

- 원본 폴더는 읽기만 합니다. 올린 파일 이름이 겹치면 `_1`을 붙여 저장합니다.
- 완성본은 덮어쓰지 않습니다. 같은 버전을 두 번 내보낼 수 없습니다.
- 배경음은 경로를 직접 넣은 파일만 씁니다.
- 작업 파일(분석 결과, 미리보기, 모델)은 `~/.autoedit` 에 둡니다.

## 키 없이 되는 말 수정

자막 크게/작게, 위로/아래로, A·B·C형, 배경음 크게/작게, 소리 크게/작게.
그 밖의 요청("3분 10초 부분 살려 줘" 등)은 Claude API 키가 필요합니다.

## 구조

| 파일 | 역할 |
|---|---|
| `autoedit/analyze.py` | 기본 정보, 무음 구간, 음량, 대표 장면 |
| `autoedit/transcribe.py` | 말소리 구간 검출 + Whisper 받아쓰기 (sherpa-onnx, CPU) |
| `autoedit/planner.py` | 규칙 기반 계획, Claude API 계획·수정 |
| `autoedit/edl.py` | 편집 지시서 구조, 검증, 버전 파일 |
| `autoedit/style.py` | 자막 A/B/C형, ASS·SRT 생성 |
| `autoedit/render.py` | FFmpeg 렌더 (HDR 변환, 음량, 배경음, 자막) |
| `autoedit/server.py` | 로컬 웹 서버 |
| `autoedit/web/index.html` | 화면 |
| `autoedit/selftest.py` | 설치 확인 (`--selftest`) |
| `packaging/`, `.github/workflows/release.yml` | 설치 파일 빌드 (PyInstaller, Inno Setup, GitHub Actions) |
| `examples/편집본_v1.edl.json` | 2026-11-16 수작업 편집본과 같은 지시서 |

## 확인된 것과 안 된 것 (2026-11-17)

확인됨 (Linux에서 실행):
- 예시 지시서로 `편집본_v1`과 같은 길이·구성의 영상 렌더
- 규칙 기반 계획(말하는 영상 / 자막 직접 입력), 규칙 기반 말 수정
- 받아쓰기: 모델 내려받기, 말소리 구간 검출, 한국어 받아쓰기
- 웹 화면: 목록, 분석, 계획, 미리보기, 표 편집, 설정

확인 안 됨:
- **Claude API 호출** (계획·수정): 키가 없어 실제 호출을 해 보지 못했습니다.
- **Windows와 macOS에서의 실행**: 글꼴 이름, 경로 처리, FFmpeg 배포판 차이.
- 배경음 섞기: 코드는 있으나 실제 음악 파일로 시험하지 못했습니다.

알려진 한계:
- 받아쓰기는 단어별 시간이 없어, 긴 문장의 자막 시간은 글자 수 비율로 나눕니다. 1초 안팎 어긋날 수 있습니다.
- small 모델은 고유명사·전문 용어를 자주 틀립니다. 설정에서 medium으로 올릴 수 있습니다 (더 느림).
- 전환 효과와 모션 그래픽은 없습니다.

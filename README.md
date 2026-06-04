# DCWatch

DCWatch는 DCInside `특이점이 온다` 마이너 갤러리(`thesingularity`)에 새 글이 올라왔을 때, 이미지 테러 의심 글을 관리자가 갑자기 직접 보지 않도록 사전에 경고하는 Windows용 로컬 감시 도구입니다.

이 도구는 자동 삭제, 자동 차단, 자동 신고를 하지 않습니다. 최종 확인과 삭제/차단은 사용자가 직접 합니다.

## 안전 원칙

- 런타임에서 LLM을 사용하지 않습니다.
- OpenAI API나 기타 LLM API를 호출하지 않습니다.
- Telegram, Discord, 이메일, 웹훅, 외부 푸시 알림을 사용하지 않습니다.
- Windows toast notification을 사용하지 않습니다.
- 경고음, 알람음, 비프음, winsound를 사용하지 않습니다.
- 경보 방식은 항상 위에 뜨는 로컬 팝업창만 사용합니다.
- 팝업에는 이미지, 썸네일, 이미지 URL을 표시하지 않습니다.
- 팝업에는 글번호, 제목, 작성자, 위험도, 위험 사유, 게시글 URL만 표시합니다.

## 감지 방식

DCWatch는 이미지를 화면에 표시하지 않고 바이트로만 다운로드한 뒤 다음 방식으로 검사합니다.

- SHA-256 완전 일치: 확정된 테러 이미지 해시와 같으면 `high`
- pHash 유사도: 확정된 테러 이미지 pHash와 Hamming distance가 설정값 이하이면 `high`
- 타일 pHash: 이미지를 3x3 같은 격자로 나눈 뒤 조각별 pHash도 비교합니다. 기존 이미지의 일부만 크롭해 재업로드한 경우를 더 잘 잡기 위한 보조 탐지입니다.
- crop-resistant hash: imagehash의 segment hash를 저장해 부분 crop 재업로드를 추가로 비교합니다.
- ORB feature matching: OpenCV가 포함된 빌드에서는 회전/크롭에 강한 특징점 descriptor도 비교합니다. OpenCV가 없으면 이 기능만 자동으로 건너뜁니다.
- NudeNet: 노출/나체 계열 라벨 점수가 설정값 이상이면 `high`
- 분석 실패: `alert_on_analysis_failure = true`일 때만 `warning`

완전히 새로운 배설물/혐오 이미지는 해시 DB만으로는 놓칠 수 있습니다. 이 도구는 새 혐오 이미지를 LLM 없이 안정적으로 판정한다고 과장하지 않습니다. 사용자가 확인한 뒤 “확정 테러 해시 DB에 등록”하면 이후 동일/유사 재업로드 탐지에 도움이 됩니다.

감시를 시작하면 현재 목록에 이미 보이는 이미지글만 기준선으로 저장하고, 그 이후 새로 올라온 이미지글만 검사합니다. 목록에서 초록색 이미지 아이콘(`icon_pic`)이 있는 글만 큐에 넣고 본문에 들어가 이미지를 검사하며, 공지/설문/이슈/텍스트글은 DB에 저장하지 않고 무시합니다.

## Windows EXE 사용

1. Release ZIP을 내려받습니다.
2. 원하는 폴더에 압축을 풉니다.
3. `DCWatch.exe`를 실행합니다.
4. 설정을 확인한 뒤 `감시 시작`을 누릅니다.

처음 실행하면 사용자 데이터가 압축을 푼 `DCWatch` 폴더 안에 저장됩니다. 기본 배포판은 portable/sandbox 방식입니다.

- 설정 파일: `DCWatch\config.toml`
- DB 파일: `DCWatch\dc_watch.sqlite3`
- 로그 폴더: `DCWatch\logs\`

EXE가 서명되지 않은 경우 Windows SmartScreen 경고가 뜰 수 있습니다.

## 개발 환경 실행

Python 3.11 이상이 필요합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .[dev]
```

NudeNet까지 사용하려면 다음처럼 설치합니다.

```powershell
python -m pip install -e .[dev,nude]
```

GUI 실행:

```powershell
python -m dc_watch gui
```

CLI 감시:

```powershell
python -m dc_watch watch
```

한 번만 검사:

```powershell
python -m dc_watch once
```

테스트 팝업:

```powershell
python -m dc_watch test-popup
```

통계:

```powershell
python -m dc_watch stats
```

## 설정

`config.example.toml`을 참고하세요. EXE 배포판은 `DCWatch.exe`가 있는 폴더의 `config.toml`을 기본으로 씁니다. 개발 중에는 현재 작업 디렉터리의 `config.toml`을 먼저 읽습니다.

주요 설정:

```toml
gallery_id = "thesingularity"
gallery_type = "minor"
poll_seconds = 45
jitter_seconds = 10
pages_to_scan = 1
scan_existing_on_first_run = true
max_image_bytes = 26214400
phash_threshold = 7
enable_tile_phash = true
tile_phash_grid_size = 3
tile_phash_threshold = 6
tile_phash_min_matches = 1
enable_crop_resistant_hash = true
crop_hash_region_cutoff = 1
crop_hash_hamming_cutoff = 16
enable_orb_matching = true
orb_max_features = 500
orb_distance_threshold = 64
orb_min_matches = 65
enable_nudenet = false
nude_score_threshold = 0.45
alert_on_analysis_failure = false
high_popup_enabled = true
warning_popup_enabled = true
topmost_popup = true
suppress_image_urls_in_logs = true
database_path = "dc_watch.sqlite3"
use_appdata_dir = false
```

`poll_seconds`는 갤러리 목록을 새로 확인하는 간격입니다. 기본값 45초면 45초마다 새 이미지글이 있는지 확인합니다. `jitter_seconds`는 큐에 쌓인 이미지글을 하나씩 검사하는 간격입니다. 기본값 10초면 새 이미지글이 여러 개 쌓였을 때 10초마다 한 글씩 본문 이미지를 검사합니다. 값을 너무 짧게 하지 마세요.

## 오탐/미탐 조정

오탐이 많을 때:

- `nude_score_threshold`를 올립니다. 예: `0.45` → `0.65`
- `phash_threshold`를 낮춥니다. 예: `7` → `5`

재업로드를 자주 놓칠 때:

- `phash_threshold`를 올립니다. 예: `7` → `9`
- `tile_phash_threshold`를 올립니다. 예: `6` → `8`
- `tile_phash_min_matches`를 낮춥니다. 기본값은 `1`입니다.
- `crop_hash_hamming_cutoff`를 올립니다. 기본값은 `16`입니다.
- `orb_min_matches`를 낮춥니다. 기본값은 `65`입니다.

값을 올릴수록 유사 이미지 탐지는 넓어지지만 오탐 가능성도 커집니다.

## 확정 테러 이미지 해시 등록

자동 등록은 하지 않습니다. 사용자가 직접 게시글을 확인한 뒤 등록해야 합니다.

이미 스캔된 글의 이미지 해시 등록:

```powershell
python -m dc_watch remember-post 1234567 known_attack_001
```

로컬 이미지 파일 등록:

```powershell
python -m dc_watch remember-file .\bad.jpg known_attack_002
```

`remember-file`, `remember-post`, GUI 확정 등록은 원본뿐 아니라 좌우반전, 회전, 회전+반전 변형의 SHA-256/pHash/tile pHash/crop-resistant hash/ORB descriptor를 등록합니다. 원본 이미지 파일이나 변형 이미지는 DB에 저장하지 않습니다.

프로젝트에는 관리자가 확정한 기본 bad hash seed가 `dc_watch/bundled_bad_hashes.json`으로 포함됩니다. 이 seed에는 원본 이미지가 들어 있지 않고 SHA-256, pHash, crop-resistant hash, tile pHash, ORB descriptor 메타데이터만 들어 있습니다. 새 DB를 만들거나 기존 DB를 migration할 때 자동으로 import되며, 같은 항목은 중복 등록되지 않습니다.

GUI에서는 최근 경보 목록 또는 팝업의 `이 글을 확정 테러 해시 DB에 등록` 버튼을 사용할 수 있습니다. 로컬 파일을 직접 등록하려면 메인 창의 `로컬 이미지 해시 DB 등록` 버튼을 누른 뒤 파일과 라벨을 선택합니다.

잘못 등록한 항목은 GUI 메인 창의 `bad hash DB 관리` 버튼에서 선택한 뒤 `선택 삭제`로 제거할 수 있습니다. 이 목록에는 이미지, 썸네일, 이미지 URL을 표시하지 않습니다.

## Windows 시작 시 자동 실행

현재 사용자 로그온 시 실행되도록 `schtasks`를 사용합니다. 관리자 권한 없이 가능한 방식입니다.

등록:

```powershell
python -m dc_watch install-autostart
```

해제:

```powershell
python -m dc_watch uninstall-autostart
```

GUI에서도 같은 기능을 제공하도록 설계되어 있습니다.

## 빌드

기본 배포 방식은 one-folder ZIP입니다. NudeNet, Pillow, imagehash, 모델 파일, SQLite, 로그 파일 때문에 디버깅과 업데이트가 쉽습니다.

```powershell
python -m pip install -e .[dev]
.\scripts\build_windows.ps1
```

결과:

```text
release\DCWatch-0.1.0-win64.zip
```

단일 EXE가 필요할 때만 one-file 빌드를 사용하세요.

```powershell
.\scripts\build_windows_onefile.ps1
```

## 설치 프로그램

Inno Setup 스크립트는 `installer\inno\dc-watch.iss`입니다. `dist\DCWatch\` 전체를 설치하고 시작 메뉴 바로가기를 만듭니다. 바탕화면 바로가기는 선택 옵션입니다.

기본 ZIP 배포판은 portable/sandbox 방식입니다. 설치 프로그램을 쓸 때도 설정, DB, 로그는 설치된 앱 폴더 안에 생성됩니다.

## 업데이트와 제거

업데이트:

1. 감시를 중지합니다.
2. 새 ZIP을 풉니다.
3. 기존 실행 폴더 안의 `config.toml`, `dc_watch.sqlite3`, `logs\`는 보존합니다.
4. 새 배포 파일의 프로그램 파일만 교체합니다.

제거:

1. GUI에서 감시를 중지합니다.
2. 자동 실행을 등록했다면 해제합니다.
3. 프로그램 폴더를 삭제합니다.
4. 사용자 데이터까지 지우려면 압축을 푼 `DCWatch` 폴더 전체를 삭제합니다.

## 제한

- PC가 꺼져 있거나 절전 상태이면 감시할 수 없습니다.
- DCInside HTML 구조가 바뀌면 파싱이 일부 실패할 수 있습니다.
- 완전히 새로운 배설물/혐오 이미지는 해시 DB 기반 탐지로는 놓칠 수 있습니다.
- NudeNet은 선택 기능이며 설치/초기화에 실패하면 해당 기능만 비활성화됩니다.

## NudeNet/CNN GUI 옵션

기본 배포본에서는 `enable_nudenet = false`입니다. 이 상태에서는 SHA-256과 pHash 기반 감지만 수행하고, NudeNet 모델을 import하거나 실행하지 않습니다.

GUI에서 `NudeNet/CNN nudity detection`을 체크하고 설정을 저장하면, NudeNet이 없을 때 설치/확인 창이 뜹니다. `NudeNet 설치/확인` 버튼으로도 같은 확인을 할 수 있습니다.

개발 모드에서는 현재 Python 환경에 다음 명령과 같은 설치를 시도합니다.

```powershell
python -m pip install "nudenet>=3.4"
```

PyInstaller EXE는 완전 portable/sandbox 배포 원칙 때문에 외부 Python, 사용자 site-packages, APPDATA에 패키지를 설치하지 않습니다. EXE에서 NudeNet을 쓰려면 빌드 환경에 NudeNet을 설치한 뒤 NudeNet 포함 배포본을 다시 빌드해야 합니다.

## 테스트

```powershell
python -m pytest
```

테스트는 mock HTML과 생성 이미지 바이트만 사용합니다. 실제 위험 이미지는 저장소에 넣지 않습니다.

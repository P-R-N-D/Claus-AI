# Claus

Claus는 사람과 AI가 개인 및 팀 컨텍스트에서 대화하고, 파일과 지식을 공유하며, 필요할 때 Browser·Terminal·Workspace를 사용해 함께 작업 결과를 만드는 AI 협업 프로젝트입니다.

## 프로젝트 방향

Claus는 단체 채팅 하나에 모든 정보를 쌓는 구조보다 Topic과 Thread를 중심으로 맥락을 유지하는 방향을 지향합니다.

- **개인 AI**: 사용자와 AI의 비공개 대화, 개인 Topic, 개인 작업을 다룹니다.
- **팀 Topic/Thread**: 게시물과 댓글/스레드 형태로 사람과 공유 AI가 같은 업무 맥락에서 협업합니다.
- **공유 AI**: 허용된 Topic/Thread, 파일, 지식 범위를 컨텍스트로 사용하고 작업과 결과를 공개적으로 연결합니다.
- **파일과 지식**: 파일 공유와 RAG 등록을 분리합니다. 파일 업로드만으로 팀 또는 조직 지식에 자동 등록하지 않습니다.
- **백그라운드 작업**: AI의 장시간 작업은 채팅 흐름을 막지 않고 별도 작업 상태로 실행합니다.
- **실행 도구**: Browser, Terminal, Workspace는 작업이 필요할 때 선택적으로 사용합니다.
- **공유 결과 화면**: 문서, 이미지, 동영상, 차트, 표, Notebook/HTML 결과와 라이브 Browser를 함께 보는 화면을 지향합니다.

현재 범위에서는 전체 OS 데스크톱 스트리밍과 제어를 다루지 않습니다. 상호작용이 필요한 Computer Use는 Browser를 우선 대상으로 합니다.

Next.js frontend는 개인 AI 대화, 팀 Topic/Thread, 파일과 Artifact, AI 작업 상태, 공유 결과 화면과 Browser 작업 화면을 위한 사용자 UI로 확장합니다. Django/DRF는 사용자·권한·협업 컨텍스트·파일·지식·작업 상태를 관리하는 기본 backend/control plane으로 유지하고, Django Admin은 내부 운영자/admin workflow에 사용합니다.

장시간 AI 실행과 Browser/Terminal/Workspace 실행은 web request 처리와 분리하는 방향을 우선합니다.

## 현재 실행 가능한 scaffold

현재 저장소에 실제로 포함된 초기 scaffold는 다음과 같습니다.

- Frontend: `/`의 Next.js 사용자 UI와 `/console` 아래의 별도 제품 Console(`/console/*`로 예약되어 있으며 현재는 `/console` 인덱스 페이지만 존재), React, TypeScript, Tailwind CSS, axios, SweetAlert2, Node Playwright 테스트.
- Backend: Django 6.1, 하나의 Django project(`config`)와 두 Django app(`core`, `agent`). 검증한 유일한 실행 환경(lane)은 Linux x86-64의 CPython 3.12.15입니다. 다른 인터프리터·운영체제·CPU는 별도 검증이 필요하며, 이에 대한 정적 근거는 [플랫폼 및 가속기 lane](DEPENDENCY-STRATEGY.md#platform-and-accelerator-lanes)에 있습니다.
- URL: `/core/*`의 Django REST Framework control-plane API, `/agent/*`의 Agent FastAPI, `/admin/*`의 Django Admin.
- ASGI 구성: `config.asgi.application`이 FastAPI와 Django를 하나의 ASGI application으로 구성하며, Daphne 기반 `manage.py runserver`와 Uvicorn에서 동일하게 제공합니다.
- Local 연동: Next.js 개발 서버가 `/core/*`와 `/agent/*` 요청을 `http://127.0.0.1:8000` backend로 rewrite합니다.
- Health endpoint: `GET /core/health/`, `GET /agent/health/`.
- Database 설정: `DATABASE_URL`(PostgreSQL URL만 허용, 잘못된 값은 시작 시 오류)로 Django database를 구성하고, 설정이 없거나 비어 있으면 `backend/db.sqlite3` SQLite로 fallback합니다.
- File storage: 기본 Django storage는 비공개 S3-compatible object storage backend(`core/storage/s3.py`)이며 `.env.example`의 object storage 환경 변수가 필요합니다.
- Browser 기반: backend Python Playwright는 비동기 Agent Browser Computer Use package 경계를 제공하며 frontend Playwright 테스트와 분리되어 있습니다.

현재 scaffold에 구현된 것은 health endpoint, ASGI 구성, `DATABASE_URL` 기반 database 설정, S3-compatible storage backend이며 각각 저장소에 테스트가 있습니다. 협업 domain model, 제품 기능의 인증·인가, RAG, LLM orchestration, 백그라운드 실행, browser session, Terminal, Workspace, 실시간 통신, WebMCP, frontend i18n은 아직 구현되어 있지 않습니다.

이번 초기 scaffold에는 Docker, Nginx, K8s, Helm, production deployment manifest, custom domain model, custom migration, SQL schema 작업이 포함되지 않습니다.

## 기술 문서

AI 코딩 에이전트와 기여자를 위한 기술 문서는 영어로 작성되며 [docs/CONTEXT.md](CONTEXT.md)가 진입점입니다. 그 문서가 작업 유형별로 어떤 문서를 읽어야 하는지 안내합니다.

- [ARCHITECTURE.md](ARCHITECTURE.md): 전체 구조와 구현됨/계획됨 구분.
- [STATE-SCHEMA.md](STATE-SCHEMA.md): 개념적 상태 모양(DB schema 아님).
- [TESTING.md](TESTING.md): 현재 존재하는 테스트와 앞으로 필요한 검증.
- [DEPENDENCY-STRATEGY.md](DEPENDENCY-STRATEGY.md): 적용한 의존성 업데이트와 호환성·보안 예외, 기존 free-threading 방침에 따른 Python 3.15 Limited API/`abi3t` wheel 대응, 성능 검증 및 유지보수 전략. Python 3.15 지원은 아직 미검증입니다.
- [SECURITY-ARCHITECTURE.md](SECURITY-ARCHITECTURE.md): 신뢰 경계와 `SEC-*` 보안 요구사항.
- [CODE-REVIEW.md](CODE-REVIEW.md), [SECURITY-REVIEW.md](SECURITY-REVIEW.md): 코드 변경의 독립 리뷰와 보안 리뷰 절차.
- [INTERACTION-INTERFACES.md](INTERACTION-INTERFACES.md), [WEBMCP.md](WEBMCP.md), [I18N.md](I18N.md): 아직 구현되지 않은 상호작용 구조, WebMCP(실험적 기술) 계약, 프런트엔드 i18n 방향.

## 로컬 실행 순서

실제로 실행해 본 환경은 Linux x86-64뿐입니다. 나머지 행은 [플랫폼 및 가속기 lane](DEPENDENCY-STRATEGY.md#platform-and-accelerator-lanes)의 정적 근거이며 지원을 보장한다는 뜻이 아닙니다.

| 머신 | 아래 단계 실행 방법 |
|---|---|
| Linux x86-64 | 표시된 대로 backend 잠금 파일을 사용합니다(검증된 lane). |
| Linux arm64(glibc), NVIDIA DGX Spark 포함 | 로컬 개발 용도로만 표시된 대로 backend 잠금 파일을 사용합니다. arm64에서 정적으로 해석 가능하지만 실제로 실행해 보지는 않았으며, 이 플랫폼을 검증하려면 별도 이름의 잠금 파일이 필요합니다([backend locks](../backend/locks/README.md)). |
| Windows x64, 그리고 NVIDIA RTX Spark나 Qualcomm Snapdragon PC 같은 Windows on Arm | WSL2(Ubuntu)에서 실행합니다. 네이티브 Windows에서는 Linux 기준으로 해석한 이 잠금 파일로 동작하는 환경을 만들 수 없습니다. Windows x64에서는 Windows에서 Django와 psycopg가 요구하는 `tzdata` 없이 설치되고, Windows on Arm에서는 `autobahn`, `cryptography`, `psycopg-binary`에 `win_arm64` wheel이 없어 설치가 실패합니다. 네이티브 Windows on Arm에서는 Playwright도 x64 브라우저를 에뮬레이션으로 실행합니다. |
| Apple Silicon의 macOS 15 이상 | 로컬 개발 용도로만 표시된 대로 backend 잠금 파일을 사용합니다. 정적으로 해석 가능하지만 실제로 실행해 보지는 않았으며, 이 플랫폼을 검증하려면 별도 이름의 잠금 파일이 필요합니다. |
| macOS 14 이하, Intel Mac, Alpine(musl) | 이 잠금 파일로는 바이너리 설치가 불가능합니다. |

CUDA는 필요하지 않습니다. 현재 의존성 그래프에는 CUDA나 다른 가속기 전용 패키지가 없으므로 NVIDIA GPU가 있든 없든, Snapdragon PC처럼 CUDA가 없는 Arm 머신에서도 같은 단계를 따릅니다.

```bash
# Backend: Linux, macOS 또는 WSL2(POSIX 셸), uv 0.12.24 사용, 저장소 루트에서 실행
uv venv --managed-python --python 3.12.15
source .venv/bin/activate
python -c "import sqlite3; print(sqlite3.sqlite_version)"  # 3.37.0 이상이어야 함
uv pip sync --require-hashes --only-binary :all: backend/locks/cp312-linux-x86_64.txt
python backend/manage.py check
python backend/manage.py test core agent
python backend/manage.py runserver 127.0.0.1:8000

# Frontend: 다른 터미널에서 저장소 루트부터 실행
source .venv/bin/activate
nvm install
nvm use
npm install --global npm@11.21.0
cd frontend
npm ci
npx next typegen
npx playwright install chromium
npm run lint
npm run test:visual
npm run dev -- --port 3000
```

`--managed-python`은 시스템 인터프리터 대신 uv가 관리하는 CPython 빌드를 사용하게 합니다. 시스템 인터프리터의 SQLite는 Django 6.1이 요구하는 3.37.0보다 오래되었을 수 있습니다. Windows PowerShell에서는 `source .venv/bin/activate` 대신 `.venv\Scripts\Activate.ps1`로 환경을 활성화하지만, 현재 네이티브 Windows에서는 이 잠금 파일로 동작하는 환경을 만들 수 없으므로 표에 적은 대로 WSL2를 사용하세요.

`npm install --global npm@11.21.0` 단계는 필수입니다. Node 24.21.0에는 npm 11.19.0이 포함되어 있고, `package.json`의 `engines` 고정은 설치를 막지 않고 경고(`EBADENGINE`)만 출력합니다. `npx next typegen`은 새로 clone한 저장소에서 `tsc`와 편집기 타입 검사에 필요한, 추적되지 않는 `frontend/next-env.d.ts`를 생성합니다. `npm run dev`와 `npm run build`도 이 파일을 생성합니다.

`npm run test:visual`은 backend와 `next dev`를 직접 시작하므로 먼저 `npm run build`를 실행할 필요가 없습니다. CI가 아닐 때는 8000번과 3000번 포트에서 이미 실행 중인 서버를 재사용합니다. `PLAYWRIGHT_NEXT_SERVER=production npm run test:visual`은 앱을 다시 빌드한 뒤 대신 `next start`로 실행하며, 실행 중인 서버를 재사용하지 않습니다. 근거로 남길 실행에는 `CI=1`을 추가해 Playwright가 실행 중인 서버를 재사용하지 않게 하고 서버 모드를 기록하세요. 자세한 내용은 [TESTING.md](TESTING.md#current-scaffold-checks)를 참고하세요.

`http://127.0.0.1:3000`에서 사용자 화면을, `http://127.0.0.1:3000/console`에서 제품 Console을 열 수 있습니다. Django Admin은 `http://127.0.0.1:8000/admin/`에서 제공됩니다. `npm run dev`는 127.0.0.1에서만 수신합니다. 로컬 네트워크의 휴대폰에서 테스트하는 경우처럼 필요할 때만 의도적으로 `npm run dev -- --hostname 0.0.0.0`을 사용하세요.

프런트엔드는 Node 24.21.0, npm 11.21.0, Next 16, Tailwind 4를 사용합니다. 다른 플랫폼의 검증은 [백엔드 잠금 안내](../backend/locks/README.md), 브라우저 제약은 [프런트엔드 설정](../frontend/README.md)을 참고하세요. Python Playwright의 브라우저 설치는 별도이며 현재 health 화면 실행에는 필요하지 않습니다. 지원 종료 및 개발 도구의 잔여 보안 권고는 의존성 전략에 명시했습니다.

### 선택 사항: Django Admin 설정

`/console/*`의 제품 Console과 `/admin/*`의 Django ORM 기반 내부 Admin은 서로 다른 UI입니다. Django Admin에 로그인하려면 먼저 Django 기본 테이블을 초기화하고 로컬 관리자를 생성합니다.

```bash
python backend/manage.py migrate
python backend/manage.py createsuperuser
python backend/manage.py runserver 127.0.0.1:8000
```

`backend/db.sqlite3`는 로컬 개발 산출물이므로 커밋하면 안 됩니다. 현재 scaffold에는 custom domain migration이 없습니다.

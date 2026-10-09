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
- Backend: Django 6.1, 하나의 Django project(`config`)와 두 Django app(`core`, `agent`). 잠금 파일을 검증한 기준 환경은 Linux x86-64의 CPython 3.12.15이며 다른 런타임·플랫폼 조합은 별도 검증이 필요합니다.
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

```bash
# Backend: Linux x86-64, uv 0.12.24 사용, 저장소 루트에서 실행
uv venv --python 3.12.15
source .venv/bin/activate
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
npx playwright install chromium
npm run lint
npm run build
npm run test:visual
npm run dev -- --hostname 127.0.0.1 --port 3000
```

`http://127.0.0.1:3000`에서 사용자 화면을, `http://127.0.0.1:3000/console`에서 제품 Console을 열 수 있습니다. Django Admin은 `http://127.0.0.1:8000/admin/`에서 제공됩니다.

프런트엔드는 Node 24.21.0, npm 11.21.0, Next 16, Tailwind 4를 사용합니다. 다른 플랫폼의 검증은 [백엔드 잠금 안내](../backend/locks/README.md), 브라우저 제약은 [프런트엔드 설정](../frontend/README.md)을 참고하세요. Python Playwright의 브라우저 설치는 별도이며 현재 health 화면 실행에는 필요하지 않습니다. 지원 종료 및 개발 도구의 잔여 보안 권고는 의존성 전략에 명시했습니다.

### 선택 사항: Django Admin 설정

`/console/*`의 제품 Console과 `/admin/*`의 Django ORM 기반 내부 Admin은 서로 다른 UI입니다. Django Admin에 로그인하려면 먼저 Django 기본 테이블을 초기화하고 로컬 관리자를 생성합니다.

```bash
python backend/manage.py migrate
python backend/manage.py createsuperuser
python backend/manage.py runserver 127.0.0.1:8000
```

`backend/db.sqlite3`는 로컬 개발 산출물이므로 커밋하면 안 됩니다. 현재 scaffold에는 custom domain migration이 없습니다.

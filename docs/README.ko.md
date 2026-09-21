# Claus

Claus는 사람과 AI가 개인 및 팀 컨텍스트에서 대화하고, 파일과 지식을 공유하며, 필요할 때 Browser·Terminal·Workspace를 사용해 함께 작업 결과를 만드는 AI 협업 프로젝트입니다.

## 협업 모델

Claus는 단체 채팅 하나에 모든 정보를 쌓는 구조보다 Topic과 Thread를 중심으로 맥락을 유지하는 방향을 지향합니다.

- **개인 AI**: 사용자와 AI의 비공개 대화, 개인 Topic, 개인 작업을 다룹니다.
- **팀 Topic/Thread**: 게시물과 댓글/스레드 형태로 사람과 공유 AI가 같은 업무 맥락에서 협업합니다.
- **공유 AI**: 허용된 Topic/Thread, 파일, 지식 범위를 컨텍스트로 사용하고 작업과 결과를 공개적으로 연결합니다.
- **파일과 지식**: 파일 공유와 RAG 등록을 분리합니다. 파일 업로드만으로 팀 또는 조직 지식에 자동 등록하지 않습니다.
- **백그라운드 작업**: AI의 장시간 작업은 채팅 흐름을 막지 않고 별도 작업 상태로 실행합니다.
- **실행 도구**: Browser, Terminal, Workspace는 작업이 필요할 때 선택적으로 사용합니다.
- **공유 결과 화면**: 문서, 이미지, 동영상, 차트, 표, Notebook/HTML 결과와 라이브 Browser를 함께 보는 화면을 지향합니다.

현재 범위에서는 전체 OS 데스크톱 스트리밍과 제어를 다루지 않습니다. 상호작용이 필요한 Computer Use는 Browser를 우선 대상으로 합니다.

## 현재 초기 실행 scaffold

현재 저장소에 실제로 포함된 초기 scaffold는 다음과 같습니다.

- Frontend: Next.js, React, TypeScript, Tailwind CSS, axios, SweetAlert2, Playwright.
- Backend: 하나의 Django project(`config`)와 `core`(DRF), `agent`(Django app + FastAPI), Django Admin, Django ORM, django-cors-headers.
- Local 연동: Next.js 개발 서버가 `/core/*`와 `/agent/*` 요청을 `http://127.0.0.1:8000` backend로 rewrite합니다.
- Health endpoint: `GET /core/health/`, `GET /agent/health/`.
- Django Admin: `/admin/`.

위의 협업, RAG, AI 작업, Browser/Terminal/Workspace, 공유 결과 화면은 프로젝트 방향이며 현재 scaffold에 모두 구현되어 있다는 의미가 아닙니다.

이번 초기 scaffold에는 Docker, Nginx, K8s, Helm, production deployment manifest, custom domain model, custom migration, SQL schema 작업이 포함되지 않습니다.

## 로컬 실행 순서

```bash
# Backend
python -m pip install -r backend/requirements.txt
playwright install chromium
python backend/manage.py check
python backend/manage.py runserver 127.0.0.1:8000

# Frontend
cd frontend
npm install
npm run build
npm run test:visual
npm run dev -- --hostname 127.0.0.1 --port 3000
```

브라우저에서 `http://127.0.0.1:3000`을 열면 현재 frontend scaffold와 `/core/health/`, `/agent/health/` 응답을 확인할 수 있습니다. Backend 연결 실패 또는 재시도 실패 시 SweetAlert2 alert가 표시됩니다.

## 방향성

Next.js frontend는 개인 AI 대화, 팀 Topic/Thread, 파일과 Artifact, AI 작업 상태, 공유 결과 화면과 Browser 작업 화면을 위한 사용자 UI로 확장합니다. Django/DRF는 사용자·권한·협업 컨텍스트·파일·지식·작업 상태를 관리하는 기본 backend/control plane으로 유지하고, Django Admin은 내부 운영자/admin workflow에 사용합니다.

장시간 AI 실행과 Browser/Terminal/Workspace 실행은 web request 처리와 분리하는 방향을 우선합니다.

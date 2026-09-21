# 데이터베이스 스키마 설계 지침

이 문서는 Claus 데이터베이스 스키마의 설계 지침만 설명합니다. 실제 migration, SQL, ORM 모델은 이 문서 작업에서 만들지 않습니다.

## 원칙

- 개인 컨텍스트와 팀 공유 컨텍스트의 접근 범위를 분리합니다.
- Topic/Thread, Message, File, Artifact, AI Task를 서로 독립적으로 추적할 수 있게 설계합니다.
- 파일 공유와 RAG/지식 인덱싱 상태를 동일한 상태로 취급하지 않습니다.
- 장시간 AI 작업은 메시지 자체가 아니라 별도 task/run 상태를 가집니다.
- Browser/Terminal/Workspace 같은 실행 세션은 지속되는 협업 데이터와 분리합니다.
- Artifact는 원본 파일, 생성 결과, 표시 상태와 출처 task를 추적할 수 있어야 합니다.
- `tool_runs`는 실행 도구, 상태, 입력 요약, 결과 참조, 시간 정보를 추적할 수 있어야 합니다.
- Secret, token, password는 일반 애플리케이션 데이터로 저장하지 않습니다.
- 외부 또는 RAG 문서에는 `source`, `scope`, `owner`, `indexed_at`, 유효성/버전 관련 메타데이터를 둘 수 있습니다.
- Timestamp는 timezone-aware를 전제로 합니다.
- Schema 변경은 향후 migration으로만 수행합니다.

## 초기 후보 테이블

초기 후보 테이블 이름은 다음과 같습니다. 이 목록은 설계 방향을 문서화하기 위한 것이며, 실제 스키마 구현이나 확정 도메인 모델이 아닙니다.

- `topics`
- `messages`
- `files`
- `artifacts`
- `agent_tasks`
- `task_runs`
- `tool_runs`
- `approvals`
- `browser_sessions`
- `knowledge_documents`
- `knowledge_chunks`

현재 canonical 문서는 Topic/Thread 협업 경계만 정의하며, Thread를 별도 엔티티/테이블로 둘지 Message의 parent/root 참조를 이용한 threading으로 표현할지는 확정하지 않습니다. 이 선택과 후보 테이블 이름은 실제 스키마 설계 전에 접근 제어, 조회 패턴, 보존 정책을 검토하여 결정해야 합니다.

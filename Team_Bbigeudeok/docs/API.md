# 로컬 API와 설계

진입점은 `run_dashboard.py` → `spatial_explorer.serve()` → `make_server()`입니다. 정적 HTML/JS와 API를 같은 Python 프로세스가 제공합니다. `127.0.0.1:8766`만 바인딩하고 공개 인터넷용 실행기로 검증하지 않았습니다.

| 메서드·경로 | 인자·기능 |
|---|---|
| GET `/` | Folium 생성 지도 리소스를 삽입한 화면 |
| GET `/roa_dashboard.js`, `/plotly.js` | 로컬 스크립트·설치된 Plotly 번들 |
| GET `/api/roa/catalog` | 동 목록·유형 범례·검증된 분류 개수 |
| GET `/api/roa/map` | 강남구 동 경계와 R/O/A 유형 |
| GET `/api/roa/detail` | `dong`: 검증된 8자리 코드. 분류·월별 근거·보류 정보 |
| GET `/api/roa/welfare` | `dong`, `mode=historical/current`, 현재 조회 시 `as_of=YYYY-MM-DD`, 선택 `query`, `limit`, `offset`, `curated_only`, `use_api_cache` |
| GET `/api/roa/resource` | `id`, 선택 `use_api_cache=true/false`. 원문 raw를 제외한 자원 상세 |
| GET `/api/agent/status`, `/api/service/status` | 비밀값 없는 공급자·자료 상태 |
| POST `/api/agent` | `dong`, `question`, `mode`, 선택 `as_of`, `engine=structured/llm`, `query`, `curated_only` |
| GET `/api/catalog`, `/api/map`, `/api/detail` | 기존 SK 공간 탐색. 상세 인자 계약은 `SpatialExplorer.validate_query()` 참조 |

POST는 `Content-Type: application/json`, 본문 1~12000바이트이며 질문은 1~1000자입니다. 중복 JSON 키, 비유한 숫자, 중복 프레이밍 헤더와 허용되지 않은 필드는 거부합니다. POST Host/Origin은 로컬 주소만 허용합니다. 오류는 잘못된 요청 400, Origin 거부 403, 알 수 없는 경로 404, 자료 불가·연결 포화 503 등으로 응답합니다.

Agent는 서버가 고정한 지역·기간의 분류, 근거, 추가 확인 질문, 검색 결과만 도구로 조회합니다. 실제 LLM 모드의 한도는 질문당 공급자 4회·모델 도구 8회이며 서버의 도구 검증과 정형 대체가 적용됩니다. LLM 질문 동시 1개, 서버 소켓 동시 8개·유휴 10초 제한이 있습니다.

공용 인터넷용 인증·계정별 영속 예산·프록시 헤더 정책은 아직 없습니다. GET API가 돌려주는 지도·파생값도 데이터 공개에 해당할 수 있으므로 권한 확인 없이 인터넷에 노출하지 마세요. 일반 파일 다운로드·환경변수 조회 도구는 제공하지 않습니다.

# 재현 가능한 범위와 자료 의존성

## 공개 사본만으로 가능한 검증

팀 폴더에서 `python -B -m unittest discover -s tests -v`와 `python -B scripts/check_public.py`를 실행합니다. 최소 환경은 Python 3.11 이상이며 추가 패키지·외부 키가 필요하지 않습니다. 테스트는 인공 숫자·모의 응답만 사용합니다. 현재 공개 테스트 10개는 실제 181개 회귀의 전부나 대체 검증이 아닙니다.

소스의 전체 Python 문법, JSON 계약, 비어 있는 노트북 출력, 허용 파일 목록과 해시를 검사합니다. GitHub Actions도 이 범위만 검증합니다. 원래의 데이터 의존 회귀 테스트와 과거 검증 보고서는 실제 수치·파생 결과를 포함할 수 있어 이번 공개 사본에서 제외했습니다. 로컬 비공개 원본에는 그대로 보존했습니다.

## 전체 서비스에 별도로 필요한 자료

| 경로(팀 폴더 기준) | 역할 |
|---|---|
| `데이터/관계망_활동_통합/`의 계약 지정 4개 입력 | ROA 신호·v2 통합표·기존 Agent JSON·행정동 대응표 |
| `Code/sk_admin_mapping/outputs/p1_20261013/regional_metrics.csv` | 비교용 활동 집계 |
| `데이터/복지자원/`의 계약 지정 3개 CSV | 로컬 복지 카탈로그 생성 입력 |
| `Code/sk_admin_mapping/outputs/canonical/agent/`의 통합·분류 JSON | 현재 코드·입력과 일치하는 조회 자료 |
| `Code/sk_admin_mapping/outputs/canonical/welfare/welfare_catalog.v1.json` | 로컬 복지 조회 자료 |
| `Code/sk_admin_mapping/outputs/canonical/spatial_explorer/` | 완료 manifest·coverage·위치 gzip CSV·3개 NPZ·3개 지리 gzip 자료 |
| `Code/sk_admin_mapping/outputs/canonical/welfare/welfare_api_cache.v1.json` | 선택적 전체 API 조회 캐시. 약 372MB |
| `Code/sk_admin_mapping/outputs/validation/step1b/source_checks.json` 등 | 선택적 출처·실연결 진단. 없으면 미확인 상태 유지 |

관련 파일은 공개 저장소에서 배포하지 않습니다. 정확한 파일 목록과 입력 해시는 `roa_contract.json`, `welfare_contract.json`을 따릅니다. 원자료부터 공간 분석 전체를 수행할 때는 SK CSV·정의서·행정경계·주민등록인구도 별도 준비해야 합니다.

## 권한 보유자의 이전·재검증

원본 프로젝트의 디렉터리 계약을 팀 폴더 안에 유지합니다. 이전된 경로·mtime·코드 해시가 기존 캐시 manifest와 다르면 읽기 단계에서 거부됩니다. 기존 캐시를 복사하기만 하면 동작한다는 보장은 없습니다. 현재 `SpatialExplorer`는 입력·출력의 경로와 파일 상태를 검사합니다.

입력이 모두 원 계약 해시와 일치하는지 먼저 확인한 후, 필요할 때만 작은 Agent·복지 문서를 다시 생성합니다.

```powershell
python -B Code/sk_admin_mapping/build_agent_input.py --check
python -B Code/sk_admin_mapping/build_classified_agent_input.py --check
```

검사가 실패하면 원인부터 확인하고 관리자 판단으로만 재생성하세요. 대용량 공간 파이프라인의 `--prepare`를 단순 복제 검증 과정에서 실행하지 마세요. 원본 데이터나 검증 해시를 새 파일에 맞춰 임의 수정하지 않습니다.

## 환경과 한계

`requirements.txt`는 기존 기능의 버전 범위를 유지합니다. `requirements-observed.txt`는 STEP 2 검증 PC에서 관찰한 직접 패키지 버전이며 완전한 잠금 파일이 아닙니다. 다른 OS·Python에서 전체 의존성을 새로 설치하는 검증은 별도 필요합니다. 공개 CI는 표준 라이브러리 테스트만 실행합니다.

R/O 표준화 원분석 코드·고정 가중치가 없어 최초 원자료부터의 완전 재현은 불가능합니다. SK CRS는 `ASSUMED_UNVERIFIED`, 관계망 산정기간은 `ASSUMED_PREV_3M_UNVERIFIED`를 유지합니다. 현재 이용 가능·자격·정원·효과가 확인됐다고 주장하지 않습니다.

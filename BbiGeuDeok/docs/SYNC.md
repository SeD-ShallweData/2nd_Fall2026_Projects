# 원본과 공개 사본의 동기화

## 작업 위치

동아리 저장소를 clone한 폴더가 Git 작업 기준입니다. 이 팀의 공개 코드·문서·테스트는 `BbiGeuDeok/`, 팀 전용 CI는 `.github/workflows/team-bbigeudeok.yml`에서 관리합니다. 다른 팀 폴더는 저장소 루트에서 추가하며, Git은 빈 디렉터리를 추적하지 않으므로 자리만 둘 때는 README나 `.gitkeep` 파일을 넣습니다.

별도로 보존한 비공개 원본 프로젝트는 데이터·완료 캐시·가상환경을 사용하는 실제 분석·서버 실행 위치입니다. 공개 clone만으로 전체 서비스를 실행할 수 없으며, 원본을 통째로 저장소에 복사하지 않습니다. 경로와 캐시 검증을 포함한 이전 전까지 기존 실행 위치를 유지합니다. 공개 사본의 기능 수정은 비공개 원본에도 반영하여 실제 데이터에서 별도로 검증합니다.

## 공개 반영 절차

공개 사본의 Python/JS/HTML과 JSON 계약은 원본의 검증된 허용 목록에서 바이트 그대로 복사합니다. 노트북은 사본에서만 출력·실행 횟수·첨부·실행 메타데이터를 제거합니다. 새 README·공개 문서·인공 테스트·예제 환경설정은 별도로 관리합니다.

원본 관리자 도구는 `tools/step2/build_public.py`이며 비공개 원본 프로젝트에 보존합니다. STEP 2 생성기는 최초 폴더명 `Team_Bbigeudeok`을 사용하므로, 출력물을 현재 clone에 그대로 덮어쓰지 않습니다. 현재 폴더명 `BbiGeuDeok`에 맞춰 경로를 대조하고 검토된 파일만 반영합니다. 관리자 도구·로컬 조사 결과·원본 파일 목록은 이 저장소에 포함하지 않습니다. 기존 `prepare_github.py`는 초기 코드만 포함하므로 최신 Agent 공개 패키지 생성에 사용하지 않습니다.

1. 비공개 원본에서 허용 목록과 공개용 문서 템플릿을 변경합니다. 기본적으로 새 데이터·로그·결과 파일은 추가하지 않습니다.
2. `python -B tools/step2/build_public.py`를 실행합니다. 홈/바탕화면 비공개 env 값은 메모리에서만 대조하며 보고서에는 실제 값을 쓰지 않습니다.
3. clone의 `BbiGeuDeok/`에 검토된 파일만 반영합니다. `PUBLIC_FILE_MANIFEST.json`의 팀 경로는 `BbiGeuDeok/`으로 유지하고, 변경·추가·삭제한 공개 파일과 팀 workflow의 크기·SHA-256을 검토 후 갱신합니다. manifest 자체는 목록에 넣지 않습니다. 관련 없는 파일의 해시는 그대로 유지합니다.
4. `git add` 후 실제 index 목록·각 blob과 manifest를 다시 검사합니다. 문서 변경도 새 검사가 필요합니다.
5. 기존 원격 이력이 생겼다면 먼저 clone/fetch하고 새 브랜치에서 이 팀 폴더와 팀 전용 workflow만 반영합니다. 기존 README는 팀 링크 추가처럼 검토한 변경만 합니다.
6. 사용자가 실제 공개 업로드를 확인한 뒤 commit/push합니다. 강제 push는 사용하지 않습니다.

최소 검증은 저장소 루트에서 다음과 같이 실행합니다. 두 번째 명령은 작업 파일과 실제 Git index를 함께 검사하므로, 검토한 변경을 `git add`한 뒤 실행합니다. index에 예전 파일이 남으면 `staged content differs`가 발생합니다.

```powershell
python -B -m unittest discover -s BbiGeuDeok/tests -v
python -B BbiGeuDeok/scripts/check_public.py
```

공개 사본에서 직접 수정했다면 비공개 원본으로 반영하고 동일 과정을 반복합니다. 생성된 `PUBLIC_FILE_MANIFEST.json`은 자신을 제외한 예정 파일의 해시를 담습니다. 일반 검사는 이 팀과 전용 workflow만 확인해 다른 팀의 추가를 방해하지 않습니다. 최초 새 저장소 검사에서는 `python -B scripts/check_public.py --initial-package`로 루트 README·ignore를 포함한 전체 예정 목록과 index까지 확인합니다.

권한이 불명확한 자료는 검사를 통과하더라도 공개하지 않습니다. 문자열 검사는 알려지지 않은 과거 비밀값이나 저작권을 보장하지 않습니다.

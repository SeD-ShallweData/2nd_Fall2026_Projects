# 보호된 로컬 시연 도구

`demo_gateway.py`는 루프백 대시보드 앞의 제한 프록시, `run_demo.py`는 백엔드·게이트웨이·Quick Tunnel의 실행과 종료를 관리합니다. Windows 실행 경로를 사용합니다. 공개 저장소에는 소스·설명 페이지·인공 테스트·승인되지 않은 예제 설정만 포함합니다. 실제 접속 링크, 설정, 사용자 이메일, 인증 쿠키, 바이너리, 로그, 검증 기록과 데이터는 포함하지 않습니다.

## 실행 준비

권한 있는 비공개 실행 프로젝트에서 기존 데이터·완료 캐시·가상환경을 준비합니다. 공개 clone만으로 실제 대시보드를 시작할 수 없습니다. 다음 상대 경로는 해당 프로젝트 루트를 기준으로 합니다.

1. 공식 [cloudflared 릴리스](https://github.com/cloudflare/cloudflared/releases)에서 Windows amd64 바이너리를 받고 출처·버전·게시된 SHA-256을 검증합니다. 검증한 파일만 `Code/sk_admin_mapping/outputs/validation/step4a/cloudflared.exe`에 둡니다. 자동 설치·다운로드는 수행하지 않습니다.
2. `demo.example.json`을 같은 폴더의 `demo.local.json`으로 복사합니다. 예제 이메일은 실제 승인된 정확한 주소로 바꿉니다. 예제는 데이터에 접근하지 않는 `preview` 모드이며 외부 전송 승인을 포함하지 않습니다.
3. 실제 데이터 모드는 자료 권한 근거 또는 명시적인 사용자 테스트 전송 승인 범위를 로컬에 기록한 뒤 구성합니다. 사용자 승인은 주최 측·제3자 권리 확인과 구분합니다. 권리가 미확인이라면 `data_rights.confirmed`를 임의로 바꾸지 않습니다. 링크 인증의 구체적인 설정 조건은 [STEP 4-A 안내](../../docs/STEP4A_DEMO.md)를 따릅니다.
4. `--check` 후 실행합니다. 기존 서버가 있으면 기본적으로 중단합니다. 운영자가 프로세스·경로를 조사한 경우에만 `reuse_inspected_backend=true`로 명시하며, 도구는 자산 바이트와 상태를 추가 확인합니다.

```powershell
& 'Code/sk_admin_mapping/.venv/Scripts/python.exe' -B 'tools/step4/run_demo.py' --check
& 'Code/sk_admin_mapping/.venv/Scripts/python.exe' -B 'tools/step4/run_demo.py'
```

`cloudflare_email` 모드는 `--allowed-mail`을 사용합니다. 인증 직후 API 세션이 만료되는 경우 이 모드를 성공으로 취급하지 않습니다. `secret_link` 모드에서는 실행할 때 난수 접속값을 만들고 전용 링크를 비공개 `outputs/validation/step4a/access_link.local.txt`에만 저장합니다. 기본 URL만으로 데이터에 접속할 수 없습니다. 전체 전용 링크는 비밀번호처럼 취급하고 허용된 사람에게만 전달합니다. 재시작 시 주소와 접속값이 바뀝니다.

## 상태·중지·재시작

```powershell
& 'Code/sk_admin_mapping/.venv/Scripts/python.exe' -B 'tools/step4/run_demo.py' --status
& 'Code/sk_admin_mapping/.venv/Scripts/python.exe' -B 'tools/step4/run_demo.py' --stop
```

`STOPPED`를 확인한 뒤 실행 명령으로 재시작합니다. 포그라운드 실행은 Ctrl+C로도 종료할 수 있습니다. 도구가 직접 만든 자식 프로세스만 종료합니다. 비정상 종료로 실행 기록이나 lock이 남으면 PID·경로·포트를 확인하고 복구합니다. 전체 Python 프로세스를 일괄 종료하거나 확인 없이 lock을 삭제하지 않습니다. 전원·네트워크·러너가 유지되는 동안에만 접속할 수 있습니다.

## 합성 검증

```powershell
python -B tools/step4/test_demo_gateway.py
```

표준 라이브러리로 16개 인증·프록시·예산·로그 경계 테스트를 실행합니다. 임시 루프백 HTTP 서버와 인공 응답만 사용하며 실제 데이터·키·Upstage·터널을 사용하지 않습니다. CI 통과는 외부 배포 성공의 증거가 아닙니다.

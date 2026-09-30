# 요구사항과 검증 연결

실제 검증 결과는 CURRENT_STATE.md와 `docs/template_validation.json`, `.work/integration/result-*.json`에 기록합니다. 단위시험의 fake runner는 실제 이미지·HTTP·AWS 검증이 아닙니다.

최신 2026-09-30 일반 UI 승인 경로 검증: `approved_project_bridge`를 UI 조건에 연결하고 기존 공통 normal bridge runtime을 재사용했습니다. 명시적 승인·snapshot·실행조건·job 지문을 기록한 뒤에만 실행합니다. 일반 UI와 동일한 `service.start_build()` workflow에서 자체 Java/Paketo와 Python/Dockerfile을 새로 빌드해 두 건 모두 컨테이너 시작, 시작/재시작의 127.0.0.1 단일 mapping, HTTP 200·기대 marker, CLEANED를 확인했습니다. Java 포트 24992→25000, Python 30924→30932이며 재조회한 포트를 사용했습니다. 근거: `docs/ui_workflow_validation.md`와 `.json`. 이번에는 Terraform/AWS/push/새 bundle/소형·대형/tar를 실행하지 않았습니다.

최신 기본 회귀는 **480 passed, 1 skipped**(단위 450, AppTest 30), Ruff PASS입니다. 근거: `.work/validation/approved-ui-pytest.xml`. 별도 실제 Docker 통합 두 건 PASS는 기본 opt-in pytest SKIP 및 AppTest stub과 구분합니다. 실제 저장 기록을 제품 renderer로 표시한 브라우저에서 8개 단계 PASS·100%·image ID를 확인했습니다. 브라우저 버튼 클릭으로 시작한 통합시험이라고 표현하지 않습니다. 초기 승인 파일 생성 오류로 빌드 전 차단된 시도는 보존하고 수정 후 재실행했습니다.

2026-09-30 실제 생성 bundle 추가 검증: Java/Paketo 중형과 Python/Dockerfile 중형 ZIP을 각각 별도 임시 폴더에 추출하고, registry/service 네 root에서 실제 `fmt -check`, `init -backend=false`, `validate`를 실행했습니다. 12개 명령 모두 PASS(exit 0), 원본 ZIP과 추출된 기존 파일은 변경 없음입니다. generator/템플릿 수정은 없고, 이번에는 mock plan/test도 실행하지 않았습니다. 세부 명령 출력·ZIP SHA256은 `docs/bundle_validation.json`, ACM/HTTP/자원/IAM 검토는 `docs/bundle_validation.md`입니다. 소형/대형 bundle CLI와 실제 AWS는 미실시입니다.

2026-09-30 % 표시 변경 후 최종 기본 회귀: **430 passed, 1 skipped**, Ruff PASS. 단위시험 404개, AppTest 26개가 통과했고, opt-in 실제 컨테이너 시험 1개는 기본 명령에서만 skip입니다. 근거: `.work/validation/percentage-pytest.xml`. 이전 398 PASS는 `.work/validation/network-pytest.xml`에 보존합니다. 별도 실제 통합시험은 **PASS**이며 아래와 `docs/network_validation.md`에 기록합니다. 이번 화면 변경 검증에서는 실제 Docker/pack/Terraform/AWS를 재실행하지 않았습니다. 이전 기록 226 PASS와 328 PASS는 `.work/validation/pytest.xml`, `progress-pytest.xml`에 보존합니다.

| 요구사항 | 구현 | 검증 근거 |
|---|---|---|
| 독립 작업공간·원본 보존 | 새 패키지, 새 .venv, 원본 수정 없는 복사 | 초기 경로/Git 진단; input safety 원본 hash 비교 |
| 제한적 Java/Dockerfile 판정 | project_input.py | test_input_safety: XML 실패, Java21 충돌, 콘솔, DB 선언, 후보 선택 |
| 안전한 빌드 입력 | BuildContext, 단일 InputLimits | 루트/홈/UNC/reparse/link/hardlink/시간/크기/파일수/깊이/제외/COPY 누락/소스변경 테스트 |
| 비밀정보·개인경로 보호 | redaction.py, ProcessRunner | assignment/key/DB URL/개인경로/긴 로그/분할 private key 마스킹 |
| 인자·환경·프로세스 경계 | process_runner.py | shell 거부, 절대 exe, 환경 allowlist, 취소/timeout/출력 제한 |
| 실제 두 빌드 경로 | builders/paketo_java.py, dockerfile.py | 명령 계약 unit; 최신 ui_workflow_integration.py에서 UI와 같은 service.start_build로 실제 새 빌드 PASS |
| Docker 29 호환 image inspect | builders/base.py | optional Config.Volumes 누락 회귀, 실제 재실행 |
| Java 최종 /tmp metadata | builders/paketo_java.py | 실제 FROM image ID 준비·USER/rootfs 보존·ONBUILD 거부·tmpfs override 시 익명 volume 없음 |
| 실제 HTTP 판정 | runtime_check.py | 상태코드·marker·404·redirect·응답 한도·느린 응답·proxy 비사용; 로컬 HTTP 서버 단위시험 |
| 자원·권한 제한 | 공통 PRESETS → Docker 인자 | CPU/RAM/PID/loopback/no host mounts/non-root/read-only/log limits; 승인 UI normal bridge, 과거 internal 호환 |
| 승인된 UI 프로젝트 normal bridge | service.py, runtime_approval.py, 공통 runtime_check.py | 분석은 실행 없음, 양쪽 승인 필수, source/conditions/manifest/build 승인 지문, 실행 job별 승인·자원, 8080·restart 강제; 실제 두 샘플 PASS |
| 기존 자체 샘플 normal bridge 전용 모드 | trusted_samples.py, RuntimeConditions.network_mode | 고정 소스/빌드 근거/opt-in/8080/marker guard 보존; UI 승인 모드와 구분 |
| 실제 포트 mapping | runtime_check.py, PortMappingEvidence | 시작·재시작 inspect+docker port 대조; 127.0.0.1 임시 포트; 부재/불일치/LAN 차단 unit과 실제 8회 시험 |
| 재시작 포트 재할당·HTTP 완료 | 실제 관찰 후 runtime 수정 | 재할당된 검증 포트 사용; 실제 루프백 HTTP/1.0·1.1 Content-Length/EOF/잘린 본문 회귀; 실제 Docker 성공 확인 |
| 동일 이미지/조건 연결 | 모델 fingerprint, service/export gates | 태그 교체·소스/포트/환경/프리셋 변경·stale/실패/정리불가 거부 |
| 한 작업·취소·재시도 | jobs.py | worker·OS lock·atomic JSON, 중복/취소/예외 테스트 |
| UI 5단계 | ui.py | AppTest 승인·뒤로·상태·재시도·크기변경·전체 성공 fixture 흐름·실패 export 거부 |
| 세부 단계·실패 분류·이미지 ID | jobs.py, progress.describe_workflow, ui.py | 빌드 성공 artifact 즉시 보존; 6개 요청 결과 분류; mapping 실패 시 후속 미실행, cleanup이 HTTP 성공을 뜻하지 않음; 실제 두 결과의 제품 renderer 브라우저 확인 |
| 실시간 진행 표시 | ProcessProgress → jobs.py → progress.py → UI fragment | 실제 Python 자식 프로세스의 종료 전 출력/조용한 구간/CRLF/분할 비밀값/취소; 시각·상태 순수 단위시험; AppTest 지연 안내·실패 로그·타이머 고정·취소 중 표시 |
| 단계 진행률 %·막대 | describe_completion → UI | 완료 경계별 계산, 시간/로그로 증가하지 않음, cleanup 진입에 성공 추정 금지, 필요한 restart/HTTP PASS/CLEANED/DONE gate; AppTest 실제 progress 값 20/60/100·중단/취소 표시; runtime marker의 성공/실패 경계는 명시적 fake runner |
| tar 해시 중 취소 | terraform_export.py | 명시적 fake runner와 해시 입력으로 중간 취소·최종 확정 전 취소·임시 파일 정리 확인; 실제 tar 재검증은 NOT_RUN |
| 브라우저 표시 | loopback Streamlit | 실제 Windows in-app browser 첫 화면, 샘플 분석, 실행 준비·승인 화면 확인 |
| Terraform 2 root | registry + service | 정적 policy 검사, 실제 fmt/init -backend=false/validate, 전 provider 명시적 mock plan |
| 실제 중형 bundle 2개 CLI | 원본 ZIP 개별 추출·불변 hash | 네 root 실제 fmt/init -backend=false/validate PASS; bundle_validation.json, 이번 mock 미실행 |
| IAM/TLS/SG/image/presets | JSON 변수·jsonencode | registry 1 + service 10 Terraform mock assertions; 자체템플릿 hash allowlist |
| 사양·runbook·빈 AWS 기록 | reports.py, terraform_export.py | CPU/RAM/포트/이미지 일치, 필수 외부값 누락 유지, AWS_NOT_TESTED 고정 |
| 작은 ZIP / 실제 tar | streamed image save | ZIP 경로·크기·hash, tar 실패정리·disk 경계·no memory load unit; opt-in tar load 실제 시험 |
| 선택적 정리 | runtime_check + cleanup_local.py | ID/label/record 경계, 다른 작업·이미지 보존, active lock, snapshot-only 삭제 unit |
| 실행 스크립트 | setup.cmd/run.cmd/test.cmd | 새 Windows .venv 설치, 공백 경로 run.cmd 실제 기동, 기본 회귀 실행 |

## 세 종류의 테스트

1. 기본 pytest: Docker/pack/Terraform/AWS를 실행하지 않습니다. 자체 Python subprocess 및 루프백 HTTP 테스트와 명시적 단위 stub을 사용합니다. 실제 Docker test 1개는 opt-in이 없으면 제외됩니다.
2. 실제 컨테이너: `RUN_LOCAL_CONTAINER_TESTS=1`에서 자체 작성 샘플만 실행합니다. image ID·Linux/amd64·프리셋·HTTP·재시작·정리·export를 실제 기록합니다. 이름만 바꾼 복사본도 새로 빌드합니다.
3. Terraform 개발 검증: 검토된 템플릿만 새 격리 폴더에 복사합니다. credentials를 제거하고 모든 provider를 mock하며 run마다 `command=plan`을 명시합니다. 실제 AWS plan은 실행하지 않습니다.

## 미검증 범위

진행률 표시 최종 보정: 실패 뒤 CLEANED인 경우 전체 %는 유지하고 마지막 단계에 `정리 완료 · 작업 중단`을 별도 표시합니다. 보정 후 진행률 AppTest 9개를 다시 실행해 PASS했으며 Ruff도 PASS입니다. 근거: `.work/validation/percentage-ui-final.xml`. 브라우저 화면이나 실제 Docker를 재실행한 것으로 기록하지 않습니다.

AWS 적용·IAM 실권한·PassRole·ACM/DNS·ALB 실제 연결·Fargate volume 쓰기 권한·학교 계정 quota는 미검증입니다. 인터넷 차단 환경, Gradle, 운영 앱의 업무 기능·DB·영구파일도 검증하지 않았습니다. UI AppTest의 성공 fixture를 실제 샘플 성공 개수에 포함하지 않습니다.

브라우저 확인은 에이전트가 수행한 화면 확인이며, 비개발자 대상 인간 사용성 연구를 대신하지 않습니다. 한글 입력 경로는 단위시험으로 확인하며 한글 설치 경로의 별도 종단 실행시험은 수행하지 않았습니다.

2026-09-30 승인된 normal bridge 자체 샘플 통합은 PASS입니다. 이전 실제 Java/Python image ID를 재확인하여 초기 중형 2개와 각 소형/중형/대형 6개 모두 HTTP 200·marker·재시작·CLEANED를 관찰한 뒤 성공 bundle 6개를 생성했습니다. 근거는 `.work/integration/result-63e3c3a6322647f880f9d49e5e91693c.json`; 실제 mapping 표와 ZIP 링크는 `docs/network_validation.md`입니다. ZIP/hash/image/preset/fingerprint 검증은 `.work/validation/network-output-verification.json`입니다. 이번 실제 결과를 단위 mock이나 AppTest 성공으로 대체하지 않았습니다.

위 과거 샘플 전용 시험 당시에는 일반 UI/임의 사용자 앱의 internal bridge를 유지했습니다. 현재 명시적으로 승인된 일반 UI 프로젝트는 문서 첫 절의 approved_project_bridge를 사용합니다. 이전 internal bridge와 이름 변경 복사본의 BLOCKED 기록은 소급 변경하지 않습니다. 최신 일반 UI 수정 시험에서 컨테이너 404·tar save/load·Terraform CLI·실제 AWS를 재실행하지 않았습니다. 기존 진단 tar의 과거 save/load 기록과 성공 bundle을 구분합니다.

2026-09-30 추가 브라우저 실행은 자체 Dockerfile 샘플 job `be40e2e2c1a6465a909f55b8c6000b89`입니다. 실제 이미지 생성/inspect와 화면의 경과 시간·빌드 로그·실패 결과를 확인했습니다. HTTP BLOCKED, CLEANED이며 전체 성공으로 세지 않습니다. 진행 정보의 무출력 60초/취소/실패 장면은 AppTest와 자체 Python 프로세스 테스트로 확인했으며, 장시간 Java 빌드를 새로 실행한 결과로 표현하지 않습니다.

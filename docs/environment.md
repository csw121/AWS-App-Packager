# 실행 환경 기록

이 문서는 2026-09-29 초기 환경과 internal bridge 차단의 **과거 기록**입니다. 2026-09-30 이후 승인된 일반 UI는 normal bridge + 127.0.0.1 임시 포트로 두 샘플 실제 시험을 통과했습니다. 최신 결과는 [UI workflow 검증](ui_workflow_validation.md), 다른 PC의 준비·GitHub 인수인계는 [HOME_HANDOFF.md](HOME_HANDOFF.md)를 따르세요. 아래 초기 Git 미생성/normal bridge 미전환 설명을 현재 제품 상태로 해석하지 않습니다.

확인일: 2026-09-29. 새 작업공간: `D:\AWS App Packager`. 시작 시 빈 폴더였고 `git rev-parse --show-toplevel`은 저장소가 아님을 확인했다. 상위 Git 이력, 이전 Cloud Readiness Lab 코드·환경·프로세스는 사용하거나 변경하지 않았다. Downloads의 지정 명세만 이 폴더에 복사했다. 새 Git 저장소/원격 연결은 만들지 않았다.

| 항목 | 확인 결과 |
|---|---|
| OS | Windows 11 10.0.26200, AMD64 |
| Python | CPython 3.13.2 64비트 |
| Git | 2.51.0.windows.2 |
| PowerShell | 7.6.5 |
| Streamlit | 1.64.0 |
| Pydantic | 2.13.5 |
| pytest / Ruff | 9.1.1 / 0.16.9 |
| Terraform CLI | 1.14.8 windows_amd64; 개발팀 검증용 |
| AWS provider | 6.14.0, 공식 provider 서명 검증된 init |
| pack | 0.40.9+git-8210eb1.build-6996 |
| Docker 초기 상태 | CLI 28.3.2 / Desktop 4.44.2, daemon 중지 |
| Docker 이후 확인 | CLI/Engine 29.8.1 / Desktop 4.93.0, Linux/amd64 |
| Docker context | desktop-linux, 로컬 named pipe |
| 디스크 | 시작 시 D: 약 920,986,222,592 bytes 여유, 쓰기 probe 성공 |

Docker 시작과 공식 pack 프로젝트 전용 다운로드는 사용자 승인 후 실행했다. pack ZIP의 공식 `.sha256`와 다운로드 파일을 비교한 후 `.tools/pack/`에 압축을 풀었다. 시스템 PATH는 변경하지 않았다. Docker 초기 시작은 dockerInference 소켓 오류로 종료되었고, 첫 빌드 중 daemon 갱신 구간에서 run-image pull이 unexpected EOF로 실패했다. 이후 읽기 전용 진단에서 Docker 29.8.1 정상 응답을 확인했다. 이 에이전트는 Docker 업데이트·공장초기화·WSL/방화벽/Hyper-V 설정 변경을 실행하지 않았다.

Python venv 및 pytest 임시 디렉터리는 샌드박스 ACL에서 PermissionError가 발생하여 필요한 명령만 권한 정책의 검토를 거쳐 실행했다. 호스트 일반 사용자 환경 동작과 샌드박스 권한을 혼동하지 않는다.

## 고정 이미지

공식 Docker Hub repository tag metadata에서 2026-09-29 확인한 Linux/amd64 digest:

- Paketo builder `0.4.579`: `sha256:c696f4078229f82f7e3faf9fd806554f897dcdc09686c9fee3edd0ff914560ff`
- Paketo run image `0.1.227`: `sha256:ff484d9146f670e30c5186f767b7adc1f94eabc2c93114c3f7b0dbb632cd36a5`
- Python `3.13.2-slim-bookworm`: `sha256:0b3498e251759df85a00474be7d3b791d6abe1600ce3531a649e42964749655f`

실제 샘플 통과 여부는 CURRENT_STATE.md에 따로 기록한다. 다운로드 metadata 확인을 앱 실행 검증으로 취급하지 않는다.

## 재현 및 준비

`requirements.lock.txt`는 새 `.venv`에 실제 설치한 버전 전체를 고정한다. 이전 가상환경이나 잠금 파일을 복사하지 않았다. `setup.cmd`는 잠금 버전을 설치하고 `run.cmd`는 실행만 한다. Docker/pack 미설치 또는 엔진 연결 불가 시 화면은 환경 준비 안내를 표시한다. 원격 endpoint와 Windows/ARM daemon을 자동 사용하지 않는다.

네트워크를 끊고 실행한 시험은 없으므로 오프라인 실행 보장은 없다. AWS 인증/API/push/실제 plan/apply/destroy는 미실시다.

## 실제 컨테이너 시험의 환경 차단

Docker 29.8.1에서 두 실제 생성 이미지와 이름 변경 복사본을 중형(1 vCPU/2048 MiB)으로 시작했다. 그러나 internal bridge의 요청 `127.0.0.1::8080`은 `HostConfig.PortBindings`에만 남고 실제 `NetworkSettings.Ports`는 `{"8080/tcp": []}`였다. HTTP와 재시작 시험은 **BLOCKED/NOT_RUN**이며 성공 bundle은 만들지 않았다. 모든 시험 container/network는 소유 ID/label 확인 뒤 CLEANED로 기록했다.

진단: `.work/integration/internal-loopback-diagnostic.json`. 최종 이미지 빌드·시험: `.work/integration/result-7fe3d3f2356940859de50f2394d03b2e.json`. [Moby에 보고된 유사 현상](https://github.com/moby/moby/discussions/53256)이 있지만 수정된 특정 Docker 버전은 확인하지 않았다. 임의 다운그레이드·방화벽 변경·일반 bridge 전환은 하지 않는다. 내부망과 루프백 publish가 함께 동작하는 승인된 로컬 Docker 환경에서 같은 시험을 재개해야 한다.

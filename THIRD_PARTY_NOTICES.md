# Third-party sources and notices

확인일: 2026-09-29. 이 저장소는 새로 작성한 프로그램·샘플·템플릿 소스를 제공한다. Docker Desktop, Docker CLI/Engine, pack, Paketo 이미지, Terraform CLI/provider 바이너리를 프로그램 배포물에 묶어 재배포하지 않는다. 개발 환경의 `.tools`, `.venv`, `.work`와 이미지 다운로드는 로컬 사용 준비용이며 Git/일반 export에서 제외한다.

| 구성요소 | 공식 출처와 확인한 라이선스/약관 |
|---|---|
| Cloud Native Buildpacks `pack` | 프로젝트 LICENSE는 Apache License 2.0. [pack LICENSE](https://github.com/buildpacks/pack/blob/main/LICENSE), [공식 releases](https://github.com/buildpacks/pack/releases) |
| Paketo builder-jammy-base | builder 소스 LICENSE는 Apache License 2.0. [builder LICENSE](https://github.com/paketo-buildpacks/builder-jammy-base/blob/main/LICENSE), [공식 Java 사용법](https://paketo.io/docs/howto/java/) |
| Paketo Java buildpack | Java buildpack 소스 LICENSE는 Apache License 2.0. [Java buildpack LICENSE](https://github.com/paketo-buildpacks/java/blob/main/LICENSE) |
| Docker CLI | CLI 소스 LICENSE는 Apache License 2.0. [Docker CLI LICENSE](https://github.com/docker/cli/blob/master/LICENSE) |
| Moby / Docker Engine 기반 구성요소 | Moby 소스 LICENSE는 Apache License 2.0. 개별 포함 구성요소의 별도 조건도 확인해야 한다. [Moby LICENSE](https://github.com/moby/moby/blob/master/LICENSE) |
| Docker Desktop | Docker Subscription Service Agreement와 포함 OSS 구성요소의 각각의 조건이 적용된다. Desktop 전체를 Apache 2.0으로 간주하지 않는다. [Docker Desktop license 안내](https://docs.docker.com/subscription-billing/desktop-license/) |

위 내용은 출처 식별이며 이 프로젝트가 제3자 제품의 사용권이나 재배포 권한을 부여하지 않는다. Apache-2.0 구성요소를 별도로 재배포하게 되면 해당 배포 버전의 LICENSE와 필요한 NOTICE/저작권 고지 등을 보존하고 실제 배포 형태의 조건을 확인해야 한다. Desktop의 적용 약관은 사용자가 자신의 이용 형태에 맞게 공식 문서에서 확인한다.

builder/run/base 컨테이너에는 운영체제 패키지·JVM·Python·기타 의존성이 포함될 수 있다. builder 저장소의 단일 LICENSE만으로 컨테이너 모든 layer의 라이선스를 설명할 수 없다. 배포하는 앱 이미지/tar의 코드·의존성·base layer별 고지와 배포 권한은 별도로 검토해야 한다. 이미지 tar 전달은 앱 코드/설정 전달도 포함할 수 있다.

Python/Streamlit/Pydantic/pytest/Ruff 및 개발 검증용 Terraform/provider의 실제 설치 버전은 `requirements.lock.txt`, `docs/environment.md`와 검증 기록에서 확인한다. 설치된 배포본의 LICENSE/METADATA와 각 공급자의 공식 조건을 유지한다. 이 저장소는 이 구성요소의 바이너리나 전체 소스 사본을 vendoring하지 않는다.

프로그램·샘플 이름에 쓰인 AWS, Docker, Paketo, Terraform 등의 이름은 호환 대상 식별을 위한 것이며 해당 업체의 후원·인증을 뜻하지 않는다.

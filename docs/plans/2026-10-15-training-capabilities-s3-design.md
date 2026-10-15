# 학습 방식·모델 호환성·S3 연결 확장 설계

상태: 2026-10-15 사용자가 “진행해”로 승인. 별도 기능 브랜치에서 구현.
기준: Windows 0.2.3, LLaMA-Factory 0.9.5, 기존 개인 PC/배정 RunPod/공용 MLflow 구조.
기존 0.2.3의 GPU 검증은 이 확장 기능을 검증한 증거가 아니다.

## 요청과 완료 기준

사용자가 요청한 LLaMA-Factory 전체 학습 방식, 모델별 자동 호환 설정, S3 데이터 직접 연결을
설정 화면부터 데이터 검사·실행·결과 회수·기록 비교까지 연결한다.
단순히 선택지를 추가하거나 지원하지 않는 조합을 엔진에 그대로 전달하지 않는다.
가능한 설정과 실제 GPU 검증 완료 상태는 구분해 표시한다.

고정 엔진의 전체 학습 단계는 PT, SFT, RM, PPO, DPO, KTO이다.
DPO 세부 loss는 sigmoid(DPO), hinge, IPO, KTO-pair, ORPO, SimPO를 제공한다.
파라미터 갱신 방식은 Full, Freeze, LoRA, OFT이며 QLoRA는 LoRA + 양자화 조합으로 모델링한다.
LoRA 세부 옵션은 rank/alpha/dropout/target, rsLoRA, DoRA, LoRA+, PiSSA를 포함한다.
양자화는 현재 검증한 bitsandbytes 4/8비트를 우선 명시적으로 지원하고,
다른 엔진·양자화·분산 최적화 플러그인은 호환성/의존성을 추가 확인하기 전 활성화하지 않는다.
이는 모든 학습 단계를 제공하는 범위이며 upstream의 모든 임의 CLI 플래그를 허용한다는 뜻은 아니다.

## 선택 가능한 접근

1. 권장: 앱이 학습 단계·모델·데이터 형식의 구조화된 호환 규칙을 소유하고 S3에서 불변 사본을 등록한다.
   현재 명세/해시/복구 흐름을 확장할 수 있고 S3 인증은 PC에만 남는다. 데이터 크기만큼 PC 저장 공간이 필요하다.
2. 원격 GPU가 S3를 직접 읽는다. 대용량 전송 경로를 줄이지만 원격 인증 전달·권한·버전 일관성·중단 재개를
   별도로 설계해야 한다. 기존 작업 회수와 다른 보안/데이터 경로가 된다.
3. LLaMA-Factory WebUI를 그대로 제공하고 별도 S3 도구를 붙인다. upstream 옵션 접근은 빠르지만
   현재 Windows 설정·공용 기록·취소/복구 흐름과 UI가 이중화된다.

## 권장 구조: 학습 기능

config.py의 학습 단계를 별도 필드로 분리하고 기존 설정은 SFT/LoRA 기본값으로 호환한다.
기존 training_method=qlora 설정은 변환 계층을 두어 계속 읽을 수 있어야 한다.
새 옵션을 추가하면서 이전 명세 검증 해시와 실행 중인 작업의 의미를 변경하지 않는다.
이전 버전 작업은 당시 패키지로 끝내거나 결과만 회수하며 새 설정으로 조용히 재해석하지 않는다.

capabilities 모듈에 엔진 버전·모델 프로필·단계·갱신 방식·정밀도·양자화·데이터 형식·필수 모델을 둔다.
UI와 plan/submit/worker가 같은 규칙을 사용한다. UI 우회 제출도 서버 측에서 차단한다.
정상/조건부/지원 불가/확인 불가와 이유를 반환한다. 확인 불가를 지원 가능으로 추측하지 않는다.

모델 선택 시 고정 revision의 HF config와 승인된 아키텍처 프로필로 template, target modules,
최대 길이와 dtype 정보를 결정한다. GPU에서 실제 BF16 지원/메모리/패키지 준비도도 검사한다.
메모리 값은 추정과 실제 가용량으로 표시하며 실행 성공을 보장하는 값으로 사용하지 않는다.
신규 모델은 upstream 목록을 무조건 활성화하지 않고 architecture/template 및 현재 transformers 호환을
확인해 등록한다. 원격 임의 코드는 자동 실행하지 않는다. 멀티모달/미확인 아키텍처는 이유와 함께 차단한다.
모델 목록 확장과 모든 모델 실제 학습 검증은 별도 상태로 관리한다.

학습 화면은 업무 → 모델 → 학습 단계 → 갱신 방식 → 해당 방식의 옵션 순서로 구성한다.
모델/단계 변경 시 기존 실행 계획을 무효화하고 호환되지 않는 값은 사용자에게 설명한다.
PPO는 보상 모델이 필수이며 모델/어댑터 종류와 revision/검증된 파일 목록을 함께 기록한다.
LoRA/Full 보상 모델과 정책의 조합 제한도 upstream 규칙대로 검사한다.
OFT처럼 고정 엔진의 경로별 제약이 있는 경우 실제 loader/parser 검증을 통과하기 전 활성화하지 않는다.

## 데이터·실행·평가·복구

데이터 종류를 텍스트(PT), 지시/대화(SFT), 선호 쌍(RM/DPO 및 ORPO/SimPO),
선호 라벨(KTO), 정책 학습 프롬프트(PPO)로 구분한다.
같은 JSON 파일이라도 단계에 맞는 필드·타입·필수 응답·chosen/rejected·boolean kto_tag를 검증한다.
기존 Alpaca 및 messages 기반 ShareGPT 매핑을 보존하고 필요한 데이터 매핑을 명시적으로 기록한다.
각 종류의 작은 합성 예제와 형식 오류 안내를 제공한다.

training adapter는 단계별 YAML 및 데이터 descriptor를 만들고 worker는 실제 산출물을 검사한다.
LoRA/OFT 어댑터, Full/Freeze 단일·분할 safetensors 및 shard index, RM/PPO value head를 구분한다.
파일 존재만이 아니라 index가 참조하는 모든 shard의 회수/해시를 검증한다.
재개에는 각 방식의 가중치·TrainerState·optimizer/scheduler·RNG 등 필요한 상태를 검사한다.
PPO 등 재개 제약이 있으면 완전 복구를 지원한다고 표시하지 않는다.

생성 가능한 정책 모델은 생성 평가를 별도로 수행한다. RM은 보상 평가를 사용하며
RM 가중치를 일반 대화 모델처럼 생성하지 않는다. PT/선호 학습 지표도 이름과 의미를 분리한다.
비교 시 업무/검증 데이터/평가 방식에 더해 단계·지표 의미가 호환되는지 검사한다.
loss 감소를 다른 학습 단계의 품질 향상으로 비교하지 않는다.

## S3 직접 연결

연결 화면에 S3 endpoint, region, addressing style, access key/secret/session token을 추가한다.
AWS S3와 표준 S3 호환 endpoint를 대상으로 하며 NCP/MinIO 등은 각각 실연결 검증 여부를 표시한다.
인증값은 Windows Credential Manager에 저장하고 UI에는 저장 여부만 반환한다.
일반 설정에는 공개 endpoint/region과 프로필 식별자만 둔다.
SDK 오류/URL/응답에서 키·서명·세션 토큰을 로그나 MLflow에 남기지 않는다.
서명 URL은 작업 명세에 저장하지 않는다. TLS 검증은 기본 활성화한다.

앱의 데이터 화면에서 버킷을 지정하고 prefix/폴더 탐색, 페이지별 목록, 크기/수정 시각을 조회한다.
ListBucket 권한이 없으면 정확한 bucket/key 입력으로 HEAD/GetObject를 사용할 수 있도록 한다.
객체 선택 뒤 예상 크기와 PC 저장 공간을 확인하고 명시적 가져오기로 다운로드한다.
버전 지원 시 VersionId를 고정하고 그렇지 않으면 HEAD ETag와 조건부 GET If-Match로 변경을 감지한다.
ETag를 내용 해시라고 취급하지 않는다. 전송 바이트의 SHA256·길이를 별도로 계산한다.
임시 파일에 기록한 뒤 길이/형식/해시 검사를 완료해야 불변 데이터 레지스트리에 등록한다.
취소·실패·객체 변경 시 부분 파일을 등록하지 않는다. 객체 key를 로컬 경로로 그대로 사용하지 않는다.

명세에는 bucket/key/version/관측 ETag/실제 SHA256과 비밀정보 없는 소스 식별자를 보관한다.
목록/크기 조회만으로 데이터가 RunPod에 전송되지는 않는다. 가져오기는 S3→PC이며,
기존 학습 시작 동의는 PC→RunPod 전송으로 구분한다. GPU에 S3 키를 전달하지 않는다.
첫 확장은 JSON/JSONL 불변 파일 가져오기이며 대용량 무제한 스트리밍이나 자동 폴더 전체 업로드가 아니다.
크기 제한은 화면에 명시하고 현재 전체 파일 파서의 메모리 한계를 숨기지 않는다.

## 검증과 배포

회귀 테스트를 먼저 작성하고 실패를 확인한 후 구현한다.
단계×갱신 방식의 YAML·지원/차단 규칙, 데이터 종류 오류, PPO 보상 모델 누락,
Full/Freeze/OFT shard·value head·체크포인트 검사를 검증한다.
S3 SDK Stubber/로컬 S3 호환 서버로 페이지 목록, version/If-Match, 변경된 객체,
접근 거부, 세션 토큰, 취소, 크기 초과, 해시와 부분 파일 정리를 검증한다.
실제 키를 포함한 설정·명세·로그 누출 회귀 검사도 포함한다.
UI는 모델 변경, 비호환 옵션, 단계별 데이터/옵션 노출, S3 탐색/가져오기/오류를 검증한다.

Python 3.12 잠금 설치, pytest, Ruff, UI, Rust, 패키지 무결성 및 Windows 설치를 수행한다.
CPU/모의 검사와 실제 GPU/실 S3 검증은 분리해 docs/PROGRESS.md에 기록한다.
실제 S3 검증에는 사용자가 지정한 읽기 권한과 비민감 객체가 필요하다.
새 유료 GPU 생성/데이터 전송은 별도 승인된 범위에서만 수행한다.
기존 사용자 Pod는 자동 정지/삭제하지 않는다.
0.2.3 릴리스를 덮어쓰지 않고 새 버전으로 검토·배포한다.

## 조사 근거

- [고정 엔진 단계·갱신 방식·선호 loss·PPO 조건](https://github.com/hiyouga/LLaMA-Factory/blob/v0.9.5/src/llamafactory/hparams/finetuning_args.py)
- [고정 엔진 상호 제약 검사](https://github.com/hiyouga/LLaMA-Factory/blob/v0.9.5/src/llamafactory/hparams/parser.py)
- [PT/선호/KTO 데이터 규격](https://github.com/hiyouga/LLaMA-Factory/blob/v0.9.5/data/README.md)
- [S3 GetObject 버전·조건부 읽기](https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetObject.html)
- [S3 페이지 목록](https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html)

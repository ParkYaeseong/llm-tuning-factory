# 학습 방식과 S3 연결 · Windows 0.3.0

앱은 개인 PC에서 설정·작업을 관리하고, 학습은 배정된 RunPod에서 실행하며 공용 MLflow에 기록합니다.
기본 모델 15개 외에 Hugging Face에서 모델을 검색하거나 저장소 ID로 추가할 수 있습니다.
목록에 있다는 것은 모든 조합을 GPU에서 검증했다는 뜻이 아닙니다.

## 최신 모델 추가

1. **학습 설정 → Hugging Face 모델 추가**에서 이름을 검색합니다. 다운로드 순 또는 최신 업데이트 순으로
   조회하며, 정확한 ID를 알면 `Qwen/Qwen3.5-0.8B`처럼 직접 입력하고 **입력한 ID 확인**을 누릅니다.
2. 구조·템플릿·고정 commit과 지원 여부를 확인하고 **이 모델 추가·선택**을 누릅니다.
   공개 메타데이터만 내려받으며 모델 가중치는 받지 않습니다. 추가한 모델은 다음 실행에도 목록에 남습니다.
3. 모델 구조별로 가능한 단계와 갱신 방식만 선택합니다. 설정에는 모델/토크나이저 commit과 config SHA256이
   묶여 저장되며, GPU에서도 실제 config와 아키텍처가 일치하는지 다시 검사합니다.
4. 비공개·접근 제한 메타데이터는 **연결 설정 → Hugging Face 접근**에 읽기 토큰을 저장합니다.
   Windows 자격 증명 저장소에 보관하며 **GPU에는 전달하지 않습니다**. 제한 모델 가중치는 GPU의 접근 권한과
   사전 캐시가 별도로 필요합니다. 공개 모델 검색에는 토큰이 필요하지 않습니다.

Qwen3·Qwen3.5·Qwen3.6, Gemma3/4 등 현재 엔진에서 인식하는 구조를 검사합니다.
Qwen3.5/3.6와 Gemma 멀티모달 구조는 이번 앱에서 **텍스트 SFT 범위만 우선 제공**합니다.
이는 앱의 보수적인 제공 범위이며 upstream 전체 학습 방식의 제한이라는 뜻은 아닙니다.
Qwen3의 선택 템플릿은 구형 PPO 전용 환경에 없으므로 PPO를 차단합니다.
이미지·오디오·영상 데이터는 등록 단계에서 거절합니다.
모르는 아키텍처, 임의 원격 코드가 필요한 모델, GGUF/어댑터만 있는 저장소, 사전 양자화된 추론용 가중치는
검색되더라도 학습 모델로 자동 허용하지 않고 이유를 표시합니다. 이런 모델은 엔진/프로필 확장이 필요합니다.

실제 공개 HF에서 Qwen3.5·Qwen3.6·Gemma4 검색과 Qwen3.5 메타데이터 확인,
Transformers 5.6.0의 Qwen3.5/3.6 config 로딩까지 확인했습니다. 가중치 다운로드·최신 모델 GPU 학습은 미검증입니다.
지원 규칙은 고정한 [LLaMA-Factory 템플릿](https://github.com/hiyouga/LLaMA-Factory/blob/v0.9.5/src/llamafactory/data/template.py)과
[HF API](https://huggingface.co/docs/hub/en/api)를 사용합니다.

## 학습 설정

**학습 설정**에서 모델 → 학습 단계 → 갱신 방식 순서로 선택합니다.
모델 변경 시 템플릿을 적용하고 이전 revision을 비웁니다. **선택한 모델 버전 확인·고정**은
고정 commit의 실제 아키텍처도 확인합니다. **호환성 확인**과 실행 계획에서 동일 규칙으로 검사하며,
설정 변경 시 이전 실행 계획은 무효가 됩니다. 확인되지 않은 아키텍처나 임의 원격 코드는 자동 허용하지 않습니다.

| 학습 단계 | 필요한 데이터 종류 | 데이터 예시 |
|---|---|---|
| PT: 추가 사전학습 | text | `{"text":"학습할 문서"}` |
| SFT: 지도학습 | instruction | `{"instruction":"질문","output":"답변"}` 또는 messages 대화 |
| RM: 보상 모델 | preference | `{"instruction":"질문","chosen":"좋은 답","rejected":"나쁜 답"}` |
| DPO / ORPO / SimPO | preference | 같은 질문의 chosen/rejected 쌍 |
| KTO | kto | `{"instruction":"질문","output":"답변","kto_tag":true}` |
| PPO | prompt | `{"instruction":"질문"}`. 별도로 준비한 보상 모델 필요 |

JSON 배열 또는 JSONL을 사용합니다. 등록 화면에서 데이터 종류를 지정합니다.
기존 데이터 버전은 instruction으로 읽으며, 다른 종류로 사용하려면 별도 버전으로 등록합니다.
KTO 라벨은 문자열 `"true"`가 아닌 boolean이어야 합니다.
ShareGPT는 `messages/role/content`와 `conversations/from/value`를 지원합니다.
사용자·응답 역할이 번갈아야 하며 선택적 input/system/tools 열은 모든 행에 일관되게 있어야 합니다.
PPO는 현재 Alpaca 프롬프트만 지원합니다. 단일 user 메시지의 ShareGPT는 고정 엔진에서 버려지므로 등록을 차단합니다.

갱신 방식은 **Full·Freeze·LoRA·QLoRA·OFT**입니다. QLoRA는 bitsandbytes 4/8비트를 지원합니다.
LoRA의 rank/alpha/dropout, rsLoRA·DoRA·LoRA+·PiSSA, Freeze의 레이어/모듈,
OFT의 rank 또는 block size를 조절할 수 있습니다. PiSSA와 일부 선호학습/양자화 조합 등은 차단합니다.
DPO에서 sigmoid·hinge·IPO·KTO-pair·ORPO·SimPO loss와 관련 가중치를 선택합니다.
이는 고정 엔진의 학습 단계·갱신 방식을 제공하는 범위이며 모든 플러그인과 임의 CLI 옵션을 제공하는 것은 아닙니다.

## 평가와 복구의 차이

- 생성 가능한 모델은 별도 instruction 검증 데이터로 생성 평가합니다. Full/Freeze는 학습한 전체 모델을 로드합니다.
- RM은 preference 검증 데이터를 선택하고 reward 평가를 사용합니다. 원생 eval_accuracy와 eval_loss를 기록합니다.
  품질 기준은 최소 eval_accuracy(0~1)를 사용합니다. 보상 모델을 대화 모델처럼 생성하지 않습니다.
- OFT의 별도 생성·보상 평가와 재개는 고정 엔진의 adapter loader 제약 때문에 차단합니다.
- PPO 재개와 Full/Freeze RM 재개는 upstream에서 지원하지 않아 차단합니다.
- 다른 단계의 train_loss를 직접 비교하지 않습니다. 공용 기록의 설정 차이는 계속 확인할 수 있습니다.

Full/Freeze는 전체 가중치를 회수하므로 GPU 메모리·PC 저장 용량·회수 시간이 크게 늘어납니다.
단일 파일뿐 아니라 shard index가 참조하는 모든 파일과 RM/PPO value head도 검사합니다.
해시는 큰 모델 파일을 통째로 메모리에 올리지 않고 계산합니다.

## PPO 전용 환경

주 환경은 LLaMA-Factory 0.9.5입니다. 이 버전은 전체 의존성 검사와 PPO가 요구하는 TRL 버전이
충돌하므로 PPO에는 **LLaMA-Factory 0.9.3 / TRL 0.9.6** 전용 잠금 환경을 사용합니다.
버전 검사를 끄거나 기존 GPU 환경을 덮어쓰지 않습니다.
PPO를 선택한 후 연결 설정에서 해당 단계의 **학습 환경 준비**를 별도로 수행합니다.
실행 시 작업의 단계로 환경을 선택하므로 화면의 현재 선택이 다른 작업의 실행 환경을 바꾸지 않습니다.
PPO에서는 OFT를 제공하지 않습니다.

보상 모델은 관리자가 GPU 저장 볼륨에 미리 준비해야 합니다. 경로·종류·파일 목록 해시를 입력합니다.
파일 목록 해시는 모든 일반 파일의 `상대 경로 → SHA256` 사전을 정렬된 canonical JSON으로 만든 뒤
그 JSON의 SHA256을 계산한 값입니다. 심볼릭 링크는 허용하지 않습니다.
LoRA 보상 어댑터의 기반 모델이 정책과 일치해야 하며, Full 보상 모델은 실제 토크나이저 어휘와
특수 토큰 ID도 정책과 같아야 합니다. Full 보상 폴더에는 토크나이저 파일도 필요합니다.
앱이 보상 모델을 임의로 생성·다운로드·전송하지는 않습니다.

관리자가 같은 작업 패키지가 설치된 환경에서 해시를 확인하는 예:

```python
from pathlib import Path
from tuning_factory.registry import canonical, digest, file_digest

folder = Path('/workspace/prepared-reward')
inventory = {p.relative_to(folder).as_posix(): file_digest(p)
             for p in sorted(folder.rglob('*')) if p.is_file() and not p.is_symlink()}
print(digest(canonical(inventory)))
```

## S3 데이터 직접 연결

1. **연결 설정 → S3 연결**에서 endpoint·region·접근 방식과 access key/secret key를 저장합니다.
   AWS 기본 endpoint는 비워 둘 수 있습니다. 임시 인증이면 session token도 입력합니다.
   AWS/NCP/MinIO 등 표준 S3 호환 서비스를 대상으로 하며 각 서비스 실계정 검증 상태는 별도입니다.
2. endpoint에는 `https://호스트`만 넣습니다. 파일 경로·서명 URL·ID/비밀번호·query는 넣지 않습니다.
   개인키처럼 S3 인증값도 Windows 자격 증명 저장소에 보관합니다. 일반 설정과 학습 명세에는 저장하지 않습니다.
3. **데이터 → S3**에서 버킷과 prefix를 입력해 파일을 조회합니다. 목록 권한이 없으면 정확한 key를 입력하고
   **객체 확인**을 사용할 수 있습니다. 버전이 필요하면 Version ID를 지정합니다.
4. 객체의 크기·버전·로컬 여유 공간을 확인하고 데이터 ID·버전·종류를 지정한 뒤 **가져오기**에 동의합니다.
   첫 버전의 가져오기 한도는 **파일당 32 MiB**입니다. 버킷 전체를 자동으로 받지 않습니다.
5. 앱이 고정 VersionId 또는 ETag 조건으로 다운로드하고 수신 길이·제공된 체크섬·SHA256·형식을 검사합니다.
   중간 실패나 객체 변경 시 부분 파일을 등록하지 않습니다. ETag를 내용 해시로 취급하지 않습니다.
6. 정상 파일을 PC의 불변 데이터로 등록합니다. S3→PC 가져오기와 PC→RunPod 전송 동의는 별개입니다.
   가져오기만으로 GPU 작업이 실행되지 않으며, S3 키를 GPU에 보내지 않습니다.

필요한 읽기 권한은 목록의 `s3:ListBucket`, 객체의 `s3:GetObject`, 특정 버전의 `s3:GetObjectVersion`입니다.
기관의 암호화/키 정책에 따라 추가 읽기 권한이 필요할 수 있습니다. 업로드·삭제 권한은 사용하지 않습니다.
데이터 소스의 bucket/key/version과 내용 해시는 기록에 포함될 수 있으므로 공유 가능한 이름을 사용합니다.
대량 스트리밍·GPU의 직접 S3 인증·폴더 전체 동기화는 이번 구현에 포함하지 않습니다.

## 검증 범위

이번 확장은 CPU 계약·데이터·호환성·복구 검사, 로컬 S3 프로토콜 시험과 Windows UI/설치 검증 대상입니다.
PPO는 격리된 실제 패키지 환경에서 CPU import/API/config 생성까지 확인했습니다.
**새 학습 조합의 실제 GPU 시험과 AWS/NCP/MinIO 실계정 시험은 아직 수행하지 않았습니다.**
0.2.3의 H100 SFT LoRA/4비트 QLoRA 검증을 새 조합의 성공 증거로 사용하지 않습니다.
정확한 최신 검사 수와 패키지 결과는 [진행 기록](PROGRESS.md)에 기록합니다.

## 지원 GPU와 메모리 추정

두 학습 환경(주 환경·PPO 환경)은 PyTorch 2.7.1 CUDA 12.8 빌드를 씁니다. 지원 세대와 드라이버 조건은
`runtime_profiles.runtime_capabilities()`에 있고, 실제 torch 패키지에서 읽은 값입니다(`scripts/inspect_gpu_wheels.py`).
학습 환경 검사는 설치된 환경에서 GPU 세대 지원과 실제 CUDA 연산 한 번을 확인합니다.

앱 기본 목록 모델과, 크기 정보가 있는 Hugging Face 등록 모델은 매개변수 수·`hidden_size`·층 수로 단일 GPU 학습 메모리를 추정합니다.
크기 정보가 없는 모델은 "메모리 추정 불가" 주의가 나옵니다. 가중치만으로 GPU 메모리의 95%를
넘으면 실행을 거부하고, 전체 추정이 90%를 넘으면 경고합니다. 예: Llama 3.1 70B bf16 LoRA(가중치 약 130GiB)는 80GB GPU에서
거부되고, 4비트 QLoRA(전체 약 50GiB)는 80GB GPU에서 허용됩니다. 여러 GPU를 쓰는 분산 학습은 지원하지 않습니다.

## 비전·오디오 겸용 모델(Qwen3.5·Gemma 4)

Qwen3.5와 Gemma 4는 텍스트·비전·오디오를 한 체크포인트에 담은 복합 구조입니다. 이 앱은 텍스트 데이터로만 SFT·DPO·RM·PT를 학습합니다
(KTO는 현재 학습 엔진에서 실패해, PPO는 전용 학습 환경이 이 모델들을 몰라 지원하지 않습니다).

- **동결 키**: 복합 구조에는 학습 설정에 `freeze_vision_tower: true`, `freeze_multi_modal_projector: true`,
  `freeze_language_model: false` 세 키가 들어가 비전·오디오 부분이 항상 동결됩니다. 다른 구조에는 이 키가 없습니다.
- **Qwen3.5 템플릿**: 기본값은 `qwen3_5`입니다. `enable_thinking: false`일 때 이 템플릿은 빈 사고 블록(`<think></think>(빈 줄 포함)`)을 프롬프트에 붙이므로(손실 없음) 학습 뒤 모델이 빈 사고 블록을 스스로 생성하지 않습니다. `qwen3_5_nothink`는 저장된 설정에서만 허용됩니다.
- **추론 템플릿**: 템플릿이 qwen3_5, qwen3_6, gemma4, gemma4n이면 학습·예측·보상 평가 설정에 `enable_thinking: false`가 더해집니다.
  허브에 등록된 `gemma4_text` 작업도 이 영향을 받습니다.
- **processor 파일**: `preprocessor_config.json`, `video_preprocessor_config.json`, `processor_config.json`, `chat_template.json`은
  있으면 기본 모델과 함께 받고 학습 결과·병합·확정 결과로 그대로 복사합니다. 토크나이저 지문에는 들어가지 않으므로 기존 지문은
  바뀌지 않으며(산출물 목록 해시에는 다른 파일처럼 들어갑니다), 없어도 오류가 아닙니다. Qwen3.5 샤드 이름
  `model.safetensors-0000N-of-0000M.safetensors`도 내려받습니다.
- **LoRA 대상**: `model.language_model.` 아래 Linear 모듈의 전체 이름입니다. 대상이 20개 이상이면 PEFT가 공통 접미사로 줄여
  기록할 수 있고, 앱은 그 경우도 같은 대상으로 확인합니다.
- **병합·확정**: 복합 구조는 이미지-텍스트 클래스로 불러와 비전 가중치를 그대로 보존합니다. transformers가 불러오지 않는
  체크포인트 텐서(Qwen3.5의 `mtp.*` 등)는 결과에 없습니다.
- **병합·확정의 수치 검사(정책 `model-ops-f32-v2`)**: 모든 모델 구조에 같습니다. FP32로 계산해 F32로 저장하고, 대상 가중치의 델타는
  원소별로(atol 1e-6, rtol 1e-5), 다시 불러온 모델의 로짓은 두 조건으로 확인합니다. 상대 L2 오차가 1e-5 이하이고, 최대 절대 오차가
  1e-4 × max(1, 기대 로짓의 최대 절댓값) 이하여야 합니다. 이전 정책 `model-ops-f32-v1`(로짓도 원소별 allclose, atol 1e-5, rtol 1e-4)은
  올바른 Qwen3.5-0.8B 확정의 FP32 반올림 오차(최대 2.24e-5, 0 근처 로짓)도 거부해서 바꿨고, 이전 영수증을 읽는 데만 남습니다.
  새 작업 결과는 v2 영수증만 받습니다.
- **메모리 추정**: 가중치는 비전·오디오를 포함한 전체 매개변수, 학습 상태는 텍스트 매개변수로 계산하며, QLoRA는 임베딩 표
  (Gemma 4 E 모델의 층별 임베딩 포함)를 bf16으로 셉니다. 어휘가 큰 이 모델들(Qwen3.5 248,320, Gemma 4 262,144)은 LM head
  로짓도 더합니다: 배치 × 최대 길이(cutoff_len) × 어휘 × 11바이트, DPO·KTO는 3배, full 학습은 2배입니다. 기본 목록의 다른
  모델과 어휘 정보가 없는 허브 모델의 추정은 그대로입니다.
  - 보정 자료(A40, 0.6.0 GPU 인수, 합성 데이터, 배치 1, 최대 길이 1024, 기울기 누적 4, gradient checkpointing; SFT·DPO 행은 약
    60–100토큰으로 짧고 PT만 최대 길이까지 채움). nvidia-smi 최대치 대 추정(GiB, 보정 전 → 보정 후):
    Qwen3.5-0.8B SFT LoRA 3.03 대 3.32 → 5.92, QLoRA 2.23 대 2.48 → 5.09, DPO 5.15 대 3.32 → 11.13, PT 5.55 대 3.32 → 5.92,
    full 16.09 대 13.02 → 18.23, freeze 3.07 대 4.02 → 6.63; gemma-4-E2B-it SFT LoRA 12.46 대 11.89 → 14.64,
    DPO 18.27 대 11.89 → 20.14, PT 14.48 대 11.89 → 14.64; Qwen3.5-9B SFT LoRA 19.36 대 21.20 → 23.80.
  - 규칙: 모든 실측이 추정 이하이고, SFT LoRA 추정은 실측의 2배 이하입니다. 11바이트(bf16 로짓, 손실용 fp32 사본과 그
    기울기)는 Gemma 4 E2B PT가 요구하는 10.4 이상, Qwen3.5-0.8B SFT LoRA 2배 한도의 11.6 이하에서 골랐습니다. DPO·KTO는
    chosen·rejected 두 벌에 실측상 한 벌이 더 있어(기준 모델 패스로 보임) 3배(2배로는 Gemma 4 E2B DPO가 모자람), full은 실측 초과분을 덮도록 2배입니다
    (full 초과분은 로짓보다 fp32 가중치·옵티마이저 임시 버퍼로 보입니다). 최대 길이까지 채운 긴 DPO·full 행은 재지 않았습니다.

from pydantic import BaseModel, Field

from app.providers.langchain_provider import generate_structured


SERVICE_EXTRACTION_CONFIDENCE_THRESHOLD = 0.75


class ExtractedService(BaseModel):
    name: str = Field(
        ...,
        description="예약 가능한 서비스명. 예: 화장실 청소, 베란다 청소",
    )
    description: str | None = Field(
        default=None,
        description="서비스 설명. 고객에게 보여줄 수 있는 간단한 설명",
    )
    price: int | None = Field(
        default=None,
        description="가격. 원화 기준 숫자만 사용. 예: 30000",
    )
    duration_minutes: int | None = Field(
        default=None,
        description="소요 시간. 분 단위 숫자만 사용. 예: 60",
    )
    should_create_service: bool = Field(
        ...,
        description="services 테이블에 후보로 등록할 만한 독립 예약 서비스인지 여부",
    )
    confidence: float = Field(
        ...,
        ge=0,
        le=1,
        description="서비스 후보 판단 확신도. 0부터 1 사이",
    )
    reason: str | None = Field(
        default=None,
        description="왜 서비스 후보로 판단했는지 또는 제외했는지 이유",
    )


class ServiceExtractionResult(BaseModel):
    services: list[ExtractedService] = Field(
        default_factory=list,
        description="지식 문서에서 추출한 예약 서비스 후보 목록",
    )


SERVICE_EXTRACTION_INSTRUCTIONS = """
너는 지식 문서에서 '예약 가능한 서비스/상품 후보'만 추출하는 역할이다.

목표:
- 사장님이 등록한 지식 문서에서 customers가 실제로 선택하거나 예약할 수 있는 항목을 찾는다.
- 특정 업종에 한정하지 않는다.
- 청소업체, 미용실, 병원/클리닉, 수리업체, 학원, 상담업, 렌탈업 등 다양한 업종에서 사용할 수 있어야 한다.
- 바로 확정 상품으로 등록하는 것이 아니라 관리자 승인 전 pending 후보로 등록할 항목만 추출한다.

서비스 후보로 추출할 수 있는 기준:
1. 고객이 독립적으로 예약/신청/선택할 수 있어 보이는 항목이다.
2. 문서 제목이나 본문이 특정 서비스, 시술, 진료, 상담, 수리, 수업, 상품 하나를 설명한다.
3. 가격, 소요시간, 포함 항목, 진행 방식, 작업 범위 중 하나 이상이 설명되어 있다.
4. 본문에 "서비스입니다", "예약 가능합니다", "진행됩니다", "포함됩니다", "제공합니다", "시술입니다", "진료입니다" 같은 표현이 있다.
5. 업종별 예시는 다음과 같다.
   - 청소업체: 화장실 청소, 베란다 청소, 입주 청소
   - 미용실: 남성 커트, 여성 커트, 염색, 펌, 두피 케어
   - 병원/클리닉: 초진 상담, 피부 진료, 예방 접종, 도수 치료
   - 수리업체: 세탁기 수리, 에어컨 점검, 출장 수리
   - 학원/상담업: 체험 수업, 1:1 상담, 입시 상담

서비스 후보로 추출하면 안 되는 기준:
1. 영업시간 안내
2. 예약 방법 안내
3. 환불 정책
4. 노쇼 정책
5. 주차 안내
6. 위치 안내
7. 준비물 안내
8. 주의사항
9. 상담사 연결 방법
10. 독립 예약 상품이 아니라 단순 포함 항목으로만 언급된 내용
11. 가격표 전체, FAQ 전체처럼 여러 정보를 섞은 일반 안내 문서에서 독립 서비스 여부가 불명확한 항목

판단 규칙:
- 독립 예약 가능성이 높으면 should_create_service=true.
- 애매하거나 단순 포함 항목이면 should_create_service=false.
- 가격은 원화 숫자만 추출한다. 예: "30,000원" -> 30000.
- 소요 시간은 분 단위 숫자로 변환한다. 예: "약 1시간" -> 60, "1시간 30분" -> 90.
- 가격이나 소요 시간이 문서에 없으면 null로 둔다.
- 가격이나 소요 시간이 없어도 독립 예약 가능한 서비스라면 should_create_service=true로 판단할 수 있다.
- confidence는 0부터 1 사이로 작성한다.
- 실제 서비스 후보만 services 배열에 넣는다.
"""


def build_service_extraction_input(
    *,
    title: str | None,
    content: str,
) -> str:
    return f"""
지식 제목:
{title or ""}

지식 본문:
{content}
""".strip()


async def extract_services_from_knowledge_text(
    *,
    organization_id: str,
    title: str | None,
    content: str,
    min_confidence: float = SERVICE_EXTRACTION_CONFIDENCE_THRESHOLD,
) -> list[dict]:
    """
    지식 제목/본문을 기반으로 예약 가능한 서비스 후보를 추출한다.

    반환값은 아직 DB에 저장하지 않는다.
    다음 단계에서 services 테이블에 approval_status='pending'으로 저장한다.
    """
    if not content.strip():
        return []

    result = await generate_structured(
        organization_id=organization_id,
        instructions=SERVICE_EXTRACTION_INSTRUCTIONS,
        user_message=build_service_extraction_input(
            title=title,
            content=content,
        ),
        schema=ServiceExtractionResult,
    )

    candidates: list[dict] = []

    for service in result.services:
        if not service.should_create_service:
            continue

        if service.confidence < min_confidence:
            continue

        candidates.append(
            {
                "name": service.name.strip(),
                "description": service.description,
                "price": service.price,
                "duration_minutes": service.duration_minutes,
                "confidence": service.confidence,
                "reason": service.reason,
                "raw_payload": service.model_dump(),
            }
        )

    return candidates
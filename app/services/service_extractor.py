from pydantic import BaseModel, Field

from app.providers.langchain_provider import generate_structured


SERVICE_EXTRACTION_CONFIDENCE_THRESHOLD = 0.75

class ExtractedServiceItemOption(BaseModel):
    option_group: str | None = Field(
        default=None,
        description="옵션 그룹. 예: 평형, 오염도, 추가 작업, 방문 조건",
    )
    option_value: str = Field(
        ...,
        description="옵션명. 예: 24평형, 베란다 확장형, 심한 오염 추가",
    )
    additional_price: int = Field(
        default=0,
        description="옵션 추가금. 원화 기준 숫자만 사용. 예: 30000",
    )
    additional_duration: int = Field(
        default=0,
        description="옵션 추가 소요 시간. 분 단위 숫자만 사용. 예: 20",
    )
    description: str | None = Field(
        default=None,
        description="옵션 설명",
    )


class ExtractedServiceItem(BaseModel):
    name: str = Field(
        ...,
        description="실제 예약 가능한 세부 서비스명. 예: 이사 청소, 화장실 청소, 베란다 청소",
    )
    description: str | None = Field(
        default=None,
        description="서비스 아이템 설명",
    )
    base_price: int = Field(
        default=0,
        description="서비스 아이템 기본 가격. 원화 기준 숫자만 사용",
    )
    duration_minutes: int = Field(
        default=0,
        description="서비스 아이템 기본 소요 시간. 분 단위 숫자만 사용",
    )
    options: list[ExtractedServiceItemOption] = Field(
        default_factory=list,
        description="이 서비스 아이템에서 선택 가능한 옵션 목록",
    )


class ExtractedService(BaseModel):
    name: str = Field(
        ...,
        description=(
            "서비스 카테고리 또는 대표 서비스명. "
            "예: 홈 클리닝, 가전 청소, 사업장 청소. "
            "문서에 카테고리가 없으면 대표 서비스명을 사용한다."
        ),
    )
    description: str | None = Field(
        default=None,
        description="서비스 설명. 고객에게 보여줄 수 있는 간단한 설명",
    )
    price: int | None = Field(
        default=None,
        description=(
            "대표 가격. 세부 아이템/옵션별 가격이 따로 있으면 0 또는 null로 둔다. "
            "원화 기준 숫자만 사용. 예: 30000"
        ),
    )
    duration_minutes: int | None = Field(
        default=None,
        description=(
            "대표 소요 시간. 세부 아이템별 시간이 따로 있으면 0 또는 null로 둔다. "
            "분 단위 숫자만 사용. 예: 60"
        ),
    )
    items: list[ExtractedServiceItem] = Field(
        default_factory=list,
        description=(
            "이 서비스 아래의 실제 예약 가능한 세부 서비스 아이템 목록. "
            "예: 이사 청소, 화장실 청소, 베란다 청소"
        ),
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
11. FAQ, 환불 정책, 위치 안내처럼 예약 가능한 서비스 구조가 없는 일반 안내 문서는 제외한다.
   단, 가격표 문서라도 서비스명, 가격, 소요시간, 옵션이 구조적으로 제시되어 있으면 서비스 후보로 추출한다.


판단 규칙:
- 독립 예약 가능성이 높으면 should_create_service=true.
서비스 계층 추출 규칙:
- services 배열의 각 항목은 상위 서비스 카테고리 또는 대표 서비스를 의미한다.
- 실제 고객이 예약할 수 있는 세부 상품은 items 배열에 넣는다.
- 예: "홈 클리닝"은 service.name, "이사 청소", "화장실 청소", "베란다 청소"는 items에 넣는다.
- 문서에 상위 카테고리가 없으면 가장 적절한 대표 서비스명을 service.name으로 사용한다.
- 평형, 오염도, 추가 작업, 방문 조건, 주말/야간 추가금처럼 선택에 따라 가격이나 시간이 달라지는 항목은 options 배열에 넣는다.
- 옵션의 추가금은 additional_price에 넣는다.
- 옵션의 추가 소요 시간은 additional_duration에 넣는다.
- 세부 아이템별 가격이 있으면 item.base_price에 넣는다.
- 세부 아이템별 기본 소요 시간이 있으면 item.duration_minutes에 넣는다.
- 가격이 옵션에 따라 결정되는 경우 service.price는 0 또는 null로 둔다.
- 소요 시간이 옵션에 따라 결정되는 경우 service.duration_minutes는 0 또는 null로 둔다.

출력 구조 예시:
{
  "services": [
    {
      "name": "홈 클리닝",
      "description": "가정 방문 청소 서비스",
      "price": 0,
      "duration_minutes": 0,
      "items": [
        {
          "name": "이사 청소",
          "description": "이사 전후 빈집 상태에서 진행하는 전체 청소",
          "base_price": 0,
          "duration_minutes": 180,
          "options": [
            {
              "option_group": "평형",
              "option_value": "24평형",
              "additional_price": 240000,
              "additional_duration": 120,
              "description": "24평 기준 이사 청소 옵션"
            },
            {
              "option_group": "추가 작업",
              "option_value": "베란다 확장형",
              "additional_price": 30000,
              "additional_duration": 20,
              "description": "베란다 확장 구조 추가 청소"
            }
          ]
        }
      ],
      "should_create_service": true,
      "confidence": 0.95,
      "reason": "서비스명, 세부 아이템, 가격, 소요 시간이 명확하게 제시되어 있음"
    }
  ]
}
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

        price = service.price
        duration_minutes = service.duration_minutes

        # services 테이블의 duration_minutes는 0을 허용하지 않을 수 있다.
        # 상위 카테고리처럼 시간이 없는 경우에는 0이 아니라 None으로 저장한다.
        if duration_minutes is not None and duration_minutes <= 0:
            duration_minutes = None

        # 가격도 실제 대표 가격이 없는 상위 카테고리라면 None으로 둔다.
        # 단, DB가 price=0을 허용하면 그대로 둬도 되지만, 카테고리 의미상 None이 더 안전하다.
        if price is not None and price <= 0:
            price = None

        candidates.append(
            {
                "name": service.name.strip(),
                "description": service.description,
                "price": price,
                "duration_minutes": duration_minutes,
                "confidence": service.confidence,
                "reason": service.reason,
                "raw_payload": service.model_dump(),
            }
        )

    return candidates
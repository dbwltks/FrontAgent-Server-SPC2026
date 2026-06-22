import unittest

from app.graph.prompt_builder import build_response_instructions


class PromptBuilderTests(unittest.TestCase):
    def test_general_response_does_not_include_rag_failure_instructions(self):
        prompt = build_response_instructions(
            intent="general",
            knowledge_context=[],
            use_knowledge=False,
            rules=[],
        )

        self.assertNotIn("[검색된 지식]", prompt)
        self.assertNotIn("필요한 정보를 찾지 못했다면", prompt)
        self.assertNotIn("담당자에게 문의", prompt)

    def test_organization_rule_is_included_once(self):
        instruction = "답변 마지막에 문의 안내를 추가한다."
        prompt = build_response_instructions(
            intent="general",
            knowledge_context=[],
            use_knowledge=False,
            rules=[{"name": "마무리 안내", "instruction": instruction}],
        )

        self.assertEqual(prompt.count(instruction), 1)

    def test_rag_instructions_are_added_only_for_knowledge_request(self):
        prompt = build_response_instructions(
            intent="pricing",
            knowledge_context=[],
            knowledge_context_groups=[
                {"query": "서비스 가격", "chunks": []},
            ],
            use_knowledge=True,
            rules=[],
        )

        self.assertIn("[검색된 지식]", prompt)
        self.assertIn("사용자 하위 질문: 서비스 가격", prompt)
        self.assertIn("검색 결과: 없음", prompt)
        self.assertIn("해당 정보를 확인하지 못했다고", prompt)


if __name__ == "__main__":
    unittest.main()

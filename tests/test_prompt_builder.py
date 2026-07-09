import unittest

from app.graph.prompt_builder import build_response_instructions


class PromptBuilderTests(unittest.TestCase):
    def test_general_response_does_not_include_rag_section(self):
        prompt = build_response_instructions(
            intent="general",
            knowledge_context=[],
            use_knowledge=False,
            rules=[],
        )
        self.assertNotIn("[검색된 지식]", prompt)
        self.assertNotIn("필요한 정보를 찾지 못했다면", prompt)

    def test_organization_rule_is_included_once(self):
        instruction = "답변 마지막에 문의 안내를 추가한다."
        prompt = build_response_instructions(
            intent="general",
            knowledge_context=[],
            use_knowledge=False,
            rules=[{"name": "마무리 안내", "instruction": instruction}],
        )
        self.assertEqual(prompt.count(instruction), 1)

    def test_rag_section_added_only_when_use_knowledge(self):
        prompt = build_response_instructions(
            intent="pricing",
            knowledge_context=[
                {"source_title": "서비스 안내", "content": "베란다 청소 50,000원"},
            ],
            use_knowledge=True,
            rules=[],
        )
        self.assertIn("[검색된 지식]", prompt)
        self.assertIn("베란다 청소 50,000원", prompt)
        self.assertIn("해당 정보를 확인하지 못했다고", prompt)

    def test_rag_not_included_when_use_knowledge_false(self):
        prompt = build_response_instructions(
            intent="pricing",
            knowledge_context=[
                {"source_title": "서비스 안내", "content": "베란다 청소 50,000원"},
            ],
            use_knowledge=False,
            rules=[],
        )
        self.assertNotIn("[검색된 지식]", prompt)

    def test_voice_prompt_uses_spoken_style(self):
        prompt = build_response_instructions(
            intent="pricing",
            knowledge_context=[],
            use_knowledge=False,
            rules=[],
            channel="web_call",
        )
        self.assertIn("실제 상담원처럼", prompt)
        self.assertIn("대화하듯 말한다", prompt)
        self.assertIn("핵심 답변, 중요한 조건, 다음 행동", prompt)
        self.assertIn("내부 구현 단어", prompt)
        self.assertNotIn("AI 상담사", prompt)


if __name__ == "__main__":
    unittest.main()

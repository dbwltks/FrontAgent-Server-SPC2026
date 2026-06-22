import unittest

from app.graph.nodes.decision_node import _normalize_knowledge_queries
from app.graph.nodes.knowledge_node import merge_unique_chunks, normalize_knowledge_queries


class KnowledgeQueryTests(unittest.TestCase):
    def test_simple_query_keeps_only_original_question(self):
        queries = _normalize_knowledge_queries(
            ["환불 정책 알려줘"],
            use_knowledge=True,
            user_message="환불 정책 알려줘",
        )

        self.assertEqual(queries, ["환불 정책 알려줘"])

    def test_compound_query_keeps_original_and_limits_generated_queries(self):
        original = "예약 취소 수수료와 환불 기간 알려줘"

        queries = normalize_knowledge_queries(
            original,
            ["예약 취소 수수료", "취소 후 환불 기간", "예약 변경 정책"],
        )

        self.assertEqual(queries, [original, "예약 취소 수수료", "취소 후 환불 기간"])

    def test_query_normalization_removes_whitespace_duplicates(self):
        queries = normalize_knowledge_queries(
            "  환불   정책  ",
            ["환불 정책", "환불 기간"],
        )

        self.assertEqual(queries, ["환불 정책", "환불 기간"])

    def test_merged_context_is_sorted_and_limited_to_six_chunks(self):
        groups = [
            {
                "chunks": [
                    {"id": str(index), "similarity": index / 10}
                    for index in range(1, 8)
                ]
            }
        ]

        merged = merge_unique_chunks(groups)

        self.assertEqual(len(merged), 6)
        self.assertEqual(
            [item["id"] for item in merged],
            ["7", "6", "5", "4", "3", "2"],
        )


if __name__ == "__main__":
    unittest.main()

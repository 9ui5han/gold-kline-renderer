import ast
import unittest
from pathlib import Path


SOURCE_PATH = Path(__file__).resolve().parents[1] / "app" / "main.py"


class MiniMaxSentenceSourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.functions = {
            node.name: node
            for node in cls.tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def test_sentence_pipeline_functions_exist(self):
        self.assertIn("parse_minimax_sentence_units", self.functions)
        self.assertIn("generate_minimax_tts_segment", self.functions)
        self.assertIn("generate_minimax_segmented_tts", self.functions)

    def test_current_official_minimax_contract_is_present(self):
        self.assertIn('"model": "speech-2.8-turbo"', self.source)
        self.assertIn('"language_boost": "English"', self.source)
        self.assertIn('data = result.get("data") or {}', self.source)
        self.assertIn('audio_url = str(data.get("audio") or "").strip()', self.source)

    def test_minimax_keeps_sentence_boundaries_only_as_alignment_fallback(self):
        self.assertIn("minimax_segment_bounds", self.source)
        self.assertIn('"minimax": minimax_segment_bounds', self.source)
        self.assertIn('alignment_method = "source_text_word_alignment"', self.source)
        self.assertIn('f"{payload.tts_provider}_segment_boundary_fallback"', self.source)


if __name__ == "__main__":
    unittest.main()

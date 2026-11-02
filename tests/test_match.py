import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import match


class MatcherTests(unittest.TestCase):
    def s(self, a, b):
        return match.score(a, b, None)

    def test_paraphrases_match(self):
        pairs = [
            ("已完成设计版报告生成并交付", "设计版报告已完成并交付"),
            ("需要sox工具支持", "Qwen TTS语音克隆需要sox工具支持"),
            ("PowerShell控制台为GBK编码，需输出UTF-8文件", "PowerShell 控制台为 GBK 编码，需要输出 UTF-8 文件"),
            ("喜欢二次元、摄影、AI、御姐", "偏好二次元、摄影、AI 和御姐风格"),
        ]
        for a, b in pairs:
            self.assertGreaterEqual(self.s(a, b), match.MATCH_THRESHOLD, (a, b, self.s(a, b)))

    def test_distinct_facts_do_not_match(self):
        pairs = [
            ("已完成设计版报告生成并交付", "微信人物画像不是商品，是可流通的玩法"),
            ("port 18060, 13 tools", "port 8080, 5 tools"),
            ("364", "12"),
        ]
        for a, b in pairs:
            self.assertLess(self.s(a, b), match.MATCH_THRESHOLD, (a, b, self.s(a, b)))

    def test_one_to_one_matching_prefers_highest_score(self):
        predicted = [{"text": "喜欢二次元、摄影"}, {"text": "port 18060, 13 tools"}]
        expected = [{"text": "偏好二次元、摄影风格"}, {"text": "port 18060 有 13 个工具"}]
        pairs, missed, extra = match.match(predicted, expected, use_embeddings=False)
        self.assertEqual(len(pairs), 2)
        self.assertEqual(missed, [])
        self.assertEqual(extra, [])


if __name__ == "__main__":
    unittest.main()

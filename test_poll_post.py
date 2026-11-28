import json
import unittest

import poll_post


class PollAttachmentTest(unittest.TestCase):
    def test_default_options_fit(self):
        got = json.loads(poll_post.poll_attachment(poll_post.DEFAULT_OPTIONS))
        self.assertEqual(list(got), ["option_a", "option_b", "option_c", "option_d"])
        self.assertEqual(got["option_d"], "실적·공시 정리")

    def test_too_few_or_many(self):
        with self.assertRaises(ValueError):
            poll_post.poll_attachment(["하나"])
        with self.assertRaises(ValueError):
            poll_post.poll_attachment(list("abcde"))

    def test_option_length(self):
        with self.assertRaises(ValueError):
            poll_post.poll_attachment(["가" * 26, "나"])
        with self.assertRaises(ValueError):
            poll_post.poll_attachment(["", "나"])

    def test_dry_run_posts_nothing(self):
        self.assertEqual(poll_post.main(["--dry-run"]), 0)


if __name__ == "__main__":
    unittest.main()

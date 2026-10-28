import unittest

from wake_words import compile_wake_pattern, extract_wake_command


class WakeWordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pattern = compile_wake_pattern(["Valya", "Валя"])

    def test_repeated_wake_words_are_all_removed(self) -> None:
        activated, command, matched = extract_wake_command(
            "Valya, please pick the blue mouse Valya, pick the blue mouse",
            self.pattern,
        )
        self.assertTrue(activated)
        self.assertEqual(matched, "Valya")
        self.assertEqual(command, "please pick the blue mouse, pick the blue mouse")

    def test_non_wake_text_is_not_a_command(self) -> None:
        activated, command, matched = extract_wake_command("pick the mouse", self.pattern)
        self.assertFalse(activated)
        self.assertEqual(command, "")
        self.assertIsNone(matched)


if __name__ == "__main__":
    unittest.main()

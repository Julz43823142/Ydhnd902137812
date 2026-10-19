import io
import unittest

from PIL import Image

from fairplay_sus_parody import build_sus_chart, sanitize_username


class SusParodyTests(unittest.TestCase):
    def test_sanitization(self):
        self.assertEqual(sanitize_username("Chess_Player"), "Chess_Player")
        for value in ("", "a b", "@someone", "a" * 26):
            with self.assertRaises(ValueError):
                sanitize_username(value)

    def test_graph_is_valid_png(self):
        data = build_sus_chart("TestPlayer")
        self.assertTrue(data.getvalue().startswith(bytes([137, 80, 78, 71])))
        image = Image.open(io.BytesIO(data.getvalue()))
        self.assertEqual(image.size, (1000, 540))


if __name__ == "__main__":
    unittest.main()

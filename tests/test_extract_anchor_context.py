import unittest
from api.services.assist_helpers import extract_anchor_context


class TestExtractAnchorContext(unittest.TestCase):

    def test_selected_text_found_exact_match(self):
        content = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        before, target, after, idx, replace = extract_anchor_context(
            content, "Second paragraph."
        )
        self.assertEqual(target, "Second paragraph.")
        self.assertEqual(before, "First paragraph.")
        self.assertEqual(after, "Third paragraph.")
        self.assertEqual(idx, 1)
        self.assertTrue(replace)

    def test_selected_text_not_found_uses_last(self):
        content = "First paragraph.\n\nSecond paragraph."
        before, target, after, idx, replace = extract_anchor_context(
            content, "Nonexistent text"
        )
        self.assertEqual(target, "Second paragraph.")
        self.assertEqual(before, "First paragraph.")
        self.assertEqual(after, "")
        self.assertEqual(idx, 1)
        self.assertTrue(replace)

    def test_selected_text_sub_paragraph(self):
        content = "This is a long paragraph with some text inside it.\n\nAnother paragraph."
        before, target, after, idx, replace = extract_anchor_context(
            content, "long paragraph"
        )
        self.assertEqual(target, "long paragraph")
        self.assertEqual(before, "This is a ")
        self.assertEqual(after, " with some text inside it.")
        self.assertEqual(idx, 0)
        self.assertTrue(replace)

    def test_selected_text_sub_paragraph_multiline(self):
        content = "First paragraph.\n\nThis is a long paragraph\nwith multiple lines in it.\n\nThird paragraph."
        before, target, after, idx, replace = extract_anchor_context(
            content, "long paragraph\nwith multiple lines"
        )
        self.assertEqual(target, "long paragraph\nwith multiple lines")
        self.assertEqual(before, "This is a ")
        self.assertEqual(after, " in it.")
        self.assertEqual(idx, 1)
        self.assertTrue(replace)

    def test_cursor_paragraph_text(self):
        content = "First.\n\nSecond.\n\nThird."
        before, target, after, idx, replace = extract_anchor_context(
            content, selected_text=None, cursor_paragraph_text="Second."
        )
        self.assertEqual(target, "Second.")
        self.assertEqual(before, "First.")
        self.assertEqual(after, "Third.")
        self.assertEqual(idx, 1)
        self.assertFalse(replace)

    def test_cursor_paragraph_text_truncated_match(self):
        content = "First.\n\nSecond paragraph with lots of text.\n\nThird."
        before, target, after, idx, replace = extract_anchor_context(
            content, selected_text=None, cursor_paragraph_text="Second paragraph"
        )
        self.assertEqual(target, "Second paragraph with lots of text.")
        self.assertEqual(idx, 1)
        self.assertFalse(replace)

    def test_no_selection_or_cursor(self):
        content = "First.\n\nSecond.\n\nThird."
        before, target, after, idx, replace = extract_anchor_context(content, selected_text=None)
        self.assertEqual(target, "Third.")
        self.assertEqual(before, "Second.")
        self.assertEqual(after, "")
        self.assertEqual(idx, 2)
        self.assertFalse(replace)

    def test_empty_content(self):
        before, target, after, idx, replace = extract_anchor_context("", selected_text=None)
        self.assertEqual(target, "")
        self.assertEqual(before, "")
        self.assertEqual(after, "")

    def test_single_paragraph(self):
        content = "Only one paragraph."
        before, target, after, idx, replace = extract_anchor_context(content, selected_text=None)
        self.assertEqual(target, "Only one paragraph.")
        self.assertEqual(before, "")
        self.assertEqual(after, "")
        self.assertEqual(idx, 0)
        self.assertFalse(replace)

    def test_selected_text_truncated_fallback(self):
        long_text = "x" * 100
        paragraph = "prefix " + long_text + " suffix"
        content = f"First.\n\n{paragraph}\n\nThird."
        before, target, after, idx, replace = extract_anchor_context(
            content, long_text
        )
        self.assertEqual(target, long_text)
        self.assertEqual(before, "prefix ")
        self.assertEqual(after, " suffix")

    def test_full_paragraph_selection_when_text_matches_paragraph(self):
        content = "Para one.\n\nPara two.\n\nPara three."
        before, target, after, idx, replace = extract_anchor_context(
            content, "Para two."
        )
        self.assertEqual(target, "Para two.")
        self.assertEqual(before, "Para one.")
        self.assertEqual(after, "Para three.")
        self.assertTrue(replace)


if __name__ == "__main__":
    unittest.main()

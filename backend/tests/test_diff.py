"""Tests for unified diff parsing.

The line numbers this produces decide which findings get posted, so an
off-by-one here becomes a review comment on the wrong line of someone's pull
request. These are worth being fussy about.
"""

from __future__ import annotations

from app.services.diff import ChangeType, changed_line_ranges, parse_unified_diff

SIMPLE_DIFF = """diff --git a/app/main.py b/app/main.py
index 1234567..89abcde 100644
--- a/app/main.py
+++ b/app/main.py
@@ -10,7 +10,9 @@ def handler():
     existing = 1
     another = 2
-    removed_line = 3
+    added_line = 3
+    second_added = 4
     trailing = 5
     final = 6
"""


class TestSingleFile:
    def test_finds_the_file(self) -> None:
        parsed = parse_unified_diff(SIMPLE_DIFF)

        assert [f.path for f in parsed.files] == ["app/main.py"]

    def test_counts_added_lines(self) -> None:
        parsed = parse_unified_diff(SIMPLE_DIFF)

        assert parsed.files[0].added_line_count == 2

    def test_numbers_added_lines_in_the_new_file(self) -> None:
        """The hunk starts at line 10. Two context lines precede the removal,
        which does not advance the new-file counter, so the additions land on
        12 and 13."""
        parsed = parse_unified_diff(SIMPLE_DIFF)

        assert parsed.files[0].added_lines == {12, 13}

    def test_counts_removed_lines(self) -> None:
        parsed = parse_unified_diff(SIMPLE_DIFF)

        assert parsed.files[0].removed_line_count == 1

    def test_treats_it_as_a_modification(self) -> None:
        parsed = parse_unified_diff(SIMPLE_DIFF)

        assert parsed.files[0].change_type is ChangeType.MODIFIED


class TestMetadataIsNotMistakenForContent:
    def test_the_plus_plus_plus_header_is_not_an_added_line(self) -> None:
        """`+++ b/file` starts with `+`. Counting it would shift every line
        number in the file by one."""
        parsed = parse_unified_diff(SIMPLE_DIFF)

        assert 10 not in parsed.files[0].added_lines
        assert parsed.files[0].added_line_count == 2

    def test_the_minus_minus_minus_header_is_not_a_removed_line(self) -> None:
        parsed = parse_unified_diff(SIMPLE_DIFF)

        assert parsed.files[0].removed_line_count == 1

    def test_a_no_newline_marker_is_ignored(self) -> None:
        diff = """diff --git a/a.txt b/a.txt
--- a/a.txt
+++ b/a.txt
@@ -1,2 +1,2 @@
 first
-second
\\ No newline at end of file
+second changed
\\ No newline at end of file
"""
        parsed = parse_unified_diff(diff)

        assert parsed.files[0].added_lines == {2}
        assert parsed.files[0].removed_line_count == 1


class TestChangeTypes:
    def test_detects_a_new_file(self) -> None:
        diff = """diff --git a/new.py b/new.py
new file mode 100644
index 0000000..1234567
--- /dev/null
+++ b/new.py
@@ -0,0 +1,3 @@
+first
+second
+third
"""
        parsed = parse_unified_diff(diff)

        assert parsed.files[0].change_type is ChangeType.ADDED
        assert parsed.files[0].added_lines == {1, 2, 3}

    def test_detects_a_deleted_file(self) -> None:
        diff = """diff --git a/gone.py b/gone.py
deleted file mode 100644
index 1234567..0000000
--- a/gone.py
+++ /dev/null
@@ -1,2 +0,0 @@
-first
-second
"""
        parsed = parse_unified_diff(diff)

        assert parsed.files[0].change_type is ChangeType.DELETED
        assert parsed.files[0].added_lines == set()
        assert parsed.files[0].removed_line_count == 2

    def test_a_deleted_file_is_not_reviewable(self) -> None:
        """There is no new source to comment on."""
        diff = """diff --git a/gone.py b/gone.py
deleted file mode 100644
--- a/gone.py
+++ /dev/null
@@ -1,1 +0,0 @@
-first
"""
        parsed = parse_unified_diff(diff)

        assert parsed.files[0].is_reviewable is False

    def test_detects_a_rename_and_reports_both_paths(self) -> None:
        diff = """diff --git a/old/name.py b/new/name.py
similarity index 95%
rename from old/name.py
rename to new/name.py
index 1234567..89abcde 100644
--- a/old/name.py
+++ b/new/name.py
@@ -1,3 +1,3 @@
 unchanged
-was
+now
"""
        parsed = parse_unified_diff(diff)

        assert parsed.files[0].change_type is ChangeType.RENAMED
        assert parsed.files[0].path == "new/name.py"
        assert parsed.files[0].previous_path == "old/name.py"

    def test_detects_a_binary_file(self) -> None:
        diff = """diff --git a/logo.png b/logo.png
index 1234567..89abcde 100644
Binary files a/logo.png and b/logo.png differ
"""
        parsed = parse_unified_diff(diff)

        assert parsed.files[0].is_binary is True
        assert parsed.files[0].is_reviewable is False


class TestMultipleFilesAndHunks:
    def test_separates_files(self) -> None:
        diff = """diff --git a/one.py b/one.py
--- a/one.py
+++ b/one.py
@@ -1,2 +1,3 @@
 keep
+added in one
diff --git a/two.py b/two.py
--- a/two.py
+++ b/two.py
@@ -5,2 +5,3 @@
 keep
+added in two
"""
        parsed = parse_unified_diff(diff)

        assert [f.path for f in parsed.files] == ["one.py", "two.py"]
        assert parsed.files[0].added_lines == {2}
        assert parsed.files[1].added_lines == {6}

    def test_handles_several_hunks_in_one_file(self) -> None:
        """Each hunk header resets the counter to its own start line."""
        diff = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1,3 +1,4 @@
 one
+early addition
 two
@@ -50,3 +51,4 @@
 fifty
+late addition
 fiftyone
"""
        parsed = parse_unified_diff(diff)

        assert parsed.files[0].added_lines == {2, 52}

    def test_totals_across_files(self) -> None:
        diff = """diff --git a/one.py b/one.py
--- a/one.py
+++ b/one.py
@@ -1,1 +1,2 @@
 keep
+a
diff --git a/two.py b/two.py
--- a/two.py
+++ b/two.py
@@ -1,1 +1,3 @@
 keep
+b
+c
"""
        parsed = parse_unified_diff(diff)

        assert parsed.total_added_lines == 3


class TestHunkHeaderVariants:
    def test_omitted_counts_default_to_one(self) -> None:
        """Git omits the count when it is 1, e.g. `@@ -1 +1 @@`."""
        diff = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1 +1 @@
-old
+new
"""
        parsed = parse_unified_diff(diff)

        assert parsed.files[0].added_lines == {1}

    def test_trailing_function_context_is_ignored(self) -> None:
        diff = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -10,2 +10,3 @@ class Thing:
 keep
+added
"""
        parsed = parse_unified_diff(diff)

        assert parsed.files[0].added_lines == {11}


class TestEdgeCases:
    def test_an_empty_diff_yields_no_files(self) -> None:
        assert parse_unified_diff("").files == []

    def test_text_before_the_first_file_header_is_ignored(self) -> None:
        parsed = parse_unified_diff("some preamble\n" + SIMPLE_DIFF)

        assert len(parsed.files) == 1

    def test_a_path_containing_spaces_survives(self) -> None:
        diff = """diff --git a/my folder/my file.py b/my folder/my file.py
--- a/my folder/my file.py
+++ b/my folder/my file.py
@@ -1,1 +1,2 @@
 keep
+added
"""
        parsed = parse_unified_diff(diff)

        assert parsed.files[0].path == "my folder/my file.py"

    def test_find_locates_a_file_by_path(self) -> None:
        parsed = parse_unified_diff(SIMPLE_DIFF)

        assert parsed.find("app/main.py") is not None
        assert parsed.find("absent.py") is None


class TestChangedLineRanges:
    def test_collapses_consecutive_lines(self) -> None:
        parsed = parse_unified_diff(SIMPLE_DIFF)

        assert changed_line_ranges(parsed.files[0]) == [(12, 13)]

    def test_separates_distant_regions(self) -> None:
        diff = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1,1 +1,2 @@
 keep
+a
@@ -50,1 +51,2 @@
 keep
+b
"""
        parsed = parse_unified_diff(diff)

        assert changed_line_ranges(parsed.files[0]) == [(2, 2), (52, 52)]

    def test_context_widens_each_range(self) -> None:
        parsed = parse_unified_diff(SIMPLE_DIFF)

        assert changed_line_ranges(parsed.files[0], context=3) == [(9, 16)]

    def test_context_never_produces_a_line_below_one(self) -> None:
        """Line 0 does not exist; widening the first line must clamp."""
        diff = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1,0 +1,1 @@
+first line
"""
        parsed = parse_unified_diff(diff)

        assert changed_line_ranges(parsed.files[0], context=5) == [(1, 6)]

    def test_a_file_with_no_additions_has_no_ranges(self) -> None:
        diff = """diff --git a/gone.py b/gone.py
deleted file mode 100644
--- a/gone.py
+++ /dev/null
@@ -1,1 +0,0 @@
-only
"""
        parsed = parse_unified_diff(diff)

        assert changed_line_ranges(parsed.files[0]) == []

import tempfile
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET

from nfo_utils import (
    disc_part_index,
    find_numbered_trailer,
    find_trailer_in_movie_folder,
    normalize_scan_roots,
    parse_xml_file,
    paths_form_multi_disc_set,
    preferred_image_file,
    preferred_nfo_file,
    replace_text_nodes,
    safe_move_directory,
    sync_actor_nodes,
    write_xml_root_atomic,
)


class XmlSafetyTests(unittest.TestCase):
    def test_actor_metadata_and_genre_are_preserved(self):
        root = ET.fromstring(
            """<movie>
            <title>T</title>
            <actor><name>Alice</name><role>Lead</role><thumb>a.jpg</thumb></actor>
            <actor><name>Bob</name><role>Guest</role></actor>
            <genre>Drama</genre><tag>Old</tag>
            </movie>"""
        )
        sync_actor_nodes(root, ["Alice", "Carol"])
        replace_text_nodes(root, "tag", ["New"])
        actors = root.findall("actor")
        self.assertEqual([a.findtext("name") for a in actors], ["Alice", "Carol"])
        self.assertEqual(actors[0].findtext("role"), "Lead")
        self.assertEqual(actors[0].findtext("thumb"), "a.jpg")
        self.assertEqual(root.findtext("genre"), "Drama")
        self.assertEqual([e.text for e in root.findall("tag")], ["New"])

    def test_atomic_xml_roundtrip_keeps_comments(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "movie.nfo"
            path.write_text("<?xml version='1.0'?><movie><!--keep--><title>A</title></movie>", encoding="utf-8")
            tree = parse_xml_file(str(path))
            tree.getroot().find("title").text = "B"
            write_xml_root_atomic(tree.getroot(), str(path))
            text = path.read_text(encoding="utf-8")
            self.assertIn("<!--keep-->", text)
            self.assertEqual(parse_xml_file(str(path)).getroot().findtext("title"), "B")


class MoveSafetyTests(unittest.TestCase):
    def test_move_and_conflict_guards(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source_parent = root / "src"
            dest_parent = root / "dest"
            source_parent.mkdir()
            dest_parent.mkdir()
            source = source_parent / "Movie"
            source.mkdir()
            (source / "data.txt").write_text("ok", encoding="utf-8")

            moved = Path(safe_move_directory(str(source), str(dest_parent)))
            self.assertTrue((moved / "data.txt").exists())
            self.assertFalse(source.exists())

            source2 = source_parent / "Movie2"
            source2.mkdir()
            with self.assertRaises(OSError):
                safe_move_directory(str(source2), str(source_parent))
            self.assertTrue(source2.exists())

            existing = dest_parent / "Movie2"
            existing.mkdir()
            (existing / "keep.txt").write_text("keep", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                safe_move_directory(str(source2), str(dest_parent))
            self.assertTrue(source2.exists())
            self.assertEqual((existing / "keep.txt").read_text(encoding="utf-8"), "keep")

            child = source2 / "inside"
            child.mkdir()
            with self.assertRaises(OSError):
                safe_move_directory(str(source2), str(child))
            self.assertTrue(source2.exists())


class PathSelectionTests(unittest.TestCase):
    def test_nested_scan_roots_are_deduplicated(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "library"
            child = root / "child"
            child.mkdir(parents=True)
            roots = normalize_scan_roots([str(child), str(root), str(root)])
            self.assertEqual(roots, [str(root)])


    def test_disc_markers_require_distinct_explicit_parts(self):
        self.assertEqual(disc_part_index(r"D:\Movies\Film-CD1\movie.nfo"), 1)
        self.assertEqual(disc_part_index(r"D:\Movies\Film_disc-2\movie.nfo"), 2)
        self.assertIsNone(disc_part_index(r"D:\abcde\movie.nfo"))
        self.assertTrue(paths_form_multi_disc_set([
            r"D:\Movies\Film-CD1\movie.nfo",
            r"D:\Movies\Film-CD2\movie.nfo",
        ]))
        self.assertFalse(paths_form_multi_disc_set([
            r"D:\Movies\Film-CD1\movie.nfo",
            r"D:\Backup\Film-CD1\movie.nfo",
        ]))

    def test_preferred_files_are_deterministic(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            for name in ["z.nfo", "Movie.nfo", "a.nfo", "z-poster.jpg", "Movie-poster.png"]:
                (folder / name).write_bytes(b"x")
            self.assertEqual(Path(preferred_nfo_file(str(folder), "Movie")).name, "Movie.nfo")
            self.assertEqual(
                Path(preferred_image_file(str(folder), "poster", "Movie")).name,
                "Movie-poster.png",
            )


class TrailerSelectionTests(unittest.TestCase):
    def test_numbered_trailer_prefers_exact_and_never_prefix_matches(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            (folder / "ABC-1234.mp4").write_bytes(b"wrong")
            (folder / "abc123.mkv").write_bytes(b"normalized")
            (folder / "ABC-123.mp4").write_bytes(b"exact")
            selected = find_numbered_trailer(str(folder), "ABC-123")
            self.assertEqual(Path(selected).name, "ABC-123.mp4")

    def test_numbered_trailer_accepts_suffix_and_strm(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            (folder / "SSIS-001-trailer.strm").write_text("https://example.test/video", encoding="utf-8")
            selected = find_numbered_trailer(str(folder), "SSIS-001")
            self.assertEqual(Path(selected).name, "SSIS-001-trailer.strm")

    def test_movie_folder_trailer_is_deterministic(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            (folder / "z-trailer.mkv").write_bytes(b"z")
            (folder / "a-trailer.mp4").write_bytes(b"a")
            selected = find_trailer_in_movie_folder(str(folder))
            self.assertEqual(Path(selected).name, "a-trailer.mp4")


if __name__ == "__main__":
    unittest.main()

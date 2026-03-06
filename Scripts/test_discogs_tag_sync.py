import tempfile
import unittest
from pathlib import Path

import discogs_tag_sync as d


class DiscogsTagSyncTests(unittest.TestCase):
    def test_parse_position(self):
        self.assertEqual(d.parse_position("1-03"), (3, 1))
        self.assertEqual(d.parse_position("A2"), (2, None))
        self.assertEqual(d.parse_position("07"), (7, None))
        self.assertEqual(d.parse_position(""), (None, None))

    def test_unique_keep_order(self):
        vals = ["Rock", "rock", "J-Pop", "", "Rock "]
        self.assertEqual(d.unique_keep_order(vals), ["Rock", "J-Pop"])

    def test_pick_best_match_prefers_track_number_and_title(self):
        local = d.LocalTrackInfo(
            path=Path("dummy.mp3"),
            title="Little Hand",
            artist="Doors",
            album="Doors",
            track_number=4,
        )

        release = {
            "id": 123,
            "title": "DOORS",
            "year": 1990,
            "artists": [{"name": "Doors"}],
            "genres": ["Pop"],
            "styles": ["J-Pop"],
            "tracklist": [
                {"position": "3", "title": "Wrong Song", "type_": "track"},
                {"position": "4", "title": "Little Hand", "type_": "track"},
            ],
        }

        match = d.pick_best_match(local, [release])
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.release_id, 123)
        self.assertEqual(match.track_title, "Little Hand")
        self.assertEqual(match.track_number, 4)
        self.assertEqual(match.release_year, 1990)
        self.assertIn("Pop", match.genres)

    def test_apply_discogs_tags_dry_run(self):
        with tempfile.NamedTemporaryFile(suffix=".mp3") as tmp:
            path = Path(tmp.name)
            match = d.DiscogsTrackMatch(
                release_id=1,
                release_title="Album",
                release_year=2001,
                release_artists=["Artist"],
                track_title="Song",
                track_position="2",
                track_number=2,
                disc_number=None,
                genres=["J-Pop"],
                composers=["Composer"],
            )

            changed, logs = d.apply_discogs_tags(path, match, dry_run=True)
            self.assertTrue(changed)
            self.assertTrue(any("TIT2" in x for x in logs))
            self.assertTrue(any("TYER" in x for x in logs))
            self.assertTrue(any("TCON" in x for x in logs))


if __name__ == "__main__":
    unittest.main()

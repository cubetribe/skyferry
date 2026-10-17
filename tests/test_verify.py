import unittest

from skyferry.verify import Entry, compare, entry_from_json, listing_from_objects

E = Entry


class VerifyTests(unittest.TestCase):
    def test_ok_and_empty_files(self):
        exp = {"a": E(3, "m1"), "leer": E(0, "d41d8cd98f00b204e9800998ecf8427e")}
        self.assertTrue(compare(exp, dict(exp), stage="local").ok)

    def test_missing_size_md5(self):
        exp = {"a": E(3, "m1"), "b": E(3, "m2"), "c": E(3, "m3")}
        act = {"a": E(4, "m1"), "c": E(3, "xx")}
        r = compare(exp, act, stage="local")
        self.assertFalse(r.ok)
        self.assertEqual((r.counts["missing"], r.counts["mismatch"]), (1, 2))

    def test_extra_is_warning_only(self):
        r = compare({"a": E(1, "m")}, {"a": E(1, "m"), "z": E(1, "q")}, stage="remote")
        self.assertTrue(r.ok)
        self.assertEqual(r.counts["extra"], 1)

    def test_gdoc(self):
        gd = entry_from_json({"Path": "r.docx", "Size": -1, "IsDir": False})
        self.assertTrue(gd.is_gdoc)
        self.assertTrue(compare({"r.docx": gd}, {"r.docx": E(123, "m")}, stage="local").ok)
        self.assertFalse(compare({"r.docx": gd}, {"r.docx": E(0, "m")}, stage="local").ok)
        self.assertFalse(compare({"r.docx": gd}, {}, stage="local").ok)

    def test_size_only_when_one_side_lacks_hash(self):
        self.assertTrue(compare({"a": E(3, "m")}, {"a": E(3, None)}, stage="remote").ok)

    def test_duplicates_fail(self):
        lst, dups = listing_from_objects([{"Path": "a", "Size": 1, "IsDir": False},
                                          {"Path": "a", "Size": 2, "IsDir": False}])
        self.assertEqual(dups, ["a"])
        self.assertFalse(compare(lst, lst, stage="local", duplicates=dups).ok)


if __name__ == "__main__":
    unittest.main()

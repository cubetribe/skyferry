import unittest

from skyferry.planner import build_tree, commit_plan, compute_stats, plan_units
from skyferry.state import State, Status

MAXB = 1000


def f(path, size, md5="x"):
    return {"Path": path, "Name": path.rsplit("/", 1)[-1], "Size": size, "IsDir": False,
            "Hashes": {"md5": md5} if size >= 0 else {}}


def d(path, id_=None):
    return {"Path": path, "Name": path.rsplit("/", 1)[-1], "Size": -1, "IsDir": True, "ID": id_ or "id:" + path}


def plan(entries, existing=None):
    tree = build_tree(iter(entries), root_id="ROOT", done_prefix="[MIGRIERT] ", max_unit_bytes=MAXB)
    return tree, plan_units(tree, max_unit_bytes=MAXB, existing=existing)


class PlannerTests(unittest.TestCase):
    def test_small_folder_single_unit_and_root_loose(self):
        _, units = plan([d("A"), f("A/x", 10), f("root.txt", 5), d("A/B"), f("A/B/y", 20)])
        kinds = [(u.drive_path, u.kind, u.is_split, u.bytes, u.files) for u in units]
        self.assertEqual(kinds, [("A", "folder", False, 30, 2), ("", "loose_files", False, 5, 1)])
        self.assertEqual(units[1].name, "(Root)")

    def test_split_large_folder(self):
        entries = [d("Big"), d("Big/p1"), d("Big/p2"), f("Big/p1/a", 600), f("Big/p2/b", 600), f("Big/l", 100),
                   f("Big/p1/gdoc.docx", -1, None)]
        _, units = plan(entries)
        by = {(u.drive_path, u.kind): u for u in units}
        self.assertTrue(by[("Big", "folder")].is_split)
        self.assertEqual(by[("Big", "folder")].parent_key, None)
        self.assertEqual(by[("Big/p1", "folder")].parent_key, ("id:Big", "folder"))
        self.assertEqual(by[("Big", "loose_files")].bytes, 100)
        self.assertEqual(by[("Big/p1", "folder")].gdocs, 1)
        self.assertEqual(by[("Big/p1", "folder")].files, 2)
        self.assertEqual(by[("Big/p1", "folder")].bytes, 600)  # Google-Doc zaehlt 0 Bytes

    def test_single_huge_file_warns(self):
        tree, units = plan([d("H"), f("H/huge", 5000)])
        self.assertEqual(len(tree.big_files), 1)
        self.assertTrue(units[0].is_split)  # Ordner > max -> Container, Datei als loose
        self.assertEqual(units[1].kind, "loose_files")

    def test_done_prefix_skipped(self):
        tree, units = plan([d("[MIGRIERT] Alt"), f("[MIGRIERT] Alt/x", 50), d("Neu"), f("Neu/y", 5),
                            f("Neu/[MIGRIERT] z", 1)])
        self.assertEqual([u.drive_path for u in units], ["Neu"])
        self.assertEqual(tree.skipped_done, 3)

    def test_duplicates_detected(self):
        _, units = plan([d("A"), f("A/x", 1), f("A/x", 2), d("B"), f("B/y", 1)])
        a = next(u for u in units if u.drive_path == "A")
        b = next(u for u in units if u.drive_path == "B")
        self.assertEqual(a.dups, ["A/x"])
        self.assertEqual(b.dups, [])

    def test_unordered_entries_and_missing_dir_entries(self):
        _, units = plan([f("A/B/c", 5), d("A")])
        self.assertEqual(units[0].drive_path, "A")
        self.assertEqual(units[0].files, 1)

    def test_stats(self):
        tree, units = plan([d("A"), f("A/x", 10), f("A/g.docx", -1, None)])
        s = compute_stats(units, tree)
        self.assertEqual((s.units, s.bytes, s.files, s.gdocs), (1, 10, 2, 1))


class CommitTests(unittest.TestCase):
    def setUp(self):
        self.st = State(":memory:")

    def test_commit_idempotent_and_dups(self):
        entries = [d("A"), f("A/x", 1), f("A/x", 1), d("B"), f("B/y", 1)]
        tree, units = plan(entries, existing=self.st.find_unit)
        stats = compute_stats(units, tree)
        commit_plan(self.st, units, tree, stats)
        self.assertEqual(stats.new, 2)
        a = self.st.find_unit("id:A", "folder")
        self.assertEqual(a.status, Status.NEEDS_ATTENTION)
        b = self.st.find_unit("id:B", "folder")
        self.st.transition(b.id, Status.PLANNED, Status.DOWNLOADING)
        entries.append(d("C")); entries.append(f("C/z", 3)); entries[4] = f("B/y", 999)
        tree, units = plan(entries, existing=self.st.find_unit)
        stats = compute_stats(units, tree)
        commit_plan(self.st, units, tree, stats)
        self.assertEqual(stats.new, 1)
        self.assertEqual(self.st.find_unit("id:B", "folder").expected_bytes, 1)  # unveraendert (schon DOWNLOADING)
        self.assertEqual(self.st.find_unit("id:B", "folder").status, Status.DOWNLOADING)

    def test_transition_checks_source_state(self):
        tree, units = plan([d("A"), f("A/x", 1)])
        commit_plan(self.st, units, tree, compute_stats(units, tree))
        u = self.st.find_unit("id:A", "folder")
        self.assertFalse(self.st.transition(u.id, Status.DOWNLOADED, Status.VERIFIED_LOCAL))
        self.assertTrue(self.st.transition(u.id, Status.PLANNED, Status.DOWNLOADING))
        self.assertGreaterEqual(len(self.st.events(unit_id=u.id)), 2)


if __name__ == "__main__":
    unittest.main()

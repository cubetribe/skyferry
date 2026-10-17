import unittest

from skyferry.config import parse_config
from skyferry.rclone import Rclone, SafetyError, classify_exit
from tests.helpers import RCLONE, TmpCase, make_config, needs_rclone

DRIVE_CFG = parse_config({"target": {"path": "/tmp/x"}, "source": {"remote": "gdrive"}})
SRC = "gdrive,root_folder_id=ABC:"


class DriveModeSafety(unittest.TestCase):
    def setUp(self):
        self.rc = Rclone(DRIVE_CFG)

    def test_forbidden_commands_on_source(self):
        for cmd in ["delete", "deletefile", "purge", "sync", "move", "rmdir", "rmdirs", "dedupe", "cleanup"]:
            with self.assertRaises(SafetyError, msg=cmd):
                self.rc.build(cmd, [SRC])

    def test_copy_into_source_blocked(self):
        with self.assertRaises(SafetyError):
            self.rc.build("copy", ["/lokal", SRC])
        with self.assertRaises(SafetyError):
            self.rc.build("copy", [SRC, "gdrive:andere"])

    def test_copy_from_source_ok_and_readonly_ok(self):
        self.rc.build("copy", [SRC, "/lokal"])
        self.rc.build("lsjson", [SRC])
        self.rc.build("size", ["gdrive:"])

    def test_delete_flags_blocked(self):
        with self.assertRaises(SafetyError):
            self.rc.build("copy", [SRC, "/lokal"], ["--delete-after"])

    def test_moveto_only_rename_with_prefix(self):
        self.rc.build("moveto", ["gdrive,root_folder_id=P:Ordner", "gdrive,root_folder_id=P:[MIGRIERT] Ordner"])
        bad = [
            ("gdrive,root_folder_id=P:Ordner", "gdrive,root_folder_id=P:Anders"),
            ("gdrive,root_folder_id=P:Ordner", "gdrive,root_folder_id=Q:[MIGRIERT] Ordner"),
            ("gdrive,root_folder_id=P:a/Ordner", "gdrive,root_folder_id=P:b/[MIGRIERT] Ordner"),
            ("gdrive,root_folder_id=P:Ordner", "/lokal/[MIGRIERT] Ordner"),
            ("/lokal/a", "gdrive:[MIGRIERT] a"),
            ("gdrive:x", "gdrive:y/[MIGRIERT] x"),
        ]
        for s, d in bad:
            with self.assertRaises(SafetyError, msg=(s, d)):
                self.rc.build("moveto", [s, d])

    def test_non_source_commands_limited(self):
        self.rc.build("copy", ["/a", "/b"])
        for cmd in ["delete", "sync", "purge"]:
            with self.assertRaises(SafetyError):
                self.rc.build(cmd, ["/a"])

    def test_exit_code_mapping(self):
        self.assertEqual(classify_exit(0), "ok")
        self.assertEqual(classify_exit(3), "not_found")
        self.assertEqual(classify_exit(5), "temporary")
        self.assertEqual(classify_exit(8), "transfer_limit")
        self.assertEqual(classify_exit(7), "fatal")
        self.assertEqual(classify_exit(-15), "interrupted")


class LocalModeSafety(TmpCase):
    def test_source_protected_in_local_mode(self):
        cfg = make_config(self.tmp)
        rc = Rclone(cfg)
        src = str(self.tmp / "drive" / "A")
        for cmd in ["delete", "sync", "purge"]:
            with self.assertRaises(SafetyError):
                rc.run(cmd, [src])
        with self.assertRaises(SafetyError):
            rc.build("copy", [str(self.tmp / "buf"), src])

    @needs_rclone
    def test_stream_and_rename(self):
        cfg = make_config(self.tmp)
        (self.tmp / "drive" / "A" / "B").mkdir(parents=True)
        (self.tmp / "drive" / "A" / "B" / "f.txt").write_text("hi")
        rc = Rclone(cfg)
        objs = list(rc.stream_lsjson(str(self.tmp / "drive"), ["-R", "--hash", "--hash-type", "md5"]))
        self.assertEqual({o["Path"] for o in objs}, {"A", "A/B", "A/B/f.txt"})
        rc.run("moveto", [str(self.tmp / "drive" / "A"), str(self.tmp / "drive" / "[MIGRIERT] A")])
        self.assertTrue((self.tmp / "drive" / "[MIGRIERT] A" / "B" / "f.txt").exists())


if __name__ == "__main__":
    unittest.main()

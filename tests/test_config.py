import unittest
from pathlib import Path

from skyferry.config import ConfigError, load_config, parse_config

ROOT = Path(__file__).resolve().parent.parent


class ConfigTests(unittest.TestCase):
    def test_example_loads_and_has_safe_defaults(self):
        cfg = load_config(ROOT / "config.example.toml")
        self.assertFalse(cfg.notify.include_paths)
        self.assertTrue(cfg.buffer.require_mount)
        self.assertEqual(cfg.source.done_prefix, "[MIGRIERT] ")
        self.assertEqual(cfg.buffer.reserve_bytes, 50_000_000_000)
        self.assertEqual(cfg.notify.ntfy_url, "")

    def test_example_contains_no_secrets(self):
        text = (ROOT / "config.example.toml").read_text()
        self.assertNotIn("hc-ping.com/", text.replace("z.B. https://hc-ping.com/<uuid>", ""))

    def test_unknown_key_and_type_errors(self):
        with self.assertRaises(ConfigError):
            parse_config({"target": {"path": "/x"}, "buffer": {"nope": 1}})
        with self.assertRaises(ConfigError):
            parse_config({"target": {"path": "/x"}, "buffer": {"max_unit_bytes": "viel"}})
        with self.assertRaises(ConfigError):
            parse_config({"target": {"path": ""}})
        with self.assertRaises(ConfigError):
            parse_config({"target": {"path": "/x"}, "source": {"done_prefix": ""}})


if __name__ == "__main__":
    unittest.main()

import os
import sys
import unittest
import shutil
import tempfile
import re
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import backlogger

class TestIcons(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.output_dir = os.path.join(self.test_dir, "output_icons")
        self.config_dir = os.path.join(self.test_dir, "config")
        os.makedirs(os.path.join(self.config_dir, "icons"))
        
        self.app = {"name": "Test App", "url": "https://example.com"}

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_fetch_icon_external_override(self):
        # Create an override icon in config_dir/icons
        override_icon_path = os.path.join(self.config_dir, "icons", "Test App.png")
        with open(override_icon_path, "w") as f:
            f.write("fake icon content")

        # Call fetch_icon with config_dir
        icon_path = backlogger.fetch_icon(self.app, output_dir=self.output_dir, config_dir=self.config_dir)

        # It should return the path in output_dir (because it was copied there)
        expected_path = os.path.join(self.output_dir, "Test App.png")
        self.assertEqual(os.path.abspath(icon_path), os.path.abspath(expected_path))
        self.assertTrue(os.path.exists(expected_path))
        
        # Verify content was copied
        with open(expected_path, "r") as f:
            self.assertEqual(f.read(), "fake icon content")

    def test_fetch_icon_favicon_override(self):
        # Create an override favicon in config_dir/icons
        override_favicon_path = os.path.join(self.config_dir, "icons", "favicon.png")
        with open(override_favicon_path, "w") as f:
            f.write("fake favicon content")

        # Mock requests to avoid network calls
        with patch('requests.get') as mock_get:
            mock_get.side_effect = Exception("Should not be called")
            
            # Call fetch_icon with config_dir
            icon_path = backlogger.fetch_icon(self.app, output_dir=self.output_dir, config_dir=self.config_dir)

            expected_path = os.path.join(self.output_dir, "favicon.png")
            self.assertEqual(os.path.abspath(icon_path), os.path.abspath(expected_path))
            self.assertTrue(os.path.exists(expected_path))

    def test_fetch_icon_priority(self):
        # 1. Bundled icon
        bundled_dir = os.path.join(self.test_dir, "icons")
        os.makedirs(bundled_dir)
        bundled_icon = os.path.join(bundled_dir, "test_app.png")
        with open(bundled_icon, "w") as f:
            f.write("bundled")

        # 2. External icon
        external_icon = os.path.join(self.config_dir, "icons", "test_app.png")
        with open(external_icon, "w") as f:
            f.write("external")

        # Patch backlogger.os.path.abspath and os.path.dirname
        # base_dir = os.path.dirname(os.path.abspath(__file__))
        with patch('backlogger.os.path.abspath') as mock_abspath:
            mock_abspath.side_effect = lambda x: x if x.startswith('/') else os.path.join(self.test_dir, x)
            with patch('backlogger.os.path.dirname') as mock_dirname:
                # When calling dirname on the result of abspath(__file__), it should return test_dir
                mock_dirname.side_effect = lambda x: self.test_dir if 'backlogger.py' in x else os.path.dirname(x)
                
                icon_path = backlogger.fetch_icon(self.app, output_dir=self.output_dir, config_dir=self.config_dir)
                
                self.assertIsNotNone(icon_path)
                with open(icon_path, "r") as f:
                    self.assertEqual(f.read(), "external") # External should win

    def test_setup_theme_logo(self):
        # Create a logo in config_dir
        logo_path = os.path.join(self.config_dir, "logo.png")
        with open(logo_path, "w") as f:
            f.write("fake logo content")

        data = {
            "theme": "modern",
            "team": "Logo Team",
            "url": "https://logo.example.com",
            "config_dir": self.config_dir
        }

        # We need to mock os.path.dirname(os.path.abspath(__file__)) to point to the real repo root
        # so it finds themes/modern/head.html
        real_base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        
        with patch('backlogger.os.path.dirname') as mock_dirname:
            mock_dirname.side_effect = lambda x: real_base_dir if 'backlogger.py' in x else os.path.dirname(x)
            
            # We also need to mock shutil.copy and shutil.copy2
            with patch('backlogger.shutil.copy') as mock_copy, \
                 patch('backlogger.shutil.copy2') as mock_copy2, \
                 patch('backlogger.open', unittest.mock.mock_open(read_data="TEAM_BRANDING")) as mock_file:
                
                theme = backlogger.setup_theme(data)
                
                self.assertEqual(theme, "modern")
                
                # Check that it tried to write the head.html with the logo
                # The logo HTML should be in the branding
                written_content = ""
                for call_args in mock_file().write.call_args_list:
                    written_content += call_args[0][0]
                
                self.assertIn('icons/logo.png', written_content)
                self.assertIn('Logo Team', written_content)
                self.assertIn('https://logo.example.com', written_content)

if __name__ == "__main__":
    unittest.main()

import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BehaviorTreeTests(unittest.TestCase):
    def test_all_enabled_tree_templates_are_well_formed(self) -> None:
        expected = {"home", "move_to", "pick", "place", "pick_and_place", "stop"}
        found = {path.stem for path in (ROOT / "behavior_trees").glob("*.xml")}
        self.assertEqual(found, expected)
        for name in expected:
            root = ElementTree.parse(ROOT / "behavior_trees" / f"{name}.xml").getroot()
            self.assertEqual(root.tag, "root")
            self.assertEqual(root.attrib["BTCPP_format"], "4")


if __name__ == "__main__":
    unittest.main()

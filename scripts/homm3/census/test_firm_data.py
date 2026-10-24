"""placements.prefer_firm_data: an operand-order guess yields to agreeing bytes."""
import unittest

from homm3.census.placements import prefer_firm_data

SLOT_STRINGS = "_?artifactSlotStrings@?BH@??InitializeArtifactTraitsTable@@YA_NXZ@4V?$TAutoArrayPtr@D@@A"


class FirmDataTests(unittest.TestCase):
    def test_the_agreeing_field_names_the_datum(self):
        # the exact teardown reaches 0x19a588; the reordered caller's
        # operand-order pairing lands on the guard before it
        names = {SLOT_STRINGS: {0x19a584, 0x19a588}}
        prefer_firm_data(names, {(SLOT_STRINGS, 0x19a588)})
        self.assertEqual(names, {SLOT_STRINGS: {0x19a588}})

    def test_two_firm_addresses_stay_a_conflict(self):
        names = {"a": {0x10, 0x20}}
        prefer_firm_data(names, {("a", 0x10), ("a", 0x20)})
        self.assertEqual(names, {"a": {0x10, 0x20}})

    def test_operand_order_alone_decides_nothing(self):
        names = {"a": {0x10, 0x20}, "b": {0x30}}
        prefer_firm_data(names, set())
        self.assertEqual(names, {"a": {0x10, 0x20}, "b": {0x30}})


if __name__ == "__main__":
    unittest.main()

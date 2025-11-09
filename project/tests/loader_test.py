import unittest

from loader.loader import CardPrint, DeckLoader


class DeckLoaderTextParseTests(unittest.TestCase):
    def setUp(self):
        self.loader = DeckLoader()

    def test_load_from_text_basic(self):
        deck_text = """
        Commander (1)
        1 Atraxa, Grand Unifier (ONE) 321

        Deck (3)
        2 Brainstorm (ICE) 45
        1 Brainstorm (ICE) 45
        """
        entries = self.loader.load_from_text(deck_text)
        self.assertEqual(len(entries), 2)

        atraxa = next(e for e in entries if e.name.startswith("Atraxa"))
        self.assertEqual(atraxa.quantity, 1)
        self.assertEqual(atraxa.set_code.upper(), "ONE")
        self.assertEqual(atraxa.collector_number, "321")

        brainstorm = next(e for e in entries if e.name == "Brainstorm")
        self.assertEqual(brainstorm.quantity, 3)

    def test_cache_requests_unique_and_repeated(self):
        cards = [
            CardPrint(name="Test", set_code="ABC", collector_number="7", quantity=2),
            CardPrint(name="Other", set_code="XYZ", collector_number="1", quantity=1),
        ]
        repeated = DeckLoader.as_cache_requests(cards, unique_only=False)
        self.assertEqual(repeated, [("ABC", "7"), ("ABC", "7"), ("XYZ", "1")])

        unique = DeckLoader.as_cache_requests(cards, unique_only=True)
        self.assertEqual(unique, [("ABC", "7"), ("XYZ", "1")])


if __name__ == "__main__":
    unittest.main()

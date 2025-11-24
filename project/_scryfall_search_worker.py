from PySide6.QtCore import QObject, Signal, Slot

from loader.loader import DeckLoader, DeckLoaderError


class _ScryfallSearchWorker(QObject):
    finished = Signal(list, str)  # results, query
    failed = Signal(str, str)  # message, query

    def __init__(self, loader: DeckLoader, query: str):
        super().__init__()
        self._loader = loader
        self._query = query

    @Slot()
    def run(self):
        try:
            results = self._loader.search_cards(self._query)
            self.finished.emit(results, self._query)
        except DeckLoaderError as exc:
            self.failed.emit(str(exc), self._query)

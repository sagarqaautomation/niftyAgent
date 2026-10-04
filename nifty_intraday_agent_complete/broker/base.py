from abc import ABC, abstractmethod

class MarketDataAdapter(ABC):
    @abstractmethod
    def connect(self):
        raise NotImplementedError

    @abstractmethod
    def subscribe(self, instrument_tokens):
        raise NotImplementedError

    @abstractmethod
    def on_tick(self, callback):
        raise NotImplementedError

    @abstractmethod
    def start(self):
        raise NotImplementedError

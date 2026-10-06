from abc import ABC, abstractmethod

class MarketDataAdapter(ABC):
    @abstractmethod
    def connect(self):
        raise NotImplementedError

    @abstractmethod
    def subscribe(self, instrument_tokens):
        raise NotImplementedError

    @abstractmethod
    def historical_data(self, instrument_token, from_date, to_date, interval):
        raise NotImplementedError

    @abstractmethod
    def set_instrument_tokens(self, instrument_tokens):
        raise NotImplementedError

    @abstractmethod
    def on_tick(self, callback):
        raise NotImplementedError

    @abstractmethod
    def on_status(self, callback):
        raise NotImplementedError

    @abstractmethod
    def start(self):
        raise NotImplementedError

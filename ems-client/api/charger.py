"""Charger Interface — Wallbox / Ladestation Steuerung."""

from abc import ABC, abstractmethod


class Charger(ABC):
    """Ladestation mit Status, Enable und Stromsteuerung."""

    @abstractmethod
    def status(self) -> str:
        """Ladestatus: A=getrennt, B=verbunden, C=lädt, F=Fehler."""
        ...

    @abstractmethod
    def enabled(self) -> bool:
        """Ob die Ladestation freigegeben ist."""
        ...

    @abstractmethod
    def enable(self, on: bool) -> None:
        """Ladestation freigeben oder sperren."""
        ...

    @abstractmethod
    def max_current(self, current: float) -> None:
        """Maximalen Ladestrom in Ampere setzen."""
        ...

"""Battery Interface — Speicher mit SoC und Leistung."""

from abc import ABC, abstractmethod


class Battery(ABC):
    """Batteriespeicher."""

    @abstractmethod
    def soc(self) -> float:
        """State of Charge in Prozent (0-100)."""
        ...

    @abstractmethod
    def current_power(self) -> float:
        """Batterieleistung in Watt. Positiv = Entladung, Negativ = Ladung."""
        ...

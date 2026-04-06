"""Meter Interface — Leistungs- und Energiemessung."""

from abc import ABC, abstractmethod


class Meter(ABC):
    """Misst aktuelle Leistung in Watt."""

    @abstractmethod
    def current_power(self) -> float:
        """Aktuelle Leistung in Watt. Positiv = Bezug/Verbrauch, Negativ = Einspeisung."""
        ...


class MeterEnergy(ABC):
    """Misst kumulierte Energie in kWh."""

    @abstractmethod
    def total_energy(self) -> float:
        """Gesamtenergie in kWh."""
        ...

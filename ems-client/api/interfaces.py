"""Optionale Mixin-Interfaces für erweiterte Gerätefunktionen."""

from abc import ABC, abstractmethod


class PhaseCurrents(ABC):
    """Liefert Phasenströme L1/L2/L3."""

    @abstractmethod
    def currents(self) -> tuple[float, float, float]:
        """Ströme in Ampere (L1, L2, L3)."""
        ...


class PhaseVoltages(ABC):
    """Liefert Phasenspannungen L1/L2/L3."""

    @abstractmethod
    def voltages(self) -> tuple[float, float, float]:
        """Spannungen in Volt (L1, L2, L3)."""
        ...


class PhasePowers(ABC):
    """Liefert Phasenleistungen L1/L2/L3."""

    @abstractmethod
    def powers(self) -> tuple[float, float, float]:
        """Leistungen in Watt (L1, L2, L3)."""
        ...


class PhaseSwitcher(ABC):
    """Umschalten zwischen 1-phasig und 3-phasig."""

    @abstractmethod
    def phases_1p3p(self, phases: int) -> None:
        """Phasen umschalten: 1 oder 3."""
        ...

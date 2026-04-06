"""Circuit — Leitungsschutz mit hierarchischer Stromverteilung.

Modelliert die elektrische Installation eines Standorts:
  Hauptsicherung (z.B. 63A 3P)
  ├── Unterverteilung Garage (32A)
  │   ├── Wallbox 1 (max 16A)
  │   └── Wallbox 2 (max 16A)
  ├── Unterverteilung Heizraum (25A)
  │   └── Heizstab (max 16A)
  └── Haushalt (Rest)

Verhindert, dass die Summe aller Verbraucher die Sicherung überlastet.
"""

import logging
from typing import Optional

log = logging.getLogger("ems.circuit")

VOLTAGE = 230  # V Nennspannung


class Circuit:
    """Leitungsschutz mit hierarchischer Begrenzung.

    Jeder Circuit hat:
    - max_current (A): Sicherungswert
    - max_power_kw (kW): Optionales Leistungslimit (z.B. bei Netzanschluss)
    - parent: Übergeordneter Circuit
    - children: Untergeordnete Circuits
    - consumers: Zugeordnete Loadpoints/Geräte mit aktuellem Verbrauch
    """

    def __init__(self, config: dict):
        self.id: str = config.get("id", "")
        self.name: str = config.get("name", "Hauptsicherung")
        self.max_current: float = float(config.get("max_current", 63))
        self.max_power_w: float = float(config.get("max_power_kw", 0)) * 1000
        self.parent: Optional["Circuit"] = None
        self.children: list["Circuit"] = []
        self._current_load_a: float = 0  # Aktuelle Last in Ampere

    def add_child(self, child: "Circuit"):
        """Fügt einen Kind-Circuit hinzu."""
        child.parent = self
        self.children.append(child)

    def available_current(self, grid_current_a: float = 0) -> float:
        """Berechnet verfügbaren Strom unter Berücksichtigung der Hierarchie.

        Args:
            grid_current_a: Aktuelle Gesamtlast am Netzanschluss (Ampere)

        Returns:
            Verfügbare Ampere für neue Verbraucher
        """
        # Eigenes Limit
        local_available = self.max_current - self._current_load_a

        # Leistungslimit (wenn gesetzt)
        if self.max_power_w > 0:
            power_limit_a = self.max_power_w / (VOLTAGE * 3)  # 3-phasig
            local_available = min(local_available, power_limit_a - self._current_load_a)

        # Parent-Limit prüfen (rekursiv)
        if self.parent:
            parent_available = self.parent.available_current(grid_current_a)
            local_available = min(local_available, parent_available)

        return max(0, local_available)

    def update_load(self, current_a: float):
        """Aktualisiert die aktuelle Last dieses Circuits."""
        self._current_load_a = current_a

    def add_load(self, current_a: float):
        """Fügt Last hinzu (kumulativ)."""
        self._current_load_a += current_a

    def reset_load(self):
        """Setzt Last auf 0 (am Anfang jedes Zyklus)."""
        self._current_load_a = 0
        for child in self.children:
            child.reset_load()

    def is_overloaded(self) -> bool:
        """Prüft ob dieser Circuit überlastet ist."""
        return self._current_load_a > self.max_current * 1.05  # 5% Toleranz

    def utilization(self) -> float:
        """Auslastung in Prozent (0-100+)."""
        if self.max_current <= 0:
            return 0
        return (self._current_load_a / self.max_current) * 100

    def state(self) -> dict:
        """Status für MQTT/Dashboard."""
        return {
            "id": self.id,
            "name": self.name,
            "max_current_a": self.max_current,
            "current_load_a": round(self._current_load_a, 1),
            "available_a": round(self.available_current(), 1),
            "utilization_pct": round(self.utilization(), 1),
            "overloaded": self.is_overloaded(),
            "children": [c.state() for c in self.children],
        }


class CircuitManager:
    """Verwaltet die Circuit-Hierarchie eines Standorts."""

    def __init__(self):
        self.root: Circuit | None = None
        self.circuits: dict[str, Circuit] = {}

    def build_from_config(self, circuits_config: list[dict]):
        """Baut die Circuit-Hierarchie aus der DB-Konfiguration.

        Args:
            circuits_config: Liste von Circuit-Dicts mit parent_circuit_id
        """
        self.circuits.clear()

        # Alle Circuits erstellen
        for cc in circuits_config:
            circuit = Circuit(cc)
            self.circuits[circuit.id] = circuit

        # Parent-Child Beziehungen aufbauen
        for cc in circuits_config:
            cid = cc.get("id", "")
            pid = cc.get("parent_circuit_id")
            if pid and pid in self.circuits and cid in self.circuits:
                self.circuits[pid].add_child(self.circuits[cid])

        # Root = Circuit ohne Parent
        roots = [c for c in self.circuits.values() if c.parent is None]
        if roots:
            self.root = roots[0]
            log.info("Circuit-Hierarchie: Root=%s (%dA), %d Circuits gesamt",
                     self.root.name, self.root.max_current, len(self.circuits))

    def get_circuit(self, circuit_id: str) -> Circuit | None:
        return self.circuits.get(circuit_id)

    def available_for_loadpoint(self, circuit_id: str | None) -> float:
        """Verfügbarer Strom für einen bestimmten Loadpoint-Circuit."""
        if circuit_id and circuit_id in self.circuits:
            return self.circuits[circuit_id].available_current()
        if self.root:
            return self.root.available_current()
        return 999  # Kein Circuit konfiguriert → unlimitiert

    def reset_all(self):
        """Setzt alle Lasten zurück (am Anfang jedes Zyklus)."""
        if self.root:
            self.root.reset_load()

    def state(self) -> dict | None:
        """Gesamtstatus für MQTT/Dashboard."""
        if self.root:
            return self.root.state()
        return None

"""Fault-tolerant (physical) resource estimation, under explicit assumptions.

Everything else in qSHA256 reports **logical** resources: qubits and gates in an
idealised, error-free machine.  This module is the only place that speaks about
physical hardware, and it is deliberately quarantined here because the step from
logical to physical is where quantum resource estimates most often go wrong.

A logical qubit is not a physical qubit.  Running a circuit with ~1300 logical
qubits does not need ~1300 physical qubits; it needs each logical qubit encoded
in a quantum error-correcting code, plus factories manufacturing the magic
states that every T gate consumes.  The multiplier is not a constant -- it
depends on the physical error rate, the target failure probability, and how long
the computation runs.

So this module takes a :class:`HardwareModel` rather than pretending there is a
universal answer, and every number it returns is stamped with the assumptions
that produced it.  Change the error rate and the answer changes; that is the
correct behaviour, not a defect.

The model
---------

Surface code, following the standard treatment in Fowler et al. (2012) and the
accounting style of Gidney & Ekera (2019):

**Logical error rate per logical qubit per code cycle**, for code distance ``d``
and physical error rate ``p`` below threshold ``p_th``::

    p_L(d) = 0.1 * (p / p_th) ** ((d + 1) / 2)

**Code distance** is the smallest odd ``d`` for which the whole computation's
error budget holds::

    p_L(d) * (logical qubits) * (code cycles) <= target_failure_probability

**Physical qubits per logical qubit** is taken as ``2 d^2`` -- the ``d^2`` data
patch plus an equal routing/ancilla allowance.

**Runtime** is estimated two ways, and the larger binds:

* *reaction-limited*: ``toffoli_depth * d`` code cycles, i.e. one logical time
  step of ``d`` syndrome rounds per layer of non-Clifford gates.  This is a
  floor set by the circuit's serial structure.
* *distillation-limited*: ``T_count / factories * factory_cycles`` code cycles,
  set by how fast magic states can be produced.

What this module will not do
----------------------------

It does not estimate anything it cannot derive from a stated formula.  Magic
state factory footprints in particular vary by more than an order of magnitude
across published designs, so the factory parameters are inputs with documented
defaults, never silently-chosen constants.  Where a quantity would require a
detailed layout study to get right, it is reported as an assumption rather than
guessed.

References
----------
- A. G. Fowler, M. Mariantoni, J. M. Martinis, A. N. Cleland, "Surface codes:
  Towards practical large-scale quantum computation", Phys. Rev. A 86, 032324
  (2012), arXiv:1208.0928.
- C. Gidney & M. Ekera, "How to factor 2048 bit RSA integers in 8 hours using
  20 million noisy qubits", Quantum 5, 433 (2021), arXiv:1905.09749.
- D. Litinski, "A Game of Surface Codes", Quantum 3, 128 (2019),
  arXiv:1808.02892.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

__all__ = [
    "HARDWARE_MODELS",
    "HardwareModel",
    "PhysicalEstimate",
    "choose_code_distance",
    "estimate_physical",
    "get_hardware_model",
    "logical_error_rate",
]


@dataclass(frozen=True)
class HardwareModel:
    """Assumptions about a physical machine.  Every field is an input, not a fact."""

    name: str
    #: Physical two-qubit gate / measurement error rate.
    physical_error_rate: float
    #: Surface-code threshold used in the error-rate fit.
    threshold: float = 0.01
    #: Wall-clock duration of one surface-code syndrome extraction round.
    cycle_time_seconds: float = 1e-6
    #: Physical qubits per logical qubit = this factor times d^2.
    routing_factor: float = 2.0
    #: Number of magic-state factories running in parallel.
    factories: int = 1
    #: Code cycles for one factory to output one distilled T state.
    factory_cycles: int = 10
    #: Physical qubits occupied by one factory.
    factory_qubits: int = 150_000
    connectivity: str = "2D nearest-neighbour"
    reference: str = ""
    notes: str = ""


HARDWARE_MODELS: dict[str, HardwareModel] = {
    "superconducting": HardwareModel(
        name="superconducting",
        physical_error_rate=1e-3,
        cycle_time_seconds=1e-6,
        factories=4,
        factory_cycles=10,
        factory_qubits=150_000,
        connectivity="2D nearest-neighbour",
        reference="Parameter style follows Gidney & Ekera, arXiv:1905.09749",
        notes=(
            "A mid-range superconducting assumption: 10^-3 physical error rate "
            "and a 1 microsecond syndrome cycle. These are round numbers chosen "
            "to be recognisable, not measurements of any specific device."
        ),
    ),
    "optimistic": HardwareModel(
        name="optimistic",
        physical_error_rate=1e-4,
        cycle_time_seconds=1e-7,
        factories=16,
        factory_cycles=10,
        factory_qubits=100_000,
        connectivity="2D nearest-neighbour",
        reference="Hypothetical improved hardware",
        notes=(
            "A deliberately favourable machine: an order of magnitude better "
            "error rate and ten times faster cycles than the superconducting "
            "model, with far more distillation capacity. Useful for bounding "
            "how much better things could plausibly get."
        ),
    ),
    "conservative": HardwareModel(
        name="conservative",
        physical_error_rate=5e-3,
        cycle_time_seconds=1e-5,
        factories=1,
        factory_cycles=20,
        factory_qubits=200_000,
        connectivity="2D nearest-neighbour",
        reference="Hypothetical near-threshold hardware",
        notes=(
            "Physical error rate only a factor of two below threshold, which "
            "forces a large code distance. Shows how sharply the physical cost "
            "depends on the error rate."
        ),
    ),
}


def get_hardware_model(name: str) -> HardwareModel:
    try:
        return HARDWARE_MODELS[name]
    except KeyError:
        raise KeyError(
            f"unknown hardware model {name!r}; available: {sorted(HARDWARE_MODELS)}"
        ) from None


def logical_error_rate(distance: int, model: HardwareModel) -> float:
    """``p_L(d) = 0.1 (p / p_th)^((d+1)/2)`` -- per logical qubit per code cycle."""
    if model.physical_error_rate >= model.threshold:
        return 1.0
    return 0.1 * (model.physical_error_rate / model.threshold) ** ((distance + 1) / 2)


def choose_code_distance(
    logical_qubits: int,
    code_cycles: float,
    model: HardwareModel,
    target_failure_probability: float = 0.01,
    max_distance: int = 101,
) -> int | None:
    """Smallest odd ``d`` whose total error stays inside the budget.

    Returns ``None`` when no distance up to ``max_distance`` suffices, which
    happens when the physical error rate is at or above threshold.  That is
    reported as "not achievable under this model" rather than papered over.
    """
    if model.physical_error_rate >= model.threshold:
        return None
    for distance in range(3, max_distance + 1, 2):
        total = logical_error_rate(distance, model) * logical_qubits * code_cycles
        if total <= target_failure_probability:
            return distance
    return None


@dataclass
class PhysicalEstimate:
    """A fault-tolerant estimate together with everything it assumed."""

    model_name: str
    achievable: bool

    logical_qubits: int
    t_count: int
    toffoli_depth: int

    code_distance: int | None = None
    physical_qubits_data: int | None = None
    physical_qubits_factories: int | None = None
    physical_qubits_total: int | None = None

    code_cycles_reaction_limited: float | None = None
    code_cycles_distillation_limited: float | None = None
    code_cycles: float | None = None
    runtime_seconds: float | None = None

    logical_error_per_qubit_cycle: float | None = None
    target_failure_probability: float = 0.01

    assumptions: list[str] = field(default_factory=list)
    provenance: str = "ASSUMPTION-DEPENDENT"

    def to_dict(self) -> dict:
        return asdict(self)

    def __str__(self) -> str:
        lines = [
            "Fault-Tolerant Resource Estimate  [ASSUMPTION-DEPENDENT]",
            "=" * 56,
            "",
            f"Hardware model:            {self.model_name}",
            "",
            "Logical input  [MEASURED from the circuit]",
            f"  logical qubits:          {self.logical_qubits:,}",
            f"  T-count:                 {self.t_count:,}",
            f"  Toffoli depth:           {self.toffoli_depth:,}",
            "",
        ]
        if not self.achievable:
            lines += [
                "Result: NOT ACHIEVABLE under this model.",
                "",
                "The physical error rate is at or above the surface-code threshold, "
                "or no code distance within the search limit meets the error budget. "
                "Increasing the code distance does not help above threshold -- adding "
                "qubits makes things worse, not better.",
                "",
            ]
        else:
            lines += [
                "Derived  [ANALYTICAL, from the formulas in this module]",
                f"  code distance d:         {self.code_distance}",
                f"  logical error / qubit / cycle: {self.logical_error_per_qubit_cycle:.2e}",
                f"  physical qubits (data):  {self.physical_qubits_data:,}",
                f"  physical qubits (factories): {self.physical_qubits_factories:,}",
                f"  physical qubits (total): {self.physical_qubits_total:,}",
                "",
                f"  code cycles, reaction-limited:     {self.code_cycles_reaction_limited:,.0f}",
                f"  code cycles, distillation-limited: "
                f"{self.code_cycles_distillation_limited:,.0f}",
                f"  code cycles (binding):   {self.code_cycles:,.0f}",
                f"  runtime:                 {_human_time(self.runtime_seconds)}",
                "",
            ]
        lines += ["Assumptions", "-" * 11]
        lines += [f"  * {a}" for a in self.assumptions]
        return "\n".join(lines)


def _human_time(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    for unit, size in (("years", 3.156e7), ("days", 86400), ("hours", 3600), ("s", 1)):
        if seconds >= size:
            return f"{seconds / size:,.2f} {unit}  ({seconds:.3g} s)"
    return f"{seconds:.3g} s"


def estimate_physical(
    logical_report,
    model: HardwareModel | str = "superconducting",
    target_failure_probability: float = 0.01,
) -> PhysicalEstimate:
    """Turn a logical :class:`~qsha256.quantum.resources.analyzer.ResourceReport`
    into a physical estimate under an explicit hardware model.

    The code distance is solved self-consistently: the runtime depends on ``d``
    and the required ``d`` depends on the runtime, so the loop iterates until it
    settles (a handful of passes; it converges quickly because ``d`` moves in
    steps of two and the dependence is logarithmic).
    """
    model = get_hardware_model(model) if isinstance(model, str) else model

    logical_qubits = logical_report.width
    t_count = logical_report.t_count
    toffoli_depth = logical_report.depth["toffoli_depth"]

    assumptions = [
        f"Surface code with a {model.threshold:g} threshold and the standard "
        f"p_L = 0.1 (p/p_th)^((d+1)/2) error fit (Fowler et al., arXiv:1208.0928).",
        f"Physical error rate p = {model.physical_error_rate:g}, "
        f"code cycle {model.cycle_time_seconds:g} s.",
        f"Physical qubits per logical qubit = {model.routing_factor:g} d^2, "
        f"covering the data patch plus routing space.",
        f"{model.factories} magic-state factory/factories, "
        f"{model.factory_cycles} code cycles per T state, "
        f"{model.factory_qubits:,} physical qubits each. Published factory designs "
        f"vary by more than an order of magnitude; these are inputs, not findings.",
        f"Total failure probability budget {target_failure_probability:g} for the whole circuit.",
        f"Connectivity assumed {model.connectivity}; the logical circuit's depth was "
        f"measured assuming all-to-all connectivity, so routing overhead is NOT "
        f"included and the runtime here is optimistic.",
        f"T-count comes from the {logical_report.clifford_t.get('model')} Toffoli "
        f"decomposition; a different decomposition changes this estimate.",
        "This is a model, not a prediction. No hardware was involved at any point.",
    ]
    if model.notes:
        assumptions.append(f"Model notes: {model.notes}")
    if model.reference:
        assumptions.append(f"Model reference: {model.reference}")

    # Self-consistent solve for d.
    distance = None
    cycles = max(1.0, float(toffoli_depth))
    for _ in range(20):
        candidate = choose_code_distance(logical_qubits, cycles, model, target_failure_probability)
        if candidate is None:
            distance = None
            break
        reaction = toffoli_depth * candidate
        distillation = (t_count / max(1, model.factories)) * model.factory_cycles
        new_cycles = max(1.0, float(max(reaction, distillation)))
        if candidate == distance and math.isclose(new_cycles, cycles, rel_tol=1e-9):
            cycles = new_cycles
            break
        distance, cycles = candidate, new_cycles

    if distance is None:
        return PhysicalEstimate(
            model_name=model.name,
            achievable=False,
            logical_qubits=logical_qubits,
            t_count=t_count,
            toffoli_depth=toffoli_depth,
            target_failure_probability=target_failure_probability,
            assumptions=assumptions,
        )

    data_qubits = math.ceil(model.routing_factor * distance**2 * logical_qubits)
    factory_qubits = model.factories * model.factory_qubits
    reaction = toffoli_depth * distance
    distillation = (t_count / max(1, model.factories)) * model.factory_cycles
    binding = max(reaction, distillation)

    return PhysicalEstimate(
        model_name=model.name,
        achievable=True,
        logical_qubits=logical_qubits,
        t_count=t_count,
        toffoli_depth=toffoli_depth,
        code_distance=distance,
        physical_qubits_data=data_qubits,
        physical_qubits_factories=factory_qubits,
        physical_qubits_total=data_qubits + factory_qubits,
        code_cycles_reaction_limited=float(reaction),
        code_cycles_distillation_limited=float(distillation),
        code_cycles=float(binding),
        runtime_seconds=binding * model.cycle_time_seconds,
        logical_error_per_qubit_cycle=logical_error_rate(distance, model),
        target_failure_probability=target_failure_probability,
        assumptions=assumptions,
    )

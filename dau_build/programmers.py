"""Board programmers: how a built bitstream reaches the FPGA.

A ``Programmer`` is the FPGA-programming counterpart to the ``backend``
group's ``SynthesisEngine`` — a polymorphic, fully hydra-configurable
model selected from the ``programmer`` config group
(``programmer=programmers/openfpgaloader``). The hardware-plan step helpers
and ``FlashTask`` delegate to ``detect_step``/``program_step``; there is no
programmer ``Literal``, string→type table, or dispatch ``if``.

The default programmer follows a board's ``PlatformDefinition.program_method``
(``jtag`` → ``OpenFpgaLoaderProgrammer``, ``flash`` →
``VivadoHwServerProgrammer``); an explicit ``programmer=`` override wins.

Detection is optional: ``detect_step`` returns ``None`` for a programmer with
no separate detect step (e.g. the Vivado hw_server path), and every plan that
emits a detect step skips a ``None`` rather than calling an unsupported one.

``ToolStep`` is imported lazily inside the step methods so this module and
``hardware_plan`` do not import each other at module load.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from ccflow import BaseModel

from dau_build.vivado_backend import flash_script as vivado_flash_script

if TYPE_CHECKING:
    from dau_build.hardware_plan import HardwareToolchainConfig, ToolStep


class Programmer(BaseModel):
    """A board-programming backend selected from the ``programmer`` config
    group. ``detect_step`` produces an optional device-detection step
    (``None`` when the programmer has none); ``program_step`` produces the
    configuration step (volatile by default, ``mode="persistent"`` for a
    non-volatile write)."""

    name: str

    def detect_step(self, config: "HardwareToolchainConfig") -> "ToolStep | None":
        return None

    def program_step(self, config: "HardwareToolchainConfig", *, mode: Literal["volatile", "persistent"] = "volatile") -> "ToolStep":
        raise NotImplementedError


class OpenFpgaLoaderProgrammer(Programmer):
    """JTAG programming via ``openFPGALoader``. ``executable``/``jtag_cable``
    default to the composed toolchain config (so a board's ``host_access``
    cable and a ``HardwarePlanTask.openfpgaloader`` override still flow
    through); an explicitly composed programmer may pin them."""

    name: str = "openfpgaloader"
    executable: str = "openFPGALoader"
    jtag_cable: str | None = None

    def _cable(self, config: "HardwareToolchainConfig") -> str:
        if self.jtag_cable is not None:
            return self.jtag_cable
        return config.required_host_access("jtag_cable")

    def detect_step(self, config: "HardwareToolchainConfig") -> "ToolStep":
        from dau_build.hardware_plan import ToolStep

        return ToolStep("jtag-detect", (self.executable, "-c", self._cable(config), "--detect"))

    def program_step(self, config: "HardwareToolchainConfig", *, mode: Literal["volatile", "persistent"] = "volatile") -> "ToolStep":
        from dau_build.hardware_plan import ToolStep

        if mode == "persistent":
            return ToolStep("program-persistent", (self.executable, "-c", self._cable(config), "-f", str(config.bitstream)))
        return ToolStep("program-volatile", (self.executable, "-c", self._cable(config), str(config.bitstream)))


class VivadoHwServerProgrammer(Programmer):
    """Programming through the Vivado hw_server ``flash.tcl`` path (the
    ``flash`` plan). Has no separate JTAG detect step (``detect_step`` returns
    ``None``); ``program_step`` emits the same ``bash -lc`` step the flash plan
    has always produced."""

    name: str = "vivado-hwserver"
    vivado_settings: Path = Path("/opt/Xilinx/2025.1/Vivado/settings64.sh")

    def program_step(self, config: "HardwareToolchainConfig", *, mode: Literal["volatile", "persistent"] = "volatile") -> "ToolStep":
        from dau_build.hardware_plan import ToolStep

        script = vivado_flash_script(
            work_root=config.work_root,
            vivado_settings=self.vivado_settings,
            vivado_executable=config.vivado_executable,
            vivado_invocation=config.vivado_invocation,
        )
        return ToolStep("flash", ("bash", "-lc", script))


for _programmer_cls in (Programmer, OpenFpgaLoaderProgrammer, VivadoHwServerProgrammer):
    _programmer_cls.model_rebuild()

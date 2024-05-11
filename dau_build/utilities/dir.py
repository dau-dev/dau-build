import inspect
import os
import os.path
from datetime import datetime

__all__ = (
    "build_directory",
    "model_path",
)


def build_directory(root: str = "") -> str:
    dir = root or os.path.dirname(inspect.stack()[1].filename)
    dir_path = os.path.abspath(os.path.join(dir, "build", f"finn-build-{datetime.now().strftime('%Y%m%d-%H-%M-%S')}"))
    if os.path.exists(dir_path):
        return dir_path
    os.makedirs(dir_path, exist_ok=True)
    os.environ["FINN_ROOT"] = "/home/timkpaine/Developer/projects/dau/finn"
    os.environ["VIVADO_PATH"] = "/opt/Xilinx/Vivado/2023.2"
    os.environ["FINN_BUILD_DIR"] = os.environ.get("FINN_BUILD_DIR", dir_path)
    os.environ["VERILATOR_ROOT"] = "/usr/local"
    os.environ["OHMYXILINX"] = "/home/timkpaine/Developer/projects/dau/finn/deps/oh-my-xilinx"
    return dir_path


def model_path(build_dir: str, filename: str):
    return os.path.join(build_dir, f"{filename}.onnx")

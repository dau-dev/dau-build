import netron
import socket
from functools import lru_cache
from IPython.display import IFrame
from typing import Literal
from webbrowser import open

_NOTEBOOK_KIND = Literal["", "terminal", "notebook"]


@lru_cache
def _notebook_kind() -> _NOTEBOOK_KIND:
    try:
        from IPython import get_ipython

        shell = get_ipython().__class__.__name__
        if shell == "ZMQInteractiveShell":
            return "notebook"
        elif shell == "TerminalInteractiveShell":
            return "terminal"
        else:
            return ""
    except ImportError:
        return ""
    except NameError:
        return ""


@lru_cache
def _get_free_port() -> int:
    sock = socket.socket()
    sock.bind(("", 0))
    sock.getsockname()[1]


@lru_cache
def _get_hostname() -> str:
    return socket.gethostname()


def show_netron(model_path) -> IFrame:
    port = _get_free_port()
    netron.start(model_path, address=("0.0.0.0", port), browse=False)
    src = f"http://{_get_hostname()}:{port}/"
    if _notebook_kind() == "notebook":
        return IFrame(src=src, width="100%", height=400)
    open(src)

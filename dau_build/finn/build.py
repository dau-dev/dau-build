from finn.transformation.fpgadataflow.make_zynq_proj import ZynqBuild

# from finn.util.basic import pynq_part_map

__all__ = (
    "ZynqBuild",
    "build_board",
)


def build_board(model, pynq_board: str = "Pynq-Z1"):
    # fpga_part = pynq_part_map[pynq_board]
    target_clk_ns = 10
    model = model.transform(ZynqBuild(platform=pynq_board, period_ns=target_clk_ns))
    return model

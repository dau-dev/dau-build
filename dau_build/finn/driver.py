from finn.transformation.fpgadataflow.make_pynq_driver import MakePYNQDriver


def build_driver(model):
    model = model.transform(MakePYNQDriver("zynq-iodma"))
    return model

import finn.transformation.fpgadataflow.convert_to_hw_layers as to_hw


def convert_to_hw(model):
    model = model.transform(to_hw.InferQuantizedMatrixVectorActivation())
    model = model.transform(to_hw.InferThresholdingLayer())
    model = model.transform(to_hw.InferBinaryMatrixVectorActivation())
    return model

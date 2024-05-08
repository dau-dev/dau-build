from finn.transformation.fpgadataflow.create_dataflow_partition import CreateDataflowPartition


def dataflow_partition(model):
    parent_model = model.transform(CreateDataflowPartition())
    return parent_model

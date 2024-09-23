class TestEnvironment:
    def test_imports(self):
        import brevitas  # noqa: F401
        import brevitas_examples  # noqa: F401
        import finn  # noqa: F401

        # import finnexperimental  # noqa: F401
        import pyverilator  # noqa: F401
        import qonnx  # noqa: F401

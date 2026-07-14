def test_toolkit_importable():
    import tokendog_mcp
    assert hasattr(tokendog_mcp, "__version__")

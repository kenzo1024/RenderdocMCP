"""`python -m renderdoc_mcp` 的入口。"""

from renderdoc_mcp.server import main


if __name__ == "__main__":
    # 直接交给 FastMCP server。
    main()

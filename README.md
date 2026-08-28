# renderdoc-mcp

一个精简版 RenderDoc MCP server，只聚焦一件事：从 `.rdc` 捕获文件里导出指定 draw call 的资源。

保留能力：

- 导出 VSIn mesh 数据
- 导出 VSOut mesh 数据
- 导出 shader 绑定纹理
- 可选导出 color/depth render target
- 生成 manifest，方便后续资产处理脚本读取

## 环境要求

RenderDoc 的 Python 模块必须能被当前 Python 解释器加载。通常需要设置：

```powershell
$env:RENDERDOC_MODULE_PATH = "C:\Program Files\RenderDoc\pymodules"
```

注意：RenderDoc 的 `renderdoc.pyd` 必须和 Python ABI 匹配。如果 RenderDoc 安装包提供的是 Python 3.6 扩展，就需要用 Python 3.6 x64 运行 MCP。

## 常用工具

- `open_capture(filepath, mode="auto")`：打开指定 `.rdc`；`mode` 可选 `auto`、`background` 或 `foreground`
- `open_capture_background(filepath)`：通过 qrenderdoc bridge 后台加载指定 `.rdc`，不显示、聚焦或激活窗口；失败时不回退到前台
- `open_capture_at_event(filepath, event_id)`：在 RenderDoc GUI 打开指定 `.rdc` 并聚焦 EID
- `focus_event(event_id)`：在 RenderDoc GUI 当前捕获中聚焦 EID
- `Activity Log`：在 RenderDoc Tools 菜单查看 Bridge 操作历史和结果
- `show_activity_log()`：从 MCP 主动显示或前置 Activity Log 底部面板
- `connect_to_gui_capture()`：通过 RenderDoc GUI bridge 打开当前 GUI 里的捕获
- `export_mesh_stage_data(...)`：导出单个 mesh stage
- `export_event_textures(...)`：导出指定 EID 的纹理
- `export_draw_bundle(...)`：一次导出 VSIn、VSOut、纹理和 manifest

GUI 聚焦功能依赖 qrenderdoc bridge。安装或更新扩展后，需要重启 RenderDoc：

```powershell
python -m renderdoc_mcp.install_bridge
```

打开指定捕获并跳到 EID 852：

```json
{
  "filepath": "D:\\captures\\frame.rdc",
  "event_id": 852
}
```

## 示例

```json
{
  "event_id": 852,
  "output_dir": "D:\\_Proj\\QTAU6\\Assets\\EndField\\Elwin",
  "prefix": "skin"
}
```

输出目录大致如下：

```text
skin_eid_852/
  skin_vsin.json
  skin_vsout.json
  skin_manifest.json
  skin_textures/
    skin_pixel_t0_xxx.png
    skin_rt_color0_eid852_xxx.png
```

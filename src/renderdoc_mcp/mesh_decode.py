"""Mesh 数据解码。

这里保留的是已经确认可用的导出链路，思路来自 RenderDoc rdtest/analyse.py：
- VSIn：从 Input Assembler 的 vertex/index buffer 读取原始顶点输入。
- VSOut：从 RenderDoc 的 PostVS buffer 读取 vertex shader 跑完后的输出。

最终输出统一成 list[dict]，每一行是一条顶点记录，方便写 JSON/CSV。
"""

import struct
from typing import Any

from renderdoc_mcp.renderdoc_api import VS_INPUT_STAGE, mesh_stages, rd


class MeshAttribute:
    """一个顶点属性及其对应的 RenderDoc MeshFormat。"""

    def __init__(self, name, mesh):
        self.name = name
        self.mesh = mesh


def get_mesh_stage_data(
    controller,
    action,
    stage: str,
    first_index: int = 0,
    max_vertices: int = 0,
    instance: int = 0,
    view: int = 0,
):
    """导出一个 mesh stage 的顶点数据。

    `vsin` 不能直接走 GetPostVSData，因为它是 shader 执行前的数据；
    其它 stage 使用 RenderDoc 的 post-transform 数据。
    """
    stage_key = stage.lower()
    count = max_vertices if max_vertices > 0 else 0

    if stage_key == VS_INPUT_STAGE:
        vertices = _get_vsin_mesh_data(controller, action, first_index, count, instance)
        meta = {"stage": stage_key, "total_vertices": action.numIndices}
        return vertices, meta

    stages = mesh_stages()
    mesh_stage = stages.get(stage_key)
    if mesh_stage is None:
        valid = ", ".join(sorted(stages))
        raise ValueError(f"Unknown mesh stage '{stage}'. Valid stages: {valid}")

    vertices, mesh_fmt = _get_postvs_mesh_data(
        controller, action, mesh_stage, first_index, count, instance, view
    )
    meta = {"stage": stage_key, "total_vertices": mesh_fmt.numIndices}
    if mesh_fmt.status:
        meta["status"] = mesh_fmt.status
    return vertices, meta


def jsonify_vertices(vertices):
    """把 tuple/float 转成 JSON 友好的值，减少浮点噪声。"""
    return [{key: _jsonify_value(value) for key, value in row.items()} for row in vertices]


def _fetch_indices(controller, mesh, first_index, num_indices):
    """读取 index buffer，并转换成实际 vertex index。

    对非 indexed draw，RenderDoc 的 indexResourceId 为空，此时直接生成连续索引。
    """
    pipe = controller.GetPipelineState()
    restart_idx = pipe.GetRestartIndex() & ((1 << (mesh.indexByteStride * 8)) - 1)
    restart_enabled = pipe.IsRestartEnabled()

    if mesh.indexResourceId == rd.ResourceId.Null():
        return list(range(first_index, first_index + num_indices))

    # indexByteOffset 是整个 IB 的起点，first_index 再叠加 draw 内部偏移。
    offset = mesh.indexByteStride * first_index
    available = max(mesh.indexByteSize - offset, 0)
    read_bytes = min(available, mesh.indexByteStride * num_indices)
    data = bytes()
    if read_bytes > 0:
        data = controller.GetBufferData(mesh.indexResourceId, mesh.indexByteOffset + offset, read_bytes)

    fmt = {1: "B", 2: "H", 4: "I"}.get(mesh.indexByteStride)
    if fmt is None:
        raise ValueError(f"Unsupported index stride: {mesh.indexByteStride}")

    available_indices = len(data) // mesh.indexByteStride
    packed = struct.unpack_from("=" + str(min(available_indices, num_indices)) + fmt, data)
    missing = [None] * max(num_indices - available_indices, 0)

    out = []
    for index in packed:
        # primitive restart index 不能加 baseVertex，它不是普通顶点。
        if restart_enabled and index == restart_idx:
            out.append(index)
        else:
            out.append(index + mesh.baseVertex)
    return out + missing


def _get_vsin_attrs(controller, vertex_offset, index_mesh):
    """从 IA 输入布局生成 VSIn 属性列表。

    每个属性都指向真实 vertex buffer 的一段字节，以及它自己的格式描述。
    """
    pipe = controller.GetPipelineState()
    vertex_buffers = pipe.GetVBuffers()
    attrs = []

    for layout in pipe.GetVertexInputs():
        if not layout.used:
            continue

        # 复制 index mesh，是为了让每个属性共享 index 信息，同时有自己的 VB 信息。
        mesh = rd.MeshFormat(index_mesh)
        binding = vertex_buffers[layout.vertexBuffer]
        offset = layout.byteOffset + vertex_offset * binding.byteStride
        mesh.vertexByteStride = binding.byteStride
        mesh.instStepRate = layout.instanceRate
        mesh.instanced = layout.perInstance
        mesh.vertexResourceId = binding.resourceId
        mesh.vertexByteOffset = binding.byteOffset + offset
        mesh.vertexByteSize = max(binding.byteSize - offset, 0)
        mesh.format = layout.format
        attrs.append(MeshAttribute(layout.name, mesh))

    return attrs


def _get_postvs_attrs(controller, mesh, data_stage):
    """根据 shader output signature 构造 VSOut/GSOut 等属性列表。"""
    pipe = controller.GetPipelineState()
    shader = _postvs_shader_reflection(pipe, data_stage)
    if shader is None:
        return []

    attrs = []
    position_index = 0
    for sig in shader.outputSignature:
        # 只导出最终会被光栅化的 stream。
        if pipe.GetRasterizedStream() >= 0 and sig.stream != pipe.GetRasterizedStream():
            continue
        if pipe.GetRasterizedStream() < 0 and sig.stream != 0:
            continue
        if sig.systemValue == rd.ShaderBuiltin.OutputIndices:
            continue

        # PostVS buffer 是 RenderDoc 生成的线性数据，需要按 signature 推出格式。
        attr_mesh = rd.MeshFormat(mesh)
        attr_mesh.format = rd.ResourceFormat()
        attr_mesh.format.compByteWidth = rd.VarTypeByteSize(sig.varType)
        attr_mesh.format.compCount = sig.compCount
        attr_mesh.format.compType = rd.VarTypeCompType(sig.varType)
        attr_mesh.format.type = rd.ResourceFormatType.Regular

        name = sig.semanticIdxName if sig.varName == "" else sig.varName
        if sig.systemValue == rd.ShaderBuiltin.Position:
            position_index = len(attrs)
        attrs.append(MeshAttribute(name, attr_mesh))

    if position_index > 0:
        # POSITION 放在第一列，后续看 JSON/CSV 时更直观。
        attrs.insert(0, attrs.pop(position_index))

    offset = 0
    for attr in attrs:
        # RenderDoc 的 PostVS 数据可能有对齐填充，这里复刻官方解析逻辑。
        fmt = attr.mesh.format
        elem_size = 8 if fmt.compByteWidth > 4 else 4
        alignment = elem_size
        if fmt.compCount == 2:
            alignment *= 2
        elif fmt.compCount > 2:
            alignment *= 4
        if pipe.HasAlignedPostVSData(data_stage) and offset % alignment:
            offset += alignment - (offset % alignment)
        attr.mesh.vertexByteOffset += offset
        offset += elem_size * fmt.compCount

    return attrs


def _postvs_shader_reflection(pipe, data_stage):
    """按 mesh stage 找对应 shader reflection。"""
    if data_stage == rd.MeshDataStage.VSOut:
        return pipe.GetShaderReflection(rd.ShaderStage.Vertex)
    if data_stage == rd.MeshDataStage.MeshOut:
        return pipe.GetShaderReflection(rd.ShaderStage.Mesh)
    if data_stage == rd.MeshDataStage.TaskOut:
        raise ValueError("TaskOut attributes are not supported by this exporter")

    shader = pipe.GetShaderReflection(rd.ShaderStage.Geometry)
    return shader or pipe.GetShaderReflection(rd.ShaderStage.Domain)


def _decode_mesh_data(
    controller,
    indices,
    display_indices,
    attrs,
    instance,
):
    """按索引表读取所有顶点属性。"""
    if not attrs:
        return []

    # 先合并每个 buffer 的读取范围，避免每个顶点/属性都单独 GetBufferData。
    ranges = _collect_buffer_ranges(attrs)
    buffers = {
        resource: controller.GetBufferData(resource, begin, end - begin)
        for resource, (begin, end) in ranges.items()
    }
    restart = _strip_restart_index(controller, attrs[0].mesh)

    rows = []
    for row_index, vertex_index in enumerate(indices):
        row = {"vtx": row_index, "idx": display_indices[row_index]}
        if restart is not None and vertex_index == restart:
            # primitive restart 行只保留索引信息，不读属性。
            rows.append(row)
            continue

        for attr in attrs:
            row[attr.name] = _read_attr_value(attr, vertex_index, instance, ranges, buffers)
        rows.append(row)

    return rows


def _collect_buffer_ranges(attrs):
    """合并每个 vertex buffer 的读取范围。"""
    ranges = {}
    for attr in attrs:
        begin = attr.mesh.vertexByteOffset
        end = min(begin + attr.mesh.vertexByteSize, 0xFFFFFFFFFFFFFFFF)
        old = ranges.get(attr.mesh.vertexResourceId)
        if old:
            begin, end = min(old[0], begin), max(old[1], end)
        ranges[attr.mesh.vertexResourceId] = (begin, end)
    return ranges


def _strip_restart_index(controller, mesh):
    """返回当前 pipeline 的 primitive restart index；没开启则返回 None。"""
    pipe = controller.GetPipelineState()
    if not pipe.IsRestartEnabled() or mesh.indexResourceId == rd.ResourceId.Null():
        return None
    return pipe.GetRestartIndex() & ((1 << (mesh.indexByteStride * 8)) - 1)


def _read_attr_value(attr: MeshAttribute, index, instance: int, ranges, buffers):
    """读取一个顶点的一项属性。"""
    if index is None:
        return None

    offset = attr.mesh.vertexByteStride * index
    if attr.mesh.instanced:
        # per-instance 属性按 instanceRate 前进，不按 vertex index 前进。
        rate = max(attr.mesh.instStepRate, 1)
        offset = attr.mesh.vertexByteStride + attr.mesh.vertexByteStride * int(instance / rate)

    resource = attr.mesh.vertexResourceId
    base = attr.mesh.vertexByteOffset + offset - ranges[resource][0]
    return _unpack_data(attr.mesh.format, buffers[resource], base)


def _unpack_data(fmt, data: bytes, offset: int):
    """按 RenderDoc ResourceFormat 从字节流里拆出一个属性值。"""
    if offset >= len(data):
        return None
    if fmt.Special():
        raise ValueError("Packed vertex formats are not supported")

    chars = {
        # 每个字符串按 compByteWidth 选择 struct 格式字符。
        rd.CompType.UInt: "xBHxIxxxQ",
        rd.CompType.SInt: "xbhxixxxq",
        rd.CompType.Float: "xxexfxxxd",
    }
    chars[rd.CompType.UNorm] = chars[rd.CompType.UInt]
    chars[rd.CompType.UScaled] = chars[rd.CompType.UInt]
    chars[rd.CompType.SNorm] = chars[rd.CompType.SInt]
    chars[rd.CompType.SScaled] = chars[rd.CompType.SInt]

    value = struct.unpack_from("=" + str(fmt.compCount) + chars[fmt.compType][fmt.compByteWidth], data, offset)
    if fmt.compType == rd.CompType.UNorm:
        # 归一化格式导出成 0..1 / -1..1 的浮点值，更符合 mesh 语义。
        max_value = float((1 << (fmt.compByteWidth * 8)) - 1)
        value = tuple(float(v) / max_value for v in value)
    elif fmt.compType == rd.CompType.SNorm:
        min_value = -(1 << (fmt.compByteWidth * 8 - 1))
        divisor = -float(min_value + 1)
        value = tuple(-1.0 if v == min_value else float(v / divisor) for v in value)
    elif fmt.compType in (rd.CompType.UScaled, rd.CompType.SScaled):
        value = tuple(float(v) for v in value)

    if fmt.BGRAOrder():
        value = tuple(value[i] for i in [2, 1, 0, 3])
    return value


def _build_index_mesh(action, ibuffer, num_indices: int):
    """把当前 draw 的 index buffer 信息整理成 MeshFormat。"""
    offset = action.indexOffset * ibuffer.byteStride
    mesh = rd.MeshFormat()
    mesh.numIndices = num_indices
    mesh.indexByteOffset = ibuffer.byteOffset + offset
    mesh.indexByteStride = ibuffer.byteStride
    mesh.indexResourceId = ibuffer.resourceId
    mesh.baseVertex = action.baseVertex
    mesh.indexByteSize = max(ibuffer.byteSize - offset, 0)

    if not (action.flags & rd.ActionFlags.Indexed):
        # 非 indexed draw 没有 IB，后续会直接生成连续索引。
        mesh.indexByteOffset = 0
        mesh.indexByteStride = 0
        mesh.indexResourceId = rd.ResourceId.Null()
    return mesh


def _get_vsin_mesh_data(controller, action, first_index, num_indices, instance):
    """读取 shader 执行前的顶点输入。"""
    if num_indices == 0:
        num_indices = action.numIndices
    else:
        num_indices = min(num_indices, action.numIndices)
    first_index = min(first_index, max(action.numIndices - 1, 0))

    ibuffer = controller.GetPipelineState().GetIBuffer()
    mesh = _build_index_mesh(action, ibuffer, num_indices)
    attrs = _get_vsin_attrs(controller, action.vertexOffset, mesh)
    indices = _fetch_indices(controller, mesh, first_index, num_indices)
    return _decode_mesh_data(controller, indices, indices, attrs, instance)


def _get_postvs_mesh_data(
    controller,
    action,
    data_stage,
    first_index: int,
    num_indices: int,
    instance: int,
    view: int,
):
    """读取 shader 执行后的顶点输出。"""
    mesh = controller.GetPostVSData(instance, view, data_stage)
    if mesh.numIndices == 0:
        return [], mesh

    if num_indices == 0:
        num_indices = mesh.numIndices
    else:
        num_indices = min(num_indices, mesh.numIndices)
    first_index = min(first_index, max(mesh.numIndices - 1, 0))

    ibuffer = controller.GetPipelineState().GetIBuffer()
    input_mesh = _build_index_mesh(action, ibuffer, num_indices)
    # VSOut 行用 post-VS 索引读取属性，但 idx 列保留原始输入索引，方便回查。
    indices = _fetch_indices(controller, mesh, first_index, num_indices)
    input_indices = _fetch_indices(controller, input_mesh, first_index, num_indices)
    attrs = _get_postvs_attrs(controller, mesh, data_stage)
    return _decode_mesh_data(controller, indices, input_indices, attrs, instance), mesh


def _jsonify_value(value: Any) -> Any:
    """输出前做轻量清洗，避免 JSON 里出现 tuple。"""
    if value is None:
        return None
    if isinstance(value, tuple):
        return [round(v, 6) if isinstance(v, float) else v for v in value]
    return value

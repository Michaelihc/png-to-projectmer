# Meshmark

**简体中文** · [English](README.en.md)

把纹章图片转换成原版 SCP:SL ProjectMER 图纸：**拆分图层 → 调整三角剖分质量 → 导出 ZIP**。

![Meshmark 完整的三步浏览器流程](docs/screenshots/workflow.png)

![真实源三角剖分的近景](docs/screenshots/triangulation.png)

Meshmark 完全在本地运行，不使用前端框架，并刻意保持 **零 CSS**。输出只包含原版 ProjectMER 的空物体与四边形基元（`BlockType 0` 和 `BlockType 1`），无需 fork 或魔改插件。

## 30 秒启动

### Windows

1. 安装 [Python 3.10+](https://www.python.org/downloads/)。
2. 下载或克隆本仓库。
3. 双击 `run-webapp.bat`。

首次启动会自动安装 Python 依赖，并打开 `http://127.0.0.1:8731/`。

### macOS / Linux

```bash
python3 -m pip install -r requirements.txt
python3 webapp/server.py
```

然后打开 `http://127.0.0.1:8731/`。

## 使用方法

1. **拆分图层：** 选择 PNG、JPG、WEBP 或 BMP。程序把图片边缘最常见的颜色识别为背景，并自动提取前景颜色图层。
2. **三角剖分：** 拖动质量滑杆。较低数值生成更轻量的图纸；较高数值保留更多轮廓细节。预览展示凸合并前真实的 earcut 三角形。
3. **导出：** 下载 ZIP，并解压至：

```text
LabAPI-beta/configs/ProjectMER/Schematics/
```

以仓库自带的 Nu-22 示例为例，质量 30 生成 501 个源三角形，质量 95 生成 1,883 个。最终运行时几何会更少，因为 Meshmark 会先把三角形合并为凸区域，再用原版平行四边形基元覆盖。

图片始终留在你的电脑上。服务器默认只绑定 `127.0.0.1`，不会把图片上传给第三方。

## 命令行

自动分层转换：

```bash
python tools/layered_emblem_to_mer.py nu22.png \
  --name nu22 \
  --output converted_mer \
  --quality 70 \
  --preview \
  --mesh-preview
```

需要精确控制配色与层级时，可使用手工配置：

```bash
python tools/layered_emblem_to_mer.py nu22.png \
  --config examples/nu22.layers.json \
  --name nu22 \
  --output converted_mer \
  --preview
```

单色剪影可以使用底层转换器：

```bash
python tools/png_to_mer_schematic.py scarletking.png \
  --name scarletking \
  --output converted_mer \
  --fill-mode ngon \
  --simplify 1.5 \
  --preview
```

为任一脚本添加 `--help` 可查看完整参数。

## 原理

```text
图片
  → 感知边界的调色板检测
  → 每个颜色图层的平滑亚像素轮廓
  → earcut 三角剖分
  → 凸区域合并
  → 平行四边形覆盖
  → 原版 ProjectMER JSON
```

ProjectMER 没有原生三角形基元。Meshmark 使用变换层级错切来表示非平行四边形：把旋转后的子四边形放在非均匀缩放的空父物体下，即可得到所需的世界空间形状。n-gon 路径会在输出四边形前合并相邻三角形，从而减少运行时对象。

主要模块：

- `webapp/server.py` — 本地 API、ZIP 导出与静态页面服务。
- `webapp/index.html` — 完整的零 CSS 界面。
- `tools/trace_svg.py` — 调色板检测与平滑分层轮廓。
- `tools/layered_emblem_to_mer.py` — 图层堆叠、质量映射、网格预览与图纸生成。
- `tools/mer_ngon_decomposition.py` — 三角剖分、凸合并与平行四边形输出。

## 图层配置

[`examples/nu22.layers.json`](examples/nu22.layers.json) 说明了配置格式。每层写法如下：

```json
"green": ["#6D9A57", 2, 0.8, "region"]
```

四个值依次为填充色、从后向前的顺序、以像素为单位的轮廓容差和模式。`region` 按颜色的自然区域描摹；`silhouette` 填满整个纹章，可作为低成本底衬，让细节从前层孔洞中显示，而无需单独生成几何。

## 开发

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m py_compile webapp/server.py tools/*.py
```

欢迎贡献代码和可复现的问题报告。请附上源图片或最小合成替代图、质量数值以及生成的对象数量。

## 授权

源代码采用 [MIT 许可证](LICENSE)。仓库自带的 SCP 衍生示例纹章及其转换输出采用 **CC BY-SA 3.0**；详见 [NOTICE.md](NOTICE.md)。你转换的图片仍保留其原始许可证。

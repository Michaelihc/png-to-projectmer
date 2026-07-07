# 纹章 → ProjectMER 图纸

**简体中文** · [English](README.en.md)

把高对比度的纹章图片（PNG / JPG / WEBP）转换成 **SCP:SL ProjectMER** 图纸，
且完全由**原版四边形基元**（`BlockType 0` Empty + `BlockType 1` Primitive）构成。
输出可直接加载于**原版 ProjectMER**，**无需任何魔改 / fork 插件**。

三角形通过 TRS 层级错切技巧表示为错切四边形（在非均匀缩放的空父物体下旋转子物体，
即可得到错切的世界矩阵）；`ngon` 填充模式会把三角形合并为凸多边形，让复杂纹章所需
的对象数量大幅下降。

## 快速开始 — 网页界面

双击 **`run-webapp.bat`**。首次运行会自动安装 Python 依赖，然后在浏览器打开
`http://127.0.0.1:8731/`。若依赖已装好，用 **`serve.bat`** 可跳过检查、直接启动。

1. 拖入一张纹章图片。
2. 选择填充颜色、描摹亮部/暗部、填充方式，以及细节（简化）滑杆。高级选项收在折叠面板里。
3. 点 **生成图纸** — 得到实时预览与运行时对象数量。
4. **下载** `<name>.zip`，解压到
   `LabAPI-beta/configs/ProjectMER/Schematics/`，即得 `<name>/<name>.json`。

界面支持**中英文切换**（默认中文）。需要 PATH 中有 Python 3.10+（`py` 启动器或 `python`）。

## 快速开始 — 命令行

```bash
py -m pip install -r requirements.txt

py tools/png_to_mer_schematic.py scarletking.png \
    --name scarletking-opt --output converted_mer \
    --fill-mode ngon --simplify 1.5 --min-area 8 \
    --foreground light --threshold 128 --width 10 \
    --color "#D0021BFF" --preview
```

加 `--preview` 会在 JSON 旁边额外生成 `<name>.preview.png` / `.svg`。
运行 `py tools/png_to_mer_schematic.py --help` 查看全部参数。

### 主要参数

| 参数 | 含义 |
| --- | --- |
| `--fill-mode {triangle,ngon}` | `ngon` 把三角形合并成凸多边形——填充区域对象更少。 |
| `--simplify PX` | 轮廓容差。越低越还原、对象越多；越高越平滑、越省。 |
| `--foreground {light,dark}` | 描摹亮部像素还是暗部像素。 |
| `--threshold 0-255` | 前景/背景分界阈值。 |
| `--color #RRGGBB[AA]` | 纹章颜色。线条会留空（透出墙面）。 |
| `--min-area PX` | 忽略小于该面积的轮廓（调低以保留细节）。 |
| `--width UNITS` | 图纸最终宽度（Unity 单位）。 |
| `--border-cylinders` | 把检测到的圆形边框转成 2 个廉价圆柱。 |
| `--trace-mode rectangle-first` | 基于描边的描摹（`--trace-source centerline` 走骨架中线）。 |

## 工具链

流水线由 `tools/` 下的三个文件组成：

- **`png_to_mer_schematic.py`** — 命令行入口：图片 → 轮廓 → 三角形（或 n 边形块）
  → 原版四边形基元图纸 JSON，并输出 SVG/PNG 预览。
- **`mer_triangle_primitives.py`** — 几何：用 TRS 层级错切把每个三角形展开为标准
  MER 四边形（中位平行四边形，附带矩形快速路径）。
- **`mer_ngon_decomposition.py`** — 把 earcut 三角形合并为凸多边形，并用尽量少的
  平行四边形覆盖（TriangleScpSl NGonDecomposition 的 2D 移植）。

`webapp/` 是本地网页界面（`server.py`，仅用标准库；`index.html`）。
`tools/circular_crop_tool.html` 是一个独立的圆形裁剪小工具。

转换结果放在 `converted_mer/<name>/`。

## 授权

- **源代码**：MIT，见 [LICENSE](LICENSE)。
- **示例纹章**（`scarletking.png` 及其转换输出）：源自 SCP 基金会，按
  **CC BY-SA 3.0** 授权。详见 [NOTICE.md](NOTICE.md)。你自己转换的图像仍归其原有授权。

> 仅面向原版 ProjectMER：fork 专用的 `BlockType 11` 三角形路径与一次性 logo 脚本
> 已被移除。

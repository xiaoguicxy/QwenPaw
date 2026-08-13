---
name: style-3d-low-poly
description: 低多边形风格。几何面片、极简色彩、平面着色、抽象造型。适用：概念设计/游戏美术/独立动画。当用户要求low-poly/低多边形/几何风格时激活。
---

# 低多边形风格 · 视觉风格

## 1. 风格定义

- **世界观/美学基调**：极简主义3D美学，用最少的几何面片表达最多的信息。抽象与具象之间。
- **核心视觉特征**：几何面片, 极简色彩, 平面着色, 抽象造型, 低模
- **参考作品**：见各风格经典影片/动画

## 2. 严禁内容

以下元素与本风格冲突，不得在 prompt 中使用：
- 高精度模型
- 写实纹理
- 复杂光影
- 平滑曲面

## 3. 图片 Prompt 规范

### 3.1 风格前缀锚点（5-10 词）
所有图片 prompt 共享的风格方向（适配不照搬）：
`几何面片, 极简色彩, 平面着色, 低多边形风格 aesthetic`

### 3.2 色彩体系
有限的调色板，每个场景3-5种颜色。平面着色，无渐变。色彩选择大胆或有主题。

### 3.3 光照体系
极简光照或无光照。平面着色为主。有时用简单的方向光区分面片。

### 3.4 角色设计 prompt 规范
极度简化的几何人物。三角形/方形/圆柱体组合。无面部细节或用极简表情。

### 3.5 场景设计 prompt 规范
几何化的风景/建筑。山是三角锥，树是圆锥+圆柱。水面是平面。

### 3.6 分镜图 prompt 规范
- 分镜图使用本风格的色彩和光照基准
- 画质限定词与风格匹配
- 相邻宫格保持风格一致

## 4. 视频 Prompt 规范

### 4.1 视频风格锚点（1-2 句）
低多边形风格，几何面片，平面着色，极简色彩，抽象造型。

### 4.2 运镜偏好
几何体自身的形态就是视觉重点。缓慢旋转展示造型。固定角度展示构图。

### 4.3 色彩/光影方向
与图片保持一致的色彩体系和光照方向。视频中光影可以随叙事变化，但风格基调不变。

## 5. 完整 Prompt 示例

### 5.1 角色/场景设计图 prompt 示例
```
Wide shot, low-poly 3D style. Geometric mountain landscape with triangular peaks, cube pine trees, flat-shaded turquoise lake. A tiny low-poly deer (~20 triangles) drinks at the edge. Limited palette: forest green, mountain grey, lake turquoise, sunset orange. No textures, flat shading. 16:9.
```

### 5.2 视频 prompt 示例
```
Shot 1 (wide, slow rotation, 3s):
Low-poly 3D. A geometric island floats in space, triangular mountains, cube buildings, cylinder lighthouse. Flat-shaded pastel palette. A tiny poly boat orbits on a flat blue water ring.

Shot 2 (medium, push-in, 2s):
Camera approaches a low-poly tree. Its triangular canopy shifts green to orange to bare branches, seasons changing in geometric transitions.

Style: Low-poly, flat shading, limited palette, clean geometric edges, abstract minimalism.
Audio: Soft ambient hum, gentle geometric clinks, wind through polygon leaves.
```

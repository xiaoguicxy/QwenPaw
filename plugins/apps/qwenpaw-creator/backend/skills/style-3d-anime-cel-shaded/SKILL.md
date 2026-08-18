---
name: style-3d-anime-cel-shaded
description: 3D赛璐珞渲染风格。3D建模+2D着色、锐利边缘、卡通光照。适用：游戏CG/动漫改编/科幻。当用户要求赛璐珞/cel-shaded/原神风格/游戏CG时激活。
---

# 3D 赛璐珞渲染 · 视觉风格

## 1. 风格定义

- **世界观/美学基调**：3D模型配合2D动画着色技术，兼具3D的空间感和2D的风格化美感。
- **核心视觉特征**：3D建模, 2D着色, 锐利边缘, 卡通光照, 风格化
- **参考作品**：《宝石之国》、《原神》等游戏过场的三维渲染卡渲动画

## 2. 严禁内容

以下元素与本风格冲突，不得在 prompt 中使用：
- 写实渲染
- 纯2D手绘
- 粗糙低模
- 过度光滑无细节

## 3. 图片 Prompt 规范

### 3.1 风格前缀锚点（5-10 词）
所有图片 prompt 共享的风格方向（适配不照搬）：
`3D建模, 2D着色, 锐利边缘, 3D 赛璐珞渲染 aesthetic`

### 3.2 色彩体系
扁平色块为主，色块边缘锐利。阴影用明确的色阶而非渐变。高光为明确的亮色块。

### 3.3 光照体系
卡通光照，明暗分界锐利。通常2-3个色阶。轮廓光常用。

### 3.4 角色设计 prompt 规范
3D建模但2D着色。面部保持动漫风格。头发分块明确。服装褶皱简化为色阶。

### 3.5 场景设计 prompt 规范
3D环境+风格化着色。远景可用写实，近景保持卡通。注重空间纵深。

### 3.6 分镜图 prompt 规范
- 分镜图使用本风格的色彩和光照基准
- 画质限定词与风格匹配
- 相邻宫格保持风格一致

## 4. 视频 Prompt 规范

### 4.1 视频风格锚点（1-2 句）
3D赛璐珞渲染，3D建模+2D着色，锐利色阶边缘，卡通光照，风格化美感。

### 4.2 运镜偏好
3D自由度，可任意角度。常用动态运镜展示3D空间感。环绕/升降/长镜头。

### 4.3 色彩/光影方向
与图片保持一致的色彩体系和光照方向。视频中光影可以随叙事变化，但风格基调不变。

## 5. 完整 Prompt 示例

### 5.1 角色/场景设计图 prompt 示例
```
Medium shot, 3D cel-shaded anime style. A young knight with short silver hair, emerald eyes, white and gold armor with blue cape, holds a glowing crystal sword. Sharp color boundaries, 2-tone shading, bold outline. Fantasy courtyard. Rim lighting, vibrant. 16:9.
```

### 5.2 视频 prompt 示例
```
Shot 1 (wide, slow orbit, 3s):
3D cel-shaded. A mystical forest with glowing mushrooms, a young elf girl with teal hair in a braid, leaf-patterned tunic, walks a mossy path. Sharp color boundaries, cartoon shading.

Shot 2 (medium, tracking, 2s):
She reaches toward a floating luminous butterfly. Eyes widen. It lands on her fingertip, warm light on her face.

Style: 3D cel-shaded, sharp edges, 2-tone shading, stylized lighting, vibrant.
Audio: Forest ambience, magical chime, gentle footsteps.
```

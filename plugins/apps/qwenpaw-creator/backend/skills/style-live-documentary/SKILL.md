---
name: style-live-documentary
description: 纪实风格。手持拍摄、自然光、低调色、粗粝质感。适用：纪录片/新闻/社会/真实记录。当用户要求纪录片/纪实/新闻风格时激活。
---

# 纪实风格 · 视觉风格

## 1. 风格定义

- **世界观/美学基调**：真实记录美学，不美化、不修饰。粗粝感是真实感的来源。
- **核心视觉特征**：手持拍摄, 自然光, 低调色, 粗粝质感, 真实
- **参考作品**：《地球脉动》《徒手攀岩》（真实记录美学）

## 2. 严禁内容

以下元素与本风格冲突，不得在 prompt 中使用：
- 电影级打光
- 过度调色
- 摆拍感
- 美化滤镜

## 3. 图片 Prompt 规范

### 3.1 风格前缀锚点（5-10 词）
所有图片 prompt 共享的风格方向（适配不照搬）：
`手持拍摄, 自然光, 低调色, 纪实风格 aesthetic`

### 3.2 色彩体系
低饱和度，保留环境原色。不过度调色。有时刻意保留不完美的白平衡。

### 3.3 光照体系
完全自然光/实景光。不打灯。接受过曝/欠曝。光线变化是真实感的一部分。

### 3.4 角色设计 prompt 规范
真实人物，无妆容。皮肤纹理、瑕疵保留。自然表情和动作。

### 3.5 场景设计 prompt 规范
真实场景，不做任何布置。环境中的杂乱是真实感。

### 3.6 分镜图 prompt 规范
- 分镜图使用本风格的色彩和光照基准
- 画质限定词与风格匹配
- 相邻宫格保持风格一致

## 4. 视频 Prompt 规范

### 4.1 视频风格锚点（1-2 句）
纪实风格，手持拍摄，自然光，低饱和度，粗粝真实质感。

### 4.2 运镜偏好
手持拍摄，可见抖动。快速摇摄跟随事件。长镜头记录完整过程。不完美的构图是特色。

### 4.3 色彩/光影方向
与图片保持一致的色彩体系和光照方向。视频中光影可以随叙事变化，但风格基调不变。

## 5. 完整 Prompt 示例

### 5.1 角色/场景设计图 prompt 示例
```
Medium shot, documentary style, handheld. An elderly fisherman with deeply wrinkled face, sun-darkened skin, faded blue jacket, mends a net on a weathered dock at early morning. Overcast natural light, no grading, slight vignette, raw authentic. 16:9.
```

### 5.2 视频 prompt 示例
```
Shot 1 (medium, handheld shake, 4s):
Documentary. A bustling morning market, vendors arrange vegetables, a customer haggles, steam from a breakfast stall. Natural light, no setup.

Shot 2 (close-up, quick pan, 2s):
Camera pans to an old woman's hands counting change, wrinkled fingers with coins. Faded floral sleeve, jade bracelet. Shallow DOF.

Style: Documentary handheld, natural light only, low saturation, raw authentic, imperfect framing.
Audio: Market chatter, vendor calls, coin clinking, distant traffic, sizzling oil.
```

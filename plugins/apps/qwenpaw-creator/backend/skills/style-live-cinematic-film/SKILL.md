---
name: style-live-cinematic-film
description: 电影质感风格。35mm胶片感、浅景深、变形宽银幕、自然光效。适用：剧情片/悬疑/文艺/情感。当用户要求电影质感/胶片感/剧情片风格时激活。
---

# 电影质感 · 视觉风格

## 1. 风格定义

- **世界观/美学基调**：院线电影品质，追求真实感与艺术性的平衡。每一帧都像电影截图。
- **核心视觉特征**：35mm胶片, 浅景深, 变形宽银幕, 自然光效, 电影调色
- **参考作品**：见各风格经典影片/动画

## 2. 严禁内容

以下元素与本风格冲突，不得在 prompt 中使用：
- 数字感/塑料感
- 过度后期
- 卡通/动画
- 过度饱和

## 3. 图片 Prompt 规范

### 3.1 风格前缀锚点（5-10 词）
所有图片 prompt 共享的风格方向（适配不照搬）：
`35mm胶片, 浅景深, 变形宽银幕, 电影质感 aesthetic`

### 3.2 色彩体系
电影调色，teal-and-orange常见。胶片色彩科学（Kodak暖调/Fuji冷调）。阴影保留细节。

### 3.3 光照体系
自然光效为主。实景光优先。黄金时段/阴天/室内实景光。避免明显的人工打光感。

### 3.4 角色设计 prompt 规范
真实人物。自然妆容。皮肤纹理真实。表情微妙。服装符合角色设定。

### 3.5 场景设计 prompt 规范
真实场景，注重环境叙事。场景中的道具讲述角色故事。光影营造氛围。

### 3.6 分镜图 prompt 规范
- 分镜图使用本风格的色彩和光照基准
- 画质限定词与风格匹配
- 相邻宫格保持风格一致

## 4. 视频 Prompt 规范

### 4.1 视频风格锚点（1-2 句）
电影质感，35mm胶片感，浅景深，自然光效，变形宽银幕调色。

### 4.2 运镜偏好
电影级运镜。稳定器/轨道/手持（根据情绪）。变形宽银幕镜头光晕。浅景深隔离主体。

### 4.3 色彩/光影方向
与图片保持一致的色彩体系和光照方向。视频中光影可以随叙事变化，但风格基调不变。

## 5. 完整 Prompt 示例

### 5.1 角色/场景设计图 prompt 示例
```
Medium close-up, cinematic 35mm film. A middle-aged man with weathered face, stubble, worn leather jacket sits alone in a dimly lit diner at night. Neon reflections on wet window. Shallow DOF, anamorphic flare, Kodak warm grade, film grain. Teal-and-orange. 2.39:1.
```

### 5.2 视频 prompt 示例
```
Shot 1 (wide, slow dolly-in, steadicam, 4s):
Cinematic 35mm. A woman in a long dark coat walks through a rain-soaked alley at night. Neon signs reflect in puddles. Steam from a manhole. Shallow DOF, anamorphic bokeh.

Shot 2 (medium close-up, handheld, 3s):
She pauses under a flickering streetlight, face half-lit. Rain on her collar. She looks over her shoulder. Film grain, natural skin, muted grade.

Style: 35mm cinematic, shallow DOF, anamorphic, Kodak warm, film grain, natural light.
Audio: Rain on pavement, distant traffic, neon buzz, footsteps in puddles.
```
